#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres --set=app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE quiz_platform LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE DATABASE quiz_platform OWNER quiz_platform;
SQL
