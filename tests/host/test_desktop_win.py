"""Open at login on Windows: the Run value, and Task Manager's own switch over it."""
from host.providers.desktop_win import (AUTOSTART_VALUE_NAME, RUN_KEY, STARTUP_APPROVED_KEY,
                                        WindowsDesktop, run_value)


def test_run_value_quotes_a_path_with_spaces():
    exe = r"C:\Users\Jane Doe\AppData\Local\Programs\Eggie\setup.exe"
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

    return WindowsDesktop(registry_reader=reader, registry_writer=writer,
                          registry_deleter=deleter, registry_binary_reader=binary_reader)


EXE = r"C:\Program Files\Eggie\setup.exe"


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
    desktop = _autostart(store)
    assert desktop.autostart_enabled(EXE) is False
    desktop.set_autostart(True, EXE)
    assert desktop.autostart_enabled(EXE) is True


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


def test_turning_on_from_eggie_clears_the_task_manager_disable():
    store = {(RUN_KEY, AUTOSTART_VALUE_NAME): run_value(EXE),
             (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME): DISABLED_IN_TASK_MANAGER}
    desktop = _autostart(store)
    desktop.set_autostart(True, EXE)
    assert (STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME) not in store
    assert desktop.autostart_enabled(EXE) is True


def test_autostart_registers_the_gui_exe_when_given_the_console_exe(tmp_path):
    """`eggie setup` runs the window from eggie.exe; a login entry pointing
    there flashes a console at every sign-in."""
    (tmp_path / "eggie.exe").write_bytes(b"")
    (tmp_path / "setup.exe").write_bytes(b"")
    console, gui = str(tmp_path / "EGGIE.EXE"), str(tmp_path / "setup.exe")
    store = {}
    desktop = _autostart(store)
    desktop.set_autostart(True, console)
    assert store[(RUN_KEY, AUTOSTART_VALUE_NAME)] == run_value(gui)
    assert desktop.autostart_enabled(gui) is True
    assert desktop.autostart_enabled(console) is True


def test_autostart_keeps_the_console_exe_when_there_is_no_gui_sibling(tmp_path):
    (tmp_path / "eggie.exe").write_bytes(b"")
    console = str(tmp_path / "eggie.exe")
    store = {}
    _autostart(store).set_autostart(True, console)
    assert store[(RUN_KEY, AUTOSTART_VALUE_NAME)] == run_value(console)
