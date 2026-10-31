/* Jobby — Add/edit job dialog, link extraction, confirmations.
   ES module, no build step. Open the dashboard through server.py. */

import { $, $$, DOC_LABEL, api, esc, formatSize, state, statusSelectOptions, toast, todayISO } from "./core.js";
import { promptRejectFiles, updateLocal } from "./data.js";
import { closeDrawer, openDrawer } from "./drawer.js";
import { setPanelNote } from "./io.js";
import { render, setTab, syncDatalists } from "./workspace.js";


export function setModalMode(mode) {
  state.modalMode = mode;
  const isEditing = Boolean(state.editingId);
  const effective = isEditing ? "manual" : mode;
  $$(".mode-btn").forEach((button) => {
    const active = button.dataset.mode === effective;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  $("#panel-link").hidden = effective !== "link";
  $("#panel-file").hidden = effective !== "file";
  $("#app-form").hidden = effective !== "manual";
  $("#modal-foot").hidden = effective === "file";
  $("#modal-modes").hidden = isEditing;
  $("#modal-sub").hidden = isEditing;
  $("#modal-save").hidden = effective === "link";
  $("#modal-cancel").hidden = effective === "file";
}


export function syncPhaseFields() {
  const phase = $("#form-phase").value;
  $$(".phase-applied-only").forEach((field) => {
    field.hidden = phase === "to_apply";
  });
}


export function openModal(app) {
  state.editingId = app ? app.id : null;
  const form = $("#app-form");
  form.reset();
  const error = $("#form-error");
  error.hidden = true;
  error.textContent = "";
  error.classList.remove("is-warning");
  $$(".field.has-error").forEach((field) => field.classList.remove("has-error"));
  $("#form-status").innerHTML = statusSelectOptions(app ? app.status : "applied", false);
  $("#modal-title").textContent = app ? "Edit job" : "Add job";
  $("#modal-save").textContent = app ? "Save changes" : "Add job";

  const values = app || {
    status: "applied",
    work_mode: "onsite",
    applied_on: todayISO(),
    phase: state.tab === "to_apply" ? "to_apply" : "applied",
  };
  Object.keys(values).forEach((key) => {
    const field = form.elements.namedItem(key);
    if (field && values[key] != null) field.value = values[key];
  });
  if (!app) {
    form.elements.namedItem("applied_on").value = todayISO();
    form.elements.namedItem("work_mode").value = "onsite";
  }
  if (!form.elements.namedItem("phase").value) form.elements.namedItem("phase").value = "applied";
  $("#extract-url").value = "";
  $("#extract-status").hidden = true;
  $("#import-status").hidden = true;
  $("#modal-import-file").value = "";
  syncDatalists();
  syncPhaseFields();
  setModalMode("manual");
  $("#modal-backdrop").hidden = false;
  $("#modal-content").scrollTop = 0;
  document.body.classList.add("no-scroll");
  if (!app) window.setTimeout(() => form.elements.namedItem("company").focus(), 30);
}


export function closeModal() {
  $("#modal-backdrop").hidden = true;
  state.editingId = null;
  document.body.classList.remove("no-scroll");
}


export async function submitForm(event) {
  event.preventDefault();
  const form = $("#app-form");
  const data = {};
  Array.from(form.elements).forEach((field) => {
    if (!field.name) return;
    data[field.name] = field.value.trim();
  });
  const error = $("#form-error");
  const companyField = form.elements.namedItem("company").closest(".field");
  const roleField = form.elements.namedItem("role").closest(".field");
  companyField.classList.toggle("has-error", !data.company);
  roleField.classList.toggle("has-error", !data.role);
  if (!data.company || !data.role) {
    error.textContent = "Company and role are required.";
    error.classList.remove("is-warning");
    error.hidden = false;
    return;
  }
  if (data.applied_on === "") delete data.applied_on;
  if (data.next_step_on === "") data.next_step_on = null;
  error.hidden = true;
  const saveButton = $("#modal-save");
  saveButton.disabled = true;
  try {
    let saved;
    const previous = state.editingId
      ? state.apps.find((item) => item.id === state.editingId) || null
      : null;
    if (state.editingId) {
      saved = await api("/api/applications/" + state.editingId, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      toast("Job updated");
    } else {
      try {
        saved = await api("/api/applications", { method: "POST", body: JSON.stringify(data) });
      } catch (duplicate) {
        const matches = duplicate.status === 409 && duplicate.data ? duplicate.data.matches || [] : [];
        if (!matches.length) throw duplicate;
        const choice = await askDuplicates({
          title: "Already on your list",
          text:
            matches.length === 1
              ? "This company and role are already saved. Open that job, or add a second copy."
              : matches.length + " jobs with this company and role are already saved. Open one, or add a second copy.",
          matches: matches,
          openLabel: "Open job",
        });
        if (choice === "open") {
          const job = matches[0];
          closeModal();
          setTab(job.phase === "to_apply" ? "to_apply" : "applied");
          openDrawer(job.id);
          return;
        }
        if (choice !== "add") return;
        saved = await api("/api/applications", {
          method: "POST",
          body: JSON.stringify(Object.assign({}, data, { force: true })),
        });
      }
      toast(saved.phase === "to_apply" ? "Job saved to apply" : "Application added");
    }
    updateLocal(saved);
    closeModal();
    if (state.drawerId === saved.id) openDrawer(saved.id);
    if (state.tab === "overview" || state.tab === "trash") {
      setTab(saved.phase === "to_apply" ? "to_apply" : "applied");
    }
    promptRejectFiles(saved, previous);
  } catch (submitError) {
    error.textContent = submitError.message;
    error.classList.remove("is-warning");
    error.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
}


export async function extractFromLink() {
  const input = $("#extract-url");
  const url = input.value.trim();
  if (!url) {
    setPanelNote("#extract-status", "Paste a job posting link first.", "error");
    input.focus();
    return;
  }
  const button = $("#extract-btn");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Reading…";
  setPanelNote("#extract-status", "Fetching the posting…", "ok");
  try {
    const result = await api("/api/extract", { method: "POST", body: JSON.stringify({ url: url }) });
    const form = $("#app-form");
    const fields = result.fields || {};
    Object.keys(fields).forEach((key) => {
      const field = form.elements.namedItem(key);
      if (!field) return;
      const value = fields[key];
      if (value == null || value === "") return;
      if (typeof field.value === "string" && field.value.trim()) return;
      field.value = value;
    });
    const appliedField = form.elements.namedItem("applied_on");
    if (!appliedField.value) appliedField.value = todayISO();
    const warnings = result.warnings || [];
    setModalMode("manual");
    $("#modal-content").scrollTop = 0;
    if (warnings.length) {
      const box = $("#form-error");
      box.textContent = warnings.join(" ");
      box.classList.add("is-warning");
      box.hidden = false;
    }
    toast(warnings.length ? "Details filled — review the fields" : "Details filled from the posting");
  } catch (error) {
    setPanelNote("#extract-status", error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}


/* Shared plumbing for the small dialogs below: one backdrop at a time, Escape
   closes, and the page picks up scrolling again when the last one is gone. */
function openDialog(template, onKey) {
  const fragment = template.content.cloneNode(true);
  const backdrop = fragment.querySelector(".picker-backdrop");
  let closed = false;
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", handler, true);
    // Another dialog may still be open behind this one.
    if (
      $("#profiles-backdrop").hidden &&
      $("#settings-backdrop").hidden &&
      $("#modal-backdrop").hidden
    ) {
      document.body.classList.remove("no-scroll");
    }
  };
  function handler(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
      onKey(null);
    }
  }
  document.body.appendChild(fragment);
  document.body.classList.add("no-scroll");
  document.addEventListener("keydown", handler, true);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) {
      close();
      onKey(null);
    }
  });
  return {
    backdrop,
    finish(value) {
      if (closed) return;
      closed = true;
      close();
      onKey(value);
    },
  };
}


/* Pick one of the files saved with your resumes. Resolves with
   { file, kind, replace }, or null when the dialog is dismissed. */
export function chooseSavedFile(config) {
  const settings = config || {};
  const template = $("#file-picker-template");
  if (!template) return Promise.resolve(null);
  const files = Array.isArray(settings.files) ? settings.files : [];
  // What the job already has, so a taken slot can offer to replace instead.
  const existing = Array.isArray(settings.existing) ? settings.existing : [];
  const kindWord = (value) =>
    value === "resume" ? "resume" : value === "cover_letter" ? "cover letter" : "file";

  return new Promise((resolve) => {
    const dialog = openDialog(template, resolve);
    const backdrop = dialog.backdrop;
    const input = backdrop.querySelector(".picker-input");
    const list = backdrop.querySelector(".picker-list");
    const empty = backdrop.querySelector(".picker-empty");
    const replaceRow = backdrop.querySelector(".picker-replace");
    const replaceInput = backdrop.querySelector(".picker-replace-input");
    const replaceWhat = backdrop.querySelector(".picker-replace-what");
    let kind = settings.kind || "resume";

    const syncReplace = () => {
      const taken = existing.filter((doc) => (doc.kind || "other") === kind);
      replaceRow.hidden = !taken.length;
      if (!taken.length) return;
      const count = taken.length > 1 ? taken.length + " " + kindWord(kind) + "s" : "a " + kindWord(kind);
      replaceWhat.textContent =
        count + " already on this job (" + taken[0].filename + ") — replace it instead of adding another copy";
    };

    const renderRows = () => {
      const query = input.value.trim().toLowerCase();
      const matches = files.filter(
        (file) =>
          !query ||
          (file.filename + " " + (file.resume_name || "") + " " + (file.job_company || ""))
            .toLowerCase()
            .includes(query)
      );
      empty.textContent = files.length
        ? "No saved file matches that search."
        : "Nothing saved yet — compile a resume, press Save PDF, then come back.";
      empty.hidden = Boolean(matches.length);
      list.innerHTML = matches
        .map((file) => {
          const where = file.job_company
            ? file.job_company + (file.job_role ? " · " + file.job_role : "")
            : file.resume_name || "";
          return (
            '<li><button type="button" class="picker-item" data-picker-file="' + file.id + '">' +
            '<span class="picker-item-name">' + esc(file.filename) + "</span>" +
            '<span class="picker-item-sub">' + esc(where) + " · " +
            esc(formatSize(file.size)) + "</span>" +
            "</button></li>"
          );
        })
        .join("");
    };

    backdrop.querySelector(".picker-title").textContent =
      settings.title || "Choose a saved file";
    const text = backdrop.querySelector(".picker-text");
    text.textContent = settings.text || "";
    text.hidden = !settings.text;
    backdrop.querySelector(".picker-cancel").addEventListener("click", () => dialog.finish(null));
    backdrop.querySelectorAll("[data-picker-kind]").forEach((button) => {
      button.addEventListener("click", () => {
        kind = button.dataset.pickerKind;
        backdrop.querySelectorAll("[data-picker-kind]").forEach((other) => {
          other.classList.toggle("is-active", other === button);
        });
        syncReplace();
      });
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-picker-file]");
      if (!button) return;
      const file = files.find((item) => String(item.id) === button.dataset.pickerFile);
      if (file) {
        dialog.finish({
          file: file,
          kind: kind,
          replace: !replaceRow.hidden && replaceInput.checked,
        });
      }
    });
    input.addEventListener("input", renderRows);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const first = list.querySelector("[data-picker-file]");
      if (first) first.click();
    });

    renderRows();
    syncReplace();
    window.setTimeout(() => input.focus(), 30);
  });
}


