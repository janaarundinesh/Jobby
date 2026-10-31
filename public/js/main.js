/* Jobby — Event wiring and boot.
   ES module, no build step. Open the dashboard through server.py. */

import { $, $$, state, statusSelectOptions, toast, todayISO, tokenFor } from "./core.js";
import { attachSavedFileToJob, downloadDocument, bulkDelete, bulkMarkApplied, bulkSetStatus, closePosition, deleteDocument, loadApps, loadProfiles, markApplied, markReviewed, patchStatus, persistProfile, renderProfileChrome, restoreJob, uploadDocuments, visibleApps } from "./data.js";
import { closeModal, confirmEmptyTrash, confirmPurge, confirmTrash, extractFromLink, openModal, setModalMode, submitForm, syncPhaseFields } from "./dialogs.js";
import { closeDrawer, closePreview, openDrawer, openPreview } from "./drawer.js";
import { exportCSV, exportJSON, handleImportFile } from "./io.js";
import { bindResumeEvents, openResumeForJob } from "./resume.js";
import { closeNewProfile, closeProfiles, closeSettings, closeUnlock, createNewProfile, deleteCurrentProfile, downloadBackup, enterProfile, hideWelcome, installEngine, lockProfile, logoutProfile, openNewProfile, openProfiles, openSettings, refreshEngineStatus, removePin, savePin, saveSettings, selectProfile, showSettingsView, showWelcome, submitUnlock } from "./profiles.js";
import { clearFilters, render, renderViewSwitch, renderWorkspace, setTab, syncFilterOptions } from "./workspace.js";
import { installFocusManagement } from "./a11y.js";
import { disableReminders, enableReminders, renderReminderSettings } from "./reminders.js";
import { registerServiceWorker, showOfflineScreen, syncThemeColor } from "./pwa.js";
import { startServerWatch } from "./status.js";


