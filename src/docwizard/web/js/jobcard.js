// Jobkaart: laat live zien wat DocWizard doet — stappen, voortgang, ETA, tokens/s, live tekst,
// denkstappen, bronnen, logboek, vragen (met editor) en artefacten.
import { t } from "../i18n.js";
import { api, authUrl } from "./api.js";
import { h, mount, clear, toast, fmtDuration, fmtEta, toTime, fmtClock, openModal } from "./dom.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";

const ACTIVE = new Set();
setInterval(() => ACTIVE.forEach((c) => c.tick()), 1000);

const STATUS_MAP = { completed: "done", succeeded: "done", success: "done", canceled: "cancelled", error: "failed", pending: "queued" };
const normStatus = (s) => STATUS_MAP[s] || s || "queued";
const FINAL = new Set(["done", "failed", "cancelled"]);

const KIND_ICON = { web: "globe", file: "file", project: "folder", knowledge: "book" };

export class JobCard {
  constructor(jobId, ctx = {}, pipelineHint = null) {
    this.id = jobId;
    this.ctx = ctx;
    this.job = { id: jobId, pipeline: pipelineHint, title: "", status: "queued", progress: 0, error: null };
    this.steps = new Map();           // key -> {key,label,status,detail,t0,t1,out,tokens}
    this.reasoning = [];               // [{key, text}]
    this.sources = [];
    this.logs = [];
    this.artifacts = [];
    this.questions = new Map();        // id -> {ev, answered, label, el}
    this.lastEventId = 0;             // hoogste opgeslagen event-id (voor /events?after=)
    this.seen = new Set();             // handtekeningen van verwerkte (opgeslagen) events, tegen dubbelen
    this.tps = null; this.eta = null; this.tokens = 0;
    this.t0 = null; this.t1 = null;
    this.selected = null;              // geselecteerde stap; null = live volgen
    this.liveKey = null;
    this.expanded = true;
    this.userToggled = false;
    this.stopArmed = false;
    this.pending = false;
    this.compact = pipelineHint === "chat";
    this.el = h("div", { class: "job-wrap", dataset: { jobId } });
    this.build();
    ACTIVE.add(this);
  }

  // ---------------------------------------------------------------- opbouw

  build() {
    if (this.compact) return this.buildCompact();
    this.ui = {};
    const u = this.ui;
    u.statusIcon = h("span", { class: "jc-status-icon" });
    u.title = h("span", { class: "jc-title-text" });
    u.pill = h("span", { class: "pill" });
    u.meta = h("span", { class: "jc-meta" });
    u.pauseBtn = h("button", { class: "btn sm ghost", onclick: () => this.togglePause() });
    u.stopBtn = h("button", { class: "btn sm ghost danger-text", onclick: () => this.stop() });
    u.retryBtn = h("button", { class: "btn sm", hidden: true, title: t("job.retryTip"), onclick: () => this.retry() }, icon("refresh", 13), t("job.retry"));
    u.toggleBtn = h("button", { class: "icon-btn sm", "aria-label": t("job.details"), onclick: () => { this.userToggled = true; this.setExpanded(!this.expanded); } });
    u.bar = h("div", { class: "bar-fill" });
    u.pct = h("span", { class: "jc-pct" });
    u.steps = h("ol", { class: "jc-steps" });
    u.liveLabel = h("span", { class: "jc-live-label" });
    u.followBtn = h("button", { class: "btn xs ghost", hidden: true, onclick: () => { this.selected = null; this.renderLive(true); this.renderSteps(); } }, t("job.followLive"));
    u.stream = h("div", { class: "jc-stream mono", tabindex: "0", "aria-live": "off" });
    u.live = h("div", { class: "jc-live" }, h("div", { class: "jc-live-head" }, h("span", { class: "live-dot" }), u.liveLabel, u.followBtn), u.stream);
    u.reasoningBody = h("div", { class: "jc-reasoning-body mono" });
    u.reasoningSum = h("summary", null);
    u.reasoning = h("details", { class: "jc-fold", hidden: true }, u.reasoningSum, u.reasoningBody);
    u.sourcesBody = h("ul", { class: "jc-sources" });
    u.sourcesSum = h("summary", null);
    u.sourcesBox = h("details", { class: "jc-fold", hidden: true }, u.sourcesSum, u.sourcesBody);
    u.logBody = h("ul", { class: "jc-log mono" });
    u.logSum = h("summary", null);
    u.logBox = h("details", { class: "jc-fold", hidden: true }, u.logSum, u.logBody);
    u.body = h("div", { class: "jc-body" }, u.steps, u.live, h("div", { class: "jc-folds" }, u.reasoning, u.sourcesBox, u.logBox));
    u.questions = h("div", { class: "jc-questions" });
    u.artifacts = h("div", { class: "jc-artifacts" });
    u.error = h("div", { class: "jc-error", hidden: true });
    u.card = h("section", { class: "job-card", "aria-label": "Job" },
      h("header", { class: "jc-head" }, u.statusIcon,
        h("div", { class: "jc-title" }, u.title, u.pill),
        u.meta,
        h("div", { class: "jc-actions" }, u.retryBtn, u.pauseBtn, u.stopBtn, u.toggleBtn)),
      h("div", { class: "jc-progress" }, h("div", { class: "bar" }, u.bar), u.pct),
      u.body, u.questions, u.error, u.artifacts);
    mount(this.el, u.card);
    this.renderAll();
  }

