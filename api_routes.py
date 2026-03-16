"""REST API Blueprint — JSON endpoints for the React frontend."""
import json
import logging
import queue
import threading
import uuid

log = logging.getLogger(__name__)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from flask import Blueprint, Response, current_app, g, jsonify, request, session, stream_with_context
from models import (
    db, AuditLog, ConfigSnapshot, Environment, Credential, Server, Service,
    ServiceInstance, InstanceConfig, ServiceConfig, ServiceConfigVersion,
    _next_version,
)
from auth import _extract_token, _verify_and_set_user
import winrm_utils

# In-memory task registry shared across routes
_tasks: dict = {}

api = Blueprint('api', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# Blueprint-level auth guard — protects ALL /api/* routes
# ---------------------------------------------------------------------------
@api.before_request
def _require_auth():
    token = _extract_token()
    if not token:
        return jsonify({"error": "Authorization required"}), 401
    err = _verify_and_set_user(token)
    if err:
        return err


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_ip():
    return (
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        or request.remote_addr or 'unknown'
    )


def _current_username():
    """Extract username from Flask g (request context) if available."""
    user = getattr(g, 'user', None)
    return user.get('username', '') if user else ''


def _audit(action, entity_type, entity_id=None, entity_name='',
           details='', result=AuditLog.RESULT_OK,
           username=None, ip_address=None):
    try:
        if username is None:
            username = _current_username()
        if ip_address is None:
            try:
                ip_address = _client_ip()
            except RuntimeError:
                ip_address = ''
        with db.session.begin_nested():
            entry = AuditLog(
                action=action, entity_type=entity_type,
                entity_id=entity_id, entity_name=entity_name,
                details=details, result=result,
                ip_address=ip_address,
                username=username,
            )
            db.session.add(entry)
        db.session.commit()
    except Exception:
        log.exception("Audit log write failed: %s %s #%s", action, entity_type, entity_id)
        db.session.rollback()


# ===== ENV CONTEXT =========================================================

@api.route('/env/current')
def env_current():
    envs = Environment.query.order_by(Environment.name).all()
    cur_id = session.get('current_env_id')
    cur = db.session.get(Environment, cur_id) if cur_id else None
    return jsonify({
        'current_env': {'id': cur.id, 'name': cur.name} if cur else None,
        'environments': [{'id': e.id, 'name': e.name} for e in envs],
    })


@api.route('/env/select/<int:env_id>', methods=['POST'])
def env_select(env_id):
    Environment.query.get_or_404(env_id)
    session['current_env_id'] = env_id
    return jsonify({'ok': True})


@api.route('/env/clear', methods=['POST'])
def env_clear():
    session.pop('current_env_id', None)
    return jsonify({'ok': True})


# ===== DASHBOARD ===========================================================

@api.route('/dashboard')
def dashboard():
    cur_id = session.get('current_env_id')
    if cur_id:
        server_count = (Server.query.join(Server.environments)
                        .filter(Environment.id == cur_id).count())
        instance_count = (ServiceInstance.query.join(Server)
                          .join(Server.environments)
                          .filter(Environment.id == cur_id).count())
    else:
        server_count = Server.query.count()
        instance_count = ServiceInstance.query.count()
    recent = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
    return jsonify({
        'env_count': Environment.query.count(),
        'server_count': server_count,
        'instance_count': instance_count,
        'service_count': Service.query.count(),
        'cred_count': Credential.query.count(),
        'recent_audit': [{
            'id': r.id, 'action': r.action, 'entity_type': r.entity_type,
            'entity_name': r.entity_name or '', 'result': r.result,
            'username': r.username or '',
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M') if r.created_at else '',
        } for r in recent],
    })


# ===== ENVIRONMENTS ========================================================

@api.route('/environments')
def env_list():
    envs = Environment.query.order_by(Environment.name).all()
    return jsonify({'environments': [{
        'id': e.id, 'name': e.name, 'description': e.description or '',
        'server_count': len(e.servers),
        'created_at': e.created_at.strftime('%d.%m.%Y %H:%M') if e.created_at else '',
    } for e in envs]})


@api.route('/environments/<int:eid>')
def env_get(eid):
    e = Environment.query.get_or_404(eid)
    return jsonify({'id': e.id, 'name': e.name, 'description': e.description or ''})


@api.route('/environments', methods=['POST'])
def env_create():
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Название обязательно'}), 400
    if Environment.query.filter_by(name=name).first():
        return jsonify({'error': f'Окружение "{name}" уже существует'}), 400
    env = Environment(name=name, description=d.get('description', '').strip())
    db.session.add(env)
    db.session.commit()
    _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_ENVIRONMENT, env.id, env.name)
    return jsonify({'id': env.id}), 201


@api.route('/environments/<int:eid>', methods=['PUT'])
def env_update(eid):
    env = Environment.query.get_or_404(eid)
    d = request.get_json(force=True)
    old_name = env.name
    env.name = d.get('name', env.name).strip()
    env.description = d.get('description', env.description or '').strip()
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_ENVIRONMENT, env.id, env.name,
           details=f'name: {old_name!r} -> {env.name!r}' if old_name != env.name else '')
    return jsonify({'ok': True})


@api.route('/environments/<int:eid>', methods=['DELETE'])
def env_delete(eid):
    env = Environment.query.get_or_404(eid)
    name = env.name
    if session.get('current_env_id') == env.id:
        session.pop('current_env_id', None)
    db.session.delete(env)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_ENVIRONMENT, eid, name)
    return jsonify({'ok': True})


# ===== CREDENTIALS =========================================================

@api.route('/credentials')
def cred_list():
    creds = Credential.query.order_by(Credential.name).all()
    return jsonify({'credentials': [{
        'id': c.id, 'name': c.name, 'username': c.username,
        'description': c.description or '',
        'server_count': len(c.servers),
        'updated_at': c.updated_at.strftime('%d.%m.%Y %H:%M') if c.updated_at else '',
    } for c in creds]})


@api.route('/credentials/<int:cid>')
def cred_get(cid):
    c = Credential.query.get_or_404(cid)
    return jsonify({
        'id': c.id, 'name': c.name, 'username': c.username,
        'description': c.description or '',
    })


@api.route('/credentials', methods=['POST'])
def cred_create():
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Название обязательно'}), 400
    if Credential.query.filter_by(name=name).first():
        return jsonify({'error': f'Учётная запись "{name}" уже существует'}), 400
    cred = Credential(
        name=name, username=d.get('username', '').strip(),
        password=d.get('password', ''),
        description=d.get('description', '').strip(),
    )
    db.session.add(cred)
    db.session.commit()
    _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_CREDENTIAL, cred.id, cred.name)
    return jsonify({'id': cred.id}), 201


@api.route('/credentials/<int:cid>', methods=['PUT'])
def cred_update(cid):
    cred = Credential.query.get_or_404(cid)
    d = request.get_json(force=True)
    cred.name = d.get('name', cred.name).strip()
    cred.username = d.get('username', cred.username).strip()
    if d.get('password'):
        cred.password = d['password']
    cred.description = d.get('description', cred.description or '').strip()
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CREDENTIAL, cred.id, cred.name)
    return jsonify({'ok': True})


@api.route('/credentials/<int:cid>', methods=['DELETE'])
def cred_delete(cid):
    cred = Credential.query.get_or_404(cid)
    if cred.servers:
        return jsonify({'error': f'Используется {len(cred.servers)} сервером(ами)'}), 400
    name = cred.name
    db.session.delete(cred)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_CREDENTIAL, cid, name)
    return jsonify({'ok': True})


# ===== SERVERS ==============================================================

