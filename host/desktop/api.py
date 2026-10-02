"""The only object JavaScript can reach.

Everything here is a security boundary, so the surface is a fixed list of
method names taking scalars. No path, command or URL arriving from JS is
passed to a shell, and nothing is eval'd. Slow work goes through
JobRegistry rather than blocking the thread that owns the window.

Thin on purpose: the decisions live in view.py, which is testable without a
window.
"""
from __future__ import annotations

import sys
import threading
from urllib.parse import quote

from host.core import constants
from host.core.status import probe

from .jobs import JobRegistry
from .lifecycle import turn_on_autostart_once
from .settings import Settings
from .view import inspect_folder, progress_event, route_for, rows_for, terminal_event

_FROZEN = object()
# Past WSL's own 30 s poweroff wait; a hung wsl.exe must not keep Quit from quitting.
QUIT_STOP_TIMEOUT = 60.0


class DesktopApi:
    IMPORT_MODES = ("merge", "replace")

    def __init__(self, provider, state, *, push,
                 probe_fn=probe, steps_factory=None,
                 client_factory=None, install_dir_factory=None, local_url=None,
                 app_update_fn=None, quit_app=None,
                 settings=None, autostart_exe=_FROZEN, window_shown_once=None,
                 stop_timeout=QUIT_STOP_TIMEOUT):
        self._provider = provider
        self._state = state
        self._probe = probe_fn
        self._steps_factory = steps_factory
        self._client_factory = client_factory or self._default_client_factory
        self._install_dir_factory = install_dir_factory or self._default_install_dir
        self._push = push
        self.jobs = JobRegistry(push)
        self._local_url = local_url or (lambda: None)
        self._home_seen = False
        self._window_shown_once = window_shown_once or (lambda: True)
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
        self._stop_timeout = stop_timeout

    @staticmethod
    def _default_client_factory(provider):
        from host.client import ApiClient

        return ApiClient.for_provider(provider)

    @staticmethod
    def _default_install_dir():
        from host.providers import default_install_dir

        return default_install_dir()

    def _default_app_update(self):
        from host.core import app_update
        return app_update.check(current=constants.APP_VERSION,
                                asset_name=self._provider.installer_asset)

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
        first_run = not readiness.vm_exists and not self._state.completed()
        # One-shot like `resumed`: the console's Home link reloads this
        # page, and a second True would bounce the user back into it.
        # A tray-only launch draws Home in a hidden window first; that call
        # must leave the entry for the first one the user can see.
        visible = self._window_shown_once()
        enter_console = (visible and not self._home_seen
                         and (route, state) == ("home", "running")
                         and not first_run and not resumed)
        if visible:
            self._home_seen = True
        return {
            "route": route,
            "state": state,
            # Carried over from host/setup_app/app.py's _should_auto_start:
            # true only where nothing has ever been recorded. Once any step
            # has completed the answer flips, because "not vm_exists" stays
            # true across every failed create_vm relaunch too.
            "first_run": first_run,
            "app_version": constants.APP_VERSION,
            "runtime_version": readiness.runtime_version or "",
            "problem": readiness.problem,
            # Set by __main__.run() when RunOnce reopened the window after a
            # restart, so the install screen can explain why it appeared.
            "resumed": resumed,
            "enter_console": enter_console,
            "app_update": self._app_release.version if self._app_release else "",
        }

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
        # The page navigates itself: pywebview resolves this call's promise
        # with evaluate_js after we return, and a page loaded meanwhile lacks
        # the callback.
        return {"ok": True, "url": url}

    def start_install(self) -> dict:
        from host.core.install import run_install

        # A fresh list per run: the steps close over provider state
        # (provider.rootfs is assigned while the list is built), so re-running
        # a list built for an earlier run installs against stale bindings.
        steps = self._steps_factory()

        def work(emit):
            def report(progress):
                emit(progress_event(progress))

            try:
                run_install(steps, self._state, report)
            except Exception as e:
                return terminal_event(e)
            try:
                turn_on_autostart_once(
                    self._settings, available=self._autostart_exe is not None,
                    enable=lambda: self._provider.set_autostart(True, self._autostart_exe))
            except Exception as e:
                # The VM is installed; an unsaved flag is not a failed install.
                print(f"Omelet could not record turning on open at login: {e!r}",
                      file=sys.stderr)
            return terminal_event(None)

        job_id = self.jobs.start("install", work)
        return {"job": job_id, "rows": rows_for(steps)}

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

    def choose_folder(self) -> dict:
        """Native folder picker. pywebview supplies it on both platforms."""
        import webview
        window = webview.windows[0]
        chosen = window.create_file_dialog(webview.FOLDER_DIALOG)
        if not chosen:
            return {"cancelled": True}
        return self.inspect_folder(chosen[0])

    def inspect_folder(self, path: str) -> dict:
        from host.client import project_id_for

        summary = inspect_folder(path)
        project_id = project_id_for(summary["name"])
        try:
            self._client_factory(self._provider).get_project(project_id)
            conflict = True
        except Exception:
            # Any refusal means "no project by that name to merge into". A
            # conflict banner shown because the API was briefly unreachable
            # would offer Replace -- which deletes -- over nothing.
            conflict = False
        return {**summary, "path": path, "project_id": project_id,
                "conflict": conflict}

    def start_import(self, path: str, mode: str) -> dict:
        from host.client import project_id_for

        if mode not in self.IMPORT_MODES:
            # Never guessed: one of the two modes deletes a project.
            raise ValueError(f"unknown import mode {mode!r}")

        summary = inspect_folder(path)
        project_id = project_id_for(summary["name"])
        client = self._client_factory(self._provider)

        def work(emit):
            if mode == "replace":
                # Delete first. The other order merges the folder in and then
                # wipes it, losing the import along with the old project.
                client.delete_project(project_id)
            client.ensure_project(project_id)

            def on_progress(phase, done, total):
                emit({"type": "progress", "phase": phase,
                      "done": done, "total": total})

            client.upload_directory(project_id, path, on_progress=on_progress)
            return {"type": "done", "project_id": project_id}

        return {"job": self.jobs.start("import", work),
                "project_id": project_id, **summary}

    def doctor(self) -> dict:
        from host.core.diagnose import render_diagnosis

        diagnosis = self._provider.preflight()
        # Rendered by host/core so the modal shows exactly what `omelet doctor`
        # prints -- one wording for the user to read out to whoever helps them.
        return {"ok": diagnosis.ok, "text": render_diagnosis(diagnosis)}

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
            install.connect_with_updates(self._provider)
            # Into the console, as a fresh launch on a running machine would.
            self._home_seen = False
            return {"type": "done"}

        return {"job": self.jobs.start("runtime_update", work)}

    def start_uninstall(self, purge: bool) -> dict:
        from host.core.install import remove_downloads, remove_vm_data

        def work(emit):
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
            self._quit_app()
            return {"type": "done"}

        return {"job": self.jobs.start("app_update", work), "version": release.version}

    # --- settings and quit ---------------------------------------------

    def get_settings(self) -> dict:
        available = self._autostart_exe is not None
        return {"autostart": available and self._provider.autostart_enabled(self._autostart_exe),
                "autostart_available": available}

    def set_autostart(self, on: bool) -> dict:
        if self._autostart_exe is None:
            return {"ok": False, "error": "Only an installed Omelet can open when you sign in."}
        try:
            self._provider.set_autostart(bool(on), self._autostart_exe)
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
                                                 daemon=True, name="omelet-quit")
            self._quit_thread.start()
        return {"quitting": True}

    def _stop_then_exit(self) -> None:
        stopper = threading.Thread(target=self._stop_vm, daemon=True, name="omelet-stop")
        stopper.start()
        stopper.join(self._stop_timeout)
        if stopper.is_alive():
            print(f"Omelet timed out stopping the virtual machine after "
                  f"{self._stop_timeout:g} s; quitting anyway.", file=sys.stderr)
        self._quit_app()

    def _stop_vm(self) -> None:
        try:
            if self._provider.running():
                self._provider.stop()
        except Exception as e:
            # A VM that will not stop must not leave an app that cannot close.
            print(f"Omelet could not stop the virtual machine: {e!r}", file=sys.stderr)
