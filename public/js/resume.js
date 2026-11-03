/* Jobby — The resume editor: LaTeX source on the left of the split,
   a rendered page on the right, and the profile's resumes in the rail.
   ES module, no build step. Open the dashboard through server.py. */

import { $, $$, DOC_LABEL, MAX_UPLOAD, api, esc, fetchFile, fmtDate, formatSize, state, toast } from "./core.js";
import { openDrawer, openPreview } from "./drawer.js";
import { savePdfDialog } from "./dialogs.js";


const rs = {
  profileId: undefined,
  list: [],
  docs: new Map(),
  tabs: [],
  activeId: null,
  loaded: false,
  loading: false,
  renderedId: null,
  draft: "",
  zoom: Number(localStorage.getItem("jobpipe:resume-zoom")) || 1,
  saveTimer: null,
  saving: false,
  dirty: false,
  confirmingDelete: false,
  previewTimer: null,
  issueCount: 0,
  // The last compile error, kept while it still describes the source on screen.
  compileLog: null,
  // The last real PDF from a LaTeX engine, while it still matches the source.
  pdf: null,
};


/* ---------------------------------------------------------------- loading */

async function loadList() {
  if (rs.loading) return;
  rs.loading = true;
  renderRail();
  try {
    const list = await api("/api/resumes");
    rs.list = Array.isArray(list) ? list : [];
    rs.loaded = true;
    // Reopen the first resume so the editor is never needlessly empty.
    if (!rs.activeId && rs.list.length) {
      const first = rs.list.find((item) => !item.application_id) || rs.list[0];
      await openResume(first.id, { render: false });
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    rs.loading = false;
    renderRail();
    renderTabsRow();
    renderEditor();
  }
}


export function resetResumeState() {
  rs.profileId = undefined;
  rs.list = [];
  rs.docs.clear();
  rs.tabs = [];
  rs.activeId = null;
  rs.loaded = false;
  rs.loading = false;
  rs.renderedId = null;
  rs.draft = "";
  rs.compileLog = null;
  clearPdf();
}


export async function openResume(id, options) {
  const settings = options || {};
  try {
    if (!rs.docs.has(id)) {
      const doc = await api("/api/resumes/" + id);
      rs.docs.set(id, doc);
      const index = rs.list.findIndex((item) => item.id === id);
      rs.list = rs.list.map((item) => (item.id === id ? Object.assign({}, item, { size: doc.size }) : item));
      if (index === -1) rs.list.push(doc);
    }
    rs.activeId = id;
    rs.renderedId = null;
    rs.compileLog = null;
    if (!rs.tabs.includes(id)) rs.tabs.push(id);
    rs.confirmingDelete = false;
    if (settings.render !== false) {
      renderRail();
      renderTabsRow();
      renderEditor();
    }
  } catch (error) {
    toast(error.message, true);
  }
}


function activeDoc() {
  return rs.docs.get(rs.activeId) || null;
}


/* ------------------------------------------------------------- the panel */

export function renderResumePanel() {
  if (state.tab !== "resume") return;
  const panel = $("#panel-resume");
  if (!panel) return;
  if (rs.profileId !== state.profileId) {
    const profileChanged = rs.profileId !== undefined;
    rs.profileId = state.profileId;
    if (profileChanged) resetResumeState();
    rs.profileId = state.profileId;
    rs.loaded = false;
    loadList();
  }
  renderRail();
  renderTabsRow();
  renderEditor();
}


function imageMap(doc) {
  const map = {};
  const files = doc && Array.isArray(doc.files) ? doc.files : [];
  files.forEach((file) => {
    map[file.filename.toLowerCase()] = {
      id: file.id,
      url: new URL(file.url, window.location.origin).href,
      content_type: file.content_type || "",
    };
  });
  return map;
}


/* Pictures used by \includegraphics.

   An <img> sends no headers, so pointing it straight at the file answered
   "This profile is locked" on a PIN-protected profile. The bytes are fetched
   with the tokens instead and swapped in as a blob URL, cached per file so
   typing does not re-download the same picture on every render. */
const IMAGE_URLS = new Map();

async function hydrateResumeImages() {
  const doc = activeDoc();
  const images = $$("#resume-preview-page img[data-resume-image]");
  await Promise.all(
    images.map(async (img) => {
      const url = await resumeImageUrl(doc, Number(img.dataset.resumeImage));
      // The page may have re-rendered, or the resume changed, while the bytes
      // were in flight: a detached node must not be written to.
      if (url && img.isConnected) img.src = url;
    })
  );
}


async function resumeImageUrl(doc, fileId) {
  if (IMAGE_URLS.has(fileId)) return IMAGE_URLS.get(fileId);
  const file = doc ? (doc.files || []).find((item) => item.id === fileId) : null;
  if (!file) return "";
  let url = "";
  try {
    url = URL.createObjectURL(await fetchFile(file.url));
  } catch (error) {
    // The dashed placeholder already says the picture is not usable.
    return "";
  }
  IMAGE_URLS.set(fileId, url);
  return url;
}


function releaseResumeImage(fileId) {
  if (!IMAGE_URLS.has(fileId)) return;
  URL.revokeObjectURL(IMAGE_URLS.get(fileId));
  IMAGE_URLS.delete(fileId);
}


function releaseResumeImages() {
  IMAGE_URLS.forEach((url) => URL.revokeObjectURL(url));
  IMAGE_URLS.clear();
}


function renderAssets() {
  const box = $("#resume-assets");
  if (!box) return;
  const doc = activeDoc();
  if (!doc) {
    box.innerHTML = "";
    return;
  }
  const files = Array.isArray(doc.files) ? doc.files : [];
  box.innerHTML =
    '<div class="resume-assets-head"><h3>Files</h3><span class="resume-assets-count">' +
    files.length + "</span></div>" +
    (files.length
      ? '<ul class="resume-assets-list">' +
        files
          .map((file) => {
            const image = /^image\//.test(file.content_type || "");
            return (
              '<li class="resume-asset">' +
              // A button, not a link: the bytes need the session and unlock
              // headers, which a link or a new tab can never send.
              '<button type="button" class="resume-asset-name" data-preview-file="' + file.id + '"' +
              ' title="Preview ' + esc(file.filename) + '">' +
              '<span class="resume-asset-icon">' + (image ? IMAGE_ICON : FILE_ICON) + "</span>" +
              '<span class="resume-asset-label">' + esc(file.filename) + "</span>" +
              "</button>" +
              '<span class="resume-asset-size">' + esc(formatSize(file.size)) + "</span>" +
              '<button type="button" class="resume-asset-remove" data-resume-file="' + file.id + '"' +
              ' aria-label="Remove ' + esc(file.filename) + '" title="Remove this file">×</button>' +
              "</li>"
            );
          })
          .join("") +
        "</ul>"
      : '<p class="resume-rail-empty">Nothing here yet. Upload a photo, a bibliography or a note to use beside the <code>.tex</code>.</p>') +
    '<label class="resume-upload">' + UPLOAD_ICON + 'Upload files<input type="file" id="resume-file-input" multiple hidden /></label>';
}


/* A resume's own file opens in the same panel as a job document: it is fetched
   with the session and unlock tokens and shown from a blob URL, so a support
   file is readable on a PIN-protected profile instead of "profile is locked". */
function previewResumeFile(fileId) {
  const doc = activeDoc();
  const file = doc ? (doc.files || []).find((item) => item.id === fileId) : null;
  if (file) openPreview(file);
}


/* Save PDF only means something while a freshly compiled PDF is on screen. */
function renderSavePdfButton() {
  const button = $("#resume-save-pdf");
  if (!button) return;
  const ready = Boolean(rs.pdf && rs.pdf.blob && rs.pdf.docId === rs.activeId);
  button.disabled = !ready;
  button.title = ready
    ? "Keep this PDF with the resume's files, under a name you choose"
    : "Compile first, then the PDF can be saved with this resume's files";
}


function renderRail() {
  const tree = $("#resume-tree");
  if (!tree) return;
  if (!rs.loaded) {
    tree.innerHTML = '<p class="resume-rail-empty">Loading…</p>';
    return;
  }
  if (!rs.list.length) {
    tree.innerHTML =
      '<p class="resume-rail-empty">No resumes yet. Start with a template built from your profile, then edit it here.</p>' +
      '<button type="button" class="btn btn-primary resume-start" data-resume-action="new">Start from a template</button>';
    return;
  }
  const masters = rs.list.filter((item) => !item.application_id);
  const tailored = rs.list.filter((item) => item.application_id);
  const row = (item) => {
    const active = item.id === rs.activeId;
    const sub = item.application_id
      ? esc(item.job_company || "Job") + (item.job_role ? " · " + esc(item.job_role) : "")
      : "Edited " + esc(fmtDate(item.updated_at));
    return (
      '<button type="button" class="resume-file' + (active ? " is-active" : "") + '"' +
      ' data-resume-id="' + item.id + '" aria-current="' + (active ? "true" : "false") + '">' +
      '<span class="resume-file-icon">' + FILE_ICON + "</span>" +
      '<span class="resume-file-main"><span class="resume-file-name">' + esc(item.name) + "</span>" +
      '<span class="resume-file-sub">' + sub + "</span></span>" +
      "</button>"
    );
  };

  tree.innerHTML =
    (masters.length
      ? '<div class="resume-rail-group"><h3>Master resumes</h3>' + masters.map(row).join("") + "</div>"
      : "") +
    (tailored.length
      ? '<div class="resume-rail-group"><h3>Tailored for jobs</h3>' + tailored.map(row).join("") + "</div>"
      : "") +
    (masters.length
      ? ""
      : '<div class="resume-rail-group"><h3>Master resumes</h3>' +
        '<p class="resume-rail-empty">None yet — the first job you tailor creates one.</p></div>') +
    '<button type="button" class="resume-new-link" data-resume-action="new">+ New resume</button>';
}


function renderTabsRow() {
  const bar = $("#resume-tabs");
  if (!bar) return;
  if (!rs.tabs.length) {
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = rs.tabs
    .map((id) => {
      const item = rs.list.find((entry) => entry.id === id);
      if (!item) return "";
      const active = id === rs.activeId;
      return (
        '<div class="resume-tab' + (active ? " is-active" : "") + '">' +
        '<button type="button" class="resume-tab-name" data-resume-id="' + id + '" role="tab"' +
        ' aria-selected="' + (active ? "true" : "false") + '">' + esc(item.name) + "</button>" +
        '<button type="button" class="resume-tab-close" data-resume-close="' + id + '"' +
        ' aria-label="Close ' + esc(item.name) + '">' + CLOSE_ICON + "</button>" +
        "</div>"
      );
    })
    .join("");
}


function renderEditor() {
  const doc = activeDoc();
  renderAssets();
  renderSavePdfButton();
  const empty = $("#resume-empty");
  const area = $("#resume-editor-area");
  const side = $("#resume-side");
  const app = $("#resume-app");
  if (!empty || !area) return;
  empty.hidden = Boolean(doc) || !rs.loaded;
  area.hidden = !doc;
  if (side) side.hidden = !doc;
  if (app) app.classList.toggle("is-empty", !doc);
  if (!doc) {
    $("#resume-preview-page").innerHTML = "";
    $("#resume-preview-title").textContent = "Preview";
    releaseResumeImages();
    clearPdf();
    setStatus(rs.loading ? "Loading…" : "No resume open");
    return;
  }
  const source = $("#resume-source");
  if (rs.renderedId !== doc.id) {
    source.value = doc.source;
    rs.renderedId = doc.id;
    rs.draft = doc.source;
    rs.dirty = false;
    // Another resume's pictures are no longer on screen; their blobs go back.
    releaseResumeImages();
    clearPdf();
    renderPreview();
  }
  $("#resume-name").value = doc.name;
  $("#resume-preview-title").textContent =
    doc.application_id && doc.job_company
      ? doc.job_company + (doc.job_role ? " — " + doc.job_role : "")
      : doc.name;
  renderDeleteBar();
  setStatus(rs.saving ? "Saving…" : rs.dirty ? "Unsaved changes" : "Saved");
}


function renderDeleteBar() {
  const bar = $("#resume-delete-bar");
  const doc = activeDoc();
  if (!bar) return;
  bar.hidden = !(rs.confirmingDelete && doc);
  if (bar.hidden) return;
  bar.querySelector(".resume-delete-text").textContent =
    "Delete " + doc.name + "? This cannot be undone.";
}


function setStatus(text) {
  const node = $("#resume-status");
  if (node) node.textContent = text;
}


/* -------------------------------------------------------------- the editor */

function renderGutter() {
  const gutter = $("#resume-gutter");
  if (!gutter) return;
  const count = Math.max(1, rs.draft.split("\n").length);
  let html = "";
  for (let line = 1; line <= count; line += 1) html += "<span>" + line + "</span>";
  gutter.innerHTML = html;
  gutter.scrollTop = $("#resume-source").scrollTop;
}


function renderPreview() {
  const page = $("#resume-preview-page");
  if (!page) return;
  const started = performance.now();
  const result = texToHtml(rs.draft, imageMap(activeDoc()));
  page.innerHTML = result.html;
  hydrateResumeImages();
  applyZoom();
  renderGutter();
  // A compile error outlives the preview render that follows it: an edit
  // schedules a preview 180 ms later, and that render used to wipe the panel
  // holding the reason the PDF is missing. The log is remembered against the
  // source it failed on, so it stays until that source changes or a compile
  // succeeds.
  if (rs.compileLog && rs.compileLog.source === rs.draft) {
    paintCompileLog(rs.compileLog.title, rs.compileLog.log);
  } else {
    rs.compileLog = null;
    renderIssues(result.issues);
  }
  rs.issueCount = result.issues.length;
  const compile = $("#resume-compile");
  if (compile) compile.dataset.ms = String(Math.round(performance.now() - started));
}


function fitScale() {
  const canvas = $("#resume-canvas");
  if (!canvas) return 1;
  const available = Math.max(240, canvas.clientWidth - 46);
  return Math.min(1, available / 794);
}


function applyZoom() {
  const page = $("#resume-preview-page");
  if (!page) return;
  // Like an editor's fit-to-width: the page always fits the pane, and the
  // zoom buttons scale from there.
  const effective = Math.round(rs.zoom * fitScale() * 100) / 100;
  page.style.setProperty("--resume-zoom", String(effective));
  const label = $("#resume-zoom");
  if (label) label.textContent = Math.round(effective * 100) + "%";
}


/* ------------------------------------------------------- real LaTeX output */

function clearPdf() {
  if (rs.pdf && rs.pdf.url) URL.revokeObjectURL(rs.pdf.url);
  rs.pdf = null;
  renderSavePdfButton();
  const pane = $("#resume-pdf");
  if (pane) pane.hidden = true;
  const frame = $("#resume-pdf-frame");
  if (frame) frame.removeAttribute("src");
  const canvas = $("#resume-canvas");
  if (canvas) canvas.classList.remove("is-pdf");
  const page = $("#resume-preview-page");
  if (page) page.hidden = false;
  const zoom = $(".resume-zoom");
  if (zoom) zoom.hidden = false;
}


function base64Bytes(value) {
  const binary = window.atob(String(value || ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}


function showPdf(result, doc, source) {
  clearPdf();
  const blob = new Blob([base64Bytes(result.pdf)], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  rs.pdf = { url, blob, docId: doc.id, source, engine: result.engine };
  const frame = $("#resume-pdf-frame");
  if (frame) frame.src = url;
  const pane = $("#resume-pdf");
  if (pane) pane.hidden = false;
  const page = $("#resume-preview-page");
  if (page) page.hidden = true;
  const canvas = $("#resume-canvas");
  if (canvas) canvas.classList.add("is-pdf");
  const zoom = $(".resume-zoom");
  if (zoom) zoom.hidden = true;
}


function paintCompileLog(title, log) {
  const box = $("#resume-issues");
  if (!box) return;
  box.hidden = false;
  box.innerHTML =
    '<strong class="resume-issues-title">' + esc(title || "LaTeX error") + "</strong>" +
    (log ? '<pre class="resume-log">' + esc(log) + "</pre>" : "");
}


function renderCompileLog(title, log) {
  rs.compileLog = { title: title, log: log, source: rs.draft };
  paintCompileLog(title, log);
}


function renderIssues(issues) {
  const box = $("#resume-issues");
  if (!box) return;
  if (!issues.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML =
    '<strong class="resume-issues-title">' +
    issues.length + (issues.length === 1 ? " warning" : " warnings") +
    "</strong><ul>" +
    issues.slice(0, 4).map((issue) => "<li>" + (issue.line ? "Line " + issue.line + ": " : "") + esc(issue.message) + "</li>").join("") +
    "</ul>";
}


function schedulePreview() {
  window.clearTimeout(rs.previewTimer);
  rs.previewTimer = window.setTimeout(renderPreview, 180);
}


function scheduleSave() {
  rs.dirty = true;
  setStatus("Unsaved changes");
  window.clearTimeout(rs.saveTimer);
  rs.saveTimer = window.setTimeout(() => saveNow(), 900);
}


async function saveNow() {
  const doc = activeDoc();
  if (!doc) return;
  window.clearTimeout(rs.saveTimer);
  rs.saving = true;
  setStatus("Saving…");
  try {
    const saved = await api("/api/resumes/" + doc.id, {
      method: "PATCH",
      body: JSON.stringify({ source: rs.draft }),
    });
    rs.docs.set(doc.id, saved);
    rs.list = rs.list.map((item) => (item.id === doc.id ? Object.assign({}, item, { updated_at: saved.updated_at, size: saved.size }) : item));
    rs.dirty = rs.draft !== saved.source;
    rs.saving = false;
    setStatus(rs.dirty ? "Unsaved changes" : "Saved " + timeOf(saved.updated_at));
    renderRail();
  } catch (error) {
    rs.saving = false;
    setStatus("Not saved");
    toast(error.message, true);
  }
}


function timeOf(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}


/* --------------------------------------------------------------- actions */

export async function createResume(options) {
  const settings = options || {};
  const payload = {};
  if (settings.application_id) {
    payload.application_id = settings.application_id;
    payload.name = slug(settings.company || "tailored");
    payload.label = [settings.company, settings.role].filter(Boolean).join(" — ");
  } else {
    let index = 1;
    while (rs.list.some((item) => item.name === "resume-" + index + ".tex")) index += 1;
    payload.name = "resume-" + index + ".tex";
  }
  try {
    const doc = await api("/api/resumes", { method: "POST", body: JSON.stringify(payload) });
    rs.list = [doc].concat(rs.list);
    rs.docs.set(doc.id, doc);
    rs.activeId = doc.id;
    rs.renderedId = null;
    rs.confirmingDelete = false;
    if (!rs.tabs.includes(doc.id)) rs.tabs.push(doc.id);
    renderRail();
    renderTabsRow();
    renderEditor();
    toast(payload.application_id ? "Tailored resume created" : "Resume created");
    return doc;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}


/** Open the job's tailored resume, creating it from the master on first use. */
export async function openResumeForJob(app) {
  if (rs.profileId !== state.profileId) resetResumeState();
  rs.profileId = state.profileId;
  if (!rs.loaded) {
    // A fresh session (or a direct jump from a job) still needs the list.
    try {
      rs.list = await api("/api/resumes");
      rs.loaded = true;
    } catch (error) {
      toast(error.message, true);
    }
  }
  const existing = rs.list.find((item) => item.application_id === app.id);
  if (existing) {
    await openResume(existing.id, { render: false });
    return existing;
  }
  const created = await createResume({
    application_id: app.id,
    company: app.company,
    role: app.role,
  });
  return created;
}


function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",").slice(1).join(","));
    reader.onerror = () => reject(new Error("Could not read " + file.name));
    reader.readAsDataURL(file);
  });
}


async function uploadResumeFiles(fileList) {
  const doc = activeDoc();
  if (!doc) return;
  const files = Array.from(fileList || []);
  if (!files.length) return;
  let added = 0;
  for (const file of files) {
    if (file.size > MAX_UPLOAD) {
      toast(file.name + " is larger than 10 MB", true);
      continue;
    }
    try {
      const data = await readFileBase64(file);
      const saved = await api("/api/resumes/" + doc.id + "/files", {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          data: data,
        }),
      });
      doc.files = (doc.files || []).concat(saved);
      added += 1;
    } catch (error) {
      toast(error.message, true);
    }
  }
  if (added) {
    renderAssets();
    renderPreview();
    toast(added === 1 ? "File added" : added + " files added");
  }
}