@api.route('/servers')
def server_list():
    cur_id = session.get('current_env_id')
    q = Server.query
    if cur_id:
        q = q.join(Server.environments).filter(Environment.id == cur_id)
    servers = q.order_by(Server.hostname).all()
    return jsonify({'servers': [{
        'id': s.id, 'hostname': s.hostname, 'port': s.port, 'use_ssl': s.use_ssl,
        'credential_id': s.credential_id,
        'credential_name': s.credential.name if s.credential else '',
        'description': s.description or '',
        'is_available': s.is_available,
        'last_checked': s.last_checked.strftime('%d.%m.%Y %H:%M') if s.last_checked else '',
        'instance_count': len(s.instances),
        'environments': [{'id': e.id, 'name': e.name} for e in s.environments],
    } for s in servers]})


@api.route('/servers/<int:sid>')
def server_get(sid):
    s = Server.query.get_or_404(sid)
    return jsonify({
        'id': s.id, 'hostname': s.hostname, 'port': s.port, 'use_ssl': s.use_ssl,
        'credential_id': s.credential_id, 'description': s.description or '',
        'env_ids': [e.id for e in s.environments],
    })


@api.route('/servers', methods=['POST'])
def server_create():

    d = request.get_json(force=True)
    env_ids = d.get('env_ids', [])
    server = Server(
        hostname=d.get('hostname', '').strip(),
        port=int(d.get('port', 5985)),
        use_ssl=bool(d.get('use_ssl')),
        credential_id=int(d.get('credential_id')),
        description=d.get('description', '').strip(),
    )
    server.environments = Environment.query.filter(Environment.id.in_(env_ids)).all()
    db.session.add(server)
    db.session.flush()
    ok, msg = winrm_utils.test_connection(server)
    server.is_available = ok
    server.last_checked = datetime.utcnow()
    db.session.commit()
    _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_SERVER, server.id, server.hostname,
           details=f'winrm={"ok" if ok else "fail: " + msg}',
           result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_WARNING)
    return jsonify({'id': server.id, 'ok': ok, 'message': msg}), 201


@api.route('/servers/<int:sid>', methods=['PUT'])
def server_update(sid):
    server = Server.query.get_or_404(sid)
    d = request.get_json(force=True)
    server.hostname = d.get('hostname', server.hostname).strip()
    server.port = int(d.get('port', server.port))
    server.use_ssl = bool(d.get('use_ssl', server.use_ssl))
    server.credential_id = int(d.get('credential_id', server.credential_id))
    server.description = d.get('description', server.description or '').strip()
    env_ids = d.get('env_ids', [])
    server.environments = Environment.query.filter(Environment.id.in_(env_ids)).all()
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_SERVER, server.id, server.hostname)
    return jsonify({'ok': True})


@api.route('/servers/<int:sid>', methods=['DELETE'])
def server_delete(sid):
    server = Server.query.get_or_404(sid)
    name = server.hostname
    db.session.delete(server)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_SERVER, sid, name)
    return jsonify({'ok': True})


@api.route('/servers/<int:sid>/test', methods=['POST'])
def server_test(sid):

    server = Server.query.get_or_404(sid)
    ok, msg = winrm_utils.test_connection(server)
    server.is_available = ok
    server.last_checked = datetime.utcnow()
    db.session.commit()
    _audit(AuditLog.ACTION_TEST_CONN, AuditLog.ENTITY_SERVER, server.id, server.hostname,
           details=msg, result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR)
    return jsonify({'ok': ok, 'message': msg})


@api.route('/servers/<int:sid>/services')
def server_services(sid):

    server = Server.query.get_or_404(sid)
    services, error = winrm_utils.list_services(server)
    if error:
        return jsonify({'ok': False, 'error': error, 'services': []})
    return jsonify({'ok': True, 'error': None, 'services': services})


@api.route('/servers/discover', methods=['POST'])
def servers_discover():
    """Discover Windows services on multiple servers in parallel.

    Body: { "server_ids": [1, 2, 3] }
    Uses SSE task stream for real-time results.
    """
    data = request.get_json(silent=True) or {}
    server_ids = data.get('server_ids', [])
    if not server_ids:
        return jsonify({'ok': False, 'error': 'server_ids обязателен'}), 400

    # Collect already-registered instance names to mark duplicates
    existing = set()
    for row in db.session.query(ServiceInstance.server_id, ServiceInstance.win_service_name).all():
        existing.add((row.server_id, row.win_service_name))

    task_id = str(uuid.uuid4())
    tq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': tq, 'done': False}
    app = current_app._get_current_object()

    def discover_one(sid):
        with app.app_context():
            server = db.session.get(Server, int(sid))
            if not server:
                tq.put({'type': 'server_done', 'server_id': sid,
                        'ok': False, 'hostname': str(sid),
                        'error': 'Сервер не найден', 'services': []})
                return
            try:
                services, error = winrm_utils.list_services(server)
                if error:
                    tq.put({'type': 'server_done', 'server_id': server.id,
                            'ok': False, 'hostname': server.hostname,
                            'error': error, 'services': []})
                    return
                svc_list = []
                for s in (services or []):
                    svc_list.append({
                        'name': s.get('name', ''),
                        'display_name': s.get('display_name', ''),
                        'status': s.get('status', ''),
                        'already_registered': (server.id, s.get('name', '')) in existing,
                    })
                tq.put({'type': 'server_done', 'server_id': server.id,
                        'ok': True, 'hostname': server.hostname,
                        'error': None, 'services': svc_list})
            except Exception as exc:
                tq.put({'type': 'server_done', 'server_id': server.id,
                        'ok': False, 'hostname': server.hostname,
                        'error': str(exc), 'services': []})

    def worker():
        try:
            max_w = max(1, min(len(server_ids), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                list(pool.map(discover_one, server_ids))
            tq.put({'type': 'done_all', 'ok': True})
        except Exception as exc:
            tq.put({'type': 'done_all', 'ok': False, 'error': str(exc)})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


# ===== SERVICES =============================================================

@api.route('/services')
def svc_list():
    services = Service.query.order_by(Service.name).all()
    return jsonify({'services': [{
        'id': s.id, 'name': s.name, 'display_name': s.display_name or '',
        'description': s.description or '',
        'instance_count': len(s.instances),
        'config_count': len(s.virtual_configs),
    } for s in services]})


@api.route('/services/<int:sid>')
def svc_get(sid):
    s = Service.query.get_or_404(sid)
    return jsonify({
        'id': s.id, 'name': s.name, 'display_name': s.display_name or '',
        'description': s.description or '',
    })


@api.route('/services', methods=['POST'])
def svc_create():
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Имя обязательно'}), 400
    if Service.query.filter_by(name=name).first():
        return jsonify({'error': f'Сервис "{name}" уже существует'}), 400
    svc = Service(name=name, display_name=d.get('display_name', '').strip(),
                  description=d.get('description', '').strip())
    db.session.add(svc)
    db.session.commit()
    _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_SERVICE, svc.id, svc.name)
    return jsonify({'id': svc.id}), 201


@api.route('/services/<int:sid>', methods=['PUT'])
def svc_update(sid):
    svc = Service.query.get_or_404(sid)
    d = request.get_json(force=True)
    svc.name = d.get('name', svc.name).strip()
    svc.display_name = d.get('display_name', svc.display_name or '').strip()
    svc.description = d.get('description', svc.description or '').strip()
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_SERVICE, svc.id, svc.name)
    return jsonify({'ok': True})


@api.route('/services/<int:sid>', methods=['DELETE'])
def svc_delete(sid):
    svc = Service.query.get_or_404(sid)
    name = svc.name
    db.session.delete(svc)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_SERVICE, sid, name)
    return jsonify({'ok': True})


# ===== SERVICE CONFIGS ======================================================

