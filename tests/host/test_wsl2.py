from pathlib import Path
from host.providers.wsl2 import Wsl2Provider


class FakeRunner:
    """Records argv, returns a scripted (returncode, stdout, stderr)."""
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.calls = []
        self._out, self._err, self._rc = stdout, stderr, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = self._err
        return R()


def make(runner, install_dir=Path("/tmp/inst"), rootfs=Path("/tmp/ubuntu.tar.gz"),
         spawner=None):
    return Wsl2Provider(
        distro="omelet-vm",
        install_dir=install_dir,
        rootfs=rootfs,
        wsl="wsl.exe",
        runner=runner,
        spawner=spawner if spawner is not None else [].append,
    )


def test_exec_builds_passthrough_argv_and_decodes_utf8():
    r = FakeRunner(stdout=b"Linux 6.6\n")
    result = make(r).exec(["uname", "-sr"])
    assert r.calls[-1] == ["wsl.exe", "-d", "omelet-vm", "--", "uname", "-sr"]
    assert result.stdout == "Linux 6.6"
    assert result.ok is True


def test_exec_root_inserts_user_root():
    r = FakeRunner()
    make(r).exec(["id", "-un"], root=True)
    assert r.calls[-1] == ["wsl.exe", "-d", "omelet-vm", "-u", "root", "--", "id", "-un"]


def test_exists_true_when_distro_in_list():
    listing = "omelet-vm\r\nUbuntu\r\n".encode("utf-16-le")
    r = FakeRunner(stdout=listing)
    assert make(r).exists() is True
    assert r.calls[-1] == ["wsl.exe", "-l", "-q"]


def test_exists_false_when_absent():
    r = FakeRunner(stdout="Ubuntu\r\n".encode("utf-16-le"))
    assert make(r).exists() is False


def test_create_imports_then_enables_systemd_then_reboots(tmp_path):
    rootfs = tmp_path / "ubuntu.tar.gz"
    rootfs.write_bytes(b"")
    install = tmp_path / "inst"
    r = FakeRunner()
    make(r, install_dir=install, rootfs=rootfs).create()
    argvs = r.calls
    assert argvs[0][:2] == ["wsl.exe", "--import"]
    assert argvs[0][2] == "omelet-vm"
    assert str(install) in argvs[0][3]
    assert str(rootfs) in argvs[0][4]
    assert argvs[0][-2:] == ["--version", "2"]
    # systemd fixup runs as root, writes wsl.conf
    assert any("-u" in a and "root" in a and "wsl.conf" in " ".join(a) for a in argvs)
    # terminates so systemd takes effect, then boots again
    terminate = argvs.index(["wsl.exe", "--terminate", "omelet-vm"])
    assert ["wsl.exe", "-d", "omelet-vm", "--", "true"] in argvs[terminate:]


def test_create_terminates_without_a_poweroff_wait(tmp_path):
    # systemd is not running yet after the import, so there is nothing to
    # power off; the graceful path would only wait out its 30 s poll.
    rootfs = tmp_path / "ubuntu.tar.gz"
    rootfs.write_bytes(b"")
    r = FakeRunner()
    make(r, install_dir=tmp_path / "inst", rootfs=rootfs).create()
    assert not any("poweroff" in a for a in r.calls)
    assert ["wsl.exe", "--terminate", "omelet-vm"] in r.calls


def test_create_rejects_a_rootfs_path_that_does_not_exist():
    r = FakeRunner()
    try:
        make(r, rootfs=Path("/tmp/definitely-not-here.wsl")).create()
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as e:
        assert "definitely-not-here" in str(e)
    assert r.calls == [], "must not shell out to wsl.exe with a bad rootfs"


def test_create_raises_when_import_fails(tmp_path):
    rootfs = tmp_path / "ubuntu.tar.gz"
    rootfs.write_bytes(b"")
    r = FakeRunner(stderr="Invalid distro name".encode("utf-16-le"), returncode=1)
    try:
        make(r, install_dir=tmp_path / "inst", rootfs=rootfs).create()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Invalid distro name" in str(e)


