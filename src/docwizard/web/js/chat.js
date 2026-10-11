// Middenpaneel: chatkop (privacy, internet, model), berichten met jobkaarten, en de invoer.
import { t } from "../i18n.js";
import { api, ChatStream } from "./api.js";
import { h, mount, clear, toast, openMenu, fmtBytes, fmtClock, modKey, emptyState } from "./dom.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { JobCard } from "./jobcard.js";
import { state, app, emit, findChat, currentProject } from "./state.js";
import { renameChat } from "./sidebar.js";

export const ACTIONS = ["auto", "chat", "new_document", "add_chapter", "translate", "correct", "review", "enrich", "diagram"];
const ACTION_ICON = { auto: "sparkle", chat: "message", new_document: "file", add_chapter: "plus", translate: "language", correct: "check", review: "eye", enrich: "book", diagram: "diagram" };
export const SLASH = [
  { cmd: "/nieuw", alias: ["/new"], action: "new_document" },
  { cmd: "/hoofdstuk", alias: ["/chapter"], action: "add_chapter" },
  { cmd: "/vertaal", alias: ["/translate"], action: "translate" },
  { cmd: "/corrigeer", alias: ["/correct"], action: "correct" },
  { cmd: "/review", alias: [], action: "review" },
  { cmd: "/verrijk", alias: ["/enrich"], action: "enrich" },
  { cmd: "/plaat", alias: ["/diagram"], action: "diagram" },
];
const PRIV = [
  { v: "private", e: "🔒" }, { v: "project", e: "📁" }, { v: "all", e: "🗂" },
];

/** Toestand van de geopende chat. */
const cur = {
  chatId: null, chat: null, stream: null, cards: new Map(), msgIds: new Set(),
  sources: [], artifacts: [], action: "auto", files: [], streamStatus: "connecting",
};
const drafts = new Map(); // chatId -> tekst
let els = {};

export function chatContext() { return cur; }

export function closeChat() {
  cur.stream?.close();
  cur.stream = null;
  cur.cards.forEach((c) => c.destroy());
  cur.cards.clear();
  cur.msgIds.clear();
  cur.chatId = null;
}

export async function openChat(chatId) {
  if (cur.chatId && els.input) drafts.set(cur.chatId, els.input.value);
  closeChat();
  cur.chatId = chatId;
  cur.chat = findChat(chatId);
  cur.sources = []; cur.artifacts = []; cur.files = []; cur.action = "auto";
  publishPanel();
  renderShell();
  let messages = [];
  let jobs = null;
  try {
    [messages, jobs] = await Promise.all([api.messages(chatId), api.chatJobs(chatId).catch(() => null)]);
  } catch (e) { mount(els.list, emptyState("alert", t("common.error"), e.message)); return; }
  if (cur.chatId !== chatId) return;
  renderMessages(messages);
  if (Array.isArray(jobs)) placeJobs(jobs, messages);
  cur.stream = new ChatStream(chatId, {
    onEvent: onEvent,
    onStatus: (s) => { cur.streamStatus = s; renderStreamStatus(); },
    onReconnect: () => resyncJobs(),
  });
  // herstel jobkaarten: status + gemiste events
  await resyncJobs(true);
  scrollToBottom(true);
}

// ------------------------------------------------------------------ opbouw

function renderShell() {
  const main = document.getElementById("main");
  const chat = cur.chat;
  els = {};
  els.title = h("button", { class: "chat-title", title: t("chat.rename"), onclick: () => chat && renameChat(chat) }, chat?.title || t("chat.new"));
  els.privacy = h("div", { class: "seg", role: "radiogroup", "aria-label": "Privacy" });
  els.internet = h("button", { class: "toggle-chip", onclick: toggleInternet });
  els.model = h("button", { class: "btn sm ghost model-btn", onclick: (e) => modelMenu(e.currentTarget) });
  els.streamDot = h("span", { class: "stream-status" });
  els.list = h("div", { class: "msg-list", role: "log", "aria-live": "polite" });
  els.scroller = h("div", { class: "chat-scroll", onscroll: onScroll }, h("div", { class: "chat-inner" }, els.list));
  els.jump = h("button", { class: "jump-bottom", hidden: true, onclick: () => scrollToBottom(true), "aria-label": "Naar onderen" }, icon("chevron-down", 16));
  main.className = "main view-chat";
  mount(main,
    (els.header = buildHeader()),
    h("div", { class: "chat-body", ondragenter: onDrag, ondragover: onDrag, ondragleave: onDragLeave, ondrop: onDrop },
      els.scroller, els.jump,
      (els.dropzone = h("div", { class: "dropzone", hidden: true }, icon("upload", 28), h("span", null, t("chat.drop"))))),
    composer());
  renderHeaderControls();
  renderStreamStatus();
  const d = drafts.get(cur.chatId);
  if (d) { els.input.value = d; autoGrow(); }
  setTimeout(() => els.input?.focus(), 0);
}

