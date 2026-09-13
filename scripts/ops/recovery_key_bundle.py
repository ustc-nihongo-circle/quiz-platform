"""Portable, passphrase-encrypted recovery keys; secret inputs use stdin only."""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "quiz-platform-recovery-key/v1"
PARAMETERS = {"n": 131072, "r": 8, "p": 1}


def derive(passphrase, salt):
    return Scrypt(salt=salt, length=32, **PARAMETERS).derive(passphrase.encode("utf-8"))


def seal(secret, passphrase):
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("Use a passphrase of at least 12 characters")
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("An existing recovery identity is required")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(derive(passphrase, salt)).encrypt(
        nonce, secret.encode("utf-8"), FORMAT.encode("ascii")
    )
    return {"format": FORMAT, "kdf": {"name": "scrypt", **PARAMETERS},
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii")}


def unseal(bundle, passphrase):
    if bundle["format"] != FORMAT or bundle["kdf"] != {"name": "scrypt", **PARAMETERS}:
        raise ValueError("Unsupported recovery bundle format")
    salt, nonce, ciphertext = (base64.b64decode(bundle[k], validate=True)
                               for k in ("salt", "nonce", "ciphertext"))
    if len(salt) != 16 or len(nonce) != 12:
        raise ValueError("Invalid recovery bundle")
    return AESGCM(derive(passphrase, salt)).decrypt(
        nonce, ciphertext, FORMAT.encode("ascii")
    ).decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seal", "open", "verify"))
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    if args.action == "seal":
        bundle = seal(request["identity"], request["passphrase"])
        assert unseal(bundle, request["passphrase"]) == request["identity"]
        # Never overwrite the only existing recovery key package.
        fd = os.open(args.bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(bundle, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"state": "sealed_and_verified", "format": FORMAT}))
    else:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        secret = unseal(bundle, request["passphrase"])
        if args.action == "open":
            # For piping directly into age --identity -; do not run into a log.
            sys.stdout.write(secret)
        else:
            assert secret == request["identity"]
            print(json.dumps({"state": "independent_process_verified"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Recovery key operation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