def test_stop_terminates_and_destroy_unregisters():
    r = FakeRunner()
    p = make(r)
    p.stop()
    assert r.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]
    p.destroy()
    assert r.calls[-1] == ["wsl.exe", "--unregister", "omelet-vm"]


def test_start_holds_the_vm_open_so_wsl_does_not_idle_it_out():
    spawned = []
    make(ScriptedRunner("pgrep"), spawner=spawned.append).start()
    assert len(spawned) == 1
    assert spawned[0][:5] == ["wsl.exe", "-d", "omelet-vm", "-u", "root"]
    assert "exec -a omelet-hold sleep infinity" in spawned[0]


def test_start_does_not_stack_a_second_hold_on_a_held_vm():
    spawned = []
    make(FakeRunner(), spawner=spawned.append).start()
    assert spawned == []


class ScriptedRunner(FakeRunner):
    """Fails only the calls whose argv contains `fail_on`; everything else
    succeeds. Enough to fail one step of a multi-command operation."""

    def __init__(self, fail_on, stderr=b"", **kwargs):
        super().__init__(**kwargs)
        self._fail_on = fail_on
        self._fail_err = stderr

    def __call__(self, argv):
        self.calls.append(argv)
        failed = self._fail_on in " ".join(argv)
        err, out = self._fail_err, self._out

        class R:
            returncode = 1 if failed else 0
            stdout = b"" if failed else out
            stderr = err if failed else b""
        return R()


def _raises(call, needle):
    try:
        call()
    except RuntimeError as e:
        assert needle in str(e), str(e)
        return str(e)
    raise AssertionError(f"expected a RuntimeError mentioning {needle!r}")


def test_create_stops_when_the_systemd_write_fails(tmp_path):
    # Unchecked, systemd stays off and the only symptom is bootstrap dying
    # minutes later at `systemctl enable --now docker`.
    rootfs = tmp_path / "ubuntu.tar.gz"
    rootfs.write_bytes(b"")
    r = ScriptedRunner("wsl.conf", stderr=b"bash: /etc/wsl.conf: Read-only file system")
    provider = make(r, install_dir=tmp_path / "inst", rootfs=rootfs)

    message = _raises(provider.create, "systemd")
    assert "Read-only file system" in message, "the guest's own words must survive"
    assert not any("--terminate" in " ".join(a) for a in r.calls), \
        "a broken VM must not be reported as created"


def test_start_reports_a_distro_that_does_not_exist(tmp_path):
    r = ScriptedRunner("true", stderr="There is no distribution with the "
                                      "supplied name.".encode("utf-16-le"))
    _raises(make(r).start, "could not be started")


def test_stop_and_destroy_report_failures_instead_of_claiming_success():
    stop = ScriptedRunner("--terminate",
                          stderr="no distribution".encode("utf-16-le"))
    _raises(make(stop).stop, "could not be stopped")

    destroy = ScriptedRunner("--unregister",
                             stderr="no distribution".encode("utf-16-le"))
    _raises(make(destroy).destroy, "could not be removed")


def test_running_reads_the_running_list_without_booting_the_distro():
    # Any `wsl -d` boots the distro, so a probe using exec() restarts a VM
    # the user just stopped.
    r = FakeRunner(stdout="Ubuntu\r\nomelet-vm\r\n".encode("utf-16-le"))
    assert make(r).running() is True
    assert r.calls == [["wsl.exe", "-l", "--running", "-q"]]


def test_running_false_when_the_distro_is_stopped():
    # With nothing running, wsl.exe prints a sentence and exits non-zero.
    r = FakeRunner(stdout="There are no running distributions.\r\n".encode("utf-16-le"),
                   returncode=1)
    assert make(r).running() is False


# What wsl.exe prints when its service gives up on the utility VM. It comes
# back through exec()'s passthrough as well as from wsl.exe's own commands.
_HUNG = ("A connection attempt failed because the connected party did not "
         "properly respond after a period of time.\r\n"
         "Error code: Wsl/Service/CreateInstance/0x8007274c\r\n")


