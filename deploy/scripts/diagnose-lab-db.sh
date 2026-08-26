#!/usr/bin/env bash
set -euo pipefail

source /srv/nihongo-quiz/shared/env/production.env
echo "password_length=${#POSTGRES_PASSWORD}"
ss -ltnp | grep ":$POSTGRES_PORT" || true
if timeout 5 env PGPASSWORD="$POSTGRES_PASSWORD" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U nihongo_quiz -d nihongo_quiz \
  -tAc 'select current_user' \
  >/dev/null 2>&1; then
  echo 'password_login=ok'
else
  echo 'password_login=failed'
fi
sudo -u postgres psql -tAc \
  "select rolname, rolpassword is not null from pg_authid where rolname = 'nihongo_quiz'"
