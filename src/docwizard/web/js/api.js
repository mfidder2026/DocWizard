// HTTP-client voor de DocWizard-engine (zie docs/CONTRACTS.md) + SSE-stroom per chat.

function resolveToken() {
  let tok = window.DOCWIZARD_TOKEN || "";
  if (tok === "__DOCWIZARD_TOKEN__") tok = "";
  if (!tok) {
    try { tok = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("dw.token") || ""; } catch { /* */ }
  }
  try { if (tok) sessionStorage.setItem("dw.token", tok); } catch { /* */ }
  return tok;
}

export const TOKEN = resolveToken();

export class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}

function detailMessage(body, status) {
  if (!body) return `HTTP ${status}`;
  if (typeof body === "string") return body.slice(0, 300) || `HTTP ${status}`;
  const d = body.detail ?? body.error ?? body.message;
  if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ");
  if (typeof d === "string") return d;
  return `HTTP ${status}`;
}

async function request(method, path, body, { raw = false } = {}) {
  const headers = { "X-DocWizard-Token": TOKEN };
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (e) {
    throw new ApiError("Geen verbinding met de engine", 0, null);
  }
  if (raw) return res;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) throw new ApiError(detailMessage(data, res.status), res.status, data);
  return data;
}

/** URL met token als queryparameter — voor <a href>, <img src> en EventSource (die geen headers kunnen zetten). */
export function authUrl(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(TOKEN)}`;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b) => request("PUT", p, b),
  patch: (p, b) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  upload: (p, fields) => {
    const fd = new FormData();
    for (const [k, v] of Object.entries(fields)) if (v !== undefined && v !== null && v !== "") fd.append(k, v);
    return request("POST", p, fd);
  },

  health: () => request("GET", "/api/health"),
  settings: () => request("GET", "/api/settings"),
  saveSettings: (s) => request("PUT", "/api/settings", s),
  testConnection: (c) => request("POST", "/api/connections/test", c),

  projects: () => request("GET", "/api/projects"),
  createProject: (b) => request("POST", "/api/projects", b),
  updateProject: (id, b) => request("PATCH", `/api/projects/${id}`, b),
  deleteProject: (id) => request("DELETE", `/api/projects/${id}`),

  templates: () => request("GET", "/api/templates"),
  template: (id) => request("GET", `/api/templates/${id}`),

  chats: (pid) => request("GET", `/api/projects/${pid}/chats`),
  createChat: (pid, b = {}) => request("POST", `/api/projects/${pid}/chats`, b),
  updateChat: (id, b) => request("PATCH", `/api/chats/${id}`, b),
  messages: (cid) => request("GET", `/api/chats/${cid}/messages`),
  send: (cid, b) => request("POST", `/api/chats/${cid}/messages`, b),

  documents: (pid) => request("GET", `/api/projects/${pid}/documents`),
  outline: (vid) => request("GET", `/api/versions/${vid}/outline`),
  preview: (vid) => request("GET", `/api/versions/${vid}/preview`),
  openVersion: (vid, reveal = false) => request("POST", `/api/versions/${vid}/open`, reveal ? { reveal: true } : {}),

  job: (id) => request("GET", `/api/jobs/${id}`),
  jobEvents: (id, after = 0) => request("GET", `/api/jobs/${id}/events?after=${after}`),
  chatJobs: (cid) => request("GET", `/api/chats/${cid}/jobs`),
  retryJob: (id) => request("POST", `/api/jobs/${id}/retry`),
  cancelJob: (id) => request("POST", `/api/jobs/${id}/cancel`),
  pauseJob: (id) => request("POST", `/api/jobs/${id}/pause`),
  resumeJob: (id) => request("POST", `/api/jobs/${id}/resume`),
  answer: (id, question_id, answer, edited = null) => request("POST", `/api/jobs/${id}/answer`, { question_id, answer, edited }),
};

/**
 * Eén EventSource per open chat. Herverbindt automatisch (met backoff als de browser het opgeeft)
 * en roept onReconnect aan zodra de verbinding na een onderbreking terug is, zodat de UI de
 * jobstatus kan verversen via GET /api/jobs/{id}.
 */
export class ChatStream {
  constructor(chatId, { onEvent, onStatus, onReconnect }) {
    this.chatId = chatId;
    this.onEvent = onEvent;
    this.onStatus = onStatus || (() => {});
    this.onReconnect = onReconnect || (() => {});
    this.es = null;
    this.closed = false;
    this.hadError = false;
    this.retry = 1000;
    this.timer = null;
    this.connect();
  }

  connect() {
    if (this.closed) return;
    const es = new EventSource(authUrl(`/api/events?chat_id=${encodeURIComponent(this.chatId)}`));
    this.es = es;
    es.onopen = () => {
      this.retry = 1000;
      this.onStatus("open");
      if (this.hadError) { this.hadError = false; this.onReconnect(); }
    };
    es.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      if (ev && (!ev.chat_id || ev.chat_id === this.chatId)) this.onEvent(ev);
    };
    es.onerror = () => {
      this.hadError = true;
      this.onStatus("reconnecting");
      if (es.readyState === EventSource.CLOSED) {
        es.close();
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.connect(), this.retry);
        this.retry = Math.min(this.retry * 2, 15000);
      }
    };
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    this.es?.close();
  }
}
