/* Jobby — Shared state, formatting, dates, the API client and small helpers.
   ES module, no build step. Open the dashboard through server.py. */

import { openUnlock } from "./profiles.js";


export const STATUSES = [
  { key: "applied", label: "Applied" },
  { key: "screening", label: "Screening" },
  { key: "interview", label: "Interview" },
  { key: "offer", label: "Offer" },
  { key: "rejected", label: "Rejected" },
  { key: "withdrawn", label: "Withdrawn" },
];


export const STATUS_LABEL = STATUSES.reduce((acc, item) => {
  acc[item.key] = item.label;
  return acc;
}, {});


export const MODE_LABEL = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };


export const DOC_LABEL = { resume: "Resume", cover_letter: "Cover letter", other: "Other" };


export const TABS = ["overview", "to_apply", "applied", "resume", "trash"];


export const CLOSED = ["rejected", "withdrawn"];


export const ACTIVE_STATUSES = ["applied", "screening", "interview"];


export const REVIEW_AFTER_DAYS = 30;


export const MAX_UPLOAD = 10 * 1024 * 1024;


export const DEFAULT_GOALS = { daily_goal: 3, weekly_goal: 10 };


export const DAY_NAMES = ["S", "M", "T", "W", "T", "F", "S"];


export const ICON = {
  pencil: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20h4l10-10a2.8 2.8 0 10-4-4L4 16v4z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.5l4.5 4.5L19 6.5"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5 19V9M12 19V5M19 19v-6"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="10" width="14" height="10" rx="2.4"/><path d="M8.5 10V7.5a3.5 3.5 0 017 0V10"/></svg>',
  unlock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="10" width="14" height="10" rx="2.4"/><path d="M8.5 10V7.5a3.5 3.5 0 016.4-2"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 4h12v17l-6-4.5L6 21V4z"/></svg>',
  restore: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 10a8 8 0 111.6 7.5"/><path d="M4 4v6h6"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 3h8l4 4v14H6V3z"/><path d="M14 3v4h4"/></svg>',
  attach: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M8 12.5V8a4 4 0 118 0v8a6 6 0 11-12 0v-6"/></svg>',
  flame: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 3c3 3.4 5 6.2 5 9a5 5 0 01-10 0c0-1.9.8-3.4 1.8-4.6.2 1.8 1.2 2.6 1.2 2.6S10.5 6.6 12 3z"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5.5a.5.5 0 01-.5.5h-13a.5.5 0 01-.5-.5v-13a.5.5 0 01.5-.5H10"/></svg>',
};


export const state = {
  apps: [],
  trash: [],
  tab: "overview",
  view: localStorage.getItem("jobpipe:view") === "board" ? "board" : "table",
  sort: {
    applied: { key: "applied_on", dir: "desc" },
    to_apply: { key: "created_at", dir: "desc" },
  },
  filters: { q: "", status: "", mode: "", source: "", match: "" },
  modalMode: "manual",
  drawerId: null,
  drawerEvents: [],
  drawerDocuments: [],
  editingId: null,
  selection: new Set(),
  previewDoc: null,
  previewUrl: null,
  goalProgress: null,
  settings: Object.assign({}, DEFAULT_GOALS),
  sent: null,
  profiles: [],
  profileId: null,
  unlockId: null,
  gate: false,
  settingsView: "menu",
  loading: true,
  // Kept up to date by js/status.js from the /api/health heartbeat; true until
  // the first answer arrives.
  serverOnline: true,
};


export const $ = (selector, scope) => (scope || document).querySelector(selector);


export const $$ = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));


export function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


export function parseDateOnly(iso) {
  if (!iso) return null;
  const parts = String(iso).slice(0, 10).split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}


export function todayISO() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}


export function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}


export function isoOf(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}


export function startOfWeek(date) {
  const day = startOfDay(date);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day;
}


/* Applications sent, per day. The server keeps this list, so deleting a job or
   moving it to another stage cannot make a past application look unsent. Until
   that list has arrived, the jobs on screen are the best answer there is. */
