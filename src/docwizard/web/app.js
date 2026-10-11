// DocWizard UI — ingang. Vanilla ES-modules, geen build-stap, werkt offline (pywebview of browser).
import { t, setLang, getLang, translateDom } from "./i18n.js";
import { api } from "./js/api.js";
import { h, mount, toast, confirmModal, emptyState, modKey, closeMenus } from "./js/dom.js";
import { icon, logo } from "./js/icons.js";
import { state, app, persist } from "./js/state.js";
import { renderSidebar, newProject, newChat } from "./js/sidebar.js";
import { openChat, closeChat, setAction, refreshChatHeader, rerenderChatHeader, focusComposer } from "./js/chat.js";
import { renderDocPanel, loadDocuments, previewVersion, resetDocPanel } from "./js/docpanel.js";
import { renderSettings, settingsDirty, discardSettings } from "./js/settings.js";
import { renderTemplates } from "./js/templates.js";
import { renderKnowledge } from "./js/knowledge.js";
import { openPalette, refreshPalette } from "./js/palette.js";

const appEl = document.getElementById("app");

// ------------------------------------------------------------------ thema

const mql = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  const root = document.documentElement;
  if (state.theme === "light" || state.theme === "dark") root.dataset.theme = state.theme;
  else delete root.dataset.theme;
  root.dataset.resolvedTheme = state.theme === "system" ? (mql.matches ? "dark" : "light") : state.theme;
}
mql.addEventListener?.("change", () => { applyTheme(); if (state.view === "chat") renderDocPanel(); });

Object.assign(app, {
  setTheme(v) { state.theme = v; persist("dw.theme", v); applyTheme(); if (state.view === "chat") renderDocPanel(); },
  cycleTheme() {
    const resolved = document.documentElement.dataset.resolvedTheme;
    app.setTheme(resolved === "dark" ? "light" : "dark");
  },
});

// ------------------------------------------------------------------ data laden

Object.assign(app, {
  async loadProjects() {
    try {
      state.projects = await api.projects();
      state.online = true;
    } catch (e) { toast(e.message, "error"); state.online = e.status !== 0 ? state.online : false; }
    await Promise.all([...state.expanded].filter((pid) => state.projects.some((p) => p.id === pid)).map((pid) => app.loadChats(pid)));
    renderSidebar();
  },
  async loadChats(pid) {
    try {
      state.chatsByProject[pid] = (await api.chats(pid)).map((c) => ({ ...c, project_id: c.project_id || pid }));
    } catch (e) { state.chatsByProject[pid] = []; }
    renderSidebar();
    refreshPalette();
  },
  async loadTemplates() {
    try { state.templates = await api.templates(); } catch { /* */ }
  },
  async preloadAllChats() {
    await Promise.all(state.projects.filter((p) => !state.chatsByProject[p.id]).map((p) => app.loadChats(p.id)));
  },
  touchChat(cid) {
    clearTimeout(app._touchTimer);
    app._touchTimer = setTimeout(() => { if (state.currentProjectId) app.loadChats(state.currentProjectId).then(refreshChatHeader); }, 400);
  },
  onChatUpdated() { refreshChatHeader(); },
  renderSidebar,
  newProject, newChat, setAction,
  openPalette,
  refreshDocuments: (vid) => loadDocuments(vid),
  previewVersion,
});

// ------------------------------------------------------------------ navigatie

async function canLeaveSettings() {
  if (state.view !== "settings" || !settingsDirty()) return true;
  const ok = await confirmModal(`${t("set.unsaved")}. ${getLang() === "nl" ? "Wijzigingen verwerpen?" : "Discard changes?"}`, { okLabel: getLang() === "nl" ? "Verwerpen" : "Discard" });
  if (ok) discardSettings();
  return ok;
}

Object.assign(app, {
  async showView(view, section) {
    if (view !== "settings" && !(await canLeaveSettings())) return;
    closeMenus();
    if (view !== "chat") closeChat();
    state.view = view;
    layout();
    renderSidebar();
    if (view === "settings") return renderSettings(section);
    if (view === "templates") return renderTemplates();
    if (view === "knowledge") return renderKnowledge();
    return renderChatArea();
  },

  async selectChat(pid, cid) {
    if (!(await canLeaveSettings())) return;
    const projectChanged = pid !== state.currentProjectId;
    state.currentProjectId = pid;
    state.currentChatId = cid;
    persist("dw.project", pid); persist("dw.chat", cid);
    state.view = "chat";
    if (projectChanged) resetDocPanel();
    layout();
    renderSidebar();
    await renderChatArea();
    loadDocuments();
  },

  async openProject(pid) {
    state.expanded.add(pid); persist("dw.expanded", [...state.expanded]);
    if (!state.chatsByProject[pid]) await app.loadChats(pid);
    const first = state.chatsByProject[pid]?.[0];
    if (first) app.selectChat(pid, first.id);
    else newChat(pid);
  },

  togglePane(name) {
    state.panes[name] = !state.panes[name];
    persist("dw.panes", state.panes);
    layout();
    if (state.view === "chat") { if (state.currentChatId) rerenderChatHeader(); else renderChatArea(); renderDocPanel(); }
    else app.showView(state.view);
  },

  rerenderAll() {
    translateDom();
    renderSidebar();
    app.showView(state.view);
    renderDocPanel();
  },
});

