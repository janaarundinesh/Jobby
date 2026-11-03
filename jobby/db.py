"""Jobby — The database: connecting, the schema, and its numbered migrations."""

from datetime import datetime
import glob
import os
import re
import sqlite3
import tempfile
import zipfile

from jobby.config import BACKUP_KEEP, DEFAULT_PROFILE_NAME, ROOT, ApiError, now_iso, today_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    location   TEXT NOT NULL DEFAULT '',
    headline   TEXT NOT NULL DEFAULT '',
    links      TEXT NOT NULL DEFAULT '',
    pin_hash   TEXT,
    pin_salt   TEXT,
    pin_set_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL DEFAULT 1,
    company       TEXT NOT NULL,
    role          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'applied',
    work_mode     TEXT NOT NULL DEFAULT 'onsite',
    location      TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    job_url       TEXT NOT NULL DEFAULT '',
    salary        TEXT NOT NULL DEFAULT '',
    contact_name  TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    phase         TEXT NOT NULL DEFAULT 'applied',
    description   TEXT NOT NULL DEFAULT '',
    applied_on    TEXT NOT NULL DEFAULT '',
    next_step     TEXT NOT NULL DEFAULT '',
    next_step_on  TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    match_pct     INTEGER,
    reviewed_at   TEXT,
    deleted_at    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status    TEXT,
    to_status      TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

/* Every application that was actually sent, on the day it went out. There is
   deliberately no foreign key: deleting a job cleans up the list, but it cannot
   un-send an application, and the goals and the streak are about sending. */
CREATE TABLE IF NOT EXISTS sent_log (
    application_id INTEGER PRIMARY KEY,
    profile_id     INTEGER NOT NULL DEFAULT 1,
    sent_on        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'other',
    filename       TEXT NOT NULL,
    stored_name    TEXT NOT NULL,
    content_type   TEXT NOT NULL DEFAULT 'application/octet-stream',
    size           INTEGER NOT NULL DEFAULT 0,
    uploaded_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resumes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id     INTEGER NOT NULL DEFAULT 1,
    application_id INTEGER,
    name           TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id    INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    stored_name  TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size         INTEGER NOT NULL DEFAULT 0,
    uploaded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    profile_id INTEGER NOT NULL DEFAULT 1,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (profile_id, key)
);

"""



# Indexes are separate from SCHEMA because several of them name columns the
# migration steps add: on a database from an older release, creating them
# before the steps run would ask for a column that is not there yet.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_events_app ON status_events(application_id);
CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_phase ON applications(phase);
CREATE INDEX IF NOT EXISTS idx_docs_app ON documents(application_id);
CREATE INDEX IF NOT EXISTS idx_resumes_profile ON resumes(profile_id);
CREATE INDEX IF NOT EXISTS idx_resumes_app ON resumes(application_id);
CREATE INDEX IF NOT EXISTS idx_resume_files ON resume_files(resume_id);
CREATE INDEX IF NOT EXISTS idx_sent_profile ON sent_log(profile_id, sent_on);
"""




def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn




def uploads_root(db_path):
    if db_path == ":memory:":
        base = os.path.join(ROOT, "data")
    else:
        base = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(base, "uploads")




def backup_database(db_path):
    """Copy the database into data/backups once a day so a bad change is undoable."""
    if db_path == ":memory:":
        return None
    folder = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, "tracker-%s.db" % datetime.now().strftime("%Y-%m-%d"))
    if not os.path.exists(target):
        source = connect(db_path)
        try:
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
    prune_backups(folder)
    return target




def prune_backups(folder, keep=BACKUP_KEEP):
    paths = sorted(glob.glob(os.path.join(folder, "tracker-*.db")))
    for path in paths[:-keep]:
        try:
            os.remove(path)
        except OSError:
            pass




BACKUP_NAME = re.compile(r"^tracker-(\d{4}-\d{2}-\d{2})(-[\w-]+)?\.db$")


