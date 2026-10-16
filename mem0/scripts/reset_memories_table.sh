#!/bin/bash
# The pgvector collection table lives in the POSTGRES_DB database (default
# "postgres"), not in APP_DB_NAME. Drop it so mem0 recreates it at the width
# configured by EMBEDDING_DIMS.
set -u
DB="${1:-postgres}"

echo "12345" | sudo -S docker compose -f /usr/local/soft/mem/docker-compose.yml exec -T postgres \
  psql -U postgres -d "$DB" -c "DROP TABLE IF EXISTS memories;" 2>&1 | grep -v 'password for'

echo "--- after drop ---"
echo "12345" | sudo -S docker compose -f /usr/local/soft/mem/docker-compose.yml exec -T postgres \
  psql -U postgres -d "$DB" -t -A -c "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>&1 | grep -v 'password for'
