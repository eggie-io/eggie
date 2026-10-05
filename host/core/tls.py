from __future__ import annotations

import functools
import ssl

import certifi

# The frozen macOS app bundles python.org's OpenSSL, whose default CA path
# (/Library/Frameworks/Python.framework/.../etc/openssl/cert.pem) exists only
# where that Python is installed. Without a bundle of our own every HTTPS
# request fails with CERTIFICATE_VERIFY_FAILED on a user's Mac and works on a
# developer's. Imported here so PyInstaller's certifi hook ships cacert.pem.
CA_BUNDLE = certifi.where()


@functools.cache
def context() -> ssl.SSLContext:
    """The system's trust store plus certifi's, for every host HTTPS request.

    Added to rather than replacing the defaults: on Windows those are the
    certificate store, which holds the roots a corporate proxy re-signs with.
    """
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=CA_BUNDLE)
    return ctx