@api.route('/services/<int:sid>/configs')
def cfg_list(sid):
    svc = Service.query.get_or_404(sid)
    env_filter = request.args.get('env_id', type=int)
    environments = Environment.query.order_by(Environment.name).all()
    used_env_ids = list({cfg.env_id for cfg in svc.virtual_configs})

    if env_filter == 0:
        display = [c for c in svc.virtual_configs if c.env_id is None]
    elif env_filter:
        display = [c for c in svc.virtual_configs if c.env_id == env_filter]
    else:
        display = list(svc.virtual_configs)

    sync_summaries = {}
    for cfg in svc.virtual_configs:
        cur = cfg.current_version
        if cfg.env_id:
            relevant = [i for i in svc.instances
                        if any(e.id == cfg.env_id for e in i.server.environments)]
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

    return jsonify({
        'service_id': sid, 'service_name': svc.display_name or svc.name,
        'environments': [{'id': e.id, 'name': e.name} for e in environments],
        'used_env_ids': used_env_ids,
        'sync_summaries': sync_summaries,
        'configs': [{
            'id': c.id, 'filename': c.filename, 'description': c.description or '',
            'env_id': c.env_id,
            'env_label': c.environment.name if c.environment else '',
            'current_version': c.current_version_number,
        } for c in display],
    })


@api.route('/services/<int:sid>/configs/<int:cid>')
def cfg_get(sid, cid):
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    return jsonify({
        'id': cfg.id, 'filename': cfg.filename, 'content': cfg.content or '',
        'description': cfg.description or '', 'env_id': cfg.env_id,
        'env_label': cfg.environment.name if cfg.environment else '',
    })


@api.route('/services/<int:sid>/configs', methods=['POST'])
def cfg_create(sid):
    svc = Service.query.get_or_404(sid)
    d = request.get_json(force=True)
    filename = d.get('filename', '').strip()
    env_id = d.get('env_id')
    if not filename:
        return jsonify({'error': 'Имя файла обязательно'}), 400
    dup = ServiceConfig.query.filter_by(service_id=sid, filename=filename, env_id=env_id).first()
    if dup:
        return jsonify({'error': f'Файл "{filename}" уже существует'}), 400
    content = d.get('content', '')
    cfg = ServiceConfig(service_id=sid, env_id=env_id, filename=filename,
                        description=d.get('description', '').strip(), content=content)
    db.session.add(cfg)
    db.session.flush()
    ver = ServiceConfigVersion(
        service_config_id=cfg.id, version=1, content=content,
        comment=d.get('comment', '').strip() or 'Первая версия',
        is_current=True, created_by=_client_ip(),
    )
    db.session.add(ver)
    db.session.commit()
    _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_CONFIG, cfg.id, filename,
           details=f'service={svc.name} v1')
    return jsonify({'id': cfg.id}), 201


@api.route('/services/<int:sid>/configs/<int:cid>', methods=['PUT'])
def cfg_update(sid, cid):
    svc = Service.query.get_or_404(sid)
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    d = request.get_json(force=True)
    new_filename = d.get('filename', cfg.filename).strip()
    new_env_id = d.get('env_id', cfg.env_id)
    if new_filename != cfg.filename or new_env_id != cfg.env_id:
        dup = ServiceConfig.query.filter(
            ServiceConfig.service_id == sid, ServiceConfig.filename == new_filename,
            ServiceConfig.env_id == new_env_id, ServiceConfig.id != cfg.id,
        ).first()
        if dup:
            return jsonify({'error': f'Файл "{new_filename}" уже существует'}), 400
    cfg.filename = new_filename
    cfg.env_id = new_env_id
    cfg.description = d.get('description', cfg.description or '').strip()
    new_content = d.get('content', cfg.content or '')
    cfg.content = new_content
    cfg.updated_at = datetime.utcnow()
    ServiceConfigVersion.query.filter_by(service_config_id=cfg.id).update({'is_current': False})
    next_v = _next_version(cfg.id)
    ver = ServiceConfigVersion(
        service_config_id=cfg.id, version=next_v, content=new_content,
        comment=d.get('comment', '').strip() or f'Версия {next_v}',
        is_current=True, created_by=_client_ip(),
    )
    db.session.add(ver)
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CONFIG, cfg.id, cfg.filename,
           details=f'service={svc.name} v{next_v}')
    return jsonify({'ok': True, 'version': next_v})


@api.route('/services/<int:sid>/configs/<int:cid>', methods=['DELETE'])
def cfg_delete(sid, cid):
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    filename = cfg.filename
    db.session.delete(cfg)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_CONFIG, cid, filename)
    return jsonify({'ok': True})


@api.route('/services/<int:sid>/configs/<int:cid>/versions')
def cfg_versions(sid, cid):
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    versions = (ServiceConfigVersion.query.filter_by(service_config_id=cid)
                .order_by(ServiceConfigVersion.version.desc()).all())
    return jsonify({
        'filename': cfg.filename,
        'versions': [{
            'id': v.id, 'version': v.version, 'comment': v.comment or '',
            'is_current': v.is_current,
            'created_at': v.created_at.strftime('%d.%m.%Y %H:%M') if v.created_at else '',
            'created_by': v.created_by or '',
        } for v in versions],
    })


@api.route('/services/<int:sid>/configs/<int:cid>/versions/<int:vid>/activate', methods=['POST'])
def cfg_version_activate(sid, cid, vid):
    svc = Service.query.get_or_404(sid)
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    ver = ServiceConfigVersion.query.filter_by(id=vid, service_config_id=cid).first_or_404()
    ServiceConfigVersion.query.filter_by(service_config_id=cid).update({'is_current': False})
    ver.is_current = True
    cfg.content = ver.content
    cfg.updated_at = datetime.utcnow()
    db.session.commit()
    _audit(AuditLog.ACTION_ROLLBACK_CONFIG, AuditLog.ENTITY_CONFIG, cfg.id, cfg.filename,
           details=f'service={svc.name} rollback to v{ver.version}')
    return jsonify({'ok': True})


@api.route('/services/<int:sid>/configs/<int:cid>/push')
def cfg_push_data(sid, cid):
    svc = Service.query.get_or_404(sid)
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    cur = cfg.current_version
    if cfg.env_id:
        relevant = [i for i in svc.instances
                    if any(e.id == cfg.env_id for e in i.server.environments)]
    else:
        relevant = svc.instances
    instances = []
    for inst in relevant:
        icfg = next((c for c in inst.configs if c.filename == cfg.filename), None)
        if icfg is None or icfg.source_version_id is None:
            st = 'untracked'
        elif icfg.is_overridden:
            st = 'overridden'
        elif cur and icfg.source_version_id == cur.id:
            st = 'synced'
        else:
            st = 'outdated'
        instances.append({
            'id': inst.id, 'win_name': inst.win_service_name,
            'hostname': inst.server.hostname, 'status': st,
        })
    return jsonify({
        'filename': cfg.filename,
        'env_label': cfg.environment.name if cfg.environment else '',
        'current_version': cur.version if cur else None,
        'instances': instances,
    })


# ===== INSTANCES ============================================================

@api.route('/instances')
def inst_list():
    cur_id = session.get('current_env_id')
    q = ServiceInstance.query.join(Server)
    if cur_id:
        q = q.join(Server.environments).filter(Environment.id == cur_id)
    instances = q.order_by(Server.hostname, ServiceInstance.win_service_name).all()
    return jsonify({'instances': [{
        'id': i.id, 'win_service_name': i.win_service_name,
        'service_name': i.service.display_name or i.service.name,
        'service_id': i.service_id,
        'hostname': i.server.hostname,
        'environments': [e.name for e in i.server.environments],
        'status': i.status,
        'config_count': len(i.configs),
    } for i in instances]})


