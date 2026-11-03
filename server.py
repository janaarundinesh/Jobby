#!/usr/bin/env python3
"""Local job application tracker.

Serves a small JSON API backed by SQLite plus the static frontend in ./public.

  python3 server.py                # http://127.0.0.1:8787
  python3 server.py --port 9000 --open
"""

import argparse
import base64

import install_tectonic
import binascii
import glob
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from extract import ExtractionError, extract_job

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(ROOT, "public")
DEFAULT_DB = os.path.join(ROOT, "data", "tracker.db")

STATUSES = ["applied", "screening", "interview", "offer", "rejected", "withdrawn"]
WORK_MODES = ["remote", "hybrid", "onsite"]
PHASES = ["to_apply", "applied"]
DOC_KINDS = ["resume", "cover_letter", "other"]
MAX_BODY = 20 * 1024 * 1024
UPLOAD_LIMIT = 10 * 1024 * 1024

# The API only answers the dashboard it serves: a loopback Host header (which
# defeats DNS rebinding), an Origin from this very server on writes, and a token
# handed out by GET /api/session that no other origin can read.
API_TOKEN = secrets.token_urlsafe(32)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# One database snapshot per day, keeping the last two weeks.
BACKUP_KEEP = 14

TEXT_LIMITS = {
    "company": 160,
    "role": 200,
    "location": 160,
    "source": 100,
    "job_url": 1000,
    "salary": 100,
    "contact_name": 160,
    "contact_email": 200,
    "next_step": 300,
    "notes": 20000,
    "description": 20000,
}

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

CREATE INDEX IF NOT EXISTS idx_events_app ON status_events(application_id);
CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_phase ON applications(phase);
CREATE INDEX IF NOT EXISTS idx_docs_app ON documents(application_id);
CREATE INDEX IF NOT EXISTS idx_resumes_profile ON resumes(profile_id);
CREATE INDEX IF NOT EXISTS idx_resumes_app ON resumes(application_id);
CREATE INDEX IF NOT EXISTS idx_resume_files ON resume_files(resume_id);
CREATE INDEX IF NOT EXISTS idx_sent_profile ON sent_log(profile_id, sent_on);
"""

DEFAULT_PROFILE_NAME = "Personal"
DEFAULT_SETTINGS = {"daily_goal": 3, "weekly_goal": 10}
PROFILE_LIMITS = {
    "name": 80,
    "email": 200,
    "phone": 40,
    "location": 120,
    "headline": 160,
    "links": 400,
}
SETTING_LIMITS = {"daily_goal": (1, 100), "weekly_goal": (1, 100)}

# The columns a partial update may set. The keys are chosen by the cleaning
# functions; update_clause refuses anything else, so a payload field can never
# reach a query even if a future cleaner stops filtering.
PROFILE_COLUMNS = set(PROFILE_LIMITS)
APPLICATION_COLUMNS = set(TEXT_LIMITS) | {
    "status",
    "phase",
    "work_mode",
    "applied_on",
    "next_step_on",
    "match_pct",
    "reviewed_at",
}
RESUME_COLUMNS = {"source", "name", "label", "updated_at"}

PIN_MIN, PIN_MAX = 4, 8
PIN_ITERATIONS = 120000
UNLOCK_TTL = 12 * 60 * 60  # an unlocked profile stays open for one working day

# Wrong PINs are counted per profile. A four-digit PIN is only ten thousand
# guesses, so the wait after a miss is what makes it worth anything: five are
# free, and every one after that doubles the wait, up to a quarter of an hour.
PIN_FREE_ATTEMPTS = 5
PIN_LOCK_BASE = 30
PIN_LOCK_MAX = 15 * 60
PIN_FORGET_AFTER = 15 * 60  # a quiet spell forgets the older misses
PIN_FAILURES = {}
PIN_LOCK = threading.Lock()

# Unlock tokens live in memory: restarting the server locks every profile again.
UNLOCK_TOKENS = {}
TOKEN_LOCK = threading.Lock()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}

# index.html carries two small inline scripts (the file: start screen and the
# module loader). Their hashes are taken from the file as it is served, so
# script-src can stay free of 'unsafe-inline'.
INLINE_SCRIPT = re.compile(rb"<script[^>]*>(.*?)</script>", re.S)
CSP_CACHE = {"stamp": None, "policy": None}


def content_security_policy():
    """What the page may load: itself, plus the two inline boot scripts."""
    page_path = os.path.join(PUBLIC_DIR, "index.html")
    try:
        stat = os.stat(page_path)
    except OSError:
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    if CSP_CACHE["stamp"] == stamp:
        return CSP_CACHE["policy"]
    try:
        with open(page_path, "rb") as handle:
            page = handle.read()
    except OSError:
        return None
    hashes = [
        "'sha256-%s'" % base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        for body in INLINE_SCRIPT.findall(page)
        if body.strip()
    ]
    policy = "; ".join(
        [
            "default-src 'self'",
            " ".join(["script-src 'self'"] + hashes),
            # Chart geometry, match colours and cover images are inline styles.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self'",
            "connect-src 'self'",
            # The PDF preview is a blob: document inside an iframe.
            "frame-src 'self' blob:",
            "worker-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
    CSP_CACHE["stamp"] = stamp
    CSP_CACHE["policy"] = policy
    return policy


class ApiError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        # Structured detail for the client (a TeX log, for instance).
        self.extra = extra or {}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso():
    return datetime.now().strftime("%Y-%m-%d")


def update_clause(data, allowed):
    """The SET clause of a partial update, from named columns only.

    The values are bound placeholders, so a key is the only way a caller could
    shape one of these statements. The keys come from the cleaning functions;
    this check keeps that true if one of them ever starts passing a payload
    field straight through.
    """
    unknown = [key for key in data if key not in allowed]
    if unknown:
        raise ApiError(500, "Internal error: cannot save %s" % unknown[0])
    return ", ".join("%s = ?" % key for key in data)


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


def host_name(value):
    """The host part of a Host header or URL authority (IPv6 brackets removed)."""
    text = (value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1].split("/", 1)[0]
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


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


def migrate(conn):
    """Add columns and tables introduced after the first release."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
    if "phase" not in columns:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN phase TEXT NOT NULL DEFAULT 'applied'"
        )
    if "description" not in columns:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    if "deleted_at" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN deleted_at TEXT")
    if "reviewed_at" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN reviewed_at TEXT")
    if "match_pct" not in columns:
        conn.execute("ALTER TABLE applications ADD COLUMN match_pct INTEGER")
    if "profile_id" not in columns:
        conn.execute(
            "ALTER TABLE applications ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1"
        )
    profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
    for column in ("pin_hash", "pin_salt", "pin_set_at"):
        if profile_columns and column not in profile_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN %s TEXT" % column)
    # Created here rather than in SCHEMA: older databases only gain the column above.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_apps_profile ON applications(profile_id)"
    )

    ensure_default_profile(conn)

    # Settings became per-profile; rebuild the old key/value table with the
    # existing goals copied onto the first profile.
    setting_columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    if setting_columns and "profile_id" not in setting_columns:
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
        if fresh_ledger:
            backfill_sent_log(conn)
        conn.commit()
    finally:
        conn.close()


def app_dict(row):
    return {key: row[key] for key in row.keys()}


def event_dict(row):
    return {key: row[key] for key in row.keys()}


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

PROFILE_FIELDS = [
    "id",
    "name",
    "email",
    "phone",
    "location",
    "headline",
    "links",
    "created_at",
    "updated_at",
]


def profile_dict(row):
    """A profile as the client sees it: never the PIN hash or salt."""
    data = {key: row[key] for key in PROFILE_FIELDS if key in row.keys()}
    data["locked"] = bool(row["pin_hash"]) if "pin_hash" in row.keys() else False
    if data["locked"] and "pin_set_at" in row.keys():
        data["pin_set_at"] = row["pin_set_at"]
    return data


def hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), bytes.fromhex(salt), PIN_ITERATIONS
    ).hex()


def check_pin(row, pin):
    if not pin or not row["pin_hash"] or not row["pin_salt"]:
        return False
    return hmac.compare_digest(row["pin_hash"], hash_pin(pin, row["pin_salt"]))


def clean_pin(value):
    if not isinstance(value, str):
        raise ApiError(400, "A PIN must be text")
    pin = value.strip()
    if not pin.isdigit() or not PIN_MIN <= len(pin) <= PIN_MAX:
        raise ApiError(400, "The PIN must be %d–%d digits" % (PIN_MIN, PIN_MAX))
    return pin


def issue_token(profile_id):
    token = secrets.token_urlsafe(24)
    now = time.time()
    with TOKEN_LOCK:
        for value in [key for key, item in UNLOCK_TOKENS.items() if item["expires"] < now]:
            UNLOCK_TOKENS.pop(value, None)
        UNLOCK_TOKENS[token] = {"profile_id": profile_id, "expires": now + UNLOCK_TTL}
    return token


def token_is_valid(token, profile_id):
    if not token:
        return False
    with TOKEN_LOCK:
        entry = UNLOCK_TOKENS.get(token)
        if entry is None:
            return False
        if entry["expires"] < time.time():
            UNLOCK_TOKENS.pop(token, None)
            return False
        return entry["profile_id"] == profile_id


def pin_wait(profile_id):
    """Seconds left on this profile's lockout; 0 when a PIN may be tried."""
    with PIN_LOCK:
        entry = PIN_FAILURES.get(profile_id)
        if not entry:
            return 0
        return max(0, int(entry["until"] - time.time() + 0.999))


def record_pin_miss(profile_id):
    """Count a wrong PIN and start, or extend, the wait it earns."""
    now = time.time()
    with PIN_LOCK:
        entry = PIN_FAILURES.get(profile_id)
        if not entry or now - entry["last"] > PIN_FORGET_AFTER:
            entry = {"count": 0, "last": now, "until": 0.0}
        entry["count"] += 1
        entry["last"] = now
        if entry["count"] > PIN_FREE_ATTEMPTS:
            steps = entry["count"] - PIN_FREE_ATTEMPTS - 1
            entry["until"] = now + min(PIN_LOCK_BASE * (2 ** steps), PIN_LOCK_MAX)
        PIN_FAILURES[profile_id] = entry
        return max(0, int(entry["until"] - now + 0.999))


def forget_pin_misses(profile_id):
    with PIN_LOCK:
        PIN_FAILURES.pop(profile_id, None)