export function sentByDay() {
  const counts = new Map();
  if (state.sent && state.sent.days) {
    Object.keys(state.sent.days).forEach((day) => {
      const count = Number(state.sent.days[day]) || 0;
      if (count > 0) counts.set(day, count);
    });
    return counts;
  }
  state.apps.forEach((app) => {
    if (app.phase === "to_apply" || !app.applied_on) return;
    const day = String(app.applied_on).slice(0, 10);
    counts.set(day, (counts.get(day) || 0) + 1);
  });
  return counts;
}


export function sentTotal() {
  if (state.sent && state.sent.days) return Number(state.sent.total) || 0;
  let total = 0;
  sentByDay().forEach((count) => {
    total += count;
  });
  return total;
}


export function appliedDays() {
  return new Set(sentByDay().keys());
}


export function bestStreak(days) {
  let best = 0;
  let run = 0;
  let previous = null;
  Array.from(days)
    .sort()
    .forEach((iso) => {
      if (previous) {
        const next = parseDateOnly(previous);
        next.setDate(next.getDate() + 1);
        run = isoOf(next) === iso ? run + 1 : 1;
      } else {
        run = 1;
      }
      best = Math.max(best, run);
      previous = iso;
    });
  return best;
}


export function currentStreak(days) {
  const cursor = startOfDay(new Date());
  if (!days.has(isoOf(cursor))) {
    cursor.setDate(cursor.getDate() - 1);
    if (!days.has(isoOf(cursor))) return 0;
  }
  let count = 0;
  while (days.has(isoOf(cursor))) {
    count += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return count;
}


export function dayDiff(iso) {
  const date = parseDateOnly(iso);
  if (!date) return null;
  return Math.round((startOfDay(date) - startOfDay(new Date())) / 86400000);
}


export function fmtDate(iso) {
  const date = parseDateOnly(iso);
  if (!date) return "—";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}


export function fmtShortDate(date) {
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}


export function relativeDays(iso) {
  const diff = dayDiff(iso);
  if (diff == null) return "";
  if (diff === 0) return "today";
  if (diff === -1) return "yesterday";
  if (diff < 0) {
    const days = Math.abs(diff);
    if (days < 7) return days + " days ago";
    if (days < 30) return Math.round(days / 7) + "w ago";
    if (days < 365) return Math.round(days / 30) + "mo ago";
    return Math.round(days / 365) + "y ago";
  }
  if (diff === 1) return "tomorrow";
  if (diff < 7) return "in " + diff + " days";
  if (diff < 30) return "in " + Math.round(diff / 7) + " weeks";
  return "in " + Math.round(diff / 30) + " months";
}


export function dueLabel(iso) {
  const diff = dayDiff(iso);
  if (diff == null) return "";
  if (diff === 0) return "Due today";
  if (diff < 0) return "Overdue by " + Math.abs(diff) + "d";
  if (diff === 1) return "Due tomorrow";
  return "Due in " + diff + "d";
}


export function isOverdue(iso) {
  const diff = dayDiff(iso);
  return diff != null && diff < 0;
}


export function daysSince(iso) {
  const diff = dayDiff(iso);
  return diff == null ? null : -diff;
}


export function needsReview(app) {
  if (app.phase === "to_apply") return false;
  if (!ACTIVE_STATUSES.includes(app.status)) return false;
  const sinceApplied = daysSince(app.applied_on);
  if (sinceApplied == null || sinceApplied < REVIEW_AFTER_DAYS) return false;
  if (!app.reviewed_at) return true;
  const sinceReview = daysSince(app.reviewed_at);
  return sinceReview == null || sinceReview >= REVIEW_AFTER_DAYS;
}


export function matchPct(app) {
  const value = app ? app.match_pct : null;
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, Math.round(number)));
}


/**
 * The address part of a saved job URL, or "" when the value is not one. People
 * paste notes after the address ("…/job (posted 2 days ago; apply on their
 * site)"), and a URL never contains a space, so the link is the first token.
 */