  buildCompact() {
    const u = (this.ui = {});
    u.reasoningBody = h("div", { class: "jc-reasoning-body mono" });
    u.reasoningSum = h("summary", null, t("job.reasoning"));
    u.reasoning = h("details", { class: "jc-fold compact", hidden: true }, u.reasoningSum, u.reasoningBody);
    u.text = h("div", { class: "md" });
    u.status = h("div", { class: "thinking" }, icon("spinner", 14), h("span", null, t("job.thinking")));
    u.error = h("div", { class: "jc-error", hidden: true });
    u.stopBtn = h("button", { class: "btn xs ghost", onclick: () => this.stop(true) }, icon("stop", 12), t("job.stop"));
    mount(this.el, h("div", { class: "msg msg-assistant streaming" },
      h("div", { class: "avatar ai" }, icon("sparkle", 14)),
      h("div", { class: "msg-body" },
        h("div", { class: "msg-head" }, h("span", { class: "msg-author" }, t("chat.assistant")), u.stopBtn),
        u.reasoning, u.status, u.text, u.error)));
    this.renderAll();
  }

  setPipeline(p) {
    if (!p || p === this.job.pipeline) return;
    this.job.pipeline = p;
    const compact = p === "chat";
    if (compact !== this.compact) { this.compact = compact; this.build(); }
  }

  // ---------------------------------------------------------------- events

  applyJobState(job) {
    if (!job) return;
    this.setPipeline(job.pipeline);
    if (job.title) this.job.title = job.title;
    this.job.status = normStatus(job.status);
    if (typeof job.progress === "number") this.job.progress = job.progress;
    this.job.error = job.error || null;
    if (job.pausable !== undefined) this.job.pausable = !!job.pausable;
    if (Array.isArray(job.steps) && job.steps.length) this.reorder(job.steps.map((s) => s.key));
    for (const s of job.steps || []) {
      const st = this.ensureStep(s.key, s.label);
      st.status = s.status || st.status;
      if (s.detail) st.detail = s.detail;
      st.t0 = toTime(s.started_at) ?? st.t0;
      st.t1 = toTime(s.ended_at) ?? st.t1;
      if (st.t0 && (!this.t0 || st.t0 < this.t0)) this.t0 = st.t0;
    }
    const pq = job.pending_question;
    if (pq && pq.question_id) {
      const q = this.questions.get(pq.question_id);
      if (q) q.answered = false; else this.questions.set(pq.question_id, { ev: pq, answered: false, label: null });
      if (this.job.status !== "paused") this.job.status = "waiting";
    }
    if (FINAL.has(this.job.status)) this.finish(true);
    this.renderAll();
  }

