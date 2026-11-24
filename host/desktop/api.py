"""The only object JavaScript can reach.

Everything here is a security boundary, so the surface is a fixed list of
method names taking scalars. No path, command or URL arriving from JS is
passed to a shell, and nothing is eval'd. Slow work goes through
JobRegistry rather than blocking the thread that owns the window.

Thin on purpose: the decisions live in view.py, which is testable without a
window.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from host.core import constants
from host.client import ApiError, ApiUnavailableError
from host.core.cloud.account import NotSignedIn
from host.core.cloud.client import web_app_url
from host.core.connect.flow import instance_alias
from host.core.events import EventSender, error_code
from host.core.provider import ProviderError
from host.core.status import probe
from host.providers import claude_ssh, codex_app, ssh_alias

from . import agents
from .jobs import JobBusy, JobRegistry
from .lifecycle import turn_on_autostart_once
from .settings import AGENTS_DONE, Settings, done_flag
from .telemetry import app_error, who
from .vm_sign_in import sign_in_vm
from .view import (agent_cards, checkout_url, checkout_wait, cloud_card, connect_targets,
                   default_kitchen_name, from_price, home_view, local_card, new_kitchen,
                   progress_event, region_tiles, route_for, rows_for, screen_for, setup_rows,
                   size_tiles, terminal_event)

log = logging.getLogger(__name__)

_FROZEN = object()
# Past WSL's own 30 s poweroff wait; a hung wsl.exe must not keep Quit from quitting.
QUIT_STOP_TIMEOUT = 60.0


class DesktopApi:
    def __init__(self, provider, state, *, push,
                 probe_fn=probe, steps_factory=None,
                 client_factory=None, install_dir_factory=None, local_url=None,
                 app_update_fn=None, quit_app=None,
                 settings=None, autostart_exe=_FROZEN,
                 stop_timeout=QUIT_STOP_TIMEOUT, log_path=None, desktop=None,
                 events: EventSender | None = None, clock=time.monotonic,
                 cloud=None, sleep=time.sleep, open_url=None,
                 ui_dir=None, home=None, env=None, connector_factory=None):
        self._provider = provider
        self._events = events or EventSender(None, {})
        self._clock = clock
        self._desktop = desktop
        self._state = state
        self._probe = probe_fn
        self._steps_factory = steps_factory
        self._client_factory = client_factory or self._default_client_factory
        self._install_dir_factory = install_dir_factory or self._default_install_dir
        self._push = push
        self.jobs = JobRegistry(push, on_crash=self._report_crash)
        self._local_url = local_url or (lambda: None)
        # (CloudAccount, CloudClient); None only for tests that need no account.
        self._account, self._cloud = cloud if cloud else (None, None)
        self._sleep = sleep
        # Looked up per call: __main__ wraps webbrowser.open to http(s) only.
        self._open_url = open_url or (lambda url: webbrowser.open(url))
        self._kitchens: list[dict] | None = None
        self._kitchens_error = ""
        self._just_signed_in = False
        self._signup_shown = False
        self._sign_in_url = ""
        # What 1d last offered, so start_checkout accepts only those picks.
        self._picks: dict[str, set[str]] = {}
        self._checkout: dict | None = None
        self._poller: threading.Thread | None = None
        self._declared = False
        self._app_update_fn = app_update_fn or self._default_app_update
        self._quit_app = quit_app or self._default_quit
        self._app_release = None
        self._app_check = None
        self._settings = settings or Settings(self._install_dir_factory().parent / "settings.json")
        if autostart_exe is _FROZEN:
            # A source checkout has no stable executable to register at login.
            autostart_exe = sys.executable if getattr(sys, "frozen", False) else None
        self._autostart_exe = autostart_exe
        self._quitting = False
        self._quit_thread = None
        self._runtime_update_tried = False
        self._runtime_update_error = ""
        self._stop_timeout = stop_timeout
        self._log_path = log_path
        self._ui_dir = Path(ui_dir) if ui_dir else Path(__file__).resolve().parent / "ui"
        self._manifests: list | None = None
        # Where the connect steps write: the same files Connector uses.
        self._home = Path(home) if home else Path.home()
        self._env = os.environ if env is None else env
        self._ssh_config = self._home / ".ssh" / "config"
        self._claude_dir = self._home / ".claude"
        self._codex_home = codex_app.default_codex_home(self._env, self._home)
        self._connector_factory = connector_factory or self._default_connector
        self._connect_cancel = threading.Event()
        self._connect_url = ""

    @staticmethod
    def _default_client_factory(provider):
        from host.client import ApiClient

        return ApiClient.for_provider(provider)

    @staticmethod
    def _default_install_dir():
        from host.providers import default_install_dir

        return default_install_dir()

    def _default_connector(self, **kw):
        from host.core.connect.flow import Connector

        def cloud():
            if self._cloud is None:
                raise NotSignedIn()
            return self._cloud

        return Connector(cloud, self._provider, home=self._home, env=self._env,
                         computer_name=self._provider.computer_name(), **kw)

    def _default_app_update(self):
        from host.core import app_update
        return app_update.check(current=constants.APP_VERSION,
                                asset_name=self._provider.installer_asset)

    def _report_crash(self, kind: str, exc: BaseException) -> None:
        app_error(self._events, f"{kind}/{error_code(exc)}", exc, **who())

    @staticmethod
    def _default_quit():
        import webview
        webview.windows[0].destroy()

    # --- what to draw -------------------------------------------------

    def home(self) -> dict:
        """Not idempotent: the first call after a resume consumes the flag.

        `resumed` is one-shot. RunOnce relaunches the app with `--resume`
        after a restart, and the first screen consumes that to continue the
        install. Left set, every later refresh() would start the install
        again -- and since most steps are always_run, that is a full
        re-install loop with no way back to Home.
        """
        readiness = self._probe(self._provider)
        if readiness.vm_reachable and not self._declared:
            from host.core.runtime_update import declare_supported
            try:
                self._declared = declare_supported(self._provider)
            except Exception:
                # A hung VM surfaces through the probe on the next refresh.
                pass
        route, state = route_for(readiness)
        resumed = getattr(self, "resumed", False)
        self.resumed = False
        signed_in = self._account is None or self._account.signed_in
        screen = screen_for(signed_in=signed_in,
                            pending_sign_in=bool(self._account and self._account.pending),
                            kitchens=[] if self._account is None else self._kitchens,
                            kitchens_error=self._kitchens_error,
                            vm_exists=readiness.vm_exists,
                            route=route, state=state, resumed=resumed,
                            just_signed_in=self._just_signed_in,
                            update_tried=self._runtime_update_tried,
                            update_error=self._runtime_update_error,
                            problem=readiness.problem)
        local = local_card(route, state)
        if signed_in and screen.action != "load_kitchens":
            # Held until the kitchen list is in, or the first load would eat it.
            self._just_signed_in = False
        return {
            "screen": screen.name,
            "action": screen.action,
            "message": screen.message,
            "signed_in": signed_in,
            "email": self._account.email if self._account else "",
            "home": home_view(self._kitchens, self._kitchens_error, local,
                              readiness.vm_exists, agents=self._agent_chips(local)),
            "kitchen_id": screen.message if screen.name == "connect" else "",
            "route": route,
            "state": state,
            "app_version": constants.APP_VERSION,
            "runtime_version": readiness.runtime_version or "",
            "problem": readiness.problem,
            # The install screen's notice explains why the window reopened
            # by itself after the restart.
            "resumed": resumed,
            "app_update": self._app_release.version if self._app_release else "",
        }

    # --- coding agents ------------------------------------------------

    def _agent_manifests(self) -> list:
        if self._manifests is None:
            try:
                self._manifests = agents.load(self._ui_dir)
            except (OSError, ValueError):
                # The shipped guides are checked by a test; a broken one hides agents, not Home.
                log.exception("could not load the agent guides")
                self._manifests = []
        return self._manifests

    def _agent_chips(self, local: str) -> dict[str, list[str]]:
        """Each Home card's connected agents' names. File reads, plus one `wsl -l -v`
        on Windows for this computer's card; never ssh: Home redraws often."""
        ids = [k["id"] for k in self._kitchens or []] + (["local"] if local != "none" else [])
        chips = {}
        for kitchen_id in ids:
            connected = self._connected_agents(kitchen_id)
            chips[kitchen_id] = [m.name for m in self._agent_manifests() if connected[m.id]]
        return chips

    def _agent_target(self, kitchen_id: str) -> tuple[str, str | None]:
        """("local" or "cloud", the ssh host agents reach it by)."""
        if kitchen_id == "local":
            return "local", getattr(self._provider, "local_ssh_alias", None)
        row = next((k for k in self._kitchens or [] if k.get("id") == kitchen_id), None)
        alias = instance_alias(row) if row and row.get("name") else None
        return "cloud", alias if alias and ssh_alias.valid_alias(alias) else None

    def _connected_agents(self, kitchen_id: str) -> dict[str, bool]:
        """From what the connect steps leave behind: the ssh block plus the agent's entry
        (or Done); on Windows' own VM the provider's answer (or Done)."""
        kind, alias = self._agent_target(kitchen_id)
        done = self._settings.members(AGENTS_DONE)
        block = bool(alias) and ssh_alias.host_installed(self._ssh_config, alias)
        connected = {}
        for m in self._agent_manifests():
            pressed = done_flag(m.id, kitchen_id) in done
            if alias:
                if m.connect == "claude":
                    own = claude_ssh.entry_installed(self._claude_dir, alias)
                elif m.connect == "codex":
                    own = codex_app.has(self._codex_home, alias)
                else:
                    own = pressed
                connected[m.id] = block and own
            elif kind == "cloud":
                # A kitchen whose name can't be an ssh host was never connected.
                connected[m.id] = False
            elif m.id in getattr(self._provider, "agent_connections", ()):
                connected[m.id] = self._added(m.id)
            else:
                connected[m.id] = pressed
        return connected

    # --- Connect an agent (3a–3d) ----------------------------------------

    def _manifest(self, agent_id: object):
        return next((m for m in self._agent_manifests() if m.id == agent_id), None)

    def _targets(self) -> list[dict]:
        # Fresh each time: a kitchen that just finished setting up must be pickable.
        # A failed load keeps the last list.
        self.load_kitchens()
        readiness = self._probe(self._provider)
        return connect_targets(self._kitchens, local_card(*route_for(readiness))
                               if readiness.vm_exists else "none")

    def _owned(self, kitchen_id: object) -> bool:
        return kitchen_id == "local" or any(
            k.get("id") == kitchen_id and k.get("is_owner") for k in self._kitchens or [])

    def _guide(self, manifest, kitchen: dict) -> list[dict]:
        kind, alias = self._agent_target(kitchen["id"])
        return agents.render_steps(manifest, self._provider.guide_os, kind, alias or "",
                                   kitchen["title"])

    def _installed(self, agent_id: str) -> bool:
        try:
            return bool(self._provider.agent_installed(agent_id))
        except Exception as e:
            # Only picks the card's line; connecting still works.
            log.warning("checking whether %s is installed failed: %s", agent_id, e)
            return False

    def agents(self, kitchen_id: str) -> dict:
        """3a for one kitchen: where it can connect to, and each agent's card."""
        targets = self._targets()
        kitchen = next((t for t in targets if t["id"] == kitchen_id), None)
        if kitchen is None:
            return {"ok": False}
        manifests = self._agent_manifests()
        kind, alias = self._agent_target(kitchen_id)
        cards = agent_cards(
            manifests, kind=kind, alias=alias,
            installed={m.id: self._installed(m.id) for m in manifests},
            connected=self._connected_agents(kitchen_id),
            signed_in={m.id: (self._codex_home / "auth.json").is_file()
                       for m in manifests if m.connect == "codex"})
        for card, m in zip(cards, manifests):
            card["has_app"] = self._provider.agent_app(m.id) is not None
        return {"ok": True, "kitchen": kitchen, "targets": targets, "cards": cards}

    def start_connect(self, agent_id: str, kitchen_id: str) -> dict:
        manifest = self._manifest(agent_id)
        kitchen = next((t for t in self._targets() if t["id"] == kitchen_id), None)
        if manifest is None or kitchen is None or not kitchen["enabled"]:
            return {"ok": False, "message": "That kitchen can't take an agent right now."}
        if self.jobs.running():
            return {"ok": False, "message": "Wait for the current job to finish first."}
        cancel = self._connect_cancel = threading.Event()
        local = kitchen_id == "local"
        instance_id = None if local else kitchen_id
        # The job's emit, once it runs; the connector is made first for its rows.
        sink = {}

        def opened(url):
            self._connect_url = url
            self._open_url(url)

        def setup(agent):
            self._client_factory(self._provider).agent_setup(agent)

        connector = self._connector_factory(
            progress=lambda step, status, message: sink["emit"](
                {"type": "step", "step": step, "status": status, "message": message}),
            cancelled=cancel.is_set, open_url=opened,
            local_setup=setup if local else None)
        rows = connector.rows(manifest.connect, instance_id)

        def work(emit):
            sink["emit"] = emit
            found = connector.connect_one(manifest.connect, instance_id)
            if cancel.is_set():
                return {"type": "cancelled"}
            lines = [f"{'✓' if line.ok else '✗'} "
                     f"{line.target}{f' · {line.agent}' if line.agent else ''}: {line.text}"
                     for line in found]
            failed = next((line for line in found if not line.ok), None)
            if failed and not local and self._account is not None and not self._account.signed_in:
                return {"type": "signed_out"}
            if failed:
                return {"type": "failed", "message": failed.text, "lines": lines}
            return {"type": "done", "lines": lines, "steps": self._guide(manifest, kitchen)}

        self._connect_url = ""
        return {"ok": True, "job": self.jobs.start("connect", work), "rows": rows}

    def cancel_connect(self) -> dict:
        self._connect_cancel.set()
        return {"ok": True}

    def open_connect_page(self) -> dict:
        if not self._connect_url:
            return {"ok": False}
        self._open_url(self._connect_url)
        return {"ok": True}

    def mark_done(self, agent_id: str, kitchen_id: str) -> dict:
        """3c's Done: the only trace an agent without an entry of its own leaves."""
        if self._manifest(agent_id) is None or not self._owned(kitchen_id):
            return {"ok": False}
        self._settings.add_member(AGENTS_DONE, done_flag(agent_id, kitchen_id))
        return {"ok": True}

    def open_agent(self, agent_id: str) -> dict:
        if self._manifest(agent_id) is None:
            return {"ok": False}
        return {"ok": bool(self._provider.open_agent(agent_id))}

    def open_agent_download(self, agent_id: str) -> dict:
        manifest = self._manifest(agent_id)
        if manifest is None:
            return {"ok": False}
        self._open_url(manifest.get_url)
        return {"ok": True}

    # --- the Eggie account ---------------------------------------------

    def load_kitchens(self) -> dict:
        if self._cloud is None:
            return {"ok": False}
        try:
            self._kitchens = self._cloud.instances()
            self._kitchens_error = ""
        except NotSignedIn:
            self._kitchens = None
            self._kitchens_error = ""
            return {"ok": False}
        except ApiUnavailableError:
            self._kitchens_error = "Can't reach Eggie right now."
            return {"ok": False}
        except ApiError as e:
            self._kitchens_error = str(e)
            return {"ok": False}
        return {"ok": True}

    def account_screen_shown(self) -> None:
        if not self._signup_shown:
            self._signup_shown = True
            self._events.send("signup_screen_shown")

    def start_sign_in(self, mode: str) -> dict:
        if self._account is None or mode not in ("create", "existing"):
            return {"ok": False, "message": "Can't sign in from here."}
        fresh = self._account.pending is None
        try:
            sign_in = self._account.start()
        except (ApiError, ApiUnavailableError) as e:
            message = ("Can't reach Eggie right now."
                       if isinstance(e, ApiUnavailableError) else str(e))
            return {"ok": False, "message": message}
        if mode == "create":
            target = urlsplit(sign_in.url)
            path = target.path + (f"?{target.query}" if target.query else "")
            url = f"{web_app_url()}/signup?redirect={quote(path, safe='')}"
        else:
            url = sign_in.url
        self._sign_in_url = url
        if fresh:
            # The service's event names the new-account method "new".
            self._events.send("signup_started",
                              {"method": "new" if mode == "create" else "existing"})
        if not (self._poller and self._poller.is_alive()):
            self._poller = threading.Thread(target=self._poll_sign_in, daemon=True,
                                            name="eggie-sign-in")
            self._poller.start()
        return {"ok": True, "url": url, "user_code": sign_in.user_code,
                "expires_in": max(0, int(sign_in.expires_at - time.time()))}

    def _poll_sign_in(self) -> None:
        while True:
            sign_in = self._account.pending
            if sign_in is None:
                return
            self._sleep(sign_in.interval)
            try:
                outcome = self._account.poll_once()
            except Exception:
                # A transient failure must not leave the account screen waiting forever.
                log.warning("sign-in poll failed", exc_info=True)
                continue
            if outcome == "pending":
                continue
            if outcome == "signed_in":
                self._kitchens, self._kitchens_error = None, ""
                self._just_signed_in = True
            self._push({"kind": "account", "type": outcome})
            return

    def open_sign_in_page(self) -> dict:
        if not self._sign_in_url:
            return {"ok": False}
        self._open_url(self._sign_in_url)
        return {"ok": True}

    def sign_out(self) -> dict:
        if self._account is not None:
            self._account.sign_out()
        self._kitchens, self._kitchens_error = None, ""
        self._just_signed_in = False
        self._checkout, self._picks = None, {}
        self._connect_url = self._sign_in_url = ""
        return {"ok": True}

    # --- making a cloud kitchen (1d) and watching it (1e) ---------------

    @staticmethod
    def _cloud_failure(e: Exception) -> dict:
        if isinstance(e, NotSignedIn):
            return {"ok": False, "signed_out": True, "message": ""}
        if isinstance(e, ApiUnavailableError):
            return {"ok": False, "signed_out": False, "message": "Can't reach Eggie right now."}
        return {"ok": False, "signed_out": False, "message": str(e)}

    def catalog(self) -> dict:
        if self._cloud is None:
            return {"ok": False, "signed_out": False, "message": "Can't reach Eggie right now."}
        try:
            instances = self._cloud.instance_catalog()
            billing = self._cloud.billing_catalog()
        except (NotSignedIn, ApiError, ApiUnavailableError) as e:
            return self._cloud_failure(e)
        sizes = size_tiles(instances, billing)
        regions = {size["id"]: region_tiles(instances, size["id"]) for size in sizes}
        self._picks = {size["id"]: {r["id"] for r in regions[size["id"]] if r["enabled"]}
                       for size in sizes if size["enabled"]}
        return {"ok": True, "name": default_kitchen_name(self._account.first_name if self._account else ""), "sizes": sizes,
                "regions": regions, "from_price": from_price(sizes)}

    def start_checkout(self, name: str, size: str, region: str) -> dict:
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 100:
            return {"ok": False, "signed_out": False, "message": "Give the kitchen a name."}
        if region not in self._picks.get(size, ()):
            return {"ok": False, "signed_out": False,
                    "message": "That size isn't available there. Pick another."}
        try:
            before = frozenset(row["id"] for row in self._cloud.instances())
        except (NotSignedIn, ApiError, ApiUnavailableError) as e:
            return self._cloud_failure(e)
        url = checkout_url(web_app_url(), name, size, region)
        self._checkout = {"url": url, "before": before, "started": self._clock()}
        self._open_url(url)
        return {"ok": True}

    def open_checkout_page(self) -> dict:
        checkout = self._checkout
        if checkout is None:
            return {"ok": False}
        self._open_url(checkout["url"])
        return {"ok": True}

    def checkout_status(self) -> dict:
        checkout = self._checkout
        if checkout is None:
            return {"state": "none", "kitchen_id": "", "wait": 0}
        wait = checkout_wait(self._clock() - checkout["started"])
        try:
            rows = self._cloud.instances()
        except NotSignedIn:
            return {"state": "signed_out", "kitchen_id": "", "wait": 0}
        except (ApiError, ApiUnavailableError):
            return {"state": "waiting", "kitchen_id": "", "wait": wait}
        found = new_kitchen(checkout["before"], rows)
        if found is None:
            return {"state": "waiting", "kitchen_id": "", "wait": wait}
        # Kept, not cleared: an answer the page drops (parked under Settings) is asked again.
        self._kitchens, self._kitchens_error = rows, ""
        return {"state": "found", "kitchen_id": found["id"], "wait": 0}

    def cancel_checkout(self) -> dict:
        self._checkout = None
        return {"ok": True}

    def kitchen(self, kitchen_id: str) -> dict:
        if self._cloud is None or not isinstance(kitchen_id, str) or not kitchen_id:
            return {"ok": False, "signed_out": False, "message": "No such kitchen."}
        try:
            row = self._cloud.instance(kitchen_id)
        except (NotSignedIn, ApiError, ApiUnavailableError) as e:
            return self._cloud_failure(e)
        if self._kitchens is not None:
            # 3a's Connect to reads this list; the row must not stay "getting ready".
            known = any(k.get("id") == row.get("id") for k in self._kitchens)
            self._kitchens = ([row if k.get("id") == row.get("id") else k for k in self._kitchens]
                              + ([] if known else [row]))
        status = row.get("status") or ""
        return {"ok": True, **cloud_card(row), "rows": setup_rows(status, row.get("step")),
                "last_error": row.get("last_error") or "",
                "slow_boot": bool(row.get("slow_boot")),
                "size_label": row.get("size_label") or "",
                "region_label": row.get("region_label") or ""}

    def open_kitchen_page(self, kitchen_id: str) -> dict:
        if not isinstance(kitchen_id, str) or not kitchen_id:
            return {"ok": False}
        self._open_url(f"{web_app_url()}/kitchens/{quote(kitchen_id, safe='')}")
        return {"ok": True}

    def open_cloud_console(self, kitchen_id: str) -> dict:
        row = next((k for k in self._kitchens or [] if k.get("id") == kitchen_id), None)
        host = (row or {}).get("console_hostname")
        if not host:
            return {"ok": False}
        self._open_url(f"{web_app_url()}/kitchens/open?{urlencode({'host': host})}")
        return {"ok": True}

    def start_app_update_check(self) -> None:
        """Once per launch, off the window's thread; announces a release so Home redraws."""
        def run():
            try:
                self._app_release = self._app_update_fn()
                if self._app_release:
                    self._push({"kind": "app_update", "type": "available",
                                "version": self._app_release.version})
            except Exception:
                # The Check for updates tile asks again and shows its error.
                pass

        self._app_check = threading.Thread(target=run, daemon=True)
        self._app_check.start()

    # --- actions ------------------------------------------------------

    def enter_console(self) -> dict:
        if self.jobs.running():
            # Leaving the local UI now would strand the job's events.
            return {"ok": False, "message": "Wait for the current job to finish first."}
        try:
            code = self._client_factory(self._provider).handoff_code()
        except Exception as e:
            # Never load the console without a code: its signed-out screen
            # sends the user to the desktop app they are already in.
            return {"ok": False, "message": f"{e}"}
        url = f"http://localhost:{constants.EDGE_PORT}/#handoff={code}"
        home = self._local_url()
        if home:
            # The console's Home button comes back here.
            url += f"&home={quote(home, safe='')}"
        if self._events.enabled:
            # The console sends its own onboarding events under this session.
            context = self._events.context
            for key, value in (("sid", self._events.session_id),
                               ("app", context["app_version"]), ("os", context["os"]),
                               ("osv", context["os_version"]), ("arch", context["arch"])):
                url += f"&{key}={quote(value, safe='')}"
        # The page navigates itself: pywebview resolves this call's promise
        # with evaluate_js after we return, and a page loaded meanwhile lacks
        # the callback.
        return {"ok": True, "url": url}

    def _added(self, agent_id: str) -> bool:
        try:
            return bool(self._provider.agent_connected(agent_id))
        except Exception as e:
            # Only decides a chip or a card's line; reads as not connected.
            log.warning("checking %s's connection failed: %s", agent_id, e)
            return False

    def start_install(self) -> dict:
        from host.core.install import DeadEnd, InstallError, RebootRequired, run_install

        # A fresh list per run: the steps close over provider state
        # (provider.rootfs is assigned while the list is built), so re-running
        # a list built for an earlier run installs against stale bindings.
        steps = self._steps_factory()

        def work(emit):
            def report(progress):
                if progress.status == "done" and progress.step == "connect":
                    self._sign_in_vm_during_install(emit, progress)
                    return
                emit(progress_event(progress))

            self._events.send("env_setup_started")
            started = self._clock()
            try:
                run_install(steps, self._state, report)
            except Exception as e:
                if isinstance(e, (InstallError, DeadEnd)):
                    step = e.step if isinstance(e, InstallError) else "preflight"
                    self._events.send("env_setup_step_failed",
                                      {"step": step, "error_code": error_code(e)})
                elif not isinstance(e, RebootRequired):
                    self._report_crash("install", e)
                return terminal_event(e)
            self._events.send("env_setup_succeeded",
                              {"duration_sec": int(self._clock() - started)})
            try:
                turn_on_autostart_once(
                    self._settings, available=self._autostart_exe is not None,
                    enable=lambda: self._desktop.set_autostart(True, self._autostart_exe))
            except Exception as e:
                # The VM is installed; an unsaved flag is not a failed install.
                log.warning("could not record turning on open at login: %r", e)
            return terminal_event(None)

        job_id = self.jobs.start("install", work)
        return {"job": job_id, "rows": rows_for(steps)}

    def _sign_in_vm_during_install(self, emit, done) -> None:
        from host.core.install import Progress

        # The CLI's headless install has no browser, so only the app asks.
        def waiting():
            emit(progress_event(Progress("connect", "running", "Press Approve in your browser")))

        try:
            outcome = sign_in_vm(self._client_factory(self._provider),
                                 open_url=self._open_url, sleep=self._sleep,
                                 monotonic=self._clock, on_waiting=waiting)
            if outcome != "signed_in":
                log.warning("the VM's own sign-in ended: %s", outcome)
        except Exception as e:
            log.warning("the VM's own sign-in failed: %r", e)
        emit(progress_event(done))

    def reboot_now(self) -> dict:
        # Order matters and is pinned by a test: a machine that goes down
        # before RunOnce is written never comes back to setup.
        self._provider.register_resume(sys.executable)
        self._provider.reboot()
        return {"ok": True}

    def reset_install(self) -> dict:
        """Forget every recorded step so the next run starts from preflight."""
        self._state.clear()
        return {"ok": True}

    def doctor(self) -> dict:
        from host.core.diagnose import render_diagnosis

        diagnosis = self._provider.preflight()
        # Rendered by host/core so the modal shows exactly what `eggie doctor`
        # prints -- one wording for the user to read out to whoever helps them.
        text = render_diagnosis(diagnosis)
        if self._log_path is not None:
            text += f"\nLog file: {self._log_path}"
        return {"ok": diagnosis.ok, "text": text}

    def start_vm(self) -> dict:
        def work(emit):
            self._provider.start()
            return {"type": "done"}

        return {"job": self.jobs.start("vm", work)}

    def stop_vm(self) -> dict:
        def work(emit):
            self._provider.stop()
            return {"type": "done"}

        return {"job": self.jobs.start("vm", work)}

    def restart_vm(self) -> dict:
        def work(emit):
            self._provider.stop()
            self._provider.start()
            return {"type": "done"}

        return {"job": self.jobs.start("vm", work)}

    def recover_vm(self, everything: bool) -> dict:
        from host.core.provider import VmUnresponsive

        everything = bool(everything)

        def work(emit):
            try:
                self._provider.recover(everything=everything)
            except VmUnresponsive as e:
                # Not a crash: the first attempt failing is what earns the
                # wider restart, and the user has to see its warning first.
                return {"type": "unresponsive", "everything": everything,
                        "message": f"{e}",
                        "warning": self._provider.recover_warning}
            return {"type": "done"}

        return {"job": self.jobs.start("recover", work)}

    def start_repair(self) -> dict:
        from host.core import install
        from host.core.bootstrap import bootstrap

        def work(emit):
            emit({"type": "stage", "stage": "bootstrap"})
            bootstrap(self._provider, repair=True)
            emit({"type": "stage", "stage": "connect"})
            install.connect_with_updates(self._provider)
            return {"type": "done"}

        return {"job": self.jobs.start("repair", work)}

    def start_runtime_update(self) -> dict:
        from host.core import install

        def work(emit):
            emit({"type": "stage", "stage": "update"})
            try:
                install.connect_with_updates(self._provider)
            except Exception as e:
                # The next home() shows this: the probe's problem is empty
                # when the runtime answers but speaks an old API.
                self._runtime_update_error = f"{e}"
                raise
            return {"type": "done"}

        # Cleared before the worker can write a new one, so an earlier
        # attempt's error never outlives a clean retry; put back on JobBusy,
        # which also leaves `tried` alone so the update stays owed.
        previous, self._runtime_update_error = self._runtime_update_error, ""
        try:
            job = self.jobs.start("runtime_update", work)
        except JobBusy:
            self._runtime_update_error = previous
            raise
        self._runtime_update_tried = True
        return {"job": job}

    def start_uninstall(self, purge: bool) -> dict:
        from host.core.install import remove_downloads, remove_vm_data

        def work(emit):
            # Lima refuses to delete a running instance, and its raw error
            # ("expected status `Stopped`, got `Running`") tells the user nothing.
            if self._provider.running():
                raise ProviderError("vm_running", "Stop the kitchen before uninstalling Eggie.")
            self._provider.destroy()
            install_dir = self._install_dir_factory()
            remove_vm_data(install_dir.parent, install_dir)
            # Purge is the app's second confirmation level (the design board's
            # "Also delete downloads and settings" checkbox), not a gate like
            # cli.uninstall's --purge -- the modal is the confirmation here.
            if purge:
                remove_downloads(install_dir.parent)
            return {"type": "done"}

        return {"job": self.jobs.start("uninstall", work)}

    def check_app_update(self) -> dict:
        self._app_release = self._app_update_fn()
        return {"available": self._app_release.version if self._app_release else "",
                "app_version": constants.APP_VERSION}

    def start_app_update(self) -> dict:
        from host.core import download
        from host.core.images import Image

        release = self._app_release
        if release is None:
            return {"ok": False}
        dest = (self._install_dir_factory().parent / "cache"
                / self._provider.installer_asset(release.version))

        def work(emit):
            def on_progress(done, total):
                emit({"type": "progress", "done": done, "total": total})

            path = download.fetch(Image(release.url, release.sha256), dest,
                                  opener=download._default_opener, on_progress=on_progress)
            self._provider.launch_installer(path)
            # The installer may end this process before run() gets to flush.
            self._events.flush(2.0)
            self._quit_app()
            return {"type": "done"}

        return {"job": self.jobs.start("app_update", work), "version": release.version}

    # --- settings and quit ---------------------------------------------

    def get_settings(self) -> dict:
        available = self._autostart_exe is not None
        signed_in = bool(self._account and self._account.signed_in)
        try:
            vm_exists = bool(self._provider.exists())
        except Exception:
            # Only hides Uninstall; there is nothing it could safely remove.
            vm_exists = False
        return {"autostart": available and self._desktop.autostart_enabled(self._autostart_exe),
                "autostart_available": available,
                "signed_in": signed_in, "email": self._account.email if signed_in else "",
                "vm_exists": vm_exists}

    def set_autostart(self, on: bool) -> dict:
        if self._autostart_exe is None:
            return {"ok": False, "error": "Only an installed Eggie can open when you sign in."}
        try:
            self._desktop.set_autostart(bool(on), self._autostart_exe)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "error": ""}

    def quit(self, force: bool) -> dict:
        if self.jobs.running() and not force and not self._quitting:
            return {"confirm": True}
        if not self._quitting:
            self._quitting = True
            # Not a job: JobRegistry runs one at a time, and Quit anyway must
            # work while an install still holds it.
            self._quit_thread = threading.Thread(target=self._stop_then_exit,
                                                 daemon=True, name="eggie-quit")
            self._quit_thread.start()
        return {"quitting": True}

    def _stop_then_exit(self) -> None:
        stopper = threading.Thread(target=self._stop_vm, daemon=True, name="eggie-stop")
        stopper.start()
        stopper.join(self._stop_timeout)
        if stopper.is_alive():
            log.warning("timed out stopping the virtual machine after %g s; quitting anyway",
                        self._stop_timeout)
        self._quit_app()

    def _stop_vm(self) -> None:
        try:
            if self._provider.running():
                self._provider.stop()
        except Exception as e:
            # A VM that will not stop must not leave an app that cannot close.
            log.warning("could not stop the virtual machine: %r", e)
