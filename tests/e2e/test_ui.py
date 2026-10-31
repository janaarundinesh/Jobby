"""Browser tests for the dashboard.

Run them with `RUN_E2E=1 .venv/bin/python -m pytest tests/e2e -q`; without the
environment variable the suite is skipped so the pre-commit hook stays fast.
"""

import base64
import datetime
import json

import pytest


def enter_profile(page):
    """The user picker is the first screen of a session; step through it."""
    page.wait_for_selector("#welcome-list [data-enter-profile]")
    page.locator("#welcome-list [data-enter-profile]").first.click()
    page.wait_for_selector("#panel-overview:not([hidden])")


def screen_state(page):
    """What the app is showing, for a failure that would otherwise be a timeout."""
    return page.evaluate(
        """() => ({
             picker: document.querySelector('#welcome')
               ? !document.querySelector('#welcome').hidden
               : null,
             cards: document.querySelectorAll('#welcome-list [data-enter-profile]').length,
             picker_text: (document.querySelector('#welcome-list') || {}).textContent || '',
             tabs: document.querySelectorAll('#main-tabs .tab').length,
             guard: document.querySelector('#file-guard')
               ? !document.querySelector('#file-guard').hidden
               : null,
             toasts: (document.querySelector('#toasts') || {}).textContent || '',
             classes: document.documentElement.className,
           })"""
    )


def enter_profile_after_reload(page):
    """A reload lands in one of two places; step in only if asked who you are.

    The app remembers the profile in this tab, so a reload either goes straight
    back into it or opens the picker, and which one is only settled once boot()
    has the profile list. Asking earlier answers "neither": the page's own markup
    is gated (html.is-welcome) until then, and an is_visible() asked in that
    window says no for both screens, so the click that follows waits out its
    thirty seconds for a dashboard that is never coming. The gate going down, or
    the picker drawing its cards, is that decision - so wait for one of those,
    then look at which screen is on. A failure says what the app was showing
    instead, because the only place this race has been seen is a slower machine,
    where a bare timeout says nothing.
    """
    try:
        page.wait_for_function(
            """() => {
                 if (document.documentElement.classList.contains('is-welcome')) {
                   return Boolean(
                     document.querySelector('#welcome-list [data-enter-profile]')
                   );
                 }
                 return true;
               }""",
            timeout=15000,
        )
    except Exception as error:
        raise AssertionError("the app showed neither screen: %s" % screen_state(page)) from error
    if page.locator("#welcome-list [data-enter-profile]").is_visible():
        enter_profile(page)


def api_post(page, path, payload):
    """POST through the dashboard's own session, like the app does."""
    return page.evaluate(
        """async ({ path, payload }) => {
            const session = await (await fetch('/api/session')).json();
            const response = await fetch(path, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-Api-Token': session.token },
              body: JSON.stringify(payload),
            });
            return { status: response.status, body: await response.json() };
          }""",
        {"path": path, "payload": payload},
    )


def test_picker_opens_the_dashboard(page):
    page.wait_for_selector("#welcome-list [data-enter-profile]")
    assert page.locator("#welcome-list [data-enter-profile]").count() == 1
    assert page.locator("#welcome-add").count() == 1
    # The picker names who you are picking and stops there: no job count to
    # hint at what sits behind a profile's PIN.
    metas = page.locator("#welcome-list .welcome-card-meta").all_inner_texts()
    assert metas and all("job" not in text.lower() for text in metas)

    enter_profile(page)
    assert page.locator(".stat-grid .stat-card").count() == 6

    page.click('[data-tab="applied"]')
    page.wait_for_selector("#workspace:not([hidden])")
    assert "No applications yet" in page.locator("#applied-empty").inner_text()


def test_no_profile_list_shows_a_job_count(page):
    """The picker and the switch-profile dialog name profiles, not their data."""
    assert api_post(
        page,
        "/api/applications",
        {"company": "Counted Co", "role": "Engineer", "phase": "to_apply"},
    )["status"] == 201
    enter_profile(page)

    page.click("#profile-btn")
    page.wait_for_selector("#profiles-backdrop:not([hidden])")
    assert page.locator("#profile-list .profile-row").count() == 1
    metas = page.locator("#profile-list .profile-row-meta").all_inner_texts()
    assert metas and all("job" not in text.lower() for text in metas)
    page.click("#profiles-done")
    page.wait_for_selector("#profiles-backdrop", state="hidden")



def test_add_application_shows_up_in_the_table(page):
    enter_profile(page)
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.fill('input[name="company"]', "Acme Robotics")
    page.fill('input[name="role"]', "Frontend Engineer")
    page.select_option("#form-status", "screening")
    page.click("#modal-save")
    page.wait_for_selector("#applied-table-body tr")

    row = page.locator("#applied-table-body tr").first
    assert "Acme Robotics" in row.inner_text()
    assert page.locator("#count-applied").inner_text() == "1"

    row.click()
    page.wait_for_selector("#drawer:not([hidden])")
    assert "Frontend Engineer" in page.locator("#drawer").inner_text()
    assert page.locator('#drawer [data-doc-drop]').count() >= 1
    page.click("#drawer .drawer-close")
    page.wait_for_selector("#drawer", state="hidden")


def test_a_duplicate_job_is_offered_instead_of_added(page):
    """The same posting should point at the row you have, not add a second."""
    enter_profile(page)
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.fill('input[name="company"]', "Acme Robotics")
    page.fill('input[name="role"]', "Frontend Engineer")
    page.click("#modal-save")
    page.wait_for_selector("#applied-table-body tr")

    # Different casing and spacing are still the same posting.
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.fill('input[name="company"]', "acme  robotics")
    page.fill('input[name="role"]', "frontend engineer")
    page.click("#modal-save")
    page.wait_for_selector(".dup-open")
    assert "Already on your list" in page.inner_text(".dup-title")
    assert "Acme Robotics" in page.inner_text(".dup-list")
    assert "Frontend Engineer" in page.inner_text(".dup-list")

    # Opening it goes to the job that exists, and nothing is created.
    page.click(".dup-open")
    page.wait_for_selector("#drawer:not([hidden])")
    assert "Frontend Engineer" in page.inner_text("#drawer")
    assert page.locator("#count-applied").inner_text() == "1"
    page.click("#drawer .drawer-close")
    page.wait_for_selector("#drawer", state="hidden")

    # Add anyway is still a real choice, for a genuine second application.
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.fill('input[name="company"]', "Acme Robotics")
    page.fill('input[name="role"]', "Frontend Engineer")
    page.click("#modal-save")
    page.wait_for_selector(".dup-add")
    page.click(".dup-add")
    page.wait_for_function(
        "() => document.querySelectorAll('#applied-table-body tr').length === 2"
    )

    # A file of postings you already have asks the same question, once for all.
    payload = json.dumps([{"company": "ACME ROBOTICS", "role": "Frontend Engineer"}]).encode(
        "utf-8"
    )
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.click('[data-mode="file"]')
    page.set_input_files(
        "#modal-import-file",
        {"name": "jobs.json", "mimeType": "application/json", "buffer": payload},
    )
    page.wait_for_selector(".dup-skip")
    assert "already on your list" in page.inner_text(".dup-title").lower()
    page.click(".dup-skip")
    page.wait_for_function(
        "() => /every row is already on your list/i.test(document.querySelector('#import-status').textContent)"
    )
    assert page.locator("#modal-backdrop").is_visible()

    # Import anyway writes the copy the warning named.
    page.set_input_files(
        "#modal-import-file",
        {"name": "jobs.json", "mimeType": "application/json", "buffer": payload},
    )
    page.wait_for_selector(".dup-add")
    page.click(".dup-add")
    page.wait_for_selector("#modal-backdrop", state="hidden")
    # A row naming no stage lands in To apply, beside the two applied copies.
    page.wait_for_function(
        "() => document.querySelectorAll('#toapply-table-body tr').length === 1"
    )
    assert page.locator("#count-applied").inner_text() == "2"


