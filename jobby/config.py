"""Jobby — Settings, constants and the small helpers every part shares."""

from datetime import datetime, timezone
import base64
import hashlib
import os
import re
import secrets

#!/usr/bin/env python3
"""Local job application tracker.

Serves a small JSON API backed by SQLite plus the static frontend in ./public.

  python3 server.py                # http://127.0.0.1:8787
  python3 server.py --port 9000 --open
"""




# The project root: this file lives one level down, in jobby/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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



CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}



# index.html carries two small inline scripts (the file: start screen and the
# module loader). Their hashes are taken from the file as it is served, so
# script-src can stay free of 'unsafe-inline'.
INLINE_SCRIPT = re.compile(rb"<script[^>]*>(.*?)</script>", re.S)


CSP_CACHE: dict = {"stamp": None, "policy": None}




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
