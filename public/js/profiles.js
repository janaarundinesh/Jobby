/* Jobby — User picker, profile switching, PIN lock and Settings.
   ES module, no build step. Open the dashboard through server.py. */

import { $, $$, DEFAULT_GOALS, ICON, api, clearToken, esc, formatSize, sessionToken, setToken, state, todayISO, toast, tokenFor } from "./core.js";
import { activeProfile, initials, loadApps, persistProfile, profileNeedsUnlock, rememberSession, renderProfileChrome } from "./data.js";
import { confirmAction } from "./dialogs.js";
import { closeDrawer } from "./drawer.js";
import { remindersOn, renderReminderSettings } from "./reminders.js";
import { render } from "./workspace.js";


export function renderProfilesList() {
  const list = $("#profile-list");
  if (!state.profiles.length) {
    list.innerHTML = '<li class="profile-row"><span class="profile-row-meta">No profiles yet.</span></li>';
    return;
  }
  list.innerHTML = state.profiles
    .map((profile) => {
      const active = profile.id === state.profileId;
      const open = profile.locked && !!tokenFor(profile.id);
      // Like the picker, this list names profiles and stops there: a job count
      // says nothing about who you are switching to, and for a locked profile
      // it would give away what the PIN is there to guard.
      const meta = profile.locked
        ? "PIN protected"
        : profile.headline || profile.email || "No contact details yet";
      return (
        '<li class="profile-row' + (active ? " is-active" : "") + '">' +
        '<span class="avatar" aria-hidden="true">' + esc(initials(profile.name)) + "</span>" +
        '<button type="button" class="profile-row-main" data-select-profile="' + profile.id + '">' +
        '<span class="profile-row-name">' + esc(profile.name) +
        (active ? '<span class="profile-tag">Current</span>' : "") +
        (profile.locked ? '<span class="profile-lock" title="PIN protected">' + ICON.lock + "</span>" : "") +
        "</span>" +
        '<span class="profile-row-meta">' + esc(meta) + "</span>" +
        "</button>" +
        '<span class="profile-row-actions">' +
        (open
          ? '<button type="button" class="btn btn-icon" data-lock-profile="' + profile.id +
            '" title="Lock now" aria-label="Lock ' + esc(profile.name) + ' now">' + ICON.unlock + "</button>"
          : "") +
        "</span>" +
        "</li>"
      );
    })
    .join("");
}


export function openProfiles() {
  renderProfilesList();
  $("#profiles-backdrop").hidden = false;
  document.body.classList.add("no-scroll");
}


export function closeProfiles() {
  $("#profiles-backdrop").hidden = true;
  if (!$("#newprofile-backdrop").hidden || !$("#settings-backdrop").hidden) return;
  document.body.classList.remove("no-scroll");
}


export function selectProfile(id) {
  if (!id) return;
  const picked = state.profiles.find((profile) => profile.id === id);
  if (picked && picked.locked && !tokenFor(id)) {
    closeProfiles();
    openUnlock(id);
    return;
  }
  if (id === state.profileId) {
    closeProfiles();
    return;
  }
  state.profileId = id;
  persistProfile();
  rememberSession(id);
  state.selection.clear();
  if (state.drawerId) closeDrawer();
  closeProfiles();
  renderProfileChrome();
  render();
  loadApps();
  const profile = activeProfile();
  if (profile) toast("Switched to " + profile.name);
}


export function openNewProfile() {
  const input = $("#newprofile-name");
  input.value = "";
  const error = $("#newprofile-error");
  error.hidden = true;
  error.textContent = "";
  $("#newprofile-backdrop").hidden = false;
  document.body.classList.add("no-scroll");
  window.setTimeout(() => input.focus(), 40);
}


export function closeNewProfile() {
  $("#newprofile-backdrop").hidden = true;
  if (!$("#profiles-backdrop").hidden || !$("#settings-backdrop").hidden) return;
  document.body.classList.remove("no-scroll");
}