def backups_folder(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def list_backups(db_path):
    """The snapshots on this computer, newest first."""
    if db_path == ":memory:":
        return []
    items = []
    for path in sorted(glob.glob(os.path.join(backups_folder(db_path), "tracker-*.db")), reverse=True):
        name = os.path.basename(path)
        match = BACKUP_NAME.match(name)
        if not match:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        items.append(
            {
                "name": name,
                "date": match.group(1),
                "label": match.group(1)
                + (" (before a restore)" if match.group(2) else ""),
                "size": size,
            }
        )
    return items


def restore_backup(db_path, name):
    """Put the database back to one of the daily snapshots.

    What is being replaced is copied aside first, under a name of its own, so a
    restore can itself be undone by restoring that copy.
    """
    if db_path == ":memory:" or not BACKUP_NAME.match(name or ""):
        raise ApiError(400, "That is not a snapshot this app made")
    folder = backups_folder(db_path)
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise ApiError(404, "That snapshot is no longer on disk")
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    keeper = os.path.join(folder, "tracker-%s-before-restore.db" % stamp)
    source = connect(db_path)
    try:
        copy = sqlite3.connect(keeper)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()

    snapshot = sqlite3.connect(path)
    try:
        target = connect(db_path)
        try:
            snapshot.backup(target)
            target.commit()
        finally:
            target.close()
    finally:
        snapshot.close()

    # A snapshot can predate the columns the app now uses; it is brought up to
    # date like any other database rather than answering with a missing column.
    conn = connect(db_path)
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
    prune_backups(folder)
    return {"restored": name, "safety_copy": os.path.basename(keeper)}


def build_backup_archive(db_path):
    """Zip a consistent snapshot of the database and every uploaded file."""
    handle = tempfile.NamedTemporaryFile(
        prefix="jobpipe-backup-", suffix=".zip", delete=False
    )
    handle.close()
    snapshot = handle.name + ".db"
    try:
        if db_path != ":memory:":
            source = connect(db_path)
            try:
                copy = sqlite3.connect(snapshot)
                try:
                    source.backup(copy)
                finally:
                    copy.close()
            finally:
                source.close()
        with zipfile.ZipFile(handle.name, "w", zipfile.ZIP_DEFLATED) as archive:
            if os.path.isfile(snapshot):
                archive.write(snapshot, "tracker.db")
            uploads = uploads_root(db_path)
            for root, _dirs, files in os.walk(uploads):
                for name in files:
                    path = os.path.join(root, name)
                    archive.write(path, os.path.join("uploads", os.path.relpath(path, uploads)))
            archive.writestr(
                "README.txt",
                "Jobby backup taken %s\n\n"
                "tracker.db is the SQLite database; uploads/ holds the documents "
                "that belong to each job (one folder per job id) and the support "
                "files of each resume (resume-<id>).\n" % now_iso(),
            )
        return handle.name
    except Exception:
        try:
            os.remove(handle.name)
        except OSError:
            pass
        raise
    finally:
        if os.path.isfile(snapshot):
            os.remove(snapshot)




def ensure_default_profile(conn):
    """Every database has at least one profile; older data belongs to it."""
    row = conn.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
    if row is None:
        stamp = now_iso()
        conn.execute(
            "INSERT INTO profiles (name, created_at, updated_at) VALUES (?, ?, ?)",
            (DEFAULT_PROFILE_NAME, stamp, stamp),
        )




def table_columns(conn, table):
    return {row["name"] for row in conn.execute("PRAGMA table_info(%s)" % table)}




def add_column(conn, table, column, definition):
    """Add a column an older database is missing."""
    if column not in table_columns(conn, table):
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, definition))




def migration_1(conn):
    """Postings stopped being one flat list: phase, description, trash, review."""
    for column, definition in (
        ("phase", "TEXT NOT NULL DEFAULT 'applied'"),
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("deleted_at", "TEXT"),
        ("reviewed_at", "TEXT"),
        ("match_pct", "INTEGER"),
    ):
        add_column(conn, "applications", column, definition)




def migration_2(conn):
    """More than one person can use the app: every job belongs to a profile."""
    add_column(conn, "applications", "profile_id", "INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_apps_profile ON applications(profile_id)"
    )