/* Ask for a name. Resolves with the trimmed text, or null when cancelled. */
export function promptName(config) {
  const settings = config || {};
  const template = $("#name-prompt-template");
  if (!template) return Promise.resolve(null);

  return new Promise((resolve) => {
    const dialog = openDialog(template, resolve);
    const backdrop = dialog.backdrop;
    const field = backdrop.querySelector(".prompt-input");
    backdrop.querySelector(".prompt-title").textContent = settings.title || "Name this file";
    const text = backdrop.querySelector(".prompt-text");
    text.textContent = settings.text || "";
    text.hidden = !settings.text;
    backdrop.querySelector(".prompt-label").textContent = settings.label || "Name";
    field.value = settings.value || "";
    backdrop.querySelector(".prompt-cancel").addEventListener("click", () => dialog.finish(null));
    backdrop.querySelector("form").addEventListener("submit", (event) => {
      event.preventDefault();
      const value = field.value.trim();
      if (!value) {
        field.focus();
        return;
      }
      dialog.finish(value);
    });
    window.setTimeout(() => {
      field.focus();
      const dot = field.value.lastIndexOf(".");
      if (dot > 0) field.setSelectionRange(0, dot);
      else field.select();
    }, 30);
  });
}


/* A job (or a file of jobs) matches one already on the list. Resolves with
   "open" (the existing job), "skip" (import the rest), "add" (a second copy),
   or null when the user backs out. */
