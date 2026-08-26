#!/usr/bin/env bash
set -euo pipefail

mode=${1:-daily}
if [[ ! "$mode" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "backup label contains unsupported characters" >&2
  exit 2
fi

app_root=/srv/nihongo-quiz
env_file=$app_root/shared/env/production.env
backup_root=$app_root/backups
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
final_dir=$backup_root/${mode}-${timestamp}
work_dir=$(mktemp -d "$backup_root/.building-${mode}-${timestamp}-XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
export PGPASSWORD="$POSTGRES_PASSWORD"

pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --host="$POSTGRES_HOST" \
  --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" \
  --file="$work_dir/database.dump" \
  "$POSTGRES_DB"

tar -C "$app_root/shared" -czf "$work_dir/media.tar.gz" media
readlink -f "$app_root/current" > "$work_dir/release.txt"
"$app_root/current/.venv/bin/python" "$app_root/current/manage.py" showmigrations --plan > "$work_dir/migrations.txt"
(
  cd "$work_dir"
  sha256sum database.dump media.tar.gz release.txt migrations.txt > manifest.sha256
)
chmod -R go-rwx "$work_dir"
mv "$work_dir" "$final_dir"
trap - EXIT

if [[ "$mode" == "daily" ]]; then
  mapfile -t old_daily < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name 'daily-*' -printf '%T@ %p\n' | sort -nr | tail -n +8 | cut -d' ' -f2-)
  for old in "${old_daily[@]}"; do rm -rf -- "$old"; done
fi

if [[ -z "${QUIZ_SECONDARY_BACKUP_DIR:-}" ]]; then
  echo "backup created but no secondary backup directory is configured" >&2
  exit 1
fi
install -d -m 0700 "$QUIZ_SECONDARY_BACKUP_DIR"
cp -a "$final_dir" "$QUIZ_SECONDARY_BACKUP_DIR/"
echo "$final_dir"
