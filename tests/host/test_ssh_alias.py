"""The `omelet` host and the VM's host keys, as macOS setup writes them.

Lima regenerates the guest's host keys on a new cloud-init instance, which it
declares on every start; a reinstall is a new VM either way. A known_hosts
entry for the old keys then blocks every client -- including Cursor's, which
ignores StrictHostKeyChecking -- so the entries are replaced, not bypassed.
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

PUBKEYS = """\
ecdsa-sha2-nistp256 AAAAE2VjZHNh root@lima-omelet-vm
ssh-ed25519 AAAAC3NzaC1l root@lima-omelet-vm
ssh-rsa AAAAB3NzaC1y root@lima-omelet-vm
"""

USER_CONFIG = """\
# Global options come before any Host block.
Include ~/.ssh/config.d/*

Host work
HostName example.com
Port 2200
"""


class Guest:
    """limactl shell answers with the guest's public keys; ssh-keygen -R
    drops the address's lines the way the real one does."""

    def __init__(self, pubkeys=PUBKEYS):
        self.calls = []
        self._pubkeys = pubkeys

    def __call__(self, argv):
        self.calls.append(argv)
        out = b""
        if argv[0] == "limactl":
            out = self._pubkeys.encode()
        elif argv[:2] == ["ssh-keygen", "-R"]:
            path = argv[4]
            with open(path) as f:
                kept = [l for l in f if not l.startswith(argv[2] + " ")]
            with open(path, "w") as f:
                f.writelines(kept)
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": b""})()


def _provider(tmp_path, *, written=True, runner=None):
    home = tmp_path / ".lima"
    if written:
        (home / "omelet-vm").mkdir(parents=True)
        (home / "omelet-vm" / "ssh.config").write_text(LIMA_SSH_CONFIG)
    return LimaProvider(name="omelet-vm", runner=runner or Guest(), lima_home=home,
                        data_root=tmp_path / "data", ssh_dir=tmp_path / ".ssh")


# --- the Host block ---

def test_the_host_names_what_lima_wrote():
    text = ssh_alias.render(FOUND)
    assert "Host omelet\n" in text
    for line in ("  HostName 127.0.0.1", "  Port 39022", "  User you",
                 "  IdentityFile /Users/you/.lima/_config/user"):
        assert line + "\n" in text


def test_a_value_with_a_space_is_quoted():
    text = ssh_alias.render({**FOUND, "Identity file": "/Users/a b/.lima/_config/user"})
    assert '  IdentityFile "/Users/a b/.lima/_config/user"\n' in text


def test_the_block_is_written_into_the_config_not_included(tmp_path):
    # Cursor's host list does not follow Include.
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    ssh_alias.install(config, FOUND)
    text = config.read_text()
    assert "Host omelet\n" in text
    assert text.count("Include") == 1          # the user's own only
    assert ssh_alias.installed(config)


def test_the_block_goes_after_global_options_and_before_every_host(tmp_path):
    # Above the user's Include, that Include would apply to omelet alone; after
    # a `Host *` with a User line, ssh would take that User instead of ours.
    config = tmp_path / "config"
    config.write_text(USER_CONFIG + "\nHost *\n  User someone\n")
    ssh_alias.install(config, FOUND)
    text = config.read_text()
    assert text.index("Include ~/.ssh/config.d") < text.index("Host omelet")
    assert text.index("Host omelet") < text.index("Host work") < text.index("Host *")


def test_a_config_with_no_hosts_gets_the_block_at_the_end(tmp_path):
    config = tmp_path / "config"
    config.write_text("AddKeysToAgent yes")          # no trailing newline
    ssh_alias.install(config, FOUND)
    assert config.read_text().startswith("AddKeysToAgent yes\n# >>> Omelet")


def test_a_block_whose_end_marker_was_deleted_takes_nothing_with_it(tmp_path):
    config = tmp_path / "config"
    damaged = USER_CONFIG.replace("Host work", ssh_alias._BEGIN + "\nHost work")
    config.write_text(damaged)
    ssh_alias.remove(config)
    assert config.read_text() == damaged
    ssh_alias.install(config, FOUND)
    assert "Host work\nHostName example.com\nPort 2200\n" in config.read_text()


def test_the_config_keeps_its_mode(tmp_path):
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    config.chmod(0o644)
    ssh_alias.install(config, FOUND)
    assert config.stat().st_mode & 0o777 == 0o644
    assert [f.name for f in tmp_path.iterdir()] == ["config"]   # no temp left


def test_installing_again_replaces_the_block(tmp_path):
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    ssh_alias.install(config, FOUND)
    ssh_alias.install(config, {**FOUND, "Port": "40000"})
    text = config.read_text()
    assert text.count("Host omelet") == 1
    assert "  Port 40000\n" in text and "  Port 39022\n" not in text


def test_text_the_user_added_after_the_block_is_kept(tmp_path):
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    ssh_alias.install(config, FOUND)
    config.write_text(config.read_text() + "\nHost later\n  Port 1\n")
    ssh_alias.install(config, FOUND)
    assert "Host later\n  Port 1\n" in config.read_text()


def test_a_missing_ssh_config_is_created_private(tmp_path):
    config = tmp_path / ".ssh" / "config"
    ssh_alias.install(config, FOUND)
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.parent.stat().st_mode & 0o777 == 0o700


def test_a_symlinked_config_is_written_through(tmp_path):
    real = tmp_path / "dotfiles" / "ssh_config"
    real.parent.mkdir()
    real.write_text(USER_CONFIG)
    link = tmp_path / "config"
    link.symlink_to(real)
    ssh_alias.install(link, FOUND)
    assert link.is_symlink()
    assert "Host omelet" in real.read_text()


def test_remove_restores_the_file_it_found(tmp_path):
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    ssh_alias.install(config, FOUND)
    ssh_alias.remove(config)
    assert config.read_text() == USER_CONFIG


def test_remove_without_an_install_changes_nothing(tmp_path):
    config = tmp_path / "config"
    config.write_text(USER_CONFIG)
    ssh_alias.remove(config)
    ssh_alias.remove(tmp_path / "absent")
    assert config.read_text() == USER_CONFIG


# --- known_hosts ---

def test_the_guest_keys_become_entries_for_the_vms_address():
    assert ssh_alias.host_key_lines("[127.0.0.1]:39022", PUBKEYS + "garbage\n") == [
        "[127.0.0.1]:39022 ecdsa-sha2-nistp256 AAAAE2VjZHNh",
        "[127.0.0.1]:39022 ssh-ed25519 AAAAC3NzaC1l",
        "[127.0.0.1]:39022 ssh-rsa AAAAB3NzaC1y",
    ]


def test_stale_keys_are_replaced_and_other_hosts_kept(tmp_path):
    known = tmp_path / "known_hosts"
    known.write_text("github.com ssh-ed25519 GH\n[127.0.0.1]:39022 ssh-ed25519 OLD\n")
    guest = Guest()
    ssh_alias.trust(known, "[127.0.0.1]:39022", PUBKEYS, guest)
    text = known.read_text()
    assert "github.com ssh-ed25519 GH\n" in text
    assert "OLD" not in text
    assert "[127.0.0.1]:39022 ssh-ed25519 AAAAC3NzaC1l\n" in text
    # ssh-keygen, so hashed entries go too.
    assert ["ssh-keygen", "-R", "[127.0.0.1]:39022", "-f", str(known)] in guest.calls


def test_no_keys_from_the_guest_leaves_known_hosts_alone(tmp_path):
    known = tmp_path / "known_hosts"
    known.write_text("[127.0.0.1]:39022 ssh-ed25519 OLD\n")
    try:
        ssh_alias.trust(known, "[127.0.0.1]:39022", "", Guest())
    except ValueError:
        pass
    assert known.read_text() == "[127.0.0.1]:39022 ssh-ed25519 OLD\n"


# --- the provider ---

def test_the_lima_step_writes_the_host_and_the_keys(tmp_path):
    provider = _provider(tmp_path)
    assert provider.ssh_shortcut()() == "Connect with: ssh omelet"
    assert "  User you\n" in (tmp_path / ".ssh" / "config").read_text()
    assert "[127.0.0.1]:39022 ssh-rsa AAAAB3NzaC1y\n" in (
        tmp_path / ".ssh" / "known_hosts").read_text()
    assert provider.access().command == "ssh omelet"


def test_the_lima_step_never_fails_setup(tmp_path):
    message = _provider(tmp_path, written=False).ssh_shortcut()()
    assert message.startswith("Could not add the 'omelet' host")
    assert not (tmp_path / ".ssh" / "config").exists()


def test_starting_the_vm_refreshes_known_hosts(tmp_path):
    # A VM on a runtime from before ssh_deletekeys: false re-keys every boot.
    known = tmp_path / ".ssh" / "known_hosts"
    known.parent.mkdir()
    known.write_text("[127.0.0.1]:39022 ssh-ed25519 OLD\n")
    _provider(tmp_path).start()
    assert "OLD" not in known.read_text()
    assert "AAAAC3NzaC1l" in known.read_text()


def test_a_start_whose_keys_cannot_be_read_still_succeeds(tmp_path):
    _provider(tmp_path, written=False).start()


def test_destroy_takes_the_host_and_its_keys_away(tmp_path):
    import shutil

    class Lima(Guest):
        # `limactl delete` removes the instance directory, ssh.config included.
        def __call__(self, argv):
            if argv[:2] == ["limactl", "delete"]:
                shutil.rmtree(tmp_path / ".lima" / "omelet-vm")
            return super().__call__(argv)

    config = tmp_path / ".ssh" / "config"
    config.parent.mkdir()
    config.write_text(USER_CONFIG)
    provider = _provider(tmp_path, runner=Lima())
    provider.ssh_shortcut()()
    provider.destroy()
    assert config.read_text() == USER_CONFIG
    assert "39022" not in (tmp_path / ".ssh" / "known_hosts").read_text()


def test_the_step_follows_create_vm_on_lima_and_is_absent_on_wsl(tmp_path):
    kwargs = dict(cache_dir=tmp_path, template_dir=tmp_path, domain="x", exe_path="x")
    lima = [s.name for s in default_steps(_provider(tmp_path), **kwargs)]
    assert lima[lima.index("create_vm") + 1] == "ssh_alias"
    assert Wsl2Provider().ssh_shortcut() is None
