from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_linux_shell_scripts_are_forced_to_lf_in_git_archives():
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes.splitlines()
    assert "deploy/systemd/* text eol=lf" in attributes.splitlines()
    for script in (REPOSITORY_ROOT / "deploy" / "scripts").glob("*.sh"):
        assert b"\r\n" not in script.read_bytes(), script


def test_linux_services_and_restore_rehearsal_keep_private_files_private():
    for unit_name in ("nihongo-quiz.service", "nihongo-quiz-backup.service"):
        unit = (REPOSITORY_ROOT / "deploy" / "systemd" / unit_name).read_text(
            encoding="utf-8"
        )
        assert "UMask=0077" in unit

    restore_script = (
        REPOSITORY_ROOT / "deploy" / "scripts" / "restore-check.sh"
    ).read_text(encoding="utf-8")
    assert 'runuser -u postgres -- createdb --owner="$POSTGRES_USER"' in restore_script
    assert 'chmod -R go-rwx "$media_check"' in restore_script