export async function createNewProfile(event) {
  event.preventDefault();
  const input = $("#newprofile-name");
  const name = input.value.trim();
  const box = $("#newprofile-error");
  if (!name) {
    box.hidden = false;
    box.textContent = "Give the new profile a name.";
    input.focus();
    return;
  }
  const button = $("#newprofile-create");
  button.disabled = true;
  try {
    const profile = await api("/api/profiles", {
      method: "POST",
      body: JSON.stringify({ name: name }),
    });
    state.profiles.push(Object.assign({ jobs: 0, total_jobs: 0 }, profile));
    closeNewProfile();
    renderWelcome();
    renderProfilesList();
    toast("Profile created — select it to start");
  } catch (error) {
    box.hidden = false;
    box.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}


export function deleteCurrentProfile() {
  const profile = activeProfile();
  if (!profile) return;
  if (state.profiles.length <= 1) {
    toast("Keep at least one profile", true);
    return;
  }
  const jobs = profile.total_jobs != null ? profile.total_jobs : profile.jobs || 0;
  confirmAction({
    title: "Delete " + profile.name + "?",
    text:
      "This profile is removed with " + jobs + " job" + (jobs === 1 ? "" : "s") +
      ", every attached document and its goals. This cannot be undone.",
    okLabel: "Delete profile",
    onConfirm: async () => {
      await api("/api/profiles/" + profile.id, { method: "DELETE" });
      state.profiles = state.profiles.filter((item) => item.id !== profile.id);
      state.profileId = state.profiles.length ? state.profiles[0].id : null;
      persistProfile();
      rememberSession(null);
      clearToken(profile.id);
      state.apps = [];
      state.trash = [];
      state.settings = Object.assign({}, DEFAULT_GOALS);
      state.selection.clear();
      closeSettings();
      renderProfilesList();
      renderProfileChrome();
      render();
      showWelcome();
      toast("Profile deleted");
    },
  });
}


export function renderWelcome() {
  const list = $("#welcome-list");
  if (!list) return;
  const addTile =
    "<li>" +
    '<button type="button" class="welcome-card welcome-add" id="welcome-add" title="Add a profile">' +
    '<span class="welcome-avatar welcome-avatar-new" aria-hidden="true">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>' +
    "</span>" +
    '<span class="welcome-card-name">Add profile</span>' +
    '<span class="welcome-card-meta">New user</span>' +
    "</button></li>";

  if (!state.profiles.length) {
    list.innerHTML =
      addTile + '<li class="welcome-empty">No profiles yet — use the plus to create one.</li>';
    return;
  }
  list.innerHTML = state.profiles
    .map((profile) => {
      // The picker is a door, not a report: it names who you are picking and
      // stops there, so nothing about a profile's jobs leaks before its PIN.
      const detail = profile.locked
        ? "PIN protected"
        : profile.headline || profile.email || "No details yet";
      const meta = esc(detail);
      return (
        "<li>" +
        '<button type="button" class="welcome-card' +
        (profile.id === state.profileId ? " is-current" : "") +
        '" data-enter-profile="' + profile.id + '" title="Open ' + esc(profile.name) + '">' +
        '<span class="welcome-avatar" aria-hidden="true">' +
        esc(initials(profile.name)) +
        (profile.locked ? '<span class="welcome-avatar-lock">' + ICON.lock + "</span>" : "") +
        "</span>" +
        '<span class="welcome-card-name">' + esc(profile.name) + "</span>" +
        '<span class="welcome-card-meta">' + meta + "</span>" +
        "</button></li>"
      );
    })
    .join("") +
    addTile;
}


export function showWelcome() {
  state.gate = true;
  document.documentElement.classList.add("is-welcome");
  renderWelcome();
  $("#welcome").hidden = false;
  window.scrollTo(0, 0);
}


export function hideWelcome() {
  state.gate = false;
  document.documentElement.classList.remove("is-welcome");
  $("#welcome").hidden = true;
}


export async function enterProfile(id) {
  const profile = state.profiles.find((item) => item.id === id);
  if (!profile) return;
  if (profile.locked && !tokenFor(id)) {
    openUnlock(id);
    return;
  }
  state.profileId = id;
  persistProfile();
  rememberSession(id);
  hideWelcome();
  state.selection.clear();
  if (state.drawerId) closeDrawer();
  renderProfileChrome();
  render();
  await loadApps();
}


export function openUnlock(profileId) {
  const profile = state.profiles.find((item) => item.id === profileId);
  if (!profile) return;
  state.unlockId = profileId;
  $("#unlock-avatar").textContent = initials(profile.name);
  $("#unlock-name").textContent = profile.name;
  $("#unlock-meta").textContent = profile.headline || "PIN protected";
  $("#unlock-pin").value = "";
  const error = $("#unlock-error");
  error.hidden = true;
  error.textContent = "";
  if ($("#unlock-backdrop").hidden) {
    $("#unlock-backdrop").hidden = false;
    document.body.classList.add("no-scroll");
  }
  window.setTimeout(() => $("#unlock-pin").focus(), 40);
}


export function closeUnlock() {
  $("#unlock-backdrop").hidden = true;
  state.unlockId = null;
  document.body.classList.remove("no-scroll");
  if (state.gate) return;
  if (profileNeedsUnlock()) {
    // There is nothing to show behind the prompt, so offer the profile chooser.
    openProfiles();
  }
}


export async function submitUnlock(event) {
  event.preventDefault();
  const profileId = state.unlockId;
  if (!profileId) return;
  const input = $("#unlock-pin");
  const pin = input.value.trim();
  const box = $("#unlock-error");
  if (!/^\d{4,8}$/.test(pin)) {
    box.hidden = false;
    box.textContent = "Enter the 4–8 digit PIN.";
    input.focus();
    return;
  }
  const button = $("#unlock-submit");
  button.disabled = true;
  try {
    const result = await api("/api/profiles/" + profileId + "/unlock", {
      method: "POST",
      body: JSON.stringify({ pin: pin }),
    });
    setToken(profileId, result.token);
    const index = state.profiles.findIndex((item) => item.id === profileId);
    if (index !== -1 && result.profile) {
      state.profiles[index] = Object.assign({}, state.profiles[index], result.profile);
    }
    state.unlockId = null;
    $("#unlock-backdrop").hidden = true;
    document.body.classList.remove("no-scroll");
    state.profileId = profileId;
    persistProfile();
    rememberSession(profileId);
    state.selection.clear();
    if (state.drawerId) closeDrawer();
    if (state.gate) hideWelcome();
    renderProfileChrome();
    render();
    toast("Profile unlocked");
    await loadApps();
  } catch (error) {
    box.hidden = false;
    box.textContent = error.status === 401 ? "That PIN is not correct." : error.message;
    input.select();
  } finally {
    button.disabled = false;
  }
}


export async function lockProfile(profile) {
  if (!profile) return;
  try {
    await api("/api/profiles/" + profile.id + "/lock", {
      method: "POST",
      body: JSON.stringify({}),
    });
  } catch (error) {
    toast(error.message, true);
  }
  clearToken(profile.id);
  renderProfilesList();
  if (profile.id === state.profileId) {
    state.apps = [];
    state.trash = [];
    state.settings = Object.assign({}, DEFAULT_GOALS);
    rememberSession(null);
    closeProfiles();
    render();
    showWelcome();
  }
  toast(profile.name + " locked");
}


export function renderPinSettings() {
  const profile = activeProfile();
  const locked = !!(profile && profile.locked);
  const status = $("#settings-pin-status");
  status.textContent = locked
    ? "On — this profile asks for its PIN when it is opened."
    : "Off — this profile opens without a PIN.";
  status.classList.toggle("is-on", locked);
  $("#settings-pin-current-wrap").hidden = !locked;
  $("#settings-pin-save").textContent = locked ? "Change PIN" : "Set PIN";
  $("#settings-pin-remove").hidden = !locked;
  ["#settings-pin-current", "#settings-pin-new", "#settings-pin-confirm"].forEach((id) => {
    $(id).value = "";
  });
  const error = $("#settings-pin-error");
  error.hidden = true;
  error.textContent = "";
}


export function pinError(message, focus) {
  const box = $("#settings-pin-error");
  box.hidden = false;
  box.textContent = message;
  if (focus) $(focus).focus();
}


export async function savePin() {
  const profile = activeProfile();
  if (!profile) return;
  const firstTime = !profile.locked;
  const current = $("#settings-pin-current").value.trim();
  const pin = $("#settings-pin-new").value.trim();
  const confirm = $("#settings-pin-confirm").value.trim();
  if (!/^\d{4,8}$/.test(pin)) {
    pinError("The PIN must be 4–8 digits.", "#settings-pin-new");
    return;
  }
  if (pin !== confirm) {
    pinError("The two PINs do not match.", "#settings-pin-confirm");
    return;
  }
  if (profile.locked && !/^\d{4,8}$/.test(current)) {
    pinError("Enter the current PIN to change it.", "#settings-pin-current");
    return;
  }
  const button = $("#settings-pin-save");
  button.disabled = true;
  try {
    const result = await api("/api/profiles/" + profile.id + "/pin", {
      method: "POST",
      body: JSON.stringify({ pin: pin, current_pin: current }),
    });
    if (result && result.token) setToken(profile.id, result.token);
    const index = state.profiles.findIndex((item) => item.id === profile.id);
    if (index !== -1) state.profiles[index] = Object.assign({}, state.profiles[index], { locked: true });
    renderPinSettings();
    renderSettingsMenu();
    renderProfilesList();
    renderProfileChrome();
    toast(firstTime ? "PIN set — this profile now asks for it" : "PIN changed");
  } catch (error) {
    pinError(error.status === 401 ? "The current PIN is not correct." : error.message, "#settings-pin-current");
  } finally {
    button.disabled = false;
  }
}


export async function removePin() {
  const profile = activeProfile();
  if (!profile || !profile.locked) return;
  const current = $("#settings-pin-current").value.trim();
  if (!/^\d{4,8}$/.test(current)) {
    pinError("Enter the current PIN to remove the lock.", "#settings-pin-current");
    return;
  }
  const button = $("#settings-pin-remove");
  button.disabled = true;
  try {
    await api("/api/profiles/" + profile.id + "/pin", {
      method: "POST",
      body: JSON.stringify({ remove: true, current_pin: current }),
    });
    clearToken(profile.id);
    const index = state.profiles.findIndex((item) => item.id === profile.id);
    if (index !== -1) state.profiles[index] = Object.assign({}, state.profiles[index], { locked: false });
    renderPinSettings();
    renderSettingsMenu();
    renderProfilesList();
    renderProfileChrome();
    toast("PIN lock removed");
  } catch (error) {
    pinError(error.status === 401 ? "The current PIN is not correct." : error.message, "#settings-pin-current");
  } finally {
    button.disabled = false;
  }
}


/* ------------------------------------------------------- the LaTeX engine */

const engine = { status: null, timer: null };


function engineLabel(info) {
  if (!info) return "Checking…";
  if (info.available) return "Available — " + (info.version || "ready");
  if (info.state === "installing") return "Installing…";
  return "Not installed — Compile uses the in-app preview";
}


function paintEngine(info) {
  const meta = $("#settings-menu-engine");
  if (meta) meta.textContent = engineLabel(info);
  const status = $("#settings-engine-status");
  const install = $("#settings-engine-install");
  const recheck = $("#settings-engine-recheck");
  const error = $("#settings-engine-error");
  // Nothing has been asked of the server yet: the row alone says "Checking…".
  if (!status || !install || !info) return;
  const installing = info.state === "installing";
  const failed = info.state === "failed";
  install.hidden = info.available;
  install.disabled = installing;
  install.textContent = installing ? "Installing…" : failed ? "Try again" : "Install Tectonic";
  if (recheck) recheck.hidden = info.available || installing;
  if (error) {
    error.hidden = !failed;
    error.textContent = failed
      ? (info.message || "The download did not finish.") +
        " You can also run python3 install_tectonic.py in the project folder."
      : "";
  }
  if (info.available) {
    status.textContent =
      "Ready — Compile builds a real PDF with " +(info.version || "the installed engine") + ".";
    status.classList.add("is-on");
  } else if (installing) {
    status.textContent = "Downloading Tectonic — this takes a moment, and the rest of the tracker stays usable.";
    status.classList.remove("is-on");
  } else {
    status.textContent = failed
      ? "Not installed. " + (info.message || "")
      : "Not installed. Compile falls back to the in-app preview.";
    status.classList.remove("is-on");
  }
}


export async function refreshEngineStatus(options) {
  const settings = options || {};
  try {
    const info = await api("/api/engine");
    engine.status = info;
    paintEngine(info);
    if (info.state === "installing") {
      // Poll while the background download runs.
      window.clearTimeout(engine.timer);
      engine.timer = window.setTimeout(() => refreshEngineStatus(), 1500);
    }
    return info;
  } catch (error) {
    if (!settings.quiet) {
      const status = $("#settings-engine-status");
      if (status && !$("#settings-backdrop").hidden) status.textContent = error.message;
    }
    return null;
  }
}


export async function installEngine(button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    const info = await api("/api/engine/install", { method: "POST", body: "{}" });
    engine.status = info;
    paintEngine(info);
    if (info.state === "installing") {
      window.clearTimeout(engine.timer);
      engine.timer = window.setTimeout(() => refreshEngineStatus(), 1500);
    } else if (info.available) {
      toast("Tectonic is ready — Compile builds a real PDF now");
    }
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = label;
  }
}


