import http.client
import io
import json
import urllib.error

import pytest

from eggie_api.infra.cloud import Cloud, CloudError, CloudUnavailable


class _Response:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Stands in for urllib's urlopen: replays (status, body) or raises."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, body, *rest = reply
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error",
                                         rest[0] if rest else {}, io.BytesIO(body))
        return _Response(status, body)


def _error(code, message="m"):
    return json.dumps({"error": {"code": code, "message": message}}).encode()


def test_the_service_error_body_becomes_a_cloud_error_with_its_code():
    cloud = Cloud("https://svc/api", opener=Opener((400, _error("slow_down"))))
    with pytest.raises(CloudError) as raised:
        cloud.device_token("dc")
    assert (raised.value.code, raised.value.status) == ("slow_down", 400)


def test_a_refused_connection_is_unavailable():
    cloud = Cloud("https://svc/api", opener=Opener(
        urllib.error.URLError(ConnectionRefusedError())))
    with pytest.raises(CloudUnavailable):
        cloud.me("t")


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_page_instead_of_the_service_is_unavailable(status):
    cloud = Cloud("https://svc/api", opener=Opener((status, b"<html>bad gateway</html>")))
    with pytest.raises(CloudUnavailable):
        cloud.me("t")


@pytest.mark.parametrize("status,code", [(502, "tunnel_provider_error"),
                                         (503, "public_urls_disabled")])
def test_a_service_error_body_on_a_gateway_status_stays_the_services_error(status, code):
    cloud = Cloud("https://svc/api", opener=Opener((status, _error(code))))
    with pytest.raises(CloudError) as raised:
        cloud.create_public_url("t", "c1", [{"local_hostname": "b.d.io", "service": "web"}],
                                "http://traefik:39080")
    assert (raised.value.code, raised.value.status) == (code, status)


def test_a_404_without_an_error_body_keeps_its_status():
    cloud = Cloud("https://svc/api", opener=Opener((404, b"Not Found")))
    with pytest.raises(CloudError) as raised:
        cloud.delete_project("t", "abc")
    assert raised.value.status == 404


def test_create_sends_the_token_the_name_and_the_client_ref():
    opener = Opener((201, json.dumps({"id": "u-1"}).encode()))
    out = Cloud("https://svc/api/", opener=opener).create_project("tok", "blog", "dev/blog")

    assert out == {"id": "u-1"}
    request = opener.requests[0]
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/projects")
    assert request.get_header("Authorization") == "Bearer tok"
    assert json.loads(request.data) == {"name": "blog", "client_ref": "dev/blog"}


def test_a_2xx_response_with_non_json_body_reports_the_actual_status():
    cloud = Cloud("https://svc/api", opener=Opener((201, b"Created")))
    with pytest.raises(CloudError) as raised:
        cloud.create_project("t", "n", "r")
    assert (raised.value.code, raised.value.status) == ("bad_response", 201)


def test_a_truncated_body_is_unavailable_not_a_bare_exception():
    class _BrokenResponse:
        status = 200

        def read(self):
            raise http.client.IncompleteRead(b"")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _BrokenOpener:
        def __call__(self, request, timeout):
            return _BrokenResponse()

    cloud = Cloud("https://svc/api", opener=_BrokenOpener())
    with pytest.raises(CloudUnavailable):
        cloud.me("t")


def test_turning_a_public_url_on_sends_the_routes_and_origin():
    opener = Opener((201, json.dumps({"urls": []}).encode()))
    cloud = Cloud("https://svc/api", opener=opener)
    routes = [{"local_hostname": "blog.d.io", "service": "web"},
              {"local_hostname": "api.blog.d.io", "service": "api_v2"}]

    cloud.create_public_url("t", "c/1", routes, "http://traefik:39080")

    request = opener.requests[0]
    assert (request.get_method(), request.full_url) == (
        "POST", "https://svc/api/v1/tunnels/projects/c%2F1/url")
    assert json.loads(request.data) == {"origin": "http://traefik:39080",
                                        "routes": routes}
    assert request.get_header("Authorization") == "Bearer t"


def test_a_refused_public_url_keeps_the_services_code():
    cloud = Cloud("https://svc/api", opener=Opener(
        (409, _error("public_url_active", "one is on"))))
    with pytest.raises(CloudError) as raised:
        cloud.create_public_url("t", "c1", [{"local_hostname": "blog.d.io",
                                             "service": "web"}],
                                "http://traefik:39080")
    assert (raised.value.code, raised.value.status) == ("public_url_active", 409)


def test_releasing_a_public_url_accepts_an_empty_204():
    opener = Opener((204, b""))
    cloud = Cloud("https://svc/api", opener=opener)

    assert cloud.release_public_url("t", "c1") is None
    assert opener.requests[0].get_method() == "DELETE"


def test_the_certificate_is_fetched_from_the_instance_route_with_the_token():
    opener = Opener((200, b'{"names": [], "certificate": "c", "private_key": "k", "not_after": "x"}'))
    out = Cloud("https://svc/api", opener=opener).instance_certificate("at-1")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("GET", "https://svc/api/v1/instances/me/certificate")
    assert request.get_header("Authorization") == "Bearer at-1"
    assert out["private_key"] == "k"


def test_a_handoff_code_is_redeemed_with_the_token_and_the_code():
    opener = Opener((200, b'{"ok": true}'))
    Cloud("https://svc/api", opener=opener).redeem_instance_handoff("at-1", "k3x9")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/instances/handoff/redeem")
    assert json.loads(request.data) == {"code": "k3x9"}
    assert request.get_header("Authorization") == "Bearer at-1"


def test_rate_limit_error_carries_retry_after():
    opener = Opener((429, _error("transfer_rate_limited"), {"Retry-After": "120"}))
    with pytest.raises(CloudError) as raised:
        Cloud("https://svc/api", opener=opener).mint_transfer_ticket("t", "k1", "p1")
    assert (raised.value.code, raised.value.retry_after) == ("transfer_rate_limited", 120)


def test_an_unparseable_retry_after_is_ignored():
    opener = Opener((429, _error("transfer_rate_limited"),
                     {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
    with pytest.raises(CloudError) as raised:
        Cloud("https://svc/api", opener=opener).mint_transfer_ticket("t", "k1", "p1")
    assert raised.value.retry_after is None


def test_mint_sends_instance_and_project():
    opener = Opener((200, b'{"ticket": "tk"}'))
    out = Cloud("https://svc/api", opener=opener).mint_transfer_ticket("at-1", "k1", "p1")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/transfers/tickets")
    assert json.loads(request.data) == {"instance_id": "k1", "project_id": "p1"}
    assert request.get_header("Authorization") == "Bearer at-1"
    assert out == {"ticket": "tk"}


def test_redeem_sends_ticket_in_the_body_not_the_url():
    opener = Opener((200, b'{"grant": 1}'))
    Cloud("https://svc/api", opener=opener).redeem_transfer_ticket("at-1", "secret-ticket")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/transfers/tickets/redeem")
    assert json.loads(request.data) == {"ticket": "secret-ticket"}
    assert "secret-ticket" not in request.full_url


def test_complete_posts_no_body():
    opener = Opener((204, b""))
    out = Cloud("https://svc/api", opener=opener).complete_transfer("at-1", "tr/1")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/transfers/tr%2F1/complete")
    assert request.data is None and out is None


def test_targets_are_listed_with_the_token():
    opener = Opener((200, b'{"targets": []}'))
    Cloud("https://svc/api", opener=opener).transfer_targets("at-1")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("GET", "https://svc/api/v1/transfers/targets")