function layout() {
  appEl.classList.toggle("sb-collapsed", !state.panes.sidebar);
  appEl.classList.toggle("doc-collapsed", !state.panes.doc || state.view !== "chat" || !state.currentProjectId);
  appEl.dataset.view = state.view;
}

async function renderChatArea() {
  const main = document.getElementById("main");
  const chatExists = state.currentChatId && (state.chatsByProject[state.currentProjectId] || []).some((c) => c.id === state.currentChatId);
  if (chatExists) { await openChat(state.currentChatId); renderDocPanel(); return; }
  closeChat();
  main.className = "main view-page";
  if (!state.projects.length) { mount(main, welcome()); layout(); return; }
  mount(main, h("div", { class: "page center-page" },
    emptyState("message", t("welcome.noChat"), t("welcome.noChat.text"),
      h("div", { class: "row" },
        state.currentProjectId ? h("button", { class: "btn primary", onclick: () => newChat(state.currentProjectId) }, icon("plus", 14), t("nav.newChat")) : null,
        h("button", { class: "btn", onclick: () => openPalette() }, icon("search", 14), t("nav.search"), h("kbd", null, `${modKey}K`))))));
  renderDocPanel();
}

function welcome() {
  const hasTemplate = state.templates.length > 0;
  const writer = state.settings?.roles?.writer;
  const step = (n, title, text, done, btn) => h("li", { class: ["welcome-step", done && "done"] },
    h("span", { class: "step-num" }, done ? icon("check", 14) : String(n)),
    h("div", { class: "step-text" }, h("strong", null, title), h("span", { class: "muted" }, text)), btn);
  return h("div", { class: "page center-page" },
    h("div", { class: "welcome" },
      h("div", { class: "welcome-logo" }, logo(44)),
      h("h1", null, t("welcome.title")),
      h("p", { class: "lead" }, t("welcome.lead")),
      h("ol", { class: "welcome-steps" },
        step(1, t("welcome.step1"), t("welcome.step1.text"), !!writer, h("button", { class: "btn sm", onclick: () => app.showView("settings", "connections") }, t("nav.settings"))),
        step(2, t("welcome.step2"), t("welcome.step2.text"), hasTemplate, h("button", { class: ["btn", "sm", !hasTemplate && "primary"], onclick: () => app.showView("templates") }, icon("upload", 13), t("tpl.upload"))),
        step(3, t("welcome.step3"), t("welcome.step3.text"), false, h("button", { class: ["btn", "sm", hasTemplate && "primary"], onclick: () => newProject() }, icon("plus", 13), t("nav.newProject")))),
      h("p", { class: "muted small center" }, `${modKey}K — ${t("nav.search")}`)));
}

// ------------------------------------------------------------------ sneltoetsen

document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (!mod) return;
  const k = e.key.toLowerCase();
  if (k === "k") { e.preventDefault(); openPalette(); }
  else if (k === ",") { e.preventDefault(); app.showView("settings"); }
  else if (k === "\\") { e.preventDefault(); app.togglePane("sidebar"); }
  else if (k === ".") { e.preventDefault(); app.togglePane("doc"); }
  else if (k === "l" && e.shiftKey) { e.preventDefault(); focusComposer(); }
});

window.addEventListener("beforeunload", (e) => { if (settingsDirty()) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------ start

let offlineToast = null;
async function healthLoop() {
  try {
    await api.health();
    if (!state.online) { state.online = true; offlineToast?.remove(); offlineToast = null; renderSidebar(); }
  } catch {
    if (state.online) { state.online = false; offlineToast = toast(t("toast.offline"), "error", 0); renderSidebar(); }
  }
  setTimeout(healthLoop, state.online ? 15000 : 4000);
}

async function init() {
  applyTheme();
  try {
    state.settings = await api.settings();
    if (state.settings?.ui_language && state.settings.ui_language !== getLang()) setLang(state.settings.ui_language);
  } catch (e) {
    if (e.status === 401) toast("Ongeldig sessietoken — start DocWizard opnieuw", "error", 0);
  }
  document.documentElement.lang = getLang();
  translateDom();
  if ("Notification" in window && Notification.permission === "default") {
    // pas vragen bij eerste interactie, niet bij laden
    document.addEventListener("click", () => { try { Notification.requestPermission(); } catch { /* */ } }, { once: true });
  }
  await Promise.all([app.loadTemplates(), app.loadProjects()]);
  if (state.currentProjectId && !state.projects.some((p) => p.id === state.currentProjectId)) { state.currentProjectId = null; state.currentChatId = null; }
  if (state.currentProjectId && !state.chatsByProject[state.currentProjectId]) await app.loadChats(state.currentProjectId);
  if (!state.currentProjectId && state.projects.length) {
    const p = state.projects[0];
    state.currentProjectId = p.id;
    state.expanded.add(p.id);
    await app.loadChats(p.id);
    state.currentChatId = state.chatsByProject[p.id]?.[0]?.id || null;
  }
  layout();
  renderSidebar();
  await renderChatArea();
  if (state.view === "chat") loadDocuments();
  document.body.classList.add("ready");
  setTimeout(healthLoop, 15000);
}

init();
