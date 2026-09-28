"""The host's check that the API in the VM speaks its own API version and
accepts its token -- turned into one sentence before the slow smoke test."""
from __future__ import annotations

import pytest

from host.client import ApiError, ApiUnavailableError
from host.core import constants
from host.core.bootstrap import BootstrapError
from host.core.install import ApiIncompatible, ApiNotAccepted, connect_step


class FakeClient:
    """`health` is the /health body. `versions` answers /version one call at a
    time, the last repeating; an exception entry is raised instead."""

    def __init__(self, *versions, health=None):
        self._health = {"status": "ok", "api": 1} if health is None else health
        self._versions = list(versions) or ["0.1.0"]

    def health(self) -> dict:
        return self._health

    def version(self) -> str:
        answer = (self._versions.pop(0) if len(self._versions) > 1
                  else self._versions[0])
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _refused(code: str) -> ApiError:
    return ApiError(code, "missing or invalid bearer token", 401)


UNSUPPORTED = max(constants.SUPPORTED_API) + 1


def test_an_api_on_another_version_with_no_way_to_update_is_reported_and_never_repaired():
    reconnects = []
    with pytest.raises(ApiIncompatible) as excinfo:
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     reconnect=lambda: reconnects.append("reconnect"))
    assert str(UNSUPPORTED) in str(excinfo.value), "support needs the number"
    assert reconnects == [], "reinstalling the same runtime cannot change its API"


def test_an_api_on_another_version_is_updated_once():
    updates = []

    def update():
        updates.append("update")
        return FakeClient("0.2.0")

    message = connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                           update=update)
    assert updates == ["update"]
    assert "updated" in message


def test_an_api_still_incompatible_after_the_update_is_reported():
    with pytest.raises(ApiIncompatible):
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     update=lambda: FakeClient(health={"status": "ok", "api": UNSUPPORTED}))


def test_a_failed_update_carries_the_installers_own_words():
    def update():
        raise BootstrapError("no runtime-v* release in repo speaks api 1")

    with pytest.raises(ApiIncompatible) as excinfo:
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     update=update)
    assert "speaks api 1" in str(excinfo.value)


def test_a_supported_api_is_never_updated():
    updates = []
    assert connect_step(None, client=FakeClient("0.1.0"),
                        update=lambda: updates.append("update")) is None
    assert updates == []


def test_an_api_from_before_the_api_number_counts_as_api_1():
    assert connect_step(None, client=FakeClient(health={"status": "ok"})) is None


def test_a_matching_api_that_accepts_this_host_is_left_alone():
    reconnects = []
    assert connect_step(None, client=FakeClient("0.1.0"),
                        reconnect=lambda: reconnects.append("reconnect")) is None
    assert reconnects == []


def test_an_api_refusing_this_host_is_reconnected_once():
    # /health skips the token check, so restart:always never restarts an API
    # that refuses every other route; only a repair recreates it.
    reconnects = []

    def reconnect():
        reconnects.append("reconnect")
        return FakeClient("0.1.0")

    message = connect_step(None, client=FakeClient(_refused("api_unconfigured")),
                           reconnect=reconnect)
    assert reconnects == ["reconnect"]
    assert "reconnected" in message


def test_an_api_still_refusing_after_the_reconnect_says_what_was_wrong():
    with pytest.raises(ApiNotAccepted) as excinfo:
        connect_step(None, client=FakeClient(_refused("unauthorized")),
                     reconnect=lambda: FakeClient(_refused("unauthorized")))
    assert "did not accept this computer" in str(excinfo.value)


def test_an_error_that_is_not_about_the_token_is_not_repaired():
    reconnects = []
    with pytest.raises(ApiError):
        connect_step(None, client=FakeClient(ApiError("internal", "boom", 500)),
                     reconnect=lambda: reconnects.append("reconnect"))
    assert reconnects == []


def test_the_api_restarting_after_the_reconnect_is_waited_out():
    # The recreated container is not serving yet when the repair returns.
    slept = []
    restarted = FakeClient(ApiUnavailableError("connection refused"), "0.1.0")
    message = connect_step(None, client=FakeClient(_refused("unauthorized")),
                           reconnect=lambda: restarted, sleep=slept.append)
    assert slept, "the restart must be waited out, not reported as a failure"
    assert "reconnected" in message
