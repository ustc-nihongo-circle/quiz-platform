#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 RELEASE_ID" >&2
  exit 2
fi
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "run this script with sudo" >&2
  exit 2
fi

app_root=/srv/nihongo-quiz
release_dir=$app_root/releases/$1
if [[ ! -d "$release_dir" ]]; then
  echo "unknown release: $release_dir" >&2
  exit 1
fi

ln -sfn "$release_dir" "$app_root/current.next"
mv -Tf "$app_root/current.next" "$app_root/current"
systemctl restart nihongo-quiz.service
curl --fail --silent http://127.0.0.1:18080/health/ready/ >/dev/null
echo "rolled application code back to $1"
echo "database migrations were not reversed"
