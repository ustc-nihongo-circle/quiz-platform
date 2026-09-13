#!/usr/bin/env python3
"""Run a pinned Quiz Platform release. Requires Python 3 and Docker Compose."""
import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def compose(*args, **kwargs):
    return run(["docker", "compose", "--project-directory", str(ROOT),
                "-f", str(ROOT / "compose.yaml"), *args], **kwargs)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def settings():
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Invalid configuration line")
            values[key] = value
    return values


def manifest():
    return json.loads((ROOT / "release.json").read_text(encoding="utf-8"))


def ensure_image():
    release = manifest()
    image = release["app_image"]
    existing = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                              capture_output=True, text=True, check=False)
    if existing.returncode == 0 and existing.stdout.strip() == release["app_image_id"]:
        return
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    archive = cache / (release["version"] + "-application.tar.gz")
    if not archive.is_file() or digest(archive) != release["app_asset_sha256"]:
        url = release["app_asset_url"]
        if not url.startswith("https://github.com/ustc-nihongo-circle/quiz-platform/releases/download/"):
            raise ValueError("Unexpected application image download origin")
        partial = archive.with_suffix(".partial")
        run(["curl", "--fail", "--location", "--retry", "3", "--output", str(partial), url])
        if digest(partial) != release["app_asset_sha256"]:
            raise ValueError("Application image checksum mismatch")
        partial.replace(archive)
    with gzip.open(archive, "rb") as stream:
        proc = subprocess.Popen(["docker", "image", "load"], stdin=subprocess.PIPE)
        try:
            shutil.copyfileobj(stream, proc.stdin)
            proc.stdin.close()
        finally:
            if proc.wait() != 0:
                raise RuntimeError("Could not load the application image")
    actual = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                 capture_output=True, text=True).stdout.strip()
    if actual != release["app_image_id"]:
        raise ValueError("Loaded application image ID differs from the release manifest")