  /** Zet de stappen in de volgorde die de engine opgeeft (stappen kunnen tussentijds worden ingevoegd). */
  reorder(keys) {
    const next = new Map();
    for (const k of keys) next.set(k, this.steps.get(k) || { key: k, label: k, status: "pending", detail: null, t0: null, t1: null, out: "", tokens: 0 });
    for (const [k, v] of this.steps) if (!next.has(k)) next.set(k, v);
    this.steps = next;
  }

  ensureStep(key, label) {
    let st = this.steps.get(key);
    if (!st) { st = { key, label: label || key, status: "pending", detail: null, t0: null, t1: null, out: "", tokens: 0 }; this.steps.set(key, st); }
    else if (label) st.label = label;
    return st;
  }

  /** Verwerk één SSE-event. replay=true bij herstel uit /events (geen animaties/auto-acties). */
  apply(ev, replay = false) {
    // Opgeslagen events hebben een oplopend id (`seq` in de mock; `id` in de engine, behalve bij artifact).
    const num = typeof ev.seq === "number" ? ev.seq : (typeof ev.id === "number" && ev.type !== "artifact" ? ev.id : null);
    if (num != null) this.lastEventId = Math.max(this.lastEventId, num);
    if (!["token", "reasoning", "stats"].includes(ev.type)) {
      const sig = [ev.type, ev.ts, ev.key, ev.status, ev.detail, ev.question_id, ev.title, ev.text, ev.kind, ev.version_id, ev.message?.id].join("\u0001");
      if (this.seen.has(sig)) return;
      this.seen.add(sig);
    }
    const ts = toTime(ev.ts) || Date.now();
    switch (ev.type) {
      case "job_started":
        this.setPipeline(ev.pipeline);
        this.job.title = ev.title || this.job.title;
        (ev.steps || []).forEach((s) => this.ensureStep(s.key, s.label));
        if (ev.steps?.length) this.reorder(ev.steps.map((s) => s.key));
        if (!this.t0) this.t0 = ts;
        if (this.job.status === "queued") this.job.status = "running";
        this.renderAll();
        break;
      case "step": {
        const st = this.ensureStep(ev.key, ev.label);
        st.status = ev.status;
        if (ev.detail !== undefined && ev.detail !== null) st.detail = ev.detail;
        if (ev.status === "running") { st.t0 = toTime(ev.started_at) || ts; st.t1 = null; this.liveKey = ev.key; }
        if (["done", "failed", "skipped"].includes(ev.status)) st.t1 = toTime(ev.ended_at) || ts;
        if (typeof ev.progress === "number") this.job.progress = ev.progress;
        if (!this.t0) this.t0 = ts;
        if (["queued", "waiting"].includes(this.job.status) && ev.status === "running") this.job.status = "running";
        if (ev.status === "running") this.resolveQuestions();
        this.renderHeader(); this.renderSteps(); this.renderLive();
        if (this.compact) this.renderCompact();
        break;
      }
      case "token": {
        const st = this.ensureStep(ev.key || this.liveKey || "_", null);
        st.out += ev.text || "";
        st.tokens++; this.tokens++;
        if (!this.liveKey) this.liveKey = st.key;
        if (this.compact) { this.scheduleCompact(); break; }
        if (this._streamKey === st.key) this.appendStream(ev.text || "");
        else if (!this.selected) this.renderLive();
        this.scheduleHeader();
        break;
      }
      case "reasoning": {
        const last = this.reasoning[this.reasoning.length - 1];
        if (last && last.key === ev.key) last.text += ev.text || "";
        else this.reasoning.push({ key: ev.key, text: ev.text || "" });
        this.scheduleReasoning();
        break;
      }
      case "log":
        this.logs.push({ level: ev.level || "info", text: ev.text, ts });
        this.renderLog();
        break;
      case "source":
        this.sources.push(ev);
        this.renderSources();
        this.ctx.onSource?.(ev, replay);
        break;
      case "question":
        if (!this.questions.has(ev.question_id)) this.questions.set(ev.question_id, { ev, answered: false, label: null });
        this.job.status = "waiting";
        this.renderQuestions(); this.renderHeader();
        if (!replay) this.ctx.onAttention?.(this, ev);
        break;
      case "artifact":
        if (!this.artifacts.some((a) => a.kind === ev.kind && a.id === ev.id && a.version_id === ev.version_id)) {
          this.artifacts.push(ev);
          this.renderArtifacts();
          this.ctx.onArtifact?.(ev, replay);
        }
        break;
      case "stats":
        if (ev.tokens_per_s != null) this.tps = ev.tokens_per_s;
        if (ev.eta_s != null) { this.eta = ev.eta_s; this.etaAt = Date.now(); }
        this.scheduleHeader();
        break;
      case "job_status":
        this.job.status = normStatus(ev.status);
        this.renderHeader();
        break;
      case "job_done":
        this.job.status = "done"; this.job.progress = 1; this.t1 = ts; this.eta = 0;
        this.summary = ev.summary;
        this.finish(replay);
        break;
      case "job_failed":
        this.job.status = /gestopt|geannuleerd|annul|cancel|stopp/i.test(ev.error || "") ? "cancelled" : "failed";
        this.job.error = ev.error; this.t1 = ts;
        this.finish(replay);
        break;
      default:
        break;
    }
    if (!replay && ["step", "question", "artifact", "job_done", "job_failed"].includes(ev.type)) this.ctx.onGrow?.();
  }