def test_importing_postings_without_a_status_saves_them_to_apply(page):
    """A file of postings is a to-apply list; the analysis it carries is kept."""
    payload = json.dumps(
        [
            {
                "rank": 1,
                "company": "Airbus Defence and Space GmbH",
                "role": "Junior Simulation Software Developer C/C++",
                "location": "Manching, Bavaria, Germany",
                "source": (
                    "Airbus careers (seen via aggregator listings: speedyapply, freehire, "
                    "Glassdoor; LinkedIn/Indeed also list Airbus Manching simulation roles)"
                ),
                "estimated_match_pct": 72,
                "why_it_matches": "Junior C/C++ simulation role; thesis in C++17.",
                "important_missing_requirements": "No avionics or real-time C experience.",
            }
        ]
    ).encode("utf-8")

    enter_profile(page)
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.click('[data-mode="file"]')
    page.set_input_files(
        "#modal-import-file",
        {"name": "jobs.json", "mimeType": "application/json", "buffer": payload},
    )
    page.wait_for_selector("#modal-backdrop", state="hidden")

    # Nothing was sent, so the job lands under To apply with its match estimate,
    # and the import takes you there rather than leaving you on the Applied tab.
    assert page.locator("#count-toapply").inner_text() == "1"
    assert page.locator("#count-applied").inner_text() == "0"
    assert page.evaluate("document.querySelector('.tab.is-active').dataset.tab") == "to_apply"
    page.wait_for_selector("#toapply-table-body tr")
    row = page.locator("#toapply-table-body tr").first
    assert "Airbus" in row.inner_text()
    assert row.locator(".match-cell").inner_text().strip() == "72%"

    row.click()
    page.wait_for_selector("#drawer:not([hidden])")
    page.wait_for_selector("#drawer .doc-list")
    detail = page.locator("#drawer").inner_text()
    assert "Resume match" in detail
    assert "72%" in detail
    notes = page.input_value("#drawer-notes")
    assert "Why it matches:\nJunior C/C++ simulation role" in notes
    assert "Important missing requirements:\nNo avionics or real-time C experience." in notes

    # The match estimate is editable from the form.
    page.click('#drawer [data-action="edit"]')
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    assert page.input_value('input[name="match_pct"]') == "72"
    page.click("#modal-cancel")
    page.wait_for_selector("#modal-backdrop", state="hidden")
    page.click("#drawer .drawer-close")
    page.wait_for_selector("#drawer", state="hidden")

    # CSV files get the same treatment through their own column names.
    csv = (
        'Company,Position,Estimated Match Pct,Why It Matches\n'
        'Globex,Staff Engineer,65 %,"Remote-friendly, four-day week"\n'
    )
    page.click("#new-btn")
    page.wait_for_selector("#modal-backdrop:not([hidden])")
    page.click('[data-mode="file"]')
    page.set_input_files(
        "#modal-import-file",
        {"name": "jobs.csv", "mimeType": "text/csv", "buffer": csv.encode("utf-8")},
    )
    page.wait_for_selector("#modal-backdrop", state="hidden")
    assert page.locator("#count-toapply").inner_text() == "2"
    assert page.locator("#count-applied").inner_text() == "0"
    globex = page.locator("#toapply-table-body tr", has_text="Globex")
    assert globex.locator(".match-cell").inner_text().strip() == "65%"
    globex.click()
    page.wait_for_selector("#drawer:not([hidden])")
    page.wait_for_selector("#drawer-notes")
    assert "Why it matches:\nRemote-friendly, four-day week" in page.input_value("#drawer-notes")


def test_settings_menu_goals_and_backup(page):
    enter_profile(page)
    page.click("#settings-btn")
    page.wait_for_selector("#settings-menu")

    labels = page.locator("#settings-menu .settings-row-label").all_inner_texts()
    assert labels == [
        "Profile",
        "Lock",
        "Goals",
        "Reminders",
        "Backup",
        "LaTeX engine",
        "Log out",
    ]

    page.click('[data-settings-view="goals"]')
    page.fill("#settings-daily", "7")
    page.fill("#settings-weekly", "35")
    page.click("#settings-save")
    page.wait_for_selector("#toasts .toast")
    assert "Settings updated" in page.locator("#toasts").inner_text()

    # Saving closes the dialog; opening it again shows the new pace.
    page.wait_for_selector("#settings-backdrop", state="hidden")
    page.click("#settings-btn")
    page.wait_for_selector("#settings-menu")
    assert "7 a day" in page.locator("#settings-menu-goals").inner_text()

    # The backup panel has no Save button and produces a real zip download.
    page.click('[data-settings-view="backup"]')
    assert page.locator("#settings-save").is_hidden()
    with page.expect_download() as download:
        page.click("#settings-backup-download")
    assert download.value.suggested_filename.endswith(".zip")
    assert download.value.path().stat().st_size > 0
    page.wait_for_timeout(200)
    assert "Backup downloaded" in page.locator("#toasts").inner_text()

    page.click("#settings-close")
    page.wait_for_selector("#settings-backdrop", state="hidden")


def test_reminders_send_one_daily_notification(page, context):
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    created = api_post(
        page,
        "/api/applications",
        {"company": "Follow Up Ltd", "role": "Engineer", "next_step_on": tomorrow},
    )
    assert created["status"] == 201

    context.add_init_script(
        """
        window.__notes = [];
        window.Notification = class {
          static permission = 'granted';
          static requestPermission() { return Promise.resolve('granted'); }
          constructor(title, options) {
            window.__notes.push({ title, body: (options || {}).body || '' });
          }
          addEventListener() {}
          close() {}
        };
        """
    )
    page.evaluate("localStorage.setItem('jobpipe:reminders', 'on')")
    page.reload()
    page.wait_for_load_state("networkidle")
    enter_profile(page)
    page.wait_for_timeout(300)

    notes = page.evaluate("window.__notes")
    assert len(notes) == 1, notes
    assert "1 follow-up due" in notes[0]["body"]

    # A reload later the same day does not nag again.
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#panel-overview:not([hidden])")
    page.wait_for_timeout(300)
    # The note list is rebuilt on every load, so an empty one plus the "already
    # reminded today" marker proves the second visit stayed quiet.
    assert page.evaluate("window.__notes") == []
    today = datetime.date.today().isoformat()
    assert page.evaluate("localStorage.getItem('jobpipe:reminded:1:' + '%s')" % today) == "1"


def test_dialogs_trap_focus_and_headers_sort_by_keyboard(page):
    enter_profile(page)
    page.click("#settings-btn")
    page.wait_for_selector("#settings-menu")
    assert page.locator("main.page").get_attribute("inert") == ""
    assert page.evaluate("document.querySelector('#settings-backdrop').contains(document.activeElement)")

    for _ in range(12):
        page.keyboard.press("Tab")
    assert page.evaluate("document.querySelector('#settings-backdrop').contains(document.activeElement)")

    page.click("#settings-close")
    page.wait_for_selector("#settings-backdrop", state="hidden")
    assert page.locator("main.page").get_attribute("inert") is None
    page.wait_for_function("document.activeElement.id === 'settings-btn'")

    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-shell:not([hidden])")
    header = page.locator('#toapply-table-shell th[data-sort="company"]')
    assert header.get_attribute("aria-sort") == "none"
    header.focus()
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "ascending"
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "descending"


def test_pin_lock_asks_before_showing_jobs(page):
    enter_profile(page)
    result = page.evaluate(
        """async () => {
            const session = await (await fetch('/api/session')).json();
            const response = await fetch('/api/profiles/1/pin', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-Api-Token': session.token,
                'X-Profile-Id': '1',
              },
              body: JSON.stringify({ pin: '4321' }),
            });
            return response.status;
          }"""
    )
    assert result == 200

    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#welcome-list [data-enter-profile]")
    page.locator("#welcome-list [data-enter-profile]").first.click()
    page.wait_for_selector("#unlock-backdrop:not([hidden])")

    page.fill("#unlock-pin", "0000")
    page.click("#unlock-submit")
    page.wait_for_selector("#unlock-error:not([hidden])")
    assert "not correct" in page.locator("#unlock-error").inner_text()

    page.fill("#unlock-pin", "4321")
    page.click("#unlock-submit")
    page.wait_for_selector("#panel-overview:not([hidden])")
    assert page.locator(".stat-grid .stat-card").count() == 6


def test_skip_link_and_delete_confirmation(page):
    # The first Tab lands on the skip link, which is hidden until focused.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.wait_for_function(
        "document.activeElement.classList.contains('skip-link') &&"
        " document.activeElement.getBoundingClientRect().top >= 0"
    )

    enter_profile(page)
    page.click("#new-btn")
    page.fill('input[name="company"]', "Doomed Corp")
    page.fill('input[name="role"]', "Tester")
    page.click("#modal-save")
    page.wait_for_selector("#applied-table-body tr")

    page.click('#applied-table-body [data-action="delete"]')
    page.wait_for_selector(".confirm-backdrop")
    assert page.evaluate("document.querySelector('.confirm-backdrop').contains(document.activeElement)")
    assert page.locator("main.page").get_attribute("inert") == ""

    # Cancelling keeps the job and hands focus back to the row button.
    page.click(".confirm-backdrop .confirm-cancel")
    page.wait_for_selector(".confirm-backdrop", state="detached")
    assert page.locator("main.page").get_attribute("inert") is None
    assert page.locator("#applied-table-body tr").count() == 1
    page.wait_for_function("document.activeElement.dataset.action === 'delete'")


