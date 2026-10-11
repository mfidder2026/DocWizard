// Rechterpaneel: document met versiekeuze, voorvertoning (sandboxed), outline, bronnen en platen.
import { t } from "../i18n.js";
import { api, authUrl } from "./api.js";
import { h, mount, emptyState, fmtDate, isMac } from "./dom.js";
import { icon } from "./icons.js";
import { state, app, on, currentProject } from "./state.js";
import { sourceItem, artifactCard, openInWord } from "./jobcard.js";

const ds = {
  projectId: null, docs: [], docId: null, versionId: null, tab: "preview",
  previewCache: new Map(), outlineCache: new Map(), sources: [], artifacts: [], loading: false, error: null,
};

on("chat-panel", ({ sources, artifacts }) => {
  ds.sources = sources; ds.artifacts = artifacts;
  if (ds.tab === "sources" || ds.tab === "diagrams") renderDocPanel(); else renderTabs();
});

const root = () => document.getElementById("docpanel");

export async function loadDocuments(selectVersionId = null) {
  const p = currentProject();
  ds.projectId = p?.id || null;
  if (!p) { ds.docs = []; renderDocPanel(); return; }
  ds.loading = true; renderDocPanel();
  try {
    ds.docs = await api.documents(p.id);
    ds.error = null;
  } catch (e) { ds.docs = []; ds.error = e.message; }
  ds.loading = false;
  if (selectVersionId) selectVersion(selectVersionId, false);
  if (!ds.docs.find((d) => d.id === ds.docId)) ds.docId = ds.docs[ds.docs.length - 1]?.id || null;
  const doc = currentDoc();
  if (doc && !doc.versions.find((v) => v.id === ds.versionId)) ds.versionId = doc.versions[doc.versions.length - 1]?.id || null;
  renderDocPanel();
}

function currentDoc() { return ds.docs.find((d) => d.id === ds.docId) || null; }

function selectVersion(vid, render = true) {
  for (const d of ds.docs) if (d.versions.some((v) => v.id === vid)) { ds.docId = d.id; ds.versionId = vid; }
  if (render) renderDocPanel();
}

export async function previewVersion(vid) {
  if (!ds.docs.some((d) => d.versions.some((v) => v.id === vid))) await loadDocuments(vid);
  else selectVersion(vid, false);
  ds.tab = "preview";
  if (!state.panes.doc) app.togglePane("doc");
  renderDocPanel();
}

let tabsEl = null;

export function renderDocPanel() {
  const el = root();
  if (!el) return;
  const project = currentProject();
  const doc = currentDoc();
  const head = h("div", { class: "dp-head" },
    h("h2", null, icon("file", 15), t("doc.title")),
    h("button", { class: "icon-btn", "aria-label": t("doc.collapse"), title: t("doc.collapse"), onclick: () => app.togglePane("doc") }, icon("panel-right", 16)));
  if (!project) { mount(el, head, h("div", { class: "dp-content" }, emptyState("folder", t("doc.noProject"), null))); return; }

  let top = null;
  if (doc) {
    const v = doc.versions.find((x) => x.id === ds.versionId);
    top = h("div", { class: "dp-top" },
      h("div", { class: "dp-selects" },
        ds.docs.length > 1
          ? h("select", { class: "select sm", "aria-label": t("doc.title"), onchange: (e) => { ds.docId = e.target.value; const d = currentDoc(); ds.versionId = d.versions[d.versions.length - 1]?.id; renderDocPanel(); } },
            ds.docs.map((d) => h("option", { value: d.id, selected: d.id === ds.docId }, d.name)))
          : h("div", { class: "dp-docname", title: doc.name }, doc.name),
        h("select", { class: "select sm version-select", "aria-label": t("doc.version"), onchange: (e) => { ds.versionId = e.target.value; renderDocPanel(); } },
          [...doc.versions].reverse().map((x) => h("option", { value: x.id, selected: x.id === ds.versionId }, `v${x.number}${changesLabel(x.changes) ? ` — ${changesLabel(x.changes)}` : ""}`)))),
      v ? h("div", { class: "dp-meta" }, `${t("doc.version")} ${v.number} · ${fmtDate(v.created_at)}`) : null,
      v ? h("div", { class: "dp-actions" },
        h("button", { class: "btn sm primary", onclick: () => openInWord(v.id) }, icon("external", 13), t("artifact.openWord")),
        h("a", { class: "btn sm", href: authUrl(`/api/versions/${v.id}/download`), download: "" }, icon("download", 13), t("artifact.download")),
        h("button", { class: "btn sm ghost", onclick: () => openInWord(v.id, true) }, icon("folder", 13), isMac ? t("doc.reveal") : t("doc.revealWin"))) : null);
  }

  tabsEl = h("div", { class: "tabs", role: "tablist" });
  const content = h("div", { class: "dp-content" });
  mount(el, head, top, tabsEl, content);
  renderTabs();
  renderTabContent(content, doc);
}