export function jobHref(value) {
  const raw = String(value || "").trim();
  if (!/^https?:\/\//i.test(raw)) return "";
  return raw.split(/\s+/)[0];
}


/**
 * Which colour band a match estimate belongs to. The filter dropdown cuts the
 * same way (80+, 60–79, 40–59, below 40), so the colour of a number and the
 * band that hides it always agree.
 */
export function matchBand(pct) {
  if (pct === null || pct === undefined) return "";
  if (pct >= 80) return "high";
  if (pct >= 60) return "good";
  if (pct >= 40) return "fair";
  return "low";
}


/** The saved estimate of how well this posting matches the resume, if any. */
export function matchBadge(app, extraClass) {
  const pct = matchPct(app);
  if (pct === null) return "";
  const band = matchBand(pct);
  return (
    '<span class="match-badge' + (band ? " is-" + band : "") +
    (extraClass ? " " + extraClass : "") +
    '" title="Estimated match with your resume">' + pct + "% match</span>"
  );
}


export function formatSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}


export function formatTimestamp(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return (
    date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) +
    ", " +
    date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  );
}


export function statusSelectOptions(selected, includeAll) {
  const options = includeAll ? ['<option value="">All stages</option>'] : [];
  STATUSES.forEach((status) => {
    options.push(
      '<option value="' + status.key + '"' + (status.key === selected ? " selected" : "") + ">" +
        status.label +
        "</option>"
    );
  });
  return options.join("");
}


export function emptyState(config) {
  return (
    '<div class="empty">' +
    '<div class="empty-icon">' + config.icon + "</div>" +
    "<h3>" + esc(config.title) + "</h3>" +
    "<p>" + esc(config.text) + "</p>" +
    (config.action
      ? '<button class="btn ' + (config.primary ? "btn-primary" : "") + '" data-action="' +
        config.action + '">' + esc(config.actionLabel) + "</button>"
      : "") +
    "</div>"
  );
}


export function isNetworkError(error) {
  return error instanceof TypeError || /load failed|failed to fetch|network ?error/i.test((error && error.message) || "");
}


export const TOKEN_KEY = "jobpipe:tokens";


export function readTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(TOKEN_KEY)) || {};
  } catch (error) {
    return {};
  }
}


export function tokenFor(profileId) {
  return readTokens()[String(profileId)] || "";
}


export function setToken(profileId, token) {
  const tokens = readTokens();
  if (token) tokens[String(profileId)] = token;
  else delete tokens[String(profileId)];
  try {
    sessionStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
  } catch (error) {
    /* private mode with storage disabled: the profile just asks again */
  }
}


export function clearToken(profileId) {
  setToken(profileId, "");
}


export let apiToken = "";


/* The topbar indicator (js/status.js) listens for these, so a request that
   cannot reach the server turns it red at once instead of waiting for the next
   heartbeat. Only the transition is announced; a response of any status means
   the server is there. */
function noteServerUnreachable() {
  document.dispatchEvent(new Event("jobpipe:server-error"));
}


function noteServerReachable() {
  if (state.serverOnline === false) document.dispatchEvent(new Event("jobpipe:server-ok"));
}


export async function sessionToken() {
  if (apiToken) return apiToken;
  let response;
  try {
    response = await fetch("/api/session", { cache: "no-store" });
  } catch (error) {
    // A stopped server reaches us here before any request is made.
    noteServerUnreachable();
    const offline = new Error(
      "Can't reach the tracker server. Check that it is still running (python3 server.py), then try again."
    );
    offline.offline = true;
    throw offline;
  }
  noteServerReachable();
  if (!response.ok) throw new Error("The tracker refused this session. Reload the dashboard.");
  const payload = await response.json();
  apiToken = payload.token || "";
  return apiToken;
}


export async function api(path, options) {
  try {
    return await request(path, options);
  } catch (error) {
    // The server was restarted and now expects a fresh token: fetch one and
    // try once more. Nothing was applied, so retrying cannot duplicate work.
    if (error.status === 403 && /session expired/i.test(error.message)) {
      apiToken = "";
      return request(path, options);
    }
    throw error;
  }
}


/* What every request to the tracker needs: the session token, and the unlock
   token when the profile is PIN protected. */
export async function authHeaders(path) {
  const headers = { "X-Api-Token": await sessionToken() };
  // Listing profiles is not scoped to one of them; everything else is.
  if (state.profileId && path !== "/api/profiles") {
    headers["X-Profile-Id"] = String(state.profileId);
    const token = tokenFor(state.profileId);
    if (token) headers["X-Profile-Token"] = token;
  }
  return headers;
}


