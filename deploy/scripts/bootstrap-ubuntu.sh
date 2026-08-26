#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run this script with sudo" >&2
  exit 2
fi
if [[ "$(ps -p 1 -o comm=)" != "systemd" ]]; then
  echo "systemd is not PID 1; enable WSL systemd before provisioning services" >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
deploy_root=$(cd -- "$script_dir/.." && pwd)
app_root=/srv/nihongo-quiz
env_file=$app_root/shared/env/production.env

apt-get update
apt-get install -y ca-certificates curl postgresql-common python3 python3-venv unzip
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt-get update
apt-get install -y postgresql-18

apt-get install -y debian-keyring debian-archive-keyring apt-transport-https gnupg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy

if ! getent group nihongo-quiz >/dev/null; then
  groupadd --system nihongo-quiz
fi
if ! id nihongo-quiz >/dev/null 2>&1; then
  useradd --system --gid nihongo-quiz --home-dir "$app_root" --shell /usr/sbin/nologin nihongo-quiz
fi
usermod -a -G nihongo-quiz caddy
install -d -o nihongo-quiz -g nihongo-quiz -m 0750 \
  "$app_root/releases" \
  "$app_root/shared/media" \
  "$app_root/shared/env" \
  "$app_root/backups" \
  "$app_root/restore-checks"

if [[ ! -f "$env_file" ]]; then
  install -o nihongo-quiz -g nihongo-quiz -m 0600 "$deploy_root/env.production.example" "$env_file.example"
  echo "created $env_file.example"
  echo "copy it to $env_file, replace every placeholder, then rerun this script" >&2
  exit 3
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
if [[ "$POSTGRES_DB" != "nihongo_quiz" || "$POSTGRES_USER" != "nihongo_quiz" ]]; then
  echo "the bootstrap script expects POSTGRES_DB and POSTGRES_USER to equal nihongo_quiz" >&2
  exit 1
fi

sudo -u postgres psql --set=app_password="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE nihongo_quiz LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nihongo_quiz')\gexec
ALTER ROLE nihongo_quiz PASSWORD :'app_password';
SELECT 'CREATE DATABASE nihongo_quiz OWNER nihongo_quiz'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'nihongo_quiz')\gexec
SQL

install -m 0644 "$deploy_root/systemd/nihongo-quiz.service" /etc/systemd/system/nihongo-quiz.service
install -m 0644 "$deploy_root/systemd/nihongo-quiz-backup.service" /etc/systemd/system/nihongo-quiz-backup.service
install -m 0644 "$deploy_root/systemd/nihongo-quiz-backup.timer" /etc/systemd/system/nihongo-quiz-backup.timer
install -m 0644 "$deploy_root/Caddyfile.lab" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable postgresql caddy nihongo-quiz.service nihongo-quiz-backup.timer
systemctl restart postgresql caddy

echo "Ubuntu host prerequisites are ready. Install the first release next."