def migration_3(conn):
    """Profiles can be locked with a PIN."""
    for column in ("pin_hash", "pin_salt", "pin_set_at"):
        add_column(conn, "profiles", column, "TEXT")




def migration_4(conn):
    """Goals became per profile; the old key/value table is rebuilt for it."""
    columns = table_columns(conn, "settings")
    if not columns or "profile_id" in columns:
        return
    conn.execute(
        "CREATE TABLE settings_new ("
        " profile_id INTEGER NOT NULL DEFAULT 1,"
        " key TEXT NOT NULL,"
        " value TEXT NOT NULL,"
        " PRIMARY KEY (profile_id, key))"
    )
    conn.execute(
        "INSERT INTO settings_new (profile_id, key, value) SELECT 1, key, value FROM settings"
    )
    conn.execute("DROP TABLE settings")
    conn.execute("ALTER TABLE settings_new RENAME TO settings")




# Every change to the shape of the database, in the order it happened, with the
# version that records it. A database keeps the versions it has already run, so
# each step happens once. The steps also check what they change, because a
# database from before schema_version existed has to start at 1 with all of
# its columns already there.
MIGRATIONS = [
    (1, "phases, descriptions, trash and review marks", migration_1),
    (2, "jobs belong to a profile", migration_2),
    (3, "profiles carry a PIN", migration_3),
    (4, "goals belong to a profile", migration_4),
]


SCHEMA_VERSION = MIGRATIONS[-1][0]




def migrate(conn):
    """Bring the schema up to date, one recorded step at a time."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    applied = {
        row["version"] for row in conn.execute("SELECT version FROM schema_version")
    }
    for version, name, step in MIGRATIONS:
        if version in applied:
            continue
        step(conn)
        conn.execute(
            "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, now_iso()),
        )
    ensure_default_profile(conn)




def sent_day(value):
    return str(value or "").strip()[:10]




def record_sent(conn, profile_id, app_id, sent_on):
    """Note that an application went out, on the day it went out.

    The row is keyed by the application so nothing is counted twice, and it is
    updated rather than added to so correcting a date moves the count to the
    right day instead of leaving it behind.
    """
    day = sent_day(sent_on) or today_iso()
    conn.execute(
        "INSERT INTO sent_log (application_id, profile_id, sent_on) VALUES (?, ?, ?)"
        " ON CONFLICT(application_id) DO UPDATE SET"
        " profile_id = excluded.profile_id, sent_on = excluded.sent_on",
        (app_id, profile_id, day),
    )




def forget_sent(conn, app_id):
    """A correction, not a cleanup: the job was never actually sent."""
    conn.execute("DELETE FROM sent_log WHERE application_id = ?", (app_id,))




def backfill_sent_log(conn):
    """Seed the ledger from the applied jobs an older database already holds."""
    conn.execute(
        "INSERT OR IGNORE INTO sent_log (application_id, profile_id, sent_on)"
        " SELECT id, profile_id, substr("
        "COALESCE(NULLIF(applied_on, ''), created_at), 1, 10)"
        " FROM applications WHERE phase != 'to_apply'"
    )




def sent_counts(db_path, profile_id):
    """Applications sent per day, and how many were sent in all."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT sent_on, COUNT(*) AS sent FROM sent_log WHERE profile_id = ?"
            " GROUP BY sent_on ORDER BY sent_on ASC",
            (profile_id,),
        ).fetchall()
    finally:
        conn.close()
    days = {}
    total = 0
    for row in rows:
        days[row["sent_on"]] = row["sent"]
        total += row["sent"]
    return {"total": total, "days": days}




def init_db(db_path):
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # A database from before the ledger has jobs whose applications were sent
        # but never recorded; fill those in once, when the table first appears.
        fresh_ledger = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sent_log'"
            ).fetchone()
            is None
        )
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.executescript(INDEXES)
        if fresh_ledger:
            backfill_sent_log(conn)
        conn.commit()
    finally:
        conn.close()




def app_dict(row):
    return {key: row[key] for key in row.keys()}




def event_dict(row):
    return {key: row[key] for key in row.keys()}
