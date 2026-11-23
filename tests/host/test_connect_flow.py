import json
import subprocess

import pytest

from host.client import ApiError, ApiUnavailableError
from host.core.connect import flow
from host.providers import claude_ssh, codex_app, ssh_alias

RESTART = "Quit and reopen Claude and Codex to see the new connections."


def row(name, *, status="running", is_owner=True, display_name=None):
    return {"id": f"id-{name}", "name": name, "display_name": display_name or name.title(),
            "login_name": "ihor", "console_hostname": f"{name}.eggie.app",
            "status": status, "is_owner": is_owner}


class FakeClient:
    def __init__(self, rows, polls=None, request_error=None):
        self.rows = rows
        self.polls = polls or {}
        self.request_error = request_error or {}
        self.remove_error = None
        self.calls = []
        self.fingerprint = "SHA256:abc+/="
        self.approve_base = "https://app.eggie.io"

    def instances(self):
        return self.rows

    def request_instance_key(self, instance_id, public_key, computer_name):
        self.calls.append(("request", instance_id, public_key, computer_name))
        if instance_id in self.request_error:
            raise self.request_error[instance_id]
        return {"id": f"req-{instance_id}", "status": "pending", "fingerprint": self.fingerprint,
                "approve_url": f"{self.approve_base}/kitchens/connect?request=req-{instance_id}"}

    def instance_key_request(self, instance_id, request_id):
        self.calls.append(("poll", instance_id, request_id))
        answer = self.polls[instance_id].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"status": answer}

    def remove_instance_key(self, instance_id, fingerprint):
        self.calls.append(("remove", instance_id, fingerprint))
        if self.remove_error:
            raise self.remove_error


class FakeSsh:
    """ssh and ssh-keygen as the flow runs them. An alias answers once its
    kitchen has been pinged (it pulled the approved key)."""

    def __init__(self):
        self.calls = []
        self.authorized = set()
        self.codex = set()
        self.signed_in = set()

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == "ssh-keygen":
            if "-lf" in argv:
                return self._done(argv, 0, b"256 SHA256:abc+/= Ihor's Mac (ED25519)\n")
            private = argv[argv.index("-f") + 1]
            open(private, "w").write("private")
            open(private + ".pub", "w").write("ssh-ed25519 AAAA Ihor's Mac\n")
            return self._done(argv, 0)
        alias = argv[argv.index("-T") + 1]
        if alias not in self.authorized:
            return self._done(argv, 255, err=b"Permission denied (publickey).\n")
        script = argv[-1]
        if "agent.json" in script:
            return self._done(argv, 0, b"curl -fsSL https://chatgpt.com/codex/install.sh | sh\n")
        if "install.sh" in script:
            self.codex.add(alias)
            return self._done(argv, 0)
        if "command -v codex" in script:
            return self._done(argv, 0 if alias in self.codex else 1)
        if "codex login status" in script:
            return self._done(argv, 0 if alias in self.signed_in else 1)
        return self._done(argv, 0)

    @staticmethod
    def _done(argv, code, out=b"", err=b""):
        return subprocess.CompletedProcess(argv, code, out, err)


class FakeLogin:
    """`ssh -L 1455:... <alias> codex login`: prints the link, then finishes."""

    def __init__(self, ssh):
        self.ssh = ssh
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        alias = argv[-2]
        ssh = self.ssh

        class Proc:
            stdout = ["Starting local login server on http://localhost:1455.\n",
                      "https://auth.openai.com/oauth/authorize?client_id=x\n"]
            returncode = None

            def wait(self, timeout=None):
                ssh.signed_in.add(alias)
                self.returncode = 0
                return 0

            def poll(self):
                return self.returncode

            def kill(self):
                pass

        return Proc()


class FakeProvider:
    def __init__(self, local_ssh_alias=None):
        self.local_ssh_alias = local_ssh_alias
        self.connected = []

    def connect_agent(self, agent_id):
        self.connected.append(agent_id)
        return f"{agent_id} connected."


