import ipaddress

import pytest

from eggie_api.core import health
from eggie_api.core.detect import WebSpec
from eggie_api.core.exec import Completed
from eggie_api.core.project import Project

# Real `cat /proc/net/tcp` output, trimmed: the header line every reader has to
# skip, then one LISTEN row (st 0A). Port 80 is 0x0050.
HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
          "retrnsmt   uid  timeout inode\n")
LOOPBACK_80 = HEADER + (
    "   0: 0100007F:0050 00000000:0000 0A 00000000:00000000 00:00000000 "
    "00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n")
WILDCARD_80 = HEADER + (
    "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 "
    "00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n")
# tcp6: four little-endian words. ::1 is the last word only.
LOOPBACK6_80 = HEADER + (
    "   0: 00000000000000000000000001000000:0050 "
    "00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 "
    "00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n")
WILDCARD6_80 = HEADER + (
    "   0: 00000000000000000000000000000000:0050 "
    "00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 "
    "00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n")
# A connection to somewhere else (st 01, ESTABLISHED) must never read as a
# listening socket.
ESTABLISHED_ONLY = HEADER + (
    "   0: 0100007F:0050 0100007F:1F90 01 00000000:00000000 00:00000000 "
    "00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0\n")

PROJECT = Project(id="blog", webs=[WebSpec(service="web", port=80)])
# The project's directory, which the API layer resolves from
# config.projects_root and hands down -- health never derives it itself.
PROJECT_DIR = "/srv/projects/blog"


class Clock:
    """Fake monotonic clock; only `sleep` moves it, so a 30 s window costs
    nothing in real time."""

    def __init__(self):
        self.now = 0.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


class Probe:
    """Replays scripted statuses; the last one repeats forever. An Exception in
    the script is raised, standing in for a connection failure."""

    def __init__(self, *statuses):
        self.script = list(statuses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, host: str):
        self.calls.append((url, host))
        answer = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeRunner:
    def __init__(self, proc_net=LOOPBACK_80, container_id="c0ffee1234"):
        self.calls: list[list[str]] = []
        self.container_id = Completed(0, f"{container_id}\n", "")
        self.proc_net = (proc_net if isinstance(proc_net, Completed)
                         else Completed(0, proc_net, ""))

    def exec(self, argv, *, root=False, env=None):
        self.calls.append(argv)
        if "-q" in argv:
            return self.container_id
        if argv[1] == "exec":
            return self.proc_net
        return Completed(0, "", "")

    def inspected(self) -> bool:
        return any(a[1] == "exec" for a in self.calls)


def diagnose(runner, probe, clock, **kwargs):
    return health.diagnose(runner, PROJECT, "test.local",
                           directory=PROJECT_DIR,
                           edge_port=41080, traefik_host="traefik",
                           http_probe=probe, sleep=clock.sleep,
                           clock=clock.monotonic, **kwargs)


def test_a_persistent_502_is_diagnosed_as_the_wrong_bind_address():
    # The failure this module exists for: the container is up, so classify()
    # calls it healthy, and the URL answers a proxy error with no cause named.
    clock, probe, runner = Clock(), Probe(502), FakeRunner()
    result = diagnose(runner, probe, clock)

    assert result is not None
    assert result.code == health.BOUND_TO_LOOPBACK
    assert "0.0.0.0" in result.message and "127.0.0.1" in result.message
    assert "web" in result.message, "the message must name the service to fix"
    assert clock.slept >= health.READY_TIMEOUT, \
        "a diagnosis must never come from a single request"


def test_a_slow_but_healthy_stack_is_not_misreported():
    # Traefik publishes a router a beat after the container starts, so the
    # first requests 404 on a stack that is perfectly fine. A false "your app
    # is bound to the wrong address" here is worse than no diagnosis at all.
    clock, probe, runner = Clock(), Probe(404, 404, 404, 200), FakeRunner()

    assert diagnose(runner, probe, clock) is None
    assert not runner.inspected(), \
        "a healthy stack must never be inspected for a fault"
    assert clock.slept < health.READY_TIMEOUT, "it stopped as soon as it answered"


def test_a_502_that_clears_inside_the_window_is_not_a_fault():
    # A container still booting behind a live router answers 502 for a moment.
    clock, probe, runner = Clock(), Probe(502, 502, 200), FakeRunner()

    assert diagnose(runner, probe, clock) is None
    assert not runner.inspected()


def test_the_probe_reaches_traefik_by_name_with_the_projects_host_header():
    # The real URL resolves to 127.0.0.1, which inside the API container is
    # the API itself -- probing it would report nonsense for every project.
    clock, probe = Clock(), Probe(200)
    diagnose(FakeRunner(), probe, clock)

    url, host = probe.calls[0]
    assert url == "http://traefik:41080/"
    assert host == "blog.test.local"


def test_a_wildcard_listener_is_not_blamed_on_the_bind_address():
    clock, probe = Clock(), Probe(502)
    result = diagnose(FakeRunner(proc_net=WILDCARD_80), probe, clock)

    assert result is not None
    assert result.code == health.SERVICE_UNREACHABLE