export function askDuplicates(config) {
  const settings = config || {};
  const template = $("#duplicate-prompt-template");
  if (!template) return Promise.resolve(null);
  const matches = Array.isArray(settings.matches) ? settings.matches : [];

  return new Promise((resolve) => {
    const dialog = openDialog(template, resolve);
    const backdrop = dialog.backdrop;
    backdrop.querySelector(".dup-title").textContent = settings.title || "Already on your list";
    backdrop.querySelector(".dup-text").textContent = settings.text || "";
    const list = backdrop.querySelector(".dup-list");
    list.innerHTML = matches
      .map((job) => {
        const phase = job.phase === "to_apply" ? "To apply" : "Applied";
        return (
          '<li class="dup-item"><span class="dup-item-name">' + esc(job.company) + "</span>" +
          '<span class="dup-item-sub">' + esc(job.role) + " · " + phase + "</span></li>"
        );
      })
      .join("");
    list.hidden = !matches.length;

    const openButton = backdrop.querySelector(".dup-open");
    const skipButton = backdrop.querySelector(".dup-skip");
    const addButton = backdrop.querySelector(".dup-add");
    openButton.hidden = !settings.openLabel;
    openButton.textContent = settings.openLabel || "Open it";
    skipButton.hidden = !settings.skipLabel;
    skipButton.textContent = settings.skipLabel || "Skip duplicates";
    addButton.textContent = settings.addLabel || "Add anyway";

    backdrop.querySelector(".dup-cancel").addEventListener("click", () => dialog.finish(null));
    openButton.addEventListener("click", () => dialog.finish("open"));
    skipButton.addEventListener("click", () => dialog.finish("skip"));
    addButton.addEventListener("click", () => dialog.finish("add"));
    window.setTimeout(() => addButton.focus(), 30);
  });
}


