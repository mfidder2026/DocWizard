// Instellingenscherm: taal, weergave, AI-verbindingen, modelrollen, kwaliteit en internet.
import { t, setLang, getLang } from "../i18n.js";
import { api } from "./api.js";
import { h, mount, toast, confirmModal } from "./dom.js";
import { icon } from "./icons.js";
import { state, app } from "./state.js";

const KINDS = [
  { v: "lmstudio", label: "LM Studio", url: "http://localhost:1234/v1" },
  { v: "ollama", label: "Ollama", url: "http://localhost:11434/v1" },
  { v: "mtplx", label: "MTPLX", url: "http://localhost:8000/v1" },
  { v: "openai", label: "OpenAI-compatibel", url: "" },
];
const DOC_LANGS = [["nl", "Nederlands"], ["en", "English"], ["de", "Deutsch"], ["fr", "Français"], ["es", "Español"], ["it", "Italiano"], ["pt", "Português"], ["pl", "Polski"], ["fy", "Frysk"]];
const ROLES = ["writer", "fast", "vision", "embedding"];
const SECTIONS = [["lang", "set.lang", "language"], ["appearance", "set.appearance", "sun"], ["connections", "set.conn", "server"], ["roles", "set.roles", "zap"], ["quality", "set.quality", "sparkle"], ["web", "set.web", "globe"]];

let draft = null;         // werkkopie
let saved = "";           // JSON van laatst opgeslagen toestand
const ui = { editing: new Set(), tests: new Map(), models: new Map(), showKey: new Set(), errors: {} };
let els = {};

export function settingsDirty() { return draft && JSON.stringify(draft) !== saved; }

export async function renderSettings(section) {
  const main = document.getElementById("main");
  main.className = "main view-page";
  if (!draft || !settingsDirty()) {
    try {
      const s = await api.settings();
      state.settings = s;
      draft = structuredClone(s);
      draft.web ||= { provider: "duckduckgo", url: "", api_key: "", confirm_queries: true, allow_domains: [] };
      draft.roles ||= {};
      saved = JSON.stringify(draft);
    } catch (e) {
      mount(main, h("div", { class: "page" }, h("div", { class: "banner error" }, icon("alert", 16), t("set.loadFailed", { error: e.message }))));
      return;
    }
  }
  els = {};
  els.status = h("span", { class: "save-status" });
  els.saveBtn = h("button", { class: "btn primary", onclick: save }, icon("check", 14), t("common.save"));
  els.body = h("div", { class: "settings-body" });
  mount(main,
    h("header", { class: "page-head" },
      !state.panes.sidebar ? h("button", { class: "icon-btn", "aria-label": t("nav.expand"), onclick: () => app.togglePane("sidebar") }, icon("panel-left", 16)) : null,
      h("h1", null, t("set.title")),
      h("div", { class: "page-head-tools" }, els.status, els.saveBtn)),
    h("div", { class: "page settings" },
      h("nav", { class: "settings-nav" }, SECTIONS.map(([id, key, ic]) =>
        h("a", { href: `#set-${id}`, onclick: (e) => { e.preventDefault(); document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); } }, icon(ic, 15), t(key)))),
      els.body));
  renderBody();
  if (section) setTimeout(() => document.getElementById(`set-${section}`)?.scrollIntoView({ block: "start" }), 30);
}

function renderBody() {
  const scroller = els.body.closest(".page");
  const top = scroller?.scrollTop ?? 0;
  mount(els.body, langSection(), appearanceSection(), connectionsSection(), rolesSection(), qualitySection(), webSection());
  if (scroller) scroller.scrollTop = top;
  updateStatus();
}

function changed() { updateStatus(); }

function updateStatus() {
  if (!els.status) return;
  const dirty = settingsDirty();
  els.status.className = `save-status ${dirty ? "dirty" : ""}`;
  mount(els.status, dirty ? [h("span", { class: "status-dot warn" }), t("set.unsaved")] : []);
  els.saveBtn.disabled = !dirty;
}

// ---------------------------------------------------------------- helpers

function section(id, titleKey, lead, ...children) {
  return h("section", { class: "set-section", id: `set-${id}` },
    h("h2", null, t(titleKey)), lead ? h("p", { class: "lead" }, lead) : null, ...children);
}