def initialize(args):
    run(["docker", "compose", "version"])
    if (ROOT / ".upgrade-failed").exists():
        raise ValueError("This upgrade failed; use a fresh release directory to retry")
    if (ROOT / ".env").exists():
        print("Configuration already exists; no keys, settings or data were overwritten.")
        if not (ROOT / ".initialized").exists():
            if settings()["APP_IMAGE"] != manifest()["app_image"]:
                raise ValueError("Existing configuration belongs to another release")
            finish_initialization()
        return
    if not re.fullmatch(r"[A-Za-z0-9.-]+", args.domain):
        raise ValueError("Use a hostname without scheme, port or path")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        raise ValueError("Invalid Compose project name")
    ports = (args.http_port, args.https_port, args.admin_port, args.admin_origin_port)
    if any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("Ports must be between 1 and 65535")
    if len({args.http_port, args.https_port, args.admin_port}) != 3:
        raise ValueError("Published container ports must be distinct")
    existing = run(["docker", "volume", "ls", "--quiet", "--filter",
                    "label=com.docker.compose.project=" + args.project],
                   capture_output=True, text=True).stdout.strip()
    if existing:
        raise ValueError("Existing project volumes found; choose another --project or use upgrade")
    local = args.domain in {"localhost", "127.0.0.1"}
    if local and args.bind != "127.0.0.1":
        raise ValueError("Local demo mode is loopback-only; use a DNS hostname for public HTTPS")
    if args.external_proxy and (local or args.bind != "127.0.0.1"):
        raise ValueError("External HTTPS mode needs a DNS hostname and loopback-only binding")
    if args.external_proxy and args.admin_port == args.admin_origin_port:
        raise ValueError("Use different local admin and external HTTPS admin ports")
    for port in (args.http_port, args.https_port, args.admin_port):
        address = "127.0.0.1" if port == args.admin_port else args.bind
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((address, port))
            except OSError:
                message = f"Cannot bind {address}:{port}; check address and port use"
                raise ValueError(message) from None
    ensure_image()
    release = manifest()
    origin_port = args.admin_origin_port if args.external_proxy else args.admin_port
    admin_origin = (f"http://localhost:{args.admin_port}" if local
                    else f"https://{args.domain}:{origin_port}")
    values = {
        "COMPOSE_PROJECT_NAME": args.project,
        "APP_IMAGE": release["app_image"],
        "POSTGRES_IMAGE": release["postgres_image"],
        "GATEWAY_IMAGE": release["gateway_image"],
        "DB_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "QUIZ_IDENTITY_KEYS": json.dumps({"v1": base64.b64encode(os.urandom(32)).decode()},
                                         separators=(",", ":")),
        "QUIZ_IDENTITY_ACTIVE_KEY_ID": "v1",
        "QUIZ_IDENTITY_HMAC_KEY": base64.b64encode(os.urandom(32)).decode(),
        "DJANGO_DEBUG": "1" if local else "0",
        "DJANGO_ALLOWED_HOSTS": f"{args.domain},localhost,127.0.0.1,backend",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"{admin_origin}," +
            (f"http://localhost:{args.http_port}" if local else f"https://{args.domain}"),
        "DJANGO_BEHIND_PROXY": "1", "DJANGO_TRUSTED_PROXY_COUNT": "1",
        "DJANGO_SECURE_SSL_REDIRECT": "0" if local else "1",
        "DJANGO_SECURE_HSTS_SECONDS": "0" if local else "300",
        "PUBLIC_SITE": "http://:80" if local or args.external_proxy else args.domain,
        "ADMIN_SITE": ("http://:8081" if local or args.external_proxy
                       else f"{args.domain}:{args.admin_port}"),
        "ADMIN_ORIGIN": admin_origin,
        "EXTERNAL_PROXY": "1" if args.external_proxy else "0",
        "BIND_ADDRESS": args.bind, "HTTP_PORT": str(args.http_port),
        "HTTPS_PORT": str(args.https_port), "ADMIN_PORT": str(args.admin_port),
        "ADMIN_INTERNAL_PORT": "8081" if local or args.external_proxy else str(args.admin_port),
    }
    if args.external_proxy:
        certificate = args.tls_certificate or f"/etc/letsencrypt/live/{args.domain}/fullchain.pem"
        certificate_key = args.tls_key or f"/etc/letsencrypt/live/{args.domain}/privkey.pem"
        if any(not re.fullmatch(r"/[A-Za-z0-9_./-]+", p)
               for p in (certificate, certificate_key)):
            raise ValueError("Use simple absolute certificate paths without spaces")
        shutil.copy2(ROOT / "Caddyfile.external", ROOT / "Caddyfile")
        config = (ROOT / "nginx.conf.template").read_text()
        replacements = {"DOMAIN": args.domain, "CERTIFICATE": certificate,
                        "CERTIFICATE_KEY": certificate_key, "HTTP_PORT": args.http_port,
                        "ADMIN_PORT": args.admin_port, "ADMIN_ORIGIN_PORT": origin_port}
        for key, value in replacements.items():
            config = config.replace("__" + key + "__", str(value))
        (ROOT / "nginx.conf").write_text(config)
    (ROOT / "content").mkdir(exist_ok=True)
    os.umask(0o077)
    keydir = ROOT / ".keys"
    keydir.mkdir(exist_ok=True)
    private = keydir / "recovery.agekey"
    if not private.exists():
        result = run(["docker", "run", "--rm", "--entrypoint", "age-keygen",
                      release["app_image"]], capture_output=True, text=True)
        with private.open("x", encoding="utf-8") as stream:
            stream.write(result.stdout)
    recipient = run(["docker", "run", "--rm", "-i", "--entrypoint", "age-keygen",
                     release["app_image"], "-y"], input=private.read_bytes(),
                    capture_output=True).stdout.decode().strip()
    if not recipient.startswith("age1"):
        raise ValueError("Could not derive the backup recipient")
    values["BACKUP_RECIPIENT"] = recipient
    with (ROOT / ".env").open("x", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    finish_initialization()
    print("Initialized. Set an administrator with: python3 quizctl.py admin <name>")
    print("Keep .env and .keys/recovery.agekey private and store a separate recovery copy.")


def finish_initialization():
    ensure_image()
    compose("config", "--quiet")
    compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "caddy", "gateway",
            "validate", "--config", "/etc/caddy/Caddyfile")
    compose("up", "-d", "--wait", "db")
    for command in ("migrate", "collectstatic"):
        compose("run", "--rm", "-T", "--no-deps", "backend", "python", "manage.py",
                command, "--noinput")
    (ROOT / ".initialized").write_text(manifest()["version"] + "\n")


