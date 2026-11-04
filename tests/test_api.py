"""End-to-end tests for the job tracker JSON API."""

import base64
import datetime
import time

import pytest

import server


def test_health(api):
    response = api.get("/api/health")
    assert response.status == 200
    assert response.data == {"ok": True}


def test_a_closing_dashboard_says_goodbye_without_the_token(api, monkeypatch):
    # Everything else on the API needs the session token; a beacon sent on
    # unload cannot carry one, so this one call is accepted from the dashboard's
    # own origin alone.
    pending = []
    tracker = server.DashboardClients(schedule=lambda seconds, callback: pending.append(callback))
    stopped = []
    tracker.on_empty = lambda: stopped.append(True)
    monkeypatch.setattr(server.Handler, "auto_stop", tracker)

    assert api.get("/api/health?client=tab-1").status == 200
    assert api.post("/api/bye", {"client": "tab-1"}).status == 200
    assert len(pending) == 1, "the goodbye did not schedule a stop"
    pending[0]()
    assert stopped == [True]


def test_a_goodbye_for_an_unknown_page_changes_nothing(api, monkeypatch):
    # Anything on this machine can post to the port, but it cannot know the id a
    # dashboard tab keeps to itself, so a guessed one ends nothing.
    pending = []
    tracker = server.DashboardClients(schedule=lambda seconds, callback: pending.append(callback))
    monkeypatch.setattr(server.Handler, "auto_stop", tracker)

    assert api.get("/api/health?client=tab-1").status == 200
    response = api.post("/api/bye", {"client": "made-up"})
    assert response.status == 200
    assert pending == []


def test_create_defaults_and_list(api):
    created = api.create(company="Acme", role="Engineer")
    assert created["phase"] == "applied"
    assert created["status"] == "applied"
    assert created["applied_on"] == datetime.date.today().isoformat()
    assert created["company"] == "Acme"

    listed = api.get("/api/applications")
    assert listed.status == 200
    assert [app["id"] for app in listed.data] == [created["id"]]


def test_to_apply_clears_applied_date(api):
    saved = api.create(company="Globex", role="Designer", phase="to_apply", applied_on="2020-01-01")
    assert saved["phase"] == "to_apply"
    assert saved["applied_on"] == ""


def test_required_fields(api):
    assert api.post("/api/applications", {"role": "Engineer"}).status == 400
    assert api.post("/api/applications", {"company": "Acme"}).status == 400
    assert api.post("/api/applications", {"company": "  ", "role": "Engineer"}).status == 400


def test_invalid_enum_values(api):
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng", "status": "nope"}).status == 400
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng", "phase": "later"}).status == 400
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng", "work_mode": "moon"}).status == 400
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng", "applied_on": "31/02/2026"}).status == 400


def test_unknown_routes_and_methods(api):
    assert api.get("/api/nope").status == 404
    assert api.get("/api/applications/abc").status == 404
    assert api.get("/api/applications/9999").status == 404
    assert api.patch("/api/trash", {}).status == 405
    assert api.post("/api/applications/1", {}).status == 405


def test_detail_includes_creation_event(api):
    created = api.create()
    response = api.get("/api/applications/%d" % created["id"])
    assert response.status == 200
    assert response.data["application"]["id"] == created["id"]
    assert response.data["documents"] == []
    assert len(response.data["events"]) == 1
    assert response.data["events"][0]["to_status"] == "applied"


def test_patch_status_logs_event(api):
    created = api.create()
    patched = api.patch("/api/applications/%d" % created["id"], {"status": "screening"})
    assert patched.status == 200
    assert patched.data["status"] == "screening"

    detail = api.get("/api/applications/%d" % created["id"]).data
    assert [event["to_status"] for event in detail["events"]] == ["applied", "screening"]
    assert detail["events"][-1]["from_status"] == "applied"


def test_patch_review_and_reset_applied_date(api):
    created = api.create(applied_on="2026-01-05")
    reviewed = api.patch("/api/applications/%d" % created["id"], {"mark_reviewed": True})
    assert reviewed.status == 200
    assert reviewed.data["reviewed_at"]

    cleared = api.patch("/api/applications/%d" % created["id"], {"next_step_on": None})
    assert cleared.status == 200
    assert cleared.data["next_step_on"] is None


def test_soft_delete_restore_and_purge(api):
    created = api.create()
    app_id = created["id"]

    trashed = api.delete("/api/applications/%d" % app_id)
    assert trashed.status == 200
    assert api.get("/api/applications").data == []
    assert [app["id"] for app in api.get("/api/applications?trash=1").data] == [app_id]

    restored = api.post("/api/applications/%d/restore" % app_id)
    assert restored.status == 200
    assert restored.data["deleted_at"] is None
    assert api.get("/api/applications?trash=1").data == []

    # An active job cannot be purged before it is trashed.
    assert api.delete("/api/applications/%d?permanent=1" % app_id).status == 400

    api.delete("/api/applications/%d" % app_id)
    purged = api.delete("/api/applications/%d?permanent=1" % app_id)
    assert purged.status == 200
    assert purged.data["permanent"] is True
    assert api.get("/api/applications/%d" % app_id).status == 404


def test_sent_applications_outlive_the_job(api):
    """Deleting a job cannot un-send it: the goals read what was sent.

    Rejecting or deleting an application is tidying up a list; the application
    still went out that day, so the weekly count and the streak must not fall.
    """
    created = api.create(company="Acme", role="Engineer", applied_on="2026-10-28")
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-28": 1}}

    api.patch("/api/applications/%d" % created["id"], {"status": "rejected"})
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-28": 1}}

    api.delete("/api/applications/%d" % created["id"])
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-28": 1}}
    api.delete("/api/applications/%d?permanent=1" % created["id"])
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-28": 1}}


def test_sent_counts_only_what_was_sent(api):
    api.create(company="Saved", role="Engineer", phase="to_apply")
    assert api.get("/api/sent").data == {"total": 0, "days": {}}

    created = api.create(company="Acme", role="Engineer", applied_on="2026-10-28")
    # Correcting the date moves the send rather than adding a second one.
    api.patch("/api/applications/%d" % created["id"], {"applied_on": "2026-10-30"})
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-30": 1}}

    # Back on the saved list means it was never sent after all.
    api.patch("/api/applications/%d" % created["id"], {"phase": "to_apply"})
    assert api.get("/api/sent").data == {"total": 0, "days": {}}


def test_imported_sends_are_counted(api):
    response = api.post(
        "/api/import",
        {
            "applications": [
                {
                    "company": "Sent Co",
                    "role": "Engineer",
                    "status": "Applied",
                    "applied_on": "2026-10-27",
                },
                {"company": "Saved Co", "role": "Engineer"},
            ]
        },
    )
    assert response.status == 200
    assert api.get("/api/sent").data == {"total": 1, "days": {"2026-10-27": 1}}


def test_an_older_database_backfills_its_sends(api):
    """A database from before the ledger keeps the applications it already has."""
    created = api.create(company="Acme", role="Engineer", applied_on="2026-10-28")
    api.create(company="Saved", role="Engineer", phase="to_apply")
    db_path = api.tmp_path / "tracker.db"

    conn = server.connect(str(db_path))
    try:
        conn.execute("DROP TABLE sent_log")
        conn.commit()
    finally:
        conn.close()

    server.init_db(str(db_path))
    assert server.sent_counts(str(db_path), 1) == {"total": 1, "days": {"2026-10-28": 1}}
    # Reopening a database that already has the ledger does not seed it twice.
    server.init_db(str(db_path))
    assert server.sent_counts(str(db_path), 1) == {"total": 1, "days": {"2026-10-28": 1}}
    assert created["id"]


def test_purge_removes_documents_and_files(api):
    created = api.create()
    app_id = created["id"]
    assert api.upload(app_id, b"my resume").status == 201
    assert api.uploads_dir(app_id).is_dir()

    api.delete("/api/applications/%d" % app_id)
    api.delete("/api/applications/%d?permanent=1" % app_id)

    assert not api.uploads_dir(app_id).exists()
    assert api.get("/api/applications/%d" % app_id).status == 404