/* A file is going into a slot that already holds one. Resolves with "replace",
   "keep" (attach a second copy) or null when the upload is cancelled. */
export function askReplace(config) {
  const settings = config || {};
  const template = $("#replace-prompt-template");
  if (!template) return Promise.resolve(null);

  return new Promise((resolve) => {
    const dialog = openDialog(template, resolve);
    const backdrop = dialog.backdrop;
    backdrop.querySelector(".replace-title").textContent =
      settings.title || "Replace the existing document?";
    backdrop.querySelector(".replace-text").textContent = settings.text || "";
    backdrop.querySelector(".replace-cancel").addEventListener("click", () => dialog.finish(null));
    backdrop.querySelector(".replace-keep").addEventListener("click", () => dialog.finish("keep"));
    backdrop.querySelector(".replace-ok").addEventListener("click", () => dialog.finish("replace"));
    window.setTimeout(() => backdrop.querySelector(".replace-ok").focus(), 30);
  });
}


/* Save the compiled PDF and put it where it is going to be used. The name and
   the job sit in one dialog, so the usual trip — save, switch tab, open the job,
   open its Documents, pick the file again — is one step. Resolves with
   { name, kind, jobId, replace } (jobId null when the file is only saved), or null. */
export function savePdfDialog(config) {
  const settings = config || {};
  const template = $("#save-pdf-template");
  if (!template) return Promise.resolve(null);
  const jobs = Array.isArray(settings.jobs) ? settings.jobs : [];
  const linkedId = settings.linkedId == null ? null : settings.linkedId;
  const documents = settings.documents || {};
  const docsFor = (job) => documents[String(job.id)] || [];
  const kindLabel = (value) =>
    value === "resume" ? "resume" : value === "cover_letter" ? "cover letter" : "file";

  return new Promise((resolve) => {
    const dialog = openDialog(template, resolve);
    const backdrop = dialog.backdrop;
    const form = backdrop.querySelector("form");
    const nameField = backdrop.querySelector(".savepdf-name");
    const search = backdrop.querySelector(".savepdf-search");
    const list = backdrop.querySelector(".savepdf-jobs");
    const empty = backdrop.querySelector(".savepdf-empty");
    const okButton = backdrop.querySelector(".savepdf-ok");
    const replaceInput = backdrop.querySelector(".savepdf-replace-input");
    let kind = settings.kind || "resume";
    let jobId = linkedId;
    let replace = true;

    nameField.value = settings.filename || "";
    backdrop.querySelector(".savepdf-text").textContent =
      settings.text ||
      "It is kept with this resume's files. Pick the job it belongs to and it is attached there too.";

    const docSummary = (docs) => {
      const counts = new Map();
      docs.forEach((doc) => {
        const key = doc.kind || "other";
        counts.set(key, (counts.get(key) || 0) + 1);
      });
      return [...counts.entries()]
        .map(([key, count]) => (DOC_LABEL[key] || "Other") + (count > 1 ? " ×" + count : ""))
        .join(" · ");
    };

    const rowHtml = (job) => {
      const selected = String(job.id) === String(jobId) ? " is-selected" : "";
      const phase = job.phase === "to_apply" ? "To apply" : "Applied";
      const docs = docsFor(job);
      return (
        '<li><button type="button" class="picker-item' + selected + '" data-savepdf-job="' + job.id + '">' +
        '<span class="picker-item-name">' + esc(job.company) + "</span>" +
        '<span class="picker-item-sub">' + esc(job.role) + " · " + phase + "</span>" +
        '<span class="picker-item-docs' + (docs.length ? "" : " is-empty") + '">' +
        esc(docs.length ? docSummary(docs) : "No documents yet") +
        "</span></button></li>"
      );
    };

    const existingFor = (job, value) =>
      job ? docsFor(job).filter((doc) => (doc.kind || "other") === value) : [];

    // Attaching adds by default, but a new version of the same resume is the
    // usual reason to save one, so a taken slot offers to replace instead.
    const syncNote = () => {
      const note = backdrop.querySelector(".savepdf-note");
      const job = jobs.find((item) => String(item.id) === String(jobId));
      const existing = existingFor(job, kind);
      note.hidden = !existing.length;
      if (!existing.length) return;
      note.querySelector(".savepdf-note-text").textContent =
        "Already has " +
        (existing.length === 1
          ? "a " + kindLabel(kind) + " (" + existing[0].filename + ")"
          : existing.length + " " + kindLabel(kind) + "s") +
        " on this job.";
    };

    const syncOk = () => {
      const job = jobs.find((item) => String(item.id) === String(jobId));
      const replacing = Boolean(job) && replace && existingFor(job, kind).length > 0;
      okButton.disabled = !job;
      okButton.textContent = replacing ? "Save & replace" : "Save & attach";
    };

    const renderJobs = () => {
      const query = search.value.trim().toLowerCase();
      const matches = jobs.filter(
        (job) => !query || (job.company + " " + job.role).toLowerCase().includes(query)
      );
      empty.textContent = jobs.length
        ? "No job matches that search."
        : "No jobs yet — save the file here and attach it later from a job's Documents section.";
      empty.hidden = Boolean(matches.length);
      list.innerHTML = matches.map(rowHtml).join("");
      syncNote();
      syncOk();
    };

    const finishWith = (attach) => {
      const value = nameField.value.trim();
      if (!value) {
        nameField.focus();
        return;
      }
      const job = jobs.find((item) => String(item.id) === String(jobId));
      const replacing = Boolean(attach && replace && job && existingFor(job, kind).length);
      dialog.finish({
        name: value,
        kind: kind,
        jobId: attach ? jobId : null,
        replace: replacing,
      });
    };

    backdrop.querySelectorAll("[data-savepdf-kind]").forEach((button) => {
      const active = button.dataset.savepdfKind === kind;
      button.classList.toggle("is-active", active);
      button.addEventListener("click", () => {
        kind = button.dataset.savepdfKind;
        backdrop.querySelectorAll("[data-savepdf-kind]").forEach((other) => {
          other.classList.toggle("is-active", other === button);
        });
        syncNote();
        syncOk();
      });
    });
    replaceInput.addEventListener("change", () => {
      replace = replaceInput.checked;
      syncOk();
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-savepdf-job]");
      if (!button) return;
      const job = jobs.find((item) => String(item.id) === button.dataset.savepdfJob);
      if (!job) return;
      jobId = job.id;
      backdrop.querySelectorAll("[data-savepdf-job]").forEach((other) => {
        other.classList.toggle("is-selected", other === button);
      });
      syncNote();
      syncOk();
      okButton.focus();
    });
    search.addEventListener("input", renderJobs);
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const first = list.querySelector("[data-savepdf-job]");
      if (first) first.click();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      finishWith(true);
    });
    backdrop.querySelector(".savepdf-cancel").addEventListener("click", () => dialog.finish(null));
    backdrop.querySelector(".savepdf-only").addEventListener("click", () => finishWith(false));

    renderJobs();
    window.setTimeout(() => {
      nameField.focus();
      const dot = nameField.value.lastIndexOf(".");
      if (dot > 0) nameField.setSelectionRange(0, dot);
      else nameField.select();
    }, 30);
  });
}


