#!/bin/sh
# =====================================================================
# Read-only role "grafana"
#   Grafana passes SQL to the database unchecked. With the owner role every
#   Grafana user could change or delete measurements, so Grafana reads
#   through a role of its own that may only SELECT.
#
#   Like everything in initdb/ this runs ONLY on an empty volume. On an
#   existing database, once by hand:
#     docker compose exec timescaledb sh /docker-entrypoint-initdb.d/90_grafana_role.sh
# =====================================================================
set -e

psql -v ON_ERROR_STOP=1 -v pw="${GRAFANA_DB_PASSWORD:?GRAFANA_DB_PASSWORD is not set}" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE grafana LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE :"DBNAME" TO grafana;
GRANT USAGE ON SCHEMA public TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana;
-- Also for tables, views and aggregates created later; otherwise a series
-- silently goes missing from a dashboard ("permission denied").
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana;
SQL