  finish(replay) {
    this.resolveQuestions();
    for (const st of this.steps.values()) if (st.status === "running") st.status = this.job.status === "done" ? "done" : "failed";
    ACTIVE.delete(this);
    if (!this.compact && !this.userToggled) this.setExpanded(this.job.status !== "done", true);
    this.renderAll();
    if (!replay && this.job.status === "done") this.ctx.onDone?.(this);
  }

  resolveQuestions() {
    let changed = false;
    for (const q of this.questions.values()) if (!q.answered) { q.answered = true; changed = true; }
    if (changed) this.renderQuestions();
  }

  // ---------------------------------------------------------------- acties

  async togglePause() {
    const paused = this.job.status === "paused";
    try {
      if (paused) await api.resumeJob(this.id); else await api.pauseJob(this.id);
      this.job.status = paused ? "running" : "paused";
      this.renderHeader();
    } catch (e) {
      toast(e.status === 404 || e.status === 405 ? t("job.pauseUnsupported") : e.message, "warning");
    }
  }

  async retry() {
    this.ui.retryBtn.disabled = true;
    try {
      const res = await api.retryJob(this.id);
      this.retried = true;
      this.renderHeader();
      if (res?.job_id) this.ctx.onRetry?.(res.job_id, this);
    } catch (e) {
      toast(e.message, "error");
    } finally { this.ui.retryBtn.disabled = false; }
  }

  async stop(immediate = false) {
    if (!immediate && !this.stopArmed) {
      this.stopArmed = true;
      this.renderHeader();
      setTimeout(() => { this.stopArmed = false; this.renderHeader(); }, 3000);
      return;
    }
    this.stopArmed = false;
    try { await api.cancelJob(this.id); } catch (e) { toast(e.message, "error"); }
    this.renderHeader();
  }

