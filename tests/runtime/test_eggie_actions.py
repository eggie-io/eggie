import getpass
import os
import pwd
import shutil
import time
import uuid

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
                        {"op": "attach", "cols": 80, "rows": 24}, {"op": "stop"},
                        {"op": "output"}):
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


def test_output_holds_a_one_offs_whole_output_past_the_screen(helper):
    helper.start_action("build", "for i in $(seq 200); do echo line-$i; done; exit 1")
    assert helper.wait_exited("build")["exit_code"] == 1
    reply = helper.ask(op="output", project="app", action="build")
    assert reply["ok"], reply
    lines = reply["text"].splitlines()
    assert "line-1" in lines and "line-200" in lines
    assert lines[-1].strip(), "trailing blank screen rows are dropped"


def test_output_without_a_session_is_not_running(helper):
    reply = helper.ask(op="output", project="app", action="never")
    assert reply["ok"] is False and reply["code"] == "not_running"


def test_output_past_its_cap_keeps_the_newest_whole_lines(helper):
    helper.kill()
    helper.start("--output-cap", "100")
    helper.start_action("build", "for i in $(seq 200); do echo line-$i; done")
    helper.wait_exited("build")
    text = helper.ask(op="output", project="app", action="build")["text"]
    assert len(text) <= 100
    assert text.startswith("line-") and "line-200" in text.split("\n")


def test_a_connection_login_runs_in_the_accounts_home(helper, tmp_path):
    out = tmp_path / "out"
    helper.start_connection("expo", f"pwd > {out}")
    assert eventually(lambda: helper.connection_run("expo")["state"] == "exited")
    assert out.read_text().strip() == pwd.getpwnam(getpass.getuser()).pw_dir


def test_a_run_past_its_deadline_is_stopped_and_marked_timed_out(helper):
    run = helper.start_connection("expo", "sleep 600", deadline=1)
    assert run["timed_out"] is False
    run = eventually(lambda: (r := helper.connection_run("expo")) and r["state"] == "exited" and r)
    assert run and run["timed_out"] is True


def test_a_run_stopped_by_hand_is_not_timed_out(helper):
    assert helper.ask(op="start", project="app", action="slow", command="exec sleep 600", log=False,
                      deadline=600)["ok"]
    reply = helper.ask(op="stop", project="app", action="slow")
    assert reply["ok"] and reply["run"]["state"] == "exited" and reply["run"]["timed_out"] is False


def test_a_stopped_run_keeps_its_screen_even_when_tmux_leaves_a_zombie(helper):
    # tmux now and then never reaps the pane's shell; a lone zombie is not a live group.
    for i in range(6):
        helper.start_action(f"s{i}", "sleep 600")
        started = time.monotonic()
        reply = helper.ask(op="stop", project="app", action=f"s{i}")
        assert reply["run"] and reply["run"]["state"] == "exited", (i, reply)
        assert time.monotonic() - started < 2


def test_a_login_that_ignores_sigterm_and_sighup_still_reads_timed_out(helper):
    helper.start_connection("expo", "trap '' TERM HUP; sleep 600", deadline=1)
    assert eventually(lambda: helper.connection_run("expo")["state"] == "exited", 5)
    # Past the stop grace, when the stubborn command is killed.
    time.sleep(6)
    run = helper.connection_run("expo")
    assert run and run["state"] == "exited" and run["timed_out"] is True


def test_a_login_started_again_after_its_deadline_is_not_stopped_with_the_old_one(helper):
    # The old pane reads dead while its stubborn command lives on through the stop grace.
    helper.start_connection("expo", "trap '' TERM HUP; sleep 600", deadline=1)
    assert eventually(lambda: helper.connection_run("expo")["state"] == "exited", 5)
    helper.start_connection("expo", "sleep 600")
    time.sleep(6)
    run = helper.connection_run("expo")
    assert run and run["state"] == "running" and run["timed_out"] is False


def test_a_deadline_holds_across_a_helper_restart(helper):
    helper.start_connection("expo", "sleep 600", deadline=2)
    helper.kill()
    helper.start()
    assert eventually(lambda: helper.connection_run("expo")["timed_out"])
    assert helper.connection_run("expo")["state"] == "exited"


def test_connection_sessions_are_not_a_projects_runs(helper):
    helper.start_connection("expo", "sleep 600")
    assert helper.ask(op="status", project="app") == {"ok": True, "runs": {}}
    assert helper.ask(op="stop-project", project="app") == {"ok": True}
    assert helper.connection_run("expo")["state"] == "running"


