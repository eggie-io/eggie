import email.message
import io
import json
import urllib.error
import urllib.request
import urllib.response

import pytest

from eggie_api.infra.instance import InstanceClient, InstanceError, InstanceUnreachable


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.bodies = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if hasattr(request.data, "read"):
            body = b""
            while chunk := request.data.read(4):
                body += chunk
            self.bodies.append(body)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _Response(json.dumps(self.reply).encode() if self.reply is not None else b"")


def _http_error(status, code, message="m"):
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return urllib.error.HTTPError("https://k/api/transfers", status, "x", {}, io.BytesIO(body))


def test_open_sends_ticket_only_in_a_header():
    opener = FakeOpener({"push_id": "p1", "token": "t1"})
    out = InstanceClient(opener=opener).open("k.example", ticket="SECRET", size=7,
                                            data=True, replace="files")
    request = opener.requests[0]
    assert out == {"push_id": "p1", "token": "t1"}
    assert request.full_url == "https://k.example/api/transfers"
    assert "SECRET" not in request.full_url
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers["eggie-transfer-ticket"] == "SECRET"
    assert headers["eggie-transfer-size"] == "7"
    assert headers["eggie-transfer-data"] == "yes"
    assert headers["eggie-transfer-replace"] == "files"


def test_upload_reports_progress_and_sends_content_length(tmp_path):
    bundle = tmp_path / "b.tar"
    bundle.write_bytes(b"abcdefghij")
    opener = FakeOpener(None)
    seen = []
    InstanceClient(opener=opener).upload("k.example", "a/b", "TOK", bundle, seen.append)
    request = opener.requests[0]
    headers = {k.lower(): v for k, v in request.header_items()}
    assert request.full_url == "https://k.example/api/transfers/a%2Fb/bundle"
    assert headers["content-length"] == "10"
    assert headers["eggie-transfer-token"] == "TOK"
    assert opener.bodies == [b"abcdefghij"]
    assert seen == [4, 8, 10]


def test_ticket_refusal_is_a_instance_error_with_the_service_code():
    opener = FakeOpener(_http_error(403, "transfer_ticket_invalid", "no"))
    with pytest.raises(InstanceError) as raised:
        InstanceClient(opener=opener).open("k", ticket="t", size=1, data=False, replace="none")
    assert raised.value.code == "transfer_ticket_invalid"
    assert raised.value.status == 403


def test_old_instance_and_connection_errors_are_unreachable():
    client = lambda reply: InstanceClient(opener=FakeOpener(reply))
    call = lambda c: c.open("k", ticket="t", size=1, data=False, replace="none")
    with pytest.raises(InstanceUnreachable, match="older"):
        call(client(_http_error(401, "not_signed_in")))
    with pytest.raises(InstanceUnreachable, match="older"):
        call(client(_http_error(404, "not_found")))
    with pytest.raises(InstanceUnreachable, match="older"):
        call(client(_http_error(403, "forbidden_origin")))
    with pytest.raises(InstanceUnreachable):
        call(client(urllib.error.URLError("refused")))
    # Only open() reads a 401 as "older": elsewhere it is a real answer.
    with pytest.raises(InstanceError):
        client(_http_error(401, "not_signed_in")).status("k", "p", "t")


def test_a_redirect_is_unreachable_and_never_followed(monkeypatch):
    sent = []

    def redirect(_handler, request):
        sent.append(request.full_url)
        headers = email.message.Message()
        headers["Location"] = "https://elsewhere.example/steal"
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
        response.msg = "Found"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", redirect)
    with pytest.raises(InstanceUnreachable):
        InstanceClient().status("k.example", "p1", "TOK")
    assert sent == ["https://k.example/api/transfers/p1"]