def test_dashboard_is_installable(page):
    assert page.get_attribute('link[rel="manifest"]', "href") == "manifest.webmanifest"
    manifest = page.evaluate("async () => (await fetch('/manifest.webmanifest')).json()")
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert len(manifest["icons"]) >= 2

    # The worker registers, takes over and caches the app shell.
    page.wait_for_function(
        "async () => { const r = await navigator.serviceWorker.getRegistration();"
        " return !!(r && r.active); }",
        timeout=15000,
    )
    cached = page.evaluate(
        "async () => { const c = await caches.open('jobpipe-shell-v1');"
        " return (await c.keys()).map((request) => new URL(request.url).pathname); }"
    )
    assert "/index.html" in cached
    assert "/styles.css" in cached
    assert "/js/main.js" in cached

    # The title bar of an installed window follows the theme. The toggle lives
    # in the header, so open a profile first (the picker has no top bar).
    enter_profile(page)
    before = page.get_attribute('meta[name="theme-color"]', "content")
    assert before in ("#05060b", "#e8ecf7")
    page.click("#theme-btn")
    after = page.get_attribute('meta[name="theme-color"]', "content")
    assert after != before


def test_offline_start_screen_offers_a_retry(page, context):
    page.wait_for_function(
        "async () => { const r = await navigator.serviceWorker.getRegistration();"
        " return !!(r && r.active); }",
        timeout=15000,
    )
    page.wait_for_timeout(500)

    # Opening the installed app while the server is down: the cached shell loads
    # and tells you how to start the server instead of failing silently.
    context.set_offline(True)
    page.reload()
    page.wait_for_selector("html.is-offline", state="attached", timeout=10000)
    assert page.locator("#file-guard").is_visible()
    assert page.locator("#file-guard-title").inner_text() == "Start the Jobby server"
    # The start screen carries the app's own mark, and it actually loads.
    assert page.evaluate("document.querySelector('#file-guard .file-guard-mark img').naturalWidth") > 0

    # "Check again" while the server is still down leaves this screen standing.
    assert page.locator("#file-guard-retry").is_visible()
    page.click("#file-guard-retry")
    page.wait_for_selector("html.is-offline", state="attached", timeout=10000)
    assert page.locator("#file-guard").is_visible()

    # Once the server is back, the screen steps into the dashboard on its own:
    # the server usually comes up seconds after this screen appears.
    context.set_offline(False)
    page.wait_for_selector("#welcome-list [data-enter-profile]", timeout=20000)
    assert page.evaluate("document.documentElement.classList.contains('is-offline')") is False


def test_the_topbar_reports_the_server_as_active(page):
    """The pill answers "is the small local server still there?" at a glance."""
    enter_profile(page)
    page.wait_for_selector('#server-status[data-state="online"]')
    assert page.locator("#server-status-text").inner_text() == "Server active"
    # Nothing to retry while it answers.
    assert page.locator("#server-status-retry").is_hidden()


