"""The `omelet` host setup adds to the user's ~/.ssh/config on macOS.

Lima regenerates the guest's host keys on every start, so a connection that
remembers them in known_hosts breaks after the next reboot or reinstall.
"""
from host.core.install import default_steps
from host.providers import ssh_alias
from host.providers.lima import LimaProvider
from host.providers.wsl2 import Wsl2Provider

LIMA_SSH_CONFIG = """\
Host lima-omelet-vm
  IdentityFile "/Users/you/.lima/_config/user"
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  User you
  Hostname 127.0.0.1
  Port 39022
"""

FOUND = {"Host": "127.0.0.1", "Port": "39022", "User": "you",
         "Identity file": "/Users/you/.lima/_config/user"}

ORBSTACK = """\
# Added by OrbStack: 'orb' SSH host for Linux machines
Include ~/.orbstack/ssh/config

host trex
HostName example.com
Port 2200
"""


def _provider(tmp_path, *, written=True):
    home = tmp_path / ".lima"
    if written:
        (home / "omelet-vm").mkdir(parents=True)
        (home / "omelet-vm" / "ssh.config").write_text(LIMA_SSH_CONFIG)
    return LimaProvider(name="omelet-vm", runner=lambda argv: None, lima_home=home,
                        data_root=tmp_path / "data", ssh_dir=tmp_path / ".ssh")


def test_the_host_turns_off_host_key_checking_for_loopback_only():
    text = ssh_alias.render(FOUND)
    assert "Host omelet\n" in text
    assert "  HostName 127.0.0.1\n" in text
    assert "  Port 39022\n" in text
    assert "  User you\n" in text
    assert "  IdentityFile /Users/you/.lima/_config/user\n" in text
    assert "  StrictHostKeyChecking no\n" in text
    assert "  UserKnownHostsFile /dev/null\n" in text


def test_a_value_with_a_space_is_quoted():
    text = ssh_alias.render({**FOUND, "Identity file": "/Users/a b/.lima/_config/user"})
    assert '  IdentityFile "/Users/a b/.lima/_config/user"\n' in text


def test_the_include_goes_above_every_existing_host_block(tmp_path):
    # Appended after `host trex`, it would only be read when connecting to trex.
    config = tmp_path / "config"
    config.write_text(ORBSTACK)
    alias = tmp_path / "data" / "ssh_config"
    ssh_alias.install(alias, config, FOUND)

    lines = config.read_text().splitlines()
    assert lines[1] == f'Include "{alias}"'
    assert config.read_text().endswith(ORBSTACK)
    assert ssh_alias.installed(alias, config)


def test_installing_twice_adds_one_include(tmp_path):
    config = tmp_path / "config"
    config.write_text(ORBSTACK)
    alias = tmp_path / "ssh_config"
    ssh_alias.install(alias, config, FOUND)
    ssh_alias.install(alias, config, {**FOUND, "Port": "40000"})
    assert config.read_text().count("Include") == 2      # ours and OrbStack's
    assert "  Port 40000\n" in alias.read_text()


def test_a_missing_ssh_config_is_created_private(tmp_path):
    config = tmp_path / ".ssh" / "config"
    ssh_alias.install(tmp_path / "ssh_config", config, FOUND)
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.parent.stat().st_mode & 0o777 == 0o700


def test_a_symlinked_config_is_written_through(tmp_path):
    real = tmp_path / "dotfiles" / "ssh_config"
    real.parent.mkdir()
    real.write_text(ORBSTACK)
    link = tmp_path / "config"
    link.symlink_to(real)
    ssh_alias.install(tmp_path / "ssh_config", link, FOUND)
    assert link.is_symlink()
    assert "Include" in real.read_text().splitlines()[1]


def test_remove_restores_the_file_it_found(tmp_path):
    config = tmp_path / "config"
    config.write_text(ORBSTACK)
    alias = tmp_path / "ssh_config"
    ssh_alias.install(alias, config, FOUND)
    ssh_alias.remove(alias, config)
    assert config.read_text() == ORBSTACK
    assert not alias.exists()


def test_remove_without_an_install_changes_nothing(tmp_path):
    config = tmp_path / "config"
    config.write_text(ORBSTACK)
    ssh_alias.remove(tmp_path / "ssh_config", config)
    ssh_alias.remove(tmp_path / "ssh_config", tmp_path / "absent")
    assert config.read_text() == ORBSTACK


def test_the_lima_step_writes_the_values_lima_wrote(tmp_path):
    provider = _provider(tmp_path)
    message = provider.ssh_shortcut()()
    assert message == "Connect with: ssh omelet"
    assert "  User you\n" in (tmp_path / "data" / "ssh_config").read_text()
    assert provider.access().command == "ssh omelet"


def test_the_lima_step_never_fails_setup(tmp_path):
    provider = _provider(tmp_path, written=False)
    message = provider.ssh_shortcut()()
    assert message.startswith("Could not add the 'omelet' host")
    assert not (tmp_path / ".ssh" / "config").exists()


def test_destroy_takes_the_host_away(tmp_path):
    provider = _provider(tmp_path)
    provider._run = lambda argv: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
    provider.ssh_shortcut()()
    provider.destroy()
    assert (tmp_path / ".ssh" / "config").read_text() == ""
    assert not (tmp_path / "data" / "ssh_config").exists()


def test_the_step_follows_create_vm_on_lima_and_is_absent_on_wsl(tmp_path):
    kwargs = dict(cache_dir=tmp_path, template_dir=tmp_path, domain="x", exe_path="x")
    lima = [s.name for s in default_steps(_provider(tmp_path), **kwargs)]
    assert lima[lima.index("create_vm") + 1] == "ssh_alias"
    assert Wsl2Provider().ssh_shortcut() is None