export function confirmAction(config) {
  const template = $("#confirm-template");
  const fragment = template.content.cloneNode(true);
  const backdrop = fragment.querySelector(".confirm-backdrop");
  const okButton = fragment.querySelector(".confirm-ok");
  fragment.querySelector(".confirm-title").textContent = config.title;
  fragment.querySelector(".confirm-text").textContent = config.text;
  okButton.textContent = config.okLabel || "Confirm";
  okButton.classList.toggle("btn-danger", config.danger !== false);
  let busy = false;
  const cleanup = () => {
    backdrop.remove();
    // Another dialog may still be open behind this one.
    if (
      $("#profiles-backdrop").hidden &&
      $("#settings-backdrop").hidden &&
      $("#modal-backdrop").hidden
    ) {
      document.body.classList.remove("no-scroll");
    }
  };
  fragment.querySelector(".confirm-cancel").textContent = config.cancelLabel || "Cancel";
  fragment.querySelector(".confirm-cancel").addEventListener("click", cleanup);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) cleanup();
  });
  okButton.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    okButton.disabled = true;
    try {
      await config.onConfirm();
      cleanup();
    } catch (error) {
      busy = false;
      okButton.disabled = false;
      toast(error.message || "Something went wrong", true);
    }
  });
  document.body.appendChild(fragment);
  document.body.classList.add("no-scroll");
  okButton.focus();
}