async function removeResumeFile(fileId) {
  const doc = activeDoc();
  if (!doc) return;
  try {
    await api("/api/resumes/" + doc.id + "/files/" + fileId, { method: "DELETE" });
    doc.files = (doc.files || []).filter((file) => file.id !== fileId);
    releaseResumeImage(fileId);
    renderAssets();
    renderPreview();
    toast("File removed");
  } catch (error) {
    toast(error.message, true);
  }
}


async function saveCompiledPdf() {
  const doc = activeDoc();
  if (!doc) return;
  const pdf = rs.pdf;
  if (!pdf || !pdf.blob || pdf.docId !== doc.id) {
    toast("Compile this resume first", true);
    return;
  }
  const suggested = doc.name.replace(/\.tex$/i, "") + ".pdf";
  // A resume made for a job opens with that job already chosen, so the common
  // case is: compile, Save & attach, done.
  const linkedId = doc.application_id == null ? null : doc.application_id;
  const jobs = (state.apps || [])
    .slice()
    .sort((a, b) => {
      const linkedA = String(a.id) === String(linkedId) ? 0 : 1;
      const linkedB = String(b.id) === String(linkedId) ? 0 : 1;
      if (linkedA !== linkedB) return linkedA - linkedB;
      return a.company.localeCompare(b.company);
    });
  // What each job already has, so the dialog can say whether this is a second
  // resume for that job. A failure here only costs the summary line.
  let documents = {};
  try {
    documents = await api("/api/documents");
  } catch (error) {
    documents = {};
  }
  const answer = await savePdfDialog({
    filename: suggested,
    jobs: jobs,
    documents: documents,
    linkedId: linkedId,
    kind: /cover/i.test(doc.name) ? "cover_letter" : "resume",
    text: linkedId
      ? "It is kept with this resume's files and attached to the job this resume was written for."
      : "It is kept with this resume's files. Pick the job it belongs to and it is attached there in the same step.",
  });
  if (!answer) return;
  const filename = /\.pdf$/i.test(answer.name) ? answer.name : answer.name + ".pdf";
  let saved;
  try {
    const data = await readFileBase64(pdf.blob);
    saved = await api("/api/resumes/" + doc.id + "/files", {
      method: "POST",
      body: JSON.stringify({
        filename: filename,
        content_type: "application/pdf",
        data: data,
      }),
    });
    doc.files = (doc.files || []).concat(saved);
    renderAssets();
  } catch (error) {
    toast(error.message, true);
    return;
  }
  if (!answer.jobId) {
    toast("Saved " + saved.filename + " with this resume");
    return;
  }
  const job = jobs.find((item) => String(item.id) === String(answer.jobId));
  const where = job ? job.company : "the job";
  const kindWord =
    answer.kind === "resume" ? "resume" : answer.kind === "cover_letter" ? "cover letter" : "file";
  try {
    await api("/api/files/" + saved.id + "/attach", {
      method: "POST",
      body: JSON.stringify({
        application_id: answer.jobId,
        kind: answer.kind,
        replace: answer.replace,
      }),
    });
    toast(
      answer.replace
        ? "Saved " + saved.filename + " and replaced the " + kindWord + " on " + where
        : "Saved " + saved.filename + " and attached it to " + where + " as " +
          (DOC_LABEL[answer.kind] || "a file")
    );
    if (String(state.drawerId) === String(answer.jobId)) openDrawer(answer.jobId);
  } catch (error) {
    toast("Saved " + saved.filename + ", but could not attach it: " + error.message, true);
  }
}