def encrypted_backup():
    cfg = settings()
    os.umask(0o077)
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    name = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    output = backup_dir / (name + ".tar.gz.age")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=ROOT) as temp:
        work = Path(temp)
        with (work / "database.dump").open("wb") as stream:
            compose("exec", "-T", "db", "pg_dump", "-U", "postgres", "-Fc", "quiz_platform",
                    stdout=stream)
        with (work / "media.tar.gz").open("wb") as stream:
            compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "backend",
                    "-czf", "-", "-C", "/app", "media", stdout=stream)
        shutil.copy2(ROOT / ".env", work / "application.env")
        shutil.copy2(ROOT / "release.json", work / "release.json")
        parts = ["database.dump", "media.tar.gz", "application.env", "release.json"]
        (work / "manifest.json").write_text(json.dumps({n: digest(work / n) for n in parts}))
        with tarfile.open(work / "bundle.tar.gz", "w:gz") as archive:
            for part in [*parts, "manifest.json"]:
                archive.add(work / part, arcname=part)
        with (work / "bundle.tar.gz").open("rb") as source, output.open("xb") as target:
            run(["docker", "run", "--rm", "-i", "--entrypoint", "age", cfg["APP_IMAGE"],
                 "-r", cfg["BACKUP_RECIPIENT"]], stdin=source, stdout=target)
    checksum = digest(output)
    output.with_suffix(output.suffix + ".sha256").write_text(checksum + "  " + output.name + "\n")
    print(output)
    return output


