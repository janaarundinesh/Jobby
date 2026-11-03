"""Jobby — Jobs: validation, storage, the trash and imports."""

import os
import re
import shutil

from jobby.config import APPLICATION_COLUMNS, ApiError, DEFAULT_SETTINGS, SETTING_LIMITS, TEXT_LIMITS, _date, _match_pct, _phase, _status, _str, _work_mode, now_iso, today_iso, tolerant_match_pct, update_clause
from jobby.db import app_dict, connect, event_dict, forget_sent, record_sent, uploads_root
from jobby.documents import list_documents, resume_file_folder



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
        known: dict = {}
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