function buildHeader() {
  const project = currentProject();
  return h("header", { class: "chat-head" },
    !state.panes.sidebar ? h("button", { class: "icon-btn", "aria-label": t("nav.expand"), title: t("nav.expand"), onclick: () => app.togglePane("sidebar") }, icon("panel-left", 16)) : null,
    h("div", { class: "chat-head-title" },
      project ? h("span", { class: "crumb" }, project.name) : null,
      project ? h("span", { class: "crumb-sep" }, "/") : null,
      els.title),
    h("div", { class: "chat-head-tools" }, els.privacy, els.internet, els.model, els.streamDot,
      h("button", { class: ["icon-btn", state.panes.doc && "on"], title: `${state.panes.doc ? t("doc.collapse") : t("doc.expand")} (${modKey}.)`, "aria-label": t("doc.expand"), onclick: () => app.togglePane("doc") }, icon("panel-right", 16))));
}

export function rerenderChatHeader() {
  if (!els.header?.isConnected) return;
  const nu = buildHeader();
  els.header.replaceWith(nu);
  els.header = nu;
}

function renderHeaderControls() {
  const c = cur.chat;
  if (!c || !els.privacy) return;
  mount(els.privacy, PRIV.map((p) => h("button", {
    class: ["seg-btn", c.privacy === p.v && "on"], role: "radio", "aria-checked": String(c.privacy === p.v),
    title: `${t(`privacy.${p.v}`)} — ${t(`privacy.${p.v}.tip`)}`, onclick: () => setPrivacy(p.v),
  }, h("span", { class: "emoji" }, p.e), h("span", { class: "seg-label" }, t(`privacy.${p.v}`)))));
  const priv = c.privacy === "private";
  els.internet.disabled = priv;
  els.internet.classList.toggle("on", !!c.internet && !priv);
  els.internet.setAttribute("aria-pressed", String(!!c.internet && !priv));
  els.internet.title = priv ? t("internet.disabledPrivate") : c.internet ? t("internet.on") : t("internet.off");
  mount(els.internet, h("span", { class: "emoji" }, "🌐"), h("span", { class: "seg-label" }, t("internet.label")));
  const conns = state.settings?.connections || [];
  const ov = c.roles?.writer;
  const conn = conns.find((x) => x.id === (ov || state.settings?.roles?.writer));
  mount(els.model, icon("server", 13), h("span", { class: "model-name" }, conn ? (conn.model || conn.name) : t("chat.model")), ov ? h("span", { class: "dot-accent" }) : null, icon("chevron-down", 12));
  els.model.title = `${t("chat.model")}: ${conn ? `${conn.name} · ${conn.model}` : "?"}${ov ? "" : ` (${t("common.default").toLowerCase()})`}`;
  els.title.textContent = c.title || t("chat.new");
}

function renderStreamStatus() {
  if (!els.streamDot) return;
  const ok = cur.streamStatus === "open";
  els.streamDot.className = `stream-status ${ok ? "ok" : "wait"}`;
  els.streamDot.title = ok ? t("chat.connected") : t("chat.reconnecting");
  mount(els.streamDot, h("span", { class: "status-dot" }));
}

