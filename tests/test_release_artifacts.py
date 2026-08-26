from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_linux_shell_scripts_are_forced_to_lf_in_git_archives():
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes.splitlines()
    for script in (REPOSITORY_ROOT / "deploy" / "scripts").glob("*.sh"):
        assert b"\r\n" not in script.read_bytes(), script
