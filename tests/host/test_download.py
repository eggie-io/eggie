import hashlib
import io
import pytest

from host.core.images import Image, WSL_IMAGES
from host.core.download import fetch, sha256_of, ChecksumMismatch

PAYLOAD = b"ubuntu-rootfs-bytes"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class FakeOpener:
    """Serves PAYLOAD, honouring a start offset like an HTTP Range request."""

    def __init__(self, payload=PAYLOAD):
        self.calls = []
        self._payload = payload

    def __call__(self, url, start_byte):
        self.calls.append((url, start_byte))
        return io.BytesIO(self._payload[start_byte:]), len(self._payload)


def test_fetch_downloads_and_verifies(tmp_path):
    opener = FakeOpener()
    dest = tmp_path / "img.wsl"
    out = fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert out.read_bytes() == PAYLOAD
    assert opener.calls == [("http://x/img.wsl", 0)]


def test_fetch_reuses_a_cached_file_with_a_matching_hash(tmp_path):
    dest = tmp_path / "img.wsl"
    dest.write_bytes(PAYLOAD)
    opener = FakeOpener()
    fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert opener.calls == [], "a valid cached file must not be re-downloaded"


def test_fetch_resumes_from_a_partial_file(tmp_path):
    dest = tmp_path / "img.wsl"
    dest.with_suffix(".wsl.part").write_bytes(PAYLOAD[:5])
    opener = FakeOpener()
    fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert opener.calls == [("http://x/img.wsl", 5)]
    assert dest.read_bytes() == PAYLOAD


def test_fetch_deletes_the_file_on_checksum_mismatch(tmp_path):
    dest = tmp_path / "img.wsl"
    opener = FakeOpener(payload=b"corrupted")
    with pytest.raises(ChecksumMismatch):
        fetch(Image("http://x/img.wsl", DIGEST), dest, opener=opener)
    assert not dest.exists(), "a bad download must not be left on disk to be reused"


def test_wsl_image_table_has_both_architectures():
    assert set(WSL_IMAGES) == {"amd64", "arm64"}
    assert all(len(i.sha256) == 64 for i in WSL_IMAGES.values())


def test_network_failures_become_download_failed_but_a_full_disk_does_not(tmp_path):
    import urllib.error
    from host.core.download import DownloadFailed, fetch
    from host.core.images import Image
    image = Image("https://example.invalid/x", "0" * 64)

    for error in (urllib.error.URLError("no route"), TimeoutError("slow"),
                  ConnectionResetError("reset")):
        def opener(url, start, error=error):
            raise error
        with pytest.raises(DownloadFailed) as e:
            fetch(image, tmp_path / "x", opener=opener)
        assert e.value.code == "download_failed"

    def full_disk(url, start):
        raise OSError(28, "No space left on device")
    with pytest.raises(OSError) as e:
        fetch(image, tmp_path / "y", opener=full_disk)
    assert not isinstance(e.value, DownloadFailed)


def test_a_connection_dropped_mid_body_is_download_failed(tmp_path):
    import http.client
    from host.core.download import fetch
    from host.core.events import error_code
    from host.core.images import Image

    class Dropping:
        def read(self, n):
            raise http.client.IncompleteRead(b"")

    with pytest.raises(Exception) as e:
        fetch(Image("https://example.invalid/x", "0" * 64), tmp_path / "x",
              opener=lambda url, start: (Dropping(), 10))
    assert error_code(e.value) == "download_failed"
