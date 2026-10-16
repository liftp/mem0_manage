#!/bin/bash
# Runs once when the postgres data volume is first initialized.
# Creates the mem0_app database used by the server for users/api-keys/settings.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE ${APP_DB_NAME:-mem0_app}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${APP_DB_NAME:-mem0_app}')\gexec
EOSQL