def pin_miss_error(seconds):
    if seconds <= 0:
        return ApiError(401, "That PIN is not correct")
    minutes = max(1, (seconds + 59) // 60)
    return ApiError(
        429,
        "Too many wrong PINs — try again in %d minute%s"
        % (minutes, "" if minutes == 1 else "s"),
        {"retry_after": seconds},
    )


def revoke_tokens(profile_id):
    with TOKEN_LOCK:
        for token in [
            key for key, item in UNLOCK_TOKENS.items() if item["profile_id"] == profile_id
        ]:
            UNLOCK_TOKENS.pop(token, None)


def unlock_profile(db_path, profile_id, payload):
    pin = payload.get("pin") if isinstance(payload, dict) else None
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ApiError(404, "Profile not found")
    if not row["pin_hash"]:
        raise ApiError(400, "This profile has no PIN")
    wait = pin_wait(profile_id)
    if wait:
        raise pin_miss_error(wait)
    if not check_pin(row, pin.strip() if isinstance(pin, str) else ""):
        raise pin_miss_error(record_pin_miss(profile_id))
    forget_pin_misses(profile_id)
    return {"token": issue_token(profile_id), "profile": profile_dict(row)}


def set_profile_pin(db_path, profile_id, payload):
    """Turn the PIN lock on, change it, or remove it."""
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Profile not found")
        if row["pin_hash"]:
            current = payload.get("current_pin")
            if not isinstance(current, str) or not check_pin(row, current.strip()):
                raise ApiError(401, "The current PIN is not correct")
        if payload.get("remove"):
            if not row["pin_hash"]:
                raise ApiError(400, "This profile has no PIN")
            conn.execute(
                "UPDATE profiles SET pin_hash = NULL, pin_salt = NULL, pin_set_at = NULL,"
                " updated_at = ? WHERE id = ?",
                (now_iso(), profile_id),
            )
            conn.commit()
            revoke_tokens(profile_id)
            forget_pin_misses(profile_id)
            return {"locked": False}
        pin = clean_pin(payload.get("pin"))
        salt = secrets.token_hex(16)
        stamp = now_iso()
        conn.execute(
            "UPDATE profiles SET pin_hash = ?, pin_salt = ?, pin_set_at = ?, updated_at = ?"
            " WHERE id = ?",
            (hash_pin(pin, salt), salt, stamp, stamp, profile_id),
        )
        conn.commit()
        # Whoever just proved (or set) the PIN stays in without being asked again.
        forget_pin_misses(profile_id)
        return {"locked": True, "token": issue_token(profile_id)}
    finally:
        conn.close()


def clean_profile(payload, partial=False):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    data = {}
    for key, limit in PROFILE_LIMITS.items():
        if partial and key not in payload:
            continue
        data[key] = _str(payload, key, limit)
    if (not partial or "name" in data) and not data.get("name"):
        raise ApiError(400, "A profile name is required")
    return data


def list_profiles(db_path):
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT p.*,"
            " (SELECT COUNT(*) FROM applications a"
            "   WHERE a.profile_id = p.id AND a.deleted_at IS NULL) AS jobs,"
            " (SELECT COUNT(*) FROM applications a WHERE a.profile_id = p.id) AS total_jobs"
            " FROM profiles p ORDER BY p.id ASC"
        ).fetchall()
        return [
            dict(profile_dict(row), jobs=row["jobs"], total_jobs=row["total_jobs"])
            for row in rows
        ]
    finally:
        conn.close()


def default_profile_id(db_path):
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else 1
    finally:
        conn.close()


def get_profile(db_path, profile_id):
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Profile not found")
        return profile_dict(row)
    finally:
        conn.close()


def create_profile(db_path, payload):
    data = clean_profile(payload)
    stamp = now_iso()
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO profiles"
            " (name, email, phone, location, headline, links, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["name"],
                data["email"],
                data["phone"],
                data["location"],
                data["headline"],
                data["links"],
                stamp,
                stamp,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return profile_dict(row)
    finally:
        conn.close()


def update_profile(db_path, profile_id, payload):
    data = clean_profile(payload, partial=True)
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "Profile not found")
        if not data:
            return profile_dict(row)
        assignments = update_clause(data, PROFILE_COLUMNS)
        conn.execute(
            "UPDATE profiles SET %s, updated_at = ? WHERE id = ?" % assignments,
            list(data.values()) + [now_iso(), profile_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return profile_dict(row)
    finally:
        conn.close()


def delete_profile(db_path, profile_id):
    """Remove a profile with every job, document and goal that belongs to it."""
    conn = connect(db_path)
    ids = []
    files = 0
    try:
        total = conn.execute("SELECT COUNT(*) AS count FROM profiles").fetchone()["count"]
        if total <= 1:
            raise ApiError(400, "Keep at least one profile")
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "Profile not found")
        ids = [
            item["id"]
            for item in conn.execute(
                "SELECT id FROM applications WHERE profile_id = ?", (profile_id,)
            ).fetchall()
        ]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            files = conn.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE application_id IN (%s)"
                % placeholders,
                ids,
            ).fetchone()["count"]
        resume_ids = [
            item["id"]
            for item in conn.execute(
                "SELECT id FROM resumes WHERE profile_id = ?", (profile_id,)
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM resume_files WHERE resume_id IN"
            " (SELECT id FROM resumes WHERE profile_id = ?)",
            (profile_id,),
        )
        conn.execute("DELETE FROM resumes WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM applications WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM sent_log WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM settings WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
    finally:
        conn.close()
    revoke_tokens(profile_id)
    forget_pin_misses(profile_id)
    for app_id in ids:
        shutil.rmtree(os.path.join(uploads_root(db_path), str(app_id)), ignore_errors=True)
    for resume_id in resume_ids:
        shutil.rmtree(resume_file_folder(db_path, resume_id), ignore_errors=True)
    return {"deleted": profile_id, "jobs": len(ids), "files": files}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _str(payload, key, limit):
    raw = payload.get(key, "")
    if raw is None:
        raw = ""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str):
        raise ApiError(400, "%s must be text" % key)
    value = raw.strip()
    if len(value) > limit:
        raise ApiError(400, "%s is too long (max %d characters)" % (key, limit))
    return value


def _date(payload, key, required=False):
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ApiError(400, "%s must be a date (YYYY-MM-DD)" % key)
    value = raw.strip()
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ApiError(400, "%s must be a date (YYYY-MM-DD)" % key)
    return value


def _status(payload, default=None):
    raw = payload.get("status")
    if raw is None:
        return default
    if not isinstance(raw, str) or raw.strip() not in STATUSES:
        raise ApiError(400, "status must be one of: %s" % ", ".join(STATUSES))
    return raw.strip()


def _work_mode(payload, default=None):
    raw = payload.get("work_mode")
    if raw is None:
        return default
    if not isinstance(raw, str) or raw.strip() not in WORK_MODES:
        raise ApiError(400, "work_mode must be one of: %s" % ", ".join(WORK_MODES))
    return raw.strip()


def _phase(payload, default=None):
    raw = payload.get("phase")
    if raw is None:
        return default
    if not isinstance(raw, str) or raw.strip() not in PHASES:
        raise ApiError(400, "phase must be one of: %s" % ", ".join(PHASES))
    return raw.strip()


def _match_pct(payload):
    """The saved resume-match estimate: 0-100, or None when the field is empty."""
    if "match_pct" not in payload:
        return None
    raw = payload["match_pct"]
    if raw is None:
        return None
    if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
        text = str(raw).strip().rstrip("%").strip()
        if not text:
            return None
        try:
            value = int(round(float(text)))
        except ValueError:
            value = None
        if value is not None and 0 <= value <= 100:
            return value
    raise ApiError(400, "Match percentage must be a whole number from 0 to 100")


def tolerant_match_pct(raw):
    """Read a match estimate out of a file, ignoring anything unreadable."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    text = str(raw).strip().rstrip("%").strip()
    if not text:
        return None
    found = re.search(r"\d+(?:\.\d+)?", text)
    if not found:
        return None
    return max(0, min(100, int(round(float(found.group(0))))))


def clean_create(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    company = _str(payload, "company", TEXT_LIMITS["company"])
    role = _str(payload, "role", TEXT_LIMITS["role"])
    if not company:
        raise ApiError(400, "Company is required")
    if not role:
        raise ApiError(400, "Role is required")
    phase = _phase(payload, "applied")
    applied_on = _date(payload, "applied_on") or ""
    if phase == "to_apply":
        status = "applied"
        applied_on = ""
    else:
        status = _status(payload, "applied")
        applied_on = applied_on or today_iso()
    data = {
        "company": company,
        "role": role,
        "status": status,
        "phase": phase,
        "work_mode": _work_mode(payload, "onsite"),
        "applied_on": applied_on,
        "next_step_on": _date(payload, "next_step_on"),
        "match_pct": _match_pct(payload),
    }
    for key, limit in TEXT_LIMITS.items():
        if key in ("company", "role"):
            continue
        data[key] = _str(payload, key, limit)
    return data


def clean_patch(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    data = {}
    for key, limit in TEXT_LIMITS.items():
        if key in payload:
            data[key] = _str(payload, key, limit)
    if "company" in data and not data["company"]:
        raise ApiError(400, "Company cannot be empty")
    if "role" in data and not data["role"]:
        raise ApiError(400, "Role cannot be empty")
    if "status" in payload:
        data["status"] = _status(payload)
    if "work_mode" in payload:
        data["work_mode"] = _work_mode(payload)
    if "phase" in payload:
        data["phase"] = _phase(payload)
    if "applied_on" in payload:
        data["applied_on"] = _date(payload, "applied_on") or ""
    if "next_step_on" in payload:
        data["next_step_on"] = _date(payload, "next_step_on")
    if payload.get("mark_reviewed") is True:
        data["reviewed_at"] = now_iso()
    if "match_pct" in payload:
        data["match_pct"] = _match_pct(payload)
    return data


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

def get_settings(db_path, profile_id):
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE profile_id = ?", (profile_id,)
        ).fetchall()
    finally:
        conn.close()
    stored = {row["key"]: row["value"] for row in rows}
    settings = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        try:
            value = int(stored.get(key, DEFAULT_SETTINGS[key]))
        except (TypeError, ValueError):
            continue
        low, high = SETTING_LIMITS[key]
        settings[key] = max(low, min(high, value))
    return settings


def save_settings(db_path, profile_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    updates = {}
    for key, (low, high) in SETTING_LIMITS.items():
        if key not in payload:
            continue
        raw = payload[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ApiError(400, "%s must be a whole number" % key)
        try:
            value = int(str(raw).strip())
        except ValueError:
            raise ApiError(400, "%s must be a whole number" % key)
        if value < low or value > high:
            raise ApiError(400, "%s must be between %d and %d" % (key, low, high))
        updates[key] = value
    if not updates:
        raise ApiError(400, "No goal values provided")
    conn = connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO settings (profile_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(profile_id, key) DO UPDATE SET value = excluded.value",
            [(profile_id, key, str(value)) for key, value in updates.items()],
        )
        conn.commit()
    finally:
        conn.close()
    return get_settings(db_path, profile_id)


def list_applications(db_path, profile_id, trashed=False):
    conn = connect(db_path)
    try:
        if trashed:
            rows = conn.execute(
                "SELECT * FROM applications"
                " WHERE profile_id = ? AND deleted_at IS NOT NULL"
                " ORDER BY deleted_at DESC, id DESC",
                (profile_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM applications"
                " WHERE profile_id = ? AND deleted_at IS NULL"
                " ORDER BY applied_on DESC, id DESC",
                (profile_id,),
            ).fetchall()
        return [app_dict(row) for row in rows]
    finally:
        conn.close()


def get_application(db_path, profile_id, app_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Application not found")
        events = conn.execute(
            "SELECT * FROM status_events WHERE application_id = ? ORDER BY id ASC",
            (app_id,),
        ).fetchall()
        return (
            app_dict(row),
            [event_dict(event) for event in events],
            list_documents(conn, app_id),
        )
    finally:
        conn.close()


def job_key(company, role):
    """What makes two rows the same posting: casing and spacing do not count."""
    squash = lambda value: " ".join(str(value or "").split()).casefold()  # noqa: E731
    return squash(company) + "\u0000" + squash(role)


def duplicate_jobs(db_path, profile_id, company, role):
    """Jobs already on this profile with the same company and role.

    Trashed jobs do not count: a posting you threw away is one you may genuinely
    want to add again.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, company, role, status, phase, applied_on FROM applications"
            " WHERE profile_id = ? AND deleted_at IS NULL"
            " ORDER BY id ASC",
            (profile_id,),
        ).fetchall()
    finally:
        conn.close()
    key = job_key(company, role)
    return [
        {
            "id": row["id"],
            "company": row["company"],
            "role": row["role"],
            "status": row["status"],
            "phase": row["phase"],
            "applied_on": row["applied_on"] or "",
        }
        for row in rows
        if job_key(row["company"], row["role"]) == key
    ]


