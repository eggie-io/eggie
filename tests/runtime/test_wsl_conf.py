"""wsl-conf.py keeps a WSL VM off the user's Windows files, on every install,
without disturbing what else wsl.conf says."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "wsl-conf.py"
INSTALL = ROOT / "runtime" / "install" / "install.sh"

ISOLATION = ["automount.enabled=false", "interop.enabled=true",
             "interop.appendWindowsPath=false"]

# A VM without the settings yet: create() wrote [boot], wsl-user.sh appended
# [user].
EXISTING_VM = "[boot]\nsystemd=true\n\n[user]\ndefault=eggie\n"
ISOLATED_VM = ("[boot]\nsystemd=true\n\n[user]\ndefault=eggie\n\n"
               "[automount]\nenabled=false\n\n"
               "[interop]\nenabled=true\nappendWindowsPath=false\n")


def _run(conf: Path, *keys: str):
    return subprocess.run([sys.executable, str(SCRIPT), str(conf), *keys],
                          capture_output=True, text=True)


def test_an_existing_vm_gets_the_isolation_sections_and_keeps_boot_and_user(tmp_path):
    conf = tmp_path / "wsl.conf"
    conf.write_text(EXISTING_VM)
    result = _run(conf, *ISOLATION)
    assert result.returncode == 0, result.stderr
    assert conf.read_text() == ISOLATED_VM


def test_running_twice_changes_nothing_the_second_time(tmp_path):
    conf = tmp_path / "wsl.conf"
    conf.write_text(EXISTING_VM)
    _run(conf, *ISOLATION)
    again = _run(conf, *ISOLATION)
    assert again.returncode == 0
    assert conf.read_text() == ISOLATED_VM
    # install.sh prints this line; repeating it on every update would claim a
    # restart is due when none is.
    assert again.stdout == ""


def test_a_vm_an_earlier_runtime_gave_interop_off_gets_it_back_on(tmp_path):
    # Interop off in one distro unregisters it on the kernel every WSL distro
    # shares, so the user's other distros lose it too.
    conf = tmp_path / "wsl.conf"
    conf.write_text(ISOLATED_VM.replace("[interop]\nenabled=true",
                                        "[interop]\nenabled=false"))
    result = _run(conf, *ISOLATION)
    assert conf.read_text() == ISOLATED_VM
    assert "next start" in result.stdout


def test_a_hand_edit_is_reverted_and_its_comments_kept(tmp_path):
    conf = tmp_path / "wsl.conf"
    conf.write_text("[boot]\nsystemd=true\n\n[interop]\nEnabled = false\n"
                    "# kept\nappendWindowsPath=true\n\n[user]\ndefault=eggie\n")
    _run(conf, *ISOLATION)
    text = conf.read_text()
    assert "Enabled = false" not in text and "appendWindowsPath=true" not in text
    assert text.count("[interop]") == 1
    assert "[interop]\nenabled=true\n# kept\nappendWindowsPath=false\n" in text
    assert "[user]\ndefault=eggie\n" in text


def test_a_missing_key_lands_in_its_own_section_not_the_last_one(tmp_path):
    conf = tmp_path / "wsl.conf"
    conf.write_text("[automount]\nroot=/win/\n\n[user]\ndefault=eggie\n")
    _run(conf, *ISOLATION)
    text = conf.read_text()
    assert "[automount]\nroot=/win/\nenabled=false\n\n[user]\ndefault=eggie\n" in text


def test_every_copy_of_a_repeated_section_is_set(tmp_path):
    # Which duplicate WSL honours is not something to bet the isolation on.
    conf = tmp_path / "wsl.conf"
    conf.write_text("[automount]\nenabled=true\n[user]\ndefault=eggie\n"
                    "[automount]\nenabled=true\n")
    _run(conf, *ISOLATION)
    assert conf.read_text().count("[automount]\nenabled=false\n") == 2


def test_a_missing_file_is_created_world_readable(tmp_path):
    conf = tmp_path / "wsl.conf"
    _run(conf, *ISOLATION)
    assert conf.read_text() == ("[automount]\nenabled=false\n\n"
                                "[interop]\nenabled=true\nappendWindowsPath=false\n")
    # WSL's init reads it; so does every user's shell tooling.
    assert conf.stat().st_mode & 0o777 == 0o644


def test_install_enforces_isolation_on_wsl_only(tmp_path):
    text = INSTALL.read_text()
    branch = text[text.index('if [[ "$vm" == wsl ]]; then'):]
    branch = branch[:branch.index("\nfi\n")]
    assert "lib/wsl-conf.py" in branch
    for key in ISOLATION:
        assert key in branch
