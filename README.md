# Jobby

**Your Job Applications Tracking Buddy.** A local dashboard for your job search: an overview of your pipeline, a separate list of jobs you still want to apply to, a sortable table and drag-and-drop board for applications you have sent, follow-up reminders, job descriptions, resume / cover letter / document attachments, and an Overleaf-style resume editor with a live preview.

No accounts, no build step, no Python dependencies. Python 3.8+ standard library only, and the frontend is plain ES modules that the browser loads directly. Data lives in SQLite on your machine. The one optional extra is [Tectonic](https://tectonic-typesetting.github.io), a LaTeX engine the resume editor uses to compile a real PDF; the launchers install it for you, and everything works without it.

## Run it

On macOS, double-click **Jobby.app** in the project folder: it starts the server if it is not already running, waits until it answers and opens the dashboard in its own window — no terminal window and no browser tab. That window is a small Swift program carried inside the bundle as source (`Contents/Resources/JobbyWindow.swift`) and compiled the first time the app opens, so there is still no build step and nothing to install; a machine without a Swift toolchain gets the dashboard in its browser instead. ⌘R reloads it, **View → Open in Browser** (⇧⌘O) puts the same page in your browser, and closing the window quits the app. Downloads land in your Downloads folder, as they would from a browser. It logs to `data/server.log` (rotated to `server.log.1` once it passes 2 MB). That bundle is the one to put in the Dock or Launchpad. Keep it in the project folder, or dock an alias to it, so it can find `server.py`.

On Windows, double-click **Jobby.vbs** for the same thing: the server starts with no console window in the way, and the dashboard opens in a window of its own — Edge (on every Windows 10 and 11 machine) or Chrome in app mode, which is a plain window with no tab strip, no address bar and the Jobby icon on the taskbar. There is no compiled program here and nothing to install: the script is read and run by Windows itself. A machine with no Chromium browser still gets the dashboard, in a normal browser window. It logs to `data/server.log` as the app does, and closing that window stops the server a few seconds later — the same arrangement as macOS, reached a different way.

The server cannot be a child of `Jobby.app`, because macOS tears an app's processes down when the app quits — and the dashboard is allowed to outlive the window (⇧⌘O, or a page left open in the browser). So the bundle carries a small invisible helper, `Jobby.app/Contents/Resources/JobbyServer.app`, which runs the server as its own app. Opening `Jobby.app` with the server already up only shows the dashboard.

The server lives as long as the dashboard does. Opening the app starts it; closing the last dashboard — the app window, or a browser tab when that is what you were using — makes the page say goodbye, and the server stops about five seconds later instead of lingering. A reload says goodbye too and the new page says hello, so reloading never takes it down. A page that vanishes without saying anything (a crash, a forced quit) is still caught by the two-minute idle stop. What a browser cannot do is *start* the server: a web page may not run a program on your machine, so opening the dashboard in a browser while the server is down shows the start screen, with Jobby.app (macOS) or Jobby.vbs (Windows) as the way to start it.

The launchers are the same thing with a visible console: **Start Jobby.command** on macOS, **Start Jobby.bat** on Windows. Either one starts the server in a terminal window and opens the dashboard — in the same app window `Jobby.vbs` opens on Windows, and in a browser tab on macOS — or, if the server is already running, just opens the dashboard. Keep that window open while you work; closing it or pressing `Ctrl+C` stops the server. Use the `.command` when you want to watch the log and stop the server with `Ctrl+C`.

Should the server die while you work, the launcher starts it again in the same window — without opening a second browser tab — and the dashboard reconnects on its own. If it fails three times within seconds of starting (a port already taken, a database it cannot open) the launcher stops trying and says so, instead of spinning forever.

From a terminal instead:

```bash
python3 server.py            # http://127.0.0.1:8787
python3 server.py --open     # ...and open it in your browser
```

Options:

| Flag | Default | What it does |
| --- | --- | --- |
| `--port` | `8787` | Port to listen on |
| `--host` | `127.0.0.1` | Bind address (keep it on loopback for local-only use) |
| `--db` | `data/tracker.db` | SQLite database file |
| `--open` | off | Open the dashboard in your default browser |
| `--exit-when-idle` | off | Stop once no request has arrived for `SECONDS`; the app passes `120` |

Stop it with `Ctrl+C`. Your data stays in `data/tracker.db` and `data/uploads/`.

The launchers also make sure [Tectonic](https://tectonic-typesetting.github.io) is available, because the resume editor's **Compile** button uses it to build a real PDF. On the first start they run [`install_tectonic.py`](install_tectonic.py), which downloads the official release for your machine — Apple silicon, Intel Mac or Windows — into `tools/tectonic/` (git-ignored) and leaves it there. An engine already on `PATH` from Homebrew, Cargo or Scoop is used as it is, and nothing is installed twice. If the download fails (offline, restricted network), the dashboard starts normally and **Compile** falls back to the in-app preview; each launch retries at most once a day. To do it by hand:

```bash
python3 install_tectonic.py          # same installer, prints what it does
brew install tectonic                # macOS, if you prefer Homebrew
```

Windows users can run the launcher, use `python install_tectonic.py`, or follow the [Tectonic install page](https://tectonic-typesetting.github.io/install.html).

Install the dashboard as an app if you like: see [Install it as an app](#install-it-as-an-app).

Opening `public/index.html` from Finder works too: if the server is already running, the page forwards to it, and if it is not you get a short start screen with the command to run, a **Check again** button and a link to the dashboard. That screen also keeps checking on its own, so starting the server is enough — no click needed. Either way the server must stay running for saves to land, so keep that Terminal window open. If an action reports that it cannot reach the server, the topbar pill turns red and its **Retry** button checks for you (see [If the server stops](#if-the-server-stops)); a single failed request is retried once automatically, so a stale connection (after the machine sleeps, for example) does not interrupt a save.

## If the server stops

Every screen is served by that one Python process, so the dashboard is only as alive as it is. Four things keep that visible and fixable:

- **The pill in the topbar** answers *is the server still there?* It reads `GET /api/health` every 20 seconds, turns red the moment a request cannot reach the server (no waiting for the next check), and turns green again as soon as one gets through. Its tooltip names the address it is watching.
- **Retry**, which appears on the pill while it is red, checks there and then and keeps the checking on a short backoff — 2 seconds at first, growing to 20 — until the server answers. After an outage long enough to notice, coming back offers **Reload** in a message, for a page whose lists may be stale.
- **The launcher** on macOS (`Jobby.app`, which starts the server when it is not running) and the `.command` / `.bat`, which restart a server that died under them (see [Run it](#run-it)).
- **Closing the dashboard**, which stops the server the app started: it goes two minutes after the last request it answered, since a dashboard asks for `/api/health` every 20 seconds. A server started in a terminal keeps running until `Ctrl+C` instead.

A page in a browser cannot start a process on your computer, and that is the limit to know about. The Retry button only ever checks. *Starting* the server happens in a launcher: open **Jobby.app** (macOS) or **Jobby.vbs** (Windows) again, double-click **Start Jobby.command** / **.bat**, or run `python3 server.py`.

This is also why a dashboard installed with the browser's own *Install page as app* can show the start screen: that icon is the browser's, so opening it cannot start Python. Use **Jobby.app** in the Dock instead and the server is started for you — see [Which app icon to use](#which-app-icon-to-use).

## Which app icon to use

There are two ways to get an icon for this dashboard, and only one of them can start the server.

- **Jobby.app** (macOS, in the project folder) — a real app bundle. Double-click it, or drag it to the Dock, and it starts the tracker, waits for it, and opens the dashboard. Keep the bundle where it is (dock an alias if you want it elsewhere), so it can find your data.
- **Jobby.vbs** (Windows, in the project folder) — the same thing as a script, which is what Windows can run without a build step. Double-click it, or pin it to the taskbar, and it starts the tracker out of sight, waits for it, and opens the dashboard in its own window. Keep it beside `server.py`.
- **The browser's install** (*Add to Dock* in Safari, *Install page as app* in Chrome) — a window without tabs. It looks the same, but it is a web page: while the server is down it can only show the *Start the Jobby server* screen. If you use this one, keep the server running (start it at login, or leave the `.command` window open) — or use the app bundle above instead.

## Install it as an app

The dashboard ships a [web app manifest](public/manifest.webmanifest) and a [service worker](public/sw.js), so it can run in its own window without browser tabs.

- **Chrome, Edge, Brave** — open the dashboard, then use the install icon in the address bar (or ⋮ → *Cast, save and share* → *Install page as app*).
- **Safari on macOS 14+** — **File → Add to Dock**. On iPhone or iPad, **Share → Add to Home Screen**.
- Anything else that supports installing web apps will offer it too; the app is served from `http://127.0.0.1:8787`, so it only works while that server runs.

The service worker caches the shell — `index.html`, `styles.css`, the ES modules and the icons — so an installed window still opens when the server is stopped, and shows the *Start the Jobby server* screen with the command and a **Check again** button, which keeps looking in the background and steps into the dashboard once the server answers. Job data, settings and documents are **never** cached: every API call goes straight to the server, so you cannot be shown a stale list of applications. While the server is running a reload always fetches the current files, so upgrades take effect immediately.

## The tabs

- **Overview** — total applications, active, interviews, offers, response rate, saved jobs, the stage funnel, applications per week (last 8 weeks) and follow-ups due in the next 7 days.
- **To apply** — jobs you have saved but not sent yet. Also a table, with a one-click **Mark as applied** action on every row (or in the detail drawer). Saved jobs are excluded from pipeline stats, follow-up reminders and the board until you send them.
- **Applied** — everything you have actually sent. Table view (search, filter, sort, inline stage changes) and board view (drag cards between Applied → Screening → Interview → Offer, plus Rejected/Withdrawn).
- **Bulk actions** — in either table, tick the checkbox on several jobs (or the header checkbox to select everything currently shown) and a bar appears with **Delete** and **Clear**. On saved jobs it also offers **Mark as applied**, which sends them to the Applied tab with today's date; on applied jobs it offers **Change stage… → Set stage**, which moves the whole selection to Screening, Interview, Offer, Rejected or Withdrawn at once. Delete asks once, then moves the selection to Trash, where it can still be restored. Moving the stage or marking saved jobs as applied offers **Undo** in the message for a few seconds, which puts every job back where it was. The selection clears when you change tabs.
- **LaTeX Editor** — the resume editor: source on the left, the rendered page on the right and your resume files in the rail. See [Resume editor](#resume-editor).
- **Trash** — deleted jobs wait here. Restore one, or delete it permanently when you are sure.

Click any row or card to open the detail drawer: stage, dates, salary, source, contact, job description, documents, personal notes, and the full stage timeline.

## Profiles

The app keeps separate profiles — for example one for your own search and one for freelance work. Each profile has its own jobs, trash, documents and goals; nothing is shared between them.

**The picker comes first.** Opening the dashboard in a new browser session shows a user screen with one round avatar per profile, what it says about itself (its headline or email, or *PIN protected*) and a lock badge when it has a PIN. It deliberately shows no job count: the screen is a door, and how many jobs sit behind a profile is that profile's business once its PIN is in. Click an avatar to open that profile; the plus circle beside them adds someone new. Reloading the same tab goes straight back into the profile you were using — the picker returns in a new tab or after closing the browser. **Log out** in Settings drops the session (and the unlock token) and returns to the picker.

- The **profile button** in the header (your initials and the profile name) opens the same list as a dialog, with the active profile tagged **Current**, and **New profile** there creates another user. It follows the picker's rule and names each profile without a job count, so no profile list in the app says how much data a profile holds; the only place jobs are counted is the delete confirmation, which is inside that profile and on its way out.
- **Clicking a profile** switches to it: the dashboard reloads with that profile's jobs and goals.
- **Deleting a profile** happens only inside that profile: **Settings → Delete this profile…**, then a confirmation spelling out how many jobs and files go with it. A profile can never be deleted from someone else's dashboard; the API refuses it too. After deleting, the app returns to the user screen and keeps at least one profile.

Contact details are edited in **Settings** and apply to the profile you are on.

## PIN lock

Any profile can be locked with a 4–8 digit PIN. It is set, changed or removed in **Settings → Lock**; changing or removing it asks for the current PIN.

- A locked profile shows a padlock in the picker and asks for the PIN before its jobs are opened. Pressing the profile asks for the PIN, a wrong one says so and lets you try again.
- The lock is enforced by the local server, not just the screen: jobs, documents, goals and trash of a locked profile answer `423 Locked` until a valid unlock token is supplied. The token lives in memory on the server and in that browser tab only; it lasts 12 hours, a page reload keeps it, and restarting the server or closing the tab asks for the PIN again.
- Previews and downloads of a locked profile's files go through a signed request rather than a plain link, because a plain link carries no unlock token and would come back as *This profile is locked*. If a token goes stale, the preview says so and asks for the PIN again; a file that is gone shows the server's message with a **Try again** button.
- **Lock now** in the profile dialog (the open-padlock icon on the active profile) drops the token and returns to the user screen immediately.
- The PIN is stored as a salted PBKDF2-SHA256 hash, never in plain text. It protects the dashboard and its API, not the database file itself: anyone who can read `data/tracker.db` can still read the jobs without the PIN.

## Deleting and the trash

Every job can be deleted from its table row, board card or detail drawer. Each delete asks for confirmation first. Deleting does **not** remove anything permanently — the job moves to the **Trash** tab with its timeline and files intact.

From Trash you can:

- **Restore** a job, which puts it back in Applied or To apply exactly as it was.
- **Delete forever**, which asks for a second confirmation and then removes the job's database row, its status history, all document records and every attached file from `data/uploads/`.
- **Empty trash**, a button on the Trash tab that permanently clears the whole bin — every job, its notes, timeline and attached files — after one confirmation. It is disabled while the trash is empty.

There is no automatic emptying otherwise: jobs stay in the trash until you restore or purge them.

## Adding jobs

Click **Add job** (or press `N`). There are three ways in:

1. **Manual** — fill in the form yourself, choosing **Applied** or **Saved — not applied yet** under Status.
2. **From link** — paste a job posting URL and press **Fetch details**. The server reads the page's `schema.org JobPosting` JSON-LD (used by Greenhouse, Lever, Workable, many company career sites) and falls back to OpenGraph metadata. It fills company, role, location, work mode, salary, source, the job description, and the posting URL for you to review. Some sites (LinkedIn, many Workday boards) block automated requests or render postings with JavaScript — you'll get a clear error and can switch to manual entry. Nothing is saved without your confirmation. Only public `http(s)` addresses are fetched; private/loopback addresses are refused.
3. **Import file** — drop or choose a `.csv` or `.json` file. Rows are appended, except postings you already have: the file is checked against your list first and you choose whether to skip those rows or import them anyway. Also available from **Export ▾ → Import CSV or JSON**.

### CSV format

Header names are matched loosely (`position`/`title` → role, `date applied` → applied_on, `company name` → company, `job description` → description, `estimated match pct` → match_pct, and so on). Only `company` and `role` are required.

```csv
Company,Role,Phase,Status,Applied On,Location,Work Mode,Source,Salary,Match %,Job URL,Next Step,Next Step Date,Contact,Email,Description,Notes
"Acme, Inc.",Senior Frontend Engineer,Applied,Applied,2026-10-20,Berlin,Hybrid,Referral,€90–110k,84,https://acme.com/jobs/1,Send follow-up,2026-10-27,Marta Klein,marta@acme.com,"Own our design system","Referred by Priya"
"Globex",Staff Engineer,To apply,,2026-10-24,Remote,Remote,Company site,€120k,65,https://globex.com/jobs/2,Tailor resume,2026-10-26,,,,"Met at a meetup"
```

`Phase` accepts `Applied` or `To apply` (also saved / wishlist / backlog / not applied). Status values are normalized (`phone screen` → screening, `offer` → offer, `rejected`/`declined` → rejected, and so on), as are work modes and dates (`YYYY-MM-DD`, `MM/DD/YYYY`, `DD/MM/YYYY` when the first number is over 12). A match estimate (`72`, `"72%"`) is stored as a whole number from 0 to 100. JSON accepts either an array of objects or `{ "applications": [...] }` with the same field aliases.

A row that names **no status and no phase was never sent**, so it is imported as *Saved — not applied yet* and appears under **To apply** with today's date left empty. Add a `status` or `phase` column when the file really does describe applications you made; a named stage (even `Rejected`) keeps the row in Applied. After an import the dashboard opens the list the rows actually went to, and the message says how many were saved to apply versus marked as applied.

No row is dropped over a field that is merely long: an aggregator's source line or tracking URL is shortened to the app's limit (ending in `…`) so the posting still arrives. A row that really cannot be used — no company or no role — is named in the import message with the reason, instead of a bare count.

### Duplicates

A job counts as the same posting when its **company and role** match, ignoring case and extra spaces. Adding one you already have — by hand, from a fetched link, or in an imported file — opens a dialog naming the job that exists, with **Open job** to go straight to it, **Add anyway** for a genuine second application, or **Cancel**. Imports check the whole file against your list, and against the rows before it in the same file, and offer **Skip them** or **Import anyway** before anything is written; skipping everything says so and leaves the file unimported. Jobs in the Trash don't count, so a posting you threw away can be added again, and two different roles at the same company are two jobs.

Files that come out of a job-search tool often carry more than the posting: a match estimate, a note on why the role fits, and the requirements the posting asks for that your resume does not cover. Two of those are kept rather than dropped — `estimated_match_pct` becomes the job's **Resume match**, and `why_it_matches` / `important_missing_requirements` are written into the job's **notes** under those headings, which the detail drawer and the search box already read. Rename them if your file spells them differently: `match`, `match_percentage`, `match_score` and `resume_match` also count as the match, and `missing_requirements` or `gaps` as the gaps. Anything else the file carries (`rank`, `employment_type`, `application_deadline`, …) is ignored.

## Documents

Open a job and use the **Documents** section to attach a **resume**, **cover letter**, or any **other** file. You can also drag files straight onto the section: drop one on the **Resume**, **Cover letter** or **Other** button to file it as that type, or drop anywhere in the box to attach several at once — the type is then guessed from the file name (`resume`, `cv`, `cover letter`, otherwise Other). Files are stored under `data/uploads/<job id>/` and listed with their type, size and date. Limit is 10 MB per file; anything larger is skipped with a message. Deleting a job also deletes its files. If the type you upload is already on the job, the dialog asks what to do with the copy that is there: **Replace** deletes it (and its file) as the new one goes in, **Keep both** attaches the new file alongside it, and **Cancel** drops the upload — the same choice the *From saved files* picker and **Save PDF** offer.

**From saved files** attaches something you already have instead of uploading it again: it lists every file kept with your resumes — the PDFs **Save PDF** made, plus support files — and you pick the type it goes in as (**Resume**, **Cover letter** or **Other**, switchable at the top of the dialog). The job then gets its own copy, so removing the resume or the job later cannot take the other one's file with it. If that type is already on the job, a **Replace** box appears above the list (ticked by default): leaving it ticked swaps the existing file out for the one you pick, unticking it keeps both. When the PDF has just been compiled, skip this section entirely: **Save PDF** can attach it to a job from its own dialog.

Click a document name to preview it without leaving the dashboard: PDFs render in the browser's viewer inside the preview panel, images display inline, and plain text, CSV and Markdown files are shown as text. The panel also offers **Download** and **Open in tab**, and closes with `Esc`, the close button, or a click outside it. Word documents and other formats browsers cannot render show a short notice with a download link instead. Files are served with a safe content type and never as executable HTML. The bytes are fetched with the session and unlock tokens and shown from a blob URL, so previews and the **Download** button work on a PIN-protected profile too.

## Resume editor

The **LaTeX Editor** tab (formerly *Resume*) is a three-pane editor laid out like Overleaf: a rail of resume files, the LaTeX source with line numbers, and the rendered page. It is per profile, so each person keeps their own. Like every tab, it runs the full width of the window — the dashboard has no centred column — so a wide monitor has no blank strips beside it and the source and preview get the space.

- **Source** — the editor is a plain textarea over the `.tex` file, so what you see is exactly what Overleaf will compile. `Tab` inserts two spaces, `Ctrl`/`Cmd`+`S` saves immediately, and everything else saves itself about a second after you stop typing (the header shows *Saved 14:32*). Tabs across the top switch between open resumes.
- **Preview** — rendered in the browser as you type; no TeX installation, and it works on Windows. It understands the commands resumes actually use: `\section` / `\subsection` with optional `*`, `itemize` and `enumerate`, `\textbf`, `\textit`, `\emph`, `\underline`, `\texttt`, `\textsc`, `\href`, `\url`, `\hfill` right-aligned dates (the `Company … 2021 – 2024` line), `\begin{center}`, simple `tabular`, sizes such as `{\Large Name}`, `\textcolor`, `\definecolor`-free colours, dashes and quotes. Unknown macros are dropped but their arguments are kept, so a custom `\cvsection{…}` still shows its text. **Compile** re-renders on demand and reports how long it took, and the strip under the editor names unbalanced braces or an environment that is never closed (with the line), instead of quietly producing a broken page.
- **Real PDF** — when [tectonic](https://tectonic-typesetting.github.io) is installed (the launchers put it in `tools/tectonic/` on first start; `brew install tectonic` or `python3 install_tectonic.py` work too), **Compile** runs it on the saved source and shows the actual PDF in the preview pane, with the stock browser viewer for pages, zoom and search. Uploaded support files are placed beside the `.tex` first, so `\includegraphics{profile.png}` resolves exactly as it does in Overleaf. Compiling is sandboxed (`--untrusted`, so no shell escape), limited to 60 seconds, and keeps the log. Without an engine — or when TeX refuses the source — the editor falls back to the in-app preview, and a TeX error puts the engine's log under the source instead of failing silently. Any edit returns to the live preview, because the PDF would be out of date.
- **Save PDF** — **Compile** only shows the PDF; **Save PDF** keeps it. Pressing it opens a single dialog for both the name (suggested from the resume, `master_cv.tex` → `master_cv.pdf`) and the job the file is for: choose the job and the PDF is attached to it as **Resume**, **Cover letter** or **Other** in the same step, which replaces the old trip through the job's Documents section and its *From saved files* picker. Each job in that list also says what it already holds — **Resume**, **Cover letter**, **Other**, with `×2` when a job has two. If the slot you picked is already taken, the dialog says which file is there (*Already has a cover letter (arun-cv-airbus.pdf) on this job*) and offers **Replace it instead of adding a second copy**, ticked by default, which turns the button into **Save & replace** — the old document and its file are removed as the new one goes in, so a tailored resume does not pile up beside last week's. Untick it to keep both, which is what attaching always did. **Just save** keeps it with the resume without attaching anything. A `.pdf` ending is added when you leave it off. A resume written for a job — one created from that job — opens with that job already selected, so **Save & attach** is one click. Nothing is saved behind your back: compiling on its own writes no file. Deleting the resume deletes the PDFs saved with it.
- **Files** — upload a photo, a PDF, a bibliography, a note: anything that belongs beside the `.tex`. The rail lists each file with its size; click to open it in the same preview panel job documents use (PDF framed, image inline, text as text, with **Download** and **Open in tab**), and `×` removes it. They are stored in `data/uploads/resume-<id>/` and deleted with the resume. The bytes are fetched with the session and unlock tokens rather than through the file's own URL, so a support file opens on a PIN-protected profile too — a bare URL can carry no headers and came back *This profile is locked*. Reference an image from the source and it renders in place:

  ```latex
  \begin{center}
  {\LARGE \textbf{Arun Dinesh Jana}}\\[3pt]
  \includegraphics[width=3cm]{profile.png}\\
  janaarundinesh@gmail.com
  \end{center}
  ```

  `width=` accepts `cm`, `mm`, `in`, `pt`, `px`, `em` and a `0.3\textwidth` fraction; without it the image is capped to the page width. A file that has not been uploaded is shown as a dashed placeholder instead, so a missing photo is visible rather than silent. The picture itself is loaded the same signed way as a rail file — cached per file, so typing does not re-download it and the preview still shows it on a locked profile.
- **Master and tailored resumes** — **Start from a template** writes a starter resume from your profile (name, headline, location, phone, email, links) that already renders. Keep your general versions as masters. Every job's detail drawer has a **Resume** button: it creates a tailored copy from your newest master — source *and* support files — and opens it, or reopens the copy you already made for that job. The rail groups them under *Tailored for jobs* with the company and role.
- **Export** — **Download .tex** for Overleaf or your own LaTeX setup, and **Print / Save as PDF**, which prints just the page (no dashboard chrome) through the browser's print dialog.

The live preview is an approximation of LaTeX, not a TeX engine: the fonts, spacing and page breaks will differ from a compiled PDF, so check anything you actually send with **Compile** (when tectonic is installed) or in Overleaf. Typing after `\end{document}` is ignored, exactly as in LaTeX.

## Using the dashboard

- **Search / filters** — search company, role, location, source, description and notes; filter by stage, location type, source and resume match (**80% and above**, **60–79%**, **40–59%**, **below 40%**, **no estimate**). The same filters apply to whichever tab you are on (stage only matters for Applied), and they narrow the board as well as the table.
- **Settings** — the gear in the header opens a menu for the current profile: **Profile** (name, headline, email, phone, location, links, and *Delete this profile…*), **Lock** (the PIN), **Goals** (daily and weekly), **Reminders** (the daily notification), **Backup** (a zip of the database and documents), **LaTeX engine** (below) and **Log out**. Each option opens its own panel with a back arrow, and `Esc` returns to the menu before closing. *Save changes* stores the details and goals together; the PIN, reminder and backup actions are buttons inside their own panels.
- **Sorting** — click a table header, or focus it and press `Enter`. Dates sort newest first and **Match** sorts highest first, so one click shows your best-fitting saved jobs at the top; jobs with no estimate always sit at the bottom rather than pretending to be a 0% match. The sorted column reports `aria-sort` to screen readers.
- **Follow-ups** — any Applied job with a next-step date in the next 7 days appears on the Overview. Every due label — "Due tomorrow", "Due in 3d", "Overdue by 2d" — is shown in red, with overdue ones in a heavier weight and a stronger tint. The same red due labels appear with the next step in the detail drawer and on board cards; a card whose step is overdue is tinted red too.
- **Goals and streak** — the Overview has an **Application goals** panel with a ring for each target: daily and weekly (default 3 a day, 10 a week). Each ring shows applications sent against the target and turns green once it is met, and crossing a target while you use the app sets off a short confetti burst with a "Daily/Weekly goal complete" toast (confetti is skipped if your system asks for reduced motion; the toast still appears). Set the numbers in **Settings → Goals** — the gear in the header opens the menu, while **Set goals** in the panel goes straight to the goals panel with the cursor in the daily field — and they are stored for the current profile in the database. A day counts once you have at least one application with that applied date; the week runs Monday to Sunday.
- **Streak** — the next panel counts consecutive days with at least one application. It keeps counting from yesterday until you send something today, so a streak is only broken once a full day passes with nothing sent. The last seven days are shown as dots and the longest run ever is listed as your best. The flame is drawn in flame orange with a warm halo and flickers gently all the time, burning brighter and a little faster once a streak is alive; both animations stop when your system asks for reduced motion.
- **Rejecting a job** — moving any application to the Rejected stage (from the table, the board, the detail drawer or the edit form) keeps it: the job stays in Applied with its notes and timeline, because a rejection is part of the history, not a reason to lose it. When the application has files attached, one question follows — *Delete the files for this application?* — naming them, with **Delete files** removing those resumes, cover letters and other documents from the database and disk, and **Keep files** leaving them where they are. A job with nothing attached is not asked about at all. Marking a posting "No longer open" from the review panel does not ask either, since that is already a confirmation step.
- **Needs review** — once an application reaches 30 days in an active stage (Applied, Screening, Interview), it is automatically flagged: a **Review** badge appears in the table and on its board card, and it is listed in the **Needs review** panel on the Overview. Open it and choose **Still open** to record the check, or **No longer open** to close it as rejected. A reviewed job becomes due again after another 30 days, so long-running processes get checked monthly. Rejected, withdrawn and offered jobs, and jobs in To apply, are never flagged.
- **Export** — every job as JSON or CSV, including phase, description, notes and the resume match.
- **Job link** — the address saved with a job (typed in the **Job URL** field, or imported as `job_url`) has its own **Link** column in both tables. Each row with an address shows one small icon button — a box with an arrow leaving it — instead of printing a wall of URLs, which keeps the column as narrow as its one-word header; hovering says *Open position* and the address it goes to, and the button carries *Open the <company> posting in a new tab* as its accessible name. Clicking opens the posting in a new tab, while the row around it still opens the detail drawer. A job with no link shows `—`, and a value that is not a web address stays visible as the text that was saved, dim and unlinked, rather than becoming a link that goes nowhere. When notes were pasted after the address — `…/junior-simulation-software-developer (listed 2 days ago; apply on their site)` — the link opens the address alone, since a URL never contains a space. The detail drawer's **Job posting** link follows the same rule. This column took the place of the next step in the tables — steps stay in the detail drawer, on board cards and in the Overview's follow-ups — because the link is the field you reach for while working a list.
- **Resume match** — a job can carry an estimate of how well the posting fits your resume, as a whole number from 0 to 100. It arrives with an import (`estimated_match_pct`), or you type it into **Resume match %** when adding or editing a job. It has its own sortable **Match** column in both tables (`88%`, or `—` when there is none), appears as a small `72% match` chip on board cards, and sits in the detail drawer's **Resume match** row. Every one of those numbers is coloured by its band — green at **80% and above**, indigo at **60–79%**, amber at **40–59%**, red **below 40%** — the same cuts as the filter, so a number and the band that hides it always agree; the colour is the whole signal, so nothing is dimmed or hidden by it. Filter the lists by band from the toolbar, or click the column to put the strongest matches first. An empty field means no estimate, and the number is never invented or recomputed. Long source lines and salary strings are each clipped to one line, so the table never grows so wide that the Match column is pushed out of view; below 1100 px the Source and Salary columns step aside entirely, and both stay readable in the detail drawer. From 1440 px the tables switch to fixed columns: the compact ones — checkbox, stage, date, link, match, actions — keep the width their content needs, and company, role, source and salary share the rest, filling it instead of leaving every cell stranded in a wide column of its own.
- **LaTeX engine** — **Settings → LaTeX engine** says whether **Compile** can build a real PDF on this computer, naming the engine it found (`Tectonic 0.17.0`) and where it lives. When there is none, an **Install Tectonic** button fetches the official release (about 20 MB) into `tools/tectonic/` inside the project — no administrator rights, nothing installed system-wide, and the rest of the tracker stays usable while it downloads, with the panel following along until it is ready. A download that cannot finish (offline, a restricted network, no build for the machine) says why and offers **Try again**; `python3 install_tectonic.py` does the same thing from a terminal. The launchers run that installer before the first start, so this panel is usually just a confirmation.
- **Reminders** — **Settings → Reminders → Turn on notifications** asks the browser for permission once. After that, at most one notification per profile per day summarises what is due ("2 follow-ups due · 1 job to review") when you open the dashboard; it never fires twice for the same day, and nothing leaves your machine. Where a page is not allowed to raise a notification (installed windows and mobile browsers), the service worker posts it instead and clicking it brings the dashboard forward.
- Keyboard: `N` new job, `/` search, `Esc` close dialog/drawer, `Enter` opens the focused row, `Tab`/`Shift+Tab` stay inside an open dialog, arrow keys move between tabs.
- Screen readers: dialogs are labelled, the page behind an open dialog is `inert`, focus returns to the button that opened it, and toasts are announced politely.
- Theme follows your system preference; the header toggle overrides it and remembers your choice.

## Look and feel

The interface uses an Apple-style glass treatment: translucent frosted panels over a slowly drifting colour backdrop, a specular highlight along the top edge of each surface, soft layered shadows and rounded 20–26 px corners. Sheets, the detail drawer and dialogs are more opaque than the panels behind them, so text stays comfortable while the glass still reads as glass.

Cards respond to the pointer: stat cards and panels lift two pixels while a soft light glint fades in behind their content, goal rings glow, board cards raise with an accent rim, document rows lift, and table rows and follow-up items gain a slim accent edge. The effects only run on devices with a real pointer and are reduced to colour changes when the system asks for reduced motion.

Both themes are glass: the header toggle switches between dark smoked glass and light frosted glass. Everything is driven by custom properties at the top of `styles.css` if you want to retune it:

- `--surface`, `--surface-raised`, `--surface-soft` — translucent panel tints; `--surface-solid` is the opaque base the drawer, dialogs and preview sheet mix with.
- `--glass-edge`, `--glass-edge-soft`, `--glass-sheen` — the inner highlight and top-light gradient that make a surface look like glass.
- `--glass-blur`, `--glass-sat` — the frosted blur and colour boost; the header, menus, board columns, drawer and dialogs blur harder than plain panels.
- `--glow-1` … `--glow-4` — the colour orbs drifting behind everything.
- `--scrim` — the dimmed, blurred layer behind the drawer and dialogs.
- `--shadow-sm`, `--shadow-md`, `--shadow-lg` — the three shadow depths.

The backdrop drift and the goal confetti respect `prefers-reduced-motion`, and browsers without `backdrop-filter` still get readable translucent tints instead of flat colour.

## Data and backups

- Everything lives in `data/tracker.db` (SQLite, WAL mode); attachments in `data/uploads/` — one folder per job (`<job id>/`) and one per resume with files (`resume-<id>/`).
- **Automatic snapshots** — the first time the server starts each day it copies the database to `data/backups/tracker-YYYY-MM-DD.db` (SQLite's own backup, so it is consistent even while the app is open) and keeps the newest 14 copies. A day with no backup yet is added on the next start.
- **Download a backup** — **Settings → Backup → Download backup (.zip)** saves `tracker.db` together with every uploaded document and resume file in one zip, ready to keep somewhere else. The zip is built from a fresh snapshot, so it is safe to download while you work.
- Trashed jobs still occupy their row and files until permanently deleted.
- You can also copy `data/` by hand, or use **Export → Export as JSON** for a readable copy of the jobs.
- PINs are stored hashed, but the database file is not encrypted; the PIN lock guards the dashboard, not `data/tracker.db` on disk.
- Unlock tokens live in the server's memory, so restarting the server locks every PIN-protected profile again.

## Local-only API

The address and the data are yours, so the server refuses to be driven by anything else on the machine:

- **Host check** — every API call must carry a loopback `Host` header (`127.0.0.1`, `localhost`, `::1`, or the address given to `--host`). This stops a DNS-rebinding page from pretending to be the dashboard.
- **Same-origin writes** — creating, changing and deleting require an `Origin` (or `Referer`) from the same server and port. A page on another site cannot post to your tracker.
- **Session token** — `GET /api/session` hands out a random token for this run of the server. Every API call except the health probe and document downloads must send it as `X-Api-Token`; only the dashboard's own origin can read the response. Reloading picks up a fresh token automatically.
- `GET /api/health` stays open so the start screen can check whether the server is running.

If you script the API, fetch the token first and send it with your requests (see the example below).

## Tests

Two suites run against the real HTTP server with a throwaway SQLite database in a temp directory, so neither touches `data/`.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q                    # API suite
RUN_E2E=1 python3 -m pytest tests/e2e   # browser suite (needs Playwright)
```

The **API suite** covers creating and validating jobs, stage changes and their timeline events, review marks, the trash flow (soft delete, restore, permanent delete, empty trash), document upload/download/inline preview with byte ranges and content-type safety, goal settings, JSON import, profiles — creating and updating them, keeping jobs, trash, documents and goals isolated per profile, deleting a profile only from inside itself, the PIN lock (hashing, wrong PINs, unlock tokens, locking again, refusing locked data without a token, and a locked profile's documents staying out of reach of a bare URL), importing (the to-apply default, the notes a file folds in, the match estimate and its validation), resumes (the starter, source and file limits, support-file upload and download, tailored copies that inherit the master's files, and per-profile isolation) — and the local-only guards (foreign `Origin`, missing token, foreign `Host`), plus the daily backup snapshot, its pruning and the backup zip.

The **browser suite** drives Chromium with Playwright: it opens the user picker, adds an application and opens its drawer, walks the Settings menu, saves goals, downloads a real backup zip, checks that a due follow-up produces exactly one notification a day, verifies the dialog focus trap and keyboard sorting, unlocks a PIN-protected profile, previews and downloads that profile's documents, opens a locked profile's resume file and its `\includegraphics` picture, sees a preview that cannot load offer **Try again**, checks that each match number and its drawer chip are coloured by their band, that the tables carry each job's link with any pasted note trimmed off, that a job closed while it opens still closes, that the topbar pill reports the server as active, turns red when a request cannot reach it, keeps **Retry** on hand and goes green again when the server answers — and confirms the app is installable (manifest, registered service worker, cached shell, themed title bar) and that opening it offline shows the start screen, which **Check again** can leave and which steps into the dashboard by itself once the server is back.

A third, small suite drives the launchers with stand-in tools. `Start Jobby.command` gets a `python3` that crashes at once, so the restart loop, the single-browser-open rule and the three-strikes give-up are pinned. `Jobby.app`'s launch script gets a `curl` that reports the server down and then up, so opening the helper, waiting for an answer, building the window (a stand-in `swiftc` writes a stub) and showing the dashboard — and leaving a running server alone — are pinned without a real port, compiler or browser; a second run makes the compiler fail, which pins the browser fallback. The helper's own script is run to check that it finds `server.py` from inside the bundle and starts it with `--exit-when-idle`. The Windows pair, `Jobby.vbs` and `Start Jobby.bat`, cannot be run on a Mac, so they are read the way Windows reads them — CRLF, single-byte text, the port and `/api/health` shared with `server.py`, every block closed — and `cscript` runs `Jobby.vbs` for real in a Windows job on GitHub Actions, where a syntax error cannot hide.


```bash
python3 -m playwright install chromium   # once, after installing requirements-dev
```

The browser suite is skipped unless `RUN_E2E=1`, so the pre-commit hook stays fast. GitHub Actions runs the API suite, the launcher scripts on Windows and, in another job, the browser suite on every push to `main` and on pull requests via [.github/workflows/tests.yml](.github/workflows/tests.yml).

### Pre-commit hook

A hook runs the same suite before every commit and aborts the commit if anything fails. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

The hook uses `$JOBPIPE_PYTHON` if set, otherwise `.venv/bin/python`, `venv/bin/python`, then `python3`. Create the virtualenv so it has pytest:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

If the interpreter it finds has no pytest, the hook says so and lets the commit through rather than blocking on a missing tool; CI still runs the suite. Skip a single commit with `git commit --no-verify`.

## Files

```
Jobby.app               macOS app bundle: starts the server if needed, then opens the dashboard
  Contents/MacOS/Jobby   the launcher: server, then the window (or a browser tab)
  Contents/Resources/JobbyWindow.swift
                        the dashboard window (WKWebView), compiled on first launch
  Contents/Resources/JobbyServer.app
                        invisible helper app that runs the server so it outlives Jobby.app
Jobby.vbs               Windows app: starts the server unseen, opens the dashboard in its own window
Start Jobby.command     macOS double-click launcher (same thing, in a terminal window)
Start Jobby.bat         Windows double-click launcher (the same window, with a visible console)
server.py        HTTP server, JSON API, validation, SQLite schema, document storage
extract.py       Job posting extraction (JSON-LD + OpenGraph, stdlib only)
public/          Dashboard UI: index.html, styles.css, sw.js, manifest.webmanifest, icons
                 and js/ ES modules (no build step)
  public/js/     main.js (events and boot), core.js (state, dates, API client), data.js
                 (loading and mutations), overview.js, workspace.js, drawer.js, dialogs.js,
                 profiles.js (picker, PIN, settings), io.js (import/export), resume.js
                 (resume editor and LaTeX preview), a11y.js (dialog focus), reminders.js
                 (daily notification), pwa.js (service worker and offline start screen),
                 status.js (the /api/health heartbeat behind the topbar pill)
tests/           pytest suite exercising the API end to end
  tests/e2e/     Playwright suite driving the dashboard in Chromium
data/            Created on first run: tracker.db, uploads/ and backups/
.githooks/       pre-commit hook that runs the suite
.github/         CI workflows that run both suites
AGENTS.md        Working conventions for automated assistants
requirements-dev.txt  pytest and Playwright, only needed for running the tests
```

## API (if you want to script it)

```
GET    /api/applications                      list all jobs
GET    /api/applications/<id>                 one job + status events + documents
POST   /api/applications                      create; answers 409 with {"matches": [...]} when that
                                              company and role are already on the list, unless
                                              {"force": true} adds a second copy
PATCH  /api/applications/<id>                 partial update (phase, status, description, match_pct, …);
                                              match_pct is 0–100, "" clears it
                                              { "mark_reviewed": true } records a review
DELETE /api/applications/<id>                 move job to trash (soft delete)
DELETE /api/applications/<id>?permanent=1     permanently delete a trashed job + files
POST   /api/applications/<id>/restore         restore a trashed job
GET    /api/applications?trash=1              list trashed jobs
DELETE /api/trash                             permanently delete every trashed job and its files
POST   /api/applications/<id>/documents       upload { filename, kind, content_type, data (base64) };
                                              "replace": true removes that job's documents of
                                              the same kind (and their files) first
GET    /api/applications/<id>/documents/<doc> download a document
GET    /api/applications/<id>/documents/<doc>?inline=1  preview a PDF, image or text file inline
DELETE /api/applications/<id>/documents/<doc> remove a document
GET    /api/resumes                            resumes with their file counts
POST   /api/resumes                            { "name": "master.tex" } starts from a template,
                                              { "application_id": 1 } makes a tailored copy of the master
GET    /api/resumes/<id>                       one resume with its source and files
PATCH  /api/resumes/<id>                       { "source", "name", "label" }
DELETE /api/resumes/<id>                       remove the resume and its files
POST   /api/resumes/<id>/files                 upload { filename, content_type, data (base64) } —
                                              this is also how the LaTeX Editor's Save PDF keeps a
                                              compiled PDF under the name you choose
GET    /api/resumes/<id>/files/<file>          open a support file (images inline)
DELETE /api/resumes/<id>/files/<file>          remove a support file
GET    /api/files                              every file saved with your resumes, newest first,
                                              with the resume (and job) it belongs to
GET    /api/documents                          what each job already has attached, keyed by job
                                              id — the Save PDF dialog's summary line
POST   /api/files/<file>/attach                { "application_id": 1, "kind": "resume" } copies a
                                              saved file onto a job's documents; add
                                              "replace": true to remove that job's documents
                                              of the same kind (and their files) first — the
                                              response lists the ids it replaced
GET    /api/engine                             whether Compile can build a PDF, with the engine's
                                              name and path, and the state of any install
POST   /api/engine/install                     fetch Tectonic into tools/tectonic/ in the background;
                                              poll GET /api/engine until it is ready or failed
GET    /api/settings                           daily and weekly application goals
PATCH  /api/settings                           update goals, e.g. { "daily_goal": 5, "weekly_goal": 20 }
GET    /api/profiles                           every profile with its job counts and locked flag
POST   /api/profiles                           create a profile, { "name": "Freelance", … }
GET    /api/profiles/<id>                      one profile
PATCH  /api/profiles/<id>                      update name, headline, email, phone, location, links
DELETE /api/profiles/<id>                      remove the profile with its jobs, files and goals;
                                              only from inside that profile (X-Profile-Id must match)
POST   /api/profiles/<id>/pin                  { "pin": "1234" } sets or changes it (needs current_pin
                                              once set); { "remove": true, "current_pin": "…" } clears it
POST   /api/profiles/<id>/unlock               { "pin": "1234" } → { "token": "…" }
POST   /api/profiles/<id>/lock                 revoke every unlock token for the profile
POST   /api/import                            { "applications": [ … ] }; rows matching an existing
                                              job are skipped and listed under "duplicates",
                                              {"force": true} imports them anyway
POST   /api/extract                           { "url": "https://…" }
GET    /api/session                           { "token": "…" } for this run of the server
GET    /api/backup                            a zip of the database and every document
GET    /api/health                            open, no token needed
```

Every endpoint except `GET /api/profiles`, `/api/session`, `/api/backup` and `/api/health` works on one profile. Send `X-Profile-Id: <id>` to pick it; without the header the first profile is used, which is what the dashboard does before it has loaded the profile list. For a PIN-protected profile also send `X-Profile-Token` with the token from `/unlock`, otherwise the data endpoints answer `423`.

Send `X-Api-Token` from `/api/session` on every call, and an `Origin` matching the server on writes (see [Local-only API](#local-only-api)):

```bash
BASE=http://127.0.0.1:8787
TOKEN=$(curl -s $BASE/api/session | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

curl -X POST $BASE/api/applications \
  -H "X-Api-Token: $TOKEN" \
  -H "Origin: $BASE" \
  -H 'Content-Type: application/json' \
  -d '{"company":"Acme","role":"Frontend Engineer","phase":"applied","status":"applied"}'
```