export function bindEvents() {
  bindResumeEvents();

  // A tablist answers the arrow keys as well as the mouse.
  const tabs = $$(".tab");
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => setTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = tabs[(index + step + tabs.length) % tabs.length];
      setTab(next.dataset.tab);
      next.focus();
    });
  });

  $$(".view-btn").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      localStorage.setItem("jobpipe:view", state.view);
      renderViewSwitch();
      renderWorkspace();
    });
  });

  $("#new-btn").addEventListener("click", () => openModal(null));
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#modal-backdrop").addEventListener("click", (event) => {
    if (event.target === $("#modal-backdrop")) closeModal();
  });
  $("#app-form").addEventListener("submit", submitForm);
  $("#form-phase").addEventListener("change", syncPhaseFields);

  $$(".mode-btn").forEach((button) => {
    button.addEventListener("click", () => {
      setModalMode(button.dataset.mode);
      if (button.dataset.mode === "link") window.setTimeout(() => $("#extract-url").focus(), 40);
    });
  });

  $("#extract-btn").addEventListener("click", extractFromLink);
  $("#extract-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      extractFromLink();
    }
  });

  $("#modal-import-file").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    handleImportFile(file, { closeModal: true, statusSelector: "#import-status" });
    event.target.value = "";
  });

  const dropzone = $("#modal-drop");
  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleImportFile(file, { closeModal: true, statusSelector: "#import-status" });
  });

  document.addEventListener("click", (event) => {
    const editButton = event.target.closest('[data-action="edit"]');
    if (editButton) {
      event.stopPropagation();
      const app = state.apps.find((item) => item.id === Number(editButton.dataset.id));
      if (app) openModal(app);
      return;
    }
    const deleteButton = event.target.closest('[data-action="delete"]');
    if (deleteButton) {
      event.stopPropagation();
      const app = state.apps.find((item) => item.id === Number(deleteButton.dataset.id));
      if (app) confirmTrash(app);
      return;
    }
    const reviewedButton = event.target.closest('[data-action="mark-reviewed"]');
    if (reviewedButton) {
      event.stopPropagation();
      markReviewed(Number(reviewedButton.dataset.id));
      return;
    }
    const closePositionButton = event.target.closest('[data-action="close-position"]');
    if (closePositionButton) {
      event.stopPropagation();
      closePosition(Number(closePositionButton.dataset.id));
      return;
    }
    const restoreButton = event.target.closest('[data-action="restore"]');
    if (restoreButton) {
      event.stopPropagation();
      restoreJob(Number(restoreButton.dataset.id));
      return;
    }
    const purgeButton = event.target.closest('[data-action="purge"]');
    if (purgeButton) {
      event.stopPropagation();
      const trashed = state.trash.find((item) => item.id === Number(purgeButton.dataset.id));
      if (trashed) confirmPurge(trashed);
      return;
    }
    const markButton = event.target.closest('[data-action="mark-applied"]');
    if (markButton) {
      event.stopPropagation();
      markApplied(Number(markButton.dataset.id));
      return;
    }
    const deleteDocButton = event.target.closest("[data-doc-delete]");
    if (deleteDocButton) {
      event.stopPropagation();
      if (state.drawerId) deleteDocument(state.drawerId, Number(deleteDocButton.dataset.docDelete));
      return;
    }
    if (event.target.closest('[data-action="pick-saved"]')) {
      event.stopPropagation();
      if (state.drawerId) attachSavedFileToJob(state.drawerId);
      return;
    }
    const downloadDocButton = event.target.closest("[data-doc-download]");
    if (downloadDocButton) {
      event.stopPropagation();
      downloadDocument(
        state.drawerId,
        Number(downloadDocButton.dataset.docDownload)
      );
      return;
    }
    const previewDocButton = event.target.closest("[data-preview-doc]");
    if (previewDocButton) {
      event.stopPropagation();
      const doc = state.drawerDocuments.find(
        (item) => item.id === Number(previewDocButton.dataset.previewDoc)
      );
      openPreview(doc);
      return;
    }
    if (event.target.closest('[data-action="close-drawer"]')) {
      closeDrawer();
      return;
    }
    if (event.target.closest('[data-action="new"]')) {
      openModal(null);
      return;
    }
    const settingsLink = event.target.closest('[data-action="open-settings"]');
    if (settingsLink) {
      // Set goals opens the goals panel itself rather than the menu behind it.
      openSettings(settingsLink.dataset.openSettings);
      return;
    }
    if (event.target.closest('[data-action="go-to-apply"]')) {
      setTab("to_apply");
      return;
    }
    if (event.target.closest('[data-action="clear-filters"]')) {
      clearFilters();
      return;
    }
    const followButton = event.target.closest("[data-open-app]");
    if (followButton) {
      openDrawer(Number(followButton.dataset.openApp));
      return;
    }
    const row = event.target.closest("tr[data-app-id]");
    if (row && !event.target.closest("select, button, a, input, textarea")) {
      openDrawer(Number(row.dataset.appId));
      return;
    }
    const card = event.target.closest(".app-card");
    if (card) {
      openDrawer(Number(card.dataset.appId));
    }
  });

  // A job's resume button opens (or creates) that job's tailored copy.
  document.addEventListener("click", async (event) => {
    const resumeButton = event.target.closest('[data-action="open-resume"]');
    if (!resumeButton) return;
    event.stopPropagation();
    const app = state.apps.find((item) => item.id === Number(resumeButton.dataset.id));
    if (!app) return;
    const doc = await openResumeForJob(app);
    if (!doc) return;
    closeDrawer();
    setTab("resume");
  });

  document.addEventListener("keydown", (event) => {
    const isTyping = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (event.key === "Escape") {
      if (!$("#newprofile-backdrop").hidden) closeNewProfile();
      else if (!$("#unlock-backdrop").hidden) closeUnlock();
      else if (!$("#profiles-backdrop").hidden) closeProfiles();
      else if (!$("#settings-backdrop").hidden) {
        if (state.settingsView !== "menu") showSettingsView("menu");
        else closeSettings();
      }
      else if (!$("#preview-backdrop").hidden) closePreview();
      else if (!$("#modal-backdrop").hidden) closeModal();
      else if (!$(".confirm-backdrop")) {
        if (state.drawerId) closeDrawer();
      }
      return;
    }
    if (isTyping) return;
    if (event.key === "/") {
      event.preventDefault();
      if (state.tab === "overview" || state.tab === "trash") setTab("applied");
      $("#search").focus();
    } else if (event.key === "n" || event.key === "N") {
      if (event.metaKey || event.ctrlKey || state.tab === "resume") return;
      event.preventDefault();
      openModal(null);
    }
  });

  $("#drawer-scrim").addEventListener("click", closeDrawer);

  $("#preview-close").addEventListener("click", closePreview);
  $("#preview-backdrop").addEventListener("click", (event) => {
    if (event.target.id === "preview-backdrop") closePreview();
  });

  $("#welcome-list").addEventListener("click", (event) => {
    if (event.target.closest("#welcome-add")) {
      openNewProfile();
      return;
    }
    const card = event.target.closest("[data-enter-profile]");
    if (card) enterProfile(Number(card.dataset.enterProfile));
  });

  $("#profiles-add").addEventListener("click", openNewProfile);
  $("#newprofile-form").addEventListener("submit", createNewProfile);
  $("#newprofile-close").addEventListener("click", closeNewProfile);
  $("#newprofile-cancel").addEventListener("click", closeNewProfile);
  $("#newprofile-backdrop").addEventListener("click", (event) => {
    if (event.target.id === "newprofile-backdrop") closeNewProfile();
  });
  $("#settings-delete-profile").addEventListener("click", deleteCurrentProfile);

  $("#unlock-form").addEventListener("submit", submitUnlock);
  $("#unlock-close").addEventListener("click", closeUnlock);
  $("#unlock-cancel").addEventListener("click", closeUnlock);
  $("#unlock-switch").addEventListener("click", () => {
    $("#unlock-backdrop").hidden = true;
    state.unlockId = null;
    openProfiles();
  });
  $("#unlock-backdrop").addEventListener("click", (event) => {
    if (event.target.id === "unlock-backdrop") closeUnlock();
  });
  $("#settings-pin-save").addEventListener("click", savePin);
  $("#settings-pin-remove").addEventListener("click", removePin);
  $("#settings-backup-download").addEventListener("click", (event) =>
    downloadBackup(event.currentTarget)
  );

  $("#settings-reminders-on").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await enableReminders();
      renderReminderSettings();
      toast("Reminders turned on");
    } catch (error) {
      const box = $("#settings-reminders-error");
      box.textContent = error.message;
      box.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  $("#settings-reminders-off").addEventListener("click", () => {
    disableReminders();
    renderReminderSettings();
    toast("Reminders turned off");
  });

  $("#profile-btn").addEventListener("click", openProfiles);
  $("#profiles-close").addEventListener("click", closeProfiles);
  $("#profiles-done").addEventListener("click", closeProfiles);
  $("#profiles-backdrop").addEventListener("click", (event) => {
    if (event.target.id === "profiles-backdrop") closeProfiles();
  });
  $("#profile-list").addEventListener("click", (event) => {
    const lockNow = event.target.closest("[data-lock-profile]");
    if (lockNow) {
      const profile = state.profiles.find(
        (item) => item.id === Number(lockNow.dataset.lockProfile)
      );
      lockProfile(profile);
      return;
    }
    const pick = event.target.closest("[data-select-profile]");
    if (pick) selectProfile(Number(pick.dataset.selectProfile));
  });

  $("#settings-btn").addEventListener("click", () => openSettings());
  $("#settings-back").addEventListener("click", () => showSettingsView("menu"));
  $("#settings-menu").addEventListener("click", (event) => {
    const action = event.target.closest("[data-settings-action]");
    if (action && action.dataset.settingsAction === "logout") {
      logoutProfile();
      return;
    }
    const row = event.target.closest("[data-settings-view]");
    if (row) showSettingsView(row.dataset.settingsView);
  });
  $("#settings-engine-install").addEventListener("click", (event) =>
    installEngine(event.currentTarget)
  );
  $("#settings-engine-recheck").addEventListener("click", () => refreshEngineStatus());
  $("#settings-close").addEventListener("click", closeSettings);
  $("#settings-cancel").addEventListener("click", closeSettings);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#empty-trash-btn").addEventListener("click", confirmEmptyTrash);
  $("#settings-backdrop").addEventListener("click", (event) => {
    if (event.target.id === "settings-backdrop") closeSettings();
  });

  const drawer = $("#drawer");

  const clearDropTarget = (zone) => {
    zone.classList.remove("is-dragover");
    $$(".doc-upload", zone).forEach((button) => button.classList.remove("is-dragover"));
  };

  drawer.addEventListener("dragover", (event) => {
    const zone = event.target.closest("[data-doc-drop]");
    if (!zone || !event.dataTransfer) return;
    const types = Array.from(event.dataTransfer.types || []);
    if (types.length && !types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    zone.classList.add("is-dragover");
    const target = event.target.closest("[data-drop-kind]");
    $$(".doc-upload", zone).forEach((button) =>
      button.classList.toggle("is-dragover", button === target)
    );
  });

  drawer.addEventListener("dragleave", (event) => {
    const zone = event.target.closest("[data-doc-drop]");
    if (!zone || zone.contains(event.relatedTarget)) return;
    clearDropTarget(zone);
  });

  drawer.addEventListener("drop", (event) => {
    const zone = event.target.closest("[data-doc-drop]");
    if (!zone) return;
    event.preventDefault();
    clearDropTarget(zone);
    if (!state.drawerId) return;
    const target = event.target.closest("[data-drop-kind]");
    const files = event.dataTransfer ? Array.from(event.dataTransfer.files || []) : [];
    if (!files.length) return;
    uploadDocuments(state.drawerId, target ? target.dataset.dropKind : null, files);
  });

  drawer.addEventListener("change", (event) => {
    const select = event.target.closest("[data-drawer-status]");
    if (select) {
      patchStatus(Number(select.dataset.drawerStatus), select.value);
      return;
    }
    const upload = event.target.closest("[data-doc-kind]");
    if (upload && upload.files && upload.files.length) {
      const files = Array.from(upload.files);
      upload.value = "";
      uploadDocuments(state.drawerId, upload.dataset.docKind, files);
    }
  });

  $("#applied-table-body").addEventListener("change", (event) => {
    const box = event.target.closest("[data-select-id]");
    if (box) {
      const id = Number(box.dataset.selectId);
      if (box.checked) state.selection.add(id);
      else state.selection.delete(id);
      renderWorkspace();
      return;
    }
    const select = event.target.closest("[data-inline-status]");
    if (select) patchStatus(Number(select.dataset.inlineStatus), select.value);
  });

  $("#toapply-table-body").addEventListener("change", (event) => {
    const box = event.target.closest("[data-select-id]");
    if (!box) return;
    const id = Number(box.dataset.selectId);
    if (box.checked) state.selection.add(id);
    else state.selection.delete(id);
    renderWorkspace();
  });

  [["#select-all-toapply", "to_apply"], ["#select-all-applied", "applied"]].forEach(([selector, phase]) => {
    $(selector).addEventListener("change", (event) => {
      const ids = visibleApps(phase).map((app) => app.id);
      if (event.target.checked) ids.forEach((id) => state.selection.add(id));
      else ids.forEach((id) => state.selection.delete(id));
      renderWorkspace();
    });
  });

  $("#bulk-apply").addEventListener("click", bulkMarkApplied);
  $("#bulk-status").addEventListener("change", (event) => {
    $("#bulk-stage-apply").disabled = !event.target.value;
  });
  $("#bulk-stage-apply").addEventListener("click", bulkSetStatus);
  $("#bulk-delete").addEventListener("click", bulkDelete);
  $("#bulk-clear").addEventListener("click", () => {
    state.selection.clear();
    renderWorkspace();
  });

  $("#applied-table-body").addEventListener("keydown", (event) => {
    if (event.target.matches("select, button")) return;
    if (event.key === "Enter" && event.target.closest("tr")) {
      openDrawer(Number(event.target.closest("tr").dataset.appId));
    }
  });

  $("#toapply-table-body").addEventListener("keydown", (event) => {
    if (event.target.matches("select, button")) return;
    if (event.key === "Enter" && event.target.closest("tr")) {
      openDrawer(Number(event.target.closest("tr").dataset.appId));
    }
  });

  // Column headers sort by mouse and by keyboard; aria-sort carries the state.
  $$("th.sortable").forEach((th) => {
    th.tabIndex = 0;
    th.setAttribute("role", "button");
    const activate = () => {
      const key = th.dataset.sort;
      const phase = state.tab === "to_apply" ? "to_apply" : "applied";
      const sort = state.sort[phase];
      if (sort.key === key) {
        sort.dir = sort.dir === "asc" ? "desc" : "asc";
      } else {
        sort.key = key;
        // Dates and the match estimate read best highest/newest first.
        sort.dir = ["applied_on", "created_at", "match_pct"].includes(key) ? "desc" : "asc";
      }
      renderWorkspace();
    };
    th.addEventListener("click", activate);
    th.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate();
    });
  });

  $("#search").addEventListener("input", (event) => {
    state.filters.q = event.target.value;
    renderWorkspace();
    syncFilterOptions();
  });

  ["status", "mode", "source", "match"].forEach((key) => {
    const select = $("#filter-" + key);
    select.addEventListener("change", (event) => {
      state.filters[key] = event.target.value;
      renderWorkspace();
      syncFilterOptions();
    });
  });

  $("#clear-filters").addEventListener("click", clearFilters);

  const storedTheme = localStorage.getItem("jobpipe:theme");
  const initialTheme =
    storedTheme || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", initialTheme);
  syncThemeColor(initialTheme);
  $("#theme-btn").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("jobpipe:theme", next);
    syncThemeColor(next);
  });

  const menu = $("#data-menu");
  const menuButton = $("#data-menu-btn");
  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = menu.hidden;
    menu.hidden = !willOpen;
    menuButton.setAttribute("aria-expanded", willOpen ? "true" : "false");
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !event.target.closest(".menu-wrap")) {
      menu.hidden = true;
      menuButton.setAttribute("aria-expanded", "false");
    }
  });
  $$("#data-menu [data-export]").forEach((button) => {
    button.addEventListener("click", () => {
      menu.hidden = true;
      if (button.dataset.export === "json") exportJSON();
      else exportCSV();
    });
  });
  $("#import-btn").addEventListener("click", () => {
    menu.hidden = true;
    $("#import-file").click();
  });
  $("#import-file").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    handleImportFile(file);
    event.target.value = "";
  });

  const board = $("#board");
  board.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".app-card");
    if (!card) return;
    event.dataTransfer.setData("text/plain", card.dataset.appId);
    event.dataTransfer.effectAllowed = "move";
    card.classList.add("is-dragging");
  });
  board.addEventListener("dragend", (event) => {
    const card = event.target.closest(".app-card");
    if (card) card.classList.remove("is-dragging");
    $$(".board-col").forEach((column) => column.classList.remove("is-drop-target"));
  });
  board.addEventListener("dragover", (event) => {
    const column = event.target.closest(".board-col");
    if (!column) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    $$(".board-col").forEach((item) => item.classList.toggle("is-drop-target", item === column));
  });
  board.addEventListener("dragleave", (event) => {
    const column = event.target.closest(".board-col");
    if (column && !column.contains(event.relatedTarget)) column.classList.remove("is-drop-target");
  });
  board.addEventListener("drop", (event) => {
    const column = event.target.closest(".board-col");
    if (!column) return;
    event.preventDefault();
    const id = Number(event.dataTransfer.getData("text/plain"));
    const status = column.dataset.dropStatus;
    column.classList.remove("is-drop-target");
    const app = state.apps.find((item) => item.id === id);
    if (app && status && app.status !== status) patchStatus(id, status);
  });
  board.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const card = event.target.closest(".app-card");
    if (card) openDrawer(Number(card.dataset.appId));
  });
}


