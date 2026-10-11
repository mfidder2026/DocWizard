// Gedeelde toestand + eenvoudige event-bus. `app` wordt door app.js gevuld met acties
// (selectChat, showView, …) zodat modules elkaar kunnen aanroepen zonder cyclische imports.

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function persist(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* opslag niet beschikbaar */ }
}

export const state = {
  settings: null,
  projects: [],
  chatsByProject: {},          // pid -> [chat]
  templates: [],
  view: "chat",                // chat | settings | templates | knowledge
  currentProjectId: load("dw.project", null),
  currentChatId: load("dw.chat", null),
  expanded: new Set(load("dw.expanded", [])),
  panes: load("dw.panes", { sidebar: true, doc: true }),
  theme: load("dw.theme", "system"), // system | light | dark
  online: true,
};

export const app = {};

const listeners = {};
export function on(evt, fn) { (listeners[evt] ||= new Set()).add(fn); return () => listeners[evt].delete(fn); }
export function emit(evt, data) { listeners[evt]?.forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); }

export function findChat(chatId) {
  for (const [pid, chats] of Object.entries(state.chatsByProject)) {
    const c = chats.find((x) => x.id === chatId);
    if (c) return { ...c, project_id: c.project_id || pid };
  }
  return null;
}

export function currentProject() {
  return state.projects.find((p) => p.id === state.currentProjectId) || null;
}

export function connectionLabel(id) {
  const c = state.settings?.connections?.find((x) => x.id === id);
  if (!c) return "";
  return c.model ? `${c.name} · ${c.model}` : c.name;
}