async function renameActive(value) {
  const doc = activeDoc();
  if (!doc) return;
  const name = String(value || "").trim();
  if (!name || name === doc.name) {
    $("#resume-name").value = doc.name;
    return;
  }
  try {
    const saved = await api("/api/resumes/" + doc.id, {
      method: "PATCH",
      body: JSON.stringify({ name: name }),
    });
    rs.docs.set(doc.id, saved);
    rs.list = rs.list.map((item) => (item.id === doc.id ? Object.assign({}, item, { name: saved.name }) : item));
    $("#resume-name").value = saved.name;
    renderRail();
    renderTabsRow();
    toast("Renamed to " + saved.name);
  } catch (error) {
    $("#resume-name").value = doc.name;
    toast(error.message, true);
  }
}


async function deleteActive() {
  const doc = activeDoc();
  if (!doc) return;
  try {
    await api("/api/resumes/" + doc.id, { method: "DELETE" });
    rs.list = rs.list.filter((item) => item.id !== doc.id);
    rs.docs.delete(doc.id);
    rs.tabs = rs.tabs.filter((id) => id !== doc.id);
    rs.confirmingDelete = false;
    const next = rs.tabs[0] || null;
    rs.activeId = next;
    rs.renderedId = null;
    if (next) await openResume(next, { render: false });
    renderRail();
    renderTabsRow();
    renderEditor();
    toast("Resume deleted");
  } catch (error) {
    toast(error.message, true);
  }
}


