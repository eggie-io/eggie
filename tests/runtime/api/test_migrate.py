import sqlite3

import pytest

from omelet_api.core import migrate
from omelet_api.core.state import State

# The shape state.py created before this module existed: no schema_version
# table, and no columns for a project's problem.
PRE_MIGRATION_SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    guest_path TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'stopped'
);
"""


def connect(tmp_path, name="state.db"):
    return sqlite3.connect(tmp_path / name)


def tables(conn) -> set[str]:
    return {row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(conn, table) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_an_empty_database_is_brought_to_the_current_schema(tmp_path):
    conn = connect(tmp_path)
    assert migrate.migrate(conn) == migrate.SCHEMA_VERSION

    assert {"projects", "schema_version"} <= tables(conn)
    assert {"problem_code", "problem_message"} <= columns(conn, "projects")


def test_migrating_an_already_current_database_is_a_no_op(tmp_path):
    conn = connect(tmp_path)
    migrate.migrate(conn)
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.commit()

    assert migrate.migrate(conn) == migrate.SCHEMA_VERSION
    assert conn.execute("SELECT status FROM projects WHERE id='blog'"
                        ).fetchone()[0] == "started_ok"
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_a_database_from_a_newer_api_refuses_to_open(tmp_path):
    # An older API meeting a newer database after a downgrade. Running the
    # list backwards, or writing to it at all, is unrecoverable.
    conn = connect(tmp_path)
    migrate.migrate(conn)
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.execute("UPDATE schema_version SET version=?",
                 (migrate.SCHEMA_VERSION + 5,))
    conn.commit()

    with pytest.raises(migrate.SchemaTooNew) as raised:
        migrate.migrate(conn)
    assert str(migrate.SCHEMA_VERSION + 5) in str(raised.value)

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == \
        migrate.SCHEMA_VERSION + 5, "the recorded version must not be rewritten"
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_a_database_written_before_migrations_existed_keeps_its_rows(tmp_path):
    # There is no schema_version table to read, so this database looks like
    # version 0 -- the migrations must recognise what is already there instead
    # of failing on it or starting over.
    conn = connect(tmp_path)
    conn.executescript(PRE_MIGRATION_SCHEMA)
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.commit()

    assert migrate.migrate(conn) == migrate.SCHEMA_VERSION
    row = conn.execute("SELECT status, problem_code FROM projects "
                       "WHERE id='blog'").fetchone()
    assert row == ("started_ok", None)


def test_state_refuses_a_future_database_instead_of_opening_it(tmp_path):
    db = tmp_path / "state.db"
    State(db).close()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_version SET version=?",
                 (migrate.SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()

    with pytest.raises(migrate.SchemaTooNew):
        State(db)


def test_state_adopts_a_pre_migration_database_without_losing_projects(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(PRE_MIGRATION_SCHEMA)
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.commit()
    conn.close()

    state = State(db)
    assert state.get_project("blog")["status"] == "started_ok"
    state.set_problem("blog", "bound_to_loopback", "listen on 0.0.0.0")
    assert state.get_project("blog")["problem_code"] == "bound_to_loopback"


def _v3_database(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for step in migrate.MIGRATIONS[:3]:
        step(conn)
    conn.execute("INSERT INTO schema_version(version) VALUES (3)")
    conn.execute("INSERT INTO projects(id, guest_path, domain, status) "
                 "VALUES ('blog', '/g/blog', 'd.io', 'started_ok')")
    conn.commit()
    conn.close()


def test_a_v3_database_gains_an_account_and_keeps_its_projects(tmp_path):
    db = tmp_path / "state.db"
    _v3_database(db)

    state = State(db)
    assert state.get_project("blog")["status"] == "started_ok"
    account = state.get_account()
    assert account["device_id"], "a device id is minted once, by the migration"
    assert account["access_token"] is None
    assert state.cloud_mapping() == {}
