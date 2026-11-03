"""Jobby — The HTTP layer: routing, guards and the server's lifecycle."""

from typing import Any

from datetime import datetime
from extract import ExtractionError, extract_job
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
import argparse
import hmac
import json
import os
import re
import sys
import threading
import time

from jobby.applications import clean_create, clean_patch, create_application, duplicate_jobs, get_application, get_settings, import_applications, list_applications, purge_application, purge_trash, restore_application, save_settings, soft_delete_application, update_application
from jobby.auth import create_profile, default_profile_id, delete_profile, get_profile, list_profiles, revoke_tokens, set_profile_pin, token_is_valid, unlock_profile, update_profile
from jobby.config import API_TOKEN, ApiError, CONTENT_TYPES, DEFAULT_DB, LOOPBACK_HOSTS, MAX_BODY, PUBLIC_DIR, content_security_policy, host_name
from jobby.db import backup_database, build_backup_archive, connect, init_db, list_backups, restore_backup, sent_counts
from jobby.documents import attach_saved_file, delete_document, delete_resume_file, documents_by_job, find_document, find_resume_file, list_saved_files, preview_format, save_document, save_resume_file
from jobby.resumes import compile_resume, create_resume, delete_resume, engine_info, get_resume, list_resumes, start_engine_install, update_resume



# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "JobTracker/1.0"
    protocol_version = "HTTP/1.1"

    # Set by main()
    db_path = DEFAULT_DB
    # Host names to accept besides loopback (only when --host binds elsewhere).
    allowed_hosts: frozenset = frozenset()
    # When the last request arrived. --exit-when-idle watches this.
    last_seen = time.monotonic()
    # The open dashboard pages, when the app started this server. None in a
    # terminal, where the server keeps running until Ctrl+C.
    auto_stop: Any = None

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
        address = self.server.server_address
        return isinstance(address, tuple) and port == address[1]

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
                    who = payload.get("client") if isinstance(payload, dict) else None
                    if isinstance(who, str) and who.strip():
                        Handler.auto_stop.bye(who.strip())
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

            if parts[0] == "backups":
                # The daily snapshots this computer already keeps, and putting
                # one back. Restoring is a write: the guard above asked for the
                # session token and a same-origin Origin.
                if len(parts) == 1:
                    if self.command != "GET":
                        raise ApiError(405, "Method not allowed")
                    return self.send_json({"backups": list_backups(self.db_path)})
                if len(parts) == 3 and parts[2] == "restore":
                    if self.command != "POST":
                        raise ApiError(405, "Method not allowed")
                    return self.send_json(restore_backup(self.db_path, parts[1]))
                raise ApiError(404, "Unknown endpoint")

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