@api.route('/instances/<int:iid>')
def inst_get(iid):
    inst = ServiceInstance.query.get_or_404(iid)
    icfg_map = {c.filename: c for c in inst.configs}
    virtual_configs = []
    for vcfg in inst.service.virtual_configs:
        cur = vcfg.current_version
        icfg = icfg_map.get(vcfg.filename)
        if icfg is None or icfg.source_version_id is None:
            sync_st = 'untracked'
        elif icfg.is_overridden:
            sync_st = 'overridden'
        elif cur and icfg.source_version_id == cur.id:
            sync_st = 'synced'
        else:
            sync_st = 'outdated'
        virtual_configs.append({
            'id': vcfg.id, 'filename': vcfg.filename,
            'env_label': vcfg.environment.name if vcfg.environment else '',
            'sync_status': sync_st,
            'version': cur.version if cur else None,
        })
    return jsonify({
        'id': inst.id, 'win_service_name': inst.win_service_name,
        'service_name': inst.service.display_name or inst.service.name,
        'hostname': inst.server.hostname,
        'exe_path': inst.exe_path or '',
        'config_dir': inst.config_dir or '',
        'status': inst.status,
        'last_status_check': inst.last_status_check.strftime('%d.%m.%Y %H:%M') if inst.last_status_check else '',
        'configs': [{
            'id': c.id, 'filename': c.filename, 'filepath': c.filepath,
            'sync_status': c.sync_status,
        } for c in inst.configs],
        'virtual_configs': virtual_configs,
    })


@api.route('/instances/<int:iid>', methods=['DELETE'])
def inst_delete(iid):
    inst = ServiceInstance.query.get_or_404(iid)
    name = inst.win_service_name
    hostname = inst.server.hostname
    db.session.delete(inst)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_INSTANCE, iid, name,
           details=f'server={hostname}')
    return jsonify({'ok': True})


@api.route('/instances/<int:iid>/refresh-status', methods=['POST'])
def inst_refresh_status(iid):

    inst = ServiceInstance.query.get_or_404(iid)
    status = winrm_utils.get_service_status(inst.server, inst.win_service_name)
    inst.status = status
    inst.last_status_check = datetime.utcnow()
    db.session.commit()
    return jsonify({'status': status})


@api.route('/instances/<int:iid>/refresh-configs', methods=['POST'])
def inst_refresh_configs(iid):

    inst = ServiceInstance.query.get_or_404(iid)
    if not inst.config_dir:
        return jsonify({'ok': False, 'message': 'config_dir не задан'})
    InstanceConfig.query.filter_by(instance_id=inst.id).delete()
    cfg_files = winrm_utils.fetch_all_configs(inst.server, inst.config_dir)
    for cf in cfg_files:
        db.session.add(InstanceConfig(
            instance_id=inst.id, filename=cf['filename'], filepath=cf['filepath'],
            content=cf['content'], encoding=cf['encoding'], fetched_at=cf['fetched_at'],
        ))
    db.session.commit()
    return jsonify({'ok': True, 'count': len(cfg_files)})


@api.route('/instances/<int:iid>/configs/<int:cid>')
def inst_cfg_get(iid, cid):
    cfg = InstanceConfig.query.filter_by(id=cid, instance_id=iid).first_or_404()
    return jsonify({
        'id': cfg.id, 'filename': cfg.filename, 'filepath': cfg.filepath,
        'content': cfg.content or '', 'encoding': cfg.encoding or 'utf-8',
        'sync_status': cfg.sync_status,
    })


@api.route('/instances/<int:iid>/configs/<int:cid>', methods=['PUT'])
def inst_cfg_update(iid, cid):
    inst = ServiceInstance.query.get_or_404(iid)
    cfg = InstanceConfig.query.filter_by(id=cid, instance_id=iid).first_or_404()
    d = request.get_json(force=True)
    cfg.content = d.get('content', cfg.content)
    cfg.updated_at = datetime.utcnow()
    cfg.is_overridden = True
    db.session.commit()
    _audit(AuditLog.ACTION_UPDATE, AuditLog.ENTITY_CONFIG, cfg.id, cfg.filename,
           details=f'instance={inst.win_service_name}')
    return jsonify({'ok': True})


@api.route('/instances/<int:iid>/configs/<int:cid>', methods=['DELETE'])
def inst_cfg_delete(iid, cid):
    cfg = InstanceConfig.query.filter_by(id=cid, instance_id=iid).first_or_404()
    filename = cfg.filename
    db.session.delete(cfg)
    db.session.commit()
    _audit(AuditLog.ACTION_DELETE, AuditLog.ENTITY_CONFIG, cid, filename)
    return jsonify({'ok': True})


# ===== MANAGE ===============================================================

@api.route('/manage')
def manage_data():
    cur_id = session.get('current_env_id')
    q = ServiceInstance.query.join(Server)
    if cur_id:
        q = q.join(Server.environments).filter(Environment.id == cur_id)
    instances = q.order_by(ServiceInstance.service_id, Server.hostname,
                           ServiceInstance.win_service_name).all()
    services_map = {}
    for inst in instances:
        sid = inst.service_id
        if sid not in services_map:
            svc = inst.service
            services_map[sid] = {
                'service': {
                    'id': svc.id, 'name': svc.name,
                    'display_name': svc.display_name or '',
                    'config_count': len(svc.virtual_configs),
                },
                'instances': [],
            }
        services_map[sid]['instances'].append({
            'id': inst.id, 'win_service_name': inst.win_service_name,
            'hostname': inst.server.hostname,
            'status': inst.status,
            'environments': [e.name for e in inst.server.environments],
        })
    return jsonify({'service_groups': list(services_map.values())})


# ===== AUDIT ================================================================

@api.route('/audit')
def audit_list():
    page = request.args.get('page', 1, type=int)
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
    username_filter = request.args.get('username', '').strip()
    if username_filter:
        query = query.filter(AuditLog.username == username_filter)
    if search:
        like = f'%{search}%'
        query = query.filter(db.or_(
            AuditLog.entity_name.ilike(like),
            AuditLog.details.ilike(like),
            AuditLog.ip_address.ilike(like),
            AuditLog.username.ilike(like),
        ))
    pagination = query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=50, error_out=False)
    return jsonify({
        'items': [{
            'id': r.id, 'action': r.action, 'entity_type': r.entity_type,
            'entity_id': r.entity_id, 'entity_name': r.entity_name or '',
            'details': r.details or '', 'result': r.result,
            'ip_address': r.ip_address or '',
            'username': r.username or '',
            'created_at': r.created_at.strftime('%d.%m.%Y %H:%M:%S') if r.created_at else '',
        } for r in pagination.items],
        'page': pagination.page, 'pages': pagination.pages, 'total': pagination.total,
        'usernames': sorted(
            u for (u,) in db.session.query(db.distinct(AuditLog.username))
            .filter(AuditLog.username.isnot(None), AuditLog.username != '').all()
        ),
    })


# ===== CONFIG SUMMARY ======================================================