def test_empty_trash_endpoint(api):
    keep = api.create(company="Keep")
    one = api.create(company="One")
    two = api.create(company="Two")
    api.upload(one["id"], b"one")
    api.delete("/api/applications/%d" % one["id"])
    api.delete("/api/applications/%d" % two["id"])

    emptied = api.delete("/api/trash")
    assert emptied.status == 200
    assert emptied.data["deleted"] == 2
    assert emptied.data["files"] == 1
    assert api.get("/api/applications?trash=1").data == []
    assert [app["id"] for app in api.get("/api/applications").data] == [keep["id"]]
    assert not api.uploads_dir(one["id"]).exists()
    assert not api.uploads_dir(two["id"]).exists()

    # Idempotent when the bin is already empty.
    again = api.delete("/api/trash")
    assert again.data == {"deleted": 0, "files": 0}


def test_document_upload_listing_and_delete(api):
    created = api.create()
    app_id = created["id"]
    uploaded = api.upload(app_id, b"hello", filename="cover.txt", kind="cover_letter")
    assert uploaded.status == 201
    doc = uploaded.data
    assert doc["kind"] == "cover_letter"
    assert doc["preview"] == "text"
    assert doc["url"] == "/api/applications/%d/documents/%d" % (app_id, doc["id"])

    detail = api.get("/api/applications/%d" % app_id).data
    assert [item["filename"] for item in detail["documents"]] == ["cover.txt"]

    removed = api.delete("/api/applications/%d/documents/%d" % (app_id, doc["id"]))
    assert removed.status == 200
    assert api.get("/api/applications/%d" % app_id).data["documents"] == []
    assert not any(api.uploads_dir(app_id).iterdir())


def test_document_download_inline_and_range(api):
    created = api.create()
    app_id = created["id"]
    payload = b"%PDF-1.4 fake pdf body for range tests"
    doc = api.upload(app_id, payload, filename="resume.pdf", content_type="application/pdf").data

    download = api.get("/api/applications/%d/documents/%d" % (app_id, doc["id"]))
    assert download.status == 200
    assert download.headers["Content-Disposition"].startswith("attachment")
    assert download.body == payload

    preview = api.get("/api/applications/%d/documents/%d?inline=1" % (app_id, doc["id"]))
    assert preview.status == 200
    assert preview.headers["Content-Type"] == "application/pdf"
    assert preview.headers["Content-Disposition"].startswith("inline")

    partial = api.get(
        "/api/applications/%d/documents/%d?inline=1" % (app_id, doc["id"]),
        headers={"Range": "bytes=0-9"},
    )
    assert partial.status == 206
    assert partial.body == payload[:10]
    assert partial.headers["Content-Range"] == "bytes 0-9/%d" % len(payload)
    assert partial.headers["Accept-Ranges"] == "bytes"

    out_of_range = api.get(
        "/api/applications/%d/documents/%d" % (app_id, doc["id"]),
        headers={"Range": "bytes=99999-"},
    )
    assert out_of_range.status == 416


def test_html_upload_is_never_served_inline(api):
    created = api.create()
    app_id = created["id"]
    doc = api.upload(
        app_id,
        b"<script>alert(1)</script>",
        filename="evil.html",
        kind="other",
        content_type="text/html",
    ).data
    assert doc["preview"] is None

    response = api.get("/api/applications/%d/documents/%d?inline=1" % (app_id, doc["id"]))
    assert response.headers["Content-Disposition"].startswith("attachment")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_document_validation(api):
    created = api.create()
    app_id = created["id"]

    missing = api.post("/api/applications/%d/documents" % app_id, {"filename": "x.txt"})
    assert missing.status == 400

    bad_base64 = api.post(
        "/api/applications/%d/documents" % app_id,
        {"filename": "x.txt", "data": "not base64!!"},
    )
    assert bad_base64.status == 400

    empty = api.post(
        "/api/applications/%d/documents" % app_id,
        {"filename": "x.txt", "data": ""},
    )
    assert empty.status == 400

    bad_kind = api.post(
        "/api/applications/%d/documents" % app_id,
        {"filename": "x.txt", "kind": "passport", "data": base64.b64encode(b"x").decode()},
    )
    assert bad_kind.status == 400

    oversized = api.post(
        "/api/applications/%d/documents" % app_id,
        {
            "filename": "big.bin",
            "data": base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode(),
        },
    )
    assert oversized.status == 413


def test_settings_defaults_and_updates(api):
    defaults = api.get("/api/settings")
    assert defaults.status == 200
    assert defaults.data == {"daily_goal": 3, "weekly_goal": 10}

    updated = api.patch("/api/settings", {"daily_goal": 5})
    assert updated.status == 200
    assert updated.data == {"daily_goal": 5, "weekly_goal": 10}

    both = api.patch("/api/settings", {"daily_goal": 2, "weekly_goal": 4})
    assert both.data == {"daily_goal": 2, "weekly_goal": 4}
    assert api.get("/api/settings").data == {"daily_goal": 2, "weekly_goal": 4}


def test_settings_validation(api):
    assert api.patch("/api/settings", {"daily_goal": 0}).status == 400
    assert api.patch("/api/settings", {"daily_goal": 101}).status == 400
    assert api.patch("/api/settings", {"weekly_goal": "many"}).status == 400
    assert api.patch("/api/settings", {}).status == 400
    # Rejected values leave the stored goals untouched.
    assert api.get("/api/settings").data == {"daily_goal": 3, "weekly_goal": 10}


def test_import_json(api):
    response = api.post(
        "/api/import",
        {"applications": [{"company": "Imported", "role": "Engineer", "phase": "to_apply"}]},
    )
    assert response.status == 200
    assert response.data["imported"] == 1
    assert response.data["skipped"] == 0

    listed = api.get("/api/applications").data
    assert [app["company"] for app in listed] == ["Imported"]
    assert listed[0]["phase"] == "to_apply"


def test_import_without_a_status_saves_to_apply(api):
    """A file of postings is a to-apply list, not a list of sent applications."""
    response = api.post(
        "/api/import",
        {
            "applications": [
                {
                    "rank": 1,
                    "company": "Airbus Defence and Space GmbH",
                    "role": "Junior Simulation Software Developer C/C++ (m/f/d)",
                    "source": "Airbus careers (seen via aggregator listings)",
                    "location": "Manching, Bavaria, Germany",
                    "salary": "Not stated",
                    "description": "Develop C/C++ simulation models for the A400M programme.",
                    "employment_type": "Full-time, permanent, junior grade",
                    "application_deadline": "Not stated",
                    "job_url": "https://example.com/jobs/airbus-simulation",
                    "estimated_match_pct": 72,
                    "why_it_matches": "Junior C/C++ simulation role; thesis in C++17.",
                    "important_missing_requirements": "No avionics or real-time C experience.",
                }
            ]
        },
    )
    assert response.data == {"imported": 1, "skipped": 0, "problems": [], "duplicates": []}

    job = api.get("/api/applications").data[0]
    assert job["phase"] == "to_apply"
    assert job["status"] == "applied"
    assert job["applied_on"] == ""
    assert job["match_pct"] == 72
    assert job["notes"] == (
        "Why it matches:\nJunior C/C++ simulation role; thesis in C++17.\n\n"
        "Important missing requirements:\nNo avionics or real-time C experience."
    )
    # Everything else about the posting is kept as before.
    assert job["description"].startswith("Develop C/C++ simulation models")
    assert job["location"] == "Manching, Bavaria, Germany"


def test_import_shortens_long_fields_instead_of_losing_the_job(api):
    """An aggregator listing can carry a source past the app's limit; keep the job."""
    source = (
        "Airbus careers (seen via aggregator listings: speedyapply, freehire, "
        "Glassdoor; LinkedIn/Indeed also list Airbus Manching simulation roles)"
    )
    response = api.post(
        "/api/import",
        {
            "applications": [
                {
                    "company": "Airbus Defence and Space GmbH",
                    "role": "Junior Simulation Software Developer C/C++ (m/f/d)",
                    "source": source,
                    "description": "d" * 25000,
                }
            ]
        },
    )
    assert response.data == {"imported": 1, "skipped": 0, "problems": [], "duplicates": []}

    job = api.get("/api/applications").data[0]
    assert job["company"] == "Airbus Defence and Space GmbH"
    assert job["phase"] == "to_apply"
    assert len(source) > 100
    assert len(job["source"]) == 100 and job["source"].endswith("\u2026")
    assert len(job["description"]) == 20000