def restore(archive_path, identity_path, *, replace=False):
    """Verify the full archive before touching the target deployment."""
    cfg = settings()
    archive_path = archive_path.resolve(strict=True)
    identity_path = identity_path.resolve(strict=True)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=ROOT) as temporary:
        work = Path(temporary)
        with (work / "payload.tar.gz").open("wb") as output:
            run(["docker", "run", "--rm", "-i", "--network", "none", "--user", "0:0",
                 "--read-only", "--entrypoint", "age", "-v", f"{archive_path}:/archive:ro",
                 cfg["APP_IMAGE"], "--decrypt", "--identity", "-", "/archive"],
                input=identity_path.read_bytes(), stdout=output)
        names = {"database.dump", "media.tar.gz", "application.env", "release.json",
                 "manifest.json"}
        with tarfile.open(work / "payload.tar.gz", "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != len(names) or set(archive.getnames()) != names:
                raise ValueError("Unexpected recovery archive structure")
            if not all(member.isfile() for member in members):
                raise ValueError("Recovery payload must contain regular files only")
            if sum(member.size for member in members) > shutil.disk_usage(ROOT).free * 0.8:
                raise ValueError("Insufficient working space for recovery")
            for member in members:
                with (work / member.name).open("wb") as target:
                    shutil.copyfileobj(archive.extractfile(member), target)
        proof = json.loads((work / "manifest.json").read_text())
        if set(proof) != names - {"manifest.json"}:
            raise ValueError("Recovery checksum manifest is incomplete")
        for name, expected in proof.items():
            if digest(work / name) != expected:
                raise ValueError("Recovery payload checksum mismatch")
        restored_release = json.loads((work / "release.json").read_text())
        if restored_release["version"] != manifest()["version"]:
            raise ValueError("Restore into the release version named in the backup first")
        recovered = dict(line.split("=", 1) for line in
                         (work / "application.env").read_text().splitlines()
                         if line and not line.startswith("#"))
        identity_keys = ("DJANGO_SECRET_KEY", "QUIZ_IDENTITY_KEYS", "QUIZ_IDENTITY_ACTIVE_KEY_ID",
                         "QUIZ_IDENTITY_HMAC_KEY")
        if any(not recovered.get(key) for key in identity_keys):
            raise ValueError("Recovery identity configuration is incomplete")
        with tarfile.open(work / "media.tar.gz", "r:gz") as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if (not parts or parts[0] != "media" or ".." in parts or
                        not (member.isfile() or member.isdir())):
                    raise ValueError("Unsafe media archive entry")
        compose("up", "-d", "--wait", "db")
        count = compose("exec", "-T", "db", "psql", "-U", "postgres", "-d", "quiz_platform",
                        "-At", "-c", "SELECT count(*) FROM pg_tables WHERE schemaname='public'",
                        capture_output=True, text=True).stdout.strip()
        if count != "0" and not replace:
            raise ValueError("Target contains tables; --replace is required with a safety backup")
        compose("stop", "gateway", "backend", "maintenance")
        if count != "0":
            print("Saving the existing target before replacement.")
            encrypted_backup()
        compose("exec", "-T", "db", "psql", "-U", "postgres", "-d", "quiz_platform", "-v",
                "ON_ERROR_STOP=1", "-c", "DROP SCHEMA public CASCADE; "
                "CREATE SCHEMA public AUTHORIZATION quiz_platform")
        with (work / "database.dump").open("rb") as source:
            compose("exec", "-T", "db", "pg_restore", "-U", "postgres", "--role=quiz_platform",
                    "--no-owner", "--no-acl", "--exit-on-error", "-d", "quiz_platform",
                    stdin=source)
        clear_media = (
            "from pathlib import Path; import shutil; p=Path('/app/media'); "
            "assert p.resolve()==p; "
            "[(f.unlink() if f.is_symlink() or f.is_file() else shutil.rmtree(f)) "
            "for f in p.iterdir()]"
        )
        compose("run", "--rm", "-T", "--no-deps", "backend", "python", "-c", clear_media)
        with (work / "media.tar.gz").open("rb") as source:
            compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "tar", "backend",
                    "-xzf", "-", "--no-same-owner", "--no-same-permissions", "-C", "/app",
                    stdin=source)
        for key in identity_keys:
            cfg[key] = recovered[key]
        temporary_env = ROOT / ".env.restoring"
        temporary_env.write_text("".join(f"{key}={value}\n" for key, value in cfg.items()))
        temporary_env.replace(ROOT / ".env")
        compose("run", "--rm", "-T", "--no-deps", "backend", "python", "manage.py", "check")
        print("Restored; runtime remains stopped. Review the result, then run quizctl.py start.")