  async answer(q, option, valueEl, errEl) {
    let value;
    const ed = q.ev.editable;
    if (ed && valueEl) {
      if (ed.kind === "json") {
        try { value = JSON.parse(valueEl.value); } catch (e) { errEl.textContent = t("job.invalidJson", { error: e.message }); valueEl.classList.add("invalid"); return; }
      } else value = valueEl.value;
    }
    errEl.textContent = ""; valueEl?.classList.remove("invalid");
    try {
      await api.answer(this.id, q.ev.question_id, option.value, ed ? value : null);
      q.answered = true; q.label = option.label;
      if (this.job.status === "waiting") this.job.status = "running";
      this.renderQuestions(); this.renderHeader();
    } catch (e) {
      errEl.textContent = t("job.answerFailed", { error: e.message });
    }
  }

  // ---------------------------------------------------------------- render

  setExpanded(v, silent) {
    this.expanded = v;
    if (this.compact || !this.ui.body) return;
    this.ui.body.hidden = !v;
    this.ui.card.classList.toggle("collapsed", !v);
    mount(this.ui.toggleBtn, icon(v ? "chevron-up" : "chevron-down", 15));
    this.ui.toggleBtn.title = v ? t("job.hideDetails") : t("job.details");
    if (!silent) this.ctx.onGrow?.();
  }

  renderAll() {
    if (this.compact) { this.renderCompact(); this.renderReasoning(); return; }
    this.setExpanded(this.expanded, true);
    this.renderHeader(); this.renderSteps(); this.renderLive(true); this.renderReasoning();
    this.renderSources(); this.renderLog(); this.renderQuestions(); this.renderArtifacts();
  }

  scheduleHeader() {
    if (this._hTimer) return;
    this._hTimer = setTimeout(() => { this._hTimer = null; this.renderHeader(); this.renderStepTokens(); }, 250);
  }

  scheduleReasoning() {
    if (this._rTimer) return;
    this._rTimer = setTimeout(() => { this._rTimer = null; this.renderReasoning(); }, 120);
  }

  scheduleCompact() {
    if (this._cTimer) return;
    this._cTimer = setTimeout(() => { this._cTimer = null; this.renderCompact(); this.ctx.onGrow?.(); }, 80);
  }

  tick() {
    if (this.compact) return;
    this.renderHeader();
    const running = [...this.steps.values()].find((s) => s.status === "running");
    if (running && running._durEl) running._durEl.textContent = fmtDuration(Date.now() - running.t0);
  }

  elapsed() {
    if (!this.t0) return null;
    return (this.t1 || (FINAL.has(this.job.status) ? this.t0 : Date.now())) - this.t0;
  }

  renderHeader() {
    if (this.compact) return;
    const u = this.ui, s = this.job.status;
    u.card.dataset.status = s;
    mount(u.statusIcon, icon({ running: "spinner", queued: "clock", waiting: "alert", paused: "pause", done: "check", failed: "alert", cancelled: "stop" }[s] || "spinner", 16));
    u.title.textContent = this.job.title || t(`action.${this.job.pipeline}`) || "Job";
    u.pill.textContent = t(`job.${s}`);
    u.pill.className = `pill pill-${s}`;
    const pct = Math.round((this.job.progress || 0) * 100);
    u.bar.style.width = `${pct}%`;
    u.pct.textContent = `${pct}%`;
    const parts = [];
    const el = this.elapsed();
    if (el != null) parts.push(h("span", { title: "Verstreken tijd" }, icon("clock", 12), fmtDuration(el)));
    if (!FINAL.has(s)) {
      if (this.eta != null && this.eta > 0) {
        const left = Math.max(0, this.eta - (Date.now() - (this.etaAt || Date.now())) / 1000);
        parts.push(h("span", null, t("job.eta", { t: fmtEta(left) })));
      }
      if (this.tps != null) parts.push(h("span", null, icon("zap", 12), t("job.tps", { n: this.tps })));
    }
    if (this.tokens) parts.push(h("span", { class: "muted" }, t("job.tokens", { n: this.tokens })));
    mount(u.meta, parts);
    const final = FINAL.has(s);
    u.pauseBtn.hidden = final || !this.job.pausable;
    u.retryBtn.hidden = !(s === "failed" || s === "cancelled") || this.retried;
    u.stopBtn.hidden = final;
    mount(u.pauseBtn, icon(s === "paused" ? "play" : "pause", 13), t(s === "paused" ? "job.resume" : "job.pause"));
    mount(u.stopBtn, icon("stop", 13), this.stopArmed ? t("job.stopConfirm") : t("job.stop"));
    u.stopBtn.classList.toggle("armed", this.stopArmed);
    if (this.job.error && s !== "done") { u.error.hidden = false; mount(u.error, icon("alert", 14), this.job.error); }
    else u.error.hidden = true;
  }

