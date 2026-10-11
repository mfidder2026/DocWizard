// Opdrachtenpalet (⌘K / Ctrl+K): chats, projecten en opdrachten doorzoeken.
import { t } from "../i18n.js";
import { h, mount, modKey } from "./dom.js";
import { icon } from "./icons.js";
import { state, app } from "./state.js";
import { ACTIONS, SLASH } from "./chat.js";

let open = null;

function norm(s) { return String(s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, ""); }

function score(text, q) {
  const s = norm(text);
  if (!q) return 1;
  if (s.startsWith(q)) return 3;
  if (s.includes(q)) return 2;
  // losse letters in volgorde
  let i = 0;
  for (const ch of s) if (ch === q[i]) i++;
  return i === q.length ? 1 : 0;
}

function items() {
  const out = [];
  const inChat = state.view === "chat" && state.currentChatId;
  out.push({ group: "palette.actions", label: t("nav.newProject"), icon: "folder", run: () => app.newProject() });
  if (state.currentProjectId) out.push({ group: "palette.actions", label: t("nav.newChat"), icon: "message", run: () => app.newChat(state.currentProjectId) });
  if (inChat) {
    for (const a of ACTIONS) {
      const sl = SLASH.find((s) => s.action === a);
      out.push({ group: "palette.actions", label: t("palette.setAction", { a: t(`action.${a}`) }), icon: "sparkle", hint: sl?.cmd, run: () => app.setAction(a) });
    }
  }
  out.push({ group: "palette.actions", label: t("nav.templates"), icon: "template", run: () => app.showView("templates") });
  out.push({ group: "palette.actions", label: t("nav.knowledge"), icon: "book", run: () => app.showView("knowledge") });
  out.push({ group: "palette.actions", label: t("nav.settings"), icon: "settings", hint: `${modKey},`, run: () => app.showView("settings") });
  out.push({ group: "palette.actions", label: t("set.conn"), icon: "server", run: () => app.showView("settings", "connections") });
  out.push({ group: "palette.actions", label: t("palette.toggleTheme"), icon: "moon", run: () => app.cycleTheme() });
  out.push({ group: "palette.actions", label: t("palette.toggleSidebar"), icon: "panel-left", hint: `${modKey}\\`, run: () => app.togglePane("sidebar") });
  out.push({ group: "palette.actions", label: t("palette.toggleDoc"), icon: "panel-right", hint: `${modKey}.`, run: () => app.togglePane("doc") });
  for (const p of state.projects) {
    out.push({ group: "palette.projects", label: p.name, sub: p.client, icon: "folder", run: () => app.openProject(p.id) });
    for (const c of state.chatsByProject[p.id] || []) {
      out.push({ group: "palette.chats", label: c.title, sub: p.name, icon: "message", run: () => app.selectChat(p.id, c.id) });
    }
  }
  return out;
}

export function openPalette() {
  if (open) { open.input.focus(); return; }
  app.preloadAllChats?.();
  let sel = 0;
  let filtered = [];
  const input = h("input", { class: "palette-input", placeholder: t("palette.placeholder"), "aria-label": t("palette.placeholder"), autocomplete: "off", spellcheck: false });
  const list = h("div", { class: "palette-list", role: "listbox" });
  const box = h("div", { class: "palette", role: "dialog", "aria-modal": "true" },
    h("div", { class: "palette-head" }, icon("search", 16), input, h("kbd", null, "esc")), list);
  const backdrop = h("div", { class: "modal-backdrop palette-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } }, box);

  function render() {
    const q = norm(input.value.trim());
    filtered = items().map((it) => ({ it, s: Math.max(score(it.label, q), score(it.sub, q) ? 1 : 0) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => (q ? b.s - a.s : 0))
      .map((x) => x.it)
      .slice(0, 60);
    if (!q) filtered.sort((a, b) => ["palette.chats", "palette.actions", "palette.projects"].indexOf(a.group) - ["palette.chats", "palette.actions", "palette.projects"].indexOf(b.group));
    sel = Math.min(sel, Math.max(0, filtered.length - 1));
    if (!filtered.length) { mount(list, h("div", { class: "palette-empty" }, t("palette.empty"))); return; }
    const nodes = [];
    let group = null;
    filtered.forEach((it, i) => {
      if (it.group !== group) { group = it.group; nodes.push(h("div", { class: "palette-group" }, t(group))); }
      nodes.push(h("button", { class: ["palette-item", i === sel && "on"], role: "option", "aria-selected": String(i === sel),
        onmousemove: () => { if (sel !== i) { sel = i; render(); } }, onclick: () => runItem(it) },
        icon(it.icon, 15), h("span", { class: "palette-label" }, it.label), it.sub ? h("span", { class: "muted" }, it.sub) : null,
        it.hint ? h("kbd", null, it.hint) : null));
    });
    mount(list, nodes);
    list.querySelector(".palette-item.on")?.scrollIntoView({ block: "nearest" });
  }

  function runItem(it) { close(); it.run(); }

  function close() {
    backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
    open = null;
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % Math.max(1, filtered.length); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + filtered.length) % Math.max(1, filtered.length); render(); }
    else if (e.key === "Enter") { e.preventDefault(); if (filtered[sel]) runItem(filtered[sel]); }
  }

  input.addEventListener("input", () => { sel = 0; render(); });
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(backdrop);
  open = { input, close, render };
  render();
  input.focus();
}

export function refreshPalette() { open?.render(); }
