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
    logging.getLogger("host.providers.tray_win").warning("tray callback failed: boom")
    assert "tray callback failed: boom" in path.read_text()


def test_setting_up_twice_writes_each_line_once(tmp_path):
    path = tmp_path / "eggie.log"
    log.setup(path)
    log.setup(path)
    logging.getLogger("host.desktop.api").warning("once")
    assert path.read_text().count("once") == 1


def test_an_unwritable_log_path_falls_back_to_stderr(tmp_path, capsys):
    """setup() is the first thing run() does; a read-only install root must
    cost the file, not the app."""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    log.setup(blocker / "eggie.log")
    logging.getLogger("host.desktop.api").warning("still heard")
    assert "still heard" in capsys.readouterr().err