function field(label, control, hint, opts = {}) {
  return h("label", { class: ["field", opts.inline && "field-inline"] },
    h("span", { class: "field-label" }, label, opts.optional ? h("span", { class: "muted" }, ` (${t("common.optional")})`) : null),
    control, hint ? h("span", { class: "field-hint" }, hint) : null, h("span", { class: "field-error" }, opts.error || ""));
}

function bindInput(obj, key, { type = "text", parse, ...rest } = {}) {
  return h("input", { type, value: obj[key] ?? "", ...rest,
    oninput: (e) => { obj[key] = parse ? parse(e.target.value) : e.target.value; changed(); } });
}

function bindSelect(obj, key, options, onChange) {
  return h("select", { onchange: (e) => { obj[key] = e.target.value; onChange?.(e.target.value); changed(); } },
    options.map(([v, label]) => h("option", { value: v, selected: (obj[key] ?? "") === v }, label)));
}

function switchEl(checked, onChange, label) {
  return h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: !!checked, onchange: (e) => { onChange(e.target.checked); changed(); } }),
    h("span", { class: "switch-track" }, h("span", { class: "switch-thumb" })),
    label ? h("span", null, label) : null);
}

const intOrNull = (v) => { const n = parseInt(String(v).replace(/\D/g, ""), 10); return Number.isFinite(n) ? n : null; };

// ---------------------------------------------------------------- secties

function langSection() {
  return section("lang", "set.lang", null,
    h("div", { class: "grid-2" },
      field(t("set.lang.ui"), bindSelect(draft, "ui_language", [["nl", "Nederlands"], ["en", "English"]])),
      field(t("set.lang.doc"), bindSelect(draft, "doc_language", DOC_LANGS))),
    field(t("set.lang.author"), bindInput(draft, "author_name", { placeholder: "Voornaam Achternaam", autocomplete: "name" }), t("set.lang.authorHint")));
}

function appearanceSection() {
  const opts = [["system", "set.theme.system", "settings"], ["light", "set.theme.light", "sun"], ["dark", "set.theme.dark", "moon"]];
  return section("appearance", "set.appearance", null,
    field(t("set.theme"), h("div", { class: "seg seg-lg", role: "radiogroup" }, opts.map(([v, k, ic]) =>
      h("button", { class: ["seg-btn", state.theme === v && "on"], role: "radio", "aria-checked": String(state.theme === v),
        onclick: (e) => { e.preventDefault(); app.setTheme(v); renderBody(); } }, icon(ic, 14), h("span", { class: "seg-label" }, t(k)))))));
}

function connectionsSection() {
  const conns = draft.connections || (draft.connections = []);
  return section("connections", "set.conn", null,
    h("p", { class: "lead" }, h("span", { class: "badge" }, t("set.conn.sub")), " ", t("set.conn.lead")),
    h("div", { class: "conn-list" }, conns.length ? conns.map((c) => connCard(c)) : h("div", { class: "muted pad" }, t("set.conn.empty"))),
    h("div", { class: "conn-add" },
      h("button", { class: "btn", onclick: () => addConnection("openai", true) }, icon("plus", 14), t("set.conn.add")),
      h("span", { class: "muted" }, t("set.conn.presets"), ":"),
      KINDS.slice(0, 3).map((k) => h("button", { class: "btn ghost sm", onclick: () => addConnection(k.v) }, icon("plus", 12), k.label))));
}

function slug(name) {
  const base = (name || "verbinding").toLowerCase().normalize("NFKD").replace(/[^\w]+/g, "-").replace(/^-|-$/g, "") || "verbinding";
  let id = base, i = 2;
  while (draft.connections.some((c) => c.id === id)) id = `${base}-${i++}`;
  return id;
}

function addConnection(kind, custom = false) {
  const k = KINDS.find((x) => x.v === kind);
  const name = custom ? "" : k.label;
  const c = { id: slug(custom ? "eigen" : k.label), name, kind, base_url: k.url, api_key: "", model: "", context_limit: null, enabled: true };
  c._new = true;
  draft.connections.push(c);
  ui.editing.add(c);
  renderBody();
  setTimeout(() => {
    const card = [...document.querySelectorAll(".conn-card")].pop();
    card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card?.querySelector(".conn-form input[type=text]")?.focus();
  }, 30);
}

function rolesUsing(id) {
  return ROLES.filter((r) => draft.roles?.[r] === id).map((r) => t(`set.role.${r}`));
}

