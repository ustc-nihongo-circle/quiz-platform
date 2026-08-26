#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 BACKUP_DIRECTORY TARGET_DATABASE" >&2
  exit 2
fi

backup_dir=$(realpath "$1")
target_db=$2
if [[ ! "$target_db" =~ _restorecheck$ ]]; then
  echo "target database must end with _restorecheck" >&2
  exit 2
fi

app_root=/srv/nihongo-quiz
env_file=$app_root/shared/env/production.env
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
export PGPASSWORD="$POSTGRES_PASSWORD"

(cd "$backup_dir" && sha256sum --check manifest.sha256)
if psql --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" --list --tuples-only | cut -d'|' -f1 | tr -d ' ' | grep -Fxq "$target_db"; then
  echo "target database already exists; refusing to overwrite it" >&2
  exit 1
fi

createdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" --username="$POSTGRES_USER" "$target_db"
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --host="$POSTGRES_HOST" \
  --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" \
  --dbname="$target_db" \
  "$backup_dir/database.dump"

media_check=$app_root/restore-checks/$target_db-media
if [[ -e "$media_check" ]]; then
  echo "media restore target already exists: $media_check" >&2
  exit 1
fi
install -d -m 0700 "$media_check"
tar -C "$media_check" -xzf "$backup_dir/media.tar.gz"
echo "restored database=$target_db media=$media_check"