def create_application(db_path, profile_id, data):
    stamp = now_iso()
    conn = connect(db_path)
    try:
        columns = list(data.keys()) + ["profile_id", "created_at", "updated_at"]
        values = list(data.values()) + [profile_id, stamp, stamp]
        placeholders = ", ".join("?" for _ in columns)
        cursor = conn.execute(
            "INSERT INTO applications (%s) VALUES (%s)"
            % (", ".join(columns), placeholders),
            values,
        )
        app_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO status_events (application_id, from_status, to_status, created_at)"
            " VALUES (?, ?, ?, ?)",
            (app_id, None, data["status"], stamp),
        )
        if data.get("phase", "applied") != "to_apply":
            record_sent(conn, profile_id, app_id, data.get("applied_on") or stamp)
        conn.commit()
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return app_dict(row)
    finally:
        conn.close()


def update_application(db_path, profile_id, app_id, data):
    stamp = now_iso()
    conn = connect(db_path)
    try:
        current = conn.execute(
            "SELECT * FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if current is None:
            raise ApiError(404, "Application not found")
        if not data:
            return app_dict(current)
        target_phase = data.get("phase", current["phase"])
        if target_phase == "to_apply":
            data["phase"] = "to_apply"
            data["applied_on"] = ""
            data["status"] = "applied"
        elif "phase" in data or "applied_on" in data:
            if not data.get("applied_on"):
                data["applied_on"] = current["applied_on"] or today_iso()
        assignments = update_clause(data, APPLICATION_COLUMNS)
        conn.execute(
            "UPDATE applications SET %s, updated_at = ? WHERE id = ?" % assignments,
            list(data.values()) + [stamp, app_id],
        )
        new_status = data.get("status")
        if new_status and new_status != current["status"]:
            conn.execute(
                "INSERT INTO status_events (application_id, from_status, to_status, created_at)"
                " VALUES (?, ?, ?, ?)",
                (app_id, current["status"], new_status, stamp),
            )
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        if row["phase"] == "to_apply":
            # Back to the saved list: it was never sent after all.
            forget_sent(conn, app_id)
        else:
            record_sent(conn, profile_id, app_id, row["applied_on"] or stamp)
        conn.commit()
        return app_dict(row)
    finally:
        conn.close()


def soft_delete_application(db_path, profile_id, app_id):
    """Move a job to the trash. Its documents stay on disk until it is purged."""
    stamp = now_iso()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Application not found")
        if not row["deleted_at"]:
            conn.execute(
                "UPDATE applications SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, app_id),
            )
            conn.commit()
        updated = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        return app_dict(updated)
    finally:
        conn.close()


def restore_application(db_path, profile_id, app_id):
    stamp = now_iso()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Application not found")
        if not row["deleted_at"]:
            raise ApiError(400, "That job is not in the trash")
        conn.execute(
            "UPDATE applications SET deleted_at = NULL, updated_at = ? WHERE id = ?",
            (stamp, app_id),
        )
        # A job trashed before the ledger existed still counts as sent.
        conn.execute(
            "INSERT OR IGNORE INTO sent_log (application_id, profile_id, sent_on)"
            " SELECT id, profile_id, substr("
            "COALESCE(NULLIF(applied_on, ''), created_at), 1, 10)"
            " FROM applications WHERE id = ? AND phase != 'to_apply'",
            (app_id,),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        return app_dict(updated)
    finally:
        conn.close()


def purge_trash(db_path, profile_id):
    """Permanently remove every trashed job, its rows and its uploaded files."""
    conn = connect(db_path)
    try:
        ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM applications WHERE deleted_at IS NOT NULL AND profile_id = ?",
                (profile_id,),
            ).fetchall()
        ]
        if not ids:
            return {"deleted": 0, "files": 0}
        placeholders = ",".join("?" for _ in ids)
        files = conn.execute(
            "SELECT COUNT(*) AS count FROM documents WHERE application_id IN (%s)" % placeholders,
            ids,
        ).fetchone()["count"]
        # A tailored resume belongs to its job; deleting the job deletes it too.
        resume_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM resumes WHERE application_id IN (%s)" % placeholders,
                ids,
            ).fetchall()
        ]
        if resume_ids:
            conn.execute(
                "DELETE FROM resume_files WHERE resume_id IN (%s)"
                % ",".join("?" for _ in resume_ids),
                resume_ids,
            )
        conn.execute(
            "DELETE FROM resumes WHERE application_id IN (%s)" % placeholders, ids
        )
        conn.execute(
            "DELETE FROM applications WHERE deleted_at IS NOT NULL AND profile_id = ?",
            (profile_id,),
        )
        conn.commit()
    finally:
        conn.close()
    for app_id in ids:
        shutil.rmtree(os.path.join(uploads_root(db_path), str(app_id)), ignore_errors=True)
    for resume_id in resume_ids:
        shutil.rmtree(resume_file_folder(db_path, resume_id), ignore_errors=True)
    return {"deleted": len(ids), "files": files}


def purge_application(db_path, profile_id, app_id):
    """Permanently remove a trashed job, its rows and its uploaded files."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Application not found")
        if not row["deleted_at"]:
            raise ApiError(400, "Move the job to the trash before deleting it permanently")
        resume_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM resumes WHERE application_id = ?", (app_id,)
            ).fetchall()
        ]
        for resume_id in resume_ids:
            conn.execute(
                "DELETE FROM resume_files WHERE resume_id = ?", (resume_id,)
            )
        conn.execute("DELETE FROM resumes WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(os.path.join(uploads_root(db_path), str(app_id)), ignore_errors=True)
    for resume_id in resume_ids:
        shutil.rmtree(resume_file_folder(db_path, resume_id), ignore_errors=True)


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

DOCUMENT_COLUMNS = (
    "application_id", "kind", "filename", "stored_name", "content_type", "size", "uploaded_at"
)


PREVIEW_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "text/plain": "text",
    "text/csv": "text",
    "text/markdown": "text",
}


def preview_format(content_type, filename):
    """Return (preview_kind, safe_content_type) for a stored document.

    Only formats that a browser renders inertly are previewable. HTML and SVG are
    deliberately excluded so an uploaded file can never run script in the app's
    origin; they are served as downloads instead.
    """
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in ("", "application/octet-stream", "binary/octet-stream"):
        guessed, _ = mimetypes.guess_type(filename or "")
        ctype = (guessed or "").lower()
    return PREVIEW_TYPES.get(ctype), ctype


def document_dict(row):
    preview, _ = preview_format(row["content_type"], row["filename"])
    return {
        "id": row["id"],
        "application_id": row["application_id"],
        "kind": row["kind"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size": row["size"],
        "uploaded_at": row["uploaded_at"],
        "preview": preview,
        "url": "/api/applications/%d/documents/%d" % (row["application_id"], row["id"]),
    }


def list_documents(conn, app_id):
    rows = conn.execute(
        "SELECT * FROM documents WHERE application_id = ? ORDER BY id ASC", (app_id,)
    ).fetchall()
    return [document_dict(row) for row in rows]


def _document_filename(payload):
    raw = payload.get("filename")
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(400, "A file name is required")
    name = os.path.basename(raw.replace("\\", "/")).strip()
    name = "".join(char for char in name if char.isprintable())[:160]
    if not name:
        raise ApiError(400, "A valid file name is required")
    return name


def save_document(db_path, profile_id, app_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    kind = payload.get("kind") or "other"
    if kind not in DOC_KINDS:
        raise ApiError(400, "kind must be one of: %s" % ", ".join(DOC_KINDS))
    filename = _document_filename(payload)
    content_type = _str(payload, "content_type", 120) or "application/octet-stream"
    encoded = payload.get("data")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ApiError(400, "File data is required")
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "File data is not valid base64")
    if not raw:
        raise ApiError(400, "That file is empty")
    if len(raw) > UPLOAD_LIMIT:
        raise ApiError(413, "Files must be 10 MB or smaller")

    conn = connect(db_path)
    try:
        exists = conn.execute(
            "SELECT id FROM applications WHERE id = ? AND profile_id = ?",
            (app_id, profile_id),
        ).fetchone()
        if exists is None:
            raise ApiError(404, "Application not found")
        # A direct upload can take over a slot that already holds a document; the
        # previous copy and its file go when the new one is in. Attach has its own
        # path because it reports which documents it replaced.
        previous = []
        if payload.get("replace") is True:
            previous = conn.execute(
                "SELECT stored_name FROM documents WHERE application_id = ? AND kind = ?",
                (app_id, kind),
            ).fetchall()
            conn.execute(
                "DELETE FROM documents WHERE application_id = ? AND kind = ?",
                (app_id, kind),
            )
        extension = os.path.splitext(filename)[1].lower().lstrip(".")
        extension = "".join(char for char in extension if char.isalnum())[:10]
        stored_name = uuid.uuid4().hex + (("." + extension) if extension else "")
        folder = os.path.join(uploads_root(db_path), str(app_id))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, stored_name)
        with open(path, "wb") as handle:
            handle.write(raw)
        stamp = now_iso()
        cursor = conn.execute(
            "INSERT INTO documents (%s) VALUES (%s)"
            % (", ".join(DOCUMENT_COLUMNS), ", ".join("?" for _ in DOCUMENT_COLUMNS)),
            (app_id, kind, filename, stored_name, content_type, len(raw), stamp),
        )
        conn.commit()
        for old in previous:
            try:
                os.remove(os.path.join(folder, old["stored_name"]))
            except OSError:
                pass
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return document_dict(row)
    finally:
        conn.close()


def find_document(db_path, profile_id, app_id, doc_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT d.* FROM documents d JOIN applications a"
            " ON a.id = d.application_id"
            " WHERE d.id = ? AND d.application_id = ? AND a.profile_id = ?",
            (doc_id, app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Document not found")
        path = os.path.join(uploads_root(db_path), str(app_id), row["stored_name"])
        if not os.path.isfile(path):
            raise ApiError(404, "That file is missing on disk")
        return row, path
    finally:
        conn.close()


def delete_document(db_path, profile_id, app_id, doc_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT d.stored_name FROM documents d JOIN applications a"
            " ON a.id = d.application_id"
            " WHERE d.id = ? AND d.application_id = ? AND a.profile_id = ?",
            (doc_id, app_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Document not found")
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    path = os.path.join(uploads_root(db_path), str(app_id), row["stored_name"])
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Resumes
# --------------------------------------------------------------------------

RESUME_LIMITS = {"name": 120, "label": 160}
RESUME_SOURCE_LIMIT = 200000
MAX_RESUMES = 60

# Longest first: the preview renders a subset of LaTeX, so a starter that only
# uses the supported commands looks right immediately.
STARTER_RESUME = r"""%% Jobby resume — edit the source here, the preview updates as you type.
%% Download the .tex from Export when you want to compile it in Overleaf.
%% Upload a photo or any support file in the Files rail, then reference it:
%% \includegraphics[width=3cm]{profile.png}
\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}

