import { $, api, escapeHTML as e, safeURL, singlePane, errorText, toast } from "./ui.js";
import { confirmAction } from "./confirm-dialog.js";
import { AnchoredPopover } from "./anchored-popover.js";
import { selectField } from "./form-controls.js";
import { defaultReportWindows } from "./report-windows.js";
import { Calendar, DateTimePicker, localDate, localDateTime } from "./date-picker.js?v=feature-pickers-1";

const kindLabels = {daily: "日报", weekly: "周报", monthly: "月报"};
const collectionFor = kind => ["weekly", "monthly"].includes(kind) ? kind : "daily";
const periodFor = row => ["weekly","monthly","custom"].includes(row.kind) ? row.period_start : row.period_end;
const byPeriod = (a,b) => periodFor(b).localeCompare(periodFor(a)) || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id);
const shiftDay = (day, days) => {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return "";
  date.setUTCDate(date.getUTCDate()+days);
  return date.toISOString().slice(0,10);
};
const selectedRange = (kind, selection, windows = defaultReportWindows) => {
  if (!selection) return ["", ""];
  const day = kind === "monthly" ? selection+"-01" : selection;
  if (!localDate(day)) return ["", ""];
  const window = windows[kind];
  if (kind === "weekly") return [day+"T"+window.start, shiftDay(day,7)+"T"+window.end];
  if (kind === "monthly") {
    const date = new Date(day+"T00:00:00Z"); date.setUTCMonth(date.getUTCMonth()+1);
    return [day+"T"+window.start, date.toISOString().slice(0,10)+"T"+window.end];
  }
  return [shiftDay(day,window.start_day)+"T"+window.start, day+"T"+window.end];
};
import { failureText, statusText, reportError, originText, progressText } from "./report-status.js?v=report-windows-edit-1";
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
    this.creation = null; this.expandedGroups = new Map(); this.groupSizes = new Map();
    this.content = $("#reportContent");
    this.creationHost = $("#reportCreationPopover");
    this.popover = new AnchoredPopover({panel:this.creationHost, trigger:$("#createReportBtn"),
      owns:target => this.creationHost.contains(this.selects.button) && this.selects.menu.contains(target),
      onClose:() => {if (this.creationHost.contains(this.selects.button)) this.selects.close(false);}});
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
      else if (event.target.closest("[data-report-group]")) {
        const key = event.target.closest("[data-report-group]").dataset.reportGroup;
        this.expandedGroups.set(this.kind+":"+key, !this.groupOpen(key));
        this.renderList();
        if (this.groupOpen(key)) this.load({quiet:true});
      }
      else if (event.target.closest("[data-report-more]")) this.load({append:true,group:event.target.closest("[data-report-more]").dataset.reportMore});
      else if (event.target.closest("[data-report-reload]")) this.load();
    });
    this.content.addEventListener("click", event => {
      if (event.target.closest("[data-report-reload]")) this.select(this.selected);
    });
    this.creationHost.addEventListener("click", event => {
      if (event.target.closest("[data-creation-cancel]")) {this.popover.close(true); if (!this.creation?.submitting) this.creation = null;}
      if (event.target.closest("[data-creation-reload]")) {this.creation = null; this.openCreation();}
      if (event.target.closest("[data-creation-model-reload]")) void this.refreshCreationModel();
      if (event.target.closest("[data-report-catalog-reload]")) void this.loadCreationCatalog(true);
      if (event.target.closest("[data-report-model-reset]") && !this.creation?.submitting) {
        this.creation.modelOverride = null; this.creation.modelChoices = structuredClone(this.creation.modelSettings);
        this.creation.requestId = requestId(); this.renderModelChoices(); this.renderCreationModel(); void this.loadCreationCatalog();
      }
    });
    this.creationHost.addEventListener("toggle", event => {
      if (event.target.id !== "reportTemporaryModel" || !this.creation) return;
      this.creation.modelExpanded = event.target.open;
      if (event.target.open) void this.loadCreationCatalog();
    }, true);
    this.creationHost.addEventListener("submit", event => {
      if (event.target.id === "reportCreationForm") {event.preventDefault(); this.submitCreation();}
    });
    this.creationHost.addEventListener("input", event => this.changeCreation(event));
    this.creationHost.addEventListener("change", event => this.changeCreation(event));
    this.content.addEventListener("toggle", event => {
      if (!event.target.matches(".report-sources[open]")) return;
      this.content.querySelectorAll(".report-sources[open]").forEach(other => {if (other !== event.target) other.open = false;});
    }, true);
    $("#retryReportBtn").addEventListener("click", () => this.retry());
    $("#createReportBtn").addEventListener("click", () => this.popover.open ? this.popover.close(true) : this.openCreation());
    $("#deleteReportBtn").addEventListener("click", () => this.deleteReport());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && this.isVisible()) {this.load({quiet:true}); if (this.popover.open) void this.refreshCreationModel();}
      else clearTimeout(this.timer);
    });
  }
  deactivate() { this.popover.close(); clearTimeout(this.timer); this.listSequence++; this.detailSequence++; }
  async openRecord(id, kind) {
    this.deactivate();
    this.kind = collectionFor(kind);
    this.selected = id; this.details = null; this.entries = []; this.meta = null;
    this.renderFilters(); this.renderDetail(true);
    $("#reportListScroll").scrollTop = 0;
    $("#reportScrollArea").scrollTop = 0;
    const loading = this.load({quiet:true}), sequence = this.listSequence;
    await loading;
    if (sequence !== this.listSequence || !this.isVisible()) return;
    if (this.details?.id === id) {
      this.expandedGroups.set(this.kind+":"+this.groupKey(this.details),true);
      this.renderList(); this.enterReader();
    }
    else await this.select(id, true);
  }
  async setKind(kind) {
    if (!kindLabels[kind] || kind === this.kind) return;
    this.deactivate(); this.kind = kind; this.entries = []; this.meta = null; this.selected = ""; this.details = null;
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
    this.timer = setTimeout(() => this.load({quiet:true}), this.meta?.groups.some(group => group.active_count) ? 3000 : 30000);
  }
  groupKey(row) {
    return periodFor(row).slice(0,this.kind === "monthly" ? 4 : 7);
  }
  currentGroup() { return (this.meta?.now || new Date(Date.now()+8*3600000).toISOString()).slice(0,this.kind === "monthly" ? 4 : 7); }
  groupOpen(key) { return this.expandedGroups.get(this.kind+":"+key) ?? key === this.currentGroup(); }
  archiveRows() {
    // A report opened from task history can be beyond the first loaded page.
    // Keep that explicitly requested record visible in its archive group.
    const selected = this.details;
    return selected && selected.id === this.selected && !this.entries.some(row=>row.id===selected.id)
      ? [...this.entries,selected].sort(byPeriod) : this.entries;
  }
  async load({append=false,quiet=false,group}={}) {
    if (append && this.loading) return;
    const sequence = ++this.listSequence, kind = this.kind;
    this.loading = true;
    if (append) this.groupSizes.set(kind+":"+group,(this.groupSizes.get(kind+":"+group)||30)+30);
    if (!quiet && !this.meta) $("#reportCardList").innerHTML = '<p class="list-message" role="status">正在读取报告…</p>';
    try {
      // Every response carries complete group counts, even for unloaded years.
      const keys = new Set(["current", ...(this.meta?.groups || []).filter(g => this.groupOpen(g.key)).map(g=>g.key)]);
      if (this.details) keys.add(this.groupKey(this.details));
      if (group) keys.add(group);
      keys.delete(this.currentGroup()); // "current" already loads it.
      const responses = await Promise.all([...keys].map(async key => {
        const size = this.groupSizes.get(kind+":"+(key === "current" ? this.currentGroup() : key)) || 30;
        const pages = await Promise.all(Array.from({length:Math.ceil(size/200)}, (_,index) => {
          const params = new URLSearchParams({kind,group:key,offset:index*200,limit:Math.min(200,size-index*200)});
          return api(`/api/reports?${params}`);
        }));
        return {...pages[0],items:pages.flatMap(page=>page.items)};
      }));
      if (sequence !== this.listSequence || kind !== this.kind) return;
      const data = responses[0];
      if (responses.some(response=>response.database_token !== data.database_token)) return this.load({quiet:true});
      if (this.token && this.token !== data.database_token) {
        this.errorNotifications.clear(); this.expandedGroups.clear(); this.groupSizes.clear();
        this.selected = ""; this.details = null; this.entries = []; this.detailSequence++;
        this.renderDetail();
      }
      this.token = data.database_token; this.meta = data;
      const updated = new Set(responses.map(response=>response.group));
      const existing = new Set(data.groups.map(g=>g.key));
      const rows = [...this.entries.filter(row=>!updated.has(this.groupKey(row)) && existing.has(this.groupKey(row))), ...responses.flatMap(response=>response.items)];
      this.entries = [...new Map(rows.map(row=>[row.id,row])).values()].sort(byPeriod);
      const changedError = this.entries.find(row => row.error && row.status !== "skipped" && this.errorNotifications.get(row.id) !== `${row.error}:${row.status}`);
      for (const row of this.entries) this.errorNotifications.set(row.id,row.error ? `${row.error}:${row.status}` : "");
      if (changedError && this.isVisible()) toast(`${changedError.title}：${failureText(changedError)}`);
      this.listError = ""; this.renderList();
      const first = this.entries.find(row=>this.groupOpen(this.groupKey(row)));
      if (!this.selected && first && this.isVisible() && !singlePane.matches) await this.select(first.id);
      else if (this.selected) {
        const row = this.entries.find(row => row.id === this.selected);
        if (row && (this.details?.updated_at !== row.updated_at || this.details?.status !== row.status || JSON.stringify(this.details?.progress) !== JSON.stringify(row.progress))) await this.select(this.selected,false,true);
        this.renderElapsed();
      } else this.renderDetail();
    } catch (error) {
      if (sequence !== this.listSequence) return;
      this.listError = "报告读取失败";
      this.renderList();
    } finally {
      if (sequence === this.listSequence) {this.loading = false; this.schedule();}
    }
  }
  renderList() {
    const host = $("#reportCardList"), active = host.contains(document.activeElement) ? document.activeElement : null;
    const focusAttr = active?.hasAttribute("data-report-group") ? "data-report-group" : active?.hasAttribute("data-report-more") ? "data-report-more" : "data-report-id";
    const focusKey = active?.getAttribute(focusAttr);
    const warning = this.meta?.calendar_warning && this.kind === "daily" ? '<p class="list-message" role="status">交易日历尚未更新，早报与午报暂缓生成。</p>' : "";
    const error = this.listError ? '<div class="list-message" role="status">报告读取失败<button class="btn-secondary" data-report-reload type="button">重试</button></div>' : "";
    const groups = (this.meta?.groups || []).map(group => {
      const open = this.groupOpen(group.key), rows = this.archiveRows().filter(row=>this.groupKey(row)===group.key);
      const label = this.kind === "monthly" ? `${group.key}年` : `${group.key.slice(0,4)}年${Number(group.key.slice(5))}月`;
      const id = `report-group-${this.kind}-${group.key}`;
      return `<section class="feed-date-group"><button class="feed-date-heading feed-group-toggle" type="button" data-report-group="${e(group.key)}" aria-expanded="${open}" aria-controls="${id}"><span class="feed-date-title"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>${label}</span><span class="feed-date-count">${group.count}份</span></button><div class="feed-date-cards" id="${id}" ${open?'':'hidden'}>${rows.map(row=>this.cardMarkup(row)).join("")}${open && rows.length<group.count ? rows.length ? `<button class="btn-secondary" type="button" data-report-more="${e(group.key)}">加载更多</button>` : `<p class="list-message" role="status">${this.listError?'暂时无法加载':'正在读取报告…'}</p>` : ''}</div></section>`;
    }).join("");
    const html = warning + error + (groups || (!this.listError ? `<div class="list-message"><p>尚无${kindLabels[this.kind]}</p><p>${this.meta?.schedule_control?.paused ? "自动生成已暂停" : this.meta?.next_edition_at ? `下一期 ${e(dateText(this.meta.next_edition_at))}` : "等待下一期生成"}</p></div>` : ""));
    if (html !== this.listHTML || !host.querySelector("[data-report-group]")) {
      host.innerHTML = html; this.listHTML = html;
      if (focusKey) host.querySelector(`[${focusAttr}="${CSS.escape(focusKey)}"]`)?.focus({preventScroll:true});
    }
  }
  cardMarkup(row) {
    const period = row.kind === "monthly" ? row.period_start.slice(0,7) : row.kind === "weekly" || row.kind === "custom"
      ? `${dateText(row.period_start).slice(0,5)} — ${dateText(new Date(new Date(row.period_end).getTime()-1)).slice(0,5)}` : dateText(row.period_end).slice(0,5);
    return `<button class="news-card ${row.id === this.selected ? "is-active active" : ""}" type="button" data-report-id="${e(row.id)}" aria-current="${row.id === this.selected}">
      <span class="news-card-meta"><span class="meta-source-box">${e(row.kind_label)}${originText(row)}</span><span class="card-time">${e(period)}</span></span>
      <span class="news-card-title">${e(row.title)}</span>${row.excerpt ? `<span class="news-card-snippet">${e(row.excerpt)}</span>` : ""}
      ${row.model_label ? `<span class="news-card-meta">${e(row.model_label)}</span>` : ""}
      ${row.status !== "done" ? `<span class="news-card-snippet ${row.error && row.status !== 'skipped'?'error-state':''}">${e(row.error ? `${statusText(row)} · ${failureText(row)}` : row.status === 'running' ? progressText(row) : statusText(row))}</span>` : ""}</button>`;
  }
  async select(id, mobile=false, quiet=false) {
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
      this.details = data.report;
      if (mobile) {
        const key = this.groupKey(data.report); this.expandedGroups.set(this.kind+":"+key,true);
        if (!this.entries.some(row=>row.id===id)) await this.load({quiet:true,group:key});
        this.renderList();
      }
      this.renderDetail();
    } catch (error) {
      if (sequence !== this.detailSequence) return;
      this.content.innerHTML = '<div class="reader-empty"><h2>报告读取失败</h2><button class="btn-secondary" type="button" data-report-reload>重试</button></div>';
    }
  }
  renderDetail(loading=false) {
    const report = this.details;
    $("#reportToolbarTitle").textContent = report ? report.kind_label + originText(report) : kindLabels[this.kind];
    $("#retryReportBtn").hidden = report?.status !== "failed";
    $("#deleteReportBtn").hidden = !report;
    $("#deleteReportBtn").disabled = report?.status === "running";
    $("#deleteReportBtn").title = report?.status === "running" ? "正在生成，完成后可删除" : "删除报告";
    if (!report) {
      this.content.innerHTML = `<div class="reader-empty"><h2>${loading ? "正在读取报告…" : this.meta?.groups.length ? "选择一份报告" : `尚无${kindLabels[this.kind]}`}</h2>${!loading && !this.meta?.groups.length ? "<p>从本次更新后开始生成。</p>" : ""}</div>`;
      return;
    }
    if (report.status !== "done") {
      this.content.innerHTML = `<div class="settings-sheet"><div class="settings-detail-heading"><h2>${e(report.title)}</h2></div><p class="muted">${e(dateText(report.period_start))} — ${e(dateText(report.period_end))} · 北京时间</p>${this.modelMarkup(report)}<p role="status">${e(statusText(report))}</p>${report.error ? `<p class="error-state" role="alert">${e(failureText(report))}</p>` : ""}${report.next_retry_at ? `<p>已尝试 ${report.attempts} / 3 次，将于 ${e(dateText(report.next_retry_at))} 自动重试。</p>` : ""}${this.progressMarkup(report)}</div>`;
      this.renderElapsed();
      return;
    }
    const content = report.content;
    const sources = new Map(content.sources.map(source => [source.ref, source]));
    const briefs = content.briefs || [];
    const hasContent = content.sections.length || briefs.length;
    const metadata = [content.sections.length ? `${content.sections.length}个主题` : "", briefs.length ? `${briefs.length}条简讯` : "", hasContent ? `约${content.reading_minutes}分钟` : ""].filter(Boolean).join(" · ");
    this.content.innerHTML = `<header class="article-header report-masthead"><div class="article-meta-top"><span class="report-edition">${e(report.kind_label)}${originText(report)}</span><span>${e(metadata)}</span></div><h1 class="article-title">${e(report.title)}</h1><div class="article-meta-bottom"><span>${e(dateText(report.period_start))} — ${e(dateText(report.period_end))}</span><span>北京时间</span></div>${this.modelMarkup(report)}</header>` +
      (content.overview.length ? `<div class="report-overview article-body"><ul>${content.overview.map(point => `<li>${e(point.text)}</li>`).join("")}</ul></div>` : "") +
      content.sections.map((section,index) => {
        const refs = [...new Set(section.paragraphs.flatMap(p => p.refs))];
        return `<section class="report-section" aria-labelledby="report-section-${index}"><div class="report-heading"><span class="report-number" aria-hidden="true">${String(index+1).padStart(2,"0")}</span><h2 class="reader-section-heading ${index === 0 ? "reader-section-heading--lead" : ""}" id="report-section-${index}">${e(section.title)}</h2></div><div class="article-body">${section.paragraphs.map(p => `<p>${emphasis(p)}</p>`).join("")}</div>${sourceMarkup(refs,sources)}</section>`;
      }).join("") + (briefs.length ? `<section class="report-section" aria-labelledby="report-briefs"><div class="report-heading"><h2 class="reader-section-heading" id="report-briefs">简讯</h2></div><div class="article-body"><ul>${briefs.map(brief=>`<li>${e(brief.text)}</li>`).join("")}</ul></div>${sourceMarkup([...new Set(briefs.flatMap(brief=>brief.refs))],sources)}</section>` : "") +
      (!hasContent ? '<div class="reader-empty"><p>本期暂无符合条件的新闻。</p></div>' : "") + '<footer class="report-end">本期完</footer>';
  }
  modelMarkup(report) {
    const seconds = Number.isFinite(report.generation_seconds) ? Math.round(report.generation_seconds) : null;
    const duration = seconds === null || report.status !== "done" ? "" : `生成用时 ${seconds < 1 ? "不足 1 秒" : seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds/60)} 分 ${seconds%60} 秒`}`;
    const parts = [report.model_label, duration, report.snapshot_captured_at ? `取材于 ${dateText(report.snapshot_captured_at)}` : ""].filter(Boolean);
    return parts.length ? `<div class="article-meta-bottom">${parts.map(part=>`<span>${e(part)}</span>`).join("")}</div>` : "";
  }
  progressMarkup(report) {
    const progress = report.progress || {}, stage = progress.stage;
    if (report.status !== "running") return stage ? `<p class="muted">上次进行到：${e(progressText(report))}</p>` : "";
    const steps = ["准备取材", "新闻快照", "编写报告", "校验内容", "保存报告"];
    const current = {waiting_material:0,indexing:0,capturing:1,reusing_material:1,preparing:2,selecting:2,merging:2,checking_material:2,repairing_material:2,writing:2,repairing:2,validating:3,saving:4,done:5}[stage] ?? 0;
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
    this.popover.show();
    if (this.creation) {this.renderCreation(); $("#reportCreationKind")?.focus({preventScroll:true}); void this.refreshCreationModel(); return;}
    const draft = {periods:null,kind:"",start:"",end:"",requestId:requestId(),submitting:false,error:""};
    this.creation = draft;
    this.renderCreation();
    this.creationHost.querySelector("[data-creation-cancel]").focus({preventScroll:true});
    try {
      const data = await api("/api/reports/manual-options");
      if (this.creation !== draft) return;
      draft.periods = data.periods; draft.token = data.database_token; draft.max = data.now.slice(0,16);
      draft.windows = data.windows || structuredClone(defaultReportWindows);
      draft.modelLabel = data.model_label;
      draft.modelSettings = data.model_settings; draft.modelChoices = structuredClone(data.model_settings); draft.catalogs = {};
      draft.clockOffset = new Date(data.now).getTime()-Date.now();
      const candidates = data.periods.filter(p => this.kind === "daily" ? ["morning","noon","evening"].includes(p.kind) : p.kind === this.kind);
      draft.kind = candidates.sort((a,b) => b.period_end.localeCompare(a.period_end))[0]?.kind || data.periods[0]?.kind || "";
      this.setCreationPeriod(); this.renderCreation();
      if (this.popover.open) $("#reportCreationKind")?.focus({preventScroll:true});
    } catch (error) {
      if (this.creation !== draft) return;
      draft.error = reportError(error); this.renderCreation();
    }
  }
  setCreationPeriod() {
    const draft = this.creation, period = draft.periods.find(p => p.kind === draft.kind);
    draft.selection = ["morning","noon","evening"].includes(draft.kind)
      ? new Date(Date.now()+draft.clockOffset+8*60*60*1000).toISOString().slice(0,10) : period?.selection || "";
    draft.start = period?.period_start.slice(0,16) || "";
    draft.end = period?.period_end.slice(0,16) || "";
  }
  changeCreation(event) {
    const draft = this.creation, input = event.target;
    if (draft && !draft.submitting && ["reportRuntime","reportModel","reportEffort"].includes(input.name)) {
      this.changeModelChoice(input); return;
    }
    if (!draft || draft.submitting || !["reportCreationKind","reportSelection","reportStart","reportEnd"].includes(input.name)) return;
    const key = {reportCreationKind:"kind",reportSelection:"selection",reportStart:"start",reportEnd:"end"}[input.name];
    const value = ["start","end"].includes(key) ? localDateTime(input.value) || input.value : input.value;
    if (["start","end"].includes(key) && event.type === "change" && localDateTime(value)) input.value = value.replace("T"," ");
    if (draft[key] === value) return;
    draft[key] = value; draft.requestId = requestId(); draft.error = "";
    if (key === "kind") {
      this.setCreationPeriod();
      $("#reportPeriodFields").innerHTML = this.creationFields();
    }
    this.renderCreationPeriod();
    if (key === "kind") this.mountCalendar();
    else if (key === "selection") this.calendar?.sync();
  }
  creationFields() {
    const draft = this.creation;
    if (draft.kind === "custom") return `<div class="form-group-grid">${[["Start","开始时间",draft.start],["End","结束时间",draft.end]].map(([key,label,value])=>`<label class="field-item"><span class="field-label">${label}</span><input class="field-input" id="report${key}" name="report${key}" type="text" placeholder="YYYY-MM-DD HH:mm" autocomplete="off" spellcheck="false" value="${e(value.replace("T"," "))}" aria-describedby="reportCreationPeriod" required></label>`).join("")}</div><div class="date-calendar date-calendar--inline" id="reportCalendar"></div>`;
    const monthly = draft.kind === "monthly", label = monthly ? "月份" : draft.kind === "weekly" ? "起始日期" : "日期";
    return `<label class="field-item"><span class="field-label">${label}</span><input class="field-input" id="reportSelection" name="reportSelection" type="text" placeholder="${monthly?'YYYY-MM':'YYYY-MM-DD'}" autocomplete="off" spellcheck="false" value="${e(draft.selection)}" aria-describedby="reportCreationPeriod" required></label><div class="date-calendar date-calendar--inline" id="reportCalendar"></div>`;
  }
  renderCreation() {
    const draft = this.creation;
    const heading = '<div class="popover-heading"><h2 class="reader-section-heading" id="reportCreationTitle">新增日报</h2></div>';
    if (!draft.periods) {
      this.creationHost.innerHTML = heading + (draft.error ? `<p class="error-state" role="alert">${e(draft.error)}</p><button class="btn-secondary" type="button" data-creation-reload>重试</button>` : '<p class="muted" role="status">正在读取可选时间范围…</p>') + '<div class="button-row"><button class="btn-secondary" type="button" data-creation-cancel>取消</button></div>';
      return;
    }
    this.creationHost.innerHTML = heading + `<form class="popover-form" id="reportCreationForm"><div class="field-item"><p class="muted catchup-runtime" id="reportCreationModel" aria-live="polite"></p><button class="reader-tool-btn" type="button" data-creation-model-reload hidden>重新读取模型</button></div><details id="reportTemporaryModel" ${draft.modelExpanded?'open':''}><summary class="reader-tool-btn">临时模型设置</summary><div class="section-stack" id="reportModelChoices"></div></details><label class="field-item"><span class="field-label">报告类型</span><select class="field-select" id="reportCreationKind" name="reportCreationKind" aria-label="报告类型">${draft.periods.map(p=>`<option value="${e(p.kind)}" ${p.kind===draft.kind?'selected':''}>${e(p.kind_label)}</option>`).join("")}</select></label>
      <div id="reportPeriodFields">${this.creationFields()}</div>
      <p class="field-label" id="reportCreationPeriod"></p><p class="error-state" id="reportCreationError" role="alert"></p><div class="button-row"><button class="btn-secondary" type="button" data-creation-cancel>取消</button><button class="btn-primary" type="submit" ${!draft.kind?'disabled':''}>开始生成</button></div></form>`;
    this.renderModelChoices(); this.renderCreationPeriod(); this.renderCreationModel(); this.mountCalendar(); this.selects.refresh(this.creationHost);
    if (draft.submitting) this.setCreationBusy();
  }
  async refreshCreationModel() {
    const draft = this.creation;
    if (!draft?.periods || draft.submitting) return;
    const sequence = draft.modelSequence = (draft.modelSequence || 0)+1;
    draft.modelLoading = true; draft.modelFailed = false; this.renderCreationModel();
    try {
      const data = await api("/api/reports/manual-options");
      if (this.creation !== draft || sequence !== draft.modelSequence) return;
      draft.modelLabel = data.model_label;
      draft.modelSettings = data.model_settings;
      if (!draft.modelOverride && JSON.stringify(draft.modelChoices) !== JSON.stringify(data.model_settings)) {
        draft.modelChoices = structuredClone(data.model_settings); this.renderModelChoices();
      }
    } catch {
      if (this.creation !== draft || sequence !== draft.modelSequence) return;
      draft.modelFailed = true;
    } finally {
      if (this.creation === draft && sequence === draft.modelSequence) {
        draft.modelLoading = false; this.renderCreationModel();
        if (draft.modelExpanded) void this.loadCreationCatalog();
      }
    }
  }
  renderCreationModel() {
    const draft = this.creation, node = $("#reportCreationModel");
    if (!draft || !node) return;
    node.textContent = draft.modelLoading ? "正在读取 Runtime 和模型…" : draft.modelFailed ? "Runtime 和模型读取失败" : draft.modelLabel || "模型尚未配置";
    if (draft.modelOverride && !draft.modelLoading && !draft.modelFailed) {
      const c = draft.modelChoices, selected = c[c.runtime];
      node.textContent = [c.runtime === "codex" ? "Codex" : "Pi", selected.model || selected.profile || "默认模型", selected.reasoning_effort, "临时"].filter(Boolean).join(" · ");
    }
    this.creationHost.querySelector("[data-creation-model-reload]").hidden = !draft.modelFailed;
    this.creationHost.querySelector('[type="submit"]').disabled = !!(draft.submitting || draft.modelLoading || draft.modelFailed || !draft.kind || this.invalidModelChoice());
  }
  creationCatalogKey() {
    const c = this.creation.modelChoices;
    return JSON.stringify([c.runtime, ...(c.runtime === "codex" ? [c.codex.provider || "", c.codex.profile || ""] : [])]);
  }
  invalidModelChoice() {
    const d = this.creation, c = d.modelChoices;
    return d.modelOverride && (d.catalogs[this.creationCatalogKey()]?.loading || c.runtime === "pi" && (!c.pi.provider || !c.pi.model));
  }
  renderModelChoices() {
    const d = this.creation, host = $("#reportModelChoices");
    if (!d?.modelChoices || !host) return;
    const focused = host.contains(document.activeElement) ? document.activeElement.id : "";
    const c = d.modelChoices, selected = c[c.runtime], codex = c.runtime === "codex";
    const catalog = d.catalogs[this.creationCatalogKey()] || {}, models = catalog.options || [];
    const modelValue = codex ? selected.model : selected.model ? JSON.stringify([selected.provider,selected.model]) : "";
    const options = models.map(m=>codex ? m : {...m,value:JSON.stringify([m.provider,m.value]),label:`${m.label || m.value} · ${m.provider}`});
    if (modelValue && !options.some(m=>m.value === modelValue)) options.push({value:modelValue,label:selected.model});
    const model = models.find(m=>m.value === selected.model && (codex || m.provider === selected.provider));
    const next = document.createElement("div");
    next.innerHTML = `<div class="form-group-grid">${selectField("Runtime","reportRuntime",c.runtime,[{value:"codex",label:"Codex CLI"},{value:"pi",label:"Pi SDK / OAR"}],null)}${selectField("模型","reportModel",modelValue,options,codex?"沿用本机默认":"请选择模型")}${selectField("推理程度","reportEffort",selected.reasoning_effort || "",model?.reasoning_efforts || [],"沿用默认")}</div><p class="muted" role="status" ${catalog.loading || catalog.error?'':'hidden'}>${e(catalog.loading?"正在读取模型目录…":catalog.error || "")}</p><div class="button-row" ${d.modelOverride || catalog.error?'':'hidden'}><button class="reader-tool-btn" type="button" data-report-catalog-reload ${catalog.error?'':'hidden'}>重试</button><button class="reader-tool-btn" type="button" data-report-model-reset ${d.modelOverride?'':'hidden'}>恢复日报设置</button></div>`;
    next.querySelector('[name="reportModel"]').disabled = !catalog.options || !!catalog.loading || d.submitting;
    next.querySelector('[name="reportEffort"]').disabled = !!catalog.loading || d.submitting || !model?.reasoning_efforts?.length;
    next.querySelector('[name="reportRuntime"]').disabled = d.submitting;
    // Keep Picker triggers stable while directory requests finish. A late
    // response must not remove the control under the pointer or keyboard.
    for (const select of next.querySelectorAll("select")) {
      const previous = host.querySelector(`[name="${select.name}"]`);
      if (!previous) continue;
      if (previous.innerHTML !== select.innerHTML || previous.disabled !== select.disabled) {
        if (this.selects.select === previous) this.selects.close(false);
        previous.innerHTML = select.innerHTML;
      }
      previous.value = select.value; previous.disabled = select.disabled;
      select.closest(".field-item").replaceWith(previous.closest(".field-item"));
    }
    host.replaceChildren(...next.childNodes);
    this.selects.refresh(host);
    if (focused) document.getElementById(focused)?.focus({preventScroll:true});
  }
  async loadCreationCatalog(reload = false) {
    const d = this.creation;
    if (!d?.modelChoices || d.submitting) return;
    const key = this.creationCatalogKey();
    if (d.catalogs[key]?.loading || !reload && d.catalogs[key]) return;
    const state = d.catalogs[key] = {loading:true,options:d.catalogs[key]?.options || []};
    this.renderModelChoices(); this.renderCreationModel();
    try {
      const data = await api("/api/reports/model-options",{method:"POST",body:{runtime:d.modelChoices.runtime}});
      Object.assign(state,data.catalog);
    } catch(error) {state.error = reportError(error);}
    finally {
      state.loading = false;
      if (this.creation === d && key === this.creationCatalogKey()) {this.renderModelChoices(); this.renderCreationModel();}
    }
  }
  changeModelChoice(input) {
    const d = this.creation, c = d.modelChoices, previous = JSON.stringify(c);
    if (input.name === "reportRuntime") c.runtime = input.value;
    else if (input.name === "reportModel") {
      const selected = c[c.runtime];
      const [provider,model] = c.runtime === "pi" && input.value ? JSON.parse(input.value) : ["",input.value];
      if (selected.model === model && (c.runtime === "codex" || selected.provider === provider)) return;
      selected.model = model;
      if (c.runtime === "pi") selected.provider = provider;
      selected.reasoning_effort = "";
    } else c[c.runtime].reasoning_effort = input.value;
    if (JSON.stringify(c) === previous) return;
    const selected = c[c.runtime];
    d.modelOverride = {runtime:c.runtime, model:selected.model || "", reasoning_effort:selected.reasoning_effort || "",
      ...(c.runtime === "pi" ? {provider:selected.provider || ""} : {})};
    d.requestId = requestId(); d.error = "";
    this.renderModelChoices(); this.renderCreationModel(); this.renderCreationPeriod();
    // Preserve keyboard focus after replacing dependent Picker options.
    this.creationHost.querySelector(`[data-select-trigger="${input.name}"]`)?.focus({preventScroll:true});
    void this.loadCreationCatalog();
  }
  mountCalendar() {
    this.calendar = null;
    const draft = this.creation;
    if (draft.kind === "custom") {
      this.calendar = new DateTimePicker({panel:$("#reportCalendar"), inputs:[$("#reportStart"),$("#reportEnd")],
        selects:this.selects, getMax:() => draft.max, onSelect:input=>this.changeCreation({target:input})});
      return;
    }
    this.calendar = new Calendar({panel:$("#reportCalendar"), input:$("#reportSelection"),
      mode:draft.kind === "monthly" ? "month" : "date", rangeDays:draft.kind === "weekly" ? 7 : 1,
      getToday:() => draft.max.slice(0,10), getMax:() => $("#reportSelection").max,
      onSelect:() => this.changeCreation({target:$("#reportSelection")})});
  }
  setCreationBusy() {
    this.creationHost.querySelectorAll("button,select,input").forEach(node=>node.disabled=true);
    const submit = this.creationHost.querySelector('[type="submit"]');
    if (submit) submit.textContent = "正在提交…";
  }
  renderCreationPeriod() {
    const draft = this.creation;
    draft.max = new Date(Date.now()+draft.clockOffset+8*60*60*1000).toISOString().slice(0,16);
    if (draft.kind === "custom") {
      for (const [id,value] of [["reportStart",draft.start],["reportEnd",draft.end]]) {
        $("#"+id).setCustomValidity(value && !localDateTime(value) ? "请输入有效时间（YYYY-MM-DD HH:mm）"
          : value > draft.max ? "时间不能晚于当前时间"
          : id === "reportEnd" && value && localDateTime(draft.start) && value <= draft.start ? "结束时间须晚于开始时间" : "");
      }
    } else {
      [draft.start, draft.end] = selectedRange(draft.kind, draft.selection, draft.windows);
      const today = draft.max.slice(0,10), input = $("#reportSelection");
      input.max = draft.kind === "monthly" ? shiftDay(today.slice(0,7)+"-01",-1).slice(0,7)
        : draft.kind === "weekly" ? shiftDay(today,-7)
        : today;
      input.setCustomValidity(draft.end > draft.max ? "所选报告的时间范围尚未结束" : draft.selection && !draft.start
        ? draft.kind === "monthly" ? "请输入有效月份（YYYY-MM）" : "请输入有效日期（YYYY-MM-DD）" : "");
    }
    const calendarWindow = draft.windows?.[draft.kind]?.start === "00:00" && draft.windows?.[draft.kind]?.end === "00:00";
    const range = draft.kind === "weekly" && calendarWindow ? `${draft.selection} — ${shiftDay(draft.selection,6)} · 共 7 天`
      : draft.kind === "monthly" && calendarWindow ? `${draft.selection} · 整月`
      : `${dateText(draft.start+"+08:00")} — ${dateText(draft.end+"+08:00")}`;
    $("#reportCreationPeriod").textContent = localDateTime(draft.start) && localDateTime(draft.end) ? range+" · 北京时间" : "请选择时间范围";
    $("#reportCreationError").textContent = draft.error || (localDateTime(draft.end) && draft.end > draft.max ? "所选报告的时间范围尚未结束" : "");
  }
  async submitCreation() {
    const draft = this.creation;
    if (!draft || draft.submitting || draft.modelLoading || draft.modelFailed || this.invalidModelChoice()) return;
    const form = $("#reportCreationForm");
    this.renderCreationPeriod();
    if (!form.reportValidity()) return;
    draft.submitting = true; this.setCreationBusy();
    try {
      const range = draft.kind === "custom" ? {period_start:draft.start+"+08:00",period_end:draft.end+"+08:00"} : {selection:draft.selection,window:draft.windows[draft.kind]};
      const data = await api("/api/reports/manual", {method:"POST",body:{kind:draft.kind,...range,request_id:draft.requestId,database_token:draft.token,...(draft.modelOverride ? {model_override:draft.modelOverride} : {})}});
      if (this.creation !== draft) return;
      const show = this.popover.open && this.isVisible();
      this.popover.close(true); this.creation = null;
      toast("报告已加入生成队列。");
      if (!show) {if (this.isVisible()) await this.load({quiet:true}); return;}
      this.kind = collectionFor(draft.kind);
      this.entries = []; this.meta = null; this.details = data.report; this.selected = data.report.id;
      const key = this.groupKey(data.report); this.expandedGroups.set(this.kind+":"+key,true);
      this.renderFilters(); await this.load({quiet:true,group:key});
      if (this.selected === data.report.id && this.isVisible()) await this.select(data.report.id,true);
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
    if (this.popover.open || !["previous","next"].includes(action) || event.target.closest("button,a,summary")) return;
    event.preventDefault();
    const visible = this.archiveRows().filter(row=>this.groupOpen(this.groupKey(row)));
    const index = visible.findIndex(row => row.id === this.selected);
    const entry = visible[Math.max(0, Math.min(visible.length-1,index+(action === "next" ? 1 : -1)))];
    if (entry) this.select(entry.id);
  }
}