def test_a_failed_request_turns_the_indicator_red_and_retry_clears_it(playwright_tools, server_url):
    # A page driven by the service worker hands its requests to the worker, where
    # a page route never sees them. Blocking it here keeps the network exactly as
    # the app's own calls are — the worker never caches the API either way — and
    # lets the test choose what the server answers.
    context = playwright_tools.new_context(
        viewport={"width": 1280, "height": 900}, service_workers="block"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(server_url + "/")
        page.wait_for_load_state("networkidle")
        enter_profile(page)
        page.wait_for_selector('#server-status[data-state="online"]')

        # The server stops. This stands in for it: the endpoints answer nothing
        # at all, the way a stopped process does.
        asked = []

        def dead_server(route):
            asked.append(route.request.url)
            route.abort()

        # Both patterns end in * : the page's own requests carry a query (the
        # health probe always names the tab it is reporting for), and a glob
        # without the * matches the path only, so the request would sail past
        # the route and reach the real server.
        page.route("**/api/health*", dead_server)
        page.route("**/api/resumes*", dead_server)

        # Opening a tab asks the server for its list, and that request now
        # fails: the pill has to say so at once, not on the next heartbeat.
        page.click('[data-tab="resume"]')
        page.wait_for_selector('#server-status[data-state="offline"]', timeout=10000)
        assert page.locator("#server-status-text").inner_text() == "Server offline"
        assert page.locator("#server-status-retry").is_visible()

        # Retry checks there and then, and does not claim the server is back.
        page.click("#server-status-retry")
        page.wait_for_selector('#server-status[data-state="offline"]', timeout=10000)
        assert any("/api/health" in url for url in asked), asked

        # A server that is down long enough to notice: coming back is worth
        # saying. Coming back to the tab asks again straight away, which is the
        # one check a test can time reliably.
        page.wait_for_timeout(5200)
        page.unroute("**/api/health*")
        page.unroute("**/api/resumes*")
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector('#server-status[data-state="online"]', timeout=10000)
        assert page.locator("#server-status-text").inner_text() == "Server active"
        assert page.locator("#server-status-retry").is_hidden()
        assert "is back" in page.locator("#toasts").inner_text()
        assert errors == []
    finally:
        context.close()


RESUME_SOURCE = r"""\documentclass[11pt]{article}
\begin{document}

\begin{center}
{\LARGE \textbf{Test Person}}\\[3pt]
test@example.com
\end{center}

\section{Experience}
\textbf{Acme GmbH} \hfill 2024 -- 2025\\
Simulation Engineer
\begin{itemize}
\item Built a particle model in C++.
\end{itemize}

\section{Education}
\textbf{University of Wuppertal} \hfill 2022 -- 2027\\
M.Sc. Computer Simulation

\end{document}
"""

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def open_resume_tab(page):
    page.click('[data-tab="resume"]')
    page.wait_for_selector("#panel-resume:not([hidden])")


def test_resume_editor_renders_the_source_and_keeps_files(page):
    enter_profile(page)
    open_resume_tab(page)

    # The empty state offers the template built from the profile.
    assert "No resume open" in page.locator("#resume-empty").inner_text()
    page.click('#resume-empty [data-resume-action="new"]')
    page.wait_for_selector("#resume-source:not([hidden])")
    page.wait_for_selector(".resume-page .tex-section")
    assert page.input_value("#resume-name") == "resume-1.tex"
    assert page.locator(".resume-tabs .resume-tab").count() == 1
    assert page.locator(".resume-rail .resume-file").count() == 1

    page.fill("#resume-source", RESUME_SOURCE)
    page.wait_for_function(
        """() => {
          const sections = Array.from(document.querySelectorAll('.resume-page .tex-section'));
          return sections.length === 2 && sections[0].textContent === 'Experience';
        }"""
    )
    page.wait_for_function("() => /^Saved \\d/.test(document.querySelector('#resume-status').textContent)")

    # The commands the resume actually uses are rendered, not printed.
    assert page.locator(".resume-page .tex-size-xlarge strong").inner_text() == "Test Person"
    assert page.locator(".resume-page .tex-line.is-split .tex-right").first.inner_text() == "2024 – 2025"
    assert "Built a particle model in C++." in page.locator(".resume-page ul li").first.inner_text()
    assert page.locator("#resume-issues").is_hidden()

    # A support file: uploaded, listed, and usable by \includegraphics.
    page.set_input_files(
        "#resume-file-input",
        {"name": "profile.png", "mimeType": "image/png", "buffer": PNG_BYTES},
    )
    page.wait_for_selector(".resume-asset-name")
    assert "profile.png" in page.locator(".resume-assets").inner_text()

    page.fill(
        "#resume-source",
        RESUME_SOURCE.replace(
            "test@example.com",
            "test@example.com\n\\includegraphics[width=3cm]{profile.png}",
        ),
    )
    page.wait_for_function(
        "() => { const img = document.querySelector('.resume-page .tex-img'); return Boolean(img && img.complete && img.naturalWidth > 0); }"
    )
    # The bytes come through the signed fetch, not a bare file URL.
    assert page.evaluate("document.querySelector('.resume-page .tex-img').getAttribute('src')").startswith(
        "blob:"
    )

    # Both the source and the file survive a reload.
    page.reload()
    enter_profile_after_reload(page)
    open_resume_tab(page)
    page.wait_for_selector(".resume-asset-name")
    assert "Built a particle model" in page.input_value("#resume-source")
    assert page.locator(".resume-asset-name").first.inner_text().strip() == "profile.png"


def test_a_job_gets_a_tailored_resume(page):
    created = api_post(
        page,
        "/api/applications",
        {"company": "Airbus", "role": "Frontend Engineer", "phase": "to_apply"},
    )
    assert created["status"] == 201
    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")
    page.locator("#toapply-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")

    page.click('#drawer [data-action="open-resume"]')
    page.wait_for_selector("#panel-resume:not([hidden])")
    page.wait_for_function(
        "() => document.querySelector('#resume-name').value === 'airbus.tex'"
    )
    page.wait_for_selector("#drawer", state="hidden")
    assert "Airbus — Frontend Engineer" in page.locator("#resume-preview-title").inner_text()
    groups = [name.lower() for name in page.locator(".resume-rail-group h3").all_inner_texts()]
    assert "tailored for jobs" in groups
    # The tailored copy is a tab of its own next to the master it came from.
    assert page.locator(".resume-tabs .resume-tab").count() >= 1
    assert page.locator(".resume-page .tex-section").count() >= 3


def test_jobs_sort_and_filter_by_resume_match(page):
    for company, pct in (
        ("Below Forty Co", 25),
        ("Forty Band Co", 45),
        ("Sixty Band Co", 65),
        ("High Match Co", 88),
        ("Unscored Co", None),
    ):
        payload = {"company": company, "role": "Engineer", "phase": "to_apply"}
        if pct is not None:
            payload["match_pct"] = pct
        assert api_post(page, "/api/applications", payload)["status"] == 201

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")

    def companies():
        return [name.strip() for name in page.locator("#toapply-table-body tr .company-name").all_inner_texts()]

    header = page.locator('#toapply-table-shell th[data-sort="match_pct"]')
    assert header.count() == 1
    assert header.inner_text().strip().lower() == "match"

    # Highest match first, and a job with no estimate never pretends to be low.
    header.click()
    assert header.get_attribute("aria-sort") == "descending"
    assert companies() == [
        "High Match Co",
        "Sixty Band Co",
        "Forty Band Co",
        "Below Forty Co",
        "Unscored Co",
    ]
    assert [
        value.strip() for value in page.locator("#toapply-table-body .match-cell").all_inner_texts()
    ] == ["88%", "65%", "45%", "25%", "—"]

    # Each number is coloured by its band — the same cuts the filter uses — and
    # the colour comes from the band token, so both themes stay in step.
    cells = page.locator("#toapply-table-body .match-cell")
    assert [cells.nth(index).get_attribute("class") for index in range(5)] == [
        "match-cell is-high",
        "match-cell is-good",
        "match-cell is-fair",
        "match-cell is-low",
        "match-cell is-empty",
    ]
    tones = page.evaluate(
        """() => [...document.querySelectorAll('#toapply-table-body .match-cell')]
             .map((cell) => getComputedStyle(cell).color)"""
    )
    assert len(set(tones[:4])) == 4
    # The em dash keeps the faint default rather than pretending to be a band.
    assert tones[4] not in tones[:4]
    assert page.evaluate(
        """() => {
          const root = getComputedStyle(document.documentElement);
          const resolve = (name) => {
            const probe = document.createElement('span');
            probe.style.color = root.getPropertyValue(name).trim();
            document.body.appendChild(probe);
            const color = getComputedStyle(probe).color;
            probe.remove();
            return color;
          };
          const fromTokens = ['--match-high', '--match-good', '--match-fair', '--match-low'].map(resolve);
          const onScreen = [...document.querySelectorAll('#toapply-table-body .match-cell')]
            .slice(0, 4).map((cell) => getComputedStyle(cell).color);
          return fromTokens.join('|') === onScreen.join('|');
        }"""
    )

    # The drawer carries the same colour in its chip and its detail row.
    page.locator("#toapply-table-body tr", has_text="High Match Co").click()
    page.wait_for_selector("#drawer.is-open")
    assert "match-badge is-high" in page.locator("#drawer .match-badge").get_attribute("class")
    assert (page.locator("#drawer .match-value").get_attribute("class") == "match-value is-high"
            and page.locator("#drawer .match-value").inner_text().strip() == "88%")
    page.keyboard.press("Escape")
    page.wait_for_selector("#drawer", state="hidden")

    header.click()
    assert header.get_attribute("aria-sort") == "ascending"
    assert companies()[-1] == "Unscored Co"
    assert companies()[0] == "Below Forty Co"

    # The match column replaced the chip beside the company name.
    assert "88%" not in page.locator("#toapply-table-body tr .company-name").last.inner_text()

    page.select_option("#filter-match", "80")
    assert companies() == ["High Match Co"]
    page.select_option("#filter-match", "60")
    assert companies() == ["Sixty Band Co"]
    page.select_option("#filter-match", "40")
    assert companies() == ["Forty Band Co"]
    page.select_option("#filter-match", "under40")
    assert companies() == ["Below Forty Co"]
    page.select_option("#filter-match", "none")
    assert companies() == ["Unscored Co"]

    # The extra column still fits the table at this width.
    assert page.evaluate(
        """() => {
          const shell = document.querySelector('#toapply-table-shell');
          return shell.scrollWidth <= shell.clientWidth + 1;
        }"""
    )

    # The filter is shared with the Applied table, which has the same column.
    assert page.locator('#applied-table-shell th[data-sort="match_pct"]').count() == 1
    assert page.locator("#clear-filters").is_visible()
    page.click("#clear-filters")
    assert page.locator("#filter-match").input_value() == ""
    assert len(companies()) == 5
    assert page.locator("#clear-filters").is_hidden()


def test_applied_list_selects_all_and_bulk_deletes(page):
    for company in ("Alpha Co", "Beta Co", "Gamma Co"):
        assert api_post(page, "/api/applications", {"company": company, "role": "Engineer"})["status"] == 201

    page.reload()
    enter_profile_after_reload(page)

    # Saved jobs come before the applications you have already sent.
    labels = [label.strip() for label in page.locator("#main-tabs .tab").all_inner_texts()]
    assert labels[0].startswith("Overview")
    assert labels[1].startswith("To apply")
    assert labels[2].startswith("Applied")
    assert labels[3] == "LaTeX Editor"
    assert labels[4].startswith("Trash")

    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")
    assert page.locator("#applied-table-body tr").count() == 3
    # The select column fits beside the rest of the table at this width.
    assert page.evaluate(
        """() => {
          const shell = document.querySelector('#applied-table-shell');
          return shell.scrollWidth <= shell.clientWidth + 1;
        }"""
    )

    header = page.locator("#select-all-applied")
    assert header.is_visible()
    header.check()
    assert page.locator("#bulk-count").inner_text() == "3 selected"
    assert page.locator("#bulk-bar").is_visible()
    # Nothing here still needs sending, so there is nothing to mark as applied.
    assert page.locator("#bulk-apply").is_hidden()

    page.locator("#applied-table-body [data-select-id]").first.uncheck()
    assert page.locator("#bulk-count").inner_text() == "2 selected"
    assert page.locator("#select-all-applied").evaluate("(box) => box.indeterminate") is True

    page.click("#bulk-delete")
    page.wait_for_selector(".confirm-backdrop")
    assert page.locator(".confirm-backdrop .confirm-title").inner_text() == "Move 2 jobs to trash?"
    page.click(".confirm-backdrop .confirm-ok")
    page.wait_for_selector(".confirm-backdrop", state="detached")

    assert page.locator("#count-applied").inner_text() == "1"
    assert page.locator("#count-trash").inner_text() == "2"
    assert page.locator("#bulk-bar").is_hidden()

    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")
    assert page.locator("#applied-table-body tr").count() == 1
    assert page.locator("#select-all-applied").is_checked() is False

    # The saved list still selects and marks, with its own header checkbox.
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-empty:not([hidden])")
    assert page.locator("#select-all-applied").is_checked() is False


def test_applied_bulk_bar_moves_several_jobs_to_a_stage(page):
    for company in ("Alpha Co", "Beta Co", "Gamma Co"):
        assert api_post(page, "/api/applications", {"company": company, "role": "Engineer"})["status"] == 201

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")

    # Saved jobs have nothing to move: the stage controls belong to applied ones.
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-empty:not([hidden])")
    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")
    assert page.locator("#bulk-status").is_hidden()

    page.check("#select-all-applied")
    assert page.locator("#bulk-status").is_visible()
    assert page.locator("#bulk-stage-apply").is_visible()
    assert page.locator("#bulk-stage-apply").is_disabled()
    assert page.locator("#bulk-apply").is_hidden()

    page.select_option("#bulk-status", "interview")
    assert page.locator("#bulk-stage-apply").is_enabled()
    page.click("#bulk-stage-apply")
    page.wait_for_selector("#bulk-bar", state="hidden")

    def stages():
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('#applied-table-body .stage-select')).map((s) => s.value)"
        )

    assert stages() == ["interview", "interview", "interview"]

    # A wrong stage move is reversible while the toast is still up.
    undo = page.locator("#toasts .toast-action")
    assert undo.count() == 1
    assert undo.inner_text() == "Undo"
    undo.click()
    page.wait_for_function(
        "() => document.querySelectorAll('#applied-table-body .stage-select[data-status=\"applied\"]').length === 3"
    )
    assert stages() == ["applied", "applied", "applied"]
    assert "Undone" in page.locator("#toasts").inner_text()

    page.check("#select-all-applied")
    page.select_option("#bulk-status", "interview")
    page.click("#bulk-stage-apply")
    page.wait_for_selector("#bulk-bar", state="hidden")
    # The stage filter and the overview funnel follow the move.
    page.select_option("#filter-status", "interview")
    assert page.locator("#applied-table-body tr").count() == 3
    assert page.inner_text('#funnel .funnel-row[data-status="interview"] .funnel-count') == "3"

    # The stage select resets, so a second selection starts from "Change stage…".
    page.select_option("#filter-status", "")
    page.check("#select-all-applied")
    assert page.input_value("#bulk-status") == ""
    assert page.locator("#bulk-stage-apply").is_disabled()


