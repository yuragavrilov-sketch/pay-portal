"""Service Management Portal — Flask application."""
import json
import logging
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # загружает .env до инициализации приложения

from flask import (
    Flask, render_template, redirect, url_for, request,
    flash, session, jsonify, Response, stream_with_context,
)
from sqlalchemy import text
from models import (
    db, AuditLog, ConfigSnapshot, Environment, Credential,
    Server, Service, ServiceInstance, InstanceConfig, ServiceConfig,
    ServiceConfigVersion, _next_version,
)
from logger import setup_logging
import winrm_utils

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _audit(
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    entity_name: str = '',
    details: str = '',
    result: str = AuditLog.RESULT_OK,
    _ip: str | None = None,
):
    """
    Writes one row to audit_log + emits a Python log line.
    Safe to call inside a request context or a background thread (_ip must be
    passed explicitly when called outside a request context).
    """
    try:
        if _ip is None:
            try:
                _ip = (
                    request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                    or request.remote_addr
                )
            except RuntimeError:
                _ip = 'system'
        ip = _ip
        entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            details=details,
            result=result,
            ip_address=ip,
        )
        db.session.add(entry)
        db.session.commit()

        level = (logging.WARNING if result == AuditLog.RESULT_WARNING
                 else logging.ERROR if result == AuditLog.RESULT_ERROR
                 else logging.INFO)
        log.log(level, '[AUDIT] %s %s#%s name=%r ip=%s%s',
                action, entity_type, entity_id, entity_name, ip,
                f' details={details!r}' if details else '')
    except Exception as exc:
        log.exception('Failed to write audit log: %s', exc)