def test_import_says_why_a_row_was_skipped(api):
    response = api.post(
        "/api/import",
        {
            "applications": [
                {"company": "", "role": "Engineer"},
                {"company": "Kept Co", "role": "Engineer"},
            ]
        },
    )
    assert response.data["imported"] == 1
    assert response.data["skipped"] == 1
    assert response.data["problems"] == [
        {"row": 1, "company": "", "reason": "Company is required"}
    ]
    assert [app["company"] for app in api.get("/api/applications").data] == ["Kept Co"]


def test_adding_the_same_company_and_role_asks_first(api):
    first = api.create(company="Airbus Defence and Space GmbH", role="Simulation Developer")
    duplicate = api.post(
        "/api/applications",
        # Casing and spacing do not make it a different posting.
        {"company": "airbus  defence and space gmbh ", "role": " Simulation   Developer"},
    )
    assert duplicate.status == 409
    assert [match["id"] for match in duplicate.data["matches"]] == [first["id"]]
    assert duplicate.data["matches"][0]["company"] == "Airbus Defence and Space GmbH"
    assert len(api.get("/api/applications").data) == 1

    # A different role, or a different company, is a different job.
    api.create(company="Airbus Defence and Space GmbH", role="Systems Engineer")
    api.create(company="Marvel Fusion", role="Simulation Developer")
    assert api.post(
        "/api/applications",
        {"company": "Marvel Fusion", "role": "Simulation Developer"},
    ).status == 409
    assert len(api.get("/api/applications").data) == 3


    # A trashed job is not in the way: the posting may honestly come back.
    api.delete("/api/applications/%d" % first["id"])
    again = api.post(
        "/api/applications",
        {"company": "Airbus Defence and Space GmbH", "role": "Simulation Developer"},
    )
    assert again.status == 201

    # Add anyway, which is what the form sends when the user chooses it.
    forced = api.post(
        "/api/applications",
        {"company": "Airbus Defence and Space GmbH", "role": "Simulation Developer", "force": True},
    )
    assert forced.status == 201
    assert len(api.get("/api/applications").data) == 4


def test_import_skips_jobs_already_on_the_list(api):
    """Importing the same postings twice must not double every job."""
    first = api.create(company="Airbus", role="Simulation Developer")
    listing = {"company": "airbus", "role": "simulation developer"}

    skipped = api.post("/api/import", {"applications": [listing]})
    assert skipped.status == 200
    assert skipped.data["imported"] == 0
    assert skipped.data["skipped"] == 1
    assert skipped.data["duplicates"] == [
        {
            "row": 1,
            "company": "airbus",
            "role": "simulation developer",
            "existing_id": first["id"],
        }
    ]
    assert len(api.get("/api/applications").data) == 1

    # Import anyway writes the copy the dialog warned about.
    forced = api.post("/api/import", {"applications": [listing], "force": True})
    assert forced.data["imported"] == 1
    assert forced.data["duplicates"] == []
    assert len(api.get("/api/applications").data) == 2

    # A posting repeated within one file is a duplicate of the row before it.
    pair = api.post(
        "/api/import",
        {
            "applications": [
                {"company": "New Co", "role": "Engineer"},
                {"company": "New Co", "role": "Engineer"},
            ]
        },
    )
    assert pair.data["imported"] == 1
    assert pair.data["skipped"] == 1
    assert pair.data["duplicates"][0]["row"] == 2
    assert pair.data["duplicates"][0]["existing_id"]
    assert len(api.get("/api/applications").data) == 3


def test_import_keeps_rows_that_name_a_stage_applied(api):
    api.post(
        "/api/import",
        {
            "applications": [
                {"company": "Sent", "role": "Engineer", "status": "Rejected"},
                {"company": "Mixed case", "role": "Designer", "phase": "To apply"},
                {"company": "No estimate", "role": "Analyst", "match_pct": "not a number"},
                {
                    "company": "Own notes",
                    "role": "Tester",
                    "notes": "Referred by Priya",
                    "why_it_matches": ["C++", "Python"],
                },
            ]
        },
    )
    jobs = {app["company"]: app for app in api.get("/api/applications").data}

    # A named stage means it was sent, so it stays an application.
    assert jobs["Sent"]["phase"] == "applied"
    assert jobs["Sent"]["status"] == "rejected"
    assert jobs["Mixed case"]["phase"] == "to_apply"
    # An unreadable estimate is dropped rather than failing the row.
    assert jobs["No estimate"]["match_pct"] is None
    assert jobs["No estimate"]["phase"] == "to_apply"
    # Notes the file wrote are kept, with the analysis appended under a heading.
    assert jobs["Own notes"]["notes"] == "Referred by Priya\n\nWhy it matches:\n- C++\n- Python"


def test_match_pct_is_validated_and_can_be_cleared(api):
    created = api.post(
        "/api/applications", {"company": "Acme", "role": "Engineer", "match_pct": "72%"}
    )
    assert created.status == 201
    assert created.data["match_pct"] == 72
    path = "/api/applications/%d" % created.data["id"]

    # A job with no estimate reports none rather than zero.
    assert api.create(company="No estimate")["match_pct"] is None

    for bad in (120, -1, "most", True):
        assert api.patch(path, {"match_pct": bad}).status == 400
    assert api.patch(path, {"match_pct": 120}).data["error"]
    # The dashboard sends "" for an empty field, which clears the estimate.
    assert api.get(path).data["application"]["match_pct"] == 72
    assert api.patch(path, {"match_pct": ""}).data["match_pct"] is None
    assert api.patch(path, {"match_pct": 0}).data["match_pct"] == 0


def test_extract_requires_url(api):
    assert api.post("/api/extract", {}).status == 400
    assert api.post("/api/extract", {"url": "   "}).status == 400


def as_profile(profile_id):
    """A request header that scopes the call to one profile."""
    return {"X-Profile-Id": str(profile_id)}


def test_profiles_default_and_crud(api):
    listed = api.get("/api/profiles")
    assert listed.status == 200
    assert [profile["name"] for profile in listed.data] == ["Personal"]
    assert listed.data[0]["jobs"] == 0

    created = api.post("/api/profiles", {"name": "Freelance", "email": "me@example.com"})
    assert created.status == 201
    assert created.data["name"] == "Freelance"
    assert created.data["email"] == "me@example.com"
    assert created.data["headline"] == ""
    second = created.data

    assert api.post("/api/profiles", {"name": "   "}).status == 400
    assert api.post("/api/profiles", {}).status == 400

    updated = api.patch("/api/profiles/%d" % second["id"], {"headline": "Designer"})
    assert updated.status == 200
    assert updated.data["headline"] == "Designer"
    assert updated.data["name"] == "Freelance"
    assert api.patch("/api/profiles/9999", {"name": "Nope"}).status == 404
    assert api.get("/api/profiles/9999").status == 404

    assert [profile["name"] for profile in api.get("/api/profiles").data] == [
        "Personal",
        "Freelance",
    ]


def test_profile_data_is_isolated(api):
    first = api.get("/api/profiles").data[0]
    other = api.post("/api/profiles", {"name": "Other"}).data
    mine = as_profile(first["id"])
    theirs = as_profile(other["id"])

    job = api.create(company="Mine")
    assert api.get("/api/applications", headers=theirs).data == []
    assert api.get("/api/applications/%d" % job["id"], headers=theirs).status == 404
    assert api.delete("/api/applications/%d" % job["id"], headers=theirs).status == 404

    their_job = api.post(
        "/api/applications", {"company": "Theirs", "role": "Dev"}, headers=theirs
    )
    assert their_job.status == 201
    assert [app["company"] for app in api.get("/api/applications", headers=mine).data] == [
        "Mine"
    ]
    assert [app["company"] for app in api.get("/api/applications", headers=theirs).data] == [
        "Theirs"
    ]

    # Goals are per profile.
    assert api.get("/api/settings", headers=mine).data["daily_goal"] == 3
    api.patch("/api/settings", {"daily_goal": 7}, headers=theirs)
    assert api.get("/api/settings", headers=theirs).data["daily_goal"] == 7
    assert api.get("/api/settings", headers=mine).data["daily_goal"] == 3

    # Import lands in the selected profile only.
    api.post(
        "/api/import",
        {"applications": [{"company": "Imported", "role": "Dev"}]},
        headers=theirs,
    )
    assert "Imported" not in [app["company"] for app in api.get("/api/applications", headers=mine).data]

    # Documents can only be attached from the owning profile.
    assert api.upload(job["id"], b"secret", headers=theirs).status == 404
    document = api.upload(job["id"], b"secret", headers=mine)
    assert document.status == 201
    path = "/api/applications/%d/documents/%d" % (job["id"], document.data["id"])
    assert api.get(path, headers=mine).status == 200
    assert api.get(path, headers=theirs).status == 404

    # Trash is per profile too.
    assert api.delete("/api/applications/%d" % job["id"], headers=mine).status == 200
    assert len(api.get("/api/applications", headers=mine).data) == 0
    assert api.get("/api/applications?trash=1", headers=theirs).data == []
    emptied = api.delete("/api/trash", headers=theirs)
    assert emptied.data["deleted"] == 0
    assert len(api.get("/api/applications?trash=1", headers=mine).data) == 1
    assert api.delete("/api/trash", headers=mine).data["deleted"] == 1