def test_marking_saved_jobs_applied_can_be_undone(page):
    for company in ("Alpha Co", "Beta Co"):
        assert (
            api_post(
                page,
                "/api/applications",
                {"company": company, "role": "Engineer", "phase": "to_apply"},
            )["status"]
            == 201
        )

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")

    page.check("#select-all-toapply")
    page.click("#bulk-apply")
    page.wait_for_function("() => document.querySelector('#count-applied').textContent === '2'")
    assert page.locator("#count-toapply").inner_text() == "0"

    undo = page.locator("#toasts .toast-action")
    assert undo.inner_text() == "Undo"
    undo.click()
    page.wait_for_function("() => document.querySelector('#count-toapply').textContent === '2'")
    assert page.locator("#count-applied").inner_text() == "0"
    page.wait_for_selector("#toapply-table-body tr")
    assert page.locator("#toapply-table-body tr").count() == 2
    assert page.input_value("#filter-status") == ""
    # The undoing is itself reported, and the undo offer is gone with the toast.
    assert "Undone" in page.locator("#toasts").inner_text()


def test_long_values_do_not_push_the_match_column_off_screen(page):
    """An aggregator source used to stretch the table until Match was out of view."""
    payload = {
        "company": "Airbus Defence and Space GmbH",
        "role": "Junior Simulation Software Developer C/C++ (m/f/d)",
        "phase": "to_apply",
        "source": (
            "Airbus careers (seen via aggregator listings: speedyapply, freehire, "
            "Glassdoor; LinkedIn/Indeed also list Airbus Manching simulation roles)"
        ),
        "salary": "EUR 50,000 - 65,000 / year (LinkedIn estimate for this role level)",
        "match_pct": 72,
    }
    # Imported, so the long source is shortened to the field limit as it arrives.
    response = api_post(page, "/api/import", {"applications": [payload]})
    assert response["status"] == 200 and response["body"]["imported"] == 1

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")

    assert page.inner_text("#toapply-table-body .match-cell") == "72%"
    assert page.evaluate(
        """() => {
          const shell = document.querySelector('#toapply-table-shell');
          const cell = shell.querySelector('.match-cell');
          return shell.scrollWidth <= shell.clientWidth + 1
            && cell.getBoundingClientRect().right <= shell.getBoundingClientRect().right + 1;
        }"""
    )
    # The source is the value that used to stretch it: it is clipped, not wrapped.
    assert page.evaluate(
        """() => {
          const chip = document.querySelector('#toapply-table-body .source-chip');
          return chip.scrollWidth > chip.clientWidth;
        }"""
    )
    # Salary stays on one line too, so the row height is unchanged.
    assert page.evaluate(
        """() => {
          const cell = document.querySelector('#toapply-table-body .salary-cell span');
          return cell.getBoundingClientRect().height < 24;
        }"""
    )


def test_the_tables_show_the_job_link_instead_of_the_next_step(page):
    for company, url in (
        (
            "Linked Co",
            "https://de.linkedin.com/jobs/view/123 (listed 2 days ago; apply on their site)",
        ),
        ("Plain Co", "notes: ask Ana for a referral"),
        ("No Link Co", ""),
    ):
        payload = {
            "company": company,
            "role": "Engineer",
            "phase": "to_apply",
            "next_step": "Send follow-up",
            "next_step_on": "2026-11-05",
        }
        if url:
            payload["job_url"] = url
        assert api_post(page, "/api/applications", payload)["status"] == 201

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")

    # Link has taken the column's place, and the next step is out of the table.
    headers = page.locator("#toapply-table-shell thead th")
    assert headers.nth(7).inner_text().strip().lower() == "link"
    assert "next step" not in page.locator("#toapply-table-shell thead").inner_text().lower()
    assert page.locator("#applied-table-shell thead th").nth(5).inner_text().strip().lower() == "link"

    linked = page.locator("#toapply-table-body tr", has_text="Linked Co")
    anchor = linked.locator(".link-cell a")
    # An icon button, not printed words or an address: the label is on the link
    # itself, and the pasted note after the address is not part of it.
    assert anchor.inner_text().strip() == ""
    assert anchor.locator("svg").count() == 1
    assert anchor.get_attribute("aria-label") == "Open the Linked Co posting in a new tab"
    assert anchor.get_attribute("href") == "https://de.linkedin.com/jobs/view/123"
    assert anchor.get_attribute("target") == "_blank"
    assert (
        anchor.get_attribute("title")
        == "Open position — https://de.linkedin.com/jobs/view/123"
    )
    assert "de.linkedin.com" not in linked.inner_text()
    assert "Send follow-up" not in linked.inner_text()
    # The button is a small square, not a line of text.
    box = anchor.bounding_box()
    assert box["width"] <= 26 and box["height"] <= 26
    # And the column is set by its one-word header rather than by an address:
    # the empty Applied table shows the width with no rows to stretch it.
    assert page.evaluate(
        """() => document.querySelector('#applied-table-shell thead .link-col')
             .getBoundingClientRect().width < 70"""
    )
    # A link that long would have stretched the row; the column now fits.
    assert page.evaluate(
        """() => {
          const shell = document.querySelector('#toapply-table-shell');
          return shell.scrollWidth <= shell.clientWidth + 1;
        }"""
    )

    plain = page.locator("#toapply-table-body tr", has_text="Plain Co").locator(".link-cell")
    assert plain.locator("a").count() == 0
    assert plain.inner_text().strip() == "notes: ask Ana for a referral"
    empty = page.locator("#toapply-table-body tr", has_text="No Link Co").locator(".link-cell")
    assert empty.inner_text().strip() == "—" and "is-empty" in empty.get_attribute("class")

    # The link opens the posting in its own tab and leaves the drawer shut.
    page.context.route(
        "**/jobs/view/123",
        lambda route: route.fulfill(status=200, content_type="text/html", body="<p>posting</p>"),
    )
    with page.context.expect_page() as popup:
        anchor.click()
    assert popup.value.url == "https://de.linkedin.com/jobs/view/123"
    popup.value.close()
    assert page.locator("#drawer").is_hidden()

    # The row around the link still opens the job.
    linked.locator("td.role-cell").click()
    page.wait_for_selector("#drawer.is-open")
    assert page.locator("#drawer .drawer-title").inner_text().strip() == "Engineer"


def test_a_job_closed_while_it_opens_still_closes(page):
    """A close that lands before the drawer's opening frame must win.

    Opening schedules a frame that adds the `is-open` class; if the drawer is
    closed first, that frame used to put the class back and the closing timeout
    then refused to hide a drawer it saw as open — the panel stayed on screen.
    """
    assert api_post(
        page,
        "/api/applications",
        {"company": "Fast Close Co", "role": "Engineer", "phase": "to_apply"},
    )["status"] == 201
    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")

    page.evaluate(
        """() => {
          // Both in one task, so the opening frame has not run when Escape closes
          // it — the interleaving a quick click-then-Escape produces.
          document.querySelector('#toapply-table-body tr').click();
          document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        }"""
    )
    page.wait_for_selector("#drawer", state="hidden", timeout=2000)
    assert page.locator("#drawer").get_attribute("class") == "drawer"