@api.route('/services/<int:sid>/config-summary')
def cfg_summary(sid):
    svc = Service.query.get_or_404(sid)
    result = []
    for cfg in svc.virtual_configs:
        cur = cfg.current_version
        versions = [
            {'id': v.id, 'version': v.version, 'comment': v.comment or '',
             'created_at': v.created_at.strftime('%d.%m.%Y %H:%M'),
             'is_current': v.is_current}
            for v in cfg.versions
        ]
        if cfg.env_id:
            relevant = [i for i in svc.instances
                        if any(e.id == cfg.env_id for e in i.server.environments)]
        else:
            relevant = svc.instances
        instances_status = []
        for inst in relevant:
            icfg = next((c for c in inst.configs if c.filename == cfg.filename), None)
            if icfg is None or icfg.source_version_id is None:
                st, inst_ver = 'untracked', None
            elif icfg.is_overridden:
                sv = db.session.get(ServiceConfigVersion, icfg.source_version_id)
                st, inst_ver = 'overridden', (sv.version if sv else None)
            elif cur and icfg.source_version_id == cur.id:
                st, inst_ver = 'synced', cur.version
            else:
                sv = db.session.get(ServiceConfigVersion, icfg.source_version_id)
                st, inst_ver = 'outdated', (sv.version if sv else None)
            instances_status.append({
                'instance_id': inst.id, 'win_name': inst.win_service_name,
                'hostname': inst.server.hostname, 'status': st, 'version': inst_ver,
            })
        result.append({
            'id': cfg.id, 'filename': cfg.filename, 'description': cfg.description or '',
            'env_id': cfg.env_id,
            'env_label': cfg.environment.name if cfg.environment else '',
            'current_version': cur.version if cur else None,
            'current_version_id': cur.id if cur else None,
            'versions': versions, 'instances': instances_status,
        })
    return jsonify({'service_id': sid, 'service_name': svc.name,
                    'display_name': svc.display_name or svc.name, 'configs': result})


# ===== SSE TASK STREAM =====================================================

@api.route('/manage/tasks/<task_id>/stream')
def task_stream(task_id):
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


# ===== CONTROL INSTANCE ====================================================

_ACTION_META = {
    'start':   (AuditLog.ACTION_START,   'Запуск'),
    'stop':    (AuditLog.ACTION_STOP,    'Остановка'),
    'restart': (AuditLog.ACTION_RESTART, 'Перезапуск'),
}


def _take_snapshot(inst, trigger, _ip='system', _username=''):
    configs_data = [
        {'filename': c.filename, 'filepath': c.filepath, 'content': c.content}
        for c in inst.configs
    ]
    snap = ConfigSnapshot(
        instance_id=inst.id, trigger=trigger,
        configs_json=json.dumps(configs_data, ensure_ascii=False),
    )
    db.session.add(snap)
    _audit(AuditLog.ACTION_SNAPSHOT, AuditLog.ENTITY_SNAPSHOT,
           inst.id, inst.win_service_name,
           details=f'перед операцией: {_ACTION_META.get(trigger, (None, trigger))[1]}'
                   f' | server={inst.server.hostname} | файлов: {len(configs_data)}',
           username=_username, ip_address=_ip)
    return snap


@api.route('/manage/instances/<int:iid>/control', methods=['POST'])
def manage_control(iid):

    inst = ServiceInstance.query.get_or_404(iid)
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    if action not in ('start', 'stop', 'restart'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400

    task_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': q, 'done': False}
    client_ip = _client_ip()
    req_username = _current_username()
    inst_id = inst.id
    action_const, action_label = _ACTION_META[action]
    app = current_app._get_current_object()

    def worker():
        try:
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, inst_id)
                if not inst_w:
                    q.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                           'message': 'Не найден', 'status': 'unknown'})
                    return
                q.put({'type': 'progress', 'instance_id': inst_id,
                       'message': 'Снимаю снэпшот конфигурации...'})
                snap = _take_snapshot(inst_w, action, _ip=client_ip, _username=req_username)
                q.put({'type': 'progress', 'instance_id': inst_id,
                       'message': f'{action_label}...', 'snap_id': snap.id})
                ok, msg = winrm_utils.control_service(inst_w.server, inst_w.win_service_name, action)
                q.put({'type': 'progress', 'instance_id': inst_id, 'message': 'Проверяю статус...'})
                new_status = winrm_utils.get_service_status(inst_w.server, inst_w.win_service_name)
                inst_w.status = new_status
                inst_w.last_status_check = datetime.utcnow()
                db.session.commit()
                _audit(action_const, AuditLog.ENTITY_INSTANCE, inst_w.id, inst_w.win_service_name,
                       details=f'server={inst_w.server.hostname} | статус: {new_status}',
                       result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                       username=req_username, ip_address=client_ip)
                q.put({'type': 'done', 'instance_id': inst_id, 'ok': ok,
                       'message': msg, 'status': new_status, 'snap_id': snap.id})
        except Exception as exc:
            q.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                   'message': str(exc), 'status': 'unknown'})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


