import { $, api, escapeHTML as e, safeURL, singlePane, errorText, toast } from "./ui.js";
import { confirmAction } from "./confirm-dialog.js";

const kindLabels = {daily: "日报", weekly: "周报", monthly: "月报"};
const collectionFor = kind => ["weekly", "monthly"].includes(kind) ? kind : "daily";
const shiftDay = (day, days) => {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return "";
  date.setUTCDate(date.getUTCDate()+days);
  return date.toISOString().slice(0,10);
};
const selectedRange = (kind, selection) => {
  if (!selection) return ["", ""];
  const day = kind === "monthly" ? selection+"-01" : selection;
  if (!shiftDay(day, 0) || shiftDay(day, 0) !== day) return ["", ""];
  if (kind === "weekly") return [day+"T00:00", shiftDay(day,7)+"T00:00"];
  if (kind === "monthly") {
    const date = new Date(day+"T00:00:00Z"); date.setUTCMonth(date.getUTCMonth()+1);
    return [day+"T00:00", date.toISOString().slice(0,10)+"T00:00"];
  }
  return kind === "morning" ? [shiftDay(day,-1)+"T21:30", day+"T08:30"]
    : kind === "noon" ? [day+"T08:30", day+"T14:30"] : [shiftDay(day,-1)+"T21:30", day+"T21:30"];
};
import { failureText, statusText, reportError, originText, progressText } from "./report-status.js?v=reports-manual-1";
const requestId = () => globalThis.crypto.randomUUID?.() || [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2,"0")).join("");
const dateText = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间无效" : new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false}).format(date);
};
const emphasis = p => {
  const at = p.emphasis ? p.text.indexOf(p.emphasis) : -1;
  return at < 0 ? e(p.text) : e(p.text.slice(0,at)) + "<strong>" + e(p.emphasis) + "</strong>" + e(p.text.slice(at+p.emphasis.length));
};
const sourceMarkup = (refs, sources) => `<details class="report-sources" name="report-sources"><summary class="reader-tool-btn">来源 · ${refs.length}篇<span class="report-source-chevron" aria-hidden="true">⌄</span></summary><div class="report-source-list"><ul>${refs.map(ref => {
  const source = sources.get(ref); if (!source) return "";
  const url = source.url ? safeURL(source.url) : "";
  return `<li>${url ? `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(source.title)}</a>` : `<span>${e(source.title)}</span>`}<span class="report-source-meta">${e(source.source_name)} · ${e(dateText(source.published_at))}</span></li>`;
}).join("")}</ul></div></details>`;

