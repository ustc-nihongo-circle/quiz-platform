#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 LAB_OPERATOR_PASSWORD" >&2
  exit 2
fi

app_root=/srv/nihongo-quiz
current=$(readlink -f "$app_root/current")
set -a
source "$app_root/shared/env/production.env"
set +a
export PYTHONPATH="$current/src"
export LAB_OPERATOR_PASSWORD=$1
python="$current/.venv/bin/python"
cd "$current"

"$python" manage.py import_question_bank content/examples/question-bank-v1
"$python" manage.py provision_quiz_operator lab-operator
"$python" manage.py shell -c \
  'import os; from django.contrib.auth import get_user_model; user=get_user_model().objects.get(username="lab-operator"); user.set_password(os.environ["LAB_OPERATOR_PASSWORD"]); user.save(update_fields=("password",))'

if ! "$python" manage.py shell -v 0 -c \
  'from quiz.models import ActivityEdition; raise SystemExit(0 if ActivityEdition.objects.filter(slug="lab-2026").exists() else 1)'; then
  bank_version=$("$python" manage.py shell -v 0 -c \
    'from quiz.models import QuestionBankVersion; print(QuestionBankVersion.objects.order_by("-imported_at").values_list("version_code", flat=True).first())')
  "$python" manage.py create_activity_edition \
    lab-2026 \
    '本地虚构预发布活动' \
    "$bank_version" \
    --actor lab-operator \
    --reason '本地虚构预发布初始化' \
    --open
fi

echo 'seeded lab-operator and lab-2026 with public synthetic data'