@api.route('/manage/services/<int:sid>/control', methods=['POST'])
def manage_svc_control(sid):

    svc = Service.query.get_or_404(sid)
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    if action not in ('start', 'stop', 'restart'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400

    cur_id = session.get('current_env_id')
    q_inst = ServiceInstance.query.filter_by(service_id=sid).join(Server)
    if cur_id:
        q_inst = q_inst.join(Server.environments).filter(Environment.id == cur_id)
    inst_ids = [i.id for i in q_inst.all()]

    task_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': q, 'done': False}
    client_ip = _client_ip()
    req_username = _current_username()
    action_const, action_label = _ACTION_META[action]
    app = current_app._get_current_object()

    def process_one(iid):
        with app.app_context():
            inst_w = db.session.get(ServiceInstance, iid)
            if not inst_w:
                r = {'instance_id': iid, 'ok': False, 'message': 'Не найден',
                     'status': 'unknown', 'snap_id': None}
                q.put({'type': 'instance_done', **r})
                return r
            q.put({'type': 'progress', 'instance_id': iid,
                   'message': f'[{inst_w.win_service_name}] Снэпшот...'})
            snap = _take_snapshot(inst_w, action, _ip=client_ip, _username=req_username)
            q.put({'type': 'progress', 'instance_id': iid,
                   'message': f'[{inst_w.win_service_name}] {action_label}...', 'snap_id': snap.id})
            ok, msg = winrm_utils.control_service(inst_w.server, inst_w.win_service_name, action)
            new_status = winrm_utils.get_service_status(inst_w.server, inst_w.win_service_name)
            inst_w.status = new_status
            inst_w.last_status_check = datetime.utcnow()
            db.session.commit()
            _audit(action_const, AuditLog.ENTITY_INSTANCE, inst_w.id, inst_w.win_service_name,
                   details=f'server={inst_w.server.hostname} | статус: {new_status}',
                   result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                   username=req_username, ip_address=client_ip)
            r = {'instance_id': iid, 'ok': ok, 'message': msg,
                 'status': new_status, 'snap_id': snap.id}
            q.put({'type': 'instance_done', **r})
            return r

    def worker():
        results = []
        try:
            max_w = max(1, min(len(inst_ids), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futures = {pool.submit(process_one, iid): iid for iid in inst_ids}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        iid = futures[f]
                        err = {'instance_id': iid, 'ok': False, 'message': str(exc),
                               'status': 'unknown', 'snap_id': None}
                        results.append(err)
                        q.put({'type': 'instance_done', **err})
            q.put({'type': 'done_all', 'service_id': sid,
                   'ok': all(r['ok'] for r in results), 'results': results})
        except Exception as exc:
            q.put({'type': 'done_all', 'service_id': sid, 'ok': False,
                   'results': results, 'error': str(exc)})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


# ===== CONFIG PUSH (task-based) =============================================

@api.route('/services/<int:sid>/configs/<int:cid>/push', methods=['POST'])
def cfg_push(sid, cid):

    svc = Service.query.get_or_404(sid)
    cfg = ServiceConfig.query.filter_by(id=cid, service_id=sid).first_or_404()
    cur_ver = cfg.current_version
    if not cur_ver:
        return jsonify({'ok': False, 'error': 'Нет активной версии'}), 400

    data = request.get_json(silent=True) or {}
    force = bool(data.get('force', False))
    effective_env_id = data.get('env_id') or cfg.env_id
    q_inst = ServiceInstance.query.filter_by(service_id=sid).join(Server)
    if effective_env_id:
        q_inst = q_inst.join(Server.environments).filter(Environment.id == int(effective_env_id))
    instances = q_inst.all()
    if not instances:
        return jsonify({'ok': False, 'error': 'Нет экземпляров'}), 400

    task_id = str(uuid.uuid4())
    tq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': tq, 'done': False}
    client_ip = _client_ip()
    push_username = _current_username()
    inst_ids = [i.id for i in instances]
    ver_id = cur_ver.id
    ver_num = cur_ver.version
    cfg_fname = cfg.filename
    svc_name = svc.name
    app = current_app._get_current_object()

    def process_one(iid):
        with app.app_context():
            inst_w = db.session.get(ServiceInstance, iid)
            if not inst_w:
                r = {'instance_id': iid, 'ok': False, 'message': 'Не найден',
                     'hostname': '?', 'win_name': '?'}
                tq.put({'type': 'instance_done', **r})
                return r
            hostname = inst_w.server.hostname
            win_name = inst_w.win_service_name
            existing = InstanceConfig.query.filter_by(instance_id=iid, filename=cfg_fname).first()
            if existing and existing.is_overridden and not force:
                r = {'instance_id': iid, 'ok': False, 'skipped': True,
                     'message': 'Пропущен (изменён вручную)', 'hostname': hostname, 'win_name': win_name}
                tq.put({'type': 'instance_done', **r})
                return r
            ver = db.session.get(ServiceConfigVersion, ver_id)
            content = ver.content or ''
            if existing:
                filepath = existing.filepath
                existing.content = content
                existing.source_version_id = ver_id
                existing.is_overridden = False
                existing.updated_at = datetime.utcnow()
            else:
                filepath = (inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname) if inst_w.config_dir else ''
                db.session.add(InstanceConfig(
                    instance_id=iid, filename=cfg_fname, filepath=filepath,
                    content=content, source_version_id=ver_id,
                    is_overridden=False, encoding='utf-8', fetched_at=datetime.utcnow()))
            db.session.flush()
            write_ok, write_msg = False, 'filepath не задан'
            if filepath:
                enc = (existing.encoding if existing else None) or 'utf-8'
                write_ok, write_msg = winrm_utils.write_file_content(inst_w.server, filepath, content, enc)
            db.session.commit()
            msg = f'v{ver_num} применена'
            if filepath:
                msg += f' | файл: {"записан" if write_ok else "ОШИБКА: " + write_msg}'
            r = {'instance_id': iid, 'ok': True, 'message': msg,
                 'hostname': hostname, 'win_name': win_name}
            tq.put({'type': 'instance_done', **r})
            return r

    def worker():
        results = []
        try:
            max_w = max(1, min(len(inst_ids), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futures = {pool.submit(process_one, iid): iid for iid in inst_ids}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        iid = futures[f]
                        err = {'instance_id': iid, 'ok': False, 'message': str(exc),
                               'hostname': '?', 'win_name': '?'}
                        results.append(err)
                        tq.put({'type': 'instance_done', **err})
            all_ok = all(r['ok'] for r in results)
            tq.put({'type': 'done_all', 'ok': all_ok,
                    'results': results, 'cfg_filename': cfg_fname, 'version': ver_num})
            # Audit log for config push
            with app.app_context():
                ok_count = sum(1 for r in results if r.get('ok'))
                entry = AuditLog(
                    action=AuditLog.ACTION_PUSH_CONFIG,
                    entity_type=AuditLog.ENTITY_CONFIG,
                    entity_id=cid,
                    entity_name=cfg_fname,
                    details=f'service={svc_name} v{ver_num} -> {ok_count}/{len(results)} instances',
                    result=AuditLog.RESULT_OK if all_ok else AuditLog.RESULT_WARNING,
                    ip_address=client_ip,
                    username=push_username,
                )
                db.session.add(entry)
                db.session.commit()
        except Exception as exc:
            tq.put({'type': 'done_all', 'ok': False, 'results': results, 'error': str(exc)})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


# ===== INSTANCE CREATE (task-based) =========================================

@api.route('/instances', methods=['POST'])
def inst_create():

    data = request.get_json(silent=True) or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'ok': False, 'error': 'Список пуст'}), 400

    task_id = str(uuid.uuid4())
    tq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': tq, 'done': False}
    client_ip = _client_ip()
    req_username = _current_username()
    app = current_app._get_current_object()

    def process_one(idx_item):
        idx, item = idx_item
        with app.app_context():
            srv_id = item.get('server_id')
            win_name = str(item.get('win_service_name', '')).strip()
            svc_id = item.get('service_id')
            if not win_name or not srv_id:
                r = {'index': idx, 'ok': False, 'win_name': win_name,
                     'hostname': '', 'message': 'Неверные параметры'}
                tq.put({'type': 'item_done', **r})
                return r
            server = db.session.get(Server, int(srv_id))
            if not server:
                r = {'index': idx, 'ok': False, 'win_name': win_name,
                     'hostname': str(srv_id), 'message': 'Сервер не найден'}
                tq.put({'type': 'item_done', **r})
                return r
            tq.put({'type': 'item_progress', 'index': idx, 'win_name': win_name,
                    'hostname': server.hostname, 'message': 'Получаю информацию...'})
            try:
                info = winrm_utils.get_service_info(server, win_name)
                exe_path = info.get('exe_path') or ''
                config_dir = winrm_utils.infer_config_dir(exe_path) if exe_path else ''
                inst = ServiceInstance(
                    server_id=server.id, service_id=int(svc_id),
                    win_service_name=win_name, exe_path=exe_path,
                    config_dir=config_dir, status=info.get('status', 'unknown'),
                    last_status_check=datetime.utcnow(),
                )
                db.session.add(inst)
                db.session.flush()
                cfg_count = 0
                if config_dir:
                    for cf in winrm_utils.fetch_all_configs(server, config_dir):
                        db.session.add(InstanceConfig(
                            instance_id=inst.id, filename=cf['filename'],
                            filepath=cf['filepath'], content=cf['content'],
                            encoding=cf['encoding'], fetched_at=cf['fetched_at'],
                        ))
                        cfg_count += 1
                db.session.commit()
                _audit(AuditLog.ACTION_CREATE, AuditLog.ENTITY_INSTANCE,
                       inst.id, win_name,
                       details=f'server={server.hostname} configs={cfg_count}',
                       username=req_username, ip_address=client_ip)
                r = {'index': idx, 'ok': True, 'win_name': win_name,
                     'hostname': server.hostname, 'message': f'Конфигов: {cfg_count}'}
                tq.put({'type': 'item_done', **r})
                return r
            except Exception as exc:
                db.session.rollback()
                r = {'index': idx, 'ok': False, 'win_name': win_name,
                     'hostname': server.hostname, 'message': str(exc)}
                tq.put({'type': 'item_done', **r})
                return r

    def worker():
        results = []
        try:
            max_w = max(1, min(len(items), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futures = {pool.submit(process_one, (idx, item)): idx
                           for idx, item in enumerate(items)}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        idx = futures[f]
                        r = {'index': idx, 'ok': False, 'win_name': '', 'hostname': '',
                             'message': str(exc)}
                        results.append(r)
                        tq.put({'type': 'item_done', **r})
            created = sum(1 for r in results if r.get('ok'))
            tq.put({'type': 'done_all', 'ok': True, 'created': created, 'total': len(items)})
        except Exception as exc:
            tq.put({'type': 'done_all', 'ok': False, 'error': str(exc), 'total': len(items)})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


# ===== CONFIG DEPLOY (service & instance) ===================================

@api.route('/manage/services/<int:sid>/config-deploy', methods=['POST'])
def manage_svc_deploy(sid):

    svc = Service.query.get_or_404(sid)
    data = request.get_json(silent=True) or {}
    cfg_id = data.get('cfg_id')
    ver_id = data.get('ver_id')
    do_restart = bool(data.get('restart', True))
    env_id = data.get('env_id')
    force = bool(data.get('force', True))

    cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=sid).first_or_404()
    ver = ServiceConfigVersion.query.filter_by(id=ver_id, service_config_id=cfg_id).first_or_404()

    q_inst = ServiceInstance.query.filter_by(service_id=sid).join(Server)
    if env_id:
        q_inst = q_inst.join(Server.environments).filter(Environment.id == int(env_id))
    instances = q_inst.all()
    if not instances:
        return jsonify({'ok': False, 'error': 'Нет экземпляров'}), 400

    task_id = str(uuid.uuid4())
    tq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': tq, 'done': False}
    client_ip = _client_ip()
    req_username = _current_username()
    inst_ids = [i.id for i in instances]
    _ver_id = ver.id
    ver_num = ver.version
    cfg_fname = cfg.filename
    svc_name = svc.name
    app = current_app._get_current_object()

    def _deploy_one(iid):
        with app.app_context():
            inst_w = db.session.get(ServiceInstance, iid)
            if not inst_w:
                r = {'instance_id': iid, 'ok': False, 'message': 'Не найден',
                     'hostname': '?', 'win_name': '?', 'status': 'unknown'}
                tq.put({'type': 'instance_done', **r})
                return r
            hostname = inst_w.server.hostname
            win_name = inst_w.win_service_name
            existing = InstanceConfig.query.filter_by(instance_id=iid, filename=cfg_fname).first()
            if existing and existing.is_overridden and not force:
                r = {'instance_id': iid, 'ok': False, 'skipped': True,
                     'message': 'Пропущен (изменён вручную)',
                     'hostname': hostname, 'win_name': win_name, 'status': inst_w.status}
                tq.put({'type': 'instance_done', **r})
                return r
            tq.put({'type': 'progress', 'instance_id': iid,
                    'message': f'[{hostname}] Записываю {cfg_fname}...'})
            ver_obj = db.session.get(ServiceConfigVersion, _ver_id)
            content = ver_obj.content or ''
            filepath = (existing.filepath if existing else '') or ''
            if not filepath and inst_w.config_dir:
                filepath = inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname
            write_ok, write_msg = False, 'filepath не задан'
            if filepath:
                enc = (existing.encoding if existing else None) or 'utf-8'
                write_ok, write_msg = winrm_utils.write_file_content(inst_w.server, filepath, content, enc)
            if existing:
                existing.content = content
                existing.source_version_id = _ver_id
                existing.is_overridden = False
                existing.updated_at = datetime.utcnow()
            else:
                db.session.add(InstanceConfig(
                    instance_id=iid, filename=cfg_fname, filepath=filepath,
                    content=content, source_version_id=_ver_id,
                    is_overridden=False, encoding='utf-8', fetched_at=datetime.utcnow()))
            db.session.flush()
            restart_ok, restart_msg, new_status = True, '', inst_w.status
            if do_restart:
                tq.put({'type': 'progress', 'instance_id': iid,
                        'message': f'[{hostname}] Перезапуск {win_name}...'})
                restart_ok, restart_msg = winrm_utils.control_service(inst_w.server, win_name, 'restart')
                new_status = winrm_utils.get_service_status(inst_w.server, win_name)
                inst_w.status = new_status
                inst_w.last_status_check = datetime.utcnow()
            db.session.commit()
            ok = (write_ok or not filepath) and restart_ok
            parts = []
            if filepath:
                parts.append(f'файл: {"ok" if write_ok else "ERR: " + write_msg}')
            if do_restart:
                parts.append(f'restart: {"ok" if restart_ok else "ERR: " + restart_msg}')
            r = {'instance_id': iid, 'ok': ok, 'message': '; '.join(parts) or 'ok',
                 'hostname': hostname, 'win_name': win_name,
                 'status': new_status, 'version': ver_num}
            tq.put({'type': 'instance_done', **r})
            return r

    def worker():
        results = []
        try:
            max_w = max(1, min(len(inst_ids), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futures = {pool.submit(_deploy_one, iid): iid for iid in inst_ids}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        iid = futures[f]
                        err = {'instance_id': iid, 'ok': False, 'message': str(exc),
                               'hostname': '?', 'win_name': '?', 'status': 'unknown'}
                        results.append(err)
                        tq.put({'type': 'instance_done', **err})
            all_ok = all(r['ok'] for r in results)
            tq.put({'type': 'done_all', 'ok': all_ok,
                    'results': results, 'cfg_filename': cfg_fname, 'version': ver_num})
            with app.app_context():
                ok_count = sum(1 for r in results if r.get('ok'))
                _audit(AuditLog.ACTION_PUSH_CONFIG, AuditLog.ENTITY_CONFIG,
                       cfg.id, cfg_fname,
                       details=f'service={svc_name} v{ver_num} -> {ok_count}/{len(results)} instances',
                       result=AuditLog.RESULT_OK if all_ok else AuditLog.RESULT_WARNING,
                       username=req_username, ip_address=client_ip)
        except Exception as exc:
            tq.put({'type': 'done_all', 'ok': False, 'results': results, 'error': str(exc)})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


@api.route('/manage/instances/<int:iid>/config-deploy', methods=['POST'])
def manage_inst_deploy(iid):

    inst = ServiceInstance.query.get_or_404(iid)
    data = request.get_json(silent=True) or {}
    cfg_id = data.get('cfg_id')
    ver_id = data.get('ver_id')
    do_restart = bool(data.get('restart', True))

    cfg = ServiceConfig.query.filter_by(id=cfg_id, service_id=inst.service_id).first_or_404()
    ver = ServiceConfigVersion.query.filter_by(id=ver_id, service_config_id=cfg_id).first_or_404()

    task_id = str(uuid.uuid4())
    tq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': tq, 'done': False}
    client_ip = _client_ip()
    req_username = _current_username()
    inst_id = inst.id
    _ver_id = ver.id
    ver_num = ver.version
    cfg_fname = cfg.filename
    app = current_app._get_current_object()

    def worker():
        try:
            with app.app_context():
                inst_w = db.session.get(ServiceInstance, inst_id)
                hostname = inst_w.server.hostname
                win_name = inst_w.win_service_name
                ver_obj = db.session.get(ServiceConfigVersion, _ver_id)
                content = ver_obj.content or ''
                existing = InstanceConfig.query.filter_by(instance_id=inst_id, filename=cfg_fname).first()
                filepath = (existing.filepath if existing else '') or ''
                if not filepath and inst_w.config_dir:
                    filepath = inst_w.config_dir.rstrip('\\') + '\\' + cfg_fname
                write_ok, write_msg = False, 'filepath не задан'
                if filepath:
                    enc = (existing.encoding if existing else None) or 'utf-8'
                    write_ok, write_msg = winrm_utils.write_file_content(inst_w.server, filepath, content, enc)
                if existing:
                    existing.content = content
                    existing.source_version_id = _ver_id
                    existing.is_overridden = False
                    existing.updated_at = datetime.utcnow()
                else:
                    db.session.add(InstanceConfig(
                        instance_id=inst_id, filename=cfg_fname, filepath=filepath,
                        content=content, source_version_id=_ver_id,
                        is_overridden=False, encoding='utf-8', fetched_at=datetime.utcnow()))
                db.session.flush()
                restart_ok, restart_msg, new_status = True, '', inst_w.status
                if do_restart:
                    restart_ok, restart_msg = winrm_utils.control_service(inst_w.server, win_name, 'restart')
                    new_status = winrm_utils.get_service_status(inst_w.server, win_name)
                    inst_w.status = new_status
                    inst_w.last_status_check = datetime.utcnow()
                db.session.commit()
                ok = (write_ok or not filepath) and restart_ok
                parts = []
                if filepath:
                    parts.append(f'файл: {"ok" if write_ok else "ERR: " + write_msg}')
                if do_restart:
                    parts.append(f'restart: {"ok" if restart_ok else "ERR: " + restart_msg}')
                _audit(AuditLog.ACTION_PUSH_CONFIG, AuditLog.ENTITY_CONFIG,
                       cfg_id, cfg_fname,
                       details=f'instance={win_name}@{hostname} v{ver_num}' +
                               (' | ' + '; '.join(parts) if parts else ''),
                       result=AuditLog.RESULT_OK if ok else AuditLog.RESULT_ERROR,
                       username=req_username, ip_address=client_ip)
                tq.put({'type': 'done', 'instance_id': inst_id, 'ok': ok,
                        'message': '; '.join(parts) or 'ok',
                        'status': new_status, 'version': ver_num,
                        'hostname': hostname, 'win_name': win_name})
        except Exception as exc:
            tq.put({'type': 'done', 'instance_id': inst_id, 'ok': False,
                    'message': str(exc), 'status': 'unknown'})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id})


# ===== SNAPSHOTS ============================================================

@api.route('/manage/instances/<int:iid>/snapshots')
def manage_snapshots(iid):
    ServiceInstance.query.get_or_404(iid)
    snaps = (ConfigSnapshot.query.filter_by(instance_id=iid)
             .order_by(ConfigSnapshot.created_at.desc()).limit(20).all())
    return jsonify({'snapshots': [
        {'id': s.id, 'trigger': s.trigger,
         'created_at': s.created_at.strftime('%d.%m.%Y %H:%M:%S'),
         'files': len(json.loads(s.configs_json or '[]'))}
        for s in snaps
    ]})


@api.route('/manage/snapshots/<int:snap_id>')
def manage_snap_detail(snap_id):
    snap = ConfigSnapshot.query.get_or_404(snap_id)
    return jsonify({
        'id': snap.id, 'instance_id': snap.instance_id, 'trigger': snap.trigger,
        'created_at': snap.created_at.strftime('%d.%m.%Y %H:%M:%S'),
        'configs': json.loads(snap.configs_json or '[]'),
    })


@api.route('/manage/snapshots/<int:snap_id>/restore', methods=['POST'])
def manage_snap_restore(snap_id):
    snap = ConfigSnapshot.query.get_or_404(snap_id)
    inst = db.session.get(ServiceInstance, snap.instance_id)
    if not inst:
        return jsonify({'ok': False, 'error': 'Экземпляр не найден'}), 404
    configs_data = json.loads(snap.configs_json or '[]')
    InstanceConfig.query.filter_by(instance_id=inst.id).delete()
    for item in configs_data:
        db.session.add(InstanceConfig(
            instance_id=inst.id, filename=item['filename'],
            filepath=item.get('filepath', ''), content=item.get('content', ''),
            encoding='utf-8', fetched_at=datetime.utcnow(),
        ))
    db.session.commit()
    return jsonify({'ok': True, 'restored': len(configs_data)})


# ===== CONFIG DIFF ==========================================================

@api.route('/manage/instances/<int:iid>/config-diff')
def manage_cfg_diff(iid):

    inst = ServiceInstance.query.get_or_404(iid)
    filename = request.args.get('filename', '').strip()
    if not filename:
        return jsonify({'ok': False, 'error': 'filename required'}), 400
    icfg = next((c for c in inst.configs if c.filename == filename), None)
    stored = icfg.content if icfg else None
    filepath = icfg.filepath if icfg else None
    if not filepath and inst.config_dir:
        filepath = inst.config_dir.rstrip('\\') + '\\' + filename
    if not filepath:
        return jsonify({'ok': False, 'error': 'filepath не задан'})
    live_content, encoding = winrm_utils.fetch_file_content(inst.server, filepath)
    if live_content is None:
        return jsonify({'ok': False, 'error': f'Не удалось прочитать файл ({filepath})'})
    identical = (stored or '').strip() == live_content.strip()
    return jsonify({
        'ok': True, 'filename': filename, 'filepath': filepath,
        'stored': stored, 'live': live_content, 'encoding': encoding,
        'identical': identical, 'win_name': inst.win_service_name,
        'hostname': inst.server.hostname,
    })


# ===== CONFIG SCAN (task-based) =============================================

@api.route('/instances/scan-configs', methods=['POST'])
def inst_scan_configs():

    data = request.get_json(silent=True) or {}
    env_id = data.get('env_id')
    q_inst = ServiceInstance.query.join(Server)
    if env_id:
        q_inst = q_inst.join(Server.environments).filter(Environment.id == int(env_id))
    instances = q_inst.all()
    if not instances:
        return jsonify({'ok': False, 'error': 'Нет экземпляров'}), 400

    task_id = str(uuid.uuid4())
    sq: queue.Queue = queue.Queue()
    _tasks[task_id] = {'q': sq, 'done': False}
    inst_ids = [i.id for i in instances]
    app = current_app._get_current_object()

    def scan_one(iid):
        with app.app_context():
            inst_w = db.session.get(ServiceInstance, iid)
            if not inst_w:
                r = {'instance_id': iid, 'ok': False, 'hostname': '?',
                     'win_name': '?', 'message': 'Не найден', 'diffs': []}
                sq.put({'type': 'scan_done', **r})
                return r
            hostname = inst_w.server.hostname
            win_name = inst_w.win_service_name
            if not inst_w.config_dir:
                r = {'instance_id': iid, 'ok': True, 'hostname': hostname,
                     'win_name': win_name, 'message': 'config_dir не задан', 'diffs': []}
                sq.put({'type': 'scan_done', **r})
                return r
            try:
                live_files = winrm_utils.fetch_all_configs(inst_w.server, inst_w.config_dir)
            except Exception as exc:
                r = {'instance_id': iid, 'ok': False, 'hostname': hostname,
                     'win_name': win_name, 'message': f'Ошибка WinRM: {exc}', 'diffs': []}
                sq.put({'type': 'scan_done', **r})
                return r
            stored = {c.filename: c.content or '' for c in inst_w.configs}
            live = {f['filename']: f['content'] or '' for f in live_files}
            diffs = []
            for fname in sorted(set(stored) | set(live)):
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
            r = {'instance_id': iid, 'ok': True, 'hostname': hostname,
                 'win_name': win_name, 'diffs': diffs, 'changed': changed,
                 'message': f'{len(diffs)} файл(ов), изменено: {changed}'}
            sq.put({'type': 'scan_done', **r})
            return r

    def worker():
        results = []
        try:
            max_w = max(1, min(len(inst_ids), 8))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futures = {pool.submit(scan_one, iid): iid for iid in inst_ids}
                for f in as_completed(futures):
                    try:
                        results.append(f.result())
                    except Exception as exc:
                        iid = futures[f]
                        r = {'instance_id': iid, 'ok': False, 'hostname': '?',
                             'win_name': '?', 'message': str(exc), 'diffs': []}
                        results.append(r)
                        sq.put({'type': 'scan_done', **r})
            total_changed = sum(r.get('changed', 0) for r in results)
            sq.put({'type': 'done_all', 'ok': True, 'total': len(results),
                    'total_changed': total_changed})
        except Exception as exc:
            sq.put({'type': 'done_all', 'ok': False, 'error': str(exc),
                    'total': len(results), 'total_changed': 0})
        finally:
            _tasks.get(task_id, {})['done'] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'task_id': task_id, 'total': len(inst_ids)})