function connCard(c) {
  const editing = ui.editing.has(c);
  const test = ui.tests.get(c);
  const kind = KINDS.find((k) => k.v === c.kind) || KINDS[3];
  const used = rolesUsing(c.id);
  const testBtn = h("button", { class: "btn sm", disabled: test?.loading, onclick: () => runTest(c) }, test?.loading ? icon("spinner", 13) : icon("zap", 13), test?.loading ? t("set.conn.testing") : t("set.conn.test"));
  const summary = h("div", { class: "conn-summary" },
    h("div", { class: "conn-icon" }, icon("server", 16)),
    h("div", { class: "conn-main" },
      h("div", { class: "conn-name" }, c.name || h("span", { class: "muted" }, t("set.conn.name")), h("span", { class: "badge" }, kind.label), !c.enabled ? h("span", { class: "badge muted" }, t("set.conn.disabled")) : null),
      h("div", { class: "conn-sub mono" }, c.base_url || "—", c.model ? h("span", null, " · ", c.model) : null),
      used.length ? h("div", { class: "conn-roles" }, t("set.conn.inUse", { roles: used.join(", ") })) : null),
    h("div", { class: "conn-tools" },
      switchEl(c.enabled, (v) => { c.enabled = v; renderBody(); }),
      !editing ? testBtn : null,
      h("button", { class: "icon-btn", title: t("common.edit"), "aria-label": t("common.edit"), "aria-expanded": String(editing), onclick: () => { editing ? ui.editing.delete(c) : ui.editing.add(c); renderBody(); } }, icon(editing ? "chevron-up" : "edit", 15)),
      h("button", { class: "icon-btn danger-text", title: t("common.delete"), "aria-label": t("common.delete"), onclick: () => removeConnection(c) }, icon("trash", 15))));
  return h("div", { class: ["conn-card", editing && "editing"] }, summary,
    test && !test.loading && !editing ? testResult(test) : null,
    editing ? connForm(c, testBtn, test) : null);
}

function testResult(r) {
  if (r.ok) {
    return h("div", { class: "test-result ok" }, icon("check", 14),
      h("span", null, t("set.conn.ok", { ms: r.latency_ms ?? "?" })),
      r.context ? h("span", { class: "muted" }, " · ", t("set.conn.ctxFound", { n: r.context.toLocaleString() })) : null,
      r.models?.length ? h("span", { class: "muted" }, " · ", t("set.conn.models", { n: r.models.length })) : null);
  }
  return h("div", { class: "test-result bad" }, icon("alert", 14), h("span", null, t("set.conn.fail", { error: r.error || "?" })));
}

