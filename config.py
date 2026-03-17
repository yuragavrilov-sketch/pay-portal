"""Centralized application configuration."""
import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'postgresql://postgres:postgres@localhost:5432/svcmgr',
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # PostgreSQL schema (empty string = use default 'public')
    DB_SCHEMA = os.environ.get('DB_SCHEMA', 'svcmgr')

    # Set search_path at the PostgreSQL protocol level so every connection
    # (including pool checkouts) has the correct schema from the start.
    SQLALCHEMY_ENGINE_OPTIONS = {}
    if DB_SCHEMA:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {
                'options': f'-c search_path={DB_SCHEMA},public'
            }
        }

    # Keycloak
    KEYCLOAK_URL = os.environ.get('KEYCLOAK_URL', 'http://localhost:8080')
    KEYCLOAK_REALM = os.environ.get('KEYCLOAK_REALM', 'svcmgr')
    KEYCLOAK_CLIENT_ID = os.environ.get('KEYCLOAK_CLIENT_ID', 'svcmgr-app')
    KEYCLOAK_CLIENT_SECRET = os.environ.get('KEYCLOAK_CLIENT_SECRET', '')