def test_every_tab_uses_the_whole_window(page):
    """A wide monitor must not leave blank strips down either side of any tab."""
    page.set_viewport_size({"width": 1650, "height": 900})
    enter_profile(page)
    for company in ("Airbus Defence and Space", "XTENDED ENGINEERING GMBH"):
        created = api_post(
            page,
            "/api/applications",
            {"company": company, "role": "Simulation Developer", "match_pct": 72},
        )
        assert created["status"] == 201
    page.reload()
    enter_profile_after_reload(page)
    for tab in ("overview", "to_apply", "applied", "resume", "trash"):
        page.click('[data-tab="%s"]' % tab)
        page.wait_for_timeout(200)
        edges = page.evaluate(
            """() => {
              const page = document.querySelector('main.page').getBoundingClientRect();
              const app = document.querySelector('#panel-resume:not([hidden]) #resume-app');
              const editor = app ? app.getBoundingClientRect() : page;
              return [
                Math.round(page.left), Math.round(page.right),
                Math.round(editor.left), Math.round(editor.right),
              ];
            }"""
        )
        assert edges == [0, 1650, 0, 1650], tab
        assert page.evaluate("() => document.documentElement.scrollWidth") == 1650, tab

    # All that room must not be spent pushing every column apart: the compact
    # ones keep their width and the text columns share the rest.
    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")
    columns = page.evaluate(
        """() => {
          const table = document.querySelector('#applied-table-shell table');
          const width = (name) =>
            Math.round(table.querySelector('thead th.' + name).getBoundingClientRect().width);
          const headers = [...table.querySelectorAll('thead th')];
          return {
            layout: getComputedStyle(table).tableLayout,
            select: width('select-col'), stage: width('stage-col'), date: width('date-col'),
            link: width('link-col'), actions: width('actions-col'), match: width('match-col'),
            company: width('company-col'), role: width('role-col'),
            source: width('source-col'), salary: width('salary-col'),
            table: Math.round(table.getBoundingClientRect().width),
            sum: headers.reduce((total, th) => total + th.getBoundingClientRect().width, 0),
          };
        }"""
    )
    assert columns["layout"] == "fixed"
    assert (columns["select"], columns["link"], columns["actions"]) == (44, 64, 88)
    assert (columns["stage"], columns["date"]) == (176, 132)
    assert columns["match"] == 74
    assert columns["company"] > 200, columns
    assert columns["company"] == columns["role"] == columns["source"] == columns["salary"], columns
    assert abs(columns["sum"] - columns["table"]) < 2, columns
    # The widest company name gets the whole cell instead of stopping at a cap.
    name = page.evaluate(
        "() => { const el = document.querySelector('#applied-table-body .company-name');"
        " return [Math.round(el.getBoundingClientRect().width), el.scrollWidth]; }"
    )
    assert name[0] > 200, name


def test_compile_shows_a_real_pdf_and_falls_back_on_tex_errors(page):
    source = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "{\\LARGE Test Person}\\\\\n"
        "\\section{Experience}\n"
        "\\begin{itemize}\n"
        "  \\item Built things.\n"
        "\\end{itemize}\n"
        "\\end{document}\n"
    )
    created = api_post(page, "/api/resumes", {"name": "master_check.tex", "source": source})
    assert created["status"] == 201
    resume_id = created["body"]["id"]

    probe = api_post(page, "/api/resumes/%d/compile" % resume_id, {"source": source})
    if probe["status"] == 501:
        pytest.skip("no LaTeX engine on this machine")
    assert probe["status"] == 200, probe
    assert base64.b64decode(probe["body"]["pdf"]).startswith(b"%PDF")

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="resume"]')
    page.wait_for_selector("#panel-resume:not([hidden])")
    page.wait_for_selector("#resume-editor-area:not([hidden])")

    page.click("#resume-compile")
    page.wait_for_selector("#resume-pdf:not([hidden])", timeout=60000)
    assert page.get_attribute("#resume-pdf-frame", "src").startswith("blob:")
    status = page.inner_text("#resume-status")
    assert "PDF" in status and status.startswith("tectonic")
    # The browser's viewer brings its own zoom, so the app's controls step aside.
    assert page.locator(".resume-zoom").is_hidden()
    assert page.evaluate(
        """() => {
          const pane = document.querySelector('#resume-pdf').getBoundingClientRect();
          const canvas = document.querySelector('#resume-canvas').getBoundingClientRect();
          return pane.width > canvas.width - 40 && pane.height > 300;
        }"""
    )

    # Editing makes the PDF stale: the live preview comes back.
    page.click("#resume-source")
    page.keyboard.press("Control+End")
    page.keyboard.type("% an edit\n")
    page.wait_for_function("() => document.querySelector('#resume-pdf').hidden")
    assert page.locator("#resume-preview-page").is_visible()
    assert page.locator(".resume-zoom").is_visible()

    # A document TeX refuses falls back to the in-app render, with the log.
    page.evaluate(
        """() => {
          const source = document.querySelector('#resume-source');
          source.value = '\\\\documentclass{article}\\n\\\\begin{document}\\n\\\\nosuchcommand{x}\\n\\\\end{document}\\n';
          source.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    page.click("#resume-compile")
    page.wait_for_function(
        "() => /LaTeX error/.test(document.querySelector('#resume-status').textContent)",
        timeout=60000,
    )
    assert "Undefined control sequence" in page.inner_text("#resume-issues")
    assert page.locator("#resume-pdf").is_hidden()
    assert page.locator("#resume-preview-page").is_visible()



def open_resume_in_editor(page, resume_id, name):
    """Switch to the Resume tab and make sure this resume is the open one."""
    page.click('[data-tab="resume"]')
    page.wait_for_selector("#panel-resume:not([hidden])")
    page.wait_for_selector('#resume-tree button[data-resume-id="%d"]' % resume_id)
    page.click('#resume-tree button[data-resume-id="%d"]' % resume_id)
    page.wait_for_function(
        "() => document.querySelector('#resume-name').value === %r" % name
    )



def test_saving_the_pdf_and_attaching_it_from_the_job(page):
    """Compile, name the PDF, then get it onto a job — from the save dialog or
    later from the job's Documents section."""
    source = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "{\\LARGE Arun Dinesh}\\\\\n"
        "\\section{Experience}\n"
        "\\begin{itemize}\n"
        "  \\item Simulation software in C++.\n"
        "\\end{itemize}\n"
        "\\end{document}\n"
    )
    probe = api_post(page, "/api/resumes", {"name": "probe.tex", "source": source})
    assert probe["status"] == 201
    compiled = api_post(
        page, "/api/resumes/%d/compile" % probe["body"]["id"], {"source": source}
    )
    if compiled["status"] == 501:
        pytest.skip("no LaTeX engine on this machine")
    assert compiled["status"] == 200, compiled

    created = api_post(page, "/api/resumes", {"name": "master_cv.tex", "source": source})
    resume_id = created["body"]["id"]
    assert created["body"]["files"] == []
    job = api_post(
        page,
        "/api/applications",
        {"company": "Airbus Defence and Space", "role": "Simulation Developer", "phase": "to_apply"},
    )
    assert job["status"] == 201

    page.reload()
    enter_profile_after_reload(page)
    open_resume_in_editor(page, resume_id, "master_cv.tex")

    # Nothing compiled yet, so there is nothing to save.
    assert page.locator("#resume-save-pdf").is_disabled()

    page.click("#resume-compile")
    page.wait_for_selector("#resume-pdf:not([hidden])", timeout=60000)
    assert page.locator("#resume-save-pdf").is_enabled()
    # Compiling on its own leaves no file behind.
    assert page.locator(".resume-assets-list li").count() == 0

    page.click("#resume-save-pdf")
    page.wait_for_selector(".savepdf-name")
    assert page.input_value(".savepdf-name") == "master_cv.pdf"
    page.fill(".savepdf-name", "arun-cv-airbus")
    page.click(".savepdf-only")
    page.wait_for_function(
        "() => document.querySelector('#resume-assets').textContent.includes('arun-cv-airbus.pdf')"
    )
    # The name gets a .pdf ending, and the file is on disk for any job to use.
    assert "Saved arun-cv-airbus.pdf" in page.inner_text("#toasts")
    assert api_post(page, "/api/files", None)["status"] == 405

    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")
    page.locator("#toapply-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")
    assert "No documents attached yet." in page.inner_text("#drawer")

    page.click('[data-action="pick-saved"]')
    page.wait_for_selector(".picker-list [data-picker-file]")
    assert "arun-cv-airbus.pdf" in page.inner_text(".picker-list")
    page.click('[data-picker-kind="cover_letter"]')
    page.click(".picker-list [data-picker-file]")

    page.wait_for_function(
        "() => document.querySelector('#drawer').textContent.includes('arun-cv-airbus.pdf')"
    )
    assert "Cover letter" in page.inner_text("#drawer .doc-meta")
    assert "Attached arun-cv-airbus.pdf as Cover letter" in page.inner_text("#toasts")

    # Picking the same slot again offers to replace the copy already there.
    page.click('[data-action="pick-saved"]')
    page.wait_for_selector(".picker-list [data-picker-file]")
    page.click('[data-picker-kind="cover_letter"]')
    page.wait_for_selector(".picker-replace:not([hidden])")
    assert "a cover letter already on this job (arun-cv-airbus.pdf)" in page.inner_text(
        ".picker-replace"
    )
    page.click(".picker-list [data-picker-file]")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('Replaced the cover letter on Airbus Defence and Space')"
    )
    # Replaced, not doubled: the job still holds the one cover letter.
    page.wait_for_function("() => document.querySelectorAll('#drawer .doc-name').length === 1")

    # It is the job's own copy: previewable and downloadable under the saved name.
    page.click("#drawer .doc-name")
    page.wait_for_selector("#preview-backdrop:not([hidden])")
    assert "arun-cv-airbus.pdf" in page.inner_text("#preview-title")
    # The bytes arrive over a signed request, so the download link only gets its
    # blob URL once that has come back.
    page.wait_for_function(
        "() => document.querySelector('#preview-download').getAttribute('download') === 'arun-cv-airbus.pdf'"
    )
    assert page.get_attribute("#preview-download", "href").startswith("blob:")
    assert page.get_attribute("#preview-open", "href").startswith("blob:")

    # Save PDF can also put the file on a job in the same dialog: choosing the
    # job there replaces the tab switch, the second picker and the drawer trip.
    page.click("#preview-close")
    page.click(".drawer-close")
    page.wait_for_selector("#drawer", state="hidden")
    page.click('[data-tab="resume"]')
    page.wait_for_selector("#resume-save-pdf:not([disabled])")
    page.click("#resume-save-pdf")
    page.wait_for_selector(".savepdf-jobs [data-savepdf-job]")
    assert "Airbus Defence and Space" in page.inner_text(".savepdf-jobs")
    # The row names what the job already holds, so a second copy is a choice.
    assert "Cover letter" in page.inner_text(".savepdf-jobs")
    # Nothing is attached until a job is chosen.
    assert page.locator(".savepdf-ok").is_disabled()
    page.fill(".savepdf-name", "arun-cv-airbus-tailored")
    page.click('[data-savepdf-kind="cover_letter"]')
    page.click(".savepdf-jobs [data-savepdf-job]")
    page.wait_for_selector(".savepdf-note:not([hidden])")
    assert "Already has a cover letter (arun-cv-airbus.pdf)" in page.inner_text(".savepdf-note")
    # Choosing the free slot says nothing, because nothing is being doubled up.
    page.click('[data-savepdf-kind="resume"]')
    assert page.locator(".savepdf-note").is_hidden()
    assert page.locator(".savepdf-ok").is_enabled()
    page.click(".savepdf-ok")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('arun-cv-airbus-tailored.pdf')"
    )
    assert "attached it to Airbus Defence and Space as Resume" in page.inner_text("#toasts")

    # The slot now holds a resume, so the next save offers to replace it rather
    # than quietly stacking a second copy.
    page.click("#resume-save-pdf")
    page.wait_for_selector(".savepdf-jobs [data-savepdf-job]")
    page.fill(".savepdf-name", "arun-cv-airbus-v2")
    page.click(".savepdf-jobs [data-savepdf-job]")
    page.wait_for_selector(".savepdf-note:not([hidden])")
    assert "Already has a resume (arun-cv-airbus-tailored.pdf) on this job." in page.inner_text(
        ".savepdf-note"
    )
    assert page.inner_text(".savepdf-ok") == "Save & replace"
    page.click(".savepdf-ok")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('arun-cv-airbus-v2.pdf')"
    )
    assert "replaced the resume on Airbus Defence and Space" in page.inner_text("#toasts")