  renderSteps() {
    if (this.compact) return;
    const rows = [...this.steps.values()].map((st) => {
      const ic = { pending: "circle", running: "spinner", done: "check", failed: "x", skipped: "slash" }[st.status] || "circle";
      const dur = st.t0 ? fmtDuration((st.t1 || Date.now()) - st.t0) : "";
      st._durEl = h("span", { class: "jc-step-dur" }, st.status === "pending" ? "" : dur);
      st._tokEl = h("span", { class: "jc-step-tok" }, st.tokens ? `${st.tokens} tok` : "");
      const selected = (this.selected || this.liveKey) === st.key;
      return h("li", { class: ["jc-step", `st-${st.status}`, selected && "selected"] },
        h("button", { class: "jc-step-btn", title: st.out ? t("job.liveOf", { step: st.label }) : "", onclick: () => { this.selected = st.key === this.liveKey ? null : st.key; this.renderSteps(); this.renderLive(true); } },
          h("span", { class: "jc-step-icon" }, icon(ic, 14)),
          h("span", { class: "jc-step-label" }, st.label),
          st.detail ? h("span", { class: "jc-step-detail" }, st.detail) : null,
          st._tokEl, st._durEl));
    });
    mount(this.ui.steps, rows);
  }

  renderStepTokens() {
    for (const st of this.steps.values()) if (st._tokEl) st._tokEl.textContent = st.tokens ? `${st.tokens} tok` : "";
  }

  renderLive(reset = false) {
    if (this.compact) return;
    let key = this.selected || this.liveKey;
    if (!this.selected && key && !this.steps.get(key)?.out) {
      // lopende stap zonder uitvoer (bv. wachten op goedkeuring): toon de laatste stap mét uitvoer
      const withOut = [...this.steps.values()].filter((s) => s.out);
      if (withOut.length) key = withOut[withOut.length - 1].key;
    }
    const st = key ? this.steps.get(key) : null;
    const u = this.ui;
    const hasAny = [...this.steps.values()].some((s) => s.out);
    u.live.hidden = !hasAny && FINAL.has(this.job.status);
    u.live.classList.toggle("is-live", !this.selected && this.job.status === "running");
    u.liveLabel.textContent = st ? t("job.liveOf", { step: st.label }) : t("job.live");
    u.followBtn.hidden = !this.selected || FINAL.has(this.job.status);
    if (reset || this._streamKey !== key) {
      this._streamKey = key;
      u.stream.textContent = st?.out || "";
      if (!st?.out) u.stream.appendChild(h("span", { class: "muted" }, t("job.noOutput")));
      u.stream.scrollTop = u.stream.scrollHeight;
    }
  }

  appendStream(text) {
    const s = this.ui.stream;
    if (s.firstChild && s.firstChild.nodeType === 1 && s.firstChild.classList.contains("muted")) clear(s);
    const stick = s.scrollTop + s.clientHeight >= s.scrollHeight - 30;
    if (s.lastChild && s.lastChild.nodeType === 3) s.lastChild.appendData(text);
    else s.appendChild(document.createTextNode(text));
    if (stick) s.scrollTop = s.scrollHeight;
  }

