// Kennis: projectdocumenten toevoegen aan de lokale kennisbank van een project.
import { t } from "../i18n.js";
import { api } from "./api.js";
import { h, mount, toast, emptyState, fmtBytes, fmtDate } from "./dom.js";
import { icon } from "./icons.js";
import { state, app } from "./state.js";

export async function renderKnowledge() {
  const main = document.getElementById("main");
  main.className = "main view-page";
  const pid = state.currentProjectId && state.projects.some((p) => p.id === state.currentProjectId) ? state.currentProjectId : state.projects[0]?.id;
  const listEl = h("div", { class: "kn-list" });
  const fileInput = h("input", { type: "file", multiple: true, hidden: true, onchange: (e) => upload([...e.target.files]) });
  const select = h("select", { onchange: (e) => { state.currentProjectId = e.target.value; renderKnowledge(); } },
    state.projects.map((p) => h("option", { value: p.id, selected: p.id === pid }, p.name)));
  mount(main,
    h("header", { class: "page-head" },
      !state.panes.sidebar ? h("button", { class: "icon-btn", "aria-label": t("nav.expand"), onclick: () => app.togglePane("sidebar") }, icon("panel-left", 16)) : null,
      h("h1", null, t("kn.title"))),
    h("div", { class: "page narrow" },
      h("p", { class: "lead" }, t("kn.lead")),
      !pid ? emptyState("folder", t("doc.noProject"), null, h("button", { class: "btn primary", onclick: () => app.newProject() }, icon("plus", 14), t("nav.newProject"))) :
        h("frag", null,
          h("div", { class: "kn-bar" }, h("label", { class: "field field-inline" }, h("span", { class: "field-label" }, t("kn.project")), select),
            h("button", { class: "btn primary", onclick: () => fileInput.click() }, icon("upload", 14), t("kn.upload")), fileInput),
          h("div", { class: "drop big", ondragover: (e) => e.preventDefault(), ondrop: (e) => { e.preventDefault(); upload([...e.dataTransfer.files]); } },
            icon("upload", 20), h("span", null, t("chat.drop"))),
          listEl)));
  if (pid) load();

  async function load() {
    try {
      const files = await api.get(`/api/projects/${pid}/files`);
      mount(listEl, files.length ? h("ul", { class: "kn-files" }, files.map((f) => h("li", null, icon("file", 15), h("span", { class: "grow" }, f.name), h("span", { class: "muted" }, fmtBytes(f.size)), h("span", { class: "muted" }, fmtDate(f.created_at)))))
        : emptyState("book", t("kn.empty"), t("kn.empty.text")));
    } catch {
      mount(listEl, emptyState("book", t("kn.empty"), t("kn.empty.text")));
    }
  }

  async function upload(files) {
    let n = 0;
    for (const file of files) {
      try { await api.upload("/api/files", { file, project_id: pid }); n++; } catch (e) { toast(t("chat.uploadFailed", { error: e.message }), "error"); }
    }
    if (n) toast(t("kn.added", { n }), "success");
    load();
  }
}
