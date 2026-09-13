#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 RELEASE_ZIP EXPECTED_SHA256" >&2
  exit 2
fi
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run this script with sudo" >&2
  exit 2
fi

archive=$(realpath "$1")
expected_sha=$2
app_root=/srv/nihongo-quiz
env_file=$app_root/shared/env/production.env
release_id=$(basename "$archive" .zip | sed 's/^nihongo-quiz-//')
release_dir=$app_root/releases/$release_id

if [[ ! -f "$env_file" ]]; then
  echo "missing $env_file" >&2
  exit 1
fi
if [[ -e "$release_dir" ]]; then
  echo "release already exists: $release_dir" >&2
  exit 1
fi

actual_sha=$(sha256sum "$archive" | awk '{print $1}')
if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "release SHA-256 mismatch" >&2
  exit 1
fi

install -d -o nihongo-quiz -g nihongo-quiz -m 0750 "$release_dir"
unzip -q "$archive" -d "$release_dir"
chown -R nihongo-quiz:nihongo-quiz "$release_dir"
runuser -u nihongo-quiz -- python3 -m venv "$release_dir/.venv"
runuser -u nihongo-quiz -- "$release_dir/.venv/bin/python" -m pip install --upgrade pip
runuser -u nihongo-quiz -- "$release_dir/.venv/bin/python" -m pip install "$release_dir"
runuser -u nihongo-quiz -- ln -s "$app_root/shared/media" "$release_dir/media"

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

cd "$release_dir"
runuser -u nihongo-quiz -- env PYTHONPATH="$release_dir/src" \
  "$release_dir/.venv/bin/python" manage.py check
runuser -u nihongo-quiz -- env PYTHONPATH="$release_dir/src" \
  "$release_dir/.venv/bin/python" manage.py migrate --noinput
runuser -u nihongo-quiz -- env PYTHONPATH="$release_dir/src" \
  "$release_dir/.venv/bin/python" manage.py collectstatic --noinput --clear

ln -sfn "$release_dir" "$app_root/current.next"
mv -Tf "$app_root/current.next" "$app_root/current"
systemctl restart nihongo-quiz.service
install -m 0644 "$release_dir/deploy/systemd/nihongo-quiz-expire-attempts.service" /etc/systemd/system/nihongo-quiz-expire-attempts.service
install -m 0644 "$release_dir/deploy/systemd/nihongo-quiz-expire-attempts.timer" /etc/systemd/system/nihongo-quiz-expire-attempts.timer
install -m 0644 "$release_dir/deploy/systemd/nihongo-quiz-prune-rate-limits.service" /etc/systemd/system/nihongo-quiz-prune-rate-limits.service
install -m 0644 "$release_dir/deploy/systemd/nihongo-quiz-prune-rate-limits.timer" /etc/systemd/system/nihongo-quiz-prune-rate-limits.timer
systemctl daemon-reload
systemctl enable --now nihongo-quiz-expire-attempts.timer
curl --fail --silent --header 'X-Forwarded-Proto: https' \
  http://127.0.0.1:18080/health/ready/ | grep -F '"database": "ok"' >/dev/null
echo "activated release $release_id"

systemctl enable --now nihongo-quiz-prune-rate-limits.timer