def test_deleting_a_profile_removes_its_jobs_files_and_goals(api):
    other = api.post("/api/profiles", {"name": "Temp"}).data
    headers = as_profile(other["id"])
    job = api.post(
        "/api/applications", {"company": "Temp", "role": "Dev"}, headers=headers
    ).data
    assert api.upload(job["id"], b"hello", headers=headers).status == 201
    folder = api.uploads_dir(job["id"])
    assert folder.is_dir()
    api.patch("/api/settings", {"daily_goal": 9}, headers=headers)

    # A profile is deleted from inside itself, not from another profile.
    assert api.delete("/api/profiles/%d" % other["id"]).status == 403
    response = api.delete("/api/profiles/%d" % other["id"], headers=headers)
    assert response.status == 200
    assert response.data == {"deleted": other["id"], "jobs": 1, "files": 1}
    assert not folder.exists()
    assert [profile["name"] for profile in api.get("/api/profiles").data] == ["Personal"]
    assert api.get("/api/settings").data["daily_goal"] == 3


def test_last_profile_cannot_be_deleted(api):
    only = api.get("/api/profiles").data[0]
    response = api.delete("/api/profiles/%d" % only["id"])
    assert response.status == 400
    assert "at least one profile" in response.data["error"]


def test_unknown_or_invalid_profile_header(api):
    assert api.get("/api/applications", headers=as_profile(999)).status == 404
    assert api.get("/api/applications", headers=as_profile("abc")).status == 400
    # The profile list itself is never scoped by the header.
    assert api.get("/api/profiles", headers=as_profile(999)).status == 200


def test_profile_counts_jobs_in_the_list(api):
    job = api.create(company="Counted")
    api.delete("/api/applications/%d" % job["id"])
    listed = api.get("/api/profiles").data
    assert listed[0]["jobs"] == 0
    assert listed[0]["total_jobs"] == 1


def test_pin_lock_requires_a_valid_token(api):
    profile = api.get("/api/profiles").data[0]
    headers = as_profile(profile["id"])

    # A profile without a PIN opens normally.
    assert api.get("/api/applications", headers=headers).status == 200

    # Turning the lock on hides the data until the PIN is entered.
    set_pin = api.post("/api/profiles/%d/pin" % profile["id"], {"pin": "4321"})
    assert set_pin.status == 200
    assert set_pin.data["locked"] is True
    # Setting the PIN keeps the caller inside instead of locking them out at once.
    keep = dict(headers, **{"X-Profile-Token": set_pin.data["token"]})
    assert api.get("/api/applications", headers=keep).status == 200
    locked = api.get("/api/applications", headers=headers)
    assert locked.status == 423
    assert locked.data["error"] == "This profile is locked"
    assert api.get("/api/settings", headers=headers).status == 423

    # The profile list stays readable but never exposes the hash.
    listed = api.get("/api/profiles").data[0]
    assert listed["locked"] is True
    assert "pin_hash" not in listed and "pin_salt" not in listed

    # Wrong PINs are rejected without opening the profile.
    assert api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "0000"}).status == 401
    assert api.post("/api/profiles/%d/unlock" % profile["id"], {}).status == 401
    assert api.get("/api/applications", headers=headers).status == 423

    opened = api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "4321"})
    assert opened.status == 200
    assert opened.data["profile"]["locked"] is True
    token = opened.data["token"]
    assert token

    unlocked = dict(headers, **{"X-Profile-Token": token})
    assert api.get("/api/applications", headers=unlocked).status == 200
    assert api.get("/api/settings", headers=unlocked).status == 200
    # A bogus token is still refused.
    assert (
        api.get(
            "/api/applications", headers=dict(headers, **{"X-Profile-Token": "bogus"})
        ).status
        == 423
    )

    # Locking again invalidates the token.
    assert api.post("/api/profiles/%d/lock" % profile["id"], {}).status == 200
    assert api.get("/api/applications", headers=unlocked).status == 423


def test_pin_guesses_slow_down_after_a_few_misses(api, monkeypatch):
    # Four digits are ten thousand guesses, so the wait after a miss is the
    # lock: a handful are free, then each one costs longer than the last.
    monkeypatch.setattr(server.auth, "PIN_FAILURES", {})
    profile = api.get("/api/profiles").data[0]
    unlock = "/api/profiles/%d/unlock" % profile["id"]
    assert api.post("/api/profiles/%d/pin" % profile["id"], {"pin": "4321"}).status == 200

    for _ in range(server.PIN_FREE_ATTEMPTS):
        assert api.post(unlock, {"pin": "0000"}).status == 401

    # The miss past the free ones starts a wait, and knowing the PIN does not
    # skip it — otherwise the wait would only slow down guesses that fail.
    blocked = api.post(unlock, {"pin": "0000"})
    assert blocked.status == 429
    assert "try again in" in blocked.data["error"]
    assert blocked.data["retry_after"] >= server.auth.PIN_LOCK_BASE
    assert api.post(unlock, {"pin": "4321"}).status == 429

    # Once the wait is over the right PIN opens the profile again, and a
    # success forgets the misses instead of carrying them forward.
    server.auth.PIN_FAILURES[profile["id"]]["until"] = 0
    assert api.post(unlock, {"pin": "4321"}).status == 200
    assert server.auth.PIN_FAILURES.get(profile["id"]) is None


def test_the_dashboard_is_served_with_a_strict_policy(api):
    response = api.get("/")
    assert response.status == 200
    policy = response.headers.get("Content-Security-Policy")
    assert policy, "the dashboard page has no Content-Security-Policy"
    scripts = policy.split("script-src", 1)[1].split(";", 1)[0]
    # The inline boot scripts are allowed by hash, not by opening scripts up.
    assert "'self'" in scripts
    assert "sha256-" in scripts
    assert "unsafe-inline" not in scripts
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_pin_changes_and_removal_need_the_current_pin(api):
    profile = api.get("/api/profiles").data[0]
    path = "/api/profiles/%d/pin" % profile["id"]

    for bad in ("12", "123456789", "abcd", "12 34"):
        assert api.post(path, {"pin": bad}).status == 400

    assert api.post(path, {"pin": "1234"}).status == 200
    # Once locked, even PIN edits need an unlock token first.
    assert api.post(path, {"pin": "5678", "current_pin": "1234"}).status == 423

    token = api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "1234"}).data["token"]
    unlocked = dict(as_profile(profile["id"]), **{"X-Profile-Token": token})
    assert api.get("/api/applications", headers=unlocked).status == 200
    assert api.post(path, {"pin": "5678", "current_pin": "0000"}, headers=unlocked).status == 401
    assert api.post(path, {"pin": "5678"}, headers=unlocked).status == 401
    changed = api.post(path, {"pin": "5678", "current_pin": "1234"}, headers=unlocked)
    assert changed.status == 200
    assert changed.data["token"]

    # The new PIN is the one that opens the profile now.
    assert api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "1234"}).status == 401
    fresh = api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "5678"}).data["token"]
    unlocked = dict(as_profile(profile["id"]), **{"X-Profile-Token": fresh})
    assert api.post(path, {"remove": True, "current_pin": "1234"}, headers=unlocked).status == 401
    assert api.post(path, {"remove": True, "current_pin": "5678"}, headers=unlocked).status == 200
    assert api.get("/api/applications", headers=as_profile(profile["id"])).status == 200
    assert api.get("/api/profiles").data[0]["locked"] is False


