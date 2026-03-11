from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from crypto import EncryptedString

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Many-to-many: Server <-> Environment
# ---------------------------------------------------------------------------
server_environments = db.Table(
    'server_environments',
    db.Column('server_id', db.Integer, db.ForeignKey('servers.id', ondelete='CASCADE'), primary_key=True),
    db.Column('env_id',    db.Integer, db.ForeignKey('environments.id', ondelete='CASCADE'), primary_key=True),
)


class Environment(db.Model):
    __tablename__ = 'environments'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(64), nullable=False, unique=True)
    description = db.Column(db.String(256))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    servers = db.relationship(
        'Server', secondary=server_environments,
        back_populates='environments',
    )

    def __repr__(self):
        return f'<Environment {self.name}>'


class Credential(db.Model):
    """
    Справочник учётных записей для WinRM.
    Один набор кредов можно привязать к нескольким серверам.
    """
    __tablename__ = 'credentials'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(128), nullable=False, unique=True)
    username    = db.Column(db.String(256), nullable=False)
    # Stored encrypted via Fernet (see crypto.py). 512 chars fits the token overhead.
    password    = db.Column(EncryptedString(512), nullable=False)
    description = db.Column(db.String(256))
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    servers = db.relationship('Server', back_populates='credential')

    def __repr__(self):
        return f'<Credential {self.name} ({self.username})>'


class Server(db.Model):
    __tablename__ = 'servers'

    id            = db.Column(db.Integer, primary_key=True)
    hostname      = db.Column(db.String(256), nullable=False, unique=True)
    port          = db.Column(db.Integer, default=5985)
    use_ssl       = db.Column(db.Boolean, default=False)
    credential_id = db.Column(db.Integer, db.ForeignKey('credentials.id'), nullable=False)
    description   = db.Column(db.String(256))
    is_available  = db.Column(db.Boolean, default=None)
    last_checked  = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    credential = db.relationship('Credential', back_populates='servers')
    environments = db.relationship(
        'Environment', secondary=server_environments,
        back_populates='servers',
    )
    instances = db.relationship('ServiceInstance', back_populates='server', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Server {self.hostname}>'


class Service(db.Model):
    """Global service catalog — not tied to any environment."""
    __tablename__ = 'services'

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(128), nullable=False, unique=True)
    display_name = db.Column(db.String(256))
    description  = db.Column(db.String(512))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    instances = db.relationship('ServiceInstance', back_populates='service', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Service {self.name}>'


class ServiceInstance(db.Model):
    """A Windows service running on a specific server."""
    __tablename__ = 'service_instances'

    id                = db.Column(db.Integer, primary_key=True)
    server_id         = db.Column(db.Integer, db.ForeignKey('servers.id'), nullable=False)
    service_id        = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    win_service_name  = db.Column(db.String(256), nullable=False)
    exe_path          = db.Column(db.String(512))
    config_dir        = db.Column(db.String(512))
    status            = db.Column(db.String(32), default='unknown')
    last_status_check = db.Column(db.DateTime)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    server  = db.relationship('Server', back_populates='instances')
    service = db.relationship('Service', back_populates='instances')
    configs = db.relationship('InstanceConfig', back_populates='instance', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('server_id', 'win_service_name', name='uq_server_win_service'),
    )

    def __repr__(self):
        return f'<ServiceInstance {self.win_service_name}@{self.server.hostname}>'


class InstanceConfig(db.Model):
    """A single configuration file belonging to a ServiceInstance."""
    __tablename__ = 'instance_configs'

    id          = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.Integer, db.ForeignKey('service_instances.id'), nullable=False)
    filename    = db.Column(db.String(256), nullable=False)
    filepath    = db.Column(db.String(512), nullable=False)
    content     = db.Column(db.Text)
    encoding    = db.Column(db.String(32), default='utf-8')
    fetched_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    instance = db.relationship('ServiceInstance', back_populates='configs')

    def __repr__(self):
        return f'<InstanceConfig {self.filename} instance={self.instance_id}>'


class ConfigSnapshot(db.Model):
    """
    Снэпшот конфигурационных файлов экземпляра сервиса,
    снятый перед операцией управления (start/stop/restart).
    """
    __tablename__ = 'config_snapshots'

    id           = db.Column(db.Integer, primary_key=True)
    instance_id  = db.Column(db.Integer, db.ForeignKey('service_instances.id'), nullable=False)
    trigger      = db.Column(db.String(32))   # start / stop / restart
    configs_json = db.Column(db.Text)         # JSON: [{filename, filepath, content}]
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    instance = db.relationship('ServiceInstance',
                               backref=db.backref('snapshots', cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<ConfigSnapshot instance={self.instance_id} trigger={self.trigger}>'


class AuditLog(db.Model):
    """
    Журнал действий пользователя.
    Пишется при любом изменении объектов через UI.
    """
    __tablename__ = 'audit_log'

    # Типы действий
    ACTION_CREATE          = 'create'
    ACTION_UPDATE          = 'update'
    ACTION_DELETE          = 'delete'
    ACTION_TEST_CONN       = 'test_connection'
    ACTION_REFRESH_STATUS  = 'refresh_status'
    ACTION_REFRESH_CONFIGS = 'refresh_configs'
    ACTION_CONTROL         = 'control'
    ACTION_SNAPSHOT        = 'snapshot'

    # Типы сущностей
    ENTITY_ENVIRONMENT = 'environment'
    ENTITY_CREDENTIAL  = 'credential'
    ENTITY_SERVER      = 'server'
    ENTITY_SERVICE     = 'service'
    ENTITY_INSTANCE    = 'instance'
    ENTITY_CONFIG      = 'config'
    ENTITY_SNAPSHOT    = 'snapshot'

    # Результат
    RESULT_OK      = 'ok'
    RESULT_WARNING = 'warning'
    RESULT_ERROR   = 'error'

    id          = db.Column(db.Integer, primary_key=True)
    action      = db.Column(db.String(32), nullable=False)
    entity_type = db.Column(db.String(32), nullable=False)
    entity_id   = db.Column(db.Integer)
    entity_name = db.Column(db.String(256))
    details     = db.Column(db.Text)          # произвольный текст / diff
    result      = db.Column(db.String(16), default=RESULT_OK)
    ip_address  = db.Column(db.String(45))    # IPv4/IPv6
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<AuditLog {self.action} {self.entity_type}#{self.entity_id}>'