def test_a_hung_wsl_is_reported_as_unresponsive_not_as_a_failed_command():
    from host.core.provider import VmUnresponsive

    for stderr in (_HUNG.encode(), _HUNG.encode("utf-16-le")):
        r = FakeRunner(stderr=stderr, returncode=4294967295)
        try:
            make(r).exec(["cat", "/opt/omelet/api.token"], root=True)
        except VmUnresponsive as e:
            assert "0x8007274c" in str(e)
        else:
            raise AssertionError("a hung WSL must not look like a missing file")


def test_a_command_failing_inside_the_vm_still_returns_its_result():
    r = FakeRunner(stderr=b"cat: /opt/omelet/api.token: No such file or directory",
                   returncode=1)
    result = make(r).exec(["cat", "/opt/omelet/api.token"], root=True)
    assert result.returncode == 1


def test_recover_restarts_only_this_vm():
    r = FakeRunner()
    make(r).recover()
    assert r.calls[0] == ["wsl.exe", "-d", "omelet-vm", "-u", "root", "--",
                          "systemctl", "poweroff"]
    assert ["wsl.exe", "--terminate", "omelet-vm"] in r.calls
    assert ["wsl.exe", "--shutdown"] not in r.calls
    assert ["wsl.exe", "-d", "omelet-vm", "--", "true"] in r.calls


def test_recover_everything_shuts_all_of_wsl_down_first():
    r = FakeRunner()
    make(r).recover(everything=True)
    assert r.calls[0] == ["wsl.exe", "--shutdown"]
    assert ["wsl.exe", "-d", "omelet-vm", "--", "true"] in r.calls


def test_recover_reports_a_vm_still_not_answering():
    from host.core.provider import VmUnresponsive

    r = ScriptedRunner("-- true", stderr=_HUNG.encode())
    try:
        make(r).recover()
    except VmUnresponsive:
        return
    raise AssertionError("a restart that did not help must say so")


def test_the_windows_installer_updates_silently_and_closes_the_running_app(tmp_path):
    spawned = []
    provider = Wsl2Provider(distro="omelet-vm", install_dir=Path("/tmp/inst"),
                           rootfs=Path("/tmp/ubuntu.tar.gz"), wsl="wsl.exe",
                           runner=FakeRunner(), spawner=spawned.append, arch="amd64")
    assert provider.installer_asset("0.2.0") == "OmeletSetup-0.2.0.exe"
    provider.launch_installer(Path("C:/cache/OmeletSetup-0.2.0.exe"))
    (argv,) = spawned
    assert argv[0].endswith("OmeletSetup-0.2.0.exe")
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"} <= set(argv[1:])


from host.providers.wsl2 import (AUTOSTART_VALUE_NAME, RUN_KEY, STARTUP_APPROVED_KEY,  # noqa: E402
                                   run_value)


class PrefixRunner:
    """Answers by argv prefix; records every call."""
    def __init__(self, answers):
        self.calls, self._answers = [], answers

    def __call__(self, argv):
        self.calls.append(argv)
        for prefix, (rc, out) in self._answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                break
        else:
            rc, out = 0, b""

        class R:
            returncode = rc
            stdout = out
            stderr = b""
        return R()


def _stopper(runner, clock_values):
    ticks = iter(clock_values)
    return Wsl2Provider(distro="omelet-vm", wsl="wsl.exe", runner=runner,
                        spawner=[].append, sleep=lambda s: None,
                        clock=lambda: next(ticks))


RUNNING = ("omelet-vm\r\n").encode("utf-16-le")


def test_stop_powers_off_through_systemd_before_terminating():
    runner = PrefixRunner({("wsl.exe", "-l", "--running", "-q"): (1, b"")})
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[0] == ["wsl.exe", "-d", "omelet-vm", "-u", "root", "--",
                               "systemctl", "poweroff"]
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]


def test_stop_terminates_even_when_poweroff_fails():
    runner = PrefixRunner({
        ("wsl.exe", "-d", "omelet-vm", "-u", "root", "--", "systemctl"): (1, b"boom"),
        ("wsl.exe", "-l", "--running", "-q"): (1, b""),
    })
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]