def _migrate_db(app):
    """Idempotent: добавляет новые колонки в существующие таблицы (PostgreSQL)."""
    with app.app_context():
        with db.engine.connect() as conn:
            # --- Column migrations ---
            col_checks = [
                (
                    "instance_configs", "source_version_id",
                    "ALTER TABLE instance_configs "
                    "ADD COLUMN source_version_id INTEGER "
                    "REFERENCES service_config_versions(id) ON DELETE SET NULL",
                ),
                (
                    "instance_configs", "is_overridden",
                    "ALTER TABLE instance_configs "
                    "ADD COLUMN is_overridden BOOLEAN NOT NULL DEFAULT FALSE",
                ),
                (
                    "service_configs", "env_id",
                    "ALTER TABLE service_configs "
                    "ADD COLUMN env_id INTEGER "
                    "REFERENCES environments(id) ON DELETE SET NULL",
                ),
            ]
            for table, column, ddl in col_checks:
                row = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=:t AND column_name=:c"
                ), {"t": table, "c": column}).fetchone()
                if not row:
                    conn.execute(text(ddl))

            # --- Constraint migrations for service_configs ---
            # Drop old unique constraint (service_id, filename) if it exists
            old_con = conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name='service_configs' "
                "AND constraint_name='uq_service_config_filename'"
            )).fetchone()
            if old_con:
                conn.execute(text(
                    "ALTER TABLE service_configs DROP CONSTRAINT uq_service_config_filename"
                ))

            # Also drop new-style constraint if it already exists (idempotent)
            new_con = conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name='service_configs' "
                "AND constraint_name='uq_service_config_filename_env'"
            )).fetchone()
            if new_con:
                conn.execute(text(
                    "ALTER TABLE service_configs DROP CONSTRAINT uq_service_config_filename_env"
                ))

            # Partial unique index: only one base (env_id IS NULL) config per (service, filename)
            idx_null = conn.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='service_configs' "
                "AND indexname='uq_svc_cfg_filename_global'"
            )).fetchone()
            if not idx_null:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_svc_cfg_filename_global "
                    "ON service_configs(service_id, filename) WHERE env_id IS NULL"
                ))

            # Partial unique index: one config per (service, filename, env) for env-specific configs
            idx_env = conn.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='service_configs' "
                "AND indexname='uq_svc_cfg_filename_env'"
            )).fetchone()
            if not idx_env:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_svc_cfg_filename_env "
                    "ON service_configs(service_id, filename, env_id) WHERE env_id IS NOT NULL"
                ))

            conn.commit()


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL',
        'postgresql://postgres:postgres@localhost:5432/svcmgr',
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    setup_logging(app)

    with app.app_context():
        db.create_all()
        _migrate_db(app)

    # ------------------------------------------------------------------
    # Context processor
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_globals():
        environments = Environment.query.order_by(Environment.name).all()
        current_env_id = session.get('current_env_id')
        current_env = db.session.get(Environment, current_env_id) if current_env_id else None
        return dict(environments=environments, current_env=current_env)

    # ------------------------------------------------------------------
    # ENV selection
    # ------------------------------------------------------------------
    @app.route('/select-env/<int:env_id>')
    def select_env(env_id):
        Environment.query.get_or_404(env_id)
        session['current_env_id'] = env_id
        return redirect(request.args.get('next') or url_for('dashboard'))

    @app.route('/clear-env')
    def clear_env():
        session.pop('current_env_id', None)
        return redirect(url_for('dashboard'))

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    @app.route('/')
    def dashboard():
        current_env_id = session.get('current_env_id')
        stats = {}
        if current_env_id:
            env = db.session.get(Environment, current_env_id)
            server_count = (
                Server.query
                .join(Server.environments)
                .filter(Environment.id == current_env_id)
                .count()
            )
            instance_count = (
                ServiceInstance.query
                .join(Server)
                .join(Server.environments)
                .filter(Environment.id == current_env_id)
                .count()
            )
            stats = {'env': env, 'servers': server_count, 'instances': instance_count}
        return render_template(
            'dashboard.html',
            stats=stats,
            service_count=Service.query.count(),
            env_count=Environment.query.count(),
            cred_count=Credential.query.count(),
        )

    # ==================================================================
    # Environments
    # ==================================================================
    @app.route('/environments')
    def env_list():
        envs = Environment.query.order_by(Environment.name).all()
        return render_template('environments/list.html', envs=envs)

    @app.route('/environments/create', methods=['GET', 'POST'])
    def env_create():
        if request.method == 'POST':
            name = request.form['name'].strip()
            if Environment.query.filter_by(name=name).first():
                flash(f'Окружение "{name}" уже существует.', 'danger')
            else:
                env = Environment(name=name, description=request.form.get('description', '').strip())
                db.session.add(env)
                db.session.commit()
                _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_ENVIRONMENT, env.id, env.name)
                flash(f'Окружение "{name}" создано.', 'success')
                return redirect(url_for('env_list'))
        return render_template('environments/form.html', env=None)

    @app.route('/environments/<int:env_id>/edit', methods=['GET', 'POST'])
    def env_edit(env_id):
        env = Environment.query.get_or_404(env_id)
        if request.method == 'POST':
            old_name = env.name
            env.name = request.form['name'].strip()
            env.description = request.form.get('description', '').strip()
            db.session.commit()
            _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_ENVIRONMENT, env.id, env.name,
                   details=f'name: {old_name!r} → {env.name!r}' if old_name != env.name else '')
            flash('Окружение обновлено.', 'success')
            return redirect(url_for('env_list'))
        return render_template('environments/form.html', env=env)

    @app.route('/environments/<int:env_id>/delete', methods=['POST'])
    def env_delete(env_id):
        env = Environment.query.get_or_404(env_id)
        name = env.name
        if session.get('current_env_id') == env.id:
            session.pop('current_env_id', None)
        db.session.delete(env)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_ENVIRONMENT, env_id, name)
        flash(f'Окружение "{name}" удалено.', 'success')
        return redirect(url_for('env_list'))

    # ==================================================================
    # Credentials
    # ==================================================================
    @app.route('/credentials')
    def credential_list():
        creds = Credential.query.order_by(Credential.name).all()
        return render_template('credentials/list.html', creds=creds)

    @app.route('/credentials/create', methods=['GET', 'POST'])
    def credential_create():
        if request.method == 'POST':
            name = request.form['name'].strip()
            if Credential.query.filter_by(name=name).first():
                flash(f'Учётная запись "{name}" уже существует.', 'danger')
            else:
                cred = Credential(
                    name=name,
                    username=request.form['username'].strip(),
                    password=request.form['password'],
                    description=request.form.get('description', '').strip(),
                )
                db.session.add(cred)
                db.session.commit()
                _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_CREDENTIAL,
                       cred.id, cred.name, details=f'username={cred.username}')
                flash(f'Учётная запись "{name}" создана.', 'success')
                return redirect(url_for('credential_list'))
        return render_template('credentials/form.html', cred=None)

    @app.route('/credentials/<int:cred_id>/edit', methods=['GET', 'POST'])
    def credential_edit(cred_id):
        cred = Credential.query.get_or_404(cred_id)
        if request.method == 'POST':
            old_username = cred.username
            cred.name = request.form['name'].strip()
            cred.username = request.form['username'].strip()
            pwd_changed = bool(request.form.get('password'))
            if pwd_changed:
                cred.password = request.form['password']
            cred.description = request.form.get('description', '').strip()
            db.session.commit()
            parts = []
            if old_username != cred.username:
                parts.append(f'username: {old_username!r} → {cred.username!r}')
            if pwd_changed:
                parts.append('password changed')
            _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CREDENTIAL,
                   cred.id, cred.name, details='; '.join(parts))
            flash('Учётная запись обновлена.', 'success')
            return redirect(url_for('credential_list'))
        return render_template('credentials/form.html', cred=cred)

    @app.route('/credentials/<int:cred_id>/delete', methods=['POST'])
    def credential_delete(cred_id):
        cred = Credential.query.get_or_404(cred_id)
        if cred.servers:
            flash(f'Нельзя удалить: используется {len(cred.servers)} сервером(ами).', 'danger')
            return redirect(url_for('credential_list'))
        name = cred.name
        db.session.delete(cred)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_CREDENTIAL, cred_id, name)
        flash(f'Учётная запись "{name}" удалена.', 'success')
        return redirect(url_for('credential_list'))

    # ==================================================================
    # Servers
    # ==================================================================
    @app.route('/servers')
    def server_list():
        current_env_id = session.get('current_env_id')
        query = Server.query
        if current_env_id:
            query = query.join(Server.environments).filter(Environment.id == current_env_id)
        servers = query.order_by(Server.hostname).all()
        return render_template('servers/list.html', servers=servers)

    @app.route('/servers/create', methods=['GET', 'POST'])
    def server_create():
        envs  = Environment.query.order_by(Environment.name).all()
        creds = Credential.query.order_by(Credential.name).all()
        if request.method == 'POST':
            env_ids = [int(i) for i in request.form.getlist('env_ids')]
            server = Server(
                hostname=request.form['hostname'].strip(),
                port=int(request.form.get('port') or 5985),
                use_ssl=bool(request.form.get('use_ssl')),
                credential_id=int(request.form['credential_id']),
                description=request.form.get('description', '').strip(),
            )
            server.environments = Environment.query.filter(Environment.id.in_(env_ids)).all()
            db.session.add(server)
            db.session.flush()

            ok, msg = winrm_utils.test_connection(server)
            server.is_available = ok
            server.last_checked = datetime.utcnow()
            db.session.commit()

            env_names = ', '.join(e.name for e in server.environments)
            _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_SERVER,
                   server.id, server.hostname,
                   details=f'envs=[{env_names}] winrm={"ok" if ok else "fail: " + msg}',
                   result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_WARNING)
            if ok:
                flash(f'Сервер "{server.hostname}" добавлен и доступен по WinRM.', 'success')
            else:
                flash(f'Сервер добавлен, но WinRM недоступен: {msg}', 'warning')
            return redirect(url_for('server_list'))
        return render_template('servers/form.html', server=None, envs=envs, creds=creds)

    @app.route('/servers/<int:server_id>/edit', methods=['GET', 'POST'])
    def server_edit(server_id):
        server = Server.query.get_or_404(server_id)
        envs  = Environment.query.order_by(Environment.name).all()
        creds = Credential.query.order_by(Credential.name).all()
        if request.method == 'POST':
            env_ids = [int(i) for i in request.form.getlist('env_ids')]
            old_hostname = server.hostname
            server.hostname      = request.form['hostname'].strip()
            server.port          = int(request.form.get('port') or 5985)
            server.use_ssl       = bool(request.form.get('use_ssl'))
            server.credential_id = int(request.form['credential_id'])
            server.description   = request.form.get('description', '').strip()
            server.environments  = Environment.query.filter(Environment.id.in_(env_ids)).all()
            db.session.commit()
            parts = []
            if old_hostname != server.hostname:
                parts.append(f'hostname: {old_hostname!r} → {server.hostname!r}')
            env_names = ', '.join(e.name for e in server.environments)
            parts.append(f'envs=[{env_names}]')
            _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_SERVER,
                   server.id, server.hostname, details='; '.join(parts))
            flash('Сервер обновлён.', 'success')
            return redirect(url_for('server_list'))
        return render_template('servers/form.html', server=server, envs=envs, creds=creds)

    @app.route('/servers/<int:server_id>/delete', methods=['POST'])
    def server_delete(server_id):
        server = Server.query.get_or_404(server_id)
        hostname = server.hostname
        db.session.delete(server)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_SERVER, server_id, hostname)
        flash(f'Сервер "{hostname}" удалён.', 'success')
        return redirect(url_for('server_list'))

    @app.route('/api/servers/<int:server_id>/services')
    def api_server_services(server_id):
        """Return list of Windows services on the server as JSON."""
        server = Server.query.get_or_404(server_id)
        services, error = winrm_utils.list_services(server)
        if error:
            return jsonify({'ok': False, 'error': error, 'services': []})
        return jsonify({'ok': True, 'error': None, 'services': services})

    @app.route('/servers/<int:server_id>/test', methods=['POST'])
    def server_test(server_id):
        server = Server.query.get_or_404(server_id)
        ok, msg = winrm_utils.test_connection(server)
        server.is_available = ok
        server.last_checked = datetime.utcnow()
        db.session.commit()
        _audit(AuditLog.ACTION_TEST_CONN, AuditLog.ENTITY_SERVER,
               server.id, server.hostname, details=msg,
               result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR)
        return jsonify({'ok': ok, 'message': msg})

    # ==================================================================
    # Services (catalog)
    # ==================================================================
    @app.route('/services')
    def service_list():
        services = Service.query.order_by(Service.name).all()
        return render_template('services/list.html', services=services)

    @app.route('/services/create', methods=['GET', 'POST'])
    def service_create():
        if request.method == 'POST':
            name = request.form['name'].strip()
            if Service.query.filter_by(name=name).first():
                flash(f'Сервис "{name}" уже существует.', 'danger')
            else:
                svc = Service(
                    name=name,
                    display_name=request.form.get('display_name', '').strip(),
                    description=request.form.get('description', '').strip(),
                )
                db.session.add(svc)
                db.session.commit()
                _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_SERVICE, svc.id, svc.name)
                flash(f'Сервис "{name}" добавлен.', 'success')
                return redirect(url_for('service_list'))
        return render_template('services/form.html', service=None)

    @app.route('/services/<int:service_id>/edit', methods=['GET', 'POST'])
    def service_edit(service_id):
        svc = Service.query.get_or_404(service_id)
        if request.method == 'POST':
            old_name = svc.name
            svc.name         = request.form['name'].strip()
            svc.display_name = request.form.get('display_name', '').strip()
            svc.description  = request.form.get('description', '').strip()
            db.session.commit()
            _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_SERVICE, svc.id, svc.name,
                   details=f'name: {old_name!r} → {svc.name!r}' if old_name != svc.name else '')
            flash('Сервис обновлён.', 'success')
            return redirect(url_for('service_list'))
        return render_template('services/form.html', service=svc)

    @app.route('/services/<int:service_id>/delete', methods=['POST'])
    def service_delete(service_id):
        svc = Service.query.get_or_404(service_id)
        name = svc.name
        db.session.delete(svc)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_SERVICE, service_id, name)
        flash(f'Сервис "{name}" удалён.', 'success')
        return redirect(url_for('service_list'))

    # ==================================================================
    # Service Virtual Configs
    # ==================================================================

    @app.route('/services/<int:service_id>/configs')
    def service_configs(service_id):
        svc = Service.query.get_or_404(service_id)
        env_filter_id = request.args.get('env_id', type=int)

        # Build sync summaries per config, instances filtered by env if specified
        sync_summaries = {}
        for cfg in svc.virtual_configs:
            cur = cfg.current_version
            # Instances relevant for this config: if config is env-specific, only that env
            target_env_id = cfg.env_id
            if target_env_id:
                relevant = [i for i in svc.instances
                            if any(e.id == target_env_id for e in i.server.environments)]
            else:
                relevant = svc.instances
            total = len(relevant)
            if not cur:
                sync_summaries[cfg.id] = {'synced': 0, 'overridden': 0, 'outdated': 0,
                                          'untracked': total, 'total': total, 'version': None}
                continue
            counts = {'synced': 0, 'overridden': 0, 'outdated': 0, 'untracked': 0}
            for inst in relevant:
                icfg = next((c for c in inst.configs if c.filename == cfg.filename), None)
                if icfg is None or icfg.source_version_id is None:
                    counts['untracked'] += 1
                elif icfg.is_overridden:
                    counts['overridden'] += 1
                elif icfg.source_version_id == cur.id:
                    counts['synced'] += 1
                else:
                    counts['outdated'] += 1
            counts['total'] = total
            counts['version'] = cur.version
            sync_summaries[cfg.id] = counts

        environments = Environment.query.order_by(Environment.name).all()
        # Collect env_ids that actually have configs for this service
        used_env_ids = {cfg.env_id for cfg in svc.virtual_configs}

        # Filter configs for display
        if env_filter_id == 0:   # 0 = base (env_id IS NULL)
            display_configs = [c for c in svc.virtual_configs if c.env_id is None]
        elif env_filter_id:
            display_configs = [c for c in svc.virtual_configs if c.env_id == env_filter_id]
        else:
            display_configs = svc.virtual_configs

        return render_template(
            'services/configs.html',
            svc=svc,
            sync_summaries=sync_summaries,
            environments=environments,
            used_env_ids=used_env_ids,
            env_filter_id=env_filter_id,
            display_configs=display_configs,
        )

    @app.route('/services/<int:service_id>/configs/create', methods=['GET', 'POST'])
    def service_config_create(service_id):
        svc = Service.query.get_or_404(service_id)
        environments = Environment.query.order_by(Environment.name).all()
        if request.method == 'POST':
            filename = request.form['filename'].strip()
            raw_env  = request.form.get('env_id', '').strip()
            env_id   = int(raw_env) if raw_env else None

            # Unique check: one base config and one per-env config per (service, filename)
            dup = ServiceConfig.query.filter_by(
                service_id=service_id, filename=filename, env_id=env_id
            ).first()
            if dup:
                env_label = f' (env #{env_id})' if env_id else ' (базовый)'
                flash(f'Файл "{filename}"{env_label} уже существует для этого сервиса.', 'danger')
            else:
                content = request.form.get('content', '')
                cfg = ServiceConfig(
                    service_id=service_id,
                    env_id=env_id,
                    filename=filename,
                    description=request.form.get('description', '').strip(),
                    content=content,
                )
                db.session.add(cfg)
                db.session.flush()

                client_ip = (
                    request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                    or request.remote_addr or 'unknown'
                )
                ver = ServiceConfigVersion(
                    service_config_id=cfg.id,
                    version=1,
                    content=content,
                    comment=request.form.get('comment', '').strip() or 'Первая версия',
                    is_current=True,
                    created_by=client_ip,
                )
                db.session.add(ver)
                db.session.commit()

                env_name = cfg.environment.name if cfg.environment else 'все env'
                _audit(AuditLog.ACTION_CREATE, 'service_config', cfg.id, filename,
                       details=f'service={svc.name} env={env_name} v1')
                flash(f'Виртуальный конфиг "{filename}" создан (v1).', 'success')
                return redirect(url_for('service_configs', service_id=service_id))
        return render_template('services/config_edit.html', svc=svc, cfg=None,
                               environments=environments)

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/edit', methods=['GET', 'POST'])
    def service_config_edit(service_id, cfg_id):
        svc = Service.query.get_or_404(service_id)
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        environments = Environment.query.order_by(Environment.name).all()
        if request.method == 'POST':
            new_content = request.form.get('content', '')
            new_filename = request.form['filename'].strip()
            raw_env = request.form.get('env_id', '').strip()
            new_env_id = int(raw_env) if raw_env else None

            # Check uniqueness only if filename or env_id changed
            if new_filename != cfg.filename or new_env_id != cfg.env_id:
                dup = ServiceConfig.query.filter(
                    ServiceConfig.service_id == service_id,
                    ServiceConfig.filename   == new_filename,
                    ServiceConfig.env_id     == new_env_id,
                    ServiceConfig.id         != cfg.id,
                ).first()
                if dup:
                    env_label = f' (env #{new_env_id})' if new_env_id else ' (базовый)'
                    flash(f'Файл "{new_filename}"{env_label} уже существует для этого сервиса.',
                          'danger')
                    return render_template('services/config_edit.html', svc=svc, cfg=cfg,
                                           environments=environments)

            cfg.filename    = new_filename
            cfg.env_id      = new_env_id
            cfg.description = request.form.get('description', '').strip()
            cfg.content     = new_content
            cfg.updated_at  = datetime.utcnow()

            client_ip = (
                request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                or request.remote_addr or 'unknown'
            )
            ServiceConfigVersion.query.filter_by(
                service_config_id=cfg.id
            ).update({'is_current': False})

            next_v = _next_version(cfg.id)
            ver = ServiceConfigVersion(
                service_config_id=cfg.id,
                version=next_v,
                content=new_content,
                comment=request.form.get('comment', '').strip() or f'Версия {next_v}',
                is_current=True,
                created_by=client_ip,
            )
            db.session.add(ver)
            db.session.commit()

            env_name = cfg.environment.name if cfg.environment else 'все env'
            _audit(AuditLog.ACTION_UPDATE, 'service_config', cfg.id, cfg.filename,
                   details=f'service={svc.name} env={env_name} v{next_v}')
            flash(f'Конфиг сохранён как версия v{next_v}.', 'success')
            return redirect(url_for('service_configs', service_id=service_id))
        return render_template('services/config_edit.html', svc=svc, cfg=cfg,
                               environments=environments)

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/delete', methods=['POST'])
    def service_config_delete(service_id, cfg_id):
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        filename = cfg.filename
        svc_name = cfg.service.name
        db.session.delete(cfg)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, 'service_config', cfg_id, filename,
               details=f'service={svc_name}')
        flash(f'Виртуальный конфиг "{filename}" удалён.', 'success')
        return redirect(url_for('service_configs', service_id=service_id))

    # ------------------------------------------------------------------
    # Service Config — Version history
    # ------------------------------------------------------------------

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/versions')
    def service_config_versions(service_id, cfg_id):
        svc = Service.query.get_or_404(service_id)
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        versions = (ServiceConfigVersion.query
                    .filter_by(service_config_id=cfg_id)
                    .order_by(ServiceConfigVersion.version.desc())
                    .all())
        return render_template('services/config_versions.html',
                               svc=svc, cfg=cfg, versions=versions)

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/versions/<int:ver_id>/activate',
               methods=['POST'])
    def service_config_version_activate(service_id, cfg_id, ver_id):
        svc = Service.query.get_or_404(service_id)
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        ver = ServiceConfigVersion.query.filter_by(
            id=ver_id, service_config_id=cfg_id
        ).first_or_404()

        # Снять is_current со всех версий, поставить на выбранную
        ServiceConfigVersion.query.filter_by(service_config_id=cfg_id).update({'is_current': False})
        ver.is_current = True
        cfg.content    = ver.content  # синхронизируем поле content
        cfg.updated_at = datetime.utcnow()
        db.session.commit()

        _audit(AuditLog.ACTION_ROLLBACK_CONFIG, 'service_config', cfg.id, cfg.filename,
               details=f'service={svc.name} rollback to v{ver.version}')
        flash(f'Конфиг "{cfg.filename}" откачен к версии v{ver.version}.', 'success')
        return redirect(url_for('service_config_versions', service_id=service_id, cfg_id=cfg_id))

    # ------------------------------------------------------------------
    # Service Config — Push to instances (async SSE)
    # ------------------------------------------------------------------

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/push')
    def service_config_push_page(service_id, cfg_id):
        svc = Service.query.get_or_404(service_id)
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        environments = Environment.query.order_by(Environment.name).all()

        # For env-specific configs, pre-filter instances to that env
        if cfg.env_id:
            relevant_instances = [i for i in svc.instances
                                  if any(e.id == cfg.env_id for e in i.server.environments)]
        else:
            relevant_instances = svc.instances

        icfg_map: dict = {}
        for inst in relevant_instances:
            icfg = next((c for c in inst.configs if c.filename == cfg.filename), None)
            cur  = cfg.current_version
            if icfg is None or icfg.source_version_id is None:
                status = 'untracked'
            elif icfg.is_overridden:
                status = 'overridden'
            elif cur and icfg.source_version_id == cur.id:
                status = 'synced'
            else:
                status = 'outdated'
            icfg_map[inst.id] = status
        return render_template('services/config_push.html',
                               svc=svc, cfg=cfg,
                               environments=environments,
                               relevant_instances=relevant_instances,
                               instance_statuses=icfg_map)

    @app.route('/services/<int:service_id>/configs/<int:cfg_id>/push', methods=['POST'])
    def service_config_push(service_id, cfg_id):
        svc = Service.query.get_or_404(service_id)
        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()

        cur_ver = cfg.current_version
        if not cur_ver:
            return jsonify({'ok': False, 'error': 'Нет активной версии для применения'}), 400

        data   = request.get_json(silent=True) or {}
        force  = bool(data.get('force', False))
        # env_id: from request, or fall back to config's own env scope
        req_env_id = data.get('env_id')
        effective_env_id = req_env_id or cfg.env_id

        q_inst = ServiceInstance.query.filter_by(service_id=service_id).join(Server)
        if effective_env_id:
            q_inst = (q_inst
                      .join(Server.environments)
                      .filter(Environment.id == int(effective_env_id)))
        instances = q_inst.all()

        if not instances:
            return jsonify({'ok': False, 'error': 'Нет экземпляров для применения'}), 400

        task_id   = str(uuid.uuid4())
        q_queue: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': q_queue, 'done': False}

        client_ip = (
            request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.remote_addr or 'unknown'
        )
        inst_ids    = [i.id for i in instances]
        ver_id      = cur_ver.id
        ver_num     = cur_ver.version
        cfg_fname   = cfg.filename
        svc_name    = svc.name

        def process_one(iid: int) -> dict:
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, iid)
                if inst_w is None:
                    r = {'instance_id': iid, 'ok': False,
                         'message': 'Экземпляр не найден', 'hostname': '?', 'win_name': '?'}
                    q_queue.put({'type': 'instance_done', **r})
                    return r

                hostname = inst_w.server.hostname
                win_name = inst_w.win_service_name

                existing = InstanceConfig.query.filter_by(
                    instance_id=iid, filename=cfg_fname
                ).first()

                if existing and existing.is_overridden and not force:
                    r = {'instance_id': iid, 'ok': False, 'skipped': True,
                         'message': 'Пропущен (конфиг изменён вручную, используйте Force)',
                         'hostname': hostname, 'win_name': win_name}
                    q_queue.put({'type': 'instance_done', **r})
                    return r

                q_queue.put({'type': 'progress', 'instance_id': iid,
                             'message': f'[{hostname}] Обновляю запись в БД…'})

                ver = db.session.get(ServiceConfigVersion, ver_id)
                content = ver.content or ''

                if existing:
                    filepath = existing.filepath
                    existing.content          = content
                    existing.source_version_id = ver_id
                    existing.is_overridden    = False
                    existing.updated_at       = datetime.utcnow()
                else:
                    filepath = ''
                    if inst_w.config_dir:
                        filepath = inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname
                    existing = InstanceConfig(
                        instance_id=iid,
                        filename=cfg_fname,
                        filepath=filepath,
                        content=content,
                        source_version_id=ver_id,
                        is_overridden=False,
                        encoding='utf-8',
                        fetched_at=datetime.utcnow(),
                    )
                    db.session.add(existing)

                db.session.flush()

                write_ok  = False
                write_msg = 'filepath не задан'
                if filepath:
                    q_queue.put({'type': 'progress', 'instance_id': iid,
                                 'message': f'[{hostname}] Записываю файл на сервер…'})
                    enc = existing.encoding or 'utf-8'
                    write_ok, write_msg = winrm_utils.write_file_content(
                        inst_w.server, filepath, content, enc
                    )

                db.session.commit()

                audit_result = (
                    AuditLog.RESULT_OK if (not filepath or write_ok)
                    else AuditLog.RESULT_WARNING
                )
                _audit(AuditLog.ACTION_PUSH_CONFIG, AuditLog.ENTITY_CONFIG,
                       iid, cfg_fname,
                       details=(
                           f'service={svc_name} v{ver_num} → '
                           f'instance={win_name} server={hostname}'
                           + (f' | write={"ok" if write_ok else "fail: " + write_msg}'
                              if filepath else ' | no_filepath')
                       ),
                       result=audit_result,
                       _ip=client_ip)

                msg = f'v{ver_num} применена'
                if filepath:
                    msg += f' | файл: {"записан" if write_ok else "ОШИБКА: " + write_msg}'

                r = {'instance_id': iid, 'ok': True,
                     'message': msg, 'hostname': hostname, 'win_name': win_name,
                     'write_ok': write_ok or not filepath}
                q_queue.put({'type': 'instance_done', **r})
                return r

        def worker():
            results: list[dict] = []
            try:
                max_w = max(1, min(len(inst_ids), 8))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futures = {pool.submit(process_one, iid): iid for iid in inst_ids}
                    for future in as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            iid = futures[future]
                            err = {'instance_id': iid, 'ok': False,
                                   'message': str(exc), 'hostname': '?', 'win_name': '?'}
                            results.append(err)
                            q_queue.put({'type': 'instance_done', **err})

                q_queue.put({'type': 'done_all',
                             'ok': all(r['ok'] for r in results),
                             'results': results,
                             'cfg_filename': cfg_fname,
                             'version': ver_num})
            except Exception as exc:
                q_queue.put({'type': 'done_all', 'ok': False,
                             'results': results, 'error': str(exc)})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'task_id': task_id})

    # ==================================================================
    # Service Instances
    # ==================================================================
    @app.route('/instances')
    def instance_list():
        current_env_id = session.get('current_env_id')
        query = ServiceInstance.query.join(Server)
        if current_env_id:
            query = query.join(Server.environments).filter(Environment.id == current_env_id)
        instances = query.order_by(Server.hostname, ServiceInstance.win_service_name).all()
        return render_template('instances/list.html', instances=instances)

    @app.route('/instances/create', methods=['GET', 'POST'])
    def instance_create():
        current_env_id = session.get('current_env_id')
        services = Service.query.order_by(Service.name).all()
        servers_q = Server.query
        if current_env_id:
            servers_q = servers_q.join(Server.environments).filter(Environment.id == current_env_id)
        servers = servers_q.order_by(Server.hostname).all()

        if request.method == 'POST':
            if request.is_json:
                items = request.json.get('items', [])
            else:
                server_ids  = request.form.getlist('server_id[]')
                win_names   = request.form.getlist('win_service_name[]')
                service_ids = request.form.getlist('service_id[]')
                items = [
                    {'server_id': srv, 'win_service_name': win, 'service_id': svc}
                    for srv, win, svc in zip(server_ids, win_names, service_ids)
                ]

            if not items:
                return jsonify({'ok': False, 'error': 'Список пуст'}), 400

            task_id = str(uuid.uuid4())
            q: queue.Queue = queue.Queue()
            _tasks[task_id] = {'q': q, 'done': False}

            client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                         or request.remote_addr or 'unknown')

            def process_one_instance(idx_item):
                idx, item = idx_item
                with app.app_context():
                    srv_id   = item.get('server_id')
                    win_name = str(item.get('win_service_name', '')).strip()
                    svc_id   = item.get('service_id')
                    if not win_name or not srv_id:
                        r = {'index': idx, 'ok': False, 'win_name': win_name,
                             'hostname': '', 'message': 'Неверные параметры'}
                        q.put({'type': 'item_done', **r})
                        return r

                    server = db.session.get(Server, int(srv_id))
                    if not server:
                        r = {'index': idx, 'ok': False, 'win_name': win_name,
                             'hostname': str(srv_id), 'message': 'Сервер не найден'}
                        q.put({'type': 'item_done', **r})
                        return r

                    q.put({'type': 'item_progress', 'index': idx,
                           'win_name': win_name, 'hostname': server.hostname,
                           'message': 'Получаю информацию о сервисе…'})
                    try:
                        info = winrm_utils.get_service_info(server, win_name)
                        exe_path   = info.get('exe_path') or ''
                        config_dir = winrm_utils.infer_config_dir(exe_path) if exe_path else ''

                        inst = ServiceInstance(
                            server_id=server.id,
                            service_id=int(svc_id),
                            win_service_name=win_name,
                            exe_path=exe_path,
                            config_dir=config_dir,
                            status=info.get('status', 'unknown'),
                            last_status_check=datetime.utcnow(),
                        )
                        db.session.add(inst)
                        db.session.flush()

                        cfg_count = 0
                        if config_dir:
                            q.put({'type': 'item_progress', 'index': idx,
                                   'win_name': win_name, 'hostname': server.hostname,
                                   'message': 'Загружаю конфиги…'})
                            for cf in winrm_utils.fetch_all_configs(server, config_dir):
                                db.session.add(InstanceConfig(
                                    instance_id=inst.id,
                                    filename=cf['filename'],
                                    filepath=cf['filepath'],
                                    content=cf['content'],
                                    encoding=cf['encoding'],
                                    fetched_at=cf['fetched_at'],
                                ))
                                cfg_count += 1

                        db.session.commit()
                        _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_INSTANCE,
                               inst.id, win_name,
                               details=(f'server={server.hostname}'
                                        f' config_dir={config_dir!r}'
                                        f' configs={cfg_count}'),
                               result=AuditLog.RESULT_WARNING if info.get('error') else AuditLog.RESULT_OK,
                               _ip=client_ip)
                        msg = info.get('error') or f'Конфигов: {cfg_count}'
                        r = {'index': idx, 'ok': True,
                             'win_name': win_name, 'hostname': server.hostname, 'message': msg}
                        q.put({'type': 'item_done', **r})
                        return r
                    except Exception as exc:
                        db.session.rollback()
                        r = {'index': idx, 'ok': False,
                             'win_name': win_name, 'hostname': server.hostname, 'message': str(exc)}
                        q.put({'type': 'item_done', **r})
                        return r

            def worker():
                results: list[dict] = []
                try:
                    max_w = max(1, min(len(items), 8))
                    with ThreadPoolExecutor(max_workers=max_w) as pool:
                        futures = {pool.submit(process_one_instance, (idx, item)): idx
                                   for idx, item in enumerate(items)}
                        for future in as_completed(futures):
                            try:
                                results.append(future.result())
                            except Exception as exc:
                                idx = futures[future]
                                r = {'index': idx, 'ok': False,
                                     'win_name': '', 'hostname': '', 'message': str(exc)}
                                results.append(r)
                                q.put({'type': 'item_done', **r})

                    created = sum(1 for r in results if r.get('ok'))
                    q.put({'type': 'done_all', 'ok': True, 'created': created,
                           'total': len(items)})
                except Exception as exc:
                    created = sum(1 for r in results if r.get('ok'))
                    q.put({'type': 'done_all', 'ok': False, 'created': created,
                           'total': len(items), 'error': str(exc)})
                finally:
                    if task_id in _tasks:
                        _tasks[task_id]['done'] = True

            threading.Thread(target=worker, daemon=True).start()
            return jsonify({'task_id': task_id})

        return render_template('instances/create.html', services=services, servers=servers)

    @app.route('/instances/<int:instance_id>')
    def instance_detail(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        # Sync-статус для каждого виртуального конфига
        icfg_map = {c.filename: c for c in inst.configs}
        sync_status = {}
        for vcfg in inst.service.virtual_configs:
            cur = vcfg.current_version
            icfg = icfg_map.get(vcfg.filename)
            if icfg is None or icfg.source_version_id is None:
                sync_status[vcfg.id] = 'untracked'
            elif icfg.is_overridden:
                sync_status[vcfg.id] = 'overridden'
            elif cur and icfg.source_version_id == cur.id:
                sync_status[vcfg.id] = 'synced'
            else:
                sync_status[vcfg.id] = 'outdated'
        return render_template('instances/detail.html', inst=inst, sync_status=sync_status)

    @app.route('/instances/<int:instance_id>/delete', methods=['POST'])
    def instance_delete(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        name     = inst.win_service_name
        hostname = inst.server.hostname
        db.session.delete(inst)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_INSTANCE,
               instance_id, name, details=f'server={hostname}')
        flash('Экземпляр удалён.', 'success')
        return redirect(url_for('instance_list'))

    @app.route('/instances/<int:instance_id>/refresh-status', methods=['POST'])
    def instance_refresh_status(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        status = winrm_utils.get_service_status(inst.server, inst.win_service_name)
        inst.status = status
        inst.last_status_check = datetime.utcnow()
        db.session.commit()
        _audit(AuditLog.ACTION_REFRESH_STATUS, AuditLog.ENTITY_INSTANCE,
               inst.id, inst.win_service_name,
               details=f'server={inst.server.hostname} status={status}')
        return jsonify({'status': status})

    @app.route('/instances/<int:instance_id>/refresh-configs', methods=['POST'])
    def instance_refresh_configs(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        if not inst.config_dir:
            return jsonify({'ok': False, 'message': 'config_dir не задан'})
        InstanceConfig.query.filter_by(instance_id=inst.id).delete()
        cfg_files = winrm_utils.fetch_all_configs(inst.server, inst.config_dir)
        for cf in cfg_files:
            db.session.add(InstanceConfig(
                instance_id=inst.id,
                filename=cf['filename'],
                filepath=cf['filepath'],
                content=cf['content'],
                encoding=cf['encoding'],
                fetched_at=cf['fetched_at'],
            ))
        db.session.commit()
        _audit(AuditLog.ACTION_REFRESH_CONFIGS, AuditLog.ENTITY_INSTANCE,
               inst.id, inst.win_service_name,
               details=f'server={inst.server.hostname} files={len(cfg_files)}')
        return jsonify({'ok': True, 'count': len(cfg_files)})

    @app.route('/instances/<int:instance_id>/configs/<int:config_id>', methods=['GET', 'POST'])
    def config_edit(instance_id, config_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        cfg  = InstanceConfig.query.filter_by(id=config_id, instance_id=instance_id).first_or_404()
        if request.method == 'POST':
            cfg.content      = request.form['content']
            cfg.updated_at   = datetime.utcnow()
            cfg.is_overridden = True  # ручное редактирование = отклонение от service config
            db.session.commit()
            _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CONFIG,
                   cfg.id, cfg.filename,
                   details=f'instance={inst.win_service_name} server={inst.server.hostname}')
            flash('Конфиг сохранён.', 'success')
            return redirect(url_for('instance_detail', instance_id=instance_id))
        return render_template('instances/config_edit.html', inst=inst, cfg=cfg)

    @app.route('/instances/<int:instance_id>/configs/<int:config_id>/delete', methods=['POST'])
    def config_delete(instance_id, config_id):
        cfg = InstanceConfig.query.filter_by(id=config_id, instance_id=instance_id).first_or_404()
        filename  = cfg.filename
        inst_name = cfg.instance.win_service_name
        db.session.delete(cfg)
        db.session.commit()
        _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_CONFIG,
               config_id, filename, details=f'instance={inst_name}')
        flash('Файл конфига удалён.', 'success')
        return redirect(url_for('instance_detail', instance_id=instance_id))

    # ==================================================================
    # Service Management (tree view + SSE control)
    # ==================================================================

    # In-memory task registry: task_id -> {'q': Queue, 'done': bool}
    _tasks: dict = {}

    # Map action string → AuditLog action constant + human label
    _ACTION_META = {
        'start':   (AuditLog.ACTION_START,   'Запуск'),
        'stop':    (AuditLog.ACTION_STOP,    'Остановка'),
        'restart': (AuditLog.ACTION_RESTART, 'Перезапуск'),
    }

    def _take_snapshot(inst, trigger: str, _ip: str = 'system') -> ConfigSnapshot:
        """Create and persist a config snapshot before a control operation."""
        configs_data = [
            {'filename': c.filename, 'filepath': c.filepath, 'content': c.content}
            for c in inst.configs
        ]
        snap = ConfigSnapshot(
            instance_id=inst.id,
            trigger=trigger,
            configs_json=json.dumps(configs_data, ensure_ascii=False),
        )
        db.session.add(snap)
        label = _ACTION_META.get(trigger, (None, trigger))[1]
        _audit(AuditLog.ACTION_SNAPSHOT, AuditLog.ENTITY_SNAPSHOT,
               inst.id, inst.win_service_name,
               details=f'перед операцией: {label} | server={inst.server.hostname} | файлов: {len(configs_data)}',
               _ip=_ip)
        return snap

    @app.route('/manage')
    def manage_index():
        current_env_id = session.get('current_env_id')
        query = ServiceInstance.query.join(Server)
        if current_env_id:
            query = query.join(Server.environments).filter(Environment.id == current_env_id)
        instances = query.order_by(ServiceInstance.service_id, Server.hostname,
                                   ServiceInstance.win_service_name).all()

        services_map: dict = {}
        for inst in instances:
            sid = inst.service_id
            if sid not in services_map:
                services_map[sid] = {'service': inst.service, 'instances': []}
            services_map[sid]['instances'].append(inst)

        return render_template('manage/index.html', service_groups=list(services_map.values()))

    # ------------------------------------------------------------------
    # SSE task stream
    # ------------------------------------------------------------------
    @app.route('/manage/tasks/<task_id>/stream')
    def manage_task_stream(task_id):
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404

        def generate():
            q = task['q']
            while True:
                try:
                    event = q.get(timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get('type') in ('done', 'done_all'):
                        break
                except queue.Empty:
                    yield 'data: {"type":"heartbeat"}\n\n'
                    if task.get('done'):
                        break
            _tasks.pop(task_id, None)

        return Response(
            stream_with_context(generate()),
            content_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                     'Connection': 'keep-alive'},
        )

    # ------------------------------------------------------------------
    # Control single instance  →  POST returns task_id immediately
    # ------------------------------------------------------------------
    @app.route('/manage/instances/<int:instance_id>/control', methods=['POST'])
    def manage_instance_control(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        action = request.json.get('action', '') if request.is_json else request.form.get('action', '')
        if action not in ('start', 'stop', 'restart'):
            return jsonify({'ok': False, 'error': 'Invalid action'}), 400

        task_id = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': q, 'done': False}

        client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                     or request.remote_addr or 'unknown')
        inst_id = inst.id

        action_const, action_label = _ACTION_META[action]

        def worker():
            try:
                with app.app_context():
                    inst_w = db.session.get(ServiceInstance, inst_id)
                    if inst_w is None:
                        q.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                               'message': 'Экземпляр не найден', 'status': 'unknown'})
                        return

                    q.put({'type': 'progress', 'instance_id': inst_id,
                           'message': 'Снимаю снэпшот конфигурации…'})
                    snap = _take_snapshot(inst_w, action, _ip=client_ip)

                    q.put({'type': 'progress', 'instance_id': inst_id,
                           'message': f'{action_label}…', 'snap_id': snap.id})
                    ok, msg = winrm_utils.control_service(inst_w.server, inst_w.win_service_name, action)

                    q.put({'type': 'progress', 'instance_id': inst_id,
                           'message': 'Проверяю статус…'})
                    new_status = winrm_utils.get_service_status(inst_w.server, inst_w.win_service_name)
                    inst_w.status = new_status
                    inst_w.last_status_check = datetime.utcnow()
                    db.session.commit()

                    details = f'server={inst_w.server.hostname} | статус: {new_status}'
                    if not ok:
                        details += f' | ошибка: {msg}'
                    _audit(action_const, AuditLog.ENTITY_INSTANCE,
                           inst_w.id, inst_w.win_service_name,
                           details=details,
                           result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                           _ip=client_ip)

                    q.put({'type': 'done', 'instance_id': inst_id, 'ok': ok,
                           'message': msg, 'status': new_status, 'snap_id': snap.id})
            except Exception as exc:
                q.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                       'message': str(exc), 'status': 'unknown'})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'task_id': task_id})

    # ------------------------------------------------------------------
    # Control all instances of a service  →  POST returns task_id
    # ------------------------------------------------------------------
    @app.route('/manage/services/<int:service_id>/control', methods=['POST'])
    def manage_service_control(service_id):
        svc = Service.query.get_or_404(service_id)
        action = request.json.get('action', '') if request.is_json else request.form.get('action', '')
        if action not in ('start', 'stop', 'restart'):
            return jsonify({'ok': False, 'error': 'Invalid action'}), 400

        current_env_id = session.get('current_env_id')
        q_inst = ServiceInstance.query.filter_by(service_id=service_id).join(Server)
        if current_env_id:
            q_inst = q_inst.join(Server.environments).filter(Environment.id == current_env_id)
        inst_ids = [i.id for i in q_inst.all()]

        task_id = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': q, 'done': False}

        client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                     or request.remote_addr or 'unknown')

        action_const, action_label = _ACTION_META[action]

        def process_one(iid: int) -> dict:
            """Runs in its own thread with its own app context."""
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, iid)
                if inst_w is None:
                    res = {'instance_id': iid, 'ok': False,
                           'message': 'Экземпляр не найден', 'status': 'unknown', 'snap_id': None}
                    q.put({'type': 'instance_done', **res})
                    return res

                svc_name = inst_w.win_service_name
                q.put({'type': 'progress', 'instance_id': iid,
                       'message': f'[{svc_name}] Снимаю снэпшот…'})
                snap = _take_snapshot(inst_w, action, _ip=client_ip)

                q.put({'type': 'progress', 'instance_id': iid,
                       'message': f'[{svc_name}] {action_label}…', 'snap_id': snap.id})
                ok, msg = winrm_utils.control_service(inst_w.server, svc_name, action)

                q.put({'type': 'progress', 'instance_id': iid,
                       'message': f'[{svc_name}] Проверяю статус…'})
                new_status = winrm_utils.get_service_status(inst_w.server, svc_name)
                inst_w.status = new_status
                inst_w.last_status_check = datetime.utcnow()
                db.session.commit()

                details = f'server={inst_w.server.hostname} | bulk | статус: {new_status}'
                if not ok:
                    details += f' | ошибка: {msg}'
                _audit(action_const, AuditLog.ENTITY_INSTANCE,
                       inst_w.id, svc_name, details=details,
                       result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                       _ip=client_ip)

                res = {'instance_id': iid, 'ok': ok, 'message': msg,
                       'status': new_status, 'snap_id': snap.id}
                q.put({'type': 'instance_done', **res})
                return res

        def worker():
            results: list[dict] = []
            try:
                max_w = max(1, min(len(inst_ids), 8))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futures = {pool.submit(process_one, iid): iid for iid in inst_ids}
                    for future in as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            iid = futures[future]
                            err = {'instance_id': iid, 'ok': False,
                                   'message': str(exc), 'status': 'unknown', 'snap_id': None}
                            results.append(err)
                            q.put({'type': 'instance_done', **err})

                q.put({'type': 'done_all', 'service_id': service_id,
                       'ok': all(r['ok'] for r in results), 'results': results})
            except Exception as exc:
                q.put({'type': 'done_all', 'service_id': service_id,
                       'ok': False, 'results': results, 'error': str(exc)})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'task_id': task_id})

    # ==================================================================
    # Manage — Config Management API
    # ==================================================================

    @app.route('/api/services/<int:service_id>/config-summary')
    def api_service_config_summary(service_id):
        """Конфиги сервиса + все версии + статус по каждому экземпляру (lazy-load)."""
        svc = Service.query.get_or_404(service_id)
        result = []
        for cfg in svc.virtual_configs:
            cur = cfg.current_version
            versions = [
                {'id': v.id, 'version': v.version, 'comment': v.comment or '',
                 'created_at': v.created_at.strftime('%d.%m.%Y %H:%M'),
                 'is_current': v.is_current}
                for v in cfg.versions
            ]
            # For env-specific configs show only instances in that env
            if cfg.env_id:
                relevant = [i for i in svc.instances
                            if any(e.id == cfg.env_id for e in i.server.environments)]
            else:
                relevant = svc.instances

            instances_status = []
            for inst in relevant:
                icfg = next((c for c in inst.configs if c.filename == cfg.filename), None)
                if icfg is None or icfg.source_version_id is None:
                    st, inst_ver_num = 'untracked', None
                elif icfg.is_overridden:
                    sv = db.session.get(ServiceConfigVersion, icfg.source_version_id)
                    st, inst_ver_num = 'overridden', (sv.version if sv else None)
                elif cur and icfg.source_version_id == cur.id:
                    st, inst_ver_num = 'synced', cur.version
                else:
                    sv = db.session.get(ServiceConfigVersion, icfg.source_version_id)
                    st, inst_ver_num = 'outdated', (sv.version if sv else None)
                instances_status.append({
                    'instance_id': inst.id,
                    'win_name':    inst.win_service_name,
                    'hostname':    inst.server.hostname,
                    'status':      st,
                    'version':     inst_ver_num,
                })
            result.append({
                'id': cfg.id, 'filename': cfg.filename, 'description': cfg.description or '',
                'env_id': cfg.env_id,
                'env_label': cfg.environment.name if cfg.environment else '',
                'current_version': cur.version if cur else None,
                'current_version_id': cur.id if cur else None,
                'versions': versions, 'instances': instances_status,
            })
        return jsonify({'service_id': service_id, 'service_name': svc.name,
                        'display_name': svc.display_name or svc.name, 'configs': result})

    # ------------------------------------------------------------------
    # Deploy config to ALL instances of a service + optional restart
    # ------------------------------------------------------------------
    @app.route('/manage/services/<int:service_id>/config-deploy', methods=['POST'])
    def manage_service_config_deploy(service_id):
        svc  = Service.query.get_or_404(service_id)
        data = request.get_json(silent=True) or {}
        cfg_id     = data.get('cfg_id')
        ver_id     = data.get('ver_id')
        do_restart = bool(data.get('restart', True))
        env_id     = data.get('env_id')
        force      = bool(data.get('force', True))

        cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=service_id).first_or_404()
        ver = ServiceConfigVersion.query.filter_by(
            id=ver_id, service_config_id=cfg_id).first_or_404()

        q_inst = ServiceInstance.query.filter_by(service_id=service_id).join(Server)
        if env_id:
            q_inst = q_inst.join(Server.environments).filter(Environment.id == int(env_id))
        instances = q_inst.all()
        if not instances:
            return jsonify({'ok': False, 'error': 'Нет экземпляров'}), 400

        task_id = str(uuid.uuid4())
        dq: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': dq, 'done': False}

        client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                     or request.remote_addr or 'unknown')
        inst_ids  = [i.id for i in instances]
        _ver_id   = ver.id
        ver_num   = ver.version
        cfg_fname = cfg.filename
        svc_name  = svc.name

        def _deploy_one(iid: int) -> dict:
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, iid)
                if inst_w is None:
                    r = {'instance_id': iid, 'ok': False, 'message': 'Не найден',
                         'hostname': '?', 'win_name': '?', 'status': 'unknown'}
                    dq.put({'type': 'instance_done', **r}); return r

                hostname = inst_w.server.hostname
                win_name = inst_w.win_service_name
                existing = InstanceConfig.query.filter_by(
                    instance_id=iid, filename=cfg_fname).first()

                if existing and existing.is_overridden and not force:
                    r = {'instance_id': iid, 'ok': False, 'skipped': True,
                         'message': 'Пропущен (изменён вручную)',
                         'hostname': hostname, 'win_name': win_name, 'status': inst_w.status}
                    dq.put({'type': 'instance_done', **r}); return r

                # 1. Write config
                dq.put({'type': 'progress', 'instance_id': iid,
                        'message': f'[{hostname}] Записываю {cfg_fname}…'})
                ver_obj  = db.session.get(ServiceConfigVersion, _ver_id)
                content  = ver_obj.content or ''
                filepath = (existing.filepath if existing else '') or ''
                if not filepath and inst_w.config_dir:
                    filepath = inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname

                write_ok, write_msg = False, 'filepath не задан'
                if filepath:
                    enc = (existing.encoding if existing else None) or 'utf-8'
                    write_ok, write_msg = winrm_utils.write_file_content(
                        inst_w.server, filepath, content, enc)

                if existing:
                    existing.content           = content
                    existing.source_version_id = _ver_id
                    existing.is_overridden     = False
                    existing.updated_at        = datetime.utcnow()
                else:
                    db.session.add(InstanceConfig(
                        instance_id=iid, filename=cfg_fname, filepath=filepath,
                        content=content, source_version_id=_ver_id,
                        is_overridden=False, encoding='utf-8', fetched_at=datetime.utcnow()))
                db.session.flush()

                # 2. Restart
                restart_ok, restart_msg, new_status = True, '', inst_w.status
                if do_restart:
                    dq.put({'type': 'progress', 'instance_id': iid,
                            'message': f'[{hostname}] Перезапуск {win_name}…'})
                    restart_ok, restart_msg = winrm_utils.control_service(
                        inst_w.server, win_name, 'restart')
                    new_status = winrm_utils.get_service_status(inst_w.server, win_name)
                    inst_w.status = new_status
                    inst_w.last_status_check = datetime.utcnow()
                db.session.commit()

                ok    = (write_ok or not filepath) and restart_ok
                parts = []
                if filepath:
                    parts.append(f'файл: {"ok" if write_ok else "ERR: " + write_msg}')
                if do_restart:
                    parts.append(f'restart: {"ok → " + new_status if restart_ok else "ERR: " + restart_msg}')
                _audit(AuditLog.ACTION_PUSH_CONFIG, AuditLog.ENTITY_INSTANCE, iid, cfg_fname,
                       details=f'service={svc_name} v{ver_num} → {win_name}@{hostname}'
                               + (' +restart' if do_restart else '')
                               + (f' | {", ".join(parts)}' if parts else ''),
                       result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_WARNING,
                       _ip=client_ip)

                r = {'instance_id': iid, 'ok': ok, 'message': '; '.join(parts) or 'ok',
                     'hostname': hostname, 'win_name': win_name,
                     'status': new_status, 'version': ver_num}
                dq.put({'type': 'instance_done', **r}); return r

        def deploy_worker():
            results: list[dict] = []
            try:
                max_w = max(1, min(len(inst_ids), 8))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futures = {pool.submit(_deploy_one, iid): iid for iid in inst_ids}
                    for future in as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            iid = futures[future]
                            err = {'instance_id': iid, 'ok': False, 'message': str(exc),
                                   'hostname': '?', 'win_name': '?', 'status': 'unknown'}
                            results.append(err)
                            dq.put({'type': 'instance_done', **err})
                dq.put({'type': 'done_all', 'ok': all(r['ok'] for r in results),
                        'results': results, 'cfg_filename': cfg_fname, 'version': ver_num})
            except Exception as exc:
                dq.put({'type': 'done_all', 'ok': False, 'results': results, 'error': str(exc)})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=deploy_worker, daemon=True).start()
        return jsonify({'task_id': task_id})

    # ------------------------------------------------------------------
    # Deploy config to ONE instance + optional restart
    # ------------------------------------------------------------------
    @app.route('/manage/instances/<int:instance_id>/config-deploy', methods=['POST'])
    def manage_instance_config_deploy(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        data = request.get_json(silent=True) or {}
        cfg_id     = data.get('cfg_id')
        ver_id     = data.get('ver_id')
        do_restart = bool(data.get('restart', True))

        cfg = ServiceConfig.query.filter_by(
            id=cfg_id, service_id=inst.service_id).first_or_404()
        ver = ServiceConfigVersion.query.filter_by(
            id=ver_id, service_config_id=cfg_id).first_or_404()

        task_id = str(uuid.uuid4())
        dq: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': dq, 'done': False}

        client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                     or request.remote_addr or 'unknown')
        inst_id   = inst.id
        _ver_id   = ver.id
        ver_num   = ver.version
        cfg_fname = cfg.filename
        svc_name  = inst.service.name

        def worker():
            try:
                with app.app_context():
                    inst_w   = db.session.get(ServiceInstance, inst_id)
                    hostname = inst_w.server.hostname
                    win_name = inst_w.win_service_name

                    dq.put({'type': 'progress', 'instance_id': inst_id,
                            'message': f'Записываю {cfg_fname}…'})
                    ver_obj  = db.session.get(ServiceConfigVersion, _ver_id)
                    content  = ver_obj.content or ''
                    existing = InstanceConfig.query.filter_by(
                        instance_id=inst_id, filename=cfg_fname).first()
                    filepath = (existing.filepath if existing else '') or ''
                    if not filepath and inst_w.config_dir:
                        filepath = inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname

                    write_ok, write_msg = False, 'filepath не задан'
                    if filepath:
                        enc = (existing.encoding if existing else None) or 'utf-8'
                        write_ok, write_msg = winrm_utils.write_file_content(
                            inst_w.server, filepath, content, enc)

                    if existing:
                        existing.content           = content
                        existing.source_version_id = _ver_id
                        existing.is_overridden     = False
                        existing.updated_at        = datetime.utcnow()
                    else:
                        db.session.add(InstanceConfig(
                            instance_id=inst_id, filename=cfg_fname, filepath=filepath,
                            content=content, source_version_id=_ver_id,
                            is_overridden=False, encoding='utf-8', fetched_at=datetime.utcnow()))
                    db.session.flush()

                    restart_ok, restart_msg, new_status = True, '', inst_w.status
                    if do_restart:
                        dq.put({'type': 'progress', 'instance_id': inst_id,
                                'message': f'Перезапуск {win_name}…'})
                        restart_ok, restart_msg = winrm_utils.control_service(
                            inst_w.server, win_name, 'restart')
                        new_status = winrm_utils.get_service_status(inst_w.server, win_name)
                        inst_w.status = new_status
                        inst_w.last_status_check = datetime.utcnow()
                    db.session.commit()

                    ok    = (write_ok or not filepath) and restart_ok
                    parts = []
                    if filepath:
                        parts.append(f'файл: {"ok" if write_ok else "ERR: " + write_msg}')
                    if do_restart:
                        parts.append(f'restart: {"ok → " + new_status if restart_ok else "ERR: " + restart_msg}')
                    _audit(AuditLog.ACTION_PUSH_CONFIG, AuditLog.ENTITY_INSTANCE,
                           inst_id, cfg_fname,
                           details=f'service={svc_name} v{ver_num} → {win_name}@{hostname}'
                                   + (' +restart' if do_restart else '')
                                   + (f' | {", ".join(parts)}' if parts else ''),
                           result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_WARNING,
                           _ip=client_ip)
                    dq.put({'type': 'done', 'instance_id': inst_id, 'ok': ok,
                            'message': '; '.join(parts) or 'ok',
                            'status': new_status, 'version': ver_num,
                            'hostname': hostname, 'win_name': win_name})
            except Exception as exc:
                dq.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                        'message': str(exc), 'status': 'unknown'})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'task_id': task_id})

    # ------------------------------------------------------------------
    # Snapshot API
    # ------------------------------------------------------------------
    @app.route('/manage/instances/<int:instance_id>/snapshots')
    def manage_instance_snapshots(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        snaps = (ConfigSnapshot.query
                 .filter_by(instance_id=instance_id)
                 .order_by(ConfigSnapshot.created_at.desc())
                 .limit(20).all())
        return jsonify({'instance': inst.win_service_name, 'snapshots': [
            {'id': s.id, 'trigger': s.trigger,
             'created_at': s.created_at.strftime('%d.%m.%Y %H:%M:%S'),
             'files': len(json.loads(s.configs_json or '[]'))}
            for s in snaps
        ]})

    @app.route('/manage/snapshots/<int:snapshot_id>')
    def manage_snapshot_detail(snapshot_id):
        snap = ConfigSnapshot.query.get_or_404(snapshot_id)
        return jsonify({
            'id': snap.id, 'instance_id': snap.instance_id, 'trigger': snap.trigger,
            'created_at': snap.created_at.strftime('%d.%m.%Y %H:%M:%S'),
            'configs': json.loads(snap.configs_json or '[]'),
        })

    @app.route('/manage/snapshots/<int:snapshot_id>/restore', methods=['POST'])
    def manage_snapshot_restore(snapshot_id):
        snap = ConfigSnapshot.query.get_or_404(snapshot_id)
        inst = db.session.get(ServiceInstance, snap.instance_id)
        if inst is None:
            return jsonify({'ok': False, 'error': 'Экземпляр не найден'}), 404
        configs_data = json.loads(snap.configs_json or '[]')
        # Replace current InstanceConfig set with snapshot contents
        InstanceConfig.query.filter_by(instance_id=inst.id).delete()
        for item in configs_data:
            db.session.add(InstanceConfig(
                instance_id=inst.id,
                filename=item['filename'],
                filepath=item.get('filepath', ''),
                content=item.get('content', ''),
                encoding='utf-8',
                fetched_at=datetime.utcnow(),
            ))
        db.session.commit()
        client_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                     or request.remote_addr or 'unknown')
        _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CONFIG,
               snap.id, f'restore snap#{snap.id}',
               details=(f'instance={inst.win_service_name} server={inst.server.hostname}'
                        f' | восстановлено файлов: {len(configs_data)}'),
               _ip=client_ip)
        return jsonify({'ok': True, 'restored': len(configs_data)})

    # ==================================================================
    # Audit journal
    # ==================================================================
    @app.route('/audit')
    def audit_list():
        page   = request.args.get('page', 1, type=int)
        action = request.args.get('action', '')
        entity = request.args.get('entity', '')
        result = request.args.get('result', '')
        search = request.args.get('q', '').strip()

        query = AuditLog.query
        if action:
            query = query.filter(AuditLog.action == action)
        if entity:
            query = query.filter(AuditLog.entity_type == entity)
        if result:
            query = query.filter(AuditLog.result == result)
        if search:
            like = f'%{search}%'
            query = query.filter(
                db.or_(
                    AuditLog.entity_name.ilike(like),
                    AuditLog.details.ilike(like),
                    AuditLog.ip_address.ilike(like),
                )
            )

        pagination = query.order_by(AuditLog.created_at.desc()).paginate(
            page=page, per_page=50, error_out=False
        )
        return render_template('audit/list.html', pagination=pagination,
                               action=action, entity=entity,
                               result=result, search=search)

    # ==================================================================
    # Config Scan — фоновый скан конфигов экземпляров
    # ==================================================================

    @app.route('/instances/scan-configs', methods=['POST'])
    def instance_scan_configs():
        """
        Запускает параллельный скан конфигов всех (или выбранных по env) экземпляров.
        Сравнивает актуальное содержимое на серверах с сохранённым в БД.
        Возвращает task_id для SSE-стрима.
        """
        data = request.get_json(silent=True) or {}
        env_id = data.get('env_id')

        q_inst = ServiceInstance.query.join(Server)
        if env_id:
            q_inst = q_inst.join(Server.environments).filter(Environment.id == int(env_id))
        instances = q_inst.all()

        if not instances:
            return jsonify({'ok': False, 'error': 'Нет экземпляров для сканирования'}), 400

        task_id = str(uuid.uuid4())
        scan_q: queue.Queue = queue.Queue()
        _tasks[task_id] = {'q': scan_q, 'done': False}

        inst_ids = [i.id for i in instances]

        def scan_one(iid: int) -> dict:
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, iid)
                if inst_w is None:
                    r = {'instance_id': iid, 'ok': False, 'hostname': '?',
                         'win_name': '?', 'message': 'Не найден', 'diffs': []}
                    scan_q.put({'type': 'scan_done', **r})
                    return r

                hostname = inst_w.server.hostname
                win_name = inst_w.win_service_name
                scan_q.put({'type': 'scan_progress', 'instance_id': iid,
                            'message': f'[{hostname}] {win_name}: чтение конфигов…'})

                if not inst_w.config_dir:
                    r = {'instance_id': iid, 'ok': True, 'hostname': hostname,
                         'win_name': win_name, 'message': 'config_dir не задан', 'diffs': []}
                    scan_q.put({'type': 'scan_done', **r})
                    return r

                try:
                    live_files = winrm_utils.fetch_all_configs(inst_w.server, inst_w.config_dir)
                except Exception as exc:
                    r = {'instance_id': iid, 'ok': False, 'hostname': hostname,
                         'win_name': win_name, 'message': f'Ошибка WinRM: {exc}', 'diffs': []}
                    scan_q.put({'type': 'scan_done', **r})
                    return r

                stored = {c.filename: c.content or '' for c in inst_w.configs}
                live   = {f['filename']: f['content'] or '' for f in live_files}

                diffs = []
                all_names = set(stored) | set(live)
                for fname in sorted(all_names):
                    s = stored.get(fname)
                    l = live.get(fname)
                    if s is None:
                        diffs.append({'file': fname, 'status': 'new_on_server'})
                    elif l is None:
                        diffs.append({'file': fname, 'status': 'missing_on_server'})
                    elif s.strip() != l.strip():
                        diffs.append({'file': fname, 'status': 'changed'})
                    else:
                        diffs.append({'file': fname, 'status': 'ok'})

                changed = sum(1 for d in diffs if d['status'] != 'ok')
                msg = f'{len(diffs)} файл(ов), изменено: {changed}' if diffs else 'нет конфигов'
                r = {'instance_id': iid, 'ok': True, 'hostname': hostname,
                     'win_name': win_name, 'message': msg,
                     'diffs': diffs, 'changed': changed}
                scan_q.put({'type': 'scan_done', **r})
                return r

        def worker():
            results: list[dict] = []
            try:
                max_w = max(1, min(len(inst_ids), 8))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futures = {pool.submit(scan_one, iid): iid for iid in inst_ids}
                    for future in as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            iid = futures[future]
                            r = {'instance_id': iid, 'ok': False,
                                 'hostname': '?', 'win_name': '?',
                                 'message': str(exc), 'diffs': []}
                            results.append(r)
                            scan_q.put({'type': 'scan_done', **r})

                total_changed = sum(r.get('changed', 0) for r in results)
                scan_q.put({'type': 'done_all', 'ok': True,
                            'total': len(results), 'total_changed': total_changed})
            except Exception as exc:
                scan_q.put({'type': 'done_all', 'ok': False, 'error': str(exc),
                            'total': len(results), 'total_changed': 0})
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'task_id': task_id, 'total': len(inst_ids)})

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
