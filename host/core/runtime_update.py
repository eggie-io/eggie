from __future__ import annotations

import base64
import json

from . import constants


def declare_supported(provider) -> bool:
    """Tell the VM which API numbers this host speaks."""
    body = json.dumps({"supported_api": sorted(constants.SUPPORTED_API)})
    encoded = base64.b64encode(body.encode()).decode("ascii")
    # Through a temp file: the boot unit may read it at any moment.
    tmp = f"{constants.HOST_JSON}.tmp"
    result = provider.exec(
        ["bash", "-lc", f"echo {encoded} | base64 -d > {tmp} && mv {tmp} {constants.HOST_JSON}"],
        root=True)
    return result.ok