\begin{document}

\begin{center}
{\LARGE \textbf{%(name)s}}\\[3pt]
%(headline)s\\[2pt]
%(contact)s
\end{center}

\section{Summary}
%(summary)s

\section{Technical Skills}
\textbf{Programming:} C++, Python, SQL\\
\textbf{Tools:} Git, Linux, Docker

\section{Experience}
\textbf{Company Name} \hfill Start -- End\\
\textit{Role title}
\begin{itemize}
\item What you built, the technology you used and the result it had.
\item A second point: scope, numbers, and why it mattered.
\end{itemize}

\section{Education}
\textbf{University Name} \hfill Start -- End\\
M.Sc. in your field

\section{Languages}
English (C1) \hfill German (B1)

\end{document}
"""


LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value):
    """Profile text goes into a LaTeX template, so escape what LaTeX reads."""
    return "".join(LATEX_SPECIALS.get(char, char) for char in (value or ""))


def resume_dict(row, full=False):
    data = {
        "id": row["id"],
        "application_id": row["application_id"],
        "name": row["name"],
        "label": row["label"],
        "size": len(row["source"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if "file_count" in row.keys():
        data["file_count"] = row["file_count"]
    if "job_company" in row.keys():
        data["job_company"] = row["job_company"] or ""
        data["job_role"] = row["job_role"] or ""
    if full:
        data["source"] = row["source"] or ""
    return data


def clean_resume_name(value, fallback="resume.tex"):
    """A file name a person would recognise: no folders, no odd characters."""
    text = os.path.basename(str(value or "").strip().replace("\\", "/"))
    text = re.sub(r"[^A-Za-z0-9._ -]", "", text).strip(" .")
    if not text:
        text = fallback
    if not text.lower().endswith(".tex"):
        text += ".tex"
    return text[: RESUME_LIMITS["name"] + 4]


def clean_resume_source(payload):
    raw = payload.get("source", "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise ApiError(400, "source must be text")
    if len(raw) > RESUME_SOURCE_LIMIT:
        raise ApiError(
            413, "A resume can be at most %d characters" % RESUME_SOURCE_LIMIT
        )
    return raw


def starter_resume(conn, profile_id):
    row = conn.execute(
        "SELECT * FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    profile = dict(row) if row is not None else {}
    name = latex_escape(profile.get("name") or "Your Name")
    headline = latex_escape(
        profile.get("headline") or "Software Engineer | Your focus areas"
    )
    contact = " | ".join(
        latex_escape(value)
        for value in (
            profile.get("location"),
            profile.get("phone"),
            profile.get("email"),
            profile.get("links"),
        )
        if value
    ) or "City, Country | your@email.com | linkedin.com/in/you"
    summary = (
        "One short paragraph on who you are, what you build and what you are "
        "looking for next."
    )
    return STARTER_RESUME % {
        "name": name,
        "headline": headline,
        "contact": contact,
        "summary": summary,
    }


def resume_job_id(conn, profile_id, payload):
    """The job a tailored copy belongs to, or None for a master resume."""
    raw = payload.get("application_id")
    if raw is None or raw == "" or raw == 0:
        return None
    if isinstance(raw, bool):
        raise ApiError(400, "application_id must be a job id")
    try:
        app_id = int(raw)
    except (TypeError, ValueError):
        raise ApiError(400, "application_id must be a job id")
    row = conn.execute(
        "SELECT id FROM applications WHERE id = ? AND profile_id = ?",
        (app_id, profile_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "Application not found")
    return app_id


def resume_file_folder(db_path, resume_id):
    return os.path.join(uploads_root(db_path), "resume-%s" % resume_id)


def resume_file_dict(row, resume_id):
    preview, _ = preview_format(row["content_type"], row["filename"])
    return {
        "id": row["id"],
        "filename": row["filename"],
        "content_type": row["content_type"],
        "size": row["size"],
        "uploaded_at": row["uploaded_at"],
        "preview": preview,
        "url": "/api/resumes/%s/files/%s" % (resume_id, row["id"]),
    }


def list_resume_files(conn, resume_id):
    rows = conn.execute(
        "SELECT * FROM resume_files WHERE resume_id = ? ORDER BY filename COLLATE NOCASE",
        (resume_id,),
    ).fetchall()
    return [resume_file_dict(row, resume_id) for row in rows]


def save_resume_file(db_path, profile_id, resume_id, payload):
    """Attach a support file (a photo, a bibliography, a note) to a resume."""
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    filename = _document_filename(payload)
    content_type = _str(payload, "content_type", 120) or "application/octet-stream"
    encoded = payload.get("data")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ApiError(400, "File data is required")
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "File data is not valid base64")
    if not raw:
        raise ApiError(400, "That file is empty")
    if len(raw) > UPLOAD_LIMIT:
        raise ApiError(413, "Files must be 10 MB or smaller")

    conn = connect(db_path)
    try:
        owner = conn.execute(
            "SELECT id FROM resumes WHERE id = ? AND profile_id = ?",
            (resume_id, profile_id),
        ).fetchone()
        if owner is None:
            raise ApiError(404, "Resume not found")
        extension = os.path.splitext(filename)[1].lower().lstrip(".")
        extension = "".join(char for char in extension if char.isalnum())[:10]
        stored_name = uuid.uuid4().hex + (("." + extension) if extension else "")
        folder = resume_file_folder(db_path, resume_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, stored_name), "wb") as handle:
            handle.write(raw)
        cursor = conn.execute(
            "INSERT INTO resume_files"
            " (resume_id, filename, stored_name, content_type, size, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (resume_id, filename, stored_name, content_type, len(raw), now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM resume_files WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return resume_file_dict(row, resume_id)
    finally:
        conn.close()


def find_resume_file(db_path, profile_id, resume_id, file_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT f.* FROM resume_files f JOIN resumes r ON r.id = f.resume_id"
            " WHERE f.id = ? AND f.resume_id = ? AND r.profile_id = ?",
            (file_id, resume_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "File not found")
        path = os.path.join(
            resume_file_folder(db_path, resume_id), row["stored_name"]
        )
        if not os.path.isfile(path):
            raise ApiError(404, "That file is missing on disk")
        return row, path
    finally:
        conn.close()


def delete_resume_file(db_path, profile_id, resume_id, file_id):
    conn = connect(db_path)
    try:
        row = find_resume_file(db_path, profile_id, resume_id, file_id)[0]
        conn.execute("DELETE FROM resume_files WHERE id = ?", (file_id,))
        conn.commit()
    finally:
        conn.close()
    try:
        os.remove(
            os.path.join(resume_file_folder(db_path, resume_id), row["stored_name"])
        )
    except OSError:
        pass
    return {"deleted": file_id}


def list_saved_files(db_path, profile_id):
    """Every file kept with this profile's resumes, newest first.

    This is the library behind "choose from saved files": the PDFs the resume
    editor saved, plus any support file uploaded beside a .tex.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT f.*, r.name AS resume_name, r.application_id AS resume_job_id,"
            " a.company AS job_company, a.role AS job_role"
            " FROM resume_files f"
            " JOIN resumes r ON r.id = f.resume_id"
            " LEFT JOIN applications a ON a.id = r.application_id"
            " WHERE r.profile_id = ?"
            " ORDER BY f.uploaded_at DESC, f.id DESC",
            (profile_id,),
        ).fetchall()
        files = []
        for row in rows:
            data = resume_file_dict(row, row["resume_id"])
            data["resume_id"] = row["resume_id"]
            data["resume_name"] = row["resume_name"]
            data["job_company"] = row["job_company"] or ""
            data["job_role"] = row["job_role"] or ""
            files.append(data)
        return files
    finally:
        conn.close()