export function renderSettingsMenu() {
  const profile = activeProfile();
  $("#settings-menu-avatar").textContent = initials(profile ? profile.name : "");
  $("#settings-menu-profile").textContent = profile
    ? [profile.name, profile.headline || profile.email].filter(Boolean).join(" · ")
    : "No profile selected";
  const locked = !!(profile && profile.locked);
  $("#settings-menu-lock").textContent = locked
    ? "On — asks for a PIN"
    : "Off — opens without a PIN";
  $("#settings-menu-goals").textContent =
    state.settings.daily_goal + " a day · " + state.settings.weekly_goal + " a week";
  $("#settings-menu-reminders").textContent = remindersOn()
    ? "On — one summary a day"
    : "Off — no notifications";
  paintEngine(engine.status);
}


export function showSettingsView(view) {
  const target = ["profile", "lock", "goals", "reminders", "backup", "engine"].includes(view)
    ? view
    : "menu";
  const profile = activeProfile();
  state.settingsView = target;
  $("#settings-hub").hidden = target !== "menu";
  $("#settings-form").hidden = target === "menu";
  $$("[data-settings-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.settingsPanel !== target;
  });
  $("#settings-back").hidden = target === "menu";
  $("#settings-save").hidden =
    target === "menu" ||
    target === "lock" ||
    target === "backup" ||
    target === "reminders" ||
    target === "engine";
  $("#settings-title").textContent = {
    menu: "Settings",
    profile: "Profile",
    lock: "Lock",
    goals: "Goals",
    reminders: "Reminders",
    backup: "Backup",
    engine: "LaTeX engine",
  }[target];
  $("#settings-sub").textContent = {
    menu: "Options for this profile.",
    profile: "Details for " + (profile ? profile.name : "this profile") + ".",
    lock: "Protect " + (profile ? profile.name : "this profile") + " with a PIN.",
    goals: "Set the pace you want to keep.",
    reminders: "Nudges about follow-ups and stale applications.",
    backup: "A safety copy of everything the tracker stores.",
    engine: "What the resume editor compiles with, on this computer.",
  }[target];
  if (target === "engine") refreshEngineStatus();
  if (target === "backup") loadBackups();
  const error = $("#settings-error");
  error.hidden = true;
  error.textContent = "";
  if (target === "menu") {
    const first = $("#settings-menu .settings-row");
    if (first) window.setTimeout(() => first.focus(), 30);
  } else if (target === "goals") {
    // Opened from Set goals: the cursor belongs in the first number, not nowhere.
    window.setTimeout(() => $("#settings-daily").select(), 30);
  }
}