def upgrade(previous):
    previous = previous.resolve(strict=True)
    if previous == ROOT or (ROOT / ".env").exists():
        raise ValueError("Extract the new release into a separate, uninitialized directory")
    old_cfg = dict(line.split("=", 1) for line in (previous / ".env").read_text().splitlines()
                   if line and not line.startswith("#"))
    previous_release = json.loads((previous / "release.json").read_text())
    if manifest()["postgres_image"] != previous_release["postgres_image"]:
        raise ValueError("Database engine upgrades require a separate tested migration")
    if not (previous / ".keys/recovery.agekey").is_file() or not (previous / "content").is_dir():
        raise ValueError("Previous deployment recovery key or content directory is missing")
    old_command = [sys.executable, str(previous / "quizctl.py")]
    guard = ("from quiz.models import ActivityEdition,QuizAttempt; "
             "assert not ActivityEdition.objects.filter(status='open').exists(),"
             "'Pause activities before upgrading'; "
             "assert not QuizAttempt.objects.filter(status='in_progress').exists(),"
             "'Wait for existing attempts to finish before upgrading'")
    run([*old_command, "manage", "shell", "-v", "0", "-c", guard])
    ensure_image()
    run([*old_command, "stop"])
    # Restart only the old database to take a stable pre-upgrade backup.
    run(["docker", "compose", "--project-directory", str(previous), "-f",
         str(previous / "compose.yaml"), "up", "-d", "--wait", "db"])
    try:
        result = run([*old_command, "backup"], capture_output=True, text=True)
    except subprocess.CalledProcessError:
        run([*old_command, "start"])
        raise RuntimeError("Pre-upgrade backup failed; previous runtime restarted") from None
    safety_backup = Path(result.stdout.strip().splitlines()[-1])
    cfg = dict(old_cfg)
    cfg.update(APP_IMAGE=manifest()["app_image"], GATEWAY_IMAGE=manifest()["gateway_image"])
    os.umask(0o077)
    with (ROOT / ".env").open("x") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in cfg.items()))
    shutil.copytree(previous / ".keys", ROOT / ".keys")
    shutil.copytree(previous / "content", ROOT / "content")
    if cfg.get("EXTERNAL_PROXY") == "1":
        shutil.copy2(ROOT / "Caddyfile.external", ROOT / "Caddyfile")
    try:
        compose("up", "-d", "--wait", "db")
        for command in ("migrate", "collectstatic"):
            compose("run", "--rm", "-T", "--no-deps", "backend", "python", "manage.py",
                    command, "--noinput")
        compose("up", "-d", "--wait", "backend")
        compose("run", "--rm", "-T", "--no-deps", "--entrypoint", "caddy", "gateway",
                "validate", "--config", "/etc/caddy/Caddyfile")
    except subprocess.CalledProcessError:
        (ROOT / ".upgrade-failed").write_text("Retry this upgrade in a fresh directory.\n")
        compose("stop", "gateway", "backend", "maintenance")
        run([*old_command, "restore", str(safety_backup), "--identity",
             str(previous / ".keys/recovery.agekey"), "--replace"])
        run([*old_command, "start"])
        raise RuntimeError("Upgrade failed; the paused previous release was restored") from None
    compose("up", "-d", "--wait", "gateway", "maintenance")
    (ROOT / ".initialized").write_text(manifest()["version"] + "\n")
    print("Upgraded. The previous directory and pre-upgrade backup were retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--domain", default="localhost")
    init.add_argument("--project", default="quiz-platform")
    init.add_argument("--bind", default="127.0.0.1")
    init.add_argument("--http-port", type=int, default=8080)
    init.add_argument("--https-port", type=int, default=8443)
    init.add_argument("--admin-port", type=int, default=19443)
    init.add_argument("--external-proxy", action="store_true")
    init.add_argument("--admin-origin-port", type=int, default=19443)
    init.add_argument("--tls-certificate")
    init.add_argument("--tls-key")
    commands.add_parser("start")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("backup")
    recovery = commands.add_parser("restore")
    recovery.add_argument("archive", type=Path)
    recovery.add_argument("--identity", type=Path, required=True)
    recovery.add_argument("--replace", action="store_true")
    update = commands.add_parser("upgrade")
    update.add_argument("--from", dest="previous", type=Path, required=True)
    admin = commands.add_parser("admin")
    admin.add_argument("name")
    manage = commands.add_parser("manage")
    manage.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "init":
        initialize(args)
    elif args.action == "start":
        if not (ROOT / ".initialized").is_file() or (ROOT / ".upgrade-failed").exists():
            raise ValueError("Complete initialization or a successful upgrade before starting")
        ensure_image()
        compose("up", "-d", "--wait")
    elif args.action == "stop":
        compose("stop")
    elif args.action == "status":
        compose("ps")
    elif args.action == "backup":
        encrypted_backup()
    elif args.action == "restore":
        restore(args.archive, args.identity, replace=args.replace)
    elif args.action == "upgrade":
        upgrade(args.previous)
    elif args.action == "admin":
        compose("run", "--rm", "--no-deps", "backend", "python", "manage.py",
                "provision_quiz_operator", args.name)
        compose("run", "--rm", "--no-deps", "backend", "python", "manage.py",
                "changepassword", args.name)
    else:
        compose("run", "--rm", "--no-deps", "backend", "python", "manage.py", *args.arguments)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Operation failed: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