class World:
    def __init__(self, home, client, provider=None):
        self.home = home
        self.client = client
        self.provider = provider or FakeProvider()
        self.ssh = FakeSsh()
        self.login = FakeLogin(self.ssh)
        self.opened = []
        self.pinged = []
        self.said = []
        self.factory_calls = 0
        self.now = 0.0
        self.port_free = True
        self.ping_lets_in = True

    def factory(self):
        self.factory_calls += 1
        if isinstance(self.client, Exception):
            raise self.client
        return self.client

    def ping(self, host):
        self.pinged.append(host)
        if self.ping_lets_in:
            self.ssh.authorized.add("eggie-" + host.split(".")[0])

    def sleep(self, seconds):
        self.now += seconds

    def connector(self):
        return flow.Connector(
            self.factory, self.provider, run=self.ssh, popen=self.login,
            open_url=self.opened.append, port_free=lambda port: self.port_free, ping=self.ping,
            sleep=self.sleep, monotonic=lambda: self.now, home=self.home, env={},
            computer_name="Ihor's Mac", say=self.said.append)

    @property
    def ssh_config(self):
        return self.home / ".ssh" / "config"

    @property
    def claude(self):
        return self.home / ".claude"

    @property
    def codex(self):
        return self.home / ".codex"

    def files(self):
        return {p: p.read_bytes() for p in (
            self.ssh_config, claude_ssh.settings_path(self.claude),
            codex_app.config_path(self.codex))}


BOTH = {"claude", "codex"}


