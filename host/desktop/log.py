"""The desktop app's log file.

The windowed Windows build has no console, so sys.stderr is None there and
a print to it is lost. The file next to settings.json is the one record a
user can send; stderr is mirrored only while one exists.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "eggie.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_file(root: Path) -> Path:
    return Path(root) / LOG_NAME


class _StderrHandler(logging.Handler):
    """Resolves sys.stderr on every record rather than at construction.

    pythonw hands the process no stderr at all, and pytest's capsys swaps
    the stream per test; a handler bound to one stream would raise in the
    first case and write to a closed file in the second.
    """

    def emit(self, record: logging.LogRecord) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


def setup(path: Path | None) -> None:
    """Route every logger to `path` (rotated) and to stderr when there is one.

    Repeatable: the handlers it installed last time are replaced, so a second
    call -- cli.setup and __main__.main both lead here -- never doubles lines.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "eggie", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(_FORMAT)
    handlers: list[logging.Handler] = [_StderrHandler()]
    unavailable = None
    if path is not None:
        # A read-only or missing install root costs the file, never the app:
        # this runs before the window exists, so an exception here is a
        # launch that dies with nothing on screen.
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2,
                                                encoding="utf-8"))
        except OSError as e:
            unavailable = e
    for handler in handlers:
        handler.eggie = True
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    if unavailable is not None:
        logging.getLogger(__name__).warning("log file %s unavailable: %r", path, unavailable)
