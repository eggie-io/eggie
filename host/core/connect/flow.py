"""`eggie connect`: Claude Code and Codex on this computer, connected to the
owner's instances and to the local VM. The desktop app will call it too."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...client import ApiError, ApiUnavailableError
from ...providers import claude_ssh, codex_app, ssh_alias
from .. import tls
from . import codex_remote, keys, ssh

log = logging.getLogger(__name__)

CLAUDE = "claude"
CODEX = "codex"
_AGENT_NAMES = {CLAUDE: "Claude Code", CODEX: "Codex"}
REMOTE_PATH = "/opt/eggie/projects"
LOCAL = "This computer"
RESTART = "Quit and reopen Claude and Codex to see the new connections."
APPROVAL_TIMEOUT = 600
APPROVAL_EVERY = 2
_DECIDED = ("approved", "denied", "expired")
_LABEL = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"{_LABEL}(\.{_LABEL})+")
_LOGIN_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


class _Failed(Exception):
    """An agent step that ended with its own sentence, not a crash."""


@dataclass(frozen=True)
class Line:
    target: str
    agent: str | None
    ok: bool
    text: str
    # Nothing done and nothing wrong: an instance skipped when all were asked for.
    neutral: bool = False


def ping(console_hostname: str) -> None:
    """Ask the instance to pull its approved keys now. Unauthenticated and
    best effort: it also pulls every 6 h on its own."""
    request = urllib.request.Request(f"https://{console_hostname}/api/ssh-keys/sync",
                                     data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10, context=tls.context()) as response:
            response.read()
    except Exception as e:
        log.warning("key sync ping to %s failed: %s", console_hostname, e)


def _run(argv):
    return subprocess.run(argv, capture_output=True)


def _alias(row: dict) -> str:
    return f"eggie-{row['name']}"


def _title(row: dict) -> str:
    return row.get("display_name") or row["name"]


class Connector:
    def __init__(self, client: Callable, provider, *, run=_run,
                 popen=codex_remote._popen, open_url=webbrowser.open,
                 port_free=codex_remote._port_free, ping=ping, sleep=time.sleep,
                 monotonic=time.monotonic, home: Path | None = None,
                 env: Mapping[str, str] = os.environ, computer_name: str,
                 say: Callable[[str], None] = lambda text: None):
        self._client_factory = client
        self._provider = provider
        self._run = run
        self._popen = popen
        self._open_url = open_url
        self._port_free = port_free
        self._ping = ping
        self._sleep = sleep
        self._monotonic = monotonic
        home = Path(home) if home else Path.home()
        self._ssh_config = home / ".ssh" / "config"
        self._key_dir = home / ".eggie" / "ssh"
        self._claude_dir = home / ".claude"
        self._codex_home = codex_app.default_codex_home(env, home)
        self._computer_name = computer_name
        self._say = say

    # -- entry points --------------------------------------------------------

    def connect(self, agents: set[str], instances: list[str] | None,
                local: bool) -> list[Line]:
        lines: list[Line] = []
        if instances != []:
            api, rows, failed = self._rows(instances)
            lines += failed
            named = instances is not None
            for r in rows:
                lines += self._guarded(
                    _title(r), lambda r=r: self._connect_instance(api, r, agents, named))
        if local:
            lines += self._guarded(LOCAL, lambda: self._connect_local(agents))
        if any(line.ok and line.agent for line in lines):
            lines.append(Line("", None, True, RESTART))
        return lines

    def remove(self, agents: set[str], instances: list[str] | None,
               local: bool) -> list[Line]:
        lines: list[Line] = []
        if instances != []:
            api, rows, failed = self._rows(instances)
            lines += failed
            if instances is None:
                listed = {_alias(r) for r in rows}
                rows = [r for r in rows if self._has_traces(_alias(r))]
            for r in rows:
                lines += self._guarded(_title(r), lambda r=r: self._remove_instance(api, r, agents))
            if instances is None and api is not None:
                for alias in self._unlisted_aliases(listed):
                    lines += self._guarded(alias, lambda a=alias: self._remove_unlisted(a, agents))
        if local:
            lines += self._guarded(LOCAL, lambda: self._remove_local(agents))
        return lines

    # -- instances -----------------------------------------------------------

    def _rows(self, wanted: list[str] | None):
        try:
            api = self._client_factory()
            rows = api.instances()
        except (ApiError, ApiUnavailableError) as e:
            return None, [], [Line("Kitchens", None, False,
                                   f"Start Eggie and sign in to connect your kitchens: {e}")]
        if wanted is None:
            return api, rows, []
        picked, missing = [], []
        for name in wanted:
            found = next((r for r in rows if name in (r.get("name"), r.get("display_name"))), None)
            if found is None:
                missing.append(Line(name, None, False, "No kitchen with this name on your account"))
            elif found not in picked:
                picked.append(found)
        return api, picked, missing

    def _guarded(self, target: str, step: Callable[[], list[Line]]) -> list[Line]:
        try:
            return step()
        except Exception as e:
            log.info("connect step for %s failed", target, exc_info=True)
            return [Line(target, None, False, str(e) or type(e).__name__)]

    def _connect_instance(self, api, row: dict, agents: set[str], named: bool) -> list[Line]:
        target, alias = _title(row), _alias(row)
        skipped = None
        if row.get("status") != "running":
            skipped = (f"Skipped: the kitchen is {row.get('status') or 'not running'}; "
                       "start it in the web app first")
        elif not row.get("is_owner"):
            skipped = "Skipped: only the kitchen's owner can connect this computer"
        if skipped:
            return [Line(target, None, not named, skipped, neutral=not named)]
        if not ssh_alias.valid_alias(alias):
            return [Line(target, None, False, f"'{alias}' can't be used as an SSH host name")]
        if not _HOSTNAME_RE.fullmatch(row.get("console_hostname") or ""):
            return [Line(target, None, False,
                         f"The kitchen's address '{row.get('console_hostname')}' "
                         "can't go in your SSH settings")]
        if not _LOGIN_RE.fullmatch(row.get("login_name") or ""):
            return [Line(target, None, False,
                         f"The kitchen's login '{row.get('login_name')}' "
                         "can't go in your SSH settings")]

        private, public = keys.ensure_key(self._key_dir, self._computer_name, self._run)
        existed = self._ssh_config.exists()
        had_block = ssh_alias.host_installed(self._ssh_config, alias)
        ssh_alias.install_host(self._ssh_config, alias, [
            ("HostName", row["console_hostname"]),
            ("User", row["login_name"]),
            ("IdentityFile", str(private)),
            ("IdentitiesOnly", "yes"),
            ("UserKnownHostsFile", str(self._key_dir / "known_hosts")),
            ("StrictHostKeyChecking", "accept-new"),
            ("ServerAliveInterval", "30"),
        ])
        access = ssh.Probe(False, "")
        try:
            access = ssh.reach(self._run, alias)
            if not access.ok:
                access = self._gain_access(api, row, public)
        finally:
            if not access.ok and not had_block:
                self._forget_block(alias, existed)
        if not access.ok:
            return [Line(target, None, False, access.reason)]

        lines = []
        if CLAUDE in agents:
            lines += self._agent(target, CLAUDE, lambda: self._claude(alias, target))
        if CODEX in agents:
            lines += self._agent(target, CODEX,
                                 lambda: self._codex(alias, f"Eggie · {target}"))
        lines.append(self._check(target, alias))
        return lines

    def _check(self, target: str, alias: str) -> Line:
        check = ssh.reach(self._run, alias)
        return Line(target, None, check.ok,
                    f"ssh {alias} works" if check.ok else f"ssh {alias} failed: {check.reason}")

    def _forget_block(self, alias: str, existed: bool) -> None:
        ssh_alias.remove_host(self._ssh_config, alias)
        if not existed and self._ssh_config.exists() and not self._ssh_config.read_text():
            self._ssh_config.unlink()

    def _gain_access(self, api, row: dict, public: str) -> ssh.Probe:
        target = _title(row)
        try:
            request = api.request_instance_key(row["id"], public, self._computer_name)
        except ApiError as e:
            if e.api_code != "ssh_key_installed":
                return ssh.Probe(False, e.message)
        else:
            url, fingerprint = request.get("approve_url") or "", self._fingerprint()
            if request.get("fingerprint") != fingerprint:
                return ssh.Probe(False, "The key request is not for this computer's key; "
                                        "nothing was opened.")
            if not url.startswith("https://"):
                return ssh.Probe(False, "The approval page is not an https:// address; "
                                        "nothing was opened.")
            self._say(f"Approve this computer for {target} in your browser — "
                      f"check the page shows {fingerprint}: {url}")
            self._open_url(url)
            status = self._await_decision(api, row["id"], request["id"])
            if status == "denied":
                return ssh.Probe(False, "The request was denied in the browser; nothing was connected.")
            if status != "approved":
                return ssh.Probe(False, "The request expired before it was approved; "
                                        "run this again.")
        self._ping(row["console_hostname"])
        reached = ssh.wait_reachable(self._run, _alias(row), sleep=self._sleep,
                                     monotonic=self._monotonic)
        if reached.ok:
            return reached
        return ssh.Probe(False, f"The kitchen did not let this computer in: "
                                f"{reached.reason.rstrip('.')}. "
                                "Run eggie connect again in a minute.")

    def _await_decision(self, api, instance_id: str, request_id: str) -> str:
        deadline = self._monotonic() + APPROVAL_TIMEOUT
        last_error = None
        while self._monotonic() < deadline:
            self._sleep(APPROVAL_EVERY)
            try:
                status = api.instance_key_request(instance_id, request_id).get("status")
            except (ApiUnavailableError, ApiError) as e:
                if isinstance(e, ApiError) and e.status < 500:
                    raise
                last_error = e
                continue
            last_error = None
            if status in _DECIDED:
                return status
        if last_error is not None:
            raise last_error
        return "expired"

    def _remove_agents(self, target: str, alias: str, agents: set[str]) -> tuple[list[Line], bool]:
        """The agents' entries, and whether the ssh block may go too."""
        lines = []
        if CLAUDE in agents:
            lines += self._agent(target, CLAUDE, lambda: self._unclaude(alias))
        if CODEX in agents:
            lines += self._agent(target, CODEX, lambda: self._uncodex(alias))
        return lines, all(line.ok for line in lines) and not self._agent_left(alias)

    def _remove_instance(self, api, row: dict, agents: set[str]) -> list[Line]:
        target, alias = _title(row), _alias(row)
        lines, block_may_go = self._remove_agents(target, alias, agents)
        if not block_may_go:
            return lines
        # The ssh block goes last: it is what makes a re-run of --remove retry the service.
        fingerprint, ok = self._fingerprint(), True
        if fingerprint is None:
            text = "This computer has no Eggie key to remove"
        else:
            try:
                api.remove_instance_key(row["id"], fingerprint)
            except ApiError as e:
                if e.api_code != "ssh_key_not_found":
                    lines.append(Line(target, None, False, e.message))
                    return lines
                # Often a key this computer sent from an earlier Eggie install.
                ok, text = False, (f"Eggie could not remove this computer's key from {target}; "
                                   "revoke it in the web app (the kitchen's SSH keys)")
            else:
                self._ping(row["console_hostname"])
                text = "This computer's key was removed from the kitchen"
        ssh_alias.remove_host(self._ssh_config, alias)
        lines.append(Line(target, None, ok, text))
        return lines

    def _unlisted_aliases(self, listed: set[str]) -> list[str]:
        found = (ssh_alias.installed_hosts(self._ssh_config)
                 + claude_ssh.entry_ids(self._claude_dir) + codex_app.aliases(self._codex_home))
        return sorted({a for a in found if a.startswith("eggie-") and ssh_alias.valid_alias(a)}
                      - listed)

    def _remove_unlisted(self, alias: str, agents: set[str]) -> list[Line]:
        lines, block_may_go = self._remove_agents(alias, alias, agents)
        if block_may_go:
            ssh_alias.remove_host(self._ssh_config, alias)
        lines.append(Line(alias, None, True,
                          "Not on your account any more, so only this computer's files "
                          "were changed; no kitchen was asked to remove a key"))
        return lines

    def _has_traces(self, alias: str) -> bool:
        return ssh_alias.host_installed(self._ssh_config, alias) or self._agent_left(alias)

    def _agent_left(self, alias: str) -> bool:
        return (claude_ssh.entry_installed(self._claude_dir, alias)
                or codex_app.has(self._codex_home, alias))

    def _fingerprint(self) -> str | None:
        public = self._key_dir / "eggie_ed25519.pub"
        if not public.exists():
            return None
        result = ssh.spawn(self._run, ["ssh-keygen", "-lf", str(public)])
        fields = result.stdout.split()
        if not result.ok or len(fields) < 2:
            raise RuntimeError(result.stderr or f"ssh-keygen exited {result.returncode}")
        return fields[1]

    # -- the local VM --------------------------------------------------------

    def _connect_local(self, agents: set[str]) -> list[Line]:
        alias = self._provider.local_ssh_alias
        lines = []
        if CLAUDE in agents:
            if alias:
                lines += self._agent(LOCAL, CLAUDE,
                                     lambda: self._provider.connect_agent("claude-code"))
            else:
                lines.append(Line(LOCAL, _AGENT_NAMES[CLAUDE], True,
                                  "On Windows, pick eggie-vm under WSL in Claude Code."))
        if CODEX in agents:
            if alias:
                lines += self._agent(LOCAL, CODEX,
                                     lambda: self._codex(alias, f"Eggie · {LOCAL}"))
            else:
                lines += self._agent(LOCAL, CODEX, lambda: self._provider.connect_agent("codex"))
        if alias:
            lines.append(self._check(LOCAL, alias))
        return lines

    def _remove_local(self, agents: set[str]) -> list[Line]:
        alias = self._provider.local_ssh_alias
        if not alias:
            return [Line(LOCAL, None, True,
                         "Nothing to undo on Windows: the agents reach the VM through WSL.")]
        # `Host eggie` stays: setup owns it and editors use it too.
        lines = []
        if CLAUDE in agents:
            lines += self._agent(LOCAL, CLAUDE, lambda: self._unclaude(claude_ssh.ENTRY_ID))
        if CODEX in agents:
            lines += self._agent(LOCAL, CODEX, lambda: self._uncodex(alias))
        return lines

    # -- one agent -----------------------------------------------------------

    def _agent(self, target: str, agent: str, step: Callable[[], str]) -> list[Line]:
        name = _AGENT_NAMES[agent]
        try:
            return [Line(target, name, True, step())]
        except _Failed as e:
            return [Line(target, name, False, str(e))]
        except Exception as e:
            log.info("%s step for %s failed", name, target, exc_info=True)
            return [Line(target, name, False, str(e) or type(e).__name__)]

    def _claude(self, alias: str, target: str) -> str:
        entry = claude_ssh.entry_for(alias, target)
        claude_ssh.install_entry(self._claude_dir, entry)
        return f"listed as “{entry['name']}”"

    def _codex(self, alias: str, label: str) -> str:
        had = codex_app.has(self._codex_home, alias)
        codex_app.add(self._codex_home, alias, REMOTE_PATH, label)
        try:
            return self._codex_ready(alias, label)
        except Exception:
            if not had:
                codex_app.remove(self._codex_home, alias)
            raise

    def _codex_ready(self, alias: str, label: str) -> str:
        installed = codex_remote.ensure_installed(self._run, alias)
        if not installed.ok:
            raise _Failed(f"Codex could not be installed there: {installed.reason}")
        if not codex_remote.signed_in(self._run, alias):
            self._say(f"Sign in to Codex for {label} in your browser…")
            signed = codex_remote.sign_in(alias, popen=self._popen, open_url=self._open_url,
                                          port_free=self._port_free)
            if not signed.ok:
                raise _Failed(f"Codex sign-in did not finish: {signed.reason}")
        return f"listed as “{label}”, signed in"

    def _unclaude(self, entry_id: str) -> str:
        if not claude_ssh.entry_installed(self._claude_dir, entry_id):
            return "was not connected"
        claude_ssh.remove_entry(self._claude_dir, entry_id)
        return "removed"

    def _uncodex(self, alias: str) -> str:
        if not codex_app.remove(self._codex_home, alias):
            return "was not connected"
        return "removed. Remove it in Codex Settings → Connections too."
