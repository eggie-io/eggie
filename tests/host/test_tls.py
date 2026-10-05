"""Every HTTPS request the host makes must verify against a CA bundle it carries.

The frozen macOS app bundles a python.org OpenSSL whose compiled-in CA path is
/Library/Frameworks/Python.framework/.../etc/openssl/cert.pem -- a file that
exists only on a Mac where that Python is installed and its "Install
Certificates" script was run. Everywhere else the Lima download failed with
CERTIFICATE_VERIFY_FAILED, shown to the user as "Lima could not be downloaded".
A dev checkout never sees it: Homebrew's OpenSSL finds its own bundle.
"""
from __future__ import annotations

import io

import certifi

from host.core import app_update, download, tls


def test_the_context_trusts_certifi_even_with_no_system_bundle(monkeypatch):
    # The frozen app's situation: OpenSSL's default locations point nowhere.
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/cert.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/certs")
    tls.context.cache_clear()
    try:
        ctx = tls.context()
        assert ctx.get_ca_certs(), "no CA certificates loaded -- every HTTPS call fails"
        assert ctx.check_hostname
    finally:
        tls.context.cache_clear()


def test_the_bundle_comes_from_certifi():
    # PyInstaller's certifi hook ships cacert.pem only if certifi is imported.
    assert tls.CA_BUNDLE == certifi.where()


class _Response(io.BytesIO):
    headers = {"Content-Length": "0"}
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _record_urlopen(monkeypatch, seen):
    def fake_urlopen(request, *, timeout, context=None):
        seen.append(context)
        return _Response(b"[]")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_the_download_opener_uses_the_bundled_context(monkeypatch):
    seen = []
    _record_urlopen(monkeypatch, seen)
    download._default_opener("https://example.invalid/x", 0)
    assert seen == [tls.context()]


def test_the_update_check_uses_the_bundled_context(monkeypatch):
    seen = []
    _record_urlopen(monkeypatch, seen)
    app_update._fetch("https://example.invalid/releases")
    assert seen == [tls.context()]
