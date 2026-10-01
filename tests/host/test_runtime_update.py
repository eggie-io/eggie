import base64
import json
import re

from host.core import constants
from host.core.provider import Completed
from host.core.runtime_update import declare_supported


class FakeProvider:
    def __init__(self, ok=True):
        self.execs = []
        self._ok = ok

    def exec(self, argv, *, root=False):
        self.execs.append((argv, root))
        return Completed(0 if self._ok else 1, "", "")


def test_the_host_writes_its_supported_apis_where_the_boot_update_reads_them():
    p = FakeProvider()
    assert declare_supported(p) is True
    ((argv, root),) = p.execs
    assert root is True
    command = argv[2]
    written = json.loads(base64.b64decode(re.search(r"echo (\S+) \| base64 -d", command)[1]))
    assert written == {"supported_api": sorted(constants.SUPPORTED_API)}
    assert command.rstrip().endswith(constants.HOST_JSON)


def test_a_failed_write_is_reported_not_raised():
    assert declare_supported(FakeProvider(ok=False)) is False
