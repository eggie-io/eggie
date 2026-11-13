import getpass
import os
import shutil
import time

import pytest

from .actions_helper_fixture import Helper, Terminal, eventually, helper  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")



def test_a_one_off_runs_in_the_project_folder_and_reports_its_exit_code(helper):
    helper.start_action("build", "pwd > out; exit 3")
    run = helper.wait_exited("build")
    assert run and run["exit_code"] == 3
    assert isinstance(run["started_at"], int)
    assert (helper.projects / "app" / "out").read_text().strip() == str(helper.projects / "app")


@pytest.mark.parametrize("command", [r"find . -maxdepth 0 -exec pwd > out \;", "pwd > out;"])
def test_a_command_ending_in_a_semicolon_reaches_bash_intact(helper, command):
    # tmux reads an argument ending in `;` as a command separator.
    helper.start_action("build", command)
    assert helper.wait_exited("build")["exit_code"] == 0
    assert (helper.projects / "app" / "out").read_text().strip() == str(helper.projects / "app")


def test_start_while_running_is_action_running(helper):
    helper.start_action("serve", "sleep 600")
    reply = helper.ask(op="start", project="app", action="serve", command="sleep 600", log=False)
    assert reply["ok"] is False and reply["code"] == "action_running"


def test_a_finished_run_can_start_again(helper):
    helper.start_action("build", "exit 3")
    assert helper.wait_exited("build")["exit_code"] == 3
    helper.start_action("build", "exit 5")
    assert eventually(lambda: helper.run("build")["exit_code"] == 5)


def test_stop_ends_a_running_command(helper):
    helper.start_action("serve", "sleep 600")
    started = time.monotonic()
    reply = helper.ask(op="stop", project="app", action="serve")
    assert time.monotonic() - started < 6
    assert reply["ok"] and (reply["run"] is None or reply["run"]["state"] == "exited")
    run = helper.run("serve")
    assert run is None or run["state"] == "exited"


def test_stop_ends_a_command_that_ignores_sigterm_and_sighup(helper):
    helper.start_action("stubborn", "trap '' TERM HUP; echo $$ > pid; exec sleep 600")
    pid_file = helper.projects / "app" / "pid"
    assert eventually(lambda: pid_file.exists() and pid_file.read_text().strip())
    pid = int(pid_file.read_text())
    assert helper.ask(op="stop", project="app", action="stubborn")["ok"]

    def gone() -> bool:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True

    assert eventually(gone, 3)


def test_stop_project_ends_only_that_projects_runs(helper):
    helper.start_action("serve", "sleep 600")
    helper.start_action("watch", "sleep 600")
    helper.start_action("serve", "sleep 600", project="other")
    assert helper.ask(op="stop-project", project="app") == {"ok": True}
    runs = helper.ask(op="status", project="app")["runs"]
    assert [r for r in runs.values() if r["state"] == "running"] == []
    assert helper.run("serve", project="other")["state"] == "running"


def test_attach_relays_input_and_output(helper):
    helper.start_action("say", "read x; echo got:$x")
    terminal = helper.attach("say")
    terminal.send(b"hi\r")
    terminal.read_until(b"got:hi")
    terminal.close()
    assert helper.wait_exited("say")["exit_code"] == 0


def test_the_tmux_prefix_reaches_the_program_and_opens_no_shell(helper):
    helper.start_action("say", "echo ready; cat -v")
    terminal = helper.attach("say")
    # Before the screen is up, the pty itself still echoes ^B.
    terminal.read_until(b"ready")
    terminal.send(b"\x02c\x02:\r")
    terminal.read_until(b"^Bc^B:")
    terminal.close()
    panes = helper.tmux("list-panes", "-s", "-t", "=app--say", "-F", "#{pane_id}").stdout.split()
    assert len(panes) == 1, panes


def test_a_stray_pane_does_not_change_an_actions_status(helper):
    helper.start_action("build", "exit 3")
    assert helper.wait_exited("build")["exit_code"] == 3
    assert helper.tmux("new-window", "-d", "-t", "=app--build:", "sleep 600").returncode == 0
    run = helper.run("build")
    assert run["state"] == "exited" and run["exit_code"] == 3


