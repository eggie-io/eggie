import io
import os
import urllib.error

import pytest

from tests.runtime.cli.loader import load

cli = load()

NET_DEV_HEADER = ("Inter-|   Receive                            |  Transmit\n"
                  " face |bytes    packets errs drop fifo frame compressed multicast|bytes\n")
LOOPBACK = "    lo:       0       0    0    0    0     0          0         0        0\n"
ETHERNET = "  eth0: 1234567    8910    0    0    0     0          0         0   456789\n"


class _Failing:
    def __init__(self, reason: OSError):
        self._reason = reason

    def open(self, request, timeout=None):
        raise urllib.error.URLError(self._reason)


REFUSED = ConnectionRefusedError(111, "Connection refused")


def _status_error(tmp_path, monkeypatch, *, interfaces=LOOPBACK + ETHERNET,
                  reason: OSError = REFUSED) -> str:
    net_dev = tmp_path / "net_dev"
    net_dev.write_text(NET_DEV_HEADER + interfaces)
    monkeypatch.setattr(cli.api, "NET_DEV", str(net_dev))
    root = tmp_path / "projects"
    (root / "blog").mkdir(parents=True)
    out, err = io.StringIO(), io.StringIO()
    env = cli.Env(root=root, cwd=root / "blog",
                  client=lambda: cli.ApiClient("t", opener=_Failing(reason)),
                  out=out, err=err)
    assert cli.main(["status"], env) == 1
    return err.getvalue()


SITE_OVERRIDE = "-f /opt/eggie/runtime/stack.site.yml"


def test_a_stopped_service_is_named_with_the_command_that_starts_it(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.api, "GUEST_SITE_ENV", str(tmp_path / "site.env"))
    message = _status_error(tmp_path, monkeypatch)
    assert "not answering" in message
    assert "sudo /usr/bin/docker compose -f /opt/eggie/stack.yml up -d" in message
    assert SITE_OVERRIDE not in cli.restart_api()


def test_on_a_site_the_compose_hint_keeps_the_site_override(tmp_path, monkeypatch):
    # Without it compose recreates Traefik off 80/443 and TLS.
    site_env = tmp_path / "site.env"
    site_env.write_text("EGGIE_SITE_HOST=foo.eggie.io\n")
    monkeypatch.setattr(cli.api, "GUEST_SITE_ENV", str(site_env))
    expected = f"sudo /usr/bin/docker compose -f /opt/eggie/stack.yml {SITE_OVERRIDE} up -d"
    assert expected in _status_error(tmp_path, monkeypatch)
    assert cli.restart_api() == f"{expected} --force-recreate api"


def test_a_loopback_only_network_namespace_reads_as_a_sandbox(tmp_path, monkeypatch):
    # Codex's bwrap sandbox: 127.0.0.1 there is an empty loopback, so the
    # refusal says nothing about the service, and a restart hint would mislead.
    message = _status_error(tmp_path, monkeypatch, interfaces=LOOPBACK)
    assert "sandbox" in message
    assert "not answering" not in message
    assert "docker compose" not in message


def test_a_socket_the_sandbox_refuses_reads_as_a_sandbox(tmp_path, monkeypatch):
    message = _status_error(tmp_path, monkeypatch,
                            reason=PermissionError(1, "Operation not permitted"))
    assert "sandbox" in message
    assert "docker compose" not in message


def test_a_vm_without_a_token_says_eggie_is_not_set_up(tmp_path):
    with pytest.raises(cli.EggieError, match="not set up"):
        cli.read_token(tmp_path / "api.token")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_an_unreadable_token_points_at_the_docker_group(tmp_path):
    token = tmp_path / "api.token"
    token.write_text("secret")
    token.chmod(0)
    with pytest.raises(cli.EggieError, match="docker group"):
        cli.read_token(token)