export class ReportPanel {
  constructor({isVisible, enterReader, exitReader, selects}) {
    Object.assign(this, {isVisible, enterReader, exitReader, selects});
    this.kind = "daily"; this.entries = []; this.selected = ""; this.token = "";
    this.listSequence = 0; this.detailSequence = 0; this.details = null;
    this.errorNotifications = new Map();
    this.creation = null;
    this.content = $("#reportContent");
    $("#reportFilters").addEventListener("click", event => {
      const button = event.target.closest("[data-report-kind]");
      if (button) this.setKind(button.dataset.reportKind);
    });
    $("#reportFilters").addEventListener("keydown", event => {
      const buttons = [...$("#reportFilters").querySelectorAll("button")], index = buttons.indexOf(event.target);
      if (index < 0 || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();
      buttons[event.key === "Home" ? 0 : event.key === "End" ? 2 : (index+(event.key === "ArrowRight" ? 1 : 2))%3].focus();
    });
    $("#reportCardList").addEventListener("click", event => {
      const card = event.target.closest("[data-report-id]");
      if (card) this.select(card.dataset.reportId, true);
      else if (event.target.closest("[data-report-more]")) this.load({append:true});
      else if (event.target.closest("[data-report-reload]")) this.load();
    });
    this.content.addEventListener("click", event => {
      if (event.target.closest("[data-report-reload]")) this.select(this.selected);
      if (event.target.closest("[data-creation-cancel]")) {
        this.creation = null; this.renderDetail();
        if (singlePane.matches) this.exitReader();
        $("#createReportBtn").focus({preventScroll:true});
      }
      if (event.target.closest("[data-creation-reload]")) {this.creation = null; this.openCreation();}
    });
    this.content.addEventListener("submit", event => {
      if (event.target.id === "reportCreationForm") {event.preventDefault(); this.submitCreation();}
    });
    this.content.addEventListener("input", event => this.changeCreation(event));
    this.content.addEventListener("change", event => this.changeCreation(event));
    this.content.addEventListener("toggle", event => {
      if (!event.target.matches(".report-sources[open]")) return;
      this.content.querySelectorAll(".report-sources[open]").forEach(other => {if (other !== event.target) other.open = false;});
    }, true);
    $("#retryReportBtn").addEventListener("click", () => this.retry());
    $("#createReportBtn").addEventListener("click", () => this.openCreation());
    $("#deleteReportBtn").addEventListener("click", () => this.deleteReport());
    document.addEventListener("visibilitychange", () => {if (!document.hidden && this.isVisible()) this.load({quiet:true}); else clearTimeout(this.timer);});
  }
  deactivate() { clearTimeout(this.timer); this.listSequence++; this.detailSequence++; }
  async openRecord(id, kind) {
    this.deactivate(); this.creation = null;
    this.kind = collectionFor(kind);
    this.selected = id; this.details = null; this.entries = [];
    this.renderFilters(); this.renderDetail(true);
    $("#reportListScroll").scrollTop = 0;
    $("#reportScrollArea").scrollTop = 0;
    const loading = this.load({quiet:true}), sequence = this.listSequence;
    await loading;
    if (sequence !== this.listSequence || !this.isVisible()) return;
    if (this.details?.id === id) this.enterReader();
    else await this.select(id, true);
  }
  async setKind(kind) {
    if (!kindLabels[kind] || kind === this.kind) return;
    this.deactivate(); this.creation = null; this.kind = kind; this.entries = []; this.selected = ""; this.details = null;
    this.renderDetail(); this.renderFilters();
    $("#reportListScroll").scrollTop = 0;
    $("#reportScrollArea").scrollTop = 0;
    await this.load();
  }
  renderFilters() {
    for (const button of $("#reportFilters").querySelectorAll("button")) {
      const active = button.dataset.reportKind === this.kind;
      button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active));
    }
  }
  schedule() {
    clearTimeout(this.timer);
    if (!this.isVisible() || document.hidden) return;
    this.timer = setTimeout(() => this.load({quiet:true}), this.entries.some(row => ["pending","running"].includes(row.status)) ? 3000 : 30000);
  }
  async load({append=false,quiet=false}={}) {
    if (append && this.loading) return;
    const sequence = ++this.listSequence, kind = this.kind;
    this.loading = true;
    if (!quiet && !this.entries.length) $("#reportCardList").innerHTML = '<p class="list-message" role="status">正在读取报告…</p>';
    const params = new URLSearchParams({kind,offset:append ? this.entries.length : 0,limit:append ? 30 : Math.min(200,Math.max(30,this.entries.length))});
    try {
      const data = await api(`/api/reports?${params}`);
      if (sequence !== this.listSequence || kind !== this.kind) return;
      if (this.token && this.token !== data.database_token) {
        this.errorNotifications.clear();
        this.selected = ""; this.details = null; this.entries = []; this.detailSequence++;
        this.renderDetail();
      }
      this.token = data.database_token; this.meta = data;
      const old = append ? this.entries : data.items;
      const incoming = append ? data.items : this.entries.length > 200 ? this.entries : [];
      this.entries = [...old,...incoming.filter(row => !old.some(previous => previous.id === row.id))];
      const changedError = this.entries.find(row => row.error && row.status !== "skipped" &&
        this.errorNotifications.get(row.id) !== `${row.error}:${row.status}`);
      for (const row of this.entries) this.errorNotifications.set(row.id, row.error ? `${row.error}:${row.status}` : "");
      if (changedError && this.isVisible()) toast(`${changedError.title}：${failureText(changedError)}`);
      this.hasMore = this.entries.length < data.total;
      this.renderList();
      if (this.creation) return;
      if (!this.selected && this.entries.length && this.isVisible() && !singlePane.matches) await this.select(this.entries[0].id);
      else if (this.selected) {
        const row = this.entries.find(row => row.id === this.selected);
        if (row && (this.details?.updated_at !== row.updated_at || this.details?.status !== row.status || JSON.stringify(this.details?.progress) !== JSON.stringify(row.progress))) await this.select(this.selected, false, true);
        this.renderElapsed();
      } else this.renderDetail();
    } catch (error) {
      if (sequence !== this.listSequence) return;
      if (!this.entries.length) $("#reportCardList").innerHTML = '<div class="list-message" role="status">报告读取失败<button class="btn-secondary" data-report-reload type="button">重试</button></div>';
      else if (!quiet) toast(errorText(error));
    } finally {
      if (sequence === this.listSequence) {this.loading = false; this.schedule();}
    }
  }
  renderList() {
    const host = $("#reportCardList");
    const focused = host.contains(document.activeElement) ? document.activeElement.dataset.reportId : null;
    const warning = this.meta?.calendar_warning && this.kind === "daily" ? '<p class="list-message" role="status">交易日历尚未更新，早报与午报暂缓生成。</p>' : "";
    const cards = this.entries.map(row => `<button class="news-card ${row.id === this.selected ? "is-active active" : ""}" type="button" data-report-id="${e(row.id)}" aria-current="${row.id === this.selected}">
      <span class="news-card-meta"><span class="meta-source-box">${e(row.kind_label)}${originText(row)}</span><span>${e(statusText(row))}</span></span>
      <span class="news-card-title">${e(row.title)}</span>${row.excerpt ? `<span class="news-card-snippet">${e(row.excerpt)}</span>` : ""}
      ${row.status === "running" ? `<span class="news-card-snippet">${e(progressText(row))}</span>` : ""}
      ${row.error ? `<span class="news-card-snippet ${row.status === "skipped" ? "" : "error-state"}">${e(failureText(row))}</span>` : ""}
      <span class="news-card-footer"><span class="card-time">${e(dateText(row.scheduled_at))}</span></span></button>`).join("") + (this.hasMore ? '<button class="btn-secondary" type="button" data-report-more>加载更多</button>' : "");
    const html = warning + cards;
    if (cards && (html !== this.listHTML || !host.querySelector("[data-report-id]"))) {
      host.innerHTML = html; this.listHTML = html;
      if (focused) host.querySelector(`[data-report-id="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
    } else if (!cards) {
      this.listHTML = "";
      host.innerHTML = warning + `<div class="list-message"><p>尚无${kindLabels[this.kind]}</p><p>${this.meta?.next_edition_at ? `下一期 ${e(dateText(this.meta.next_edition_at))}` : "等待下一期生成"}</p></div>`;
    }
  }
  async select(id, mobile=false, quiet=false) {
    this.creation = null;
    const sequence = ++this.detailSequence, token = this.token;
    const changed = this.selected !== id;
    this.selected = id;
    this.renderList();
    if (!quiet) {this.details = null; this.renderDetail(true);}
    if (changed) $("#reportScrollArea").scrollTop = 0;
    if (mobile) this.enterReader();
    try {
      const data = await api(`/api/reports/${encodeURIComponent(id)}`);
      if (sequence !== this.detailSequence || this.selected !== id || token !== this.token) return;
      if (data.database_token !== token) {this.selected = ""; this.details = null; return this.load();}
      this.details = data.report; this.renderDetail();
    } catch (error) {
      if (sequence !== this.detailSequence) return;
      this.content.innerHTML = '<div class="reader-empty"><h2>报告读取失败</h2><button class="btn-secondary" type="button" data-report-reload>重试</button></div>';
    }
  }
  renderDetail(loading=false) {
    if (this.creation) {this.renderCreation(); return;}
    const report = this.details;
    $("#reportToolbarTitle").textContent = report ? report.kind_label + originText(report) : kindLabels[this.kind];
    $("#retryReportBtn").hidden = report?.status !== "failed";
    $("#deleteReportBtn").hidden = !report;
    $("#deleteReportBtn").disabled = report?.status === "running";
    $("#deleteReportBtn").title = report?.status === "running" ? "正在生成，完成后可删除" : "删除报告";
    if (!report) {
      this.content.innerHTML = `<div class="reader-empty"><h2>${loading ? "正在读取报告…" : this.entries.length ? "选择一份报告" : `尚无${kindLabels[this.kind]}`}</h2>${!loading && !this.entries.length ? "<p>从本次更新后开始生成。</p>" : ""}</div>`;
      return;
    }
    if (report.status !== "done") {
      this.content.innerHTML = `<div class="settings-sheet"><div class="settings-detail-heading"><h2>${e(report.title)}</h2></div><p class="muted">${e(dateText(report.period_start))} — ${e(dateText(report.period_end))} · 北京时间</p><p role="status">${e(statusText(report))}</p>${report.error ? `<p class="error-state" role="alert">${e(failureText(report))}</p>` : ""}${report.next_retry_at ? `<p>已尝试 ${report.attempts} / 3 次，将于 ${e(dateText(report.next_retry_at))} 自动重试。</p>` : ""}${this.progressMarkup(report)}</div>`;
      this.renderElapsed();
      return;
    }
    const content = report.content;
    const sources = new Map(content.sources.map(source => [source.ref, source]));
    const briefs = content.briefs || [];
    const hasContent = content.sections.length || briefs.length;
    const metadata = [content.sections.length ? `${content.sections.length}个主题` : "", briefs.length ? `${briefs.length}条简讯` : "", hasContent ? `约${content.reading_minutes}分钟` : ""].filter(Boolean).join(" · ");
    this.content.innerHTML = `<header class="article-header"><div class="article-meta-top"><span>${e(report.kind_label)}${originText(report)}</span><span>${e(metadata)}</span></div><h1 class="article-title">${e(report.title)}</h1><div class="article-meta-bottom"><span>${e(dateText(report.period_start))} — ${e(dateText(report.period_end))}</span><span>北京时间</span></div></header>` +
      (content.overview.length ? `<div class="report-overview article-body"><ul>${content.overview.map(point => `<li>${e(point.text)}</li>`).join("")}</ul></div>` : "") +
      content.sections.map((section,index) => {
        const refs = [...new Set(section.paragraphs.flatMap(p => p.refs))];
        return `<section class="report-section" aria-labelledby="report-section-${index}"><div class="report-heading"><span class="report-number" aria-hidden="true">${String(index+1).padStart(2,"0")}</span><h2 class="reader-section-heading ${index < 2 ? "reader-section-heading--lead" : ""}" id="report-section-${index}">${e(section.title)}</h2></div><div class="article-body">${section.paragraphs.map(p => `<p>${emphasis(p)}</p>`).join("")}</div>${sourceMarkup(refs,sources)}</section>`;
      }).join("") + (briefs.length ? `<section class="report-section" aria-labelledby="report-briefs"><div class="report-heading"><h2 class="reader-section-heading" id="report-briefs">简讯</h2></div><div class="article-body"><ul>${briefs.map(brief=>`<li>${e(brief.text)}</li>`).join("")}</ul></div>${sourceMarkup([...new Set(briefs.flatMap(brief=>brief.refs))],sources)}</section>` : "") +
      (!hasContent ? '<div class="reader-empty"><p>本期暂无符合条件的新闻。</p></div>' : "") + '<footer class="report-end">本期完</footer>';
  }
  progressMarkup(report) {
    const progress = report.progress || {}, stage = progress.stage;
    if (report.status !== "running") return stage ? `<p class="muted">上次进行到：${e(progressText(report))}</p>` : "";
    const steps = ["刷新索引", "读取新闻", "编写报告", "校验内容", "保存报告"];
    const current = {indexing:0,capturing:1,preparing:2,selecting:2,merging:2,checking_material:2,repairing_material:2,writing:2,repairing:2,validating:3,saving:4,done:5}[stage] ?? 0;
    return `<section class="settings-section-card settings-section-card--compact" aria-label="生成进度"><div class="section-card-title"><span>生成进度</span><span class="muted" id="reportElapsed"></span></div><p role="status" aria-live="polite">${e(progressText(report))}</p>${Number.isInteger(progress.source_count) ? `<p class="muted">已读取 ${e(progress.source_count)} 条新闻</p>` : ""}<ol class="section-stack">${steps.map((label,index)=>`<li class="field-item ${index === current ? "" : "muted"}" ${index===current?'aria-current="step"':''}>${index<current?'✓':index+1} · ${e(label)}${index===current?' · 进行中':''}</li>`).join("")}</ol></section>`;
  }
  renderElapsed() {
    const host = $("#reportElapsed"), start = this.details?.started_at;
    if (!host || !start) return;
    const seconds = Math.max(0, Math.floor((Date.now()-new Date(start).getTime())/1000));
    host.textContent = `已用时 ${Math.floor(seconds/60)}分${seconds%60}秒`;
  }
  async retry() {
    const id = this.selected;
    $("#retryReportBtn").disabled = true;
    try {
      await api(`/api/reports/${encodeURIComponent(id)}/retry`, {method:"POST",body:{database_token:this.token}});
      if (id === this.selected) await this.select(id);
      await this.load({quiet:true});
    } catch (error) {toast(reportError(error));}
    finally {$("#retryReportBtn").disabled = false;}
  }
  async openCreation() {
    if (this.creation) {this.enterReader(); return;}
    const draft = {periods:null,kind:"",start:"",end:"",requestId:requestId(),submitting:false,error:""};
    this.creation = draft; this.detailSequence++;
    this.renderCreation(); this.enterReader();
    $("#reportScrollArea").scrollTop = 0;
    try {
      const data = await api("/api/reports/manual-options");
      if (this.creation !== draft) return;
      draft.periods = data.periods; draft.token = data.database_token; draft.max = data.now.slice(0,16);
      draft.clockOffset = new Date(data.now).getTime()-Date.now();
      const candidates = data.periods.filter(p => this.kind === "daily" ? ["morning","noon","evening"].includes(p.kind) : p.kind === this.kind);
      draft.kind = candidates.sort((a,b) => b.period_end.localeCompare(a.period_end))[0]?.kind || data.periods[0]?.kind || "";
      this.setCreationPeriod(); this.renderCreation();
      if (this.isVisible()) $("#reportCreationKind")?.focus({preventScroll:true});
    } catch (error) {
      if (this.creation !== draft) return;
      draft.error = reportError(error); this.renderCreation();
    }
  }
  setCreationPeriod() {
    const draft = this.creation, period = draft.periods.find(p => p.kind === draft.kind);
    draft.selection = period?.selection || "";
    draft.start = period?.period_start.slice(0,16) || "";
    draft.end = period?.period_end.slice(0,16) || "";
  }
  changeCreation(event) {
    const draft = this.creation, input = event.target;
    if (!draft || draft.submitting || !["reportCreationKind","reportSelection","reportStart","reportEnd"].includes(input.name)) return;
    const key = {reportCreationKind:"kind",reportSelection:"selection",reportStart:"start",reportEnd:"end"}[input.name];
    if (draft[key] === input.value) return;
    draft[key] = input.value; draft.requestId = requestId(); draft.error = "";
    if (key === "kind") {
      this.setCreationPeriod();
      $("#reportPeriodFields").innerHTML = this.creationFields();
    }
    this.renderCreationPeriod();
  }
  creationFields() {
    const draft = this.creation;
    if (draft.kind === "custom") return `<div class="form-group-grid"><label class="field-item"><span class="field-label">开始时间 · 北京时间</span><input class="field-input" id="reportStart" name="reportStart" type="datetime-local" step="60" value="${e(draft.start)}" max="${e(draft.max)}" required></label><label class="field-item"><span class="field-label">结束时间 · 北京时间</span><input class="field-input" id="reportEnd" name="reportEnd" type="datetime-local" step="60" value="${e(draft.end)}" max="${e(draft.max)}" required></label></div>`;
    const monthly = draft.kind === "monthly", label = monthly ? "月份" : draft.kind === "weekly" ? "起始日期" : "日期";
    return `<label class="field-item"><span class="field-label">${label}</span><input class="field-input" id="reportSelection" name="reportSelection" type="${monthly?'month':'date'}" value="${e(draft.selection)}" required></label>`;
  }
  renderCreation() {
    const draft = this.creation;
    $("#reportToolbarTitle").textContent = "新增日报";
    $("#retryReportBtn").hidden = true; $("#deleteReportBtn").hidden = true;
    const heading = '<div class="settings-detail-heading"><h2>新增日报</h2></div>';
    if (!draft.periods) {
      this.content.innerHTML = heading + (draft.error ? `<p class="error-state" role="alert">${e(draft.error)}</p><button class="btn-secondary" type="button" data-creation-reload>重试</button>` : '<p class="muted" role="status">正在读取可选时间范围…</p>') + '<div class="button-row"><button class="btn-secondary" type="button" data-creation-cancel>返回报告</button></div>';
      return;
    }
    this.content.innerHTML = heading + `<form class="settings-sheet" id="reportCreationForm"><label class="field-item"><span class="field-label">报告类型</span><select class="field-select" id="reportCreationKind" name="reportCreationKind" aria-label="报告类型">${draft.periods.map(p=>`<option value="${e(p.kind)}" ${p.kind===draft.kind?'selected':''}>${e(p.kind_label)}</option>`).join("")}</select></label>
      <div id="reportPeriodFields">${this.creationFields()}</div>
      <p class="muted" id="reportCreationPeriod"></p><div class="button-row"><button class="btn-secondary" type="button" data-creation-cancel>返回报告</button><button class="btn-primary" type="submit" ${!draft.kind?'disabled':''}>开始生成</button></div><p class="error-state" id="reportCreationError" role="alert"></p></form>`;
    this.renderCreationPeriod(); this.selects.refresh(this.content);
  }
  renderCreationPeriod() {
    const draft = this.creation;
    draft.max = new Date(Date.now()+draft.clockOffset+8*60*60*1000).toISOString().slice(0,16);
    if (draft.kind === "custom") {
      $("#reportStart").max = draft.max; $("#reportEnd").max = draft.max;
      $("#reportEnd").setCustomValidity(draft.end && draft.start && draft.end <= draft.start ? "结束时间须晚于开始时间" : "");
    } else {
      [draft.start, draft.end] = selectedRange(draft.kind, draft.selection);
      const today = draft.max.slice(0,10), input = $("#reportSelection");
      input.max = draft.kind === "monthly" ? shiftDay(today.slice(0,7)+"-01",-1).slice(0,7)
        : draft.kind === "weekly" ? shiftDay(today,-7)
        : draft.max.slice(11) >= {morning:"08:30",noon:"14:30",evening:"21:30"}[draft.kind] ? today : shiftDay(today,-1);
      input.setCustomValidity(draft.end > draft.max ? "所选报告的时间范围尚未结束" : draft.selection && !draft.start ? "请选择有效日期" : "");
    }
    const range = draft.kind === "weekly" ? `${draft.selection} — ${shiftDay(draft.selection,6)} · 共 7 天`
      : draft.kind === "monthly" ? `${draft.selection} · 整月`
      : `${dateText(draft.start+"+08:00")} — ${dateText(draft.end+"+08:00")}`;
    $("#reportCreationPeriod").textContent = draft.start && draft.end ? range+" · 北京时间" : "请选择时间范围";
    $("#reportCreationError").textContent = draft.error;
  }
  async submitCreation() {
    const draft = this.creation;
    if (!draft || draft.submitting) return;
    const form = $("#reportCreationForm");
    if (!form.reportValidity()) return;
    draft.submitting = true;
    this.content.querySelectorAll("button,select,input").forEach(node=>node.disabled=true);
    this.content.querySelector('[type="submit"]').textContent = "正在提交…";
    try {
      const range = draft.kind === "custom" ? {period_start:draft.start+"+08:00",period_end:draft.end+"+08:00"} : {selection:draft.selection};
      const data = await api("/api/reports/manual", {method:"POST",body:{kind:draft.kind,...range,request_id:draft.requestId,database_token:draft.token}});
      if (this.creation !== draft) return;
      this.creation = null; this.kind = collectionFor(draft.kind);
      this.entries = []; this.details = null; this.selected = data.report.id;
      this.renderFilters(); await this.load({quiet:true});
      if (this.selected === data.report.id && this.isVisible()) {await this.select(data.report.id,true); toast("报告已加入生成队列。");}
    } catch (error) {
      if (this.creation !== draft) return;
      draft.submitting = false; draft.error = reportError(error); this.renderCreation();
    } finally {draft.submitting = false;}
  }
  async deleteReport() {
    const report = this.details, token = this.token;
    if (!report || report.status === "running" || !await confirmAction({title:"删除报告",message:`删除「${report.title}」？新闻资料会保留。`,action:"删除",trigger:"#deleteReportBtn"})) return;
    if (this.token !== token || this.details?.id !== report.id) return toast("报告已切换，请重新操作。");
    $("#deleteReportBtn").disabled = true;
    try {
      await api(`/api/reports/${encodeURIComponent(report.id)}`,{method:"DELETE",body:{database_token:token}});
      if (this.selected === report.id) {this.selected=""; this.details=null; this.renderDetail();}
      await this.load(); if (singlePane.matches && !this.selected) this.exitReader();
      toast("报告已删除。");
    } catch (error) {toast(reportError(error));}
    finally {$("#deleteReportBtn").disabled = this.details?.status === "running";}
  }
  handleKey(event, action) {
    if (!["previous","next"].includes(action) || event.target.closest("button,a,summary")) return;
    event.preventDefault();
    const index = this.entries.findIndex(row => row.id === this.selected);
    const entry = this.entries[Math.max(0, Math.min(this.entries.length-1,index+(action === "next" ? 1 : -1)))];
    if (entry) this.select(entry.id);
  }
}