def test_a_second_attach_sees_what_was_on_screen(helper):
    # The tab closed mid-prompt: the run goes on, and reopening shows the prompt.
    helper.start_action("login", "echo enter-the-code; read code; echo code:$code")
    first = helper.attach("login")
    first.read_until(b"enter-the-code")
    first.close()
    time.sleep(0.3)
    assert helper.run("login")["state"] == "running"
    second = helper.attach("login")
    second.read_until(b"enter-the-code")
    second.send(b"123\r")
    second.read_until(b"code:123")
    second.close()


def test_a_resize_reaches_the_actions_window(helper):
    helper.start_action("serve", "sleep 600")

    def size() -> str:
        return helper.tmux("display-message", "-p", "-t", "=app--serve:",
                           "#{window_width}x#{window_height}").stdout.strip()

    terminal = helper.attach("serve", cols=80, rows=24)
    assert eventually(lambda: size() == "80x24"), size()
    terminal.resize(101, 33)
    assert eventually(lambda: size() == "101x33"), size()
    terminal.close()


def test_attach_without_a_session_is_not_running(helper):
    sock, rfile, reply = helper._connect(
        {"op": "attach", "project": "app", "action": "never", "cols": 80, "rows": 24})
    rfile.close()
    sock.close()
    assert reply["ok"] is False and reply["code"] == "not_running"


def test_a_long_running_log_holds_output_and_a_one_off_writes_none(helper):
    log = helper.logs / "app" / "serve.log"
    log.parent.mkdir()
    log.write_text("stale-from-last-run\n")
    helper.start_action("serve", "echo serving-now; sleep 600", log=True)
    helper.start_action("login", "echo the-secret-prompt")
    assert eventually(lambda: "serving-now" in log.read_text())
    assert "stale-from-last-run" not in log.read_text()
    helper.wait_exited("login")
    assert not (helper.logs / "app" / "login.log").exists()


def test_a_log_stops_growing_at_its_cap(helper):
    helper.kill()
    helper.start("--log-cap", "1000")
    helper.start_action("serve", "for i in $(seq 500); do echo line-$i; done; echo all-printed; sleep 600",
                        log=True)
    terminal = helper.attach("serve")
    terminal.read_until(b"all-printed")
    terminal.close()
    log = helper.logs / "app" / "serve.log"
    assert eventually(lambda: log.stat().st_size >= 1000)
    time.sleep(0.3)
    assert log.stat().st_size == 1000


def test_a_project_removed_outside_the_api_can_still_have_its_actions_stopped(helper):
    helper.start_action("serve", "sleep 600")
    shutil.rmtree(helper.projects / "app")
    assert helper.ask(op="stop", project="app", action="serve")["ok"]
    assert helper.ask(op="stop-project", project="app") == {"ok": True}
    assert helper.ask(op="status", project="app") == {"ok": True, "runs": {}}


def test_runs_survive_a_helper_restart(helper):
    helper.start_action("serve", "sleep 600")
    helper.kill()
    helper.start()
    assert helper.run("serve")["state"] == "running"


def test_actions_run_as_the_connect_user_and_as_root_when_it_is_empty(helper):
    helper.start_action("first", "true")
    assert set(helper.runuser_log.read_text().split()) == {getpass.getuser()}
    helper.set_user("")
    helper.start_action("second", "true")
    assert helper.runuser_log.read_text().split()[-1] == "root"


def test_ids_that_are_not_slugs_are_refused(helper):
    for project, action in [("../x", "a"), ("a b", "a"), ("app", "../x"), ("app", "a b"),
                            ("app", "a--b")]:
        for request in ({"op": "start", "command": "true", "log": False},
                        {"op": "attach", "cols": 80, "rows": 24}, {"op": "stop"}):
            reply = helper.ask(**request, project=project, action=action)
            assert reply["ok"] is False and reply["code"] == "invalid", (request, project, action, reply)


def test_start_refuses_a_project_with_no_folder(helper):
    reply = helper.ask(op="start", project="missing", action="a", command="true", log=False)
    assert reply["ok"] is False and reply["code"] == "invalid"


def test_a_log_folder_planted_as_a_link_is_not_followed(helper, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (helper.logs / "app").symlink_to(elsewhere)
    reply = helper.ask(op="start", project="app", action="serve", command="sleep 600", log=True)
    assert reply["ok"] is False
    assert list(elsewhere.iterdir()) == []


def test_an_unexpected_error_still_gets_a_reply(helper):
    # A lone surrogate cannot be encoded into tmux's argv.
    reply = helper.ask(op="start", project="app", action="odd", command="echo \ud800", log=False)
    assert reply["ok"] is False and reply["code"] == "failed"