def documents_by_job(db_path, profile_id):
    """What every job already has attached, keyed by job id.

    The Save PDF dialog lists the jobs it can attach to, and showing each one's
    existing documents there is the difference between adding a second resume to
    a job and noticing that only after the fact.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT d.id, d.application_id, d.kind, d.filename FROM documents d"
            " JOIN applications a ON a.id = d.application_id"
            " WHERE a.profile_id = ?"
            " ORDER BY d.id ASC",
            (profile_id,),
        ).fetchall()
    finally:
        conn.close()
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row["application_id"]), []).append(
            {"id": row["id"], "kind": row["kind"], "filename": row["filename"]}
        )
    return grouped


def find_saved_file(db_path, profile_id, file_id):
    """One library file, with its bytes, scoped to this profile."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT f.* FROM resume_files f JOIN resumes r ON r.id = f.resume_id"
            " WHERE f.id = ? AND r.profile_id = ?",
            (file_id, profile_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ApiError(404, "File not found")
    path = os.path.join(resume_file_folder(db_path, row["resume_id"]), row["stored_name"])
    if not os.path.isfile(path):
        raise ApiError(404, "That file is missing on disk")
    return row, path


def replace_kind_documents(db_path, profile_id, app_id, kind):
    """Drop a job's documents of one kind, and their files, before attaching.

    Attaching adds by default; { "replace": true } turns it into an update of
    that slot, so yesterday's tailored resume does not sit beside today's.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT d.id, d.stored_name FROM documents d JOIN applications a"
            " ON a.id = d.application_id"
            " WHERE d.application_id = ? AND a.profile_id = ? AND d.kind = ?",
            (app_id, profile_id, kind),
        ).fetchall()
        if rows:
            conn.execute(
                "DELETE FROM documents WHERE application_id = ? AND kind = ?",
                (app_id, kind),
            )
            conn.commit()
    finally:
        conn.close()
    for row in rows:
        try:
            os.remove(
                os.path.join(uploads_root(db_path), str(app_id), row["stored_name"])
            )
        except OSError:
            pass
    return [row["id"] for row in rows]


def attach_saved_file(db_path, profile_id, file_id, payload):
    """File one of the saved files onto a job, without uploading it again."""
    if payload is not None and not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    fields = payload or {}
    raw_id = fields.get("application_id")
    if isinstance(raw_id, bool) or raw_id in (None, "", 0):
        raise ApiError(400, "A job is required to attach this file")
    try:
        app_id = int(raw_id)
    except (TypeError, ValueError):
        raise ApiError(400, "application_id must be a job id")
    kind = fields.get("kind") or "other"
    if kind not in DOC_KINDS:
        raise ApiError(400, "kind must be one of: %s" % ", ".join(DOC_KINDS))
    row, path = find_saved_file(db_path, profile_id, file_id)
    with open(path, "rb") as handle:
        raw = handle.read()
    # Validate everything that can fail before the old copy goes away.
    replaced = []
    if fields.get("replace") is True:
        replaced = replace_kind_documents(db_path, profile_id, app_id, kind)
    document = save_document(
        db_path,
        profile_id,
        app_id,
        {
            "kind": kind,
            "filename": row["filename"],
            "content_type": row["content_type"],
            "data": base64.b64encode(raw).decode("ascii"),
        },
    )
    return {"document": document, "application_id": app_id, "replaced": replaced}


def list_resumes(db_path, profile_id):
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT r.*, a.company AS job_company, a.role AS job_role,"
            " (SELECT COUNT(*) FROM resume_files f WHERE f.resume_id = r.id) AS file_count"
            " FROM resumes r LEFT JOIN applications a ON a.id = r.application_id"
            " WHERE r.profile_id = ?"
            " ORDER BY r.application_id IS NOT NULL ASC, r.application_id ASC,"
            " r.updated_at DESC",
            (profile_id,),
        ).fetchall()
        return [resume_dict(row) for row in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# LaTeX compilation
# --------------------------------------------------------------------------

MAX_COMPILE_SECONDS = 60
COMPILE_LOCK = threading.Lock()

# Where install_tectonic.py puts the engine it downloads. Keep this in step with
# TOOLS in that script: it is the whole reason the launchers need no setup.
TECTONIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "tectonic")


def latex_engine():
    """The TeX engine to compile with, if this machine has one installed.

    A copy fetched by install_tectonic.py lives in tools/ and is preferred, so
    the launchers work without editing PATH; an engine installed by Homebrew,
    Cargo or Scoop is found on PATH as usual.
    """
    for name in ("tectonic.exe", "tectonic"):
        local = os.path.join(TECTONIC_DIR, name)
        if os.path.isfile(local):
            return local
    return shutil.which("tectonic")


def engine_version(binary):
    """What the engine calls itself, e.g. "Tectonic 0.17.0"; "" if it will not run."""
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    lines = (result.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else ""


# The one-click install in Settings. A download runs in a background thread and
# Settings polls engine_info() for the outcome, so a slow network never holds a
# request open.
ENGINE_INSTALL_LOCK = threading.Lock()
ENGINE_INSTALL = {"state": "idle", "message": ""}


def engine_info():
    """What Settings shows: is Compile able to build a PDF on this machine?"""
    engine = latex_engine()
    with ENGINE_INSTALL_LOCK:
        install = dict(ENGINE_INSTALL)
    state = install["state"]
    if state != "installing":
        # The engine on disk decides, whatever an earlier attempt reported.
        if engine:
            state = "ready"
        elif state == "ready":
            # Installed, but not where Compile looks: say so instead of claiming
            # a download that is no longer there.
            state = "failed"
            install["message"] = (
                "Tectonic was installed but the tracker cannot find it at "
                + TECTONIC_DIR
            )
        elif state != "failed":
            state = "idle"
    return {
        "available": bool(engine),
        "version": engine_version(engine) if engine else "",
        "path": engine or "",
        "state": state,
        "message": install["message"],
    }


def start_engine_install():
    """Begin fetching Tectonic; repeat calls while it runs are harmless."""
    if latex_engine():
        return engine_info()
    with ENGINE_INSTALL_LOCK:
        if ENGINE_INSTALL["state"] == "installing":
            return engine_info()
        ENGINE_INSTALL["state"] = "installing"
        ENGINE_INSTALL["message"] = "Downloading Tectonic…"
    threading.Thread(
        target=run_engine_install, name="tectonic-install", daemon=True
    ).start()
    return engine_info()


def run_engine_install():
    """The download itself, run off the request thread.

    A thread has no caller to raise into, so a failure becomes state + message
    that Settings shows verbatim — the same text the launcher prints.
    """
    try:
        # Into the folder Compile actually searches, whatever the CLI's default is.
        binary = install_tectonic.install_engine(dest_dir=TECTONIC_DIR, quiet=True)
        if not os.path.isfile(binary):
            raise RuntimeError("the download did not leave an engine behind")
        version = engine_version(binary)
        if not version:
            raise RuntimeError("the downloaded Tectonic would not run")
        state, message = "ready", "Tectonic installed at " + binary
    except Exception as error:  # offline, no build for this machine, bad archive
        state = "failed"
        message = str(error) or error.__class__.__name__
    with ENGINE_INSTALL_LOCK:
        ENGINE_INSTALL["state"] = state
        ENGINE_INSTALL["message"] = message


def tex_error_summary(text, limit=3000):
    """The lines of a TeX log worth showing: what broke, and where."""
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    picked = []
    for index, line in enumerate(lines):
        if line.startswith("!") or "error:" in line.lower() or line.startswith("l."):
            picked.append(line.strip())
        elif picked and picked[-1].startswith("!") and line.strip():
            # TeX puts the explanation on the line after the bang.
            picked.append(line.strip())
    if not picked:
        picked = [line.strip() for line in lines if line.strip()][-8:]
    seen = set()
    unique = []
    for line in picked:
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)
    return "\n".join(unique)[:limit].strip()


def compile_resume(db_path, profile_id, resume_id, payload):
    """Compile a resume to a real PDF with tectonic, when it is installed.

    Returns the PDF as base64 plus how long it took. Raises 501 when no engine is
    available (the browser preview is the fallback) and 422 when the source does
    not compile, with the log lines that explain why.
    """
    engine = latex_engine()
    if not engine:
        raise ApiError(501, "No LaTeX engine is installed on this computer")
    resume = get_resume(db_path, profile_id, resume_id)
    source = resume["source"]
    if isinstance(payload, dict) and payload.get("source") is not None:
        source = clean_resume_source(payload)

    started = time.time()
    workdir = tempfile.mkdtemp(prefix="jobpipe-tex-")
    try:
        # Support files keep their uploaded names: \includegraphics{photo.png}
        # has to resolve against the file as the user wrote it.
        conn = connect(db_path)
        try:
            # Newest first, and one copy per name: two uploads called photo.png
            # would otherwise overwrite each other, and whichever the user just
            # replaced is the one the .tex should get.
            files = conn.execute(
                "SELECT filename, stored_name FROM resume_files"
                " WHERE resume_id = ? ORDER BY uploaded_at DESC, id DESC",
                (resume_id,),
            ).fetchall()
        finally:
            conn.close()
        folder = resume_file_folder(db_path, resume_id)
        copied = set()
        for row in files:
            safe_name = os.path.basename(row["filename"])
            if not safe_name or safe_name in (".", "..") or safe_name in copied:
                continue
            source_path = os.path.join(folder, row["stored_name"])
            if os.path.exists(source_path):
                shutil.copyfile(source_path, os.path.join(workdir, safe_name))
                copied.add(safe_name)

        main = os.path.join(workdir, "resume.tex")
        with open(main, "w", encoding="utf-8") as handle:
            handle.write(source)
        outdir = os.path.join(workdir, "out")
        os.makedirs(outdir, exist_ok=True)
        command = [
            engine,
            "--untrusted",
            "--keep-logs",
            "--chatter",
            "minimal",
            "--outdir",
            outdir,
            main,
        ]
        try:
            with COMPILE_LOCK:
                result = subprocess.run(
                    command,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=MAX_COMPILE_SECONDS,
                )
        except subprocess.TimeoutExpired:
            raise ApiError(
                422, "LaTeX took longer than %d seconds" % MAX_COMPILE_SECONDS
            )
        pdf_path = os.path.join(outdir, "resume.pdf")
        log_path = os.path.join(workdir, "resume.log")
        log_text = ""
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                log_text = handle.read()
        if result.returncode != 0 or not os.path.exists(pdf_path):
            summary = tex_error_summary(log_text or result.stderr or result.stdout)
            raise ApiError(
                422, "LaTeX could not compile this resume", extra={"log": summary}
            )
        with open(pdf_path, "rb") as handle:
            body = handle.read()
        reported = engine_version(engine).split()
        version = "tectonic " + reported[-1] if reported else "tectonic"
        return {
            "pdf": base64.b64encode(body).decode("ascii"),
            "engine": version,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def get_resume(db_path, profile_id, resume_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT r.*, a.company AS job_company, a.role AS job_role"
            " FROM resumes r LEFT JOIN applications a ON a.id = r.application_id"
            " WHERE r.id = ? AND r.profile_id = ?",
            (resume_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Resume not found")
        data = resume_dict(row, full=True)
        data["files"] = list_resume_files(conn, resume_id)
        return data
    finally:
        conn.close()


def create_resume(db_path, profile_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    conn = connect(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM resumes WHERE profile_id = ?", (profile_id,)
        ).fetchone()["count"]
        if count >= MAX_RESUMES:
            raise ApiError(400, "You can keep up to %d resumes" % MAX_RESUMES)
        app_id = resume_job_id(conn, profile_id, payload)
        name = clean_resume_name(payload.get("name"), "resume-%d.tex" % (count + 1))
        label = _str(payload, "label", RESUME_LIMITS["label"])
        if not label and app_id is not None:
            job = conn.execute(
                "SELECT company, role FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
            label = " — ".join(part for part in (job["company"], job["role"]) if part)
        if not label:
            label = os.path.splitext(name)[0]

        # Where the text comes from: an explicit copy, the master resume, or a
        # starter written from the profile. A tailored copy starts from the
        # newest master so it is a real starting point, not a blank page.
        source = ""
        template_id = None
        if payload.get("copy_of") not in (None, "", 0):
            try:
                copy_id = int(payload.get("copy_of"))
            except (TypeError, ValueError):
                raise ApiError(400, "copy_of must be a resume id")
            row = conn.execute(
                "SELECT id, source FROM resumes WHERE id = ? AND profile_id = ?",
                (copy_id, profile_id),
            ).fetchone()
            if row is None:
                raise ApiError(404, "Resume to copy not found")
            source = row["source"] or ""
            template_id = row["id"]
        elif "source" in payload:
            source = clean_resume_source(payload)
        else:
            master = conn.execute(
                "SELECT id, source FROM resumes"
                " WHERE profile_id = ? AND application_id IS NULL"
                " ORDER BY updated_at DESC, id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
            if master is not None:
                source = master["source"] or ""
                template_id = master["id"]
            else:
                source = starter_resume(conn, profile_id)

        stamp = now_iso()
        cursor = conn.execute(
            "INSERT INTO resumes"
            " (profile_id, application_id, name, label, source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (profile_id, app_id, name, label, source, stamp, stamp),
        )
        resume_id = cursor.lastrowid

        # A copy keeps its support files: a tailored resume that references the
        # photo still has the photo.
        if template_id is not None:
            rows = conn.execute(
                "SELECT * FROM resume_files WHERE resume_id = ?", (template_id,)
            ).fetchall()
            if rows:
                folder = resume_file_folder(db_path, resume_id)
                os.makedirs(folder, exist_ok=True)
                origin = resume_file_folder(db_path, template_id)
                for row in rows:
                    stored = uuid.uuid4().hex + os.path.splitext(row["stored_name"])[1]
                    try:
                        shutil.copy2(
                            os.path.join(origin, row["stored_name"]),
                            os.path.join(folder, stored),
                        )
                    except OSError:
                        continue
                    conn.execute(
                        "INSERT INTO resume_files"
                        " (resume_id, filename, stored_name, content_type, size, uploaded_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            resume_id,
                            row["filename"],
                            stored,
                            row["content_type"],
                            row["size"],
                            stamp,
                        ),
                    )
        conn.commit()
    finally:
        conn.close()
    return get_resume(db_path, profile_id, resume_id)


def update_resume(db_path, profile_id, resume_id, payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "Expected a JSON object")
    updates = {}
    if "source" in payload:
        updates["source"] = clean_resume_source(payload)
    if "name" in payload:
        updates["name"] = clean_resume_name(payload.get("name"))
    if "label" in payload:
        updates["label"] = _str(payload, "label", RESUME_LIMITS["label"])
    if not updates:
        raise ApiError(400, "Nothing to update")
    updates["updated_at"] = now_iso()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM resumes WHERE id = ? AND profile_id = ?",
            (resume_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Resume not found")
        assignments = update_clause(updates, RESUME_COLUMNS)
        conn.execute(
            "UPDATE resumes SET %s WHERE id = ?" % assignments,
            list(updates.values()) + [resume_id],
        )
        conn.commit()
    finally:
        conn.close()
    return get_resume(db_path, profile_id, resume_id)


def delete_resume(db_path, profile_id, resume_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM resumes WHERE id = ? AND profile_id = ?",
            (resume_id, profile_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "Resume not found")
        conn.execute("DELETE FROM resume_files WHERE resume_id = ?", (resume_id,))
        conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(resume_file_folder(db_path, resume_id), ignore_errors=True)
    return {"deleted": resume_id}


# Analysis a file may carry next to a posting; folded into the notes the app shows.
IMPORT_NOTE_SECTIONS = (
    ("why_it_matches", "Why it matches"),
    ("important_missing_requirements", "Important missing requirements"),
    ("missing_requirements", "Important missing requirements"),
    ("gaps", "Important missing requirements"),
)


def _flat_text(value):
    """A file's value as text: lists become bullet lines, numbers become digits."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            "- " + _flat_text(item) for item in value if _flat_text(item)
        )
    if isinstance(value, dict):
        return "\n".join(
            "- %s: %s" % (key, _flat_text(item))
            for key, item in value.items()
            if _flat_text(item)
        )
    return str(value).strip()


