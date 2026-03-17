"""Service Management Portal — Flask application (React frontend)."""
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, send_from_directory
from sqlalchemy import text
from models import db
from logger import setup_logging

log = logging.getLogger(__name__)


# =========================================================================
# Versioned migrations — each runs exactly once, tracked in _schema_migrations
# =========================================================================
MIGRATIONS = [
    (1, "add source tracking to instance_configs", """
        ALTER TABLE instance_configs
          ADD COLUMN IF NOT EXISTS source_version_id INTEGER
          REFERENCES service_config_versions(id) ON DELETE SET NULL;
        ALTER TABLE instance_configs
          ADD COLUMN IF NOT EXISTS is_overridden BOOLEAN NOT NULL DEFAULT FALSE;
    """),
    (2, "add env_id to service_configs", """
        ALTER TABLE service_configs
          ADD COLUMN IF NOT EXISTS env_id INTEGER
          REFERENCES environments(id) ON DELETE SET NULL;
    """),
    (3, "add username to audit_log", """
        ALTER TABLE audit_log
          ADD COLUMN IF NOT EXISTS username VARCHAR(128);
    """),
    (4, "replace unique constraint with partial indexes on service_configs", """
        ALTER TABLE service_configs
          DROP CONSTRAINT IF EXISTS uq_service_config_filename;
        ALTER TABLE service_configs
          DROP CONSTRAINT IF EXISTS uq_service_config_filename_env;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_svc_cfg_filename_global
          ON service_configs(service_id, filename) WHERE env_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_svc_cfg_filename_env
          ON service_configs(service_id, filename, env_id) WHERE env_id IS NOT NULL;
    """),
]


def _ensure_schema(app):
    """Create the application PostgreSQL schema if it doesn't exist."""
    schema = app.config.get('DB_SCHEMA', '')
    if not schema:
        return

    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema):
        raise ValueError(f"Invalid DB_SCHEMA value: {schema!r}")

    # search_path is already set via SQLALCHEMY_ENGINE_OPTIONS connect_args
    with db.engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
        log.info("Ensured database schema: %s", schema)


def _run_migrations():
    """Apply pending versioned migrations."""
    with db.engine.connect() as conn:
        # Ensure tracking table exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                version  INTEGER PRIMARY KEY,
                name     VARCHAR(256) NOT NULL,
                applied_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()

        # Get current version
        current = conn.execute(text(
            "SELECT COALESCE(MAX(version), 0) FROM _schema_migrations"
        )).scalar() or 0

        applied = 0
        for version, name, sql in MIGRATIONS:
            if version <= current:
                continue
            log.info("Applying migration %d: %s", version, name)
            conn.execute(text(sql))
            conn.execute(text(
                "INSERT INTO _schema_migrations (version, name) VALUES (:v, :n)"
            ), {"v": version, "n": name})
            conn.commit()
            applied += 1
            log.info("Migration %d applied", version)

        if applied:
            log.info("Applied %d migration(s), current version: %d", applied, MIGRATIONS[-1][0])
        else:
            log.info("Database is up to date (version %d)", current)


def create_app():
    react_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'react')

    app = Flask(__name__, static_folder=None)
    app.config.from_object('config.Config')

    db.init_app(app)
    setup_logging(app)

    with app.app_context():
        _ensure_schema(app)
        db.create_all()
        _run_migrations()

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
        if path and os.path.isfile(os.path.join(react_dir, path)):
            return send_from_directory(react_dir, path)
        index_path = os.path.join(react_dir, 'index.html')
        if os.path.isfile(index_path):
            return send_from_directory(react_dir, 'index.html')
        return ('<h3>React app not built yet</h3>'
                '<p>Run <code>cd frontend && npm install && npm run build</code></p>'), 200

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('FLASK_PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
