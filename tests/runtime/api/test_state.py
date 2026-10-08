import pytest

from eggie_api.core.state import State


def test_add_and_get_project(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_project("myproj", "/opt/eggie/projects/myproj", "myproj.d.io")
    row = s.get_project("myproj")
    assert row["guest_path"] == "/opt/eggie/projects/myproj"
    assert row["status"] == "stopped"


def test_projects_survive_reopen(tmp_path):
    db = tmp_path / "s.db"
    s = State(db)
    s.add_project("p1", "/g/p1", "p1.d.io", status="running")
    s.close()
    reopened = State(db)
    assert reopened.get_project("p1")["status"] == "running"


def test_remove_project_forgets_it(tmp_path):
    s = State(tmp_path / "s.db")
    s.add_project("p", "/g/p", "p.d.io")
    s.remove_project("p")
    assert s.get_project("p") is None


def test_state_serves_threads_other_than_the_one_that_opened_it(tmp_path):
    # sqlite3 refuses a connection used off its creating thread, and every
    # route runs in FastAPI's threadpool while jobs run on threads of their
    # own. Two writers on one row must also leave a value that was actually
    # written, not a half-applied one.
    import threading

    s = State(tmp_path / "s.db")
    s.add_project("p", "/g/p", "p.d.io")
    start = threading.Barrier(6)
    errors: list[BaseException] = []

    def hammer(n: int):
        try:
            start.wait(5)
            for _ in range(20):
                s.set_status("p", f"status-{n}")
                s.set_problem("p", f"code-{n}", f"message-{n}")
                assert s.get_project("p") is not None
                s.list_projects()
        except BaseException as e:  # noqa: BLE001 - reported, not swallowed
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not errors, f"state failed off-thread: {errors}"
    assert s.get_project("p")["status"] in {f"status-{n}" for n in range(6)}


def test_the_device_id_survives_reopening_the_database(tmp_path):
    first = State(tmp_path / "state.db").get_account()["device_id"]
    assert State(tmp_path / "state.db").get_account()["device_id"] == first


def test_update_account_refuses_a_field_it_does_not_know(tmp_path):
    state = State(tmp_path / "state.db")
    with pytest.raises(ValueError):
        state.update_account(**{"email=NULL; --": "x"})


def test_secrets_are_kept_per_project_and_never_listed_with_values(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.add_project("b", "/p/b", "d")
    s.set_secrets("a", {"KEY": "one", "OTHER": "two"}, at=10.0)
    s.set_secrets("b", {"KEY": "three"}, at=11.0)
    assert [r["name"] for r in s.secret_names("a")] == ["KEY", "OTHER"]
    assert all(set(r) == {"name", "updated_at"} for r in s.secret_names("a"))
    assert s.secret_values("a") == {"KEY": "one", "OTHER": "two"}
    assert s.secret_values("b") == {"KEY": "three"}


def test_setting_a_secret_again_replaces_it_and_stamps_the_change(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    s.set_secrets("a", {"KEY": "two"}, at=20.0)
    assert s.secret_values("a") == {"KEY": "two"}
    assert s.get_project("a")["secrets_changed_at"] == 20.0


def test_deleting_an_absent_secret_reports_it_and_stamps_nothing(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    assert s.delete_secret("a", "NOPE", at=20.0) is False
    assert s.get_project("a")["secrets_changed_at"] == 10.0
    assert s.delete_secret("a", "KEY", at=30.0) is True
    assert s.secret_values("a") == {}
    assert s.get_project("a")["secrets_changed_at"] == 30.0


def test_secrets_outlive_the_project_row_until_dropped(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    s.set_secrets("a", {"KEY": "one"}, at=10.0)
    s.remove_project("a")
    assert s.secret_values("a") == {"KEY": "one"}
    s.drop_secrets("a")
    assert s.secret_values("a") == {}


def test_without_an_explicit_time_the_change_is_stamped_at_write(tmp_path):
    import time
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    before = time.time()
    s.set_secrets("a", {"KEY": "v"})
    stamped = s.get_project("a")["secrets_changed_at"]
    assert before <= stamped <= time.time()
    assert s.delete_secret("a", "KEY") is True
    assert s.get_project("a")["secrets_changed_at"] >= stamped


def _state_with_project(tmp_path):
    s = State(tmp_path / "state.db")
    s.add_project("a", "/p/a", "d")
    return s


def test_a_request_shows_until_its_name_gets_a_value(tmp_path):
    s = _state_with_project(tmp_path)
    s.request_secret("a", "STRIPE_KEY", "Stripe dashboard")
    assert s.secret_requests("a") == [{"name": "STRIPE_KEY", "hint": "Stripe dashboard"}]
    s.set_secrets("a", {"STRIPE_KEY": "sk"})
    assert s.secret_requests("a") == []


def test_a_request_for_a_name_that_already_has_a_value_is_not_listed(tmp_path):
    s = _state_with_project(tmp_path)
    s.set_secrets("a", {"STRIPE_KEY": "sk"})
    s.request_secret("a", "STRIPE_KEY", "again")
    assert s.secret_requests("a") == []


def test_a_request_never_marks_secrets_changed(tmp_path):
    s = _state_with_project(tmp_path)
    s.request_secret("a", "STRIPE_KEY", "hint")
    assert s.get_project("a")["secrets_changed_at"] is None


def test_deleting_a_request_reports_whether_it_existed(tmp_path):
    s = _state_with_project(tmp_path)
    s.request_secret("a", "K", "hint")
    assert s.delete_request("a", "K") is True
    assert s.delete_request("a", "K") is False


def test_drop_secrets_clears_values_and_requests(tmp_path):
    s = _state_with_project(tmp_path)
    s.set_secrets("a", {"A": "1"})
    s.request_secret("a", "B", "hint")
    s.drop_secrets("a")
    assert s.secret_values("a") == {} and s.secret_requests("a") == []
