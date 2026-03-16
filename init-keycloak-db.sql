-- Create a separate database for Keycloak on the same PostgreSQL instance.
-- docker-entrypoint-initdb.d scripts run only on first container init.
CREATE DATABASE keycloak;
