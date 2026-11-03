/* Jobby — CSV/JSON export and import.
   ES module, no build step. Open the dashboard through server.py. */

import { $, api, download, state, toast, todayISO } from "./core.js";
import { loadApps } from "./data.js";
import { askDuplicates, closeModal } from "./dialogs.js";
import { setTab } from "./workspace.js";


export function exportJSON() {
  if (!state.apps.length) {
    toast("Nothing to export yet", true);
    return;
  }
  download("job-applications-" + todayISO() + ".json", JSON.stringify(state.apps, null, 2), "application/json");
  toast("Exported " + state.apps.length + " jobs");
}


export function exportCSV() {
  if (!state.apps.length) {
    toast("Nothing to export yet", true);
    return;
  }
  const columns = [
    "id", "phase", "company", "role", "status", "applied_on", "work_mode", "location", "source",
    "salary", "match_pct", "contact_name", "contact_email", "job_url", "next_step", "next_step_on", "description", "notes", "reviewed_at",
  ];
  const cell = (value) => '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"';
  const rows = [columns.join(",")];
  const ordered = state.apps.slice().sort((a, b) => (a.id > b.id ? 1 : -1));
  ordered.forEach((app) => {
    rows.push(columns.map((column) => cell(app[column])).join(","));
  });
  download("job-applications-" + todayISO() + ".csv", rows.join("\n"), "text/csv");
  toast("Exported " + state.apps.length + " jobs");
}


export const HEADER_ALIASES = {
  company: ["company", "company_name", "employer", "organization", "organisation", "business"],
  role: ["role", "position", "job_title", "title", "job", "job_position"],
  phase: ["phase", "bucket", "workflow"],
  status: ["status", "stage", "state", "application_status"],
  applied_on: ["applied_on", "applied", "date_applied", "applied_date", "application_date", "date", "applied_at"],
  work_mode: ["work_mode", "location_type", "workplace", "workplace_type"],
  location: ["location", "city", "place", "job_location"],
  source: ["source", "channel", "job_board", "platform", "found_via"],
  salary: ["salary", "compensation", "pay", "salary_range"],
  job_url: ["job_url", "url", "link", "job_link", "posting_url", "posting", "job_posting"],
  contact_name: ["contact_name", "contact", "recruiter", "hiring_manager"],
  contact_email: ["contact_email", "email", "recruiter_email"],
  next_step: ["next_step", "next_action", "next"],
  next_step_on: ["next_step_on", "next_step_date", "follow_up", "follow_up_date", "followup_date", "reminder"],
  description: ["description", "job_description", "jd", "role_description"],
  match_pct: [
    "match_pct", "match", "match_percentage", "match_score", "estimated_match_pct", "estimated_match",
    "resume_match", "resume_match_pct", "fit_pct", "match_pct_estimate",
  ],
  notes: ["notes", "note", "comments", "details", "personal_notes"],
};

// Analysis some files carry beside a posting; the server folds these into notes.
export const NOTE_ALIASES = ["why_it_matches", "important_missing_requirements", "missing_requirements", "gaps"];


export function normalizeHeader(value) {
  return String(value == null ? "" : value)
    .replace(/^\uFEFF/, "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_")
    .replace(/[^a-z0-9_]/g, "");
}


export function canonicalKey(header) {
  const normalized = normalizeHeader(header);
  for (const target of Object.keys(HEADER_ALIASES)) {
    if (HEADER_ALIASES[target].includes(normalized)) return target;
  }
  return normalized;
}


export function parseCSV(text) {
  const rows = [];
  let row = [];
  let value = "";
  let inQuotes = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (inQuotes) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          value += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        value += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === "," || char === ";") {
      row.push(value);
      value = "";
    } else if (char === "\n") {
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else if (char !== "\r") {
      value += char;
    }
  }
  if (value !== "" || row.length) {
    row.push(value);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((cell) => cell.trim() !== ""));
}


export function normalizeStatusValue(value) {
  const text = String(value == null ? "" : value).trim().toLowerCase();
  if (!text) return "applied";
  if (/withdraw|cancel/.test(text)) return "withdrawn";
  if (/offer|hired|accept/.test(text)) return "offer";
  if (/interview|panel|onsite/.test(text)) return "interview";
  if (/screen|phone|recruiter_call/.test(text)) return "screening";
  if (/reject|declin|denied|ghost/.test(text)) return "rejected";
  return "applied";
}


export function normalizePhaseValue(value) {
  const text = String(value == null ? "" : value).trim().toLowerCase();
  if (!text) return "";
  if (/to.?apply|saved|wishlist|backlog|not.?applied|todo|to.?send|shortlist/.test(text)) return "to_apply";
  if (/applied|sent|submitted|done/.test(text)) return "applied";
  return "";
}


