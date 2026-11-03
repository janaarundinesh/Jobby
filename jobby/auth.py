"""Jobby — Profiles, their PINs, and the unlock tokens a session carries."""

import hashlib
import hmac
import os
import secrets
import shutil
import threading
import time

from jobby.config import ApiError, PIN_FORGET_AFTER, PIN_FREE_ATTEMPTS, PIN_ITERATIONS, PIN_LOCK_BASE, PIN_LOCK_MAX, PIN_MAX, PIN_MIN, PROFILE_COLUMNS, PROFILE_LIMITS, UNLOCK_TTL, _str, now_iso, update_clause
from jobby.db import connect, uploads_root
from jobby.documents import resume_file_folder

PIN_FAILURES: dict = {}


PIN_LOCK = threading.Lock()



# Unlock tokens live in memory: restarting the server locks every profile again.
UNLOCK_TOKENS: dict = {}


TOKEN_LOCK = threading.Lock()




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
