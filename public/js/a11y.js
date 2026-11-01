/* Jobby — Keyboard and screen-reader behaviour for the dialogs.
   ES module, no build step. Open the dashboard through server.py. */

import { $$ } from "./core.js";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

// The page behind a modal must not be reachable by Tab or screen readers.
// The island of section tabs sits outside the header but is just as much part of
// the background while a dialog is open.
const BACKGROUND = ".chrome, .menu-row, main.page, .welcome";

const open = new Set();
let lastOutsideFocus = null;

function dialogs() {
  return $$('[aria-modal="true"]').filter(
    (dialog) => !dialog.closest("[hidden]") && dialog.getClientRects().length > 0
  );
}

function focusable(root) {
  return $$(FOCUSABLE, root).filter(
    (element) => !element.disabled && element.getClientRects().length > 0
  );
}

function setBackgroundInert(on) {
  $$(BACKGROUND).forEach((element) => {
    if (on && !element.hasAttribute("inert")) element.setAttribute("inert", "");
    else if (!on && element.hasAttribute("inert")) element.removeAttribute("inert");
  });
}

// Remember where focus was before a dialog took it, so it can go back there.
document.addEventListener(
  "focusin",
  (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.closest('[aria-modal="true"]')) lastOutsideFocus = target;
  },
  true
);

// The same control after a re-render replaced it (a table row, say).
function equivalent(element) {
  if (!element) return null;
  if (element.id) {
    const twin = document.getElementById(element.id);
    if (twin) return twin;
  }
  const action = element.dataset && element.dataset.action;
  const id = element.dataset && element.dataset.id;
  if (action && id) {
    const twin = document.querySelector(
      '[data-action="' + action + '"][data-id="' + id + '"]'
    );
    if (twin) return twin;
  }
  return null;
}

function restoreFocus(element) {
  const usable = (item) =>
    item && document.contains(item) && item.getClientRects().length > 0;
  const target = usable(element) ? element : equivalent(element);
  if (usable(target)) {
    target.focus({ preventScroll: true });
    return;
  }
  // Nothing to go back to: keep the keyboard inside the content area rather
  // than dropping focus onto the document body.
  const region = document.querySelector("main.page") || document.body;
  if (!region.hasAttribute("tabindex")) region.setAttribute("tabindex", "-1");
  region.focus({ preventScroll: true });
}

function sync() {
  const current = dialogs();
  const top = current.length ? current[current.length - 1] : null;

  // Lift or apply the background block first: focus() is ignored inside an
  // inert subtree, and the page behind must be usable again before we hand
  // focus back to the control that opened the dialog.
  setBackgroundInert(current.length > 0);

  for (const dialog of Array.from(open)) {
    if (current.includes(dialog)) continue;
    open.delete(dialog);
    if (current.length) continue;
    // The close path often re-renders the table right after removing the
    // dialog, so wait a frame before handing focus back: by then the fresh row
    // exists and equivalent() can find the same button again.
    const target = lastOutsideFocus;
    window.requestAnimationFrame(() => {
      if (!dialogs().length) restoreFocus(target);
    });
  }

  let justOpened = false;
  for (const dialog of current) {
    if (open.has(dialog)) continue;
    open.add(dialog);
    if (!dialog.hasAttribute("tabindex")) dialog.setAttribute("tabindex", "-1");
    justOpened = true;
  }

  if (top && justOpened) {
    const first = focusable(top)[0];
    (first || top).focus({ preventScroll: true });
  }
}

// Keep Tab inside the topmost dialog and let Shift+Tab wrap backwards.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Tab") return;
  const current = dialogs();
  const top = current[current.length - 1];
  if (!top) return;
  const items = focusable(top);
  if (!items.length) {
    event.preventDefault();
    top.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const outside = !top.contains(active);
  if (event.shiftKey && (outside || active === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (outside || active === last)) {
    event.preventDefault();
    first.focus();
  }
});

export function installFocusManagement() {
  const observer = new MutationObserver(sync);
  // Hidden flags cover the static dialogs; childList covers the confirmation
  // dialog, which is cloned in and out of the page on demand.
  observer.observe(document.body, {
    attributes: true,
    attributeFilter: ["hidden"],
    childList: true,
    subtree: true,
  });
  sync();
}
