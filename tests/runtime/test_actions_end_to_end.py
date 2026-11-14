import shutil

import pytest

from eggie_api.infra.actions_helper import ActionsHelper

from .actions_helper_fixture import eventually, helper  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")


def test_the_api_client_drives_the_real_helper(helper):
    client = ActionsHelper(helper.socket)
    client.start("app", "say", "read x; echo got:$x", log=False)

    terminal = client.attach("app", "say", 80, 24)
    try:
        terminal.write(b"hi\r")
        seen = b""
        while b"got:hi" not in seen:
            chunk = terminal.read()
            assert chunk, f"stream ended before got:hi; saw {seen!r}"
            seen += chunk
    finally:
        terminal.close()

    run = eventually(lambda: (r := client.status("app").get("say")) and r["state"] == "exited" and r)
    assert run and run["exit_code"] == 0
    assert "got:hi" in client.output("app", "say")
