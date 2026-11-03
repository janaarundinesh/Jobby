"""Bringing an older database up to date, and recording that it happened."""

import sqlite3

import server


def _old_database(path):
    """A database shaped like the first release, before any of the steps."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            applied_on TEXT NOT NULL DEFAULT '',
            work_mode TEXT NOT NULL DEFAULT 'onsite',
            next_step_on TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            headline TEXT NOT NULL DEFAULT '',
            links TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO settings (key, value) VALUES ('daily_goal', '7');
        INSERT INTO applications (company, role, status, created_at, updated_at)
            VALUES ('Old Co', 'Engineer', 'applied',
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def _versions(path):
    conn = sqlite3.connect(str(path))
    try:
        return [
            row[0]
            for row in conn.execute("SELECT version FROM schema_version ORDER BY version")
        ]
    finally:
        conn.close()


def _stamp(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT version, applied_at FROM schema_version ORDER BY version"
        ).fetchall()
    finally:
        conn.close()


def test_an_old_database_gains_what_the_steps_add(tmp_path):
    db = tmp_path / "tracker.db"
    _old_database(db)

    server.init_db(str(db))

    conn = server.connect(str(db))
    try:
        assert {
            "phase",
            "description",
            "deleted_at",
            "reviewed_at",
            "match_pct",
            "profile_id",
        } <= server.table_columns(conn, "applications")
        assert {"pin_hash", "pin_salt", "pin_set_at"} <= server.table_columns(
            conn, "profiles"
        )
        # The jobs that were already there belong to the first profile, and its
        # goal came across into the per-profile settings table.
        assert conn.execute("SELECT profile_id FROM applications").fetchone()[
            "profile_id"
        ] == 1
        goal = conn.execute(
            "SELECT value FROM settings WHERE profile_id = 1 AND key = 'daily_goal'"
        ).fetchone()
        assert goal["value"] == "7"
        # The ledger was seeded from the jobs the old database already held.
        assert conn.execute("SELECT COUNT(*) AS count FROM sent_log").fetchone()[
            "count"
        ] == 1
    finally:
        conn.close()

    assert _versions(db) == [version for version, _name, _step in server.MIGRATIONS]


def test_the_steps_are_recorded_once_and_not_repeated(tmp_path):
    db = tmp_path / "tracker.db"
    _old_database(db)
    server.init_db(str(db))
    first = _stamp(db)

    server.init_db(str(db))

    # Same versions, same timestamps: nothing ran a second time.
    assert _stamp(db) == first


def test_a_fresh_database_starts_at_the_current_version(tmp_path):
    db = tmp_path / "tracker.db"
    server.init_db(str(db))
    versions = _versions(db)
    assert versions == [version for version, _name, _step in server.MIGRATIONS]
    assert server.SCHEMA_VERSION == max(versions)