async function patchChat(body) {
  const c = cur.chat;
  try {
    const updated = await api.updateChat(c.id, body);
    Object.assign(c, body, updated || {});
    const list = state.chatsByProject[c.project_id];
    const i = list?.findIndex((x) => x.id === c.id) ?? -1;
    if (i >= 0) list[i] = { ...list[i], ...c };
    renderHeaderControls();
    app.renderSidebar();
  } catch (e) { toast(e.message, "error"); }
}

function setPrivacy(v) {
  if (cur.chat.privacy === v) return;
  patchChat(v === "private" ? { privacy: v, internet: false } : { privacy: v });
}

function toggleInternet() {
  if (cur.chat.privacy === "private") return;
  patchChat({ internet: !cur.chat.internet });
}

function modelMenu(anchor) {
  const conns = (state.settings?.connections || []).filter((c) => c.enabled);
  const ov = cur.chat.roles?.writer || "";
  const def = state.settings?.roles?.writer;
  const defConn = conns.find((c) => c.id === def);
  openMenu(anchor, [
    { label: t("chat.modelDefault"), desc: defConn ? `${defConn.name} · ${defConn.model}` : "", checked: !ov, onClick: () => patchChat({ roles: {} }) },
    "sep",
    ...conns.map((c) => ({ label: c.name, desc: c.model || "—", icon: "server", checked: ov === c.id, onClick: () => patchChat({ roles: { ...(cur.chat.roles || {}), writer: c.id } }) })),
    "sep",
    { label: t("nav.settings"), icon: "settings", onClick: () => app.showView("settings", "roles") },
  ], { align: "right", className: "menu-wide" });
}

// ------------------------------------------------------------------ berichten

function renderMessages(messages) {
  clear(els.list);
  if (!messages.length) { els.list.appendChild(chatEmpty()); return; }
  for (const m of messages) appendMessage(m, true);
}

function chatEmpty() {
  const tpl = state.templates.find((x) => x.id === currentProject()?.template_id);
  return h("div", { class: "chat-empty" },
    emptyState("message", t("chat.empty"), t("chat.empty.text")),
    tpl ? h("p", { class: "muted center" }, icon("template", 13), ` ${tpl.name}`) : null,
    h("div", { class: "suggestions" }, ["chat.suggest1", "chat.suggest2", "chat.suggest3"].map((k) =>
      h("button", { class: "suggestion", onclick: () => { els.input.value = t(k); autoGrow(); onInput(); els.input.focus(); } }, t(k)))));
}

function appendMessage(m, initial = false) {
  if (m.id && cur.msgIds.has(m.id)) return;
  if (m.id) cur.msgIds.add(m.id);
  els.list.querySelector(".chat-empty")?.remove();
  const jobId = m.meta?.job_id;
  if (m.role === "event" && jobId && !m.meta?.compacted_count) {
    // (oudere mock) plaatsaanduiding voor een jobkaart
    const card = ensureCard(jobId, m.meta?.pipeline);
    if (!card.el.isConnected) els.list.appendChild(card.el);
    return;
  }
  if (m.role === "assistant" && jobId) {
    const card = cur.cards.get(jobId);
    if (card && card.compact) card.el.remove(); // het streaming-antwoord wordt vervangen door het echte bericht
  }
  const el = messageEl(m);
  el.dataset.created = m.created_at || "";
  els.list.appendChild(el);
  if (m.role === "user" && jobId) {
    // de engine koppelt de job aan het gebruikersbericht: kaart direct eronder
    const card = ensureCard(jobId, m.meta?.action === "chat" ? "chat" : null);
    els.list.appendChild(card.el);
  }
  if (!initial) scrollToBottom();
}

/** Jobs uit GET /api/chats/{id}/jobs die nog geen plek hebben (bv. hervatte jobs) op tijd invoegen. */
function placeJobs(jobs, messages) {
  for (const j of jobs) {
    const card = ensureCard(j.id, j.pipeline);
    card.applyJobState(j);
    if (card.el.isConnected) continue;
    const when = Date.parse(j.created_at || "") || 0;
    const after = [...els.list.children].find((n) => n.dataset?.created && Date.parse(n.dataset.created) > when);
    els.list.insertBefore(card.el, after || null);
  }
}