export function init() {
  installFocusManagement();
  registerServiceWorker();
  // Starts the /api/health heartbeat behind the topbar indicator.
  startServerWatch();
  $("#form-applied-on").value = todayISO();
  $("#form-status").innerHTML = statusSelectOptions("applied", false);
  $("#filter-status").innerHTML = statusSelectOptions("", true);
  bindEvents();
  syncPhaseFields();
  render();
  boot();
}


export async function boot() {
  try {
    await loadProfiles();
  } catch (error) {
    // Served from the app icon while the server is down: offer a way back in.
    if (error.offline && navigator.serviceWorker && navigator.serviceWorker.controller) {
      showOfflineScreen();
      return;
    }
    toast(error.message, true);
  }
  // The picker is the first screen of every browser session; a reload of the
  // same tab stays inside the profile it was opened with.
  let session = NaN;
  try {
    session = Number(sessionStorage.getItem("jobpipe:session"));
  } catch (error) {
    session = NaN;
  }
  const current = state.profiles.find((profile) => profile.id === session);    if (current && (!current.locked || tokenFor(current.id))) {
      state.profileId = current.id;
      persistProfile();
      // This session opens inside the profile, so the gate comes down: the shell
      // in the page's own markup is gated until boot decides.
      hideWelcome();
      renderProfileChrome();
      await loadApps();
  } else {
    showWelcome();
  }
}
// This module is injected dynamically, so it can run after parsing is done:
// only wait for the event when the document is still loading.
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