function connForm(c, testBtn, test) {
  const err = ui.errors[c.id] || {};
  const keyShown = ui.showKey.has(c);
  const models = ui.models.get(c) || [];
  const listId = `models-${c.id}`;
  const modelInput = h("input", { type: "text", value: c.model, list: listId, placeholder: "bijv. qwen3-30b-a3b", spellcheck: false,
    oninput: (e) => { c.model = e.target.value; changed(); } });
  const fetchBtn = h("button", { class: "btn sm", type: "button", onclick: () => runTest(c, true) }, icon("refresh", 13), t("set.conn.fetchModels"));
  const modelSelect = models.length ? h("select", { class: "model-select", "aria-label": t("set.conn.chooseModel"), onchange: (e) => {
    if (!e.target.value) return;
    c.model = e.target.value; modelInput.value = c.model;
    const m = models.find((x) => x.id === c.model);
    changed(); renderBody();
    if (m?.context_length) toast(`${c.model}: context ${m.context_length.toLocaleString()}`, "info", 2000);
  } }, h("option", { value: "" }, `${t("set.conn.chooseModel")} (${models.length})`),
  models.map((m) => h("option", { value: m.id, selected: m.id === c.model }, m.context_length ? `${m.id}  ·  ${Math.round(m.context_length / 1024)}k` : m.id))) : null;

  return h("div", { class: "conn-form" },
    h("div", { class: "grid-2" },
      field(t("set.conn.name"), bindInput(c, "name", { placeholder: "Mijn server", class: err.name ? "invalid" : "" }), null, { error: err.name }),
      field(t("set.conn.kind"), bindSelect(c, "kind", KINDS.map((k) => [k.v, k.label]), (v) => {
        const preset = KINDS.find((k) => k.v === v);
        if (preset?.url && (!c.base_url || KINDS.some((k) => k.url === c.base_url))) c.base_url = preset.url;
        if (!c.name || KINDS.some((k) => k.label === c.name)) c.name = preset.label === "OpenAI-compatibel" ? c.name : preset.label;
        renderBody();
      }))),
    field(t("set.conn.url"), bindInput(c, "base_url", { placeholder: "http://localhost:1234/v1", spellcheck: false, class: ["mono", err.base_url && "invalid"].filter(Boolean).join(" ") }), t("set.conn.urlHint"), { error: err.base_url }),
    field(t("set.conn.key"), h("div", { class: "input-group" },
      bindInput(c, "api_key", { type: keyShown ? "text" : "password", autocomplete: "off", spellcheck: false, placeholder: "sk-…" }),
      h("button", { class: "icon-btn", type: "button", "aria-label": keyShown ? t("common.hide") : t("common.show"), title: keyShown ? t("common.hide") : t("common.show"),
        onclick: (e) => { e.preventDefault(); keyShown ? ui.showKey.delete(c) : ui.showKey.add(c); renderBody(); } }, icon(keyShown ? "eye-off" : "eye", 15))),
    t("set.conn.keyHint"), { optional: true }),
    field(t("set.conn.model"), h("div", { class: "model-row" },
      h("div", { class: "input-group" }, modelInput, fetchBtn),
      modelSelect,
      h("datalist", { id: listId }, models.map((m) => h("option", { value: m.id }))))),
    h("div", { class: "grid-2" },
      field(t("set.conn.ctx"), h("input", { type: "number", min: 512, step: 512, value: c.context_limit ?? "", placeholder: "auto",
        oninput: (e) => { c.context_limit = intOrNull(e.target.value); changed(); } }), t("set.conn.ctxHint"), { optional: true }),
      h("div", { class: "field" }, h("span", { class: "field-label" }, t("set.conn.enabled")), switchEl(c.enabled, (v) => { c.enabled = v; renderBody(); }, c.enabled ? t("common.yes") : t("common.no")))),
    h("div", { class: "conn-form-foot" },
      testBtn,
      test && !test.loading ? testResult(test) : h("span"),
      h("button", { class: "btn sm ghost", onclick: () => { ui.editing.delete(c); renderBody(); } }, t("set.conn.done"))));
}

async function runTest(c, fetchModels = false) {
  ui.tests.set(c, { loading: true });
  renderBody();
  const { _new, ...conn } = c;
  try {
    const r = await api.testConnection(conn);
    ui.tests.set(c, r);
    if (r.models?.length) ui.models.set(c, r.models);
    if (fetchModels && r.ok && !c.model && r.models?.length) { c.model = r.models[0].id; }
    if (fetchModels && !r.ok) toast(t("set.conn.fail", { error: r.error }), "error");
  } catch (e) {
    ui.tests.set(c, { ok: false, error: e.message });
  }
  renderBody();
}

async function removeConnection(c) {
  if (!(await confirmModal(t("set.conn.deleteConfirm", { name: c.name || c.id })))) return;
  draft.connections = draft.connections.filter((x) => x !== c);
  for (const r of ROLES) if (draft.roles[r] === c.id) draft.roles[r] = "";
  renderBody();
}

function rolesSection() {
  const conns = draft.connections || [];
  return section("roles", "set.roles", t("set.roles.lead"),
    h("div", { class: "roles" }, ROLES.map((r) => {
      const opts = [["", t("common.none")], ...conns.map((c) => [c.id, `${c.name || c.id}${c.model ? ` — ${c.model}` : ""}${c.enabled ? "" : ` (${t("set.conn.disabled").toLowerCase()})`}`])];
      return h("div", { class: "role-row" },
        h("div", { class: "role-text" }, h("strong", null, t(`set.role.${r}`)), h("span", { class: "muted" }, t(`set.role.${r}.desc`))),
        bindSelect(draft.roles, r, opts));
    })));
}

function qualitySection() {
  return section("quality", "set.quality", null,
    h("div", { class: "grid-2" },
      field(t("set.quality.ctx"), bindInput(draft, "quality_context_tokens", { type: "number", min: 2048, step: 1024, parse: intOrNull }), t("set.quality.ctxHint")),
      field(t("set.quality.out"), bindInput(draft, "max_output_tokens", { type: "number", min: 256, step: 256, parse: intOrNull }))),
    field(t("set.quality.think"), switchEl(draft.allow_thinking, (v) => { draft.allow_thinking = v; }, t("set.quality.thinkLabel")),
      t("set.quality.thinkHint")));
}