  renderReasoning() {
    const u = this.ui;
    u.reasoning.hidden = this.reasoning.length === 0;
    if (!this.reasoning.length) return;
    if (!this.compact) mount(u.reasoningSum, icon("brain", 13), t("job.reasoning"));
    const b = u.reasoningBody;
    const stick = b.scrollTop + b.clientHeight >= b.scrollHeight - 30;
    mount(b, this.reasoning.map((r) => h("div", { class: "reason-block" },
      !this.compact ? h("div", { class: "reason-step" }, this.steps.get(r.key)?.label || r.key) : null,
      h("div", null, r.text))));
    if (stick) b.scrollTop = b.scrollHeight;
  }

  renderSources() {
    if (this.compact) return;
    const u = this.ui;
    u.sourcesBox.hidden = this.sources.length === 0;
    mount(u.sourcesSum, icon("link", 13), `${t("job.sources")} (${this.sources.length})`);
    mount(u.sourcesBody, this.sources.map(sourceItem));
  }

  renderLog() {
    if (this.compact) return;
    const u = this.ui;
    u.logBox.hidden = this.logs.length === 0;
    mount(u.logSum, icon("terminal", 13), `${t("job.log")} (${this.logs.length})`);
    mount(u.logBody, this.logs.map((l) => h("li", { class: `log-${l.level}` }, h("span", { class: "log-time" }, fmtClock(l.ts)), h("span", { class: "log-level" }, l.level), h("span", null, l.text))));
  }

  renderQuestions() {
    if (this.compact) return;
    mount(this.ui.questions, [...this.questions.values()].map((q) => this.questionEl(q)));
  }

  questionEl(q) {
    const ev = q.ev;
    if (q.answered) {
      return h("div", { class: "jc-question answered" }, icon("check", 14),
        h("span", null, ev.text), q.label ? h("span", { class: "muted" }, t("job.answered", { label: q.label })) : null);
    }
    const err = h("div", { class: "field-error" });
    let editor = null;
    if (ev.editable) {
      const val = ev.editable.kind === "json" ? JSON.stringify(ev.editable.value, null, 2) : String(ev.editable.value ?? "");
      editor = h("textarea", { class: "mono jc-editor", spellcheck: ev.editable.kind !== "json", rows: Math.min(18, Math.max(4, val.split("\n").length + 1)), value: val,
        oninput: (e) => {
          if (ev.editable.kind !== "json") return;
          try { JSON.parse(e.target.value); e.target.classList.remove("invalid"); err.textContent = ""; }
          catch (x) { e.target.classList.add("invalid"); err.textContent = t("job.invalidJson", { error: x.message }); }
        },
        onkeydown: (e) => {
          if (e.key === "Tab") { e.preventDefault(); const s = e.target.selectionStart; e.target.setRangeText("  ", s, e.target.selectionEnd, "end"); }
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); const first = (ev.options || [])[0]; if (first) this.answer(q, first, editor, err); }
        } });
    }
    const outlinePreview = ev.editable?.kind === "json" && Array.isArray(ev.editable.value) && ev.editable.value.every((o) => o && o.title)
      ? h("ol", { class: "jc-outline-preview" }, ev.editable.value.map((o) => h("li", { style: { paddingLeft: `${((o.level || 1) - 1) * 16}px` } }, h("span", { class: "num" }, o.number ?? ""), " ", o.title)))
      : null;
    return h("div", { class: "jc-question", role: "group" },
      h("div", { class: "jc-q-head" }, icon("alert", 15), h("strong", null, ev.text)),
      outlinePreview && editor ? h("details", { class: "jc-q-edit" }, h("summary", null, t("job.editValue"), " (JSON)"), editor) : editor,
      outlinePreview,
      err,
      h("div", { class: "jc-q-actions" }, (ev.options || [{ value: "ok", label: "OK" }]).map((o, i) =>
        h("button", { class: ["btn", "sm", i === 0 && "primary", o.value === "cancel" && "ghost"], onclick: () => this.answer(q, o, editor, err) }, o.label))));
  }

  renderArtifacts() {
    if (this.compact) return;
    mount(this.ui.artifacts, this.artifacts.map((a) => artifactCard(a, this.ctx)));
  }

  renderCompact() {
    const u = this.ui;
    const out = [...this.steps.values()].map((s) => s.out).join("");
    u.text.innerHTML = renderMarkdown(out);
    u.status.hidden = !!out || FINAL.has(this.job.status);
    u.stopBtn.hidden = FINAL.has(this.job.status);
    if (this.job.error) { u.error.hidden = false; mount(u.error, icon("alert", 14), this.job.error); }
  }

  destroy() { ACTIVE.delete(this); this.el.remove(); }
}