function messageEl(m) {
  if (m.role === "system") return h("div", { class: "msg-system" }, m.content);
  if (m.role === "event") {
    const n = m.meta?.compacted_count;
    return h("div", { class: "compact-marker", title: m.meta?.summary || "" }, icon(n ? "database" : "info", 13),
      n ? t("chat.compacted", { n }) : m.content);
  }
  const isUser = m.role === "user";
  const files = m.meta?.files || [];
  const action = m.meta?.action;
  return h("article", { class: ["msg", isUser ? "msg-user" : "msg-assistant"] },
    !isUser ? h("div", { class: "avatar ai" }, icon("sparkle", 14)) : null,
    h("div", { class: "msg-body" },
      h("div", { class: "msg-head" },
        h("span", { class: "msg-author" }, isUser ? (state.settings?.author_name || t("chat.you")) : t("chat.assistant")),
        action && !["auto", "chat"].includes(action) ? h("span", { class: "badge" }, icon(ACTION_ICON[action] || "sparkle", 11), t(`action.${action}`)) : null,
        h("span", { class: "msg-time" }, fmtClock(m.created_at))),
      m.content ? h("div", { class: "md", html: renderMarkdown(m.content) }) : null,
      files.length ? h("div", { class: "msg-files" }, files.map((f) => h("span", { class: "file-chip" }, icon("file", 12), f.name, f.size ? h("span", { class: "muted" }, fmtBytes(f.size)) : null))) : null));
}

function ensureCard(jobId, pipeline) {
  let card = cur.cards.get(jobId);
  if (card) return card;
  card = new JobCard(jobId, {
    onGrow: () => scrollToBottom(),
    onSource: (ev) => { cur.sources.push(ev); publishPanel(); },
    onArtifact: (ev, replay) => {
      cur.artifacts.push(ev); publishPanel();
      if (ev.kind === "document" && !replay) app.refreshDocuments?.(ev.version_id);
    },
    onPreview: (a) => app.previewVersion?.(a.version_id),
    onRetry: (newId, old) => {
      const nc = ensureCard(newId, old.job.pipeline);
      old.el.after(nc.el);
      api.job(newId).then((j) => nc.applyJobState(j)).catch(() => {});
      scrollToBottom(true);
    },
    onAttention: (c, ev) => notify(ev.text),
    onDone: (c) => notify(c.summary || t("job.done")),
  }, pipeline);
  cur.cards.set(jobId, card);
  return card;
}

function publishPanel() {
  emit("chat-panel", { sources: cur.sources, artifacts: cur.artifacts });
}

async function resyncJobs(full = false) {
  const chatId = cur.chatId;
  for (const [id, card] of cur.cards) {
    if (!full && ["done", "failed", "cancelled"].includes(card.job.status)) continue;
    try {
      const job = await api.job(id);
      if (cur.chatId !== chatId) return;
      // gemiste events ophalen (optioneel endpoint; zie CONTRACTS "Afwijkingen UI")
      try {
        const evs = await api.jobEvents(id, card.lastEventId);
        if (Array.isArray(evs)) evs.forEach((ev) => (ev.type === "message" && ev.message ? appendMessage(ev.message) : card.apply(ev, true)));
      } catch { /* endpoint niet beschikbaar: alleen status */ }
      card.applyJobState(job);
      if (full && card.compact && ["done", "failed", "cancelled"].includes(card.job.status) && !card.job.error) card.el.remove();
    } catch (e) {
      if (e.status === 404) card.applyJobState({ status: "failed", error: "Job niet gevonden" });
    }
  }
}

function onEvent(ev) {
  if (ev.type === "message" && ev.message) {
    appendMessage(ev.message);
    app.touchChat?.(cur.chatId);
    return;
  }
  if (!ev.job_id) return;
  let card = cur.cards.get(ev.job_id);
  if (!card) {
    card = ensureCard(ev.job_id, ev.pipeline);
    els.list.querySelector(".chat-empty")?.remove();
    els.list.appendChild(card.el);
  }
  card.apply(ev);
}

function notify(text) {
  if (document.hasFocus()) return;
  try {
    if ("Notification" in window && Notification.permission === "granted") new Notification("DocWizard", { body: text });
  } catch { /* geen notificaties */ }
}

