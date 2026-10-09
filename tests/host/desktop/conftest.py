import logging

import pytest


@pytest.fixture(autouse=True)
def _forget_eggie_log_handlers():
    """run() and log.setup() leave handlers on the root logger; an open
    RotatingFileHandler on tmp_path would block its cleanup on Windows."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "eggie", False):
            root.removeHandler(handler)
            handler.close()
