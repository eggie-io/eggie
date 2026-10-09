"""The log file is the only record a windowed build leaves behind.

The Windows build freezes the GUI with console=False, so sys.stderr is None
there; anything that assumes a stderr exists fails silently on exactly the
machines whose failures matter.
"""
from __future__ import annotations

import logging
import sys

from host.desktop import log


def test_a_warning_reaches_the_file_when_there_is_no_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    path = tmp_path / "eggie.log"
    log.setup(path)
    try:
        logging.getLogger("host.providers.tray_win").warning("tray callback failed: boom")
    finally:
        log.setup(None)
    assert "tray callback failed: boom" in path.read_text()


def test_setting_up_twice_writes_each_line_once(tmp_path):
    path = tmp_path / "eggie.log"
    log.setup(path)
    log.setup(path)
    try:
        logging.getLogger("host.desktop.api").warning("once")
    finally:
        log.setup(None)
    assert path.read_text().count("once") == 1
