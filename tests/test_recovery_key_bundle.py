import json
import subprocess
import sys
from pathlib import Path


def test_portable_recovery_key_cli_requires_passphrase_and_preserves_existing_bundle(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "scripts/ops/recovery_key_bundle.py"
    target = tmp_path / "recovery.json"
    identity = "synthetic-recovery-identity"
    passphrase = "synthetic-passphrase-not-a-real-secret"

    def invoke(action, **payload):
        return subprocess.run(
            [sys.executable, str(helper), action, str(target)],
            input=json.dumps(payload), text=True, capture_output=True, check=False,
        )

    sealed = invoke("seal", identity=identity, passphrase=passphrase)
    assert sealed.returncode == 0
    original = target.read_bytes()
    assert identity.encode() not in original
    assert passphrase.encode() not in original
    assert identity not in sealed.stdout + sealed.stderr
    assert invoke("open", passphrase=passphrase).stdout == identity
    assert invoke("open", passphrase="wrong-passphrase").returncode == 1
    assert invoke("seal", identity="replacement", passphrase=passphrase).returncode == 1
    assert target.read_bytes() == original
    corrupted = json.loads(original)
    corrupted["ciphertext"] = corrupted["ciphertext"][:-4] + "AAAA"
    target.write_text(json.dumps(corrupted))
    assert invoke("open", passphrase=passphrase).returncode == 1
