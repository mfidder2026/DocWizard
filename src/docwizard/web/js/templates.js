// Sjablonen: uploaden (.docx/.dotx en .pptx/.potx), lijst, en profiel bekijken (structuur, velden, huisstijl).
import { t } from "../i18n.js";
import { api } from "./api.js";
import { h, mount, toast, emptyState, fmtDate, fmtBytes } from "./dom.js";
import { icon } from "./icons.js";
import { state, app } from "./state.js";

let selectedId = null;
let pendingFile = null;

export async function renderTemplates(id) {
  if (id !== undefined) selectedId = id;
  const main = document.getElementById("main");
  main.className = "main view-page";
  const list = h("div", { class: "tpl-list" });
  const detail = h("div", { class: "tpl-detail" });
  mount(main,
    h("header", { class: "page-head" },
      !state.panes.sidebar ? h("button", { class: "icon-btn", "aria-label": t("nav.expand"), onclick: () => app.togglePane("sidebar") }, icon("panel-left", 16)) : null,
      h("h1", null, t("tpl.title"))),
    h("div", { class: "page tpl-page" },
      h("div", { class: "tpl-side" }, uploadCard(), list),
      detail));
  await app.loadTemplates();
  renderList(list, detail);
}

function renderList(list, detail) {
  if (!state.templates.length) {
    mount(list);
    mount(detail, emptyState("template", t("tpl.empty"), t("tpl.empty.text")));
    return;
  }
  if (!state.templates.some((x) => x.id === selectedId)) selectedId = state.templates[0].id;
  mount(list, state.templates.map((tp) => h("button", { class: ["tpl-item", tp.id === selectedId && "on"], onclick: () => { selectedId = tp.id; renderList(list, detail); } },
    h("div", { class: "tpl-item-icon" }, icon("template", 16)),
    h("div", { class: "tpl-item-text" },
      h("strong", null, tp.name),
      h("span", { class: "muted" }, [t("tpl.version", { v: tp.version ?? 1 }), fmtDate(tp.created_at)].filter(Boolean).join(" · ")),
      tp.profile_summary ? h("span", { class: "muted small" }, tp.profile_summary) : null))));
  renderDetail(detail, selectedId);
}

function uploadCard() {
  const nameInput = h("input", { type: "text", placeholder: "Architecture Quick Scan", "aria-label": t("tpl.name") });
  const fileLabel = h("span", { class: "drop-text" }, t("tpl.dropHere"));
  const fileInput = h("input", { type: "file", accept: ".docx,.dotx,.pptx,.potx", hidden: true,
    onchange: (e) => pick(e.target.files[0]) });
  const btn = h("button", { class: "btn primary", disabled: true, onclick: () => upload() }, icon("upload", 14), t("tpl.upload"));
  const err = h("div", { class: "field-error" });
  const drop = h("button", { class: "drop", type: "button", onclick: () => fileInput.click(),
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
    ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); pick(e.dataTransfer.files[0]); } },
  icon("upload", 20), fileLabel);

  function pick(f) {
    err.textContent = "";
    if (!f) return;
    if (!/\.(docx|dotx|pptx|potx)$/i.test(f.name)) { err.textContent = t("tpl.chooseFile"); return; }
    pendingFile = f;
    mount(fileLabel, h("strong", null, f.name), h("span", { class: "muted" }, ` · ${fmtBytes(f.size)}`));
    if (!nameInput.value) nameInput.value = f.name.replace(/\.(docx|dotx|pptx|potx)$/i, "").replace(/[_-]+/g, " ");
    btn.disabled = false;
  }

  async function upload() {
    if (!pendingFile) return;
    btn.disabled = true;
    mount(btn, icon("spinner", 14), t("tpl.analyzing"));
    try {
      const tp = await api.upload("/api/templates", { file: pendingFile, name: nameInput.value.trim() || pendingFile.name });
      toast(t("tpl.uploaded", { name: tp.name }), "success");
      pendingFile = null;
      selectedId = tp.id;
      renderTemplates();
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
      mount(btn, icon("upload", 14), t("tpl.upload"));
    }
  }

  return h("div", { class: "card upload-card" },
    h("h3", null, t("tpl.upload")),
    h("p", { class: "muted small" }, t("tpl.lead")),
    drop, fileInput,
    h("label", { class: "field" }, h("span", { class: "field-label" }, t("tpl.name")), nameInput),
    err, btn);
}