/* A document or resume file as bytes.

   A browser only adds these headers to a fetch: an <img>, an <iframe> or a
   download link sends none of them, so for a PIN-protected profile anything
   opened that way came back as "This profile is locked". Files are fetched here
   instead and shown from a blob URL. */
export async function fetchFile(path) {
  let response;
  try {
    response = await fetch(path, { headers: await authHeaders(path), cache: "no-store" });
  } catch (error) {
    noteServerUnreachable();
    throw new Error("Can't reach the tracker server. Check that it is still running, then try again.");
  }
  noteServerReachable();
  if (!response.ok) {
    let message = "This file could not be loaded (" + response.status + ")";
    try {
      const payload = await response.json();
      if (payload && payload.error) message = payload.error;
    } catch (error) {
      // Not a JSON error body: the generic message above is the honest one.
    }
    if (response.status === 423 && state.profileId) {
      clearToken(state.profileId);
      openUnlock(state.profileId);
    }
    const failure = new Error(message);
    failure.status = response.status;
    throw failure;
  }
  return response.blob();
}


export async function request(path, options) {
  const init = Object.assign({ headers: { "Content-Type": "application/json" } }, options);
  init.headers = Object.assign({}, init.headers, await authHeaders(path));
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    // A laptop waking from sleep or a restarted server leaves a stale
    // keep-alive socket behind. One fresh attempt usually gets through.
    // POST is left alone so a retry can never create a duplicate.
    const canRetry = (init.method || "GET").toUpperCase() !== "POST";
    if (!canRetry || !isNetworkError(error)) throw error;
    await new Promise((resolve) => setTimeout(resolve, 150));
    try {
      response = await fetch(path, init);
    } catch (retryError) {
      noteServerUnreachable();
      const offline = new Error(
        window.location.protocol === "file:"
          ? "Open the dashboard from http://127.0.0.1:8787 (run python3 server.py) instead of opening the HTML file directly."
          : "Can't reach the tracker server. Check that it is still running (python3 server.py), then try again."
      );
      // Lets the boot path show the start screen instead of an empty picker.
      offline.offline = true;
      throw offline;
    }
  }
  noteServerReachable();
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = null;
    }
  }
  if (!response.ok) {
    const message = payload && payload.error ? payload.error : "Request failed (" + response.status + ")";
    const error = new Error(message);
    error.status = response.status;
    // Structured detail some failures carry: the jobs a duplicate add matched,
    // for instance, so the form can offer to open one instead of guessing.
    if (payload) error.data = payload;
    // A failed compile carries the TeX log so the editor can show it.
    if (payload && typeof payload.log === "string") error.log = payload.log;
    // The profile locked while this tab was open (or its token expired).
    if (response.status === 423 && state.profileId && path !== "/api/profiles") {
      clearToken(state.profileId);
      openUnlock(state.profileId);
    }
    throw error;
  }
  return payload;
}


export function toast(message, isError, variant, action) {
  const container = $("#toasts");
  const element = document.createElement("div");
  element.className = "toast" + (isError ? " is-error" : "") + (variant ? " is-" + variant : "");
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  element.appendChild(text);
  let timer = null;
  const dismiss = () => {
    window.clearTimeout(timer);
    if (element.classList.contains("is-leaving")) return;
    element.classList.add("is-leaving");
    window.setTimeout(() => element.remove(), 200);
  };
  if (action && action.onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label || "Undo";
    button.addEventListener("click", async () => {
      button.disabled = true;
      dismiss();
      try {
        await action.onClick();
      } catch (error) {
        toast(error.message || "Could not undo that", true);
      }
    });
    element.appendChild(button);
  }
  container.appendChild(element);
  while (container.children.length > 3) container.firstElementChild.remove();
  // An offer to undo needs to be readable and reachable before it disappears.
  timer = window.setTimeout(dismiss, action ? 8000 : 2600);
}


export function download(filename, content, type) {
  const blob = new Blob([content], { type: type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