def test_locked_profile_metadata_is_still_selectable(api):
    profile = api.get("/api/profiles").data[0]
    api.post("/api/profiles/%d/pin" % profile["id"], {"pin": "1111"})
    listed = api.get("/api/profiles").data[0]
    assert listed["name"] == "Personal"
    assert listed["locked"] is True
    assert listed["pin_set_at"]
    assert api.get("/api/profiles/%d" % profile["id"]).status == 200
    # Editing someone else's locked profile needs their PIN, and the profile can
    # only be deleted from inside itself.
    assert api.patch("/api/profiles/%d" % profile["id"], {"name": "Hacked"}).status == 423
    assert api.delete("/api/profiles/%d" % profile["id"]).status == 423
    other = api.post("/api/profiles", {"name": "Other"}).data
    assert api.delete("/api/profiles/%d" % profile["id"], headers=as_profile(other["id"])).status == 403
    assert api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "1111"}).status == 200


def test_writes_must_come_from_the_dashboard(api):
    created = api.create(company="Acme", role="Engineer")
    # A page on another site cannot create, change or delete anything.
    foreign = api.post("/api/applications", {"company": "Evil", "role": "X"}, origin="https://evil.example")
    assert foreign.status == 403
    assert foreign.data["error"] == "Requests must come from the dashboard itself"
    assert api.delete("/api/applications/%d" % created["id"], origin="https://evil.example").status == 403
    assert api.patch("/api/settings", {"daily_goal": 9}, origin="http://127.0.0.1:1").status == 403
    # Reads from the dashboard itself still work.
    assert api.get("/api/applications").status == 200


def test_mutations_need_the_session_token(api):
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng"}, token="").status == 403
    assert api.post("/api/applications", {"company": "Acme", "role": "Eng"}, token="wrong").status == 403
    assert api.get("/api/applications", token="wrong").status == 403
    assert api.get("/api/settings", token="").status == 403
    # The token is readable from the dashboard's own origin and works.
    assert api.token
    assert api.get("/api/applications").status == 200


def test_health_stays_reachable_for_the_start_screen(api):
    response = api.get("/api/health", token="", headers={"Host": "evil.example"})
    assert response.status == 200


def test_foreign_host_is_refused(api):
    response = api.get("/api/applications", headers={"Host": "evil.example"})
    assert response.status == 403
    assert "only answers on this machine" in response.data["error"]


def test_document_downloads_need_no_token(api):
    created = api.create()
    uploaded = api.upload(created["id"], b"hello resume")
    assert uploaded.status == 201
    response = api.get(uploaded.data["url"], token="")
    assert response.status == 200
    assert response.body == b"hello resume"


def test_a_locked_profile_hides_its_documents_from_bare_urls(api):
    """A download link cannot carry the unlock token, so the dashboard fetches
    document bytes with a signed request instead of opening a bare URL."""
    profile = api.get("/api/profiles").data[0]
    created = api.create(company="Locked", role="Dev")
    uploaded = api.upload(created["id"], b"secret cover letter", filename="cover.txt")
    assert uploaded.status == 201
    url = uploaded.data["url"]
    # No PIN yet: the browser can open the file on its own.
    assert api.get(url, token="").status == 200

    set_pin = api.post("/api/profiles/%d/pin" % profile["id"], {"pin": "4321"})
    assert set_pin.status == 200
    bare = api.get(url, token="")
    assert bare.status == 423
    assert bare.data["error"] == "This profile is locked"

    token = api.post("/api/profiles/%d/unlock" % profile["id"], {"pin": "4321"}).data["token"]
    signed = api.get(url, token="", headers={"X-Profile-Token": token})
    assert signed.status == 200
    assert signed.body == b"secret cover letter"


def test_backup_archive_holds_the_database_and_documents(api):
    import io
    import zipfile

    created = api.create(company="Backed", role="Up")
    api.upload(created["id"], b"resume bytes")
    response = api.get("/api/backup")
    assert response.status == 200
    assert response.headers["Content-Type"] == "application/zip"

    archive = zipfile.ZipFile(io.BytesIO(response.body))
    names = archive.namelist()
    assert "tracker.db" in names
    assert "README.txt" in names
    stored = [name for name in names if name.startswith("uploads/%d/" % created["id"])]
    assert len(stored) == 1 and stored[0].endswith(".txt")


def test_daily_backup_runs_once_and_prunes(tmp_path):
    import sqlite3

    import server

    db_path = tmp_path / "tracker.db"
    server.init_db(str(db_path))
    server.create_application(
        str(db_path), 1, server.clean_create({"company": "Backed", "role": "Up"})
    )
    first = server.backup_database(str(db_path))
    second = server.backup_database(str(db_path))
    assert first == second
    assert len(list((tmp_path / "backups").glob("tracker-*.db"))) == 1

    copy = sqlite3.connect(first)
    try:
        rows = copy.execute("SELECT company FROM applications").fetchall()
    finally:
        copy.close()
    assert rows == [("Backed",)]

    folder = tmp_path / "backups"
    for day in range(1, 20):
        (folder / ("tracker-2020-01-%02d.db" % day)).write_bytes(b"")
    server.prune_backups(str(folder), keep=5)
    assert len(list(folder.glob("tracker-*.db"))) == 5
    assert (folder / "tracker-2020-01-19.db").exists()


def test_installable_app_assets_are_served(api):
    manifest = api.get("/manifest.webmanifest")
    assert manifest.status == 200
    assert manifest.headers["Content-Type"] == "application/manifest+json"
    assert manifest.data["start_url"] == "/"
    assert manifest.data["display"] == "standalone"
    sizes = {
        icon["sizes"]
        for icon in manifest.data["icons"]
        if icon.get("type") == "image/png" and icon.get("purpose", "any") == "any"
    }
    assert {"192x192", "512x512"} <= sizes

    worker = api.get("/sw.js")
    assert worker.status == 200
    assert worker.headers["Content-Type"].startswith("text/javascript")
    assert b"fetch" in worker.body and b"/api/" in worker.body

    for path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"):
        icon = api.get(path)
        assert icon.status == 200, path
        assert icon.headers["Content-Type"] == "image/png", path
        assert icon.body[:8] == b"\x89PNG\r\n\x1a\n", path

    # The light theme's backdrop is a photograph. It has to go out as an image,
    # and it has to be in the shell's cache list, or the theme loses its
    # background the moment the dashboard is opened offline.
    photo = api.get("/img/car.jpg")
    assert photo.status == 200
    assert photo.headers["Content-Type"] == "image/jpeg"
    assert photo.body[:3] == b"\xff\xd8\xff", "not a JPEG"
    assert b"/img/car.jpg" in worker.body


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_resume_editor_starts_from_the_profile(api):
    created = api.post("/api/resumes", {"name": "master_software"})
    assert created.status == 201
    resume = created.data
    assert resume["name"] == "master_software.tex"
    assert resume["application_id"] is None
    assert resume["files"] == []
    assert "\\begin{document}" in resume["source"]
    assert "Technical Skills" in resume["source"]

    listed = api.get("/api/resumes").data
    assert [(item["name"], item["file_count"]) for item in listed] == [("master_software.tex", 0)]
    # The list stays small: the source only travels with the one resume opened.
    assert "source" not in listed[0]

    detail = api.get("/api/resumes/%d" % resume["id"])
    assert detail.status == 200
    assert detail.data["source"] == resume["source"]

    saved = api.patch("/api/resumes/%d" % resume["id"], {"source": "\\section{Hi}"})
    assert saved.data["source"] == "\\section{Hi}"
    assert api.patch("/api/resumes/%d" % resume["id"], {}).status == 400
    assert api.patch("/api/resumes/%d" % resume["id"], {"source": "x" * 200001}).status == 413

    # A name is a file name: no folders, and always a .tex.
    renamed = api.patch("/api/resumes/%d" % resume["id"], {"name": "../../etc/passwd"})
    assert renamed.data["name"] == "passwd.tex"

    assert api.delete("/api/resumes/%d" % resume["id"]).status == 200
    assert api.get("/api/resumes/%d" % resume["id"]).status == 404