function closeTab(id) {
  rs.tabs = rs.tabs.filter((entry) => entry !== id);
  if (rs.activeId === id) {
    rs.activeId = rs.tabs[0] || null;
    rs.renderedId = null;
    if (rs.activeId) openResume(rs.activeId, { render: false });
  }
  renderTabsRow();
  renderEditor();
}


function downloadTex() {
  const doc = activeDoc();
  if (!doc) return;
  const blob = new Blob([rs.draft], { type: "text/x-tex;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = doc.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast("Downloaded " + doc.name);
}


function printResume() {
  const doc = activeDoc();
  if (!doc) return;
  const page = $("#resume-preview-page");
  const win = window.open("", "_blank");
  if (!win) {
    toast("Allow pop-ups to print the resume", true);
    return;
  }
  const title = esc(doc.name.replace(/\.tex$/i, ""));
  win.document.write(
    "<!DOCTYPE html><html><head><meta charset=\"utf-8\" /><title>" + title + "</title>" +
      '<link rel="stylesheet" href="' + window.location.origin + '/styles.css" />' +
      '<style>body { margin: 0; background: #fff; } .resume-page { box-shadow: none; margin: 0 auto; }</style>' +
      "</head><body class=\"resume-print\">" +
      '<article class="resume-page" id="resume-preview-page">' + page.innerHTML + "</article>" +
      "</body></html>"
  );
  win.document.close();
  win.focus();
  window.setTimeout(() => win.print(), 350);
}


/* --------------------------------------------------------------- events */

export function bindResumeEvents() {
  const source = $("#resume-source");
  if (!source) return;

  source.addEventListener("input", () => {
    rs.draft = source.value;
    // Any edit makes a compiled PDF stale: back to the live preview.
    clearPdf();
    scheduleSave();
    schedulePreview();
  });

  source.addEventListener("scroll", () => {
    const gutter = $("#resume-gutter");
    if (gutter) gutter.scrollTop = source.scrollTop;
  });

  source.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = source.selectionStart;
      const end = source.selectionEnd;
      source.value = source.value.slice(0, start) + "  " + source.value.slice(end);
      source.selectionStart = source.selectionEnd = start + 2;
      rs.draft = source.value;
      clearPdf();
      scheduleSave();
      schedulePreview();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveNow();
    }
  });

  $("#resume-compile").addEventListener("click", async () => {
    const doc = activeDoc();
    if (!doc) return;
    // Save first: the save sets "Saved 14:32", which would otherwise land on
    // top of the compile message a moment later.
    await saveNow();
    renderPreview();
    const source = rs.draft;
    const button = $("#resume-compile");
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Compiling…";
    try {
      const result = await api("/api/resumes/" + doc.id + "/compile", {
        method: "POST",
        body: JSON.stringify({ source: source }),
      });
      if (rs.draft !== source) {
        toast("The source changed while compiling — compile again", true);
        return;
      }
      // A real PDF supersedes whatever the last failure said.
      rs.compileLog = null;
      showPdf(result, doc, source);
      renderSavePdfButton();
      renderPreview();
      setStatus(
        result.engine + " · PDF in " + (Number(result.elapsed_ms || 0) / 1000).toFixed(2) + " s"
      );
    } catch (error) {
      // No engine, or TeX refused: keep the in-app preview, which is already
      // rendered, and say why the PDF is missing.
      clearPdf();
      renderPreview();
      if (error.status === 501) {
        setStatus("No LaTeX engine — showing the in-app preview");
      } else if (error.status === 422) {
        renderCompileLog(error.message, error.log || "");
        setStatus("LaTeX error — showing the in-app preview");
      } else {
        setStatus("Compile failed — showing the in-app preview");
        toast(error.message, true);
      }
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  });

  $("#resume-save-pdf").addEventListener("click", saveCompiledPdf);

  $("#resume-name").addEventListener("change", (event) => renameActive(event.target.value));
  $("#resume-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.target.blur();
    }
  });

  $("#resume-delete").addEventListener("click", () => {
    rs.confirmingDelete = true;
    renderDeleteBar();
  });
  $("#resume-delete-cancel").addEventListener("click", () => {
    rs.confirmingDelete = false;
    renderDeleteBar();
  });
  $("#resume-delete-confirm").addEventListener("click", deleteActive);

  const zoom = (step) => {
    rs.zoom = Math.max(0.6, Math.min(1.6, Math.round((rs.zoom + step) * 10) / 10));
    localStorage.setItem("jobpipe:resume-zoom", String(rs.zoom));
    applyZoom();
  };
  $$("[data-zoom]").forEach((button) => {
    button.addEventListener("click", () => zoom(button.dataset.zoom === "+" ? 0.1 : -0.1));
  });
  window.addEventListener("resize", () => {
    if (state.tab === "resume") applyZoom();
  });

  const assets = $("#resume-assets");
  assets.addEventListener("change", (event) => {
    if (!event.target.matches("#resume-file-input")) return;
    uploadResumeFiles(event.target.files);
    event.target.value = "";
  });
  assets.addEventListener("click", (event) => {
    const preview = event.target.closest("[data-preview-file]");
    if (preview) {
      event.preventDefault();
      previewResumeFile(Number(preview.dataset.previewFile));
      return;
    }
    const remove = event.target.closest("[data-resume-file]");
    if (!remove) return;
    event.preventDefault();
    event.stopPropagation();
    removeResumeFile(Number(remove.dataset.resumeFile));
  });
  ["dragenter", "dragover"].forEach((name) => {
    assets.addEventListener(name, (event) => {
      event.preventDefault();
      assets.classList.add("is-dragover");
    });
  });
  assets.addEventListener("dragleave", (event) => {
    if (assets.contains(event.relatedTarget)) return;
    assets.classList.remove("is-dragover");
  });
  assets.addEventListener("drop", (event) => {
    event.preventDefault();
    assets.classList.remove("is-dragover");
    uploadResumeFiles(event.dataTransfer ? event.dataTransfer.files : []);
  });

  const menuButton = $("#resume-menu-btn");
  const menu = $("#resume-menu");
  menuButton.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuButton.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("click", (event) => {
    if (menu.hidden) return;
    if (event.target.closest("#resume-menu") || event.target.closest("#resume-menu-btn")) return;
    menu.hidden = true;
    menuButton.setAttribute("aria-expanded", "false");
  });
  $$("[data-resume-export]").forEach((button) => {
    button.addEventListener("click", () => {
      menu.hidden = true;
      menuButton.setAttribute("aria-expanded", "false");
      if (button.dataset.resumeExport === "tex") downloadTex();
      if (button.dataset.resumeExport === "pdf") printResume();
    });
  });

  document.addEventListener("click", async (event) => {
    const file = event.target.closest("[data-resume-id]");
    if (file) {
      event.preventDefault();
      openResume(Number(file.dataset.resumeId));
      return;
    }
    const close = event.target.closest("[data-resume-close]");
    if (close) {
      event.preventDefault();
      closeTab(Number(close.dataset.resumeClose));
      return;
    }
    const action = event.target.closest("[data-resume-action]");
    if (action && action.dataset.resumeAction === "new") {
      event.preventDefault();
      await createResume();
    }
  });
}