export function normalizeWorkMode(value) {
  const text = String(value == null ? "" : value).trim().toLowerCase();
  if (/remote|telecommut|anywhere/.test(text)) return "remote";
  if (/hybrid|flex/.test(text)) return "hybrid";
  return "onsite";
}


export function normalizeDate(value) {
  const text = String(value == null ? "" : value).trim();
  if (!text) return "";
  let match = text.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
  if (match) {
    return match[1] + "-" + match[2].padStart(2, "0") + "-" + match[3].padStart(2, "0");
  }
  match = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$/);
  if (match) {
    const first = Number(match[1]);
    const month = first > 12 ? match[2] : match[1];
    const day = first > 12 ? match[1] : match[2];
    let year = Number(match[3]);
    if (year < 100) year += 2000;
    return year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
  }
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) {
    return [
      parsed.getFullYear(),
      String(parsed.getMonth() + 1).padStart(2, "0"),
      String(parsed.getDate()).padStart(2, "0"),
    ].join("-");
  }
  return "";
}


export function normalizeMatchPct(value) {
  const text = String(value == null ? "" : value).replace(/%/g, "").trim();
  const found = text.match(/\d+(?:\.\d+)?/);
  if (!found) return "";
  const number = Math.round(Number(found[0]));
  if (!Number.isFinite(number)) return "";
  return String(Math.max(0, Math.min(100, number)));
}


export function cleanImportedRow(row) {
  const cleaned = {};
  Object.keys(row).forEach((key) => {
    let value = row[key];
    if (value == null) return;
    if (Array.isArray(value)) value = value.join("\n- ");
    else if (isObject(value)) return;
    value = String(value).trim();
    if (!value) return;
    if (key === "match_pct") {
      value = normalizeMatchPct(value);
      if (!value) return;
    }
    if (key === "status") value = normalizeStatusValue(value);
    if (key === "phase") {
      value = normalizePhaseValue(value);
      if (!value) return;
    }
    if (key === "work_mode") value = normalizeWorkMode(value);
    if (key === "applied_on" || key === "next_step_on") {
      value = normalizeDate(value);
      if (!value) return;
    }
    cleaned[key] = value;
  });
  if (cleaned.phase === "to_apply") delete cleaned.applied_on;
  return cleaned;
}


export function rowsFromCSV(text) {
  const table = parseCSV(String(text).replace(/^\uFEFF/, ""));
  if (table.length < 2) return { rows: [], skipped: 0 };
  const headers = table[0].map(canonicalKey);
  const rows = table.slice(1).map((cells) => {
    const row = {};
    headers.forEach((key, index) => {
      if (!key) return;
      row[key] = cells[index] == null ? "" : cells[index];
    });
    return cleanImportedRow(row);
  });
  const valid = rows.filter((row) => row.company && row.role);
  return { rows: valid, skipped: rows.length - valid.length };
}


// The keys a saved file may keep its list of jobs under, besides a bare array.
export const LIST_KEYS = ["applications", "jobs", "items", "data", "results", "rows"];


function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}


function parseLines(lines) {
  const items = [];
  for (const line of lines) {
    try {
      items.push(JSON.parse(line));
    } catch (error) {
      return null;
    }
  }
  return items;
}


function jobsIn(parsed) {
  if (Array.isArray(parsed)) return parsed;
  if (!isObject(parsed)) return null;
  const wrapper = LIST_KEYS.find((key) => Array.isArray(parsed[key]));
  if (wrapper) return parsed[wrapper];
  // One posting on its own is still a file worth importing.
  if (parsed.company || parsed.role) return [parsed];
  return null;
}


export function rowsFromJSON(text) {
  const source = String(text == null ? "" : text).replace(/^\uFEFF/, "").trim();
  if (!source) throw new Error("That file is empty.");
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (error) {
    // A file of one posting per line is JSON too, just not one document: try it
    // that way before reporting the file as unreadable.
    const lines = source.split(/\r?\n/).filter((line) => line.trim());
    const lineItems = lines.length > 1 ? parseLines(lines) : null;
    if (!lineItems) {
      throw new Error(
        "That file is not valid JSON: " + (error.message || "it could not be read") + "."
      );
    }
    parsed = lineItems;
  }
  const list = jobsIn(parsed);
  if (!list) {
    throw new Error(
      "That JSON file has no list of jobs in it. Send an array of jobs, or an object that " +
        'holds them under "applications" — docs/examples/jobs.example.json shows the shape.'
    );
  }
  const rows = [];
  let skipped = 0;
  list.forEach((item) => {
    if (!isObject(item)) {
      skipped += 1;
      return;
    }
    const row = {};
    Object.keys(item).forEach((key) => {
      row[canonicalKey(key)] = item[key];
    });
    const cleaned = cleanImportedRow(row);
    if (!cleaned.company || !cleaned.role) {
      skipped += 1;
      return;
    }
    rows.push(cleaned);
  });
  return { rows, skipped };
}


