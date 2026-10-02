from __future__ import annotations

from host.desktop.settings import AUTOSTART_SET_ONCE, TRAY_NOTICE_SHOWN, Settings


def test_a_missing_file_reads_as_all_false(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    assert settings.get(AUTOSTART_SET_ONCE) is False
    assert settings.get(TRAY_NOTICE_SHOWN) is False


def test_a_corrupt_file_reads_as_all_false(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings(path).get(AUTOSTART_SET_ONCE) is False


def test_a_non_object_file_reads_as_all_false(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2]")
    assert Settings(path).get(AUTOSTART_SET_ONCE) is False


def test_setting_one_key_keeps_the_other(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    Settings(path).set(AUTOSTART_SET_ONCE, True)
    Settings(path).set(TRAY_NOTICE_SHOWN, True)
    reread = Settings(path)
    assert reread.get(AUTOSTART_SET_ONCE) is True
    assert reread.get(TRAY_NOTICE_SHOWN) is True