export function confirmTrash(app) {
  confirmAction({
    title: "Move to trash?",
    text:
      app.company + " — " + app.role +
      " will move to the trash. You can restore it from the Trash tab; nothing is deleted permanently yet.",
    okLabel: "Move to trash",
    onConfirm: async () => {
      const result = await api("/api/applications/" + app.id, { method: "DELETE" });
      const trashed = result && result.application ? result.application : app;
      state.apps = state.apps.filter((item) => item.id !== app.id);
      state.trash = [trashed].concat(state.trash.filter((item) => item.id !== app.id));
      render();
      if (state.drawerId === app.id) closeDrawer();
      toast("Moved to trash");
    },
  });
}


export function confirmEmptyTrash() {
  if (!state.trash.length) return;
  const count = state.trash.length;
  confirmAction({
    title: "Empty the trash?",
    text:
      count + " deleted job" + (count === 1 ? "" : "s") +
      " and all of their attached documents (resume, cover letter, files) will be removed from the database and disk." +
      " This cannot be undone.",
    okLabel: "Empty trash",
    onConfirm: async () => {
      const result = await api("/api/trash", { method: "DELETE" });
      state.trash = [];
      render();
      const removed = result && result.deleted ? result.deleted : count;
      toast("Trash emptied — " + removed + " job" + (removed === 1 ? "" : "s") + " deleted");
    },
  });
}


export function confirmPurge(app) {
  confirmAction({
    title: "Delete permanently?",
    text:
      app.company + " — " + app.role +
      ", its notes, timeline and all attached documents (resume, cover letter, files) will be removed from the database and disk. This cannot be undone.",
    okLabel: "Delete forever",
    onConfirm: async () => {
      await api("/api/applications/" + app.id + "?permanent=1", { method: "DELETE" });
      state.trash = state.trash.filter((item) => item.id !== app.id);
      render();
      toast("Permanently deleted");
    },
  });
}
