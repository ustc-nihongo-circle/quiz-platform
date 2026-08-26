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
mkdir -p "$QUIZ_SECONDARY_BACKUP_DIR"
if ! chmod 0700 "$QUIZ_SECONDARY_BACKUP_DIR" 2>/dev/null; then
  # Windows DrvFs/NTFS mounts may reject POSIX mode changes. The primary Linux
  # backup remains 0700; access to this secondary copy is governed by Windows ACLs.
  echo "secondary backup filesystem does not support chmod; relying on host ACLs" >&2
fi
# The integrity manifest, rather than POSIX metadata, is authoritative for the
# secondary copy. Copy the flat bundle file-by-file because GNU cp otherwise
# tries to chmod a newly-created directory even with --no-preserve on DrvFs.
secondary_target=$QUIZ_SECONDARY_BACKUP_DIR/$(basename "$final_dir")
mkdir "$secondary_target"
for backup_file in "$final_dir"/*; do
  dd if="$backup_file" of="$secondary_target/$(basename "$backup_file")" \
    conv=fsync status=none
done
echo "$final_dir"