def test_an_ipv6_loopback_listener_is_caught_too():
    clock, probe = Clock(), Probe(502)
    result = diagnose(FakeRunner(proc_net=LOOPBACK6_80), probe, clock)
    assert result.code == health.BOUND_TO_LOOPBACK


def test_an_outbound_connection_is_not_read_as_a_listening_socket():
    # Only st 0A is LISTEN; an established connection from 127.0.0.1 must not
    # be mistaken for the app's own socket.
    clock, probe = Clock(), Probe(502)
    result = diagnose(FakeRunner(proc_net=ESTABLISHED_ONLY), probe, clock)
    assert result.code == health.SERVICE_UNREACHABLE


def test_a_container_that_cannot_be_inspected_is_hedged_not_blamed():
    # A slim image may have no `cat`, and an exited container refuses exec.
    # Reporting a cause that was never confirmed is how a user is sent to fix
    # the wrong thing.
    clock, probe = Clock(), Probe(502)
    runner = FakeRunner(proc_net=Completed(126, "", "exec: \"cat\": not found"))
    result = diagnose(runner, probe, clock)

    assert result.code == health.SERVICE_UNREACHABLE


def test_a_missing_container_id_still_answers_instead_of_raising():
    clock, probe = Clock(), Probe(502)
    runner = FakeRunner()
    runner.container_id = Completed(1, "", "no such service: web")
    result = diagnose(runner, probe, clock)

    assert result.code == health.SERVICE_UNREACHABLE
    assert not runner.inspected()


def test_traefik_being_unreachable_is_never_blamed_on_the_project():
    # Every probe raising means the API cannot reach the proxy at all. That
    # is not the user's compose file, and stamping every project with a
    # problem would bury the real one.
    clock, probe, runner = Clock(), Probe(ConnectionRefusedError("no route")), FakeRunner()

    assert diagnose(runner, probe, clock) is None


def test_an_application_error_is_the_apps_answer_not_a_routing_fault():
    # A 500 from the app itself means Traefik reached the container.
    clock, probe, runner = Clock(), Probe(500), FakeRunner()

    assert diagnose(runner, probe, clock) is None
    assert len(probe.calls) == 1, "an answered request needs no retry window"


def test_a_project_with_no_web_service_is_never_probed():
    clock, probe = Clock(), Probe(502)
    result = health.diagnose(FakeRunner(), Project(id="worker", webs=[]),
                             "test.local", directory=PROJECT_DIR,
                             http_probe=probe,
                             sleep=clock.sleep, clock=clock.monotonic)
    assert result is None
    assert probe.calls == []


@pytest.mark.parametrize("field, expected", [
    ("0100007F:0050", ("127.0.0.1", 80)),
    ("00000000:1F90", ("0.0.0.0", 8080)),
    ("00000000000000000000000001000000:0050", ("::1", 80)),
    ("00000000000000000000000000000000:1F90", ("::", 8080)),
    ("0000000000000000FFFF00000100007F:0050", ("::ffff:7f00:1", 80)),
])
def test_proc_net_addresses_decode_byte_order_correctly(field, expected):
    # The words are little-endian; get this wrong and 127.0.0.1 reads as
    # 1.0.0.127 and no bind-address fault is ever confirmed.
    address, port = health._decode_address(field)
    # Compared as addresses: Python 3.12.14 prints an IPv4-mapped address
    # as ::ffff:127.0.0.1 where earlier patch releases print ::ffff:7f00:1.
    assert (address, port) == (ipaddress.ip_address(expected[0]), expected[1])


def test_an_ipv6_service_on_all_interfaces_is_not_read_as_loopback():
    # The false positive that would matter: `::` differs from `::1` by one
    # byte in the word this parser reverses.
    clock, probe = Clock(), Probe(502)
    listeners = health.parse_listeners(WILDCARD6_80)

    assert [listener.is_loopback for listener in listeners] == [False]
    assert diagnose(FakeRunner(proc_net=WILDCARD6_80), probe, clock).code == \
        health.SERVICE_UNREACHABLE


def test_answers_asks_once_and_never_waits_out_a_window():
    # This one runs inside a read the CLI is blocked on; a retry window here
    # would make every `eggie status` on a broken project take 30 seconds.
    probe = Probe(502)
    assert health.answers(PROJECT, "test.local", edge_port=41080,
                          http_probe=probe) is False
    assert len(probe.calls) == 1

    answering = Probe(200)
    assert health.answers(PROJECT, "test.local", edge_port=41080,
                          http_probe=answering) is True


def test_answers_is_false_when_nothing_answers_at_all():
    # No answer is not evidence the fault is gone, and this result is what
    # clears a stored diagnosis.
    assert health.answers(PROJECT, "test.local",
                          http_probe=Probe(ConnectionRefusedError("x"))) is False
    assert health.answers(PROJECT, "test.local", http_probe=Probe(404)) is False