def test_settings_shows_the_engine_and_installs_it_on_one_click(page, monkeypatch, tmp_path):
    """Settings says whether Compile can build a PDF, and offers to fix it."""
    import server

    tools = tmp_path / "tools" / "tectonic"
    monkeypatch.setattr(server, "TECTONIC_DIR", str(tools))
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    monkeypatch.setattr(server, "ENGINE_INSTALL", {"state": "idle", "message": ""})
    monkeypatch.setattr(server, "engine_version", lambda binary: "Tectonic 9.9.9")

    enter_profile(page)
    page.click("#settings-btn")
    page.wait_for_selector("#settings-backdrop:not([hidden])")
    page.wait_for_function(
        "() => /Not installed/.test(document.querySelector('#settings-menu-engine').textContent)"
    )
    assert "preview" in page.inner_text("#settings-menu-engine")

    page.click('[data-settings-view="engine"]')
    page.wait_for_selector('[data-settings-panel="engine"]:not([hidden])')
    page.wait_for_function(
        "() => /Not installed/.test(document.querySelector('#settings-engine-status').textContent)"
    )
    assert page.locator("#settings-engine-install").is_visible()

    # One click, and the download runs on the server in the background.
    def fake_install(**kwargs):
        tools.mkdir(parents=True, exist_ok=True)
        binary = tools / "tectonic"
        binary.write_bytes(b"#!/bin/sh\n")
        return str(binary)

    monkeypatch.setattr(server.install_tectonic, "install_engine", fake_install)
    page.click("#settings-engine-install")
    page.wait_for_function(
        "() => /Ready/.test(document.querySelector('#settings-engine-status').textContent)",
        timeout=15000,
    )
    assert "Tectonic 9.9.9" in page.inner_text("#settings-engine-status")
    assert page.locator("#settings-engine-install").is_hidden()
    assert page.locator("#settings-engine-error").is_hidden()

    # The hub row reflects it too, without another round of clicking.
    page.click("#settings-back")
    page.wait_for_function(
        "() => /Available/.test(document.querySelector('#settings-menu-engine').textContent)"
    )
    assert "Tectonic 9.9.9" in page.inner_text("#settings-menu-engine")


def test_set_goals_opens_the_goals_panel(page):
    """The Overview's Set goals link goes to the numbers, not the menu behind them."""
    enter_profile(page)
    page.click('[data-action="open-settings"][data-open-settings="goals"]')
    page.wait_for_selector("#settings-backdrop:not([hidden])")
    page.wait_for_selector('[data-settings-panel="goals"]:not([hidden])')
    assert page.inner_text("#settings-title") == "Goals"
    assert page.locator("#settings-hub").is_hidden()
    assert page.locator("#settings-save").is_visible()
    # The cursor lands in the first number a frame after the panel appears.
    page.wait_for_function("() => document.activeElement.id === 'settings-daily'")

    page.fill("#settings-daily", "5")
    page.fill("#settings-weekly", "20")
    page.click("#settings-save")
    page.wait_for_selector("#toasts .toast")
    assert "Settings updated" in page.inner_text("#toasts")

    # The gear keeps opening the menu, as before.
    page.click("#settings-btn")
    page.wait_for_selector("#settings-hub:not([hidden])")
    assert page.locator('[data-settings-panel="goals"]').is_hidden()
    assert page.inner_text("#settings-title") == "Settings"


def test_uploading_into_a_taken_slot_offers_to_replace(page):
    """A second upload into the same slot asks instead of quietly stacking up."""
    enter_profile(page)
    created = api_post(
        page,
        "/api/applications",
        {"company": "Airbus", "role": "Simulation Developer", "phase": "to_apply"},
    )
    assert created["status"] == 201
    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")
    page.locator("#toapply-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")

    page.set_input_files(
        '#drawer [data-doc-kind="resume"]',
        {"name": "cv-v1.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 one"},
    )
    page.wait_for_function("() => document.querySelectorAll('#drawer .doc-name').length === 1")

    # The slot is taken, so the next upload asks what to do with the copy there.
    page.set_input_files(
        '#drawer [data-doc-kind="resume"]',
        {"name": "cv-v2.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 two"},
    )
    page.wait_for_selector(".replace-ok")
    assert "Replace the existing resume?" in page.inner_text(".replace-title")
    assert "cv-v1.pdf" in page.inner_text(".replace-text")
    page.click(".replace-ok")
    page.wait_for_function(
        "() => document.querySelector('#toasts').textContent.includes('Replaced the resume with cv-v2.pdf')"
    )
    # The toast lands before the drawer repaints, so wait for the drawer to name
    # the new file rather than reading it once and hoping it is already there.
    page.wait_for_function(
        "() => document.querySelector('#drawer').textContent.includes('cv-v2.pdf')"
    )
    page.wait_for_function("() => document.querySelectorAll('#drawer .doc-name').length === 1")
    assert "cv-v2.pdf" in page.inner_text("#drawer")

    # Keep both is still there for a copy that is meant to sit beside the other.
    page.set_input_files(
        '#drawer [data-doc-kind="resume"]',
        {"name": "cv-v3.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 three"},
    )
    page.wait_for_selector(".replace-keep")
    page.click(".replace-keep")
    page.wait_for_function("() => document.querySelectorAll('#drawer .doc-name').length === 2")


def wait_for_drawer_timeline(page, label):
    """Wait until the drawer's second render, the one with the new timeline.

    The drawer paints once from what the app already knows and again when its
    fetch comes back, so a stage picked in between lands on a <select> that is
    about to be replaced. The timeline entry only exists after that second
    render, which makes it the signal that the drawer has settled.
    """
    page.wait_for_function(
        "(label) => Array.from(document.querySelectorAll('#drawer .timeline-label'))"
        ".some((item) => item.textContent.includes(label))",
        arg=label,
    )


