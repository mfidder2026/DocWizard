// Linkerpaneel: projecten → chats, plus navigatie naar Sjablonen / Kennis / Instellingen.
import { t } from "../i18n.js";
import { api } from "./api.js";
import { h, mount, openMenu, formModal, confirmModal, toast, modKey, fmtDate } from "./dom.js";
import { icon, logo } from "./icons.js";
import { state, app, persist, connectionLabel } from "./state.js";

const root = () => document.getElementById("sidebar");

export function renderSidebar() {
  const el = root();
  if (!el) return;
  const writer = state.settings?.roles?.writer;
  mount(el,
    h("div", { class: "sb-head" },
      h("div", { class: "brand" }, logo(22), h("span", null, t("app.name"))),
      h("button", { class: "icon-btn", title: `${t("nav.collapse")} (${modKey}\\)`, "aria-label": t("nav.collapse"), onclick: () => app.togglePane("sidebar") }, icon("panel-left", 16))),
    h("button", { class: "sb-search", onclick: () => app.openPalette() },
      icon("search", 15), h("span", null, t("nav.search")), h("kbd", null, `${modKey}K`)),
    h("div", { class: "sb-section-head" },
      h("span", null, t("nav.projects")),
      h("button", { class: "icon-btn sm", title: t("nav.newProject"), "aria-label": t("nav.newProject"), onclick: () => newProject() }, icon("plus", 15))),
    h("nav", { class: "sb-tree", "aria-label": t("nav.projects") },
      state.projects.length === 0
        ? h("div", { class: "sb-empty" }, h("p", null, t("welcome.step3.text")),
          h("button", { class: "btn sm", onclick: () => newProject() }, icon("plus", 14), t("nav.newProject")))
        : state.projects.map(projectNode)),
    h("nav", { class: "sb-nav" },
      navItem("templates", "template", t("nav.templates")),
      navItem("knowledge", "book", t("nav.knowledge")),
      navItem("settings", "settings", t("nav.settings"), `${modKey},`)),
    h("button", { class: "sb-model", title: t("nav.writer"), onclick: () => app.showView("settings", "roles") },
      h("span", { class: ["status-dot", state.online ? "ok" : "bad"] }),
      h("span", { class: "sb-model-text" }, writer ? connectionLabel(writer) : t("welcome.step1"))),
  );
}

function navItem(view, ic, label, hint) {
  return h("button", { class: ["sb-nav-item", state.view === view && "active"], onclick: () => app.showView(view), "aria-current": state.view === view ? "page" : null },
    icon(ic, 16), h("span", null, label), hint ? h("kbd", null, hint) : null);
}

function projectNode(p) {
  const open = state.expanded.has(p.id);
  const chats = state.chatsByProject[p.id];
  const active = state.currentProjectId === p.id;
  return h("div", { class: "sb-project" },
    h("div", { class: ["sb-row", "sb-project-row", active && state.view === "chat" && !state.currentChatId && "active"] },
      h("button", { class: "sb-row-main", "aria-expanded": String(open), onclick: () => toggleProject(p.id) },
        icon(open ? "chevron-down" : "chevron-right", 14, "chev"),
        icon(open ? "folder-open" : "folder", 15),
        h("span", { class: "sb-label" }, p.name),
        p.client && p.client !== p.name ? h("span", { class: "sb-sub" }, p.client) : null),
      h("button", { class: "icon-btn sm sb-hover", title: t("nav.newChat"), "aria-label": t("nav.newChat"), onclick: () => newChat(p.id) }, icon("plus", 14)),
      h("button", { class: "icon-btn sm sb-hover", "aria-label": "Menu", onclick: (e) => projectMenu(e.currentTarget, p) }, icon("more", 14))),
    open ? h("div", { class: "sb-chats" },
      !chats ? h("div", { class: "sb-loading" }, t("common.loading")) :
        chats.length === 0 ? h("button", { class: "sb-row sb-chat-empty", onclick: () => newChat(p.id) }, icon("plus", 13), t("nav.newChat")) :
          chats.map((c) => chatNode(p, c))) : null);
}

const PRIV_ICON = { private: "🔒", project: "📁", all: "🗂" };