def test_resume_support_files_are_stored_and_served(api):
    resume = api.post("/api/resumes", {"name": "master"}).data
    path = "/api/resumes/%d/files" % resume["id"]
    uploaded = api.post(
        path,
        {
            "filename": "profile.png",
            "content_type": "image/png",
            "data": base64.b64encode(PNG).decode("ascii"),
        },
    )
    assert uploaded.status == 201
    assert uploaded.data["filename"] == "profile.png"
    assert uploaded.data["url"] == "/api/resumes/%d/files/%d" % (resume["id"], uploaded.data["id"])
    # The rail previews it like a job document, without guessing the type.
    assert uploaded.data["preview"] == "image"

    assert [file["filename"] for file in api.get("/api/resumes/%d" % resume["id"]).data["files"]] == [
        "profile.png"
    ]
    # A browser loads the image without a session header, so it downloads bare.
    downloaded = api.get(uploaded.data["url"], token="")
    assert downloaded.status == 200
    assert downloaded.headers["Content-Type"] == "image/png"
    assert downloaded.body == PNG
    on_disk = api.uploads_dir("resume-%d" % resume["id"])
    assert on_disk.is_dir()

    assert api.post(path, {"filename": "", "data": "aGk="}).status == 400
    assert api.post(path, {"filename": "empty.txt"}).status == 400
    assert api.delete(uploaded.data["url"]).status == 200
    assert api.get("/api/resumes/%d" % resume["id"]).data["files"] == []
    assert api.delete(uploaded.data["url"]).status == 404


def test_tailored_resume_copies_the_master_and_its_files(api):
    job = api.create(company="Airbus", role="Simulation Developer")
    master = api.post("/api/resumes", {"name": "master"}).data
    api.post(
        "/api/resumes/%d/files" % master["id"],
        {
            "filename": "profile.png",
            "content_type": "image/png",
            "data": base64.b64encode(PNG).decode("ascii"),
        },
    )

    tailored = api.post("/api/resumes", {"application_id": job["id"]})
    assert tailored.status == 201
    assert tailored.data["application_id"] == job["id"]
    assert tailored.data["source"] == master["source"]
    assert [file["filename"] for file in tailored.data["files"]] == ["profile.png"]
    assert tailored.data["label"] == "Airbus — Simulation Developer"

    # The copy owns its files: removing one leaves the master's untouched.
    copied = tailored.data["files"][0]
    assert api.delete(
        "/api/resumes/%d/files/%d" % (tailored.data["id"], copied["id"])
    ).status == 200
    assert len(api.get("/api/resumes/%d" % master["id"]).data["files"]) == 1

    jobs = {item["application_id"]: item for item in api.get("/api/resumes").data}
    assert jobs[job["id"]]["job_company"] == "Airbus"
    assert jobs[job["id"]]["job_role"] == "Simulation Developer"

    # The job's permanent delete takes its tailored resume, and its files.
    api.delete("/api/applications/%d" % job["id"])
    api.delete("/api/applications/%d?permanent=1" % job["id"])
    assert [item["name"] for item in api.get("/api/resumes").data] == ["master.tex"]
    assert not api.uploads_dir("resume-%d" % tailored.data["id"]).exists()


def test_resumes_are_kept_per_profile(api):
    mine = api.post("/api/resumes", {"name": "mine"}).data
    job = api.create(company="Mine", role="Engineer")
    api.post("/api/profiles", {"name": "Theirs"})
    mine_headers = as_profile(1)
    theirs_headers = as_profile(2)

    assert api.get("/api/resumes", headers=theirs_headers).data == []
    assert api.get("/api/resumes/%d" % mine["id"], headers=theirs_headers).status == 404
    # A resume cannot be attached to another profile's job.
    response = api.post(
        "/api/resumes",
        {"application_id": job["id"]},
        headers=theirs_headers,
    )
    assert response.status == 404

    # Deleting the profile takes its resumes and their files with it.
    api.post(
        "/api/resumes/%d/files" % mine["id"],
        {"filename": "a.txt", "data": base64.b64encode(b"hi").decode("ascii")},
        headers=mine_headers,
    )
    folder = api.uploads_dir("resume-%d" % mine["id"])
    assert folder.is_dir()
    assert api.delete("/api/profiles/1", headers=mine_headers).status == 200
    assert not folder.exists()


# Ask the server where it would look: it also accepts the copy that
# install_tectonic.py leaves in tools/, which is not on PATH.
TECTONIC = server.latex_engine()
needs_tex = pytest.mark.skipif(TECTONIC is None, reason="tectonic is not installed")

SIMPLE_TEX = r"""\documentclass{article}
\begin{document}
{\LARGE Test Person}\\
\section{Experience}
\begin{itemize}
  \item Built things.
\end{itemize}
\end{document}
"""


@needs_tex
def test_compiling_a_resume_returns_a_pdf(api):
    resume = api.post("/api/resumes", {"name": "master", "source": SIMPLE_TEX}).data
    response = api.post("/api/resumes/%d/compile" % resume["id"], {"source": SIMPLE_TEX})
    assert response.status == 200, response.data
    pdf = base64.b64decode(response.data["pdf"])
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
    assert response.data["engine"].startswith("tectonic")
    assert response.data["elapsed_ms"] >= 0

    # No source in the body means "compile what is saved".
    saved = api.post("/api/resumes/%d/compile" % resume["id"], {})
    assert saved.status == 200
    assert base64.b64decode(saved.data["pdf"]).startswith(b"%PDF")


@needs_tex
def test_a_failed_compile_reports_the_tex_log(api):
    resume = api.post("/api/resumes", {"name": "master"}).data
    broken = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\nosuchcommand{x}\n"
        "\\end{document}\n"
    )
    response = api.post("/api/resumes/%d/compile" % resume["id"], {"source": broken})
    assert response.status == 422
    assert response.data["error"] == "LaTeX could not compile this resume"
    assert "Undefined control sequence" in response.data["log"]
    assert "l.3" in response.data["log"]


@needs_tex
def test_compiling_finds_an_uploaded_support_file(api):
    resume = api.post("/api/resumes", {"name": "master"}).data
    uploaded = api.post(
        "/api/resumes/%d/files" % resume["id"],
        {
            "filename": "profile.png",
            "content_type": "image/png",
            "data": base64.b64encode(PNG).decode("ascii"),
        },
    )
    assert uploaded.status == 201
    source = (
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        "Photo: \\includegraphics[width=2cm]{profile.png}\n"
        "\\end{document}\n"
    )
    response = api.post("/api/resumes/%d/compile" % resume["id"], {"source": source})
    assert response.status == 200, response.data
    assert base64.b64decode(response.data["pdf"]).startswith(b"%PDF")


