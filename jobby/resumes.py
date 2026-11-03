"""Jobby — Resumes: sources, tailored copies, and the LaTeX engine."""

import base64
import install_tectonic
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from jobby.config import ApiError, RESUME_COLUMNS, ROOT, _str, now_iso, update_clause
from jobby.db import connect
from jobby.documents import list_resume_files, resume_file_folder



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
TECTONIC_DIR = os.path.join(ROOT, "tools", "tectonic")




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
        copy_of = payload.get("copy_of")
        if copy_of is not None and copy_of not in ("", 0):
            try:
                copy_id = int(copy_of)
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
