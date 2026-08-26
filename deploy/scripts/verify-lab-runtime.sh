#!/usr/bin/env bash
set -euo pipefail

app_root=/srv/nihongo-quiz
current=$(readlink -f "$app_root/current")
echo "current=$(basename "$current")"
source "$app_root/shared/env/production.env"
export PYTHONPATH="$current/src"
"$current/.venv/bin/python" - <<'PY'
import config
import quiz

print(f"config={config.__file__}")
print(f"quiz={quiz.__file__}")
PY

systemctl is-active postgresql nihongo-quiz caddy
curl --fail --silent --header 'X-Forwarded-Proto: https' \
  http://127.0.0.1:18080/health/ready/
echo
ca=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
curl --fail --silent --cacert "$ca" https://quiz.localhost:18443/health/ready/
echo
test -f "$current/staticfiles/quiz/ops.css"
curl --fail --silent --cacert "$ca" https://quiz.localhost:18443/static/quiz/ops.css \
  | head -c 24
echo