function chatNode(p, c) {
  const active = state.view === "chat" && state.currentChatId === c.id;
  return h("div", { class: ["sb-row", "sb-chat-row", active && "active"] },
    h("button", { class: "sb-row-main", onclick: () => app.selectChat(p.id, c.id), ondblclick: () => renameChat(c), "aria-current": active ? "true" : null },
      icon("message", 14),
      h("span", { class: "sb-label" }, c.title || t("chat.new")),
      h("span", { class: "sb-meta", title: t(`privacy.${c.privacy}`) }, PRIV_ICON[c.privacy] || "", c.internet ? "🌐" : "")),
    h("span", { class: "sb-time" }, fmtDate(c.updated_at)),
    h("button", { class: "icon-btn sm sb-hover", "aria-label": t("chat.rename"), onclick: () => renameChat(c) }, icon("edit", 13)));
}

async function toggleProject(pid) {
  if (state.expanded.has(pid)) state.expanded.delete(pid);
  else { state.expanded.add(pid); if (!state.chatsByProject[pid]) await app.loadChats(pid); }
  persist("dw.expanded", [...state.expanded]);
  renderSidebar();
}

export async function newProject() {
  if (!state.templates.length) await app.loadTemplates();
  const vals = await formModal({
    title: t("project.new"), submitLabel: t("common.create"),
    fields: projectFields({ template_id: state.templates.length === 1 ? state.templates[0].id : "" }),
  });
  if (!vals) return;
  try {
    const p = await api.createProject({ ...vals, template_id: vals.template_id || null });
    toast(t("project.created"), "success");
    await app.loadProjects();
    state.expanded.add(p.id);
    persist("dw.expanded", [...state.expanded]);
    await newChat(p.id);
  } catch (e) { toast(e.message, "error"); }
}

function projectFields(p) {
  return [
    { name: "name", label: t("project.name"), required: true, value: p.name, placeholder: t("project.namePlaceholder") },
    { name: "client", label: t("project.client"), value: p.client, placeholder: t("project.clientPlaceholder") },
    { name: "template_id", label: t("project.template"), type: "select", value: p.template_id || "",
      options: [{ value: "", label: t("project.noTemplate") }, ...state.templates.map((x) => ({ value: x.id, label: x.name }))] },
    { name: "privacy_default", label: t("project.privacy"), type: "select", value: p.privacy_default || "project", required: true,
      options: ["private", "project", "all"].map((v) => ({ value: v, label: `${PRIV_ICON[v]} ${t(`privacy.${v}`)}` })) },
  ];
}

async function projectMenu(anchor, p) {
  openMenu(anchor, [
    { label: t("nav.newChat"), icon: "plus", onClick: () => newChat(p.id) },
    { label: t("project.edit"), icon: "edit", onClick: () => editProject(p) },
    "sep",
    { label: t("common.delete"), icon: "trash", danger: true, onClick: () => deleteProject(p) },
  ], { align: "right" });
}

async function editProject(p) {
  if (!state.templates.length) await app.loadTemplates();
  const vals = await formModal({ title: t("project.edit"), fields: projectFields(p) });
  if (!vals) return;
  try {
    await api.updateProject(p.id, { ...vals, template_id: vals.template_id || null });
    await app.loadProjects();
    toast(t("common.saved"), "success");
  } catch (e) { toast(e.message, "error"); }
}

async function deleteProject(p) {
  if (!(await confirmModal(t("project.deleteConfirm", { name: p.name })))) return;
  try {
    await api.deleteProject(p.id);
    delete state.chatsByProject[p.id];
    if (state.currentProjectId === p.id) app.selectChat(null, null);
    await app.loadProjects();
    toast(t("project.deleted"), "success");
  } catch (e) { toast(e.message, "error"); }
}

export async function newChat(pid) {
  try {
    const c = await api.createChat(pid, {});
    state.expanded.add(pid);
    persist("dw.expanded", [...state.expanded]);
    await app.loadChats(pid);
    app.selectChat(pid, c.id);
  } catch (e) { toast(e.message, "error"); }
}

export async function renameChat(c) {
  const vals = await formModal({ title: t("chat.rename"), fields: [{ name: "title", label: t("chat.title"), required: true, value: c.title }] });
  if (!vals) return;
  try {
    await api.updateChat(c.id, { title: vals.title });
    const pid = c.project_id || Object.keys(state.chatsByProject).find((k) => state.chatsByProject[k].some((x) => x.id === c.id));
    if (pid) await app.loadChats(pid);
    app.onChatUpdated?.();
  } catch (e) { toast(e.message, "error"); }
}