def _has_text(row, key):
    return bool(_flat_text(row.get(key)))


def _clamp_import_text(row):
    """Shorten a field the app would reject instead of losing the whole posting."""
    for key, limit in TEXT_LIMITS.items():
        value = row.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if len(value) > limit:
            row[key] = value[: limit - 1].rstrip() + "\u2026"
        else:
            row[key] = value
    return row


def prepare_import_row(item):
    """Fill in what an exported file leaves out.

    A row that names neither a phase nor a stage was never sent anywhere: it is
    a posting worth applying to, so it lands in *To apply* rather than appearing
    as an application you already made. Any analysis and match estimate the file
    carries is kept, with the written analysis folded into the notes.

    Long text is shortened rather than rejected: an aggregator listing often
    carries a source or URL past the app's limit, and dropping the posting
    because of it would silently lose a job the file did list.
    """
    if not isinstance(item, dict):
        return item
    row = _clamp_import_text(dict(item))
    # Files write "To apply" or "Rejected" where the API wants an exact value.
    for key in ("phase", "status", "work_mode"):
        if isinstance(row.get(key), str):
            row[key] = re.sub(r"[\s-]+", "_", row[key].strip().lower())
    if not _has_text(row, "phase") and not _has_text(row, "status"):
        row["phase"] = "to_apply"
    if not _has_text(row, "match_pct"):
        for key in ("estimated_match_pct", "match_percentage", "resume_match_pct", "match"):
            if _has_text(row, key):
                row["match_pct"] = row[key]
                break
    row["match_pct"] = tolerant_match_pct(row.get("match_pct"))

    blocks = []
    for key in ("notes", "personal_notes"):
        text = _flat_text(row.get(key))
        if text:
            blocks.append(text)
            break
    seen = set()
    for key, label in IMPORT_NOTE_SECTIONS:
        text = _flat_text(row.get(key))
        if not text or label in seen:
            continue
        seen.add(label)
        blocks.append(label + ":\n" + text)
    row["notes"] = "\n\n".join(blocks)[: TEXT_LIMITS["notes"]]
    return row