/* -------------------------------------------------------------- rendering */

function graphicWidth(optional) {
  const match = String(optional || "").match(/width\s*=\s*([\d.]+)\s*(cm|mm|in|pt|px|em)/i);
  if (!match) {
    const fraction = String(optional || "").match(/width\s*=\s*([\d.]+)\\textwidth/);
    return fraction ? Math.round(Number(fraction[1]) * 100) + "%" : "";
  }
  const factor = { cm: 37.8, mm: 3.78, in: 96, pt: 1.33, px: 1, em: 16 }[match[2].toLowerCase()];
  return Math.round(Number(match[1]) * factor) + "px";
}


function slug(value) {
  const text = String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return (text || "resume") + ".tex";
}


const FILE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M6 3h8l4 4v14H6V3z"/><path d="M14 3v4h4"/></svg>';
const IMAGE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.4"/><path d="M21 16l-5-4.5-6 5"/></svg>';
const UPLOAD_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 16V4m0 0L8 8m4-4l4 4"/><path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>';
const CLOSE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';


/* ------------------------------------------------------ LaTeX → HTML */

const SIZES = {
  tiny: "tex-size-tiny",
  scriptsize: "tex-size-tiny",
  footnotesize: "tex-size-small",
  small: "tex-size-small",
  normalsize: "tex-size-normal",
  large: "tex-size-large",
  Large: "tex-size-large",
  LARGE: "tex-size-xlarge",
  huge: "tex-size-xlarge",
  Huge: "tex-size-xxlarge",
};

