#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run this script as root" >&2
  exit 2
fi

umask 077
env_file=/srv/nihongo-quiz/shared/env/production.env
django_secret=$(openssl rand -base64 48 | tr -d '\n')
db_password=$(openssl rand -hex 32)
identity_key=$(openssl rand -base64 32 | tr -d '\n')
hmac_key=$(openssl rand -base64 32 | tr -d '\n')
pg_port=$(pg_lsclusters --no-header | awk '$1 == "18" && $2 == "main" { print $3; exit }')
if [[ -z "$pg_port" ]]; then
  echo "PostgreSQL 18/main cluster is not available" >&2
  exit 1
fi

printf '%s\n' \
  'DJANGO_DEBUG=0' \
  "DJANGO_SECRET_KEY=$django_secret" \
  'DJANGO_ALLOWED_HOSTS=quiz.localhost,localhost,127.0.0.1' \
  'DJANGO_CSRF_TRUSTED_ORIGINS=https://quiz.localhost:18443' \
  'DJANGO_USE_SQLITE=0' \
  'DJANGO_SECURE_SSL_REDIRECT=1' \
  'DJANGO_BEHIND_PROXY=1' \
  'QUIZ_TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128' \
  'DJANGO_TRUSTED_PROXY_COUNT=0' \
  'DJANGO_SECURE_HSTS_SECONDS=0' \
  'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=0' \
  'DJANGO_SECURE_HSTS_PRELOAD=0' \
  'POSTGRES_DB=nihongo_quiz' \
  'POSTGRES_USER=nihongo_quiz' \
  "POSTGRES_PASSWORD=$db_password" \
  'POSTGRES_HOST=127.0.0.1' \
  "POSTGRES_PORT=$pg_port" \
  "QUIZ_IDENTITY_KEYS='{\"v1\":\"$identity_key\"}'" \
  'QUIZ_IDENTITY_ACTIVE_KEY_ID=v1' \
  "QUIZ_IDENTITY_HMAC_KEY=$hmac_key" \
  'QUIZ_SECONDARY_BACKUP_DIR=/mnt/e/Program-personal/Repository/ustc-nihongo-quiz/_private/deployment-local/backups' \
  > "$env_file"

chown nihongo-quiz:nihongo-quiz "$env_file"
chmod 0600 "$env_file"
stat -c 'path=%n mode=%a owner=%U:%G size=%s' "$env_file"
