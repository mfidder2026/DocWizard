// Kleine DOM-helpers: h() bouwt elementen veilig (tekst via textContent), plus toasts, modals en menu's.
import { t, getLang } from "../i18n.js";
import { icon } from "./icons.js";

export function h(tag, props, ...children) {
  const el = tag === "frag" ? document.createDocumentFragment() : document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = Array.isArray(v) ? v.filter(Boolean).join(" ") : v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "html") el.innerHTML = v; // alleen voor vertrouwde, zelf-geëscapete HTML
      else if (k === "value") el.value = v;
      else if (k in el && k !== "list" && typeof v !== "string") el[k] = v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function mount(el, ...children) { clear(el); append(el, children); return el; }

export const $ = (sel, root = document) => root.querySelector(sel);

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl+";

export function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtDuration(ms) {
  if (ms == null || !isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}:${String(s % 60).padStart(2, "0")}`;
  return `${Math.floor(m / 60)}u${String(m % 60).padStart(2, "0")}`;
}

export function fmtEta(sec) {
  if (sec == null || !isFinite(sec)) return "";
  if (sec < 60) return `${Math.max(1, Math.round(sec))} s`;
  return `${Math.round(sec / 60)} min`;
}

export function toTime(ts) {
  if (ts == null) return null;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const p = Date.parse(ts);
  return isNaN(p) ? null : p;
}

export function fmtClock(ts) {
  const ms = toTime(ts);
  if (!ms) return "";
  return new Date(ms).toLocaleTimeString(getLang() === "en" ? "en-GB" : "nl-NL", { hour: "2-digit", minute: "2-digit" });
}

export function fmtDate(ts) {
  const ms = toTime(ts);
  if (!ms) return "";
  const d = new Date(ms);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return fmtClock(ms);
  return d.toLocaleDateString(getLang() === "en" ? "en-GB" : "nl-NL", { day: "numeric", month: "short" });
}

// --- toasts ----------------------------------------------------------------

export function toast(message, kind = "info", timeout = 3500) {
  let root = $("#toasts");
  if (!root) { root = h("div", { id: "toasts", class: "toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(root); }
  const ic = { success: "check", error: "alert", warning: "alert", info: "info" }[kind] || "info";
  const el = h("div", { class: `toast toast-${kind}` }, icon(ic, 16), h("span", null, message),
    h("button", { class: "icon-btn sm", "aria-label": t("common.close"), onclick: () => el.remove() }, icon("x", 14)));
  root.appendChild(el);
  if (timeout) setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 200); }, timeout);
  return el;
}

// --- modal -----------------------------------------------------------------

let modalStack = [];

export function openModal({ title, body, footer, width = 480, onClose }) {
  const prevFocus = document.activeElement;
  const close = () => {
    backdrop.remove();
    modalStack = modalStack.filter((m) => m !== close);
    document.removeEventListener("keydown", onKey, true);
    onClose?.();
    prevFocus?.focus?.();
  };
  const onKey = (e) => {
    if (e.key === "Escape" && modalStack[modalStack.length - 1] === close) { e.stopPropagation(); close(); }
  };
  const dialog = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title, style: { width: `min(${width}px, calc(100vw - 32px))` } },
    h("div", { class: "modal-head" }, h("h2", null, title),
      h("button", { class: "icon-btn", "aria-label": t("common.close"), onclick: close }, icon("x", 16))),
    h("div", { class: "modal-body" }, body),
    footer ? h("div", { class: "modal-foot" }, footer) : null);
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } }, dialog);
  document.body.appendChild(backdrop);
  document.addEventListener("keydown", onKey, true);
  modalStack.push(close);
  setTimeout(() => (dialog.querySelector("[autofocus], input, select, textarea, button.primary") || dialog).focus(), 20);
  return { close, dialog };
}

/** Formuliermodal. fields: [{name,label,type,value,options,placeholder,required,hint}] → Promise<values|null> */
export function formModal({ title, fields, submitLabel = t("common.save"), width }) {
  return new Promise((resolve) => {
    let done = false;
    const inputs = {};
    const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); submit(); } },
      fields.map((f, i) => {
        let input;
        if (f.type === "select") {
          input = h("select", { name: f.name }, f.options.map((o) => h("option", { value: o.value, selected: o.value === (f.value ?? "") }, o.label)));
        } else {
          input = h("input", { name: f.name, type: f.type || "text", value: f.value ?? "", placeholder: f.placeholder || "", autofocus: i === 0, autocomplete: "off" });
        }
        inputs[f.name] = input;
        return h("label", { class: "field" }, h("span", { class: "field-label" }, f.label, f.required ? null : h("span", { class: "muted" }, ` (${t("common.optional")})`)),
          input, f.hint ? h("span", { class: "field-hint" }, f.hint) : null,
          h("span", { class: "field-error" }));
      }),
      h("button", { type: "submit", hidden: true }));
    const submit = () => {
      const values = {};
      let ok = true;
      for (const f of fields) {
        const el = inputs[f.name];
        values[f.name] = el.value.trim();
        const err = el.parentElement.querySelector(".field-error");
        if (f.required && !values[f.name]) { ok = false; el.classList.add("invalid"); err.textContent = t("set.required"); }
        else { el.classList.remove("invalid"); err.textContent = ""; }
      }
      if (!ok) return;
      done = true; m.close(); resolve(values);
    };
    const m = openModal({
      title, width, body: form,
      footer: [h("button", { class: "btn", onclick: () => m.close() }, t("common.cancel")),
        h("button", { class: "btn primary", onclick: submit }, submitLabel)],
      onClose: () => { if (!done) resolve(null); },
    });
  });
}

export function confirmModal(message, { okLabel = t("common.delete"), danger = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal({
      title: t("common.yes") === "Ja" ? "Bevestigen" : "Confirm", width: 420, body: h("p", null, message),
      footer: [h("button", { class: "btn", onclick: () => m.close() }, t("common.cancel")),
        h("button", { class: `btn primary ${danger ? "danger" : ""}`, onclick: () => { done = true; m.close(); resolve(true); } }, okLabel)],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

// --- popover menu ------------------------------------------------------------

/** items: [{label, icon, onClick, danger, desc, checked}] | "sep" */
export function openMenu(anchor, items, { align = "left", className = "" } = {}) {
  closeMenus();
  const menu = h("div", { class: `menu ${className}`, role: "menu" },
    items.map((it) => it === "sep" ? h("div", { class: "menu-sep" }) :
      h("button", { class: ["menu-item", it.danger && "danger", it.checked && "checked"], role: "menuitem",
        onclick: (e) => { e.stopPropagation(); closeMenus(); it.onClick?.(); } },
        it.icon ? icon(it.icon, 15) : h("span", { class: "menu-icon-space" }),
        h("span", { class: "menu-text" }, h("span", null, it.label), it.desc ? h("span", { class: "menu-desc" }, it.desc) : null),
        it.hint ? h("kbd", null, it.hint) : null,
        it.checked ? icon("check", 14) : null)));
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = align === "right" ? r.right - mw : r.left;
  left = Math.max(8, Math.min(left, innerWidth - mw - 8));
  let top = r.bottom + 4;
  if (top + mh > innerHeight - 8) top = Math.max(8, r.top - mh - 4);
  menu.style.left = `${left}px`; menu.style.top = `${top}px`;
  const first = menu.querySelector(".menu-item");
  first?.focus();
  menu.addEventListener("keydown", (e) => {
    const list = [...menu.querySelectorAll(".menu-item")];
    const i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
    if (e.key === "Escape") { closeMenus(); anchor.focus?.(); }
  });
  setTimeout(() => document.addEventListener("mousedown", outside, true), 0);
  function outside(e) { if (!menu.contains(e.target)) closeMenus(); }
  menu._cleanup = () => document.removeEventListener("mousedown", outside, true);
  return menu;
}

export function closeMenus() {
  document.querySelectorAll(".menu").forEach((m) => { m._cleanup?.(); m.remove(); });
}

export function emptyState(iconName, title, text, actions) {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon(iconName, 22)),
    h("h3", null, title), text ? h("p", null, text) : null,
    actions ? h("div", { class: "empty-actions" }, actions) : null);
}