const SKIP_COMMANDS = [
  "documentclass", "usepackage", "setlength", "addtolength", "newcommand", "renewcommand",
  "providecommand", "definecolor", "pagestyle", "thispagestyle", "fontsize", "linespread",
  "hypersetup", "graphicspath", "geometry", "DeclareUnicodeCharacter", "vspace", "vspace*",
  "hspace", "hspace*", "label", "setcounter", "bibliographystyle", "bibliography",
  "institute", "title", "author", "date", "maketitle", "pagestyle",
];

const SAFE_URL = /^(https?:|mailto:)/i;
const SAFE_COLOR = /^(#[0-9a-f]{3,8}|[a-z]{3,20})$/i;

const NAMED = {
  LaTeX: "LaTeX",
  TeX: "TeX",
  ldots: "…",
  dots: "…",
  textbackslash: "\\",
  textasciitilde: "~",
  textasciicircum: "^",
  textendash: "–",
  textemdash: "—",
  textbar: "|",
  today: new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }),
  quad: " ",
  qquad: "  ",
};


let CURRENT_IMAGES = {};


export function texToHtml(source, images) {
  CURRENT_IMAGES = images || {};
  const body = stripComments(String(source || ""));
  const start = body.indexOf("\\begin{document}");
  const end = body.lastIndexOf("\\end{document}");
  const content = start === -1
    ? body
    : body.slice(start + "\\begin{document}".length, end > start ? end : body.length);
  return {
    html: '<div class="tex-body">' + renderBlocks(content) + "</div>",
    issues: findIssues(source),
  };
}


function stripComments(text) {
  return text
    .split("\n")
    .map((line) => {
      let out = "";
      for (let index = 0; index < line.length; index += 1) {
        const char = line[index];
        if (char === "\\") {
          out += char + (line[index + 1] || "");
          index += 1;
          continue;
        }
        if (char === "%") break;
        out += char;
      }
      return out;
    })
    .join("\n");
}


function findIssues(source) {
  const issues = [];
  const stack = [];
  stripComments(String(source || ""))
    .split("\n")
    .forEach((line, index) => {
      const tokens = line.match(/\\(begin|end)\{[A-Za-z*]+\}/g) || [];
      tokens.forEach((token) => {
        const kind = token.startsWith("\\begin") ? "begin" : "end";
        const name = token.slice(token.indexOf("{") + 1, token.lastIndexOf("}"));
        if (kind === "begin") {
          stack.push({ name: name, line: index + 1 });
          return;
        }
        const at = stack.map((item) => item.name).lastIndexOf(name);
        if (at === -1) issues.push({ line: index + 1, message: "\\end{" + name + "} has no matching \\begin" });
        else stack.splice(at, 1);
      });
    });
  stack.forEach((item) => issues.push({ line: item.line, message: "\\begin{" + item.name + "} is never closed" }));

  let depth = 0;
  let reported = false;
  stripComments(String(source || ""))
    .split("\n")
    .forEach((line, index) => {
      for (let position = 0; position < line.length; position += 1) {
        const char = line[position];
        if (char === "\\") {
          position += 1;
          continue;
        }
        if (char === "{") depth += 1;
        if (char === "}") depth -= 1;
        if (depth < 0 && !reported) {
          reported = true;
          issues.push({ line: index + 1, message: "A closing brace has no opening brace" });
        }
      }
    });
  if (depth !== 0) {
    issues.push({
      line: 0,
      message: depth > 0 ? depth + " brace(s) are never closed" : -depth + " extra closing brace(s)",
    });
  }
  return issues;
}


function consumeGroup(text, from) {
  if (text[from] !== "{") return null;
  let depth = 0;
  for (let index = from; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\\") {
      index += 1;
      continue;
    }
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return { content: text.slice(from + 1, index), end: index + 1 };
    }
  }
  return { content: text.slice(from + 1), end: text.length };
}


function consumeOptional(text, from) {
  if (text[from] !== "[") return null;
  const end = text.indexOf("]", from);
  if (end === -1) return null;
  return { content: text.slice(from + 1, end), end: end + 1 };
}


function skipSpaces(text, from) {
  let index = from;
  while (index < text.length && /\s/.test(text[index])) index += 1;
  return index;
}