function webSection() {
  const w = draft.web;
  const providers = [["duckduckgo", "DuckDuckGo", "Geen sleutel nodig"], ["searxng", "SearXNG", "Eigen (lokale) instantie"], ["brave", "Brave Search", "API-key vereist"]];
  return section("web", "set.web", t("set.web.lead"),
    field(t("set.web.provider"), h("div", { class: "radio-cards", role: "radiogroup" }, providers.map(([v, label, sub]) =>
      h("label", { class: ["radio-card", w.provider === v && "on"] },
        h("input", { type: "radio", name: "web-provider", value: v, checked: w.provider === v, onchange: () => { w.provider = v; changed(); renderBody(); } }),
        h("strong", null, label), h("span", { class: "muted" }, sub))))),
    w.provider === "searxng" ? field(t("set.web.searxngUrl"), bindInput(w, "url", { placeholder: "http://localhost:8888", class: "mono", spellcheck: false })) : null,
    w.provider === "brave" ? field(t("set.web.braveKey"), bindInput(w, "api_key", { type: "password", autocomplete: "off" })) : null,
    h("div", { class: "field" }, switchEl(w.confirm_queries, (v) => { w.confirm_queries = v; }, t("set.web.confirm")), h("span", { class: "field-hint" }, t("set.web.confirmHint"))),
    field(t("set.web.domains"), h("textarea", { rows: 4, class: "mono", spellcheck: false, placeholder: "overheid.nl\neuropa.eu\nnoraonline.nl",
      value: (w.allow_domains || []).join("\n"),
      oninput: (e) => { w.allow_domains = e.target.value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean); changed(); } }), t("set.web.domainsHint"), { optional: true }));
}

// ---------------------------------------------------------------- opslaan

function validate() {
  ui.errors = {};
  let ok = true;
  for (const c of draft.connections) {
    const e = {};
    if (!c.name?.trim()) e.name = t("set.required");
    if (!/^https?:\/\/\S+$/i.test(c.base_url || "")) e.base_url = t("set.conn.urlHint");
    if (Object.keys(e).length) { ui.errors[c.id] = e; ui.editing.add(c); ok = false; }
  }
  return ok;
}

async function save() {
  if (!validate()) { renderBody(); toast(t("set.invalid"), "error"); document.querySelector(".conn-card .invalid")?.scrollIntoView({ block: "center" }); return; }
  for (const c of draft.connections) {
    if (!c._new) continue;
    const old = c.id;
    c.id = "\u0000";
    c.id = slug(c.name);
    for (const r of ROLES) if (draft.roles[r] === old) draft.roles[r] = c.id;
  }
  const payload = structuredClone({ ...draft, connections: draft.connections.map(({ _new, ...c }) => ({ ...c, name: c.name.trim(), base_url: c.base_url.trim() })) });
  els.saveBtn.disabled = true;
  mount(els.saveBtn, icon("spinner", 14), t("common.save"));
  try {
    const res = await api.saveSettings(payload);
    const next = res && typeof res === "object" && res.connections ? res : payload;
    // werkkopie behouden (zelfde objecten, zodat open editors blijven), maar zonder _new-markering
    draft.connections.forEach((c) => delete c._new);
    state.settings = next;
    saved = JSON.stringify(draft);
    const langChanged = next.ui_language && next.ui_language !== getLang();
    if (langChanged) { setLang(next.ui_language); toast(t("set.saved"), "success"); app.rerenderAll(); return; }
    toast(t("set.saved"), "success");
    mount(els.status, h("span", { class: "saved-ok" }, icon("check", 13), t("common.saved")));
    app.renderSidebar();
  } catch (e) {
    toast(t("set.saveFailed", { error: e.message }), "error", 6000);
    mount(els.status, h("span", { class: "save-error" }, icon("alert", 13), t("set.saveFailed", { error: e.message })));
  } finally {
    mount(els.saveBtn, icon("check", 14), t("common.save"));
    els.saveBtn.disabled = !settingsDirty();
  }
}

export function discardSettings() { draft = null; saved = ""; ui.editing.clear(); ui.tests.clear(); }
