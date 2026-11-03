"""Jobby — Uploaded files: job documents and a resume's support files."""

import base64
import binascii
import mimetypes
import os
import uuid

from jobby.config import ApiError, DOC_KINDS, UPLOAD_LIMIT, _str, now_iso
from jobby.db import connect, uploads_root



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
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(str(row["application_id"]), []).append(
            {"id": row["id"], "kind": row["kind"], "filename": row["filename"]}
        )
    return grouped




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
    if raw_id is None or isinstance(raw_id, bool) or raw_id in ("", 0):
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