def test_stop_gives_up_waiting_after_thirty_seconds_and_terminates():
    runner = PrefixRunner({("wsl.exe", "-l", "--running", "-q"): (0, RUNNING)})
    _stopper(runner, [0, 10, 20, 31]).stop()
    polls = [c for c in runner.calls if c[:3] == ["wsl.exe", "-l", "--running"]]
    assert len(polls) == 3
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]


def test_stop_terminates_when_the_hung_wsl_rejects_poweroff():
    hung = _HUNG.encode()

    class Hung(PrefixRunner):
        def __call__(self, argv):
            result = super().__call__(argv)
            if "poweroff" in argv:
                class R:
                    returncode = 4294967295
                    stdout = b""
                    stderr = hung
                return R()
            return result

    runner = Hung({("wsl.exe", "-l", "--running", "-q"): (1, b"")})
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]


def test_stop_terminates_when_the_hung_wsl_rejects_the_running_poll():
    hung = _HUNG.encode()

    class HungPoll(PrefixRunner):
        def __call__(self, argv):
            result = super().__call__(argv)
            if argv[:3] == ["wsl.exe", "-l", "--running"]:
                class R:
                    returncode = 4294967295
                    stdout = b""
                    stderr = hung
                return R()
            return result

    runner = HungPoll({})
    _stopper(runner, [0, 1]).stop()
    assert runner.calls[-1] == ["wsl.exe", "--terminate", "omelet-vm"]


def test_run_value_quotes_a_path_with_spaces():
    exe = r"C:\Users\Jane Doe\AppData\Local\Programs\Omelet\setup.exe"
    assert run_value(exe) == f'"{exe}" setup --background'


def _autostart(store):
    def reader(key, name):
        return store.get((key, name))

    def writer(key, name, value):
        store[(key, name)] = value

    def binary_reader(key, name):
        value = store.get((key, name))
        return value if isinstance(value, bytes) else None

    def deleter(key, name):
        store.pop((key, name), None)

    return Wsl2Provider(distro="omelet-vm", wsl="wsl.exe", runner=lambda a: None,
                        spawner=[].append, registry_reader=reader,
                        registry_writer=writer, registry_deleter=deleter,
                        registry_binary_reader=binary_reader)


EXE = r"C:\Program Files\Omelet\setup.exe"


def test_set_autostart_on_writes_the_run_value():
    store = {}
    _autostart(store).set_autostart(True, EXE)
    assert store == {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE)}


def test_set_autostart_off_deletes_the_run_value():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE)}
    _autostart(store).set_autostart(False, EXE)
    assert store == {}


def test_autostart_reads_enabled_only_for_this_exe():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(r"C:\old\setup.exe")}
    provider = _autostart(store)
    assert provider.autostart_enabled(EXE) is False
    provider.set_autostart(True, EXE)
    assert provider.autostart_enabled(EXE) is True


def test_autostart_reads_disabled_when_the_value_is_missing():
    assert _autostart({}).autostart_enabled(EXE) is False


# Task Manager's Startup tab writes this; the first byte is the state.
DISABLED_IN_TASK_MANAGER = bytes([0x03]) + bytes(11)
ENABLED_IN_TASK_MANAGER = bytes([0x02]) + bytes(11)


def test_disabled_in_task_manager_reads_as_off():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE),
             (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME): DISABLED_IN_TASK_MANAGER}
    assert _autostart(store).autostart_enabled(EXE) is False


def test_re_enabled_in_task_manager_reads_as_on():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE),
             (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME): ENABLED_IN_TASK_MANAGER}
    assert _autostart(store).autostart_enabled(EXE) is True


def test_turning_on_from_omelet_clears_the_task_manager_disable():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE),
             (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME): DISABLED_IN_TASK_MANAGER}
    provider = _autostart(store)
    provider.set_autostart(True, EXE)
    assert (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME) not in store
    assert provider.autostart_enabled(EXE) is True