def test_rejecting_a_job_keeps_it_and_only_asks_about_its_files(page):
    """Rejected is a stage, not a delete: the job stays, its files are a question."""
    enter_profile(page)
    assert api_post(
        page,
        "/api/applications",
        {"company": "Nope Co", "role": "Engineer", "status": "screening"},
    )["status"] == 201
    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")

    page.locator("#applied-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")
    page.set_input_files(
        '#drawer [data-doc-kind="resume"]',
        {"name": "nope-cv.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 rejected"},
    )
    page.wait_for_selector("#drawer .doc-name")

    # The question names the file and offers to keep it.
    page.select_option("[data-drawer-status]", "rejected")
    page.wait_for_selector(".confirm-backdrop")
    assert "Delete the file for this application?" in page.inner_text(".confirm-title")
    assert "nope-cv.pdf" in page.inner_text(".confirm-text")
    page.click(".confirm-backdrop .confirm-cancel")
    page.wait_for_selector(".confirm-backdrop", state="detached")
    wait_for_drawer_timeline(page, "Screening → Rejected")

    # Keep file: the job is still there, rejected, with its resume attached.
    assert page.inner_text("#drawer .status-pill") == "Rejected"
    assert page.locator("#drawer .doc-name").count() == 1
    assert page.locator("#count-applied").inner_text() == "1"

    # Off rejected and back, this time taking the file away.
    page.select_option("[data-drawer-status]", "withdrawn")
    wait_for_drawer_timeline(page, "Rejected → Withdrawn")
    page.select_option("[data-drawer-status]", "rejected")
    page.wait_for_selector(".confirm-backdrop")
    page.click(".confirm-backdrop .confirm-ok")
    page.wait_for_selector("#drawer .doc-empty")

    # The application outlives its files.
    assert page.locator("#drawer .doc-name").count() == 0
    assert page.locator("#count-applied").inner_text() == "1"
    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="applied"]')
    page.wait_for_selector("#applied-table-body tr")
    assert page.locator("#applied-table-body .stage-select").first.input_value() == "rejected"


def test_job_document_preview_and_download_on_a_locked_profile(page):
    """A preview opened with a bare URL came back "This profile is locked"."""
    enter_profile(page)
    created = api_post(
        page,
        "/api/applications",
        {"company": "Locked Ltd", "role": "Simulation Developer", "phase": "to_apply"},
    )
    assert created["status"] == 201
    app_id = created["body"]["id"]

    # A text document, so the preview has to be fetched rather than framed.
    uploaded = api_post(
        page,
        "/api/applications/%d/documents" % app_id,
        {
            "filename": "cover-letter.txt",
            "kind": "cover_letter",
            "content_type": "text/plain",
            "data": base64.b64encode(b"Tailored for the A400M role.\n").decode("ascii"),
        },
    )
    assert uploaded["status"] == 201, uploaded

    # Turn the lock on, then come back in through the PIN prompt.
    locked = api_post(page, "/api/profiles/1/pin", {"pin": "4321"})
    assert locked["status"] == 200, locked
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#welcome-list [data-enter-profile]")
    page.locator("#welcome-list [data-enter-profile]").first.click()
    page.wait_for_selector("#unlock-backdrop:not([hidden])")
    page.fill("#unlock-pin", "4321")
    page.click("#unlock-submit")
    page.wait_for_selector("#panel-overview:not([hidden])")

    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")
    page.locator("#toapply-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")

    page.click("#drawer .doc-name")
    page.wait_for_selector("#preview-backdrop:not([hidden])")
    page.wait_for_function(
        "() => document.querySelector('#preview-body .preview-text') !== null"
    )
    shown = page.inner_text("#preview-body")
    assert "Tailored for the A400M role." in shown
    assert "locked" not in shown.lower()

    # Downloading needs the same unlock token, so it goes through fetch too.
    page.click("#preview-close")
    page.wait_for_selector("#preview-backdrop", state="hidden")
    with page.expect_download() as download:
        page.click("#drawer [data-doc-download]")
    assert download.value.suggested_filename == "cover-letter.txt"
    assert download.value.path().read_text() == "Tailored for the A400M role.\n"


def test_resume_files_and_pictures_open_on_a_locked_profile(page):
    r"""The rail file and \includegraphics both used bare URLs, and a bare URL
    cannot carry the unlock token a PIN-protected profile needs."""
    enter_profile(page)
    open_resume_tab(page)
    page.click('#resume-empty [data-resume-action="new"]')
    page.wait_for_selector("#resume-source:not([hidden])")
    page.set_input_files(
        "#resume-file-input",
        {"name": "profile.png", "mimeType": "image/png", "buffer": PNG_BYTES},
    )
    page.wait_for_selector(".resume-asset-name")
    page.fill(
        "#resume-source",
        RESUME_SOURCE.replace(
            "test@example.com",
            "test@example.com\n\\includegraphics[width=3cm]{profile.png}",
        ),
    )
    page.wait_for_function(
        "() => { const img = document.querySelector('.resume-page .tex-img'); return Boolean(img && img.naturalWidth > 0); }"
    )
    # The reload below would otherwise cut the autosave short, and the locked
    # profile would come back without the source that uses the picture.
    page.wait_for_function(
        "() => /^Saved \\d/.test(document.querySelector('#resume-status').textContent)"
    )

    # Lock the profile, then come back in through the PIN prompt.
    locked = api_post(page, "/api/profiles/1/pin", {"pin": "4321"})
    assert locked["status"] == 200, locked
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#welcome-list [data-enter-profile]")
    page.locator("#welcome-list [data-enter-profile]").first.click()
    page.wait_for_selector("#unlock-backdrop:not([hidden])")
    page.fill("#unlock-pin", "4321")
    page.click("#unlock-submit")
    page.wait_for_selector("#panel-overview:not([hidden])")
    open_resume_tab(page)
    page.wait_for_selector(".resume-asset-name")

    # The picture in the rendered page is fetched with the unlock token.
    page.wait_for_function(
        "() => { const img = document.querySelector('.resume-page .tex-img'); return Boolean(img && img.naturalWidth > 0); }"
    )
    assert page.evaluate(
        "document.querySelector('.resume-page .tex-img').getAttribute('src')"
    ).startswith("blob:")

    # And the file in the rail opens the preview panel, not a locked page.
    page.click(".resume-asset-name")
    page.wait_for_selector("#preview-backdrop:not([hidden])")
    page.wait_for_function(
        "() => document.querySelector('#preview-body .preview-image') !== null"
    )
    assert page.evaluate(
        "document.querySelector('#preview-body .preview-image').getAttribute('src')"
    ).startswith("blob:")
    assert "profile.png" in page.inner_text("#preview-title")
    assert "locked" not in page.inner_text("#preview-body").lower()

    page.click("#preview-close")
    page.wait_for_selector("#preview-backdrop", state="hidden")


def test_a_document_that_vanished_offers_try_again(page):
    """A preview that cannot load says why, and offers another go."""
    enter_profile(page)
    created = api_post(
        page,
        "/api/applications",
        {"company": "Gone Ltd", "role": "Simulation Developer", "phase": "to_apply"},
    )
    app_id = created["body"]["id"]
    uploaded = api_post(
        page,
        "/api/applications/%d/documents" % app_id,
        {
            "filename": "notes.txt",
            "kind": "other",
            "content_type": "text/plain",
            "data": base64.b64encode(b"Notes for the role.\n").decode("ascii"),
        },
    )
    assert uploaded["status"] == 201, uploaded
    document_id = uploaded["body"]["id"]

    page.reload()
    enter_profile_after_reload(page)
    page.click('[data-tab="to_apply"]')
    page.wait_for_selector("#toapply-table-body tr")
    page.locator("#toapply-table-body tr").first.click()
    page.wait_for_selector("#drawer:not([hidden])")

    # Remove the file behind the dashboard's back, as another window could.
    removed = page.evaluate(
        """async ({ path }) => {
            const session = await (await fetch('/api/session')).json();
            const response = await fetch(path, {
              method: 'DELETE',
              headers: { 'X-Api-Token': session.token },
            });
            return response.status;
          }""",
        {"path": "/api/applications/%d/documents/%d" % (app_id, document_id)},
    )
    assert removed == 200

    page.click("#drawer .doc-name")
    page.wait_for_selector("#preview-backdrop:not([hidden])")
    page.wait_for_selector("#preview-body .preview-fallback")
    # The server's own words, not a bare status code.
    assert "Document not found" in page.inner_text("#preview-body")

    # Try again retries the same file rather than leaving a dead end.
    page.click("#preview-retry")
    page.wait_for_selector("#preview-body .preview-fallback")
    assert "Document not found" in page.inner_text("#preview-body")