export function sourceItem(s) {
  const body = [h("span", { class: "src-icon" }, icon(KIND_ICON[s.kind] || "file", 14)),
    h("span", { class: "src-text" }, h("span", { class: "src-title" }, s.title), s.snippet ? h("span", { class: "src-snippet" }, s.snippet) : null),
    h("span", { class: "src-kind" }, s.kind)];
  if (s.url && /^https?:/i.test(s.url)) {
    return h("li", null, h("a", { class: "src", href: s.url, target: "_blank", rel: "noopener noreferrer", title: s.url }, ...body, icon("external", 12)));
  }
  return h("li", null, h("button", { class: "src", onclick: (e) => e.currentTarget.classList.toggle("open") }, ...body));
}

export function artifactCard(a, ctx = {}) {
  if (a.kind === "diagram") {
    const svgUrl = authUrl(a.url && a.url.endsWith(".svg") ? a.url : `/api/diagrams/${a.id}.svg`);
    const show = () => openModal({ title: a.name, width: 900, body: h("div", { class: "diagram-large" }, h("img", { src: svgUrl, alt: a.name })),
      footer: diagramLinks(a) });
    return h("div", { class: "artifact artifact-diagram" },
      h("button", { class: "artifact-thumb", onclick: show, "aria-label": a.name }, h("img", { src: svgUrl, alt: a.name, loading: "lazy" })),
      h("div", { class: "artifact-info" },
        h("div", { class: "artifact-kind" }, icon("diagram", 13), t("artifact.diagram")),
        h("div", { class: "artifact-name" }, a.name),
        h("div", { class: "artifact-actions" }, diagramLinks(a))));
  }
  const vid = a.version_id;
  return h("div", { class: "artifact artifact-doc" },
    h("div", { class: "artifact-icon" }, icon("file", 22)),
    h("div", { class: "artifact-info" },
      h("div", { class: "artifact-kind" }, t("artifact.document"), a.version ? ` · ${t("artifact.version", { v: a.version })}` : ""),
      h("div", { class: "artifact-name" }, a.name),
      h("div", { class: "artifact-actions" },
        vid ? h("button", { class: "btn sm primary", onclick: () => openInWord(vid) }, icon("external", 13), t("artifact.openWord")) : null,
        vid ? h("a", { class: "btn sm", href: authUrl(`/api/versions/${vid}/download`), download: "" }, icon("download", 13), t("artifact.download")) : null,
        vid && ctx.onPreview ? h("button", { class: "btn sm ghost", onclick: () => ctx.onPreview(a) }, icon("eye", 13), t("artifact.preview")) : null)));
}

function diagramLinks(a) {
  return [
    h("a", { class: "btn sm", href: authUrl(`/api/diagrams/${a.id}.drawio`), download: `${a.name}.drawio` }, icon("download", 13), ".drawio"),
    h("a", { class: "btn sm", href: authUrl(`/api/diagrams/${a.id}.png`), download: `${a.name}.png` }, icon("image", 13), ".png"),
  ];
}

export async function openInWord(vid, reveal = false) {
  try { await api.openVersion(vid, reveal); if (!reveal) toast(t("artifact.opened"), "success", 2000); }
  catch (e) { toast(e.message, "error"); }
}