/* The same rule the server uses: casing and spacing do not make a new job. */
export function jobKey(company, role) {
  const squash = (value) =>
    String(value == null ? "" : value).trim().replace(/\s+/g, " ").toLowerCase();
  return squash(company) + "\u0000" + squash(role);
}


export async function importRows(rows, options) {
  const force = Boolean(options && options.force);
  const result = await api("/api/import", {
    method: "POST",
    body: JSON.stringify({ applications: rows, force: force }),
  });
  await loadApps();
  return result;
}


export function setPanelNote(selector, message, tone) {
  const element = $(selector);
  if (!element) return;
  element.hidden = !message;
  element.textContent = message || "";
  element.classList.toggle("is-error", tone === "error");
  element.classList.toggle("is-ok", tone === "ok");
}


export async function handleImportFile(file, options) {
  if (!file) return;
  const settings = options || {};
  const status = settings.statusSelector ? (message, tone) => setPanelNote(settings.statusSelector, message, tone) : null;
  try {
    const text = await file.text();
    const isCSV = /\.csv$/i.test(file.name) || /csv/i.test(file.type);
    const parsed = isCSV ? rowsFromCSV(text) : rowsFromJSON(text);
    if (!parsed.rows.length) {
      throw new Error(
        isCSV
          ? "No jobs found. A CSV needs company and role columns."
          : "No jobs found. Every job in a JSON file needs a company and a role."
      );
    }
    // Postings already on the list are named before anything is written, so a
    // second import of the same file is a decision rather than a pile of copies.
    const known = new Map();
    state.apps.forEach((app) => known.set(jobKey(app.company, app.role), app));
    let rows = parsed.rows;
    let force = false;
    const duplicates = rows.filter((row) => known.has(jobKey(row.company, row.role)));
    if (duplicates.length) {
      const listed = new Set();
      const matches = [];
      duplicates.forEach((row) => {
        const app = known.get(jobKey(row.company, row.role));
        if (listed.has(app.id)) return;
        listed.add(app.id);
        matches.push(app);
      });
      const choice = await askDuplicates({
        title: "Some jobs are already on your list",
        text:
          duplicates.length + " of " + rows.length + " row" + (rows.length === 1 ? "" : "s") +
          " match a job you already have. Skip them, or import everything anyway.",
        matches: matches,
        skipLabel: "Skip them",
        addLabel: "Import anyway",
      });
      if (!choice) {
        if (status) status("Import cancelled.", "");
        return;
      }
      if (choice === "skip") {
        rows = rows.filter((row) => !known.has(jobKey(row.company, row.role)));
      } else {
        force = true;
      }
    }
    if (!rows.length) {
      const message = "Every row is already on your list — nothing to import.";
      if (status) status(message, "ok");
      else toast(message);
      return;
    }
    if (status) status("Importing " + rows.length + " job(s)…", "ok");
    const result = await importRows(rows, { force: force });
    const imported = Number(result.imported) || 0;
    const skipped = parsed.skipped + (Number(result.skipped) || 0);
    // A row that names no status was never applied to; the server saves it to apply.
    const savedRows = Math.min(
      rows.filter((row) => !row.phase && !row.status).length,
      imported
    );
    const sentRows = imported - savedRows;
    const reasons = (result.problems || []).map((problem) => problem.reason).filter(Boolean);
    if (parsed.skipped) reasons.push("a row has no company or role");
    const shownReasons = Array.from(new Set(reasons)).slice(0, 2);
    const summary =
      "Imported " + imported + " job" + (imported === 1 ? "" : "s") +
      (savedRows ? " — " + savedRows + " saved to apply" : "") +
      (sentRows ? (savedRows ? " and " : " — ") + sentRows + " marked as applied" : "") +
      (skipped
        ? ", skipped " + skipped + " row" + (skipped === 1 ? "" : "s") +
          (shownReasons.length ? ": " + shownReasons.join("; ") : "")
        : "") +
      ".";
    toast(summary);
    if (status) status(summary, "ok");
    if (settings.closeModal) closeModal();
    // Show the list the import actually filled, rather than leaving the user on
    // whichever tab they happened to open the dialog from.
    if (imported && !sentRows) setTab("to_apply");
    else if (imported && !savedRows) setTab("applied");
  } catch (error) {
    const message = error.message || "Import failed";
    if (status) status(message, "error");
    else toast(message, true);
  }
}