async function renderDetail(el, id) {
  mount(el, h("div", { class: "dp-loading" }, icon("spinner", 16), t("common.loading")));
  let tp;
  try { tp = await api.template(id); } catch (e) { mount(el, emptyState("alert", t("common.error"), e.message)); return; }
  if (id !== selectedId) return;
  const p = tp.profile || {};
  const hs = p.house_style || p.huisstijl || {};
  const fields = p.fields || p.velden || [];
  const outline = p.outline || p.structure || p.structuur || [];
  const guide = p.writing_guide || p.schrijfgids || null;
  mount(el,
    h("div", { class: "tpl-head" },
      h("div", null, h("h2", null, tp.name),
        h("div", { class: "muted" }, [p.doc_type, p.language?.toUpperCase(), t("tpl.version", { v: tp.version ?? 1 }), tp.filename].filter(Boolean).join(" · "))),
      h("button", { class: "btn", onclick: () => app.newProject?.() }, icon("plus", 14), t("project.new"))),
    h("div", { class: "tpl-grid" },
      h("div", { class: "card" }, h("h3", null, icon("list", 15), t("tpl.outline")),
        outline.length ? h("ol", { class: "outline-tree" }, outline.map(outlineNode)) : h("p", { class: "muted" }, t("doc.noOutline"))),
      h("div", { class: "tpl-col" },
        h("div", { class: "card" }, h("h3", null, icon("key", 15), t("tpl.fields"), h("span", { class: "count" }, fields.length)),
          fields.length ? h("table", { class: "table" },
            h("thead", null, h("tr", null, ["tpl.field.label", "tpl.field.key", "tpl.field.source", "tpl.field.default"].map((k) => h("th", null, t(k))))),
            h("tbody", null, fields.map((f) => h("tr", null, h("td", null, f.label || ""), h("td", { class: "mono" }, f.key || f.sleutel || ""), h("td", { class: "mono muted" }, f.source || f.bron || ""), h("td", null, f.default || f.standaard || "")))))
            : h("p", { class: "muted" }, "—")),
        h("div", { class: "card" }, h("h3", null, icon("sparkle", 15), t("tpl.houseStyle")), kvList(hs)),
        guide ? h("div", { class: "card" }, h("h3", null, icon("edit", 15), t("tpl.writingGuide")), kvList(guide)) : null)),
    h("details", { class: "card raw" }, h("summary", null, t("tpl.raw")), h("pre", { class: "mono" }, JSON.stringify(p, null, 2))));
}

function outlineNode(o) {
  const kids = o.children || o.subsecties || [];
  const extras = [];
  if (o.guidance || o.instructie) extras.push(h("div", { class: "guidance" }, h("span", { class: "tag" }, t("tpl.guidance")), o.guidance || o.instructie));
  if (o.fixed_text || o.vaste_tekst) extras.push(h("div", { class: "guidance fixed" }, h("span", { class: "tag" }, t("tpl.fixedText")), o.fixed_text || o.vaste_tekst));
  const meta = [];
  if (o.length || o.lengte) meta.push(`${t("tpl.length")}: ${o.length || o.lengte}`);
  if (o.table?.columns || o.tabel?.kolommen) meta.push(`${t("tpl.table")}: ${(o.table?.columns || o.tabel.kolommen).join(" | ")}`);
  if (o.research || o.onderzoek) meta.push(`${t("tpl.research")}: ${(o.research || o.onderzoek).join(", ")}`);
  return h("li", null,
    h("div", { class: "outline-title" }, h("span", { class: "num" }, o.number ?? o.nummer ?? ""), h("span", null, o.title || o.titel)),
    extras, meta.length ? h("div", { class: "outline-meta" }, meta.join(" · ")) : null,
    kids.length ? h("ol", null, kids.map(outlineNode)) : null);
}

const LABELS = {
  headings: "Koppen", body: "Platte tekst", bullet: "Opsomming", table: "Tabel", numbered_headings: "Genummerde koppen",
  fonts: "Lettertypen", tone: "Toon", perspective: "Perspectief", forbidden: "Vermijden", examples: "Voorbeelden",
};

function kvList(obj) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return h("p", { class: "muted" }, "—");
  const fmt = (v) => {
    if (v === true) return t("common.yes");
    if (v === false) return t("common.no");
    if (Array.isArray(v)) return v.join(", ");
    if (v && typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(" · ");
    return String(v ?? "");
  };
  return h("dl", { class: "kv" }, entries.map(([k, v]) => [h("dt", null, LABELS[k] || k), h("dd", null, fmt(v))]));
}
