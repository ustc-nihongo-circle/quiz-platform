import base64
import json
import os

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret")
os.environ.setdefault("DJANGO_USE_SQLITE", "1")
os.environ.setdefault(
    "QUIZ_IDENTITY_KEYS",
    json.dumps({"test-v1": base64.b64encode(b"k" * 32).decode("ascii")}),
)
os.environ.setdefault("QUIZ_IDENTITY_ACTIVE_KEY_ID", "test-v1")
os.environ.setdefault(
    "QUIZ_IDENTITY_HMAC_KEY",
    base64.b64encode(b"h" * 32).decode("ascii"),
)

from .settings import *  # noqa: F403
