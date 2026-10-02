import types

import pytest

from host.providers.mac_login import MainAppLoginItem

ENABLED, REQUIRES_APPROVAL = 1, 2


class FakeService:
    def __init__(self, status_after):
        self._status = 0
        self._status_after = status_after

    def registerAndReturnError_(self, error):
        self._status = self._status_after
        return True, None

    def status(self):
        return self._status


@pytest.fixture
def service_management(monkeypatch):
    # ServiceManagement exists only on macOS; these are its real enum values.
    module = types.SimpleNamespace(SMAppServiceStatusEnabled=ENABLED,
                                   SMAppServiceStatusRequiresApproval=REQUIRES_APPROVAL)
    monkeypatch.setitem(__import__("sys").modules, "ServiceManagement", module)


def _item(service):
    item = MainAppLoginItem()
    item._service = lambda: service
    return item


def test_a_login_item_waiting_for_approval_tells_the_user_where_to_allow_it(service_management):
    with pytest.raises(RuntimeError, match="System Settings → General → Login Items"):
        _item(FakeService(REQUIRES_APPROVAL)).register()


def test_an_approved_login_item_registers_quietly(service_management):
    _item(FakeService(ENABLED)).register()