export async function downloadBackup(button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Preparing the zip…";
  try {
    const response = await fetch("/api/backup", {
      headers: { "X-Api-Token": await sessionToken() },
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error((payload && payload.error) || "The backup could not be built");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "jobby-backup-" + todayISO() + ".zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast("Backup downloaded");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}


export async function loadBackups() {
  const list = $("#settings-backup-list");
  if (!list) return;
  list.innerHTML = '<li class="backup-empty">Reading the snapshots on this computer…</li>';
  try {
    const payload = await api("/api/backups");
    const backups = (payload && payload.backups) || [];
    if (!backups.length) {
      list.innerHTML =
        '<li class="backup-empty">No snapshot yet. One is taken the first time the server starts each day.</li>';
      return;
    }
    list.innerHTML = backups
      .map(
        (item) =>
          '<li class="backup-row">' +
          '<span class="backup-main">' +
          '<span class="backup-date">' + esc(item.label || item.date) + "</span>" +
          '<span class="backup-meta">' + esc(formatSize(item.size)) + " · " + esc(item.name) + "</span>" +
          "</span>" +
          '<button type="button" class="btn btn-ghost" data-restore-backup="' + esc(item.name) + '">Restore</button>' +
          "</li>"
      )
      .join("");
    $$("#settings-backup-list [data-restore-backup]").forEach((button) => {
      button.addEventListener("click", () => restoreBackup(button));
    });
  } catch (error) {
    list.innerHTML = "";
    toast(error.message, true);
  }
}


async function restoreBackup(button) {
  const name = button.dataset.restoreBackup;
  const ok = await confirmAction({
    title: "Restore this snapshot?",
    text:
      "The database goes back to " + name + ". Uploaded files stay as they are, and the data " +
      "you have now is kept as a snapshot first.",
    okLabel: "Restore",
  });
  if (!ok) return;
  button.disabled = true;
  button.textContent = "Restoring…";
  try {
    await api("/api/backups/" + encodeURIComponent(name) + "/restore", {
      method: "POST",
      body: "{}",
    });
    toast("Restored " + name + " — reloading");
    window.setTimeout(() => window.location.reload(), 600);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Restore";
    toast(error.message, true);
  }
}


export async function logoutProfile() {
  const profile = activeProfile();
  closeSettings();
  if (profile) {
    try {
      await api("/api/profiles/" + profile.id + "/lock", {
        method: "POST",
        body: "{}",
      });
    } catch (error) {
      /* the token expires on its own; logging out locally is enough */
    }
    clearToken(profile.id);
  }
  rememberSession(null);
  state.apps = [];
  state.trash = [];
  state.settings = Object.assign({}, DEFAULT_GOALS);
  state.selection.clear();
  if (state.drawerId) closeDrawer();
  render();
  showWelcome();
  toast("Logged out");
}


/* `view` opens straight into one of the panels ("goals" from the Overview's Set
   goals link); the header gear leaves it out and lands on the menu. */
export function openSettings(view) {
  const profile = activeProfile();
  $("#settings-name").value = profile ? profile.name : "";
  $("#settings-headline").value = profile ? profile.headline || "" : "";
  $("#settings-email").value = profile ? profile.email || "" : "";
  $("#settings-phone").value = profile ? profile.phone || "" : "";
  $("#settings-location").value = profile ? profile.location || "" : "";
  $("#settings-links").value = profile ? profile.links || "" : "";
  $("#settings-sub").textContent = profile
    ? "Details for " + profile.name + " and the pace you want to keep."
    : "Your details and the pace you want to keep.";
  $("#settings-daily").value = state.settings.daily_goal;
  $("#settings-weekly").value = state.settings.weekly_goal;
  renderPinSettings();
  renderReminderSettings();
  renderSettingsMenu();
  // The engine row should be right the first time Settings opens.
  refreshEngineStatus({ quiet: true });
  $("#settings-delete-profile").hidden = state.profiles.length <= 1;
  $("#settings-backdrop").hidden = false;
  document.body.classList.add("no-scroll");
  showSettingsView(typeof view === "string" ? view : "menu");
}


export function closeSettings() {
  $("#settings-backdrop").hidden = true;
  document.body.classList.remove("no-scroll");
  // Stop polling for an install once nobody is looking at the panel.
  window.clearTimeout(engine.timer);
}


export async function saveSettings(event) {
  event.preventDefault();
  const daily = Number($("#settings-daily").value);
  const weekly = Number($("#settings-weekly").value);
  const valid = (value) => Number.isInteger(value) && value >= 1 && value <= 100;
  const box = $("#settings-error");
  const profile = activeProfile();
  const details = {
    name: $("#settings-name").value.trim(),
    headline: $("#settings-headline").value.trim(),
    email: $("#settings-email").value.trim(),
    phone: $("#settings-phone").value.trim(),
    location: $("#settings-location").value.trim(),
    links: $("#settings-links").value.trim(),
  };
  if (!profile) {
    box.textContent = "No profile is selected. Pick one from the profile button first.";
    box.hidden = false;
    return;
  }
  if (!details.name) {
    box.textContent = "Give this profile a name.";
    box.hidden = false;
    $("#settings-name").focus();
    return;
  }
  if (!valid(daily) || !valid(weekly)) {
    box.textContent = "Enter whole numbers between 1 and 100 for both goals.";
    box.hidden = false;
    return;
  }
  const button = $("#settings-save");
  button.disabled = true;
  try {
    const updated = await api("/api/profiles/" + profile.id, {
      method: "PATCH",
      body: JSON.stringify(details),
    });
    const index = state.profiles.findIndex((item) => item.id === profile.id);
    if (index !== -1) state.profiles[index] = Object.assign({}, state.profiles[index], updated);
    const saved = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ daily_goal: daily, weekly_goal: weekly }),
    });
    state.settings = Object.assign({}, state.settings, saved);
    render();
    renderSettingsMenu();
    closeSettings();
    toast("Settings updated");
  } catch (err) {
    box.hidden = false;
    box.textContent = err.message;
  } finally {
    button.disabled = false;
  }
}
