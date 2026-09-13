"""Decrypt an age backup using a passphrase-wrapped key without displaying the key."""
import argparse
import getpass
import json
import os
import subprocess
from pathlib import Path

from recovery_key_bundle import unseal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    secret = unseal(json.loads(args.bundle.read_text(encoding="utf-8")),
                    getpass.getpass("Recovery passphrase (hidden): "))
    # age reads its identity from stdin; the archive remains a named file.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    succeeded = False
    try:
        with os.fdopen(fd, "wb") as output:
            subprocess.run(["age", "--decrypt", "--identity", "-", str(args.archive)],
                           input=secret.encode("utf-8"), stdout=output, check=True)
        succeeded = True
    finally:
        if not succeeded:
            args.output.unlink(missing_ok=True)
    print("Decrypted into the specified private file; keep it on encrypted storage.")


if __name__ == "__main__":
    main()