function renderTabs() {
  if (!tabsEl) return;
  const diagrams = ds.artifacts.filter((a) => a.kind === "diagram");
  const tabs = [["preview", t("doc.preview")], ["outline", t("doc.outline")], ["sources", `${t("doc.sources")}${ds.sources.length ? ` ${ds.sources.length}` : ""}`], ["diagrams", `${t("doc.diagrams")}${diagrams.length ? ` ${diagrams.length}` : ""}`]];
  mount(tabsEl, tabs.map(([k, label]) => h("button", { class: ["tab", ds.tab === k && "on"], role: "tab", "aria-selected": String(ds.tab === k), onclick: () => { ds.tab = k; renderDocPanel(); } }, label)));
}

async function renderTabContent(content, doc) {
  const vid = ds.versionId;
  if (ds.tab === "sources") {
    mount(content, ds.sources.length ? h("ul", { class: "jc-sources dp-list" }, ds.sources.map(sourceItem)) : emptyState("link", t("doc.noSources"), null));
    return;
  }
  if (ds.tab === "diagrams") {
    const d = ds.artifacts.filter((a) => a.kind === "diagram");
    mount(content, d.length ? h("div", { class: "dp-diagrams" }, d.map((a) => artifactCard(a))) : emptyState("diagram", t("doc.noDiagrams"), null));
    return;
  }
  if (ds.loading) { mount(content, h("div", { class: "dp-loading" }, icon("spinner", 16), t("common.loading"))); return; }
  if (ds.error) { mount(content, emptyState("alert", t("common.error"), ds.error)); return; }
  if (!doc || !vid) { mount(content, emptyState("file", t("doc.empty"), t("doc.empty.text"))); return; }

  if (ds.tab === "outline") {
    mount(content, h("div", { class: "dp-loading" }, icon("spinner", 16)));
    try {
      if (!ds.outlineCache.has(vid)) ds.outlineCache.set(vid, await api.outline(vid));
      const ol = ds.outlineCache.get(vid);
      if (ds.versionId !== vid || ds.tab !== "outline") return;
      mount(content, ol.length ? h("ol", { class: "dp-outline" }, ol.map((o) => h("li", { class: `lvl-${o.level || 1}` }, h("span", { class: "num" }, o.number), h("span", null, o.title))))
        : emptyState("list", t("doc.noOutline"), null));
    } catch (e) { mount(content, emptyState("alert", t("common.error"), e.message)); }
    return;
  }
  // voorvertoning
  mount(content, h("div", { class: "dp-loading" }, icon("spinner", 16), t("common.loading")));
  try {
    if (!ds.previewCache.has(vid)) ds.previewCache.set(vid, await api.preview(vid));
    if (ds.versionId !== vid || ds.tab !== "preview") return;
    const frame = h("iframe", { class: "preview-frame", sandbox: "", title: t("doc.preview"), referrerpolicy: "no-referrer" });
    frame.srcdoc = pageDoc(ds.previewCache.get(vid));
    mount(content, h("div", { class: "preview-wrap" }, frame));
  } catch (e) { mount(content, emptyState("alert", t("common.error"), e.message)); }
}

/** Zet het HTML-fragment in een papierachtig vel. Het iframe is volledig gesandboxed (geen scripts). */
function pageDoc(fragment) {
  const dark = document.documentElement.dataset.resolvedTheme === "dark";
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'">
<style>
html{background:${dark ? "#0e0f11" : "#eceef1"};}
body{margin:0;padding:20px 16px 40px;font:11pt/1.5 Calibri,Carlito,"Segoe UI",-apple-system,Helvetica,Arial,sans-serif;color:#1b1d22;}
.sheet{background:#fff;max-width:640px;margin:0 auto;padding:56px 56px 72px;box-shadow:0 1px 3px rgba(0,0,0,.12),0 8px 24px rgba(0,0,0,.08);border-radius:2px;min-height:600px;}
h1,h2,h3,h4{color:#004B87;font-weight:600;line-height:1.25;margin:1.4em 0 .5em}
h1{font-size:20pt;margin-top:0}h2{font-size:15pt}h3{font-size:12.5pt}h4{font-size:11pt}
.doc-meta{color:#6b7280;font-size:9.5pt;margin-top:-.4em}
p{margin:0 0 .7em}table{border-collapse:collapse;width:100%;margin:.8em 0;font-size:9.5pt}
th{background:#004B87;color:#fff;text-align:left}th,td{border:1px solid #c9d2dc;padding:4px 6px;vertical-align:top}
img,svg{max-width:100%;height:auto}a{color:#004B87}
ins{background:#e6f4ea;text-decoration:none}del{background:#fde8e8}
@media (max-width:520px){body{padding:12px 10px 32px}.sheet{padding:32px 26px 48px;font-size:10.5pt}h1{font-size:17pt}}
</style></head><body><div class="sheet">${fragment}</div></body></html>`;
}

export function resetDocPanel() {
  ds.docId = null; ds.versionId = null; ds.previewCache.clear(); ds.outlineCache.clear();
}

function changesLabel(c) {
  if (!c) return "";
  if (typeof c === "string") return c;
  return c.actie || c.bron || Object.values(c).filter((v) => typeof v === "string").join(", ");
}