function renderBlocks(text) {
  const out = [];
  const beginRe = /\\(begin|end)\{([A-Za-z*]+)\}/g;
  let cursor = 0;
  let match;
  while ((match = beginRe.exec(text)) !== null) {
    if (match[1] === "end") continue;
    const name = match[2];
    const found = matchEnvironment(text, match.index, name);
    out.push(renderChunk(text.slice(cursor, match.index)));
    out.push(renderEnvironment(name, found.content));
    cursor = found.end;
    beginRe.lastIndex = found.end;
  }
  out.push(renderChunk(text.slice(cursor)));
  return out.filter(Boolean).join("");
}


const HEADING_TOKEN = /(\\(?:sub){0,2}section\*?\s*\{[^{}]*\})/;


function renderChunk(text) {
  // A \section always starts its own block, as in LaTeX: the heading must not
  // share a line with the entry that follows it.
  return String(text || "")
    .split(HEADING_TOKEN)
    .map((segment) => {
      if (!segment.trim()) return "";
      if (/^\s*\\(?:sub){0,2}section\*?\s*\{/.test(segment)) return renderInline(segment.trim());
      return renderParagraphs(segment);
    })
    .join("");
}


function matchEnvironment(text, beginIndex, name) {
  const tokenRe = /\\(begin|end)\{([A-Za-z*]+)\}/g;
  tokenRe.lastIndex = beginIndex;
  let depth = 0;
  let token;
  while ((token = tokenRe.exec(text)) !== null) {
    if (token[2] !== name) continue;
    depth += token[1] === "begin" ? 1 : -1;
    if (depth === 0) {
      const contentStart = text.indexOf("{", beginIndex) + 1;
      const contentStartEnd = text.indexOf("}", contentStart) + 1;
      return { content: text.slice(contentStartEnd, token.index), end: tokenRe.lastIndex };
    }
  }
  const contentStartEnd = text.indexOf("}", text.indexOf("{", beginIndex)) + 1;
  return { content: text.slice(contentStartEnd), end: text.length };
}


function renderEnvironment(name, content) {
  const kind = name.replace(/\*/g, "");
  if (kind === "itemize" || kind === "enumerate" || kind === "description") {
    const items = content.split(/\\item\b/).slice(1);
    const listHtml = items
      .map((item) => {
        const optional = consumeOptional(item, skipSpaces(item, 0));
        const body = optional ? item.slice(optional.end) : item;
        return "<li>" + renderParagraphs(body) + "</li>";
      })
      .join("");
    const tag = kind === "enumerate" ? "ol" : "ul";
    return '<' + tag + ' class="tex-list">' + listHtml + "</" + tag + ">";
  }
  if (kind === "center") return '<div class="tex-center">' + renderParagraphs(content) + "</div>";
  if (kind === "flushleft") return '<div class="tex-flushleft">' + renderParagraphs(content) + "</div>";
  if (kind === "flushright") return '<div class="tex-flushright">' + renderParagraphs(content) + "</div>";
  if (kind === "verbatim" || kind === "lstlisting") return "<pre class=\"tex-verbatim\">" + esc(content) + "</pre>";
  if (kind === "tabular" || kind === "tabular*" || kind === "tabularx") {
    const rows = content
      .split(/\\\\/)
      .map((row) => row.replace(/\\(hline|toprule|midrule|bottomrule)/g, "").trim())
      .filter(Boolean)
      .map((row) => {
        const cells = row.split("&").map((cell) => "<td>" + renderInline(cell.trim()) + "</td>").join("");
        return "<tr>" + cells + "</tr>";
      })
      .join("");
    return '<table class="tex-tabular"><tbody>' + rows + "</tbody></table>";
  }
  if (kind === "document") return renderBlocks(content);
  return '<div class="tex-block">' + renderBlocks(content) + "</div>";
}


function renderParagraphs(text) {
  return String(text || "")
    .split(/\n[ \t]*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => {
      const lines = paragraph
        .split(/\\\\(?:\[[^\]]*\])?/)
        .map((line) => line.trim())
        .filter(Boolean);
      return lines.map(renderLine).join("");
    })
    .join("");
}


function renderLine(line) {
  const parts = line.split(/\\hfill\b/);
  if (parts.length > 1) {
    return (
      '<div class="tex-line is-split">' +
      parts
        .map((part, index) => {
          const cls = index === 0 ? "tex-left" : "tex-right";
          return '<span class="' + cls + '">' + renderInline(part.trim()) + "</span>";
        })
        .join("") +
      "</div>"
    );
  }
  return '<div class="tex-line">' + renderInline(line) + "</div>";
}