@pytest.mark.parametrize("deadline", [0, "5", 86401, True])
def test_start_refuses_a_bad_deadline(helper, deadline):
    reply = helper.ask(op="start", connection="expo", command="true", log=False, deadline=deadline)
    assert reply["ok"] is False and reply["code"] == "invalid"


@pytest.mark.parametrize("request_", [
    {"connection": "expo", "project": "app", "action": "a"},
    {"connection": "expo", "action": "a"},
    {"connection": "+expo"},
    {},
])
def test_a_request_needs_exactly_one_valid_target(helper, request_):
    reply = helper.ask(op="run", **request_)
    assert reply["ok"] is False and reply["code"] == "invalid", reply


def test_a_connection_login_refuses_a_log(helper):
    reply = helper.ask(op="start", connection="expo", command="true", log=True)
    assert reply["ok"] is False and reply["code"] == "invalid"


def test_check_returns_the_commands_exit_code(helper):
    assert helper.ask(op="check", command="exit 0") == {"ok": True, "exit_code": 0}
    assert helper.ask(op="check", command="exit 3") == {"ok": True, "exit_code": 3}


def test_a_check_past_its_timeout_is_null_and_leaves_nothing_behind(helper):
    # The subshell is a forked bash, so it carries the marker; a lone `sleep` would be exec'd
    # and lose it.
    marker = f"chk-{uuid.uuid4()}"
    command = f"(sleep 600; : {marker}); : {marker}"
    assert helper.ask(op="check", command=command) == {"ok": True, "exit_code": None}

    def gone() -> bool:
        return not any(marker in cmdline for cmdline in cmdlines())

    assert eventually(gone, 3)


def cmdlines() -> list[str]:
    found = []
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            try:
                found.append(open(f"{entry.path}/cmdline", "rb").read().decode(errors="replace"))
            except OSError:
                pass
    return found


def screen(helper, action):
    reply = helper.ask(op="screen", project="app", action=action)
    assert reply["ok"], reply
    return reply


def test_screen_has_the_prompt_on_the_cursor_row(helper):
    helper.start_action("login", "printf 'one\\nApple ID: '; read -r x")
    shown = eventually(lambda: (s := screen(helper, "login")) and "Apple ID:" in s["text"] and s)
    assert shown["rows"][shown["cursor_y"]].startswith("Apple ID:")
    assert isinstance(shown["history_size"], int)


def test_a_prompt_wider_than_the_pane_comes_back_whole_as_the_cursor_line(helper):
    prompt = "? " + "Do you want to log in to your Apple account? " * 4 + "(Y/n)"
    helper.start_action("login", f"printf 'one\\n{prompt} '; read -r x")
    shown = eventually(lambda: (s := screen(helper, "login")) and "(Y/n)" in s["text"] and s)
    assert prompt not in shown["rows"][shown["cursor_y"]], "the prompt must wrap for this test"
    assert shown["cursor_line"] == prompt


def test_a_long_link_comes_back_whole_in_text(helper):
    link = "https://expo.dev/" + "a" * 200
    helper.start_action("login", f"echo {link}; read -r x")
    assert eventually(lambda: link in screen(helper, "login")["text"])


def test_screen_without_a_session_is_not_running(helper):
    reply = helper.ask(op="screen", project="app", action="never")
    assert reply["ok"] is False and reply["code"] == "not_running"


def test_type_reaches_the_command_verbatim_and_never_argv(helper, tmp_path):
    out = tmp_path / "out"
    helper.start_action("login", f'read -r x; printf %s "$x" > {out}')
    answer = "-t x; 'q' $HOME"
    assert helper.ask(op="type", project="app", action="login", text=answer) == {"ok": True}
    assert eventually(lambda: out.exists() and out.read_text() == answer), \
        out.read_text() if out.exists() else None
    assert "'q' $HOME" not in helper.argv_log.read_text()


def test_type_refuses_a_second_line(helper):
    helper.start_action("login", "read -r x")
    reply = helper.ask(op="type", project="app", action="login", text="a\nb")
    assert reply["ok"] is False and reply["code"] == "invalid"


def test_type_into_a_finished_run_is_not_running(helper):
    helper.start_action("login", "true")
    helper.wait_exited("login")
    reply = helper.ask(op="type", project="app", action="login", text="x")
    assert reply["ok"] is False and reply["code"] == "not_running"


def test_an_empty_answer_is_just_enter(helper, tmp_path):
    out = tmp_path / "out"
    helper.start_action("login", f'read -r x; printf "[%s]" "$x" > {out}')
    assert helper.ask(op="type", project="app", action="login", text="") == {"ok": True}
    assert eventually(lambda: out.exists() and out.read_text() == "[]")
