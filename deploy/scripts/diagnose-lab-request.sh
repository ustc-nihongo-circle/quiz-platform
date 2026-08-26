#!/usr/bin/env bash
set -euo pipefail

app_root=/srv/nihongo-quiz
current=$(readlink -f "$app_root/current")
set -a
source "$app_root/shared/env/production.env"
set +a
export PYTHONPATH="$current/src"
export DJANGO_SETTINGS_MODULE=config.settings

"$current/.venv/bin/python" - <<'PY'
import django
from django.test import Client

django.setup()
response = Client(raise_request_exception=True).get(
    "/ops/login/",
    HTTP_HOST="quiz.localhost:18443",
    HTTP_X_FORWARDED_FOR="127.0.0.1",
    HTTP_X_FORWARDED_HOST="quiz.localhost:18443",
    HTTP_X_FORWARDED_PROTO="https",
)
print(f"status={response.status_code}")
PY