// ------------------------------------------------------------------ scrollen

let stickBottom = true;
function onScroll() {
  const s = els.scroller;
  stickBottom = s.scrollTop + s.clientHeight >= s.scrollHeight - 80;
  els.jump.hidden = stickBottom;
}

function scrollToBottom(force = false) {
  if (!els.scroller) return;
  if (!force && !stickBottom) { els.jump.hidden = false; return; }
  requestAnimationFrame(() => { els.scroller.scrollTop = els.scroller.scrollHeight; stickBottom = true; els.jump.hidden = true; });
}

// ------------------------------------------------------------------ invoer

function composer() {
  els.input = h("textarea", { class: "composer-input", rows: 1, placeholder: t("chat.placeholder"), "aria-label": t("chat.placeholder"),
    oninput: () => { autoGrow(); onInput(); }, onkeydown: onKey });
  els.files = h("div", { class: "composer-files" });
  els.actionBtn = h("button", { class: "btn sm ghost action-btn", onclick: (e) => actionMenu(e.currentTarget), "aria-haspopup": "menu" });
  els.fileInput = h("input", { type: "file", multiple: true, hidden: true, onchange: (e) => { uploadFiles([...e.target.files]); e.target.value = ""; } });
  els.send = h("button", { class: "send-btn", "aria-label": t("chat.send"), title: `${t("chat.send")} (${modKey}↵)`, onclick: send }, icon("send", 16));
  els.slash = h("div", { class: "slash-pop", hidden: true, role: "listbox" });
  const c = h("div", { class: "composer" },
    h("div", { class: "composer-box" }, els.slash, els.files, els.input,
      h("div", { class: "composer-bar" },
        els.actionBtn,
        h("button", { class: "icon-btn", title: t("chat.attach"), "aria-label": t("chat.attach"), onclick: () => els.fileInput.click() }, icon("paperclip", 16)),
        els.fileInput,
        h("span", { class: "composer-hint" }, t("chat.sendHint", { mod: modKey })),
        els.send)));
  renderAction(); renderFiles();
  return c;
}

function autoGrow() {
  const i = els.input;
  i.style.height = "auto";
  i.style.height = `${Math.min(i.scrollHeight, 260)}px`;
}

function renderAction() {
  mount(els.actionBtn, icon(ACTION_ICON[cur.action], 14), h("span", null, t(`action.${cur.action}`)), icon("chevron-down", 12));
  els.actionBtn.classList.toggle("on", cur.action !== "auto");
}

export function setAction(a) { cur.action = a; if (els.actionBtn) { renderAction(); els.input?.focus(); } }

function actionMenu(anchor) {
  openMenu(anchor, ACTIONS.map((a) => ({
    label: t(`action.${a}`), desc: t(`action.${a}.desc`), icon: ACTION_ICON[a], checked: cur.action === a,
    hint: SLASH.find((s) => s.action === a)?.cmd, onClick: () => setAction(a),
  })), { className: "menu-wide" });
}

function slashMatches() {
  const v = els.input.value;
  const m = v.match(/^\/(\S*)$/);
  if (!m) return null;
  const q = m[0].toLowerCase();
  return SLASH.filter((s) => s.cmd.startsWith(q) || s.alias.some((a) => a.startsWith(q)));
}

let slashIndex = 0;
function onInput() {
  const list = slashMatches();
  if (!list || !list.length) { els.slash.hidden = true; return; }
  slashIndex = Math.min(slashIndex, list.length - 1);
  els.slash.hidden = false;
  mount(els.slash, list.map((s, i) => h("button", { class: ["slash-item", i === slashIndex && "active"], role: "option",
    onmousedown: (e) => { e.preventDefault(); pickSlash(s); } },
    icon(ACTION_ICON[s.action], 14), h("code", null, s.cmd), h("span", null, t(`action.${s.action}`)), h("span", { class: "muted" }, t(`action.${s.action}.desc`)))));
}

function pickSlash(s) {
  els.input.value = "";
  els.slash.hidden = true;
  setAction(s.action);
}