function renderInline(text) {
  let out = "";
  let index = 0;
  const source = String(text || "");

  while (index < source.length) {
    const char = source[index];

    if (char === "\\") {
      const next = source[index + 1];
      if (next === undefined) break;

      if (!/[A-Za-z]/.test(next)) {
        // One-character commands: escapes, spacing and a literal line break.
        if (next === "\\") {
          out += "<br />";
          index += 2;
          continue;
        }
        const simple = { "&": "&amp;", "%": "%", $: "$", "#": "#", _: "_", "{": "{", "}": "}" };
        if (Object.prototype.hasOwnProperty.call(simple, next)) {
          out += simple[next];
          index += 2;
          continue;
        }
        if (next === " " || next === "," || next === ";" || next === ":" || next === "!") {
          out += next === " " ? " " : "";
          index += 2;
          continue;
        }
        out += esc(next);
        index += 2;
        continue;
      }

      let end = index + 1;
      while (end < source.length && /[A-Za-z*]/.test(source[end])) end += 1;
      const name = source.slice(index + 1, end);
      index = end;

      if (SIZES[name]) {
        // \Large{text}: wrap the group that follows. A bare \Large is a
        // size switch with no end in the source, so it is dropped.
        const group = consumeGroup(source, skipSpaces(source, index));
        if (group) {
          out += '<span class="' + SIZES[name] + '">' + renderInline(group.content) + "</span>";
          index = group.end;
        }
        continue;
      }
      if (name === "hfill") {
        out += '<span class="tex-hfill"></span>';
        continue;
      }
      if (name === "newline" || name === "linebreak" || name === "par") {
        out += "<br />";
        continue;
      }
      if (name === "smallskip" || name === "medskip" || name === "bigskip") {
        out += '<span class="tex-skip"></span><br />';
        continue;
      }
      if (name === "hrule" || name === "rule") {
        if (name === "rule") {
          const width = consumeGroup(source, skipSpaces(source, index));
          if (width) index = width.end;
          const height = consumeGroup(source, skipSpaces(source, index));
          if (height) index = height.end;
        }
        out += '<hr class="tex-rule" />';
        continue;
      }
      if (name === "includegraphics") {
        const optional = consumeOptional(source, skipSpaces(source, index));
        if (optional) index = optional.end;
        const file = consumeGroup(source, skipSpaces(source, index));
        if (file) index = file.end;
        const label = file ? file.content.split("/").pop().trim() : "image";
        const uploaded = CURRENT_IMAGES[label.toLowerCase()];
        const width = optional ? graphicWidth(optional.content) : "";
        if (uploaded && /^image\//.test(uploaded.content_type)) {
          // The src is filled in once the page is on screen: the bytes need
          // the unlock token, which an <img> cannot send.
          out +=
            '<img class="tex-img" data-resume-image="' + uploaded.id + '"' +
            ' alt="' + esc(label) + '"' +
            (width ? ' style="width:' + width + '"' : "") +
            " />";
        } else {
          out +=
            '<span class="tex-image" title="' + esc(label) + '">' + esc(label) +
            (uploaded ? "" : " — not uploaded") +
            "</span>";
        }
        continue;
      }
      if (SKIP_COMMANDS.includes(name)) {
        for (;;) {
          const at = skipSpaces(source, index);
          const group = consumeGroup(source, at);
          const optional = consumeOptional(source, at);
          if (optional) {
            index = optional.end;
            continue;
          }
          if (!group) break;
          index = group.end;
        }
        continue;
      }

      const headings = { section: "h2", subsection: "h3", subsubsection: "h4", paragraph: "h5", subparagraph: "h5" };
      const heading = headings[name.replace(/\*$/, "")];
      if (heading) {
        let at = skipSpaces(source, index);
        const optional = consumeOptional(source, at);
        if (optional) at = optional.end;
        const group = consumeGroup(source, skipSpaces(source, at));
        if (!group) continue;
        index = group.end;
        out +=
          '<' + heading + ' class="tex-section tex-' + name.replace(/\*$/, "") + '">' +
          renderInline(group.content) +
          "</" + heading + ">";
        continue;
      }

      const formats = {
        textbf: "strong",
        textit: "em",
        emph: "em",
        underline: "u",
        texttt: "code",
        textsc: "tex-smallcaps",
        textsuperscript: "sup",
        textsubscript: "sub",
      };
      if (formats[name]) {
        const group = consumeGroup(source, skipSpaces(source, index));
        if (!group) continue;
        index = group.end;
        const tag = formats[name];
        if (tag.startsWith("tex-")) out += '<span class="' + tag + '">' + renderInline(group.content) + "</span>";
        else out += "<" + tag + ">" + renderInline(group.content) + "</" + tag + ">";
        continue;
      }
      if (name === "href") {
        const url = consumeGroup(source, skipSpaces(source, index));
        if (!url) continue;
        index = url.end;
        const label = consumeGroup(source, skipSpaces(source, index));
        if (!label) continue;
        index = label.end;
        const target = url.content.trim();
        if (!SAFE_URL.test(target)) {
          out += renderInline(label.content);
          continue;
        }
        out += '<a href="' + esc(target) + '" target="_blank" rel="noopener noreferrer">' + renderInline(label.content) + "</a>";
        continue;
      }
      if (name === "url") {
        const url = consumeGroup(source, skipSpaces(source, index));
        if (!url) continue;
        index = url.end;
        const target = url.content.trim();
        if (!SAFE_URL.test(target)) {
          out += esc(target);
          continue;
        }
        out += '<a href="' + esc(target) + '" target="_blank" rel="noopener noreferrer">' + esc(target) + "</a>";
        continue;
      }
      if (name === "textcolor" || name === "color") {
        const colour = consumeGroup(source, skipSpaces(source, index));
        if (!colour) continue;
        index = colour.end;
        const value = colour.content.trim();
        if (name === "color") continue;
        const label = consumeGroup(source, skipSpaces(source, index));
        if (!label) continue;
        index = label.end;
        if (!SAFE_COLOR.test(value)) {
          out += renderInline(label.content);
          continue;
        }
        out += '<span class="tex-color" style="color:' + esc(value) + '">' + renderInline(label.content) + "</span>";
        continue;
      }
      if (name === "begin" || name === "end") {
        const group = consumeGroup(source, skipSpaces(source, index));
        if (group) index = group.end;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(NAMED, name)) {
        out += esc(NAMED[name]);
        continue;
      }
      // An unknown command with arguments still has text worth showing.
      const group = consumeGroup(source, skipSpaces(source, index));
      if (group) {
        index = group.end;
        out += renderInline(group.content);
      }
      continue;
    }

    if (char === "{") {
      const group = consumeGroup(source, index);
      if (group) {
        // {\LARGE Name} is the usual shape: the size applies to the group.
        const size = group.content.match(/^\s*\\([A-Za-z]+)\b/);
        if (size && SIZES[size[1]]) {
          out +=
            '<span class="' + SIZES[size[1]] + '">' +
            renderInline(group.content.slice(size[0].length)) +
            "</span>";
        } else {
          out += '<span class="tex-group">' + renderInline(group.content) + "</span>";
        }
        index = group.end;
        continue;
      }
      index += 1;
      continue;
    }
    if (char === "}") {
      index += 1;
      continue;
    }
    if (char === "~") {
      out += "&nbsp;";
      index += 1;
      continue;
    }
    if (char === "-" && source[index + 1] === "-" && source[index + 2] === "-") {
      out += "—";
      index += 3;
      continue;
    }
    if (char === "-" && source[index + 1] === "-") {
      out += "–";
      index += 2;
      continue;
    }
    if (char === "`" && source[index + 1] === "`") {
      out += "“";
      index += 2;
      continue;
    }
    if (char === "'" && source[index + 1] === "'") {
      out += "”";
      index += 2;
      continue;
    }
    if (char === "&") {
      out += "&amp;";
      index += 1;
      continue;
    }

    let run = "";
    while (index < source.length && !/["\\{}~&`]/.test(source[index]) && !(source[index] === "-" && source[index + 1] === "-")) {
      run += source[index];
      index += 1;
    }
    if (!run) {
      run = source[index];
      index += 1;
    }
    out += esc(run);
  }

  return out;
}