def import_applications(db_path, profile_id, items, force=False):
    """Insert rows from a file, leaving out jobs the profile already has.

    A file lists postings, not applications, so importing the same one twice used
    to double every job. Rows matching an existing company and role are counted
    as duplicates and reported instead, unless force is set — the import dialog
    asks which of the two the user wants before it comes this far.
    """
    if not isinstance(items, list):
        raise ApiError(400, "Import expects a JSON array of applications")
    stamp = now_iso()
    imported = 0
    skipped = 0
    problems = []
    duplicates = []
    conn = connect(db_path)
    try:
        known = {}
        if not force:
            for row in conn.execute(
                "SELECT id, company, role FROM applications"
                " WHERE profile_id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchall():
                known.setdefault(job_key(row["company"], row["role"]), row["id"])
        for index, item in enumerate(items):
            try:
                data = clean_create(prepare_import_row(item))
            except ApiError as error:
                skipped += 1
                company = item.get("company") if isinstance(item, dict) else None
                problems.append(
                    {
                        "row": index + 1,
                        "company": _flat_text(company)[:160],
                        "reason": error.message,
                    }
                )
                continue
            key = job_key(data["company"], data["role"])
            if not force and key in known:
                skipped += 1
                duplicates.append(
                    {
                        "row": index + 1,
                        "company": data["company"],
                        "role": data["role"],
                        "existing_id": known[key],
                    }
                )
                continue
            columns = list(data.keys()) + ["profile_id", "created_at", "updated_at"]
            values = list(data.values()) + [profile_id, stamp, stamp]
            cursor = conn.execute(
                "INSERT INTO applications (%s) VALUES (%s)"
                % (", ".join(columns), ", ".join("?" for _ in columns)),
                values,
            )
            conn.execute(
                "INSERT INTO status_events (application_id, from_status, to_status, created_at)"
                " VALUES (?, ?, ?, ?)",
                (cursor.lastrowid, None, data["status"], stamp),
            )
            if data.get("phase", "applied") != "to_apply":
                record_sent(
                    conn, profile_id, cursor.lastrowid, data.get("applied_on") or stamp
                )
            known.setdefault(key, cursor.lastrowid)
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return imported, skipped, problems, duplicates


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "JobTracker/1.0"
    protocol_version = "HTTP/1.1"

    # Set by main()
    db_path = DEFAULT_DB
    # Host names to accept besides loopback (only when --host binds elsewhere).
    allowed_hosts = frozenset()
    # When the last request arrived. --exit-when-idle watches this.
    last_seen = time.monotonic()
    # The open dashboard pages, when the app started this server. None in a
    # terminal, where the server keeps running until Ctrl+C.
    auto_stop = None

    def log_message(self, fmt, *args):
        sys.stderr.write(
            "%s  %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args)
        )

    def handle_one_request(self):
        # Browsers routinely drop keep-alive sockets; that is not an error worth a
        # traceback in the terminal.
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    # -- helpers ---------------------------------------------------------
    def drain_body(self):
        """Discard an unread request body. A body left in the socket would be
        parsed as the next request line on a keep-alive connection."""
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY:
            try:
                self.rfile.read(length)
            except (OSError, ValueError):
                self.close_connection = True

    def send_json(self, payload, status=200):
        self.drain_body()
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def send_document(self, path, content_type, filename, inline=False):
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            return self.send_error_json(404, "That file is missing on disk")
        preview, safe_type = preview_format(content_type, filename)
        can_preview = bool(inline and preview)
        total = len(body)

        # Browsers render PDFs with a viewer that asks for byte ranges. Answering
        # every request with the whole file makes it retry forever, so honour
        # Range here (a single range is all the viewer needs).
        start, end, partial = 0, total - 1, False
        range_header = (self.headers.get("Range") or "").strip()
        match = re.match(r"^bytes=(\d*)-(\d*)$", range_header)
        if match and total:
            raw_start, raw_end = match.groups()
            if not raw_start and not raw_end:
                match = None
            elif raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else total - 1
            else:
                start = max(0, total - int(raw_end))
            if match:
                if start >= total or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % total)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, total - 1)
                partial = True

        chunk = body[start : end + 1]
        self.send_response(206 if partial else 200)
        self.send_header(
            "Content-Type", safe_type if can_preview else (content_type or "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, total))
        self.send_header(
            "Content-Disposition", "%s; filename*=UTF-8''%s" % ("inline" if can_preview else "attachment", quote(filename))
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(chunk)

    def send_archive(self, path, download_name):
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''%s" % quote(download_name),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self):
        self._body_read = True
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Invalid Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError(413, "Request body is too large")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Invalid JSON body")

    # -- static files ----------------------------------------------------
    def serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        relative = unquote(path.lstrip("/"))
        target = os.path.normpath(os.path.join(PUBLIC_DIR, relative))
        if not target.startswith(PUBLIC_DIR + os.sep) and target != PUBLIC_DIR:
            self.send_error_json(403, "Forbidden")
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self.send_error_json(404, "Not found")
            return
        extension = os.path.splitext(target)[1].lower()
        content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
        with open(target, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            policy = content_security_policy()
            if policy:
                self.send_header("Content-Security-Policy", policy)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- routing ---------------------------------------------------------
    def route(self):
        Handler.last_seen = time.monotonic()
        parsed = urlparse(self.path)
        path = parsed.path
        self._body_read = False
        self.query = parse_qs(parsed.query)
        parts = [unquote(part) for part in path.split("/") if part]

        if parts and parts[0] == "api":
            return self.api(parts[1:])

        if self.command in ("GET", "HEAD"):
            return self.serve_static(path)
        return self.send_error_json(405, "Method not allowed")

    def origin_allowed(self):
        """True when a write came from a page this server itself is serving."""
        source = (self.headers.get("Origin") or "").strip()
        if source in ("", "null"):
            # Older browsers only send Referer on same-origin writes.
            source = (self.headers.get("Referer") or "").strip()
        if not source:
            return False
        parsed = urlparse(source)
        if parsed.scheme != "http":
            return False
        origin_host = host_name(parsed.netloc)
        if origin_host not in LOOPBACK_HOSTS and origin_host not in self.allowed_hosts:
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        return port == self.server.server_address[1]

    def guard(self, parts):
        """Refuse API calls that did not come from this dashboard.

        Any web page the browser visits can send requests to a local port, so the
        API checks who is asking: the health probe stays open for the start
        screen, document downloads stay header-free because browsers open them
        directly, and everything else needs the loopback Host, a same-origin
        write and the token from /api/session.
        """
        if parts[0] == "health":
            return
        host = host_name(self.headers.get("Host") or "")
        if host not in LOOPBACK_HOSTS and host not in self.allowed_hosts:
            raise ApiError(403, "This server only answers on this machine")

        # Browsers open these directly (an image in the preview, a download
        # link), so they cannot carry the session header either.
        is_document_download = (
            len(parts) == 4
            and parts[2] in ("documents", "files")
            and parts[0] in ("applications", "resumes")
        )
        if is_document_download or parts[0] == "session":
            return
        if self.command not in ("GET", "HEAD") and not self.origin_allowed():
            raise ApiError(403, "Requests must come from the dashboard itself")
        token = (self.headers.get("X-Api-Token") or "").strip()
        if not hmac.compare_digest(token, API_TOKEN):
            raise ApiError(403, "Session expired — reload the dashboard")

    def profile_id(self):
        """The profile a request works on: X-Profile-Id, else the first profile."""
        raw = (self.headers.get("X-Profile-Id") or "").strip()
        if not raw:
            value = default_profile_id(self.db_path)
        else:
            try:
                value = int(raw)
            except ValueError:
                raise ApiError(400, "Invalid profile id")
            conn = connect(self.db_path)
            try:
                row = conn.execute("SELECT id FROM profiles WHERE id = ?", (value,)).fetchone()
            finally:
                conn.close()
            if row is None:
                raise ApiError(404, "Profile not found")
        self.require_unlock(value)
        return value

    def require_unlock(self, profile_id):
        """A PIN-protected profile answers only with a valid unlock token."""
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT pin_hash FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ApiError(404, "Profile not found")
        if not row["pin_hash"]:
            return
        token = (self.headers.get("X-Profile-Token") or "").strip()
        if token_is_valid(token, profile_id):
            return
        raise ApiError(423, "This profile is locked")

    def profiles(self, parts):
        if not parts:
            if self.command == "GET":
                return self.send_json(list_profiles(self.db_path))
            if self.command == "POST":
                return self.send_json(create_profile(self.db_path, self.read_json()), 201)
            raise ApiError(405, "Method not allowed")
        try:
            profile_id = int(parts[0])
        except ValueError:
            raise ApiError(404, "Unknown endpoint")

        if len(parts) == 2:
            action = parts[1]
            if self.command != "POST":
                raise ApiError(405, "Method not allowed")
            if action == "unlock":
                return self.send_json(
                    unlock_profile(self.db_path, profile_id, self.read_json())
                )
            if action == "lock":
                revoke_tokens(profile_id)
                profile = get_profile(self.db_path, profile_id)
                return self.send_json({"locked": profile["locked"]})
            if action == "pin":
                self.require_unlock(profile_id)
                return self.send_json(
                    set_profile_pin(self.db_path, profile_id, self.read_json())
                )
            raise ApiError(404, "Unknown endpoint")

        if len(parts) != 1:
            raise ApiError(404, "Unknown endpoint")
        if self.command in ("GET", "HEAD"):
            return self.send_json(get_profile(self.db_path, profile_id))
        if self.command in ("PATCH", "PUT"):
            self.require_unlock(profile_id)
            return self.send_json(update_profile(self.db_path, profile_id, self.read_json()))
        if self.command == "DELETE":
            # A profile can only be removed from inside itself, never from
            # another profile's dashboard.
            if self.profile_id() != profile_id:
                raise ApiError(403, "Delete a profile from inside it")
            return self.send_json(delete_profile(self.db_path, profile_id))
        raise ApiError(405, "Method not allowed")

    def api(self, parts):
        try:
            if not parts:
                raise ApiError(404, "Unknown endpoint")

            if parts[0] == "health" and self.command == "GET":
                if Handler.auto_stop is not None:
                    client = (self.query.get("client") or [""])[0].strip()
                    if client:
                        loaded = (self.query.get("hello") or [""])[0] == "1"
                        if loaded:
                            Handler.auto_stop.hello(client)
                        else:
                            Handler.auto_stop.touch(client)
                return self.send_json({"ok": True})

            if parts[0] == "bye" and self.command == "POST":
                # The last thing a closing dashboard says. It carries no token
                # because a beacon on unload cannot reliably send headers, and no
                # Origin either. What protects it is the id: a page can only end
                # itself, and an id nothing has registered changes nothing.
                if Handler.auto_stop is not None:
                    payload = self.read_json()
                    client = payload.get("client") if isinstance(payload, dict) else None
                    if isinstance(client, str) and client.strip():
                        Handler.auto_stop.bye(client.strip())
                return self.send_json({"ok": True})

            self.guard(parts)

            if parts[0] == "session" and self.command == "GET":
                return self.send_json({"token": API_TOKEN})

            if parts[0] == "backup" and self.command == "GET":
                archive = build_backup_archive(self.db_path)
                return self.send_archive(
                    archive,
                    "jobby-backup-%s.zip" % datetime.now().strftime("%Y-%m-%d"),
                )

            if parts[0] == "profiles":
                return self.profiles(parts[1:])

            profile_id = self.profile_id()

            if parts[0] == "extract":
                if self.command != "POST":
                    raise ApiError(405, "Method not allowed")
                payload = self.read_json()
                target = payload.get("url") if isinstance(payload, dict) else None
                if not isinstance(target, str) or not target.strip():
                    raise ApiError(400, "A job posting URL is required")
                try:
                    return self.send_json(extract_job(target.strip()))
                except ExtractionError as error:
                    raise ApiError(422, str(error))

            if parts[0] == "resumes":
                if len(parts) == 1:
                    if self.command == "GET":
                        return self.send_json(list_resumes(self.db_path, profile_id))
                    if self.command == "POST":
                        return self.send_json(
                            create_resume(self.db_path, profile_id, self.read_json()), 201
                        )
                    raise ApiError(405, "Method not allowed")
                if len(parts) == 3 and parts[2] == "compile":
                    if self.command != "POST":
                        raise ApiError(405, "Method not allowed")
                    try:
                        resume_id = int(parts[1])
                    except ValueError:
                        raise ApiError(404, "Unknown endpoint")
                    payload = self.read_json()
                    return self.send_json(
                        compile_resume(self.db_path, profile_id, resume_id, payload)
                    )
                if len(parts) >= 3 and parts[2] == "files":
                    try:
                        resume_id = int(parts[1])
                    except ValueError:
                        raise ApiError(404, "Unknown endpoint")
                    if len(parts) == 3:
                        if self.command != "POST":
                            raise ApiError(405, "Method not allowed")
                        return self.send_json(
                            save_resume_file(
                                self.db_path, profile_id, resume_id, self.read_json()
                            ),
                            201,
                        )
                    if len(parts) == 4:
                        try:
                            file_id = int(parts[3])
                        except ValueError:
                            raise ApiError(404, "Unknown endpoint")
                        if self.command in ("GET", "HEAD"):
                            row, path = find_resume_file(
                                self.db_path, profile_id, resume_id, file_id
                            )
                            return self.send_document(
                                path, row["content_type"], row["filename"], inline=True
                            )
                        if self.command == "DELETE":
                            return self.send_json(
                                delete_resume_file(
                                    self.db_path, profile_id, resume_id, file_id
                                )
                            )
                        raise ApiError(405, "Method not allowed")
                    raise ApiError(404, "Unknown endpoint")

                if len(parts) == 2:
                    try:
                        resume_id = int(parts[1])
                    except ValueError:
                        raise ApiError(404, "Unknown endpoint")
                    if self.command == "GET":
                        return self.send_json(
                            get_resume(self.db_path, profile_id, resume_id)
                        )
                    if self.command in ("PATCH", "PUT"):
                        return self.send_json(
                            update_resume(
                                self.db_path, profile_id, resume_id, self.read_json()
                            )
                        )
                    if self.command == "DELETE":
                        return self.send_json(
                            delete_resume(self.db_path, profile_id, resume_id)
                        )
                    raise ApiError(405, "Method not allowed")
                raise ApiError(404, "Unknown endpoint")

            if parts[0] == "documents":
                # What each job already has, for the Save PDF dialog.
                if len(parts) != 1:
                    raise ApiError(404, "Unknown endpoint")
                if self.command != "GET":
                    raise ApiError(405, "Method not allowed")
                return self.send_json(documents_by_job(self.db_path, profile_id))

            if parts[0] == "files":
                # The library a job's Documents section picks from.
                if len(parts) == 1:
                    if self.command != "GET":
                        raise ApiError(405, "Method not allowed")
                    return self.send_json(list_saved_files(self.db_path, profile_id))
                if len(parts) == 3 and parts[2] == "attach":
                    if self.command != "POST":
                        raise ApiError(405, "Method not allowed")
                    try:
                        file_id = int(parts[1])
                    except ValueError:
                        raise ApiError(404, "Unknown endpoint")
                    return self.send_json(
                        attach_saved_file(
                            self.db_path, profile_id, file_id, self.read_json()
                        )
                    )
                raise ApiError(404, "Unknown endpoint")

            if parts[0] == "trash":
                if len(parts) != 1:
                    raise ApiError(404, "Unknown endpoint")
                if self.command != "DELETE":
                    raise ApiError(405, "Method not allowed")
                return self.send_json(purge_trash(self.db_path, profile_id))

            if parts[0] == "settings":
                if len(parts) != 1:
                    raise ApiError(404, "Unknown endpoint")
                if self.command == "GET":
                    return self.send_json(get_settings(self.db_path, profile_id))
                if self.command in ("PATCH", "PUT"):
                    return self.send_json(
                        save_settings(self.db_path, profile_id, self.read_json())
                    )
                raise ApiError(405, "Method not allowed")

            if parts[0] == "engine":
                # Machine-wide rather than per profile, but reached from Settings.
                if len(parts) == 1:
                    if self.command != "GET":
                        raise ApiError(405, "Method not allowed")
                    return self.send_json(engine_info())
                if len(parts) == 2 and parts[1] == "install":
                    if self.command != "POST":
                        raise ApiError(405, "Method not allowed")
                    return self.send_json(start_engine_install())
                raise ApiError(404, "Unknown endpoint")

            if parts[0] == "sent":
                # Applications sent, per day. Kept apart from the job rows so that
                # deleting one cannot make it look like it was never sent.
                if len(parts) != 1:
                    raise ApiError(404, "Unknown endpoint")
                if self.command != "GET":
                    raise ApiError(405, "Method not allowed")
                return self.send_json(sent_counts(self.db_path, profile_id))

            if parts[0] != "applications" and parts[0] != "import":
                raise ApiError(404, "Unknown endpoint")

            if parts[0] == "import":
                if self.command != "POST":
                    raise ApiError(405, "Method not allowed")
                payload = self.read_json()
                items = payload.get("applications") if isinstance(payload, dict) else payload
                force = isinstance(payload, dict) and payload.get("force") is True
                imported, skipped, problems, duplicates = import_applications(
                    self.db_path, profile_id, items, force=force
                )
                return self.send_json(
                    {
                        "imported": imported,
                        "skipped": skipped,
                        "problems": problems,
                        "duplicates": duplicates,
                    }
                )

            if parts[0] == "applications" and len(parts) == 3 and parts[2] == "restore":
                if self.command != "POST":
                    raise ApiError(405, "Method not allowed")
                try:
                    app_id = int(parts[1])
                except ValueError:
                    raise ApiError(404, "Unknown endpoint")
                return self.send_json(restore_application(self.db_path, profile_id, app_id))

            if parts[0] == "applications" and len(parts) >= 3 and parts[2] == "documents":
                try:
                    app_id = int(parts[1])
                except ValueError:
                    raise ApiError(404, "Unknown endpoint")
                if len(parts) == 3:
                    if self.command != "POST":
                        raise ApiError(405, "Method not allowed")
                    document = save_document(
                        self.db_path, profile_id, app_id, self.read_json()
                    )
                    return self.send_json(document, 201)
                if len(parts) == 4:
                    try:
                        doc_id = int(parts[3])
                    except ValueError:
                        raise ApiError(404, "Unknown endpoint")
                    if self.command in ("GET", "HEAD"):
                        row, path = find_document(self.db_path, profile_id, app_id, doc_id)
                        inline = (self.query.get("inline") or ["0"])[0].lower() in (
                            "1", "true", "yes"
                        )
                        return self.send_document(
                            path, row["content_type"], row["filename"], inline=inline
                        )
                    if self.command == "DELETE":
                        delete_document(self.db_path, profile_id, app_id, doc_id)
                        return self.send_json({"deleted": doc_id})
                    raise ApiError(405, "Method not allowed")
                raise ApiError(404, "Unknown endpoint")

            if len(parts) == 1:
                if self.command == "GET":
                    trashed = (self.query.get("trash") or ["0"])[0].lower() in ("1", "true", "yes")
                    return self.send_json(
                        list_applications(self.db_path, profile_id, trashed=trashed)
                    )
                if self.command == "POST":
                    payload = self.read_json()
                    data = clean_create(payload)
                    # Same company and role as a job you already have: say which
                    # one, and let the form decide whether to add a second copy.
                    if payload.get("force") is not True:
                        matches = duplicate_jobs(
                            self.db_path, profile_id, data["company"], data["role"]
                        )
                        if matches:
                            raise ApiError(
                                409,
                                "That company and role are already on your list",
                                {"matches": matches},
                            )
                    return self.send_json(
                        create_application(self.db_path, profile_id, data), 201
                    )
                raise ApiError(405, "Method not allowed")

            if len(parts) == 2:
                try:
                    app_id = int(parts[1])
                except ValueError:
                    raise ApiError(404, "Unknown endpoint")

                if self.command in ("GET", "HEAD"):
                    application, events, documents = get_application(
                        self.db_path, profile_id, app_id
                    )
                    return self.send_json(
                        {
                            "application": application,
                            "events": events,
                            "documents": documents,
                        }
                    )
                if self.command in ("PATCH", "PUT"):
                    data = clean_patch(self.read_json())
                    return self.send_json(
                        update_application(self.db_path, profile_id, app_id, data)
                    )
                if self.command == "DELETE":
                    permanent = (self.query.get("permanent") or ["0"])[0].lower() in (
                        "1", "true", "yes"
                    )
                    if permanent:
                        purge_application(self.db_path, profile_id, app_id)
                        return self.send_json({"permanent": True, "deleted": app_id})
                    application = soft_delete_application(
                        self.db_path, profile_id, app_id
                    )
                    return self.send_json({"trashed": app_id, "application": application})
                raise ApiError(405, "Method not allowed")

            raise ApiError(404, "Unknown endpoint")
        except ApiError as error:
            if error.extra:
                payload = {"error": error.message}
                payload.update(error.extra)
                return self.send_json(payload, error.status)
            return self.send_error_json(error.status, error.message)
        except (BrokenPipeError, ConnectionResetError):
            # The browser closed the socket (a preview iframe or tab being closed).
            self.close_connection = True
        except Exception as error:  # keep the API JSON-only on unexpected failures
            sys.stderr.write("Unhandled error: %r\n" % (error,))
            return self.send_error_json(500, "Server error")

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def do_POST(self):
        self.route()

    def do_PATCH(self):
        self.route()

    def do_PUT(self):
        self.route()

    def do_DELETE(self):
        self.route()


class DashboardClients:
    """The dashboard pages that are open, so the server can stop with them.

    A server started by Jobby.app has no window of its own: it lives only as long
    as the dashboard does. Every tab or window sends a random id with its
    heartbeat; when one closes it says goodbye, and once none is left the server
    stops. A short pause before that means a reload — goodbye from the old page,
    hello from the new one — leaves the server alone.

    Only the app installs this: a server started from a terminal keeps running
    until Ctrl+C, and the idle watch above stays as the backstop for a page that
    vanishes without saying anything (a crash, a killed process).
    """

    def __init__(self, grace=5.0, forget=1800.0, clock=time.monotonic, schedule=None):
        self._lock = threading.Lock()
        self._seen = {}
        # Pages that have said goodbye. A heartbeat already on its way must not
        # bring one back; only a page *load* (a hello) can.
        self._closed = {}
        self.grace = grace
        self.forget = forget
        self.clock = clock
        self.schedule = schedule or self._later
        self.on_empty = None
        self._pending = False

    @staticmethod
    def _later(seconds, callback):
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        timer.start()
        return timer

    def hello(self, client):
        """A page has loaded: it is here, whatever happened to its last visit."""
        with self._lock:
            self._closed.pop(client, None)
            self._seen[client] = self.clock()

    def touch(self, client):
        """A heartbeat. It keeps a page alive, but cannot revive a closed one."""
        with self._lock:
            if client in self._closed:
                return
            self._seen[client] = self.clock()

    def bye(self, client):
        with self._lock:
            # An id this server has not seen cannot end anything; that is what
            # keeps a stray request from a web page from stopping the server.
            if client not in self._seen:
                return
            del self._seen[client]
            self._closed[client] = self.clock()
            if self._seen or self._pending:
                return
            self._pending = True
        self.schedule(self.grace, self._check)

    def _check(self):
        with self._lock:
            self._pending = False
            now = self.clock()
            for key in [key for key, seen in self._seen.items() if seen < now - self.forget]:
                del self._seen[key]
            # A closed id is forgotten soon after; a page that comes back later
            # (a reload that took its time) is simply a page again.
            for key in [key for key, closed in self._closed.items() if closed < now - 30]:
                del self._closed[key]
            empty = not self._seen
        if empty and self.on_empty is not None:
            self.on_empty()


def watch_idle(server, idle_seconds, interval=None, clock=time.monotonic, sleep=time.sleep, report=print):
    """Stop the server once nothing has asked it for anything for a while.

    An open dashboard asks for /api/health every 20 seconds, so a quiet server
    means no window is left. Only the app starts the server this way: one started
    in a terminal stays up until Ctrl+C.
    """
    if interval is None:
        interval = min(5.0, max(0.5, idle_seconds / 4.0))
    while True:
        sleep(interval)
        if clock() - Handler.last_seen > idle_seconds:
            report("\nNo dashboard has asked for anything for %g seconds - stopping." % idle_seconds)
            server.shutdown()
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jobby — Your Job Applications Tracking Buddy")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to the SQLite database")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in a browser")
    parser.add_argument(
        "--exit-when-idle",
        type=float,
        default=0,
        metavar="SECONDS",
        help="stop once no request has arrived for this many seconds",
    )
    args = parser.parse_args(argv)

    init_db(args.db)
    Handler.db_path = args.db
    if host_name(args.host) not in LOOPBACK_HOSTS:
        # Serving beyond loopback is explicit, so accept that host name too.
        Handler.allowed_hosts = frozenset({host_name(args.host)})
    backup_database(args.db)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d" % (args.host, args.port)
    print("Jobby — Your Job Applications Tracking Buddy")
    print("Running at %s" % url)
    print("Database: %s" % os.path.abspath(args.db))
    print("Press Ctrl+C to stop")
    if args.exit_when_idle > 0:
        print("Stops on its own after %g seconds with no request" % args.exit_when_idle)
        threading.Thread(
            target=watch_idle, args=(server, args.exit_when_idle), daemon=True
        ).start()
        # ...and sooner than that when the last dashboard says it is closing.
        clients = DashboardClients()
        clients.on_empty = server.shutdown
        Handler.auto_stop = clients
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
