"""Service Management Portal — Flask application."""
import json
import logging
import os
import queue
import threading
import uuid
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # загружает .env до инициализации приложения

from flask import (
    Flask, render_template, redirect, url_for, request,
    flash, session, jsonify, Response, stream_with_context,
)
from models import db, AuditLog, ConfigSnapshot, Environment, Credential, Server, Service, ServiceInstance, InstanceConfig
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
            server_ids  = request.form.getlist('server_id[]')
            win_names   = request.form.getlist('win_service_name[]')
            service_ids = request.form.getlist('service_id[]')

            created, errors = 0, []
            for srv_id, win_name, service_id in zip(server_ids, win_names, service_ids):
                service_id = int(service_id)
                win_name = win_name.strip()
                if not win_name or not srv_id:
                    continue
                server = db.session.get(Server, int(srv_id))
                if not server:
                    continue

                info = winrm_utils.get_service_info(server, win_name)
                if info.get('error'):
                    errors.append(f'{server.hostname}/{win_name}: {info["error"]}')

                exe_path   = info.get('exe_path') or ''
                config_dir = winrm_utils.infer_config_dir(exe_path) if exe_path else ''

                inst = ServiceInstance(
                    server_id=server.id,
                    service_id=service_id,
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

                _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_INSTANCE,
                       inst.id, win_name,
                       details=f'server={server.hostname} config_dir={config_dir!r} configs={cfg_count}',
                       result=AuditLog.RESULT_WARNING if info.get('error') else AuditLog.RESULT_OK)
                created += 1

            db.session.commit()
            if created:
                flash(f'Добавлено экземпляров: {created}.', 'success')
            for e in errors:
                flash(e, 'warning')
            return redirect(url_for('instance_list'))

        return render_template('instances/create.html', services=services, servers=servers)

    @app.route('/instances/<int:instance_id>')
    def instance_detail(instance_id):
        inst = ServiceInstance.query.get_or_404(instance_id)
        return render_template('instances/detail.html', inst=inst)

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
            cfg.content    = request.form['content']
            cfg.updated_at = datetime.utcnow()
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
        _audit(AuditLog.ACTION_SNAPSHOT, AuditLog.ENTITY_SNAPSHOT,
               inst.id, inst.win_service_name,
               details=f'trigger={trigger} server={inst.server.hostname} files={len(configs_data)}',
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
                           'message': f'Выполняю {action}…', 'snap_id': snap.id})
                    ok, msg = winrm_utils.control_service(inst_w.server, inst_w.win_service_name, action)

                    q.put({'type': 'progress', 'instance_id': inst_id,
                           'message': 'Проверяю статус…'})
                    new_status = winrm_utils.get_service_status(inst_w.server, inst_w.win_service_name)
                    inst_w.status = new_status
                    inst_w.last_status_check = datetime.utcnow()
                    db.session.commit()

                    _audit(AuditLog.ACTION_CONTROL, AuditLog.ENTITY_INSTANCE,
                           inst_w.id, inst_w.win_service_name,
                           details=f'action={action} server={inst_w.server.hostname} result={msg} status={new_status}',
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

        def worker():
            results = []
            try:
                with app.app_context():
                    for inst_id in inst_ids:
                        inst_w = db.session.get(ServiceInstance, inst_id)
                        if inst_w is None:
                            results.append({'instance_id': inst_id, 'ok': False,
                                            'message': 'Not found', 'status': 'unknown'})
                            continue

                        q.put({'type': 'progress', 'instance_id': inst_id,
                               'message': f'[{inst_w.win_service_name}] Снимаю снэпшот…'})
                        snap = _take_snapshot(inst_w, action, _ip=client_ip)

                        q.put({'type': 'progress', 'instance_id': inst_id,
                               'message': f'[{inst_w.win_service_name}] Выполняю {action}…',
                               'snap_id': snap.id})
                        ok, msg = winrm_utils.control_service(inst_w.server, inst_w.win_service_name, action)

                        q.put({'type': 'progress', 'instance_id': inst_id,
                               'message': f'[{inst_w.win_service_name}] Проверяю статус…'})
                        new_status = winrm_utils.get_service_status(inst_w.server, inst_w.win_service_name)
                        inst_w.status = new_status
                        inst_w.last_status_check = datetime.utcnow()
                        db.session.commit()

                        _audit(AuditLog.ACTION_CONTROL, AuditLog.ENTITY_INSTANCE,
                               inst_w.id, inst_w.win_service_name,
                               details=f'action={action} bulk=service#{service_id} server={inst_w.server.hostname} result={msg} status={new_status}',
                               result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                               _ip=client_ip)

                        res = {'instance_id': inst_id, 'ok': ok, 'message': msg,
                               'status': new_status, 'snap_id': snap.id}
                        results.append(res)
                        q.put({'type': 'instance_done', **res})

            except Exception as exc:
                q.put({'type': 'done_all', 'service_id': service_id, 'ok': False,
                       'results': results, 'error': str(exc)})
                return
            finally:
                if task_id in _tasks:
                    _tasks[task_id]['done'] = True

            q.put({'type': 'done_all', 'service_id': service_id,
                   'ok': all(r['ok'] for r in results), 'results': results})

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

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
