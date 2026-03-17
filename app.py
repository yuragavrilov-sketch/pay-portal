"""Service Management Portal — Flask application (React frontend)."""
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, send_from_directory
from sqlalchemy import event, text
from models import db
from logger import setup_logging

log = logging.getLogger(__name__)


def _ensure_schema(app):
    """Create the application PostgreSQL schema if it doesn't exist and set search_path."""
    schema = app.config.get('DB_SCHEMA', '')
    if not schema:
        return

    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema):
        raise ValueError(f"Invalid DB_SCHEMA value: {schema!r}")

    # Set search_path for every new connection via engine event
    @event.listens_for(db.engine, "connect")
    def set_search_path(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {schema}, public")
        cursor.close()

    # Create the schema if it doesn't exist
    with db.engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"
        ), {"s": schema}).fetchone()
        if not exists:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
            conn.commit()
            log.info("Created database schema: %s", schema)
        else:
            log.info("Database schema already exists: %s", schema)


def _migrate_db(app):
    """Idempotent: добавляет новые колонки в существующие таблицы (PostgreSQL)."""
    schema = app.config.get('DB_SCHEMA', '') or 'public'

    with app.app_context():
        with db.engine.connect() as conn:
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
                (
                    "audit_log", "username",
                    "ALTER TABLE audit_log "
                    "ADD COLUMN username VARCHAR(128)",
                ),
            ]
            for table, column, ddl in col_checks:
                row = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name=:t AND column_name=:c"
                ), {"s": schema, "t": table, "c": column}).fetchone()
                if not row:
                    conn.execute(text(ddl))

            old_con = conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema=:s AND table_name='service_configs' "
                "AND constraint_name='uq_service_config_filename'"
            ), {"s": schema}).fetchone()
            if old_con:
                conn.execute(text(
                    "ALTER TABLE service_configs DROP CONSTRAINT uq_service_config_filename"
                ))

            new_con = conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema=:s AND table_name='service_configs' "
                "AND constraint_name='uq_service_config_filename_env'"
            ), {"s": schema}).fetchone()
            if new_con:
                conn.execute(text(
                    "ALTER TABLE service_configs DROP CONSTRAINT uq_service_config_filename_env"
                ))

            idx_null = conn.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname=:s AND tablename='service_configs' "
                "AND indexname='uq_svc_cfg_filename_global'"
            ), {"s": schema}).fetchone()
            if not idx_null:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_svc_cfg_filename_global "
                    "ON service_configs(service_id, filename) WHERE env_id IS NULL"
                ))

            idx_env = conn.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname=:s AND tablename='service_configs' "
                "AND indexname='uq_svc_cfg_filename_env'"
            ), {"s": schema}).fetchone()
            if not idx_env:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_svc_cfg_filename_env "
                    "ON service_configs(service_id, filename, env_id) WHERE env_id IS NOT NULL"
                ))

            conn.commit()


def create_app():
    react_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'react')

    app = Flask(__name__, static_folder=None)
    app.config.from_object('config.Config')

    db.init_app(app)
    setup_logging(app)

    with app.app_context():
        _ensure_schema(app)
        db.create_all()
        _migrate_db(app)

    # ------------------------------------------------------------------
    # Register Auth blueprint (login / logout / refresh / me)
    # ------------------------------------------------------------------
    from auth import auth as auth_bp
    app.register_blueprint(auth_bp)

    # ------------------------------------------------------------------
    # Register API blueprint (all JSON endpoints)
    # ------------------------------------------------------------------
    from api_routes import api as api_bp
    app.register_blueprint(api_bp)

    # ------------------------------------------------------------------
    # Serve React SPA
    # ------------------------------------------------------------------
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_react(path):
        # Serve static assets from React build
        if path and os.path.isfile(os.path.join(react_dir, path)):
            return send_from_directory(react_dir, path)
        # For all other routes, serve React index.html (SPA routing)
        index_path = os.path.join(react_dir, 'index.html')
        if os.path.isfile(index_path):
            return send_from_directory(react_dir, 'index.html')
        # Fallback: if React not built yet
        return ('<h3>React app not built yet</h3>'
                '<p>Run <code>cd frontend && npm install && npm run build</code></p>'), 200

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