def test_engine_status_says_what_compile_would_use(api, monkeypatch):
    monkeypatch.setattr(server.resumes, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(server.resumes, "latex_engine", lambda: None)
    response = api.get("/api/engine")
    assert response.status == 200
    assert response.data == {
        "available": False,
        "version": "",
        "path": "",
        "state": "idle",
        "message": "",
    }

    monkeypatch.setattr(server.resumes, "latex_engine", lambda: "/usr/local/bin/tectonic")
    monkeypatch.setattr(server.resumes, "engine_version", lambda binary: "Tectonic 0.17.0")
    status = api.get("/api/engine").data
    assert status["available"] is True
    assert status["version"] == "Tectonic 0.17.0"
    assert status["path"] == "/usr/local/bin/tectonic"
    assert status["state"] == "ready"

    assert api.get("/api/engine/install").status == 405
    assert api.post("/api/engine", {}).status == 405
    assert api.get("/api/engine/nonsense").status == 404


def wait_for_install(api, seconds=5):
    status = api.get("/api/engine").data
    deadline = time.time() + seconds
    while status["state"] == "installing" and time.time() < deadline:
        time.sleep(0.02)
        status = api.get("/api/engine").data
    return status


def test_installing_the_engine_downloads_in_the_background(api, monkeypatch, tmp_path):
    tools = tmp_path / "tools" / "tectonic"
    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tools))
    monkeypatch.setattr(server.resumes.shutil, "which", lambda name: None)
    monkeypatch.setattr(server.resumes, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(server.resumes, "engine_version", lambda binary: "Tectonic 9.9.9")
    calls = []

    def fake_install(**kwargs):
        calls.append(kwargs)
        tools.mkdir(parents=True, exist_ok=True)
        binary = tools / "tectonic"
        binary.write_bytes(b"#!/bin/sh\n")
        return str(binary)

    monkeypatch.setattr(server.install_tectonic, "install_engine", fake_install)
    assert api.get("/api/engine").data["available"] is False

    started = api.post("/api/engine/install", {})
    assert started.status == 200
    assert started.data["state"] in ("installing", "ready")

    status = wait_for_install(api)
    assert status["state"] == "ready", status
    assert status["available"] is True
    assert status["version"] == "Tectonic 9.9.9"
    assert status["path"] == str(tools / "tectonic")
    assert "Tectonic installed at" in status["message"]
    # It must land in the folder Compile searches, not the CLI's own default.
    assert calls == [{"dest_dir": str(tools), "quiet": True}]

    # An engine that is already there is never downloaded a second time.
    again = api.post("/api/engine/install", {})
    assert again.data["available"] is True
    assert calls == [{"dest_dir": str(tools), "quiet": True}]


def test_an_install_that_lands_elsewhere_is_not_called_ready(api, monkeypatch, tmp_path):
    """A successful download the tracker cannot see is a failure, not a success."""
    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(server.resumes.shutil, "which", lambda name: None)
    monkeypatch.setattr(server.resumes, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(server.resumes, "engine_version", lambda binary: "Tectonic 9.9.9")
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    binary = elsewhere / "tectonic"
    binary.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(server.install_tectonic, "install_engine", lambda **kwargs: str(binary))

    api.post("/api/engine/install", {})
    status = wait_for_install(api)
    assert status["state"] == "failed"
    assert status["available"] is False
    assert "cannot find it" in status["message"]


def test_a_failed_install_is_reported_rather_than_hidden(api, monkeypatch, tmp_path):
    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(server.resumes.shutil, "which", lambda name: None)
    monkeypatch.setattr(server.resumes, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(
        server.install_tectonic,
        "install_engine",
        lambda **kwargs: (_ for _ in ()).throw(OSError("temporary failure in name resolution")),
    )
    assert api.post("/api/engine/install", {}).status == 200
    status = wait_for_install(api)
    assert status["state"] == "failed"
    assert "name resolution" in status["message"]
    assert status["available"] is False


def test_a_downloaded_engine_that_will_not_run_counts_as_a_failure(api, monkeypatch, tmp_path):
    binary = tmp_path / "tectonic"
    binary.write_bytes(b"not really an engine")
    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(server.resumes.shutil, "which", lambda name: None)
    monkeypatch.setattr(server.resumes, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(server.resumes, "engine_version", lambda engine: "")
    monkeypatch.setattr(server.install_tectonic, "install_engine", lambda **kwargs: str(binary))
    api.post("/api/engine/install", {})
    status = wait_for_install(api)
    assert status["state"] == "failed"
    assert "would not run" in status["message"]


@needs_tex
def test_engine_status_describes_the_engine_that_is_installed(api):
    status = api.get("/api/engine").data
    assert status["available"] is True
    assert status["path"] == TECTONIC
    assert status["state"] == "ready"
    assert status["version"]


def test_compile_says_when_no_latex_engine_is_installed(api, monkeypatch, tmp_path):
    resume = api.post("/api/resumes", {"name": "master"}).data
    # No engine on PATH, and none in the folder the installer fills.
    monkeypatch.setattr(server.resumes.shutil, "which", lambda name: None)
    monkeypatch.setattr(server.resumes, "TECTONIC_DIR", str(tmp_path / "tools" / "tectonic"))
    response = api.post("/api/resumes/%d/compile" % resume["id"], {})
    assert response.status == 501
    assert "No LaTeX engine" in response.data["error"]


def save_pdf(api, resume, filename="master_software.pdf", raw=b"%PDF-1.4 saved"):
    """What the Resume tab's Save PDF button sends."""
    response = api.post(
        "/api/resumes/%d/files" % resume["id"],
        {
            "filename": filename,
            "content_type": "application/pdf",
            "data": base64.b64encode(raw).decode("ascii"),
        },
    )
    assert response.status == 201, response.data
    return response.data


def test_a_saved_pdf_shows_up_in_the_file_library(api):
    resume = api.post("/api/resumes", {"name": "master_software"}).data
    saved = save_pdf(api, resume)
    assert saved["filename"] == "master_software.pdf"
    assert saved["content_type"] == "application/pdf"

    library = api.get("/api/files")
    assert library.status == 200
    assert [file["filename"] for file in library.data] == ["master_software.pdf"]
    entry = library.data[0]
    assert entry["id"] == saved["id"]
    assert entry["resume_id"] == resume["id"]
    assert entry["resume_name"] == "master_software.tex"
    assert entry["size"] == len(b"%PDF-1.4 saved")
    assert entry["job_company"] == ""
    assert api.get(entry["url"], token="").body == b"%PDF-1.4 saved"

    # A file saved with a job's tailored resume says which job it was for.
    job = api.create(company="Airbus", role="Simulation Developer")
    tailored = api.post(
        "/api/resumes", {"name": "airbus", "application_id": job["id"]}
    ).data
    save_pdf(api, tailored, filename="airbus-tailored.pdf", raw=b"%PDF-1.4 airbus")
    library = api.get("/api/files").data
    assert [(file["filename"], file["resume_name"]) for file in library] == [
        ("airbus-tailored.pdf", "airbus.tex"),
        # A tailored copy starts with the master's support files, so the master's
        # saved PDF comes along until it is removed.
        ("master_software.pdf", "airbus.tex"),
        ("master_software.pdf", "master_software.tex"),
    ]
    assert library[0]["job_company"] == "Airbus"
    assert library[0]["job_role"] == "Simulation Developer"

    # Files are per profile, like everything else.
    other = api.post("/api/profiles", {"name": "Second"}).data
    assert api.get("/api/files", headers={"X-Profile-Id": str(other["id"])}).data == []


def test_a_saved_file_can_be_attached_to_a_job(api):
    resume = api.post("/api/resumes", {"name": "master_software"}).data
    saved = save_pdf(api, resume)
    job = api.create(company="Airbus", role="Junior Simulation Developer")
    attach = "/api/files/%d/attach" % saved["id"]

    assert api.get(attach).status == 405
    assert api.post(attach, {}).status == 400
    assert api.post(attach, {"application_id": "not-a-job"}).status == 400
    assert api.post(attach, {"application_id": 424242}).status == 404
    assert api.post(attach, {"application_id": job["id"], "kind": "passport"}).status == 400

    attached = api.post(attach, {"application_id": job["id"], "kind": "resume"})
    assert attached.status == 200, attached.data
    document = attached.data["document"]
    assert document["filename"] == "master_software.pdf"
    assert document["kind"] == "resume"
    assert document["content_type"] == "application/pdf"
    assert document["application_id"] == job["id"]
    # The job's copy is its own file: deleting the job cannot lose the saved one.
    assert api.get(document["url"], token="").body == b"%PDF-1.4 saved"
    detail = api.get("/api/applications/%d" % job["id"]).data
    assert [doc["filename"] for doc in detail["documents"]] == ["master_software.pdf"]
    assert [file["filename"] for file in api.get("/api/files").data] == ["master_software.pdf"]

    # Without a kind it lands under Other.
    again = api.post(attach, {"application_id": job["id"]})
    assert again.status == 200
    assert again.data["document"]["kind"] == "other"

    # Someone else's file, and someone else's job, are both out of reach.
    other = api.post("/api/profiles", {"name": "Second"}).data
    stranger = api.create(company="Elsewhere", role="Engineer")
    denied_file = api.post(
        attach,
        {"application_id": job["id"]},
        headers={"X-Profile-Id": str(other["id"])},
    )
    assert denied_file.status == 404
    denied_job = api.post(
        "/api/files/%d/attach" % saved["id"],
        {"application_id": stranger["id"]},
        headers={"X-Profile-Id": str(other["id"])},
    )
    assert denied_job.status == 404

    assert api.post("/api/files/424242/attach", {"application_id": job["id"]}).status == 404


def test_a_direct_upload_can_replace_the_previous_copy(api):
    """The Documents section's own upload offers the same replace as attaching."""
    job = api.create(company="Airbus", role="Simulation Developer")
    first = api.upload(
        job["id"], b"%PDF-1.4 one", filename="cv-v1.pdf", content_type="application/pdf"
    )
    assert first.status == 201, first.data
    assert api.upload(job["id"], b"Dear Airbus", filename="cover.txt", kind="cover_letter").status == 201

    # Without replace, uploading again keeps both copies.
    again = api.upload(
        job["id"], b"%PDF-1.4 two", filename="cv-v2.pdf", content_type="application/pdf"
    )
    assert again.status == 201
    resumes = [
        doc
        for doc in api.get("/api/applications/%d" % job["id"]).data["documents"]
        if doc["kind"] == "resume"
    ]
    assert sorted(doc["filename"] for doc in resumes) == ["cv-v1.pdf", "cv-v2.pdf"]

    replaced = api.upload(
        job["id"],
        b"%PDF-1.4 three",
        filename="cv-v3.pdf",
        content_type="application/pdf",
        replace=True,
    )
    assert replaced.status == 201, replaced.data
    documents = api.get("/api/applications/%d" % job["id"]).data["documents"]
    assert sorted(doc["kind"] for doc in documents) == ["cover_letter", "resume"]
    resume = next(doc for doc in documents if doc["kind"] == "resume")
    assert resume["filename"] == "cv-v3.pdf"
    # The replaced files are gone from disk, not just from the list.
    for doc in resumes:
        missing = api.get(
            "/api/applications/%d/documents/%d" % (job["id"], doc["id"]), token=""
        )
        assert missing.status == 404


def test_attaching_can_replace_the_previous_copy(api):
    """A new version of the same document replaces the old one when asked."""
    resume = api.post("/api/resumes", {"name": "master_software"}).data
    first_file = save_pdf(api, resume, filename="master-v1.pdf", raw=b"%PDF-1.4 v1")
    second_file = save_pdf(api, resume, filename="master-v2.pdf", raw=b"%PDF-1.4 v2")
    job = api.create(company="Airbus", role="Simulation Developer")
    other_job = api.create(company="Marvel Fusion", role="Engineer")

    attach = lambda file_id, **fields: api.post(  # noqa: E731
        "/api/files/%d/attach" % file_id,
        dict({"application_id": job["id"], "kind": "resume"}, **fields),
    )

    assert attach(first_file["id"]).data["replaced"] == []
    # Adding stays the default: a second copy is kept beside the first.
    added = attach(second_file["id"])
    assert added.data["replaced"] == []
    assert len(api.get("/api/applications/%d" % job["id"]).data["documents"]) == 2

    previous = [doc["id"] for doc in api.get("/api/applications/%d" % job["id"]).data["documents"]]
    replaced = attach(second_file["id"], replace=True)
    assert replaced.status == 200, replaced.data
    assert sorted(replaced.data["replaced"]) == sorted(previous)
    assert replaced.data["document"]["filename"] == "master-v2.pdf"

    documents = api.get("/api/applications/%d" % job["id"]).data["documents"]
    assert [doc["filename"] for doc in documents] == ["master-v2.pdf"]
    # The replaced copies are gone from disk too, not just from the list.
    for doc_id in previous:
        missing = api.get(
            "/api/applications/%d/documents/%d" % (job["id"], doc_id), token=""
        )
        assert missing.status == 404

    # Only that kind on that job: a cover letter and another job are untouched.
    cover = api.upload(job["id"], b"Dear Airbus", kind="cover_letter")
    elsewhere = api.upload(
        other_job["id"], b"%PDF-1.4 elsewhere", kind="resume", filename="other.pdf"
    )
    assert cover.status == 201 and elsewhere.status == 201
    again = attach(first_file["id"], replace=True)
    assert len(again.data["replaced"]) == 1
    kinds = sorted(
        doc["kind"] for doc in api.get("/api/applications/%d" % job["id"]).data["documents"]
    )
    assert kinds == ["cover_letter", "resume"]
    assert [
        doc["filename"]
        for doc in api.get("/api/applications/%d" % other_job["id"]).data["documents"]
    ] == ["other.pdf"]

    # Nothing to replace is not an error; an unknown kind still is.
    empty = attach(
        second_file["id"],
        replace=True,
        application_id=other_job["id"],
        kind="cover_letter",
    )
    assert empty.data["replaced"] == []
    assert attach(first_file["id"], replace=True, kind="passport").status == 400


def test_documents_by_job_lists_what_every_job_has(api):
    """The Save PDF dialog names what a job already holds before attaching."""
    assert api.get("/api/documents").data == {}

    airbus = api.create(company="Airbus", role="Simulation Developer")
    marvel = api.create(company="Marvel Fusion", role="Research Software Engineer")
    api.upload(
        airbus["id"],
        b"%PDF-1.4 cv",
        filename="airbus-cv.pdf",
        kind="resume",
        content_type="application/pdf",
    )
    api.upload(
        airbus["id"], b"Dear Airbus", filename="airbus-cover.txt", kind="cover_letter"
    )

    grouped = api.get("/api/documents").data
    # Only jobs that hold something are listed, and only the fields the dialog
    # needs: no stored names, sizes or URLs.
    assert list(grouped) == [str(airbus["id"])]
    assert [(entry["kind"], entry["filename"]) for entry in grouped[str(airbus["id"])]] == [
        ("resume", "airbus-cv.pdf"),
        ("cover_letter", "airbus-cover.txt"),
    ]
    assert all(set(entry) == {"id", "kind", "filename"} for entry in grouped[str(airbus["id"])])
    assert str(marvel["id"]) not in grouped

    # Per profile, like every other list, and read-only.
    other = api.post("/api/profiles", {"name": "Second"}).data
    assert api.get("/api/documents", headers={"X-Profile-Id": str(other["id"])}).data == {}
    assert api.post("/api/documents", {}).status == 405
    assert api.get("/api/documents/1").status == 404


@needs_tex
def test_compiling_does_not_save_anything(api):
    """Compile is a preview; saving is a separate, named action."""
    resume = api.post("/api/resumes", {"name": "master_software"}).data
    saved = save_pdf(api, resume, filename="already-saved.pdf", raw=b"%PDF-1.4 mine")

    response = api.post("/api/resumes/%d/compile" % resume["id"], {"source": SIMPLE_TEX})
    assert response.status == 200, response.data
    assert "saved" not in response.data
    assert base64.b64decode(response.data["pdf"]).startswith(b"%PDF")

    listed = api.get("/api/resumes/%d" % resume["id"]).data["files"]
    assert [file["filename"] for file in listed] == ["already-saved.pdf"]
    assert listed[0]["id"] == saved["id"]
    assert api.get(listed[0]["url"], token="").body == b"%PDF-1.4 mine"


@needs_tex
def test_another_profile_cannot_compile_your_resume(api):
    resume = api.post("/api/resumes", {"name": "master"}).data
    other = api.post("/api/profiles", {"name": "Second"}).data
    response = api.post(
        "/api/resumes/%d/compile" % resume["id"],
        {},
        headers={"X-Profile-Id": str(other["id"])},
    )
    assert response.status == 404


def test_backups_can_be_listed_and_restored(api, tmp_path):
    db_path = str(tmp_path / "tracker.db")
    api.create(company="Before the snapshot")
    server.backup_database(db_path)
    api.create(company="After the snapshot")

    listed = api.get("/api/backups")
    assert listed.status == 200
    names = [item["name"] for item in listed.data["backups"]]
    assert names, "the snapshot jobby took is not listed"

    restored = api.post("/api/backups/%s/restore" % names[0], {})
    assert restored.status == 200
    assert restored.data["restored"] == names[0]
    # The database is back to the morning, not merged with what came after.
    companies = [app["company"] for app in api.get("/api/applications").data]
    assert companies == ["Before the snapshot"]
    # What was replaced was copied aside, so the restore can be undone.
    assert restored.data["safety_copy"].endswith("before-restore.db")
    later = [item["name"] for item in api.get("/api/backups").data["backups"]]
    assert restored.data["safety_copy"] in later


def test_a_snapshot_name_this_app_did_not_make_is_refused(api):
    assert api.get("/api/backups").status == 200
    # Not a name of ours: refused before it can become a path.
    assert api.post("/api/backups/..%2Ftracker.db/restore", {}).status == 400
    assert api.post("/api/backups/tracker.db/restore", {}).status == 400
    # A name of ours that is no longer on disk.
    assert api.post("/api/backups/tracker-1999-01-01.db/restore", {}).status == 404