def test_new_instance_requests_approves_pings_and_writes_all_three(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": ["pending", "approved"]})
    w = World(tmp_path, client)

    lines = w.connector().connect(BOTH, ["shop"], local=False)

    assert all(line.ok for line in lines), lines
    assert lines[-1].text == RESTART
    assert client.calls[0] == ("request", "id-shop", "ssh-ed25519 AAAA Ihor's Mac", "Ihor's Mac")
    approve = "https://app.eggie.io/kitchens/connect?request=req-id-shop"
    assert w.opened[0] == approve
    assert any(approve in s and "Shop" in s and "SHA256:abc+/=" in s for s in w.said)
    assert w.pinged == ["shop.eggie.app"]

    config = w.ssh_config.read_text()
    key_dir = tmp_path / ".eggie" / "ssh"
    for option in ("Host eggie-shop", "HostName shop.eggie.app", "User ihor",
                   f"IdentityFile {key_dir / 'eggie_ed25519'}", "IdentitiesOnly yes",
                   f"UserKnownHostsFile {key_dir / 'known_hosts'}",
                   "StrictHostKeyChecking accept-new", "ServerAliveInterval 30"):
        assert option in config
    settings = json.loads(claude_ssh.settings_path(w.claude).read_text())
    assert settings["sshConfigs"] == [claude_ssh.entry_for("eggie-shop", "Shop")]
    codex = json.loads(codex_app.config_path(w.codex).read_text())
    assert codex["remoteConnections"] == [{
        "sshAlias": "eggie-shop",
        "projects": [{"remotePath": "/opt/eggie/projects", "label": "Eggie · Shop"}]}]
    # Codex was missing on the kitchen, then signed in through this computer's browser.
    assert "eggie-shop" in w.ssh.codex and "eggie-shop" in w.ssh.signed_in
    assert w.opened[1].startswith("https://auth.openai.com/")


def test_second_run_changes_nothing(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": ["approved"]})
    w = World(tmp_path, client)
    w.connector().connect(BOTH, None, local=False)
    before = w.files()
    client.calls.clear()
    w.opened.clear()
    w.login.calls.clear()

    lines = w.connector().connect(BOTH, None, local=False)

    assert all(line.ok for line in lines), lines
    assert client.calls == []
    assert w.opened == []
    assert w.login.calls == []
    assert w.files() == before


@pytest.mark.parametrize("named", [None, ["cold", "theirs"]])
def test_stopped_or_not_owned_instance_is_skipped_with_a_reason(tmp_path, named):
    client = FakeClient([row("cold", status="stopped"), row("theirs", is_owner=False)])
    w = World(tmp_path, client)

    lines = w.connector().connect(BOTH, named, local=False)

    # Asked for by name it is a failure; swept up with every instance it is not.
    ok = named is None
    assert [(line.target, line.ok, line.neutral) for line in lines] == [
        ("Cold", ok, ok), ("Theirs", ok, ok)]
    assert "stopped" in lines[0].text
    assert "owner" in lines[1].text
    assert client.calls == []
    assert not w.ssh_config.exists()


@pytest.mark.parametrize("outcome, polls", [
    ("denied", ["pending", "denied"]),
    ("expired", ["expired"]),
    ("expired", ["pending"] * 400),  # never decided within the ten minutes
])
def test_denied_request_writes_nothing_and_next_instance_still_runs(tmp_path, outcome, polls):
    client = FakeClient([row("first"), row("second")],
                        polls={"id-first": polls, "id-second": ["approved"]})
    w = World(tmp_path, client)
    w.ssh_config.parent.mkdir()
    w.ssh_config.write_text("Host work\n  HostName work.example\n")

    lines = w.connector().connect(BOTH, None, local=False)

    first = [line for line in lines if line.target == "First"]
    assert len(first) == 1 and not first[0].ok and outcome in first[0].text
    assert all(line.ok for line in lines if line.target == "Second")
    config = w.ssh_config.read_text()
    assert "eggie-first" not in config and "Host eggie-second" in config
    assert config.endswith("Host work\n  HostName work.example\n")
    ids = [e["id"] for e in json.loads(claude_ssh.settings_path(w.claude).read_text())["sshConfigs"]]
    assert ids == ["eggie-second"]
    assert not codex_app.has(w.codex, "eggie-first") and codex_app.has(w.codex, "eggie-second")


def test_already_installed_key_goes_straight_to_ping(tmp_path):
    client = FakeClient([row("shop")], request_error={
        "id-shop": ApiError("ssh_key_installed", "already there", 409)})
    w = World(tmp_path, client)

    lines = w.connector().connect({"claude"}, ["shop"], local=False)

    assert all(line.ok for line in lines), lines
    assert w.opened == []
    assert w.pinged == ["shop.eggie.app"]
    assert not any(call[0] == "poll" for call in client.calls)
    assert claude_ssh.entry_installed(w.claude, "eggie-shop")


def test_remove_drops_entries_and_asks_service_to_remove_this_key(tmp_path):
    client = FakeClient([row("shop"), row("other")],
                        polls={"id-shop": ["approved"], "id-other": ["approved"]})
    w = World(tmp_path, client)
    w.connector().connect(BOTH, None, local=False)
    w.pinged.clear()

    lines = w.connector().remove(BOTH, ["shop"], local=False)

    assert all(line.ok for line in lines), lines
    assert ("remove", "id-shop", "SHA256:abc+/=") in client.calls
    assert w.pinged == ["shop.eggie.app"]
    assert not ssh_alias.host_installed(w.ssh_config, "eggie-shop")
    assert not claude_ssh.entry_installed(w.claude, "eggie-shop")
    assert not codex_app.has(w.codex, "eggie-shop")
    assert any("Codex Settings → Connections" in line.text for line in lines
               if line.agent == "Codex")
    # The other instance keeps everything.
    assert ssh_alias.host_installed(w.ssh_config, "eggie-other")
    assert claude_ssh.entry_installed(w.claude, "eggie-other")
    assert codex_app.has(w.codex, "eggie-other")


def test_local_wsl_connects_codex_and_explains_claude(tmp_path):
    w = World(tmp_path, FakeClient([]))

    lines = w.connector().connect(BOTH, [], local=True)

    assert w.provider.connected == ["codex"]
    assert w.factory_calls == 0
    claude = [line for line in lines if line.agent == "Claude Code"]
    assert claude[0].text == "On Windows, pick eggie-vm under WSL in Claude Code."
    assert all(line.ok for line in lines)


def test_local_lima_adds_codex_on_the_vm_alias_and_remove_keeps_its_host(tmp_path):
    w = World(tmp_path, FakeClient([]), FakeProvider(local_ssh_alias="eggie"))
    ssh_alias.install_host(w.ssh_config, "eggie", [("HostName", "127.0.0.1")])
    w.ssh.authorized.add("eggie")
    w.ssh.codex.add("eggie")

    lines = w.connector().connect(BOTH, [], local=True)

    assert all(line.ok for line in lines), lines
    assert w.provider.connected == ["claude-code"]
    assert codex_app.has(w.codex, "eggie")
    assert "eggie" in w.ssh.signed_in

    claude_ssh.install(w.claude)
    w.connector().remove(BOTH, [], local=True)
    assert not codex_app.has(w.codex, "eggie")
    assert not claude_ssh.installed(w.claude)
    assert ssh_alias.host_installed(w.ssh_config, "eggie")


@pytest.mark.parametrize("error", [
    ApiUnavailableError("could not reach the Eggie API"),
    ApiError("not_signed_in", "sign in to Eggie first", 409),
])
def test_without_the_api_this_computer_still_connects(tmp_path, error):
    w = World(tmp_path, error)

    lines = w.connector().connect({"codex"}, None, local=True)

    assert not lines[0].ok
    assert lines[0].text.startswith("Start Eggie and sign in to connect your kitchens: ")
    assert w.provider.connected == ["codex"]


def test_codex_connection_is_taken_back_when_its_sign_in_cannot_start(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": ["approved"]})
    w = World(tmp_path, client)
    w.port_free = False

    lines = w.connector().connect(BOTH, ["shop"], local=False)

    codex = [line for line in lines if line.agent == "Codex"]
    assert len(codex) == 1 and not codex[0].ok and "1455" in codex[0].text
    assert not codex_app.has(w.codex, "eggie-shop")
    assert claude_ssh.entry_installed(w.claude, "eggie-shop")


def test_failed_key_removal_keeps_the_host_so_remove_retries(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": ["approved"]})
    w = World(tmp_path, client)
    w.connector().connect(BOTH, None, local=False)
    client.remove_error = ApiUnavailableError("could not reach the Eggie API")

    lines = w.connector().remove(BOTH, None, local=False)

    assert not all(line.ok for line in lines)
    assert ssh_alias.host_installed(w.ssh_config, "eggie-shop")

    client.remove_error = None
    client.calls.clear()
    lines = w.connector().remove(BOTH, None, local=False)

    assert all(line.ok for line in lines), lines
    assert client.calls == [("remove", "id-shop", "SHA256:abc+/=")]
    assert not ssh_alias.host_installed(w.ssh_config, "eggie-shop")


def test_approval_poll_rides_out_a_transient_error(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": [
        ApiUnavailableError("could not reach the Eggie API"),
        ApiError("upstream_error", "the service failed", 502),
        "approved"]})
    w = World(tmp_path, client)

    lines = w.connector().connect({"claude"}, ["shop"], local=False)

    assert all(line.ok for line in lines), lines
    assert claude_ssh.entry_installed(w.claude, "eggie-shop")


def test_approved_but_unreachable_says_to_try_again(tmp_path):
    client = FakeClient([row("shop")], polls={"id-shop": ["approved"]})
    w = World(tmp_path, client)
    w.ping_lets_in = False

    lines = w.connector().connect(BOTH, ["shop"], local=False)

    assert len(lines) == 1 and not lines[0].ok
    assert lines[0].text.endswith("Run eggie connect again in a minute.")
    assert not w.ssh_config.exists()


@pytest.mark.parametrize("change", [
    {"fingerprint": "SHA256:someone-else"},
    {"approve_base": "http://app.eggie.io"},
])
def test_request_not_for_this_key_or_not_https_is_refused_unopened(tmp_path, change):
    client = FakeClient([row("shop")], polls={"id-shop": ["approved"]})
    for name, value in change.items():
        setattr(client, name, value)
    w = World(tmp_path, client)

    lines = w.connector().connect(BOTH, ["shop"], local=False)

    assert len(lines) == 1 and not lines[0].ok
    assert w.opened == []
    assert not any(call[0] == "poll" for call in client.calls)
    assert not w.ssh_config.exists()


@pytest.mark.parametrize("field, value", [
    ("console_hostname", "shop.eggie.app\nProxyCommand x"),
    ("console_hostname", "shop"),
    ("console_hostname", "-shop.eggie.app"),
    ("console_hostname", "Shop.eggie.app"),
    ("login_name", "ihor x"),
    ("login_name", "1ihor"),
    ("login_name", "i" * 33),
])
def test_a_hostname_or_login_ssh_config_cannot_hold_writes_nothing(tmp_path, field, value):
    client = FakeClient([{**row("shop"), field: value}], polls={"id-shop": ["approved"]})
    w = World(tmp_path, client)

    lines = w.connector().connect(BOTH, ["shop"], local=False)

    assert len(lines) == 1 and not lines[0].ok
    assert client.calls == []
    assert not w.ssh_config.exists()
