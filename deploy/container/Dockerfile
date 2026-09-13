FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends age ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 quiz && useradd --uid 10001 --gid quiz --no-create-home quiz
COPY deploy/container/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir -r /tmp/requirements.lock
COPY --chown=10001:10001 src/ /app/src/
COPY --chown=10001:10001 manage.py pyproject.toml README.md LICENSE NOTICE.md /app/
COPY --chown=10001:10001 content/examples/ /app/content/examples/
COPY --chown=10001:10001 scripts/ops/ /app/scripts/ops/
RUN mkdir -p /app/var /app/media /app/staticfiles && chown -R quiz:quiz /app/var /app/media /app/staticfiles
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health/ready/',headers={'X-Forwarded-Proto':'https'}),timeout=3)"
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "35", "--error-logfile", "-"]
