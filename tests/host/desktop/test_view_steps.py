"""The install panel's rows.

The design board is a macOS mock and shows seven. default_steps returns seven
on Lima and nine on WSL2, so a hard-coded seven puts "Step 4 of 7" on a
machine running nine steps and binds the progress bar to the wrong row.
"""
from __future__ import annotations

from host.core.install import Step
from host.desktop.view import rows_for, step_label


def _step(name, **kwargs):
    return Step(name, lambda: None, **kwargs)


def test_rows_follow_the_list_the_factory_returned():
    windows = [_step("preflight"), _step("remediate"), _step("reboot_gate"),
               _step("fetch_image", progress=True), _step("create_vm"),
               _step("bootstrap"), _step("connect"), _step("verify"),
               _step("finish")]
    rows = rows_for(windows)
    assert [r["name"] for r in rows] == [s.name for s in windows]
    assert len(rows) == 9


def test_a_mac_list_is_the_seven_the_board_draws():
    mac = [_step("preflight"),
           _step("install_runtime", label="Installing Lima 2.2.0", progress=True),
           _step("create_vm"), _step("bootstrap"), _step("connect"),
           _step("verify"), _step("finish")]
    assert len(rows_for(mac)) == 7


def test_only_the_download_row_carries_a_progress_bar():
    rows = rows_for([_step("preflight"), _step("fetch_image", progress=True)])
    assert [r["progress"] for r in rows] == [False, True]


def test_a_provider_that_names_its_own_step_wins():
    # "Installing Lima 2.2.0" is Lima's sentence -- the version in it is the
    # provider's fact. A label table copy would go stale on the next pin.
    assert step_label(_step("install_runtime", label="Installing Lima 2.2.0")) \
        == "Installing Lima 2.2.0"


def test_a_step_without_a_label_uses_the_table():
    assert step_label(_step("bootstrap")) == "Installing Eggie"


def test_an_unknown_step_falls_back_to_its_name():
    assert step_label(_step("brand_new_step")) == "brand_new_step"
