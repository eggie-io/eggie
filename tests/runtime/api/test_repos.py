import pytest

from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos


def test_add_and_get_project(tmp_path):
    s = Repos.open(Database(tmp_path / "s.db"))
    s.projects.add("myproj", "/opt/eggie/projects/myproj", "myproj.d.io")
    row = s.projects.get("myproj")
    assert row["guest_path"] == "/opt/eggie/projects/myproj"
    assert row["status"] == "stopped"


def test_projects_survive_reopen(tmp_path):
    db = tmp_path / "s.db"
    s = Repos.open(Database(db))
    s.projects.add("p1", "/g/p1", "p1.d.io", status="running")
    s.db.close()
    reopened = Repos.open(Database(db))
    assert reopened.projects.get("p1")["status"] == "running"


def test_remove_project_forgets_it(tmp_path):
    s = Repos.open(Database(tmp_path / "s.db"))
    s.projects.add("p", "/g/p", "p.d.io")
    s.projects.remove("p")
    assert s.projects.get("p") is None


def test_repos_serve_threads_other_than_the_one_that_opened_the_database(tmp_path):
    # sqlite3 refuses a connection used off its creating thread, and every
    # route runs in FastAPI's threadpool while jobs run on threads of their
    # own. Two writers on one row must also leave a value that was actually
    # written, not a half-applied one.
    import threading

    s = Repos.open(Database(tmp_path / "s.db"))
    s.projects.add("p", "/g/p", "p.d.io")
    start = threading.Barrier(6)
    errors: list[BaseException] = []

    def hammer(n: int):
        try:
            start.wait(5)
            for _ in range(20):
                s.projects.set_status("p", f"status-{n}")
                s.projects.set_problem("p", f"code-{n}", f"message-{n}")
                assert s.projects.get("p") is not None
                s.projects.list()
        except BaseException as e:  # noqa: BLE001 - reported, not swallowed
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not errors, f"state failed off-thread: {errors}"
    assert s.projects.get("p")["status"] in {f"status-{n}" for n in range(6)}


def test_the_device_id_survives_reopening_the_database(tmp_path):
    first = Repos.open(Database(tmp_path / "state.db")).account.get()["device_id"]
    assert Repos.open(Database(tmp_path / "state.db")).account.get()["device_id"] == first


def test_update_account_refuses_a_field_it_does_not_know(tmp_path):
    state = Repos.open(Database(tmp_path / "state.db"))
    with pytest.raises(ValueError):
        state.account.update(**{"email=NULL; --": "x"})


def test_secrets_are_kept_per_project_and_never_listed_with_values(tmp_path):
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    s.projects.add("b", "/p/b", "d")
    s.secrets.set("a", {"KEY": "one", "OTHER": "two"}, at=10.0)
    s.secrets.set("b", {"KEY": "three"}, at=11.0)
    assert [r["name"] for r in s.secrets.names("a")] == ["KEY", "OTHER"]
    assert all(set(r) == {"name", "updated_at"} for r in s.secrets.names("a"))
    assert s.secrets.values("a") == {"KEY": "one", "OTHER": "two"}
    assert s.secrets.values("b") == {"KEY": "three"}


def test_setting_a_secret_again_replaces_it_and_stamps_the_change(tmp_path):
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    s.secrets.set("a", {"KEY": "one"}, at=10.0)
    s.secrets.set("a", {"KEY": "two"}, at=20.0)
    assert s.secrets.values("a") == {"KEY": "two"}
    assert s.projects.get("a")["secrets_changed_at"] == 20.0


def test_deleting_an_absent_secret_reports_it_and_stamps_nothing(tmp_path):
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    s.secrets.set("a", {"KEY": "one"}, at=10.0)
    assert s.secrets.delete("a", "NOPE", at=20.0) is False
    assert s.projects.get("a")["secrets_changed_at"] == 10.0
    assert s.secrets.delete("a", "KEY", at=30.0) is True
    assert s.secrets.values("a") == {}
    assert s.projects.get("a")["secrets_changed_at"] == 30.0


def test_secrets_outlive_the_project_row_until_dropped(tmp_path):
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    s.secrets.set("a", {"KEY": "one"}, at=10.0)
    s.projects.remove("a")
    assert s.secrets.values("a") == {"KEY": "one"}
    s.secrets.drop("a")
    assert s.secrets.values("a") == {}


def test_without_an_explicit_time_the_change_is_stamped_at_write(tmp_path):
    import time
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    before = time.time()
    s.secrets.set("a", {"KEY": "v"})
    stamped = s.projects.get("a")["secrets_changed_at"]
    assert before <= stamped <= time.time()
    assert s.secrets.delete("a", "KEY") is True
    assert s.projects.get("a")["secrets_changed_at"] >= stamped


def _state_with_project(tmp_path):
    s = Repos.open(Database(tmp_path / "state.db"))
    s.projects.add("a", "/p/a", "d")
    return s


def test_a_request_shows_until_its_name_gets_a_value(tmp_path):
    s = _state_with_project(tmp_path)
    s.secrets.request("a", "STRIPE_KEY", "Stripe dashboard")
    assert s.secrets.requests("a") == [{"name": "STRIPE_KEY", "hint": "Stripe dashboard"}]
    s.secrets.set("a", {"STRIPE_KEY": "sk"})
    assert s.secrets.requests("a") == []


def test_a_request_for_a_name_that_already_has_a_value_is_not_listed(tmp_path):
    s = _state_with_project(tmp_path)
    s.secrets.set("a", {"STRIPE_KEY": "sk"})
    s.secrets.request("a", "STRIPE_KEY", "again")
    assert s.secrets.requests("a") == []


def test_a_request_never_marks_secrets_changed(tmp_path):
    s = _state_with_project(tmp_path)
    s.secrets.request("a", "STRIPE_KEY", "hint")
    assert s.projects.get("a")["secrets_changed_at"] is None


def test_deleting_a_request_reports_whether_it_existed(tmp_path):
    s = _state_with_project(tmp_path)
    s.secrets.request("a", "K", "hint")
    assert s.secrets.delete_request("a", "K") is True
    assert s.secrets.delete_request("a", "K") is False


def test_drop_secrets_clears_values_and_requests(tmp_path):
    s = _state_with_project(tmp_path)
    s.secrets.set("a", {"A": "1"})
    s.secrets.request("a", "B", "hint")
    s.secrets.drop("a")
    assert s.secrets.values("a") == {} and s.secrets.requests("a") == []