function onKey(e) {
  if (!els.slash.hidden) {
    const list = slashMatches() || [];
    if (e.key === "ArrowDown") { e.preventDefault(); slashIndex = (slashIndex + 1) % list.length; onInput(); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); slashIndex = (slashIndex - 1 + list.length) % list.length; onInput(); return; }
    if ((e.key === "Enter" || e.key === "Tab") && list[slashIndex]) { e.preventDefault(); pickSlash(list[slashIndex]); return; }
    if (e.key === "Escape") { els.slash.hidden = true; return; }
  }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
  if (e.key === "Backspace" && !els.input.value && cur.action !== "auto") setAction("auto");
}

function parseSlash(text) {
  const m = text.match(/^(\/\S+)\s*([\s\S]*)$/);
  if (!m) return null;
  const s = SLASH.find((x) => x.cmd === m[1].toLowerCase() || x.alias.includes(m[1].toLowerCase()));
  return s ? { action: s.action, text: m[2] } : null;
}

async function send() {
  let text = els.input.value.trim();
  let action = cur.action;
  const sl = parseSlash(text);
  if (sl) { action = sl.action; text = sl.text.trim(); }
  if (cur.files.some((f) => f.uploading)) { toast(t("chat.uploading"), "info"); return; }
  if (!text && !cur.files.length && action === "auto") return;
  const body = { text, file_ids: cur.files.map((f) => f.id), action, params: {} };
  els.send.disabled = true;
  try {
    const res = await api.send(cur.chatId, body);
    els.input.value = ""; autoGrow(); drafts.delete(cur.chatId);
    cur.files = []; renderFiles();
    setAction("auto");
    if (res?.message) appendMessage(res.message);
    if (res?.job_id) {
      const existed = cur.cards.has(res.job_id); // SSE kan sneller zijn dan de POST-respons
      const card = ensureCard(res.job_id, action === "chat" ? "chat" : null);
      els.list.appendChild(card.el); // (her)plaatsen ná het gebruikersbericht
      if (!existed) api.job(res.job_id).then((j) => card.applyJobState(j)).catch(() => {});
    }
    scrollToBottom(true);
    app.touchChat?.(cur.chatId);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    els.send.disabled = false;
    els.input.focus();
  }
}

// ------------------------------------------------------------------ bestanden

function renderFiles() {
  if (!els.files) return;
  els.files.hidden = !cur.files.length;
  mount(els.files, cur.files.map((f) => h("span", { class: ["file-chip", f.uploading && "uploading", f.error && "error"] },
    f.uploading ? icon("spinner", 12) : icon(f.error ? "alert" : "file", 12),
    h("span", { class: "file-name" }, f.name), f.size != null ? h("span", { class: "muted" }, fmtBytes(f.size)) : null,
    h("button", { class: "chip-x", "aria-label": t("common.delete"), onclick: () => { cur.files = cur.files.filter((x) => x !== f); renderFiles(); } }, icon("x", 11)))));
}

async function uploadFiles(files) {
  for (const file of files) {
    const entry = { name: file.name, size: file.size, uploading: true };
    cur.files.push(entry); renderFiles();
    try {
      const res = await api.upload("/api/files", { file, chat_id: cur.chatId });
      Object.assign(entry, res, { uploading: false });
    } catch (e) {
      toast(t("chat.uploadFailed", { error: e.message }), "error");
      cur.files = cur.files.filter((x) => x !== entry);
    }
    renderFiles();
  }
}

let dragDepth = 0;
function onDrag(e) {
  if (!e.dataTransfer?.types?.includes("Files")) return;
  e.preventDefault();
  if (e.type === "dragenter") dragDepth++;
  els.dropzone.hidden = false;
}
function onDragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) els.dropzone.hidden = true; }
function onDrop(e) {
  e.preventDefault(); dragDepth = 0; els.dropzone.hidden = true;
  const files = [...(e.dataTransfer?.files || [])];
  if (files.length) uploadFiles(files);
}

export function refreshChatHeader() {
  cur.chat = findChat(cur.chatId) || cur.chat;
  renderHeaderControls();
}

export function focusComposer() { els.input?.focus(); }
