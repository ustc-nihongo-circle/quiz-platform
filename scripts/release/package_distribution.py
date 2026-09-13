"""Build a whitelist-only Linux deployment ZIP from a pinned release manifest."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_FILES = ("quizctl.py", "compose.yaml", "Caddyfile", "Caddyfile.external",
                "nginx.conf.template", "init-db.sh")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = manifest["version"]
    if version != "v1.0.0" or manifest["format"] != "quiz-platform-release/v1":
        raise ValueError("This packager currently targets the v1.0.0 archive release")
    args.output.mkdir(exist_ok=True, parents=True)
    stem = "quiz-platform-" + version
    target = args.output / (stem + ".zip")
    files = {name: ROOT / "deploy/bundle" / name for name in BUNDLE_FILES}
    files.update({"README.md": ROOT / "docs/deployment-release.md",
                  "LICENSE": ROOT / "LICENSE", "NOTICE.md": ROOT / "NOTICE.md"})
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(stem + "/release.json", json.dumps(manifest, indent=2) + "\n")
        for name, source in files.items():
            archive.writestr(stem + "/" + name,
                             source.read_text(encoding="utf-8").replace("\r\n", "\n"))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files) + 1
    print(json.dumps({"file": str(target), "bytes": target.stat().st_size,
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
