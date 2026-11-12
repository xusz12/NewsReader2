import { DatePicker, dateKey, localDate } from "./date-picker.js?v=feature-pickers-1";
import { $, api, escapeHTML as e, errorText, toast } from "./ui.js";
import { dismissOnOutsidePress } from "./outside-dismiss.js?v=report-layout-1";
import { classificationStatus } from "./classification-progress.js?v=translation-routing-1";

const recordsFrom = data => (data.records || (data.days || []).flatMap(day => day.groups.flatMap(group =>
  (group.entries || (group.labels || []).map(label => ({ label: label.replace(/ error$/, "") })))
    .map(entry => ({ ...entry, date: day.date, time: group.time, status: "failed", error_count: 1, error: entry.error || "未记录具体错误" }))
))).sort((a, b) => `${b.date} ${b.time}`.localeCompare(`${a.date} ${a.time}`))
  .map((record, index) => ({ ...record, index }));

const countText = value => Number.isFinite(value) ? `${value} 条` : "未记录";
const aggregateCountText = (value, incomplete = false) => incomplete
  ? value > 0 ? `≥ ${countText(value)}` : "未记录"
  : countText(value);

const hasSourceError = record => record.error_count > 0 || record.status === "failed" || !!record.error;
const noticesFrom = record => (record.notices || []).filter(notice => ["translation", "publication"].includes(notice.stage));
const needsAttention = record => hasSourceError(record) || noticesFrom(record).length > 0;
const batchKey = record => record.run_id || `${record.date}T${record.time}`;
const actionKey = (record, stage) => `${batchKey(record)}:${stage}:${stage === "translation" ? record.source_id : ""}`;

function batchesFrom(records) {
  const batches = new Map();
  for (const record of records) {
    const key = batchKey(record);
    if (!batches.has(key)) batches.set(key, { key, started: record.batch_started_at || `${record.date}T${record.time}+08:00`, records: [] });
    batches.get(key).records.push(record);
  }
  return [...batches.values()].sort((a, b) => b.started.localeCompare(a.started));
}

function metricText(records, key) {
  const known = records.map(record => record[key]).filter(Number.isFinite);
  return known.length ? aggregateCountText(known.reduce((sum, count) => sum + count, 0), known.length < records.length) : "未记录";
}

function recordStatus(record) {
  if (record.kind === "publication") return "跨日入库";
  if (record.status === "pending") return "等待采集";
  if (record.status === "running") return "采集中";
  if (record.status === "canceled") return "账号已移除 · 已取消采集";
  if (hasSourceError(record)) return `错误 · ${errorBadge(record.error || "")}`;
  if (noticesFrom(record).some(notice => notice.stage === "translation")) return "提示 · 翻译失败";
  if (record.publication_status === "failed") return "提示 · 入库失败";
  if (record.publication_status === "pending") return "等待整批入库";
  if (record.published_at && !record.published_at.startsWith(record.date)) return `${record.published_at.slice(0, 10)} 已入库`;
  return "采集完成";
}

function errorBadge(log) {
  const code = log.match(/\bHTTP(?:Error)?[\s:/]*(?:[12](?:\.\d)?\s+)?([45]\d\d)\b/i)?.[1];
  if (code) return `HTTP ${code}`;
  return /timeout|timed?\s*out|超时/i.test(log) ? "连接超时" : "采集异常";
}

function shiftDay(day, offset) {
  const date = localDate(day);
  date.setDate(date.getDate() + offset);
  return dateKey(date);
}

export class UpstreamErrors {
  constructor({ onClassificationRefresh, onClassificationRetry, onClassificationSettings } = {}) {
    Object.assign(this, { onClassificationRefresh, onClassificationRetry, onClassificationSettings });
    this.panel = $("#upstreamErrorsPanel");
    this.backdrop = $("#upstreamErrorsBackdrop");
    this.button = $("#upstreamErrorsBtn");
    this.content = $("#upstreamErrorsContent");
    this.loadMessage = $("#upstreamLoadMessage");
    this.retryButton = $("[data-upstream-retry]", this.panel);
    this.retryButton.onclick = () => this.load(this.requestedDay);
    this.loadedDay = "";
    this.sequence = 0;
    this.view = "all";
    this.expandedBatches = new Set();
    this.retrying = new Set();
    this.actionFeedback = new Map();
    this.today = dateKey(new Date());
    this.button.onclick = event => this.open ? this.close() : this.show(event.detail === 0);
    this.button.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") { event.preventDefault(); this.show(true); }
    });
    $("#closeUpstreamErrorsBtn").onclick = () => this.close(true);
    dismissOnOutsidePress({
      panel: this.panel, trigger: this.button, priority: 1,
      isOpen: () => this.open,
      dismiss: () => this.close(true),
    });
    document.addEventListener("keydown", event => {
      if (this.open && event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation();
        if (this.datePicker.open) this.datePicker.close(true);
        else this.close(true);
      }
    }, true);
    document.addEventListener("topbar:popover-open", event => { if (event.detail !== this.panel.id) this.close(); });
    window.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("scroll", () => this.position());
    this.datePicker = new DatePicker({ input: $("#upstreamErrorDate"), button: $("#upstreamCalendarBtn"), panel: $("#upstreamCalendar"), error: $("#upstreamDateError"), getToday: () => this.today, onSelect: day => this.selectDay(day), onInvalid: () => { ++this.sequence; this.content.setAttribute("aria-busy", "false"); this.setLoadStatus(""); } });
    $(".upstream-error-controls", this.panel).addEventListener("focusin", event => {
      if (!event.target.closest(".date-calendar")) event.target.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
    });
    $(".upstream-date-shortcuts").onclick = event => {
      const shortcut = event.target.closest("[data-upstream-day-offset]");
      if (shortcut) {
        const offset = Number(shortcut.dataset.upstreamDayOffset);
        if (offset === 0) this.showToday();
        else this.selectDay(shiftDay(this.today, offset));
      }
    };
    const stepDate = offset => {
      const day = $("#upstreamErrorDate").value;
      if (this.datePicker.setValue(day)) this.selectDay(shiftDay(day, offset));
    };
    $("#upstreamPreviousDayBtn").onclick = () => stepDate(-1);
    $("#upstreamNextDayBtn").onclick = () => stepDate(1);
    this.updateDateControls();
    this.content.onclick = event => {
      const view = event.target.closest("[data-upstream-view]");
      const copy = event.target.closest("[data-upstream-copy]");
      const action = event.target.closest("[data-upstream-action]");
      const classification = event.target.closest("[data-upstream-classification]");
      if (view) { this.view = view.dataset.upstreamView; this.render(); }
      else if (copy) this.copyLog(copy);
      else if (action) void this.retryNotice(action);
      else if (classification) void this.classificationAction(classification);
    };
    this.content.addEventListener("toggle", event => {
      const details = event.target;
      if (!this.content.contains(details) || details.dataset.upstreamDetails === undefined) return;
      if (details.open) this.expandedBatches.add(details.dataset.upstreamDetails);
      else this.expandedBatches.delete(details.dataset.upstreamDetails);
    }, true);
    const sync = () => { if (!document.hidden) { this.refreshBadge(); this.refreshRecords(); } };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    setInterval(sync, 60000);
    sync();
  }

  get open() { return !this.panel.hidden; }

  show(keyboard = false) {
    document.dispatchEvent(new CustomEvent("topbar:popover-open", { detail: this.panel.id }));
    this.panel.hidden = false;
    this.backdrop.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.position(); this.showToday();
    void this.onClassificationRefresh?.();
    if (keyboard) $("#upstreamErrorDate").focus({ preventScroll: true });
  }

  close(restoreFocus = false) {
    if (!this.open) return;
    this.datePicker.close();
    this.panel.hidden = true;
    this.backdrop.hidden = true;
    ++this.sequence;
    this.content.setAttribute("aria-busy", "false");
    this.setLoadStatus("");
    this.button.setAttribute("aria-expanded", "false");
    if (restoreFocus) this.button.focus({ preventScroll: true });
    document.dispatchEvent(new Event("agent:rendered"));
  }

  position() {
    if (!this.open) return;
    const anchor = this.button.getBoundingClientRect(), viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    this.panel.style.width = `${Math.min(640, width - 24)}px`;
    this.panel.style.left = `${Math.max(left + 12, Math.min(anchor.left + anchor.width / 2 - this.panel.offsetWidth / 2, left + width - this.panel.offsetWidth - 12))}px`;
    this.panel.style.top = `${Math.max(top + 12, anchor.bottom + 10)}px`;
    const available = Math.max(0, top + height - this.panel.getBoundingClientRect().top - 12);
    this.panel.style.maxHeight = `${Math.min(640, available)}px`;
    this.datePicker?.position();
  }

  showToday() {
    // Omit the day so the server resolves today's date afresh, including after midnight.
    this.datePicker.setValue(this.today);
    this.updateDateControls();
    this.refreshBadge();
    this.load("");
  }

  selectDay(day) {
    if (!this.datePicker.setValue(day)) return;
    this.load(day);
  }

  updateDateControls() {
    const date = $("#upstreamErrorDate");
    const selected = date.value || this.today;
    date.dataset.max = this.today;
    this.datePicker.render();
    document.querySelectorAll("[data-upstream-day-offset]").forEach(button => {
      const offset = Number(button.dataset.upstreamDayOffset);
      const day = shiftDay(this.today, offset);
      const shortDate = `${Number(day.slice(5, 7))}/${Number(day.slice(8, 10))}`;
      const label = button.querySelector(".upstream-date-label") || button;
      label.textContent = offset === 0 ? `今天 (${shortDate})` : offset === -1 ? `昨天 (${shortDate})` : shortDate;
      button.setAttribute("aria-pressed", String(selected === day));
      button.setAttribute("aria-label", `${offset === 0 ? "今天" : offset === -1 ? "昨天" : "前天"}，${day}`);
    });
    $("#upstreamNextDayBtn").disabled = selected >= this.today;
  }

  async refreshBadge() {
    if (this.badgeRequest) return this.badgeRequest;
    this.badgeRequest = (async () => {
      try {
        // Current source and processing failures, independent of the history date.
        const data = await api("/api/error-stats?details=0");
        this.badgeData = data;
        this.badgeError = false;
        this.today = data.today || data.day || this.today;
        this.updateDateControls();
      } catch {
        this.badgeError = true;
      }
      this.renderBadge();
    })();
    try { await this.badgeRequest; } finally { this.badgeRequest = null; }
  }

  classificationIssue() {
    const { progress: p, error } = this.classificationSnapshot || {};
    if (p?.enabled === false) return null;
    if (error) return { message: error, retry: false };
    if (!p || !(p.pending || p.running || p.failed || p.error)) return null;
    const errors = p.errors || [];
    if (p.configured && !p.error && !p.failed && !p.throttle?.rate_limited && !errors.length) return null;
    const codes = [...new Set([p.error, ...errors.map(row => row.code)].filter(Boolean))];
    const reasons = { http_401: "鉴权失败（HTTP 401）", http_403: "无调用权限（HTTP 403）", http_429: "请求限流（HTTP 429）",
      http_529: "服务过载（HTTP 529）", network_error: "网络连接失败", invalid_response: "模型返回格式异常",
      classification_failed: "模型调用失败", worker_unavailable: "分类服务暂不可用", interrupted: "分类任务中断" };
    const status = p.configured && !p.error && errors.length && !p.failed && !p.throttle?.rate_limited
      ? "分类请求失败，正在自动重试" : classificationStatus(p);
    return { message: [status, p.failed ? `${p.failed} 条新闻分类失败` : "",
      ...codes.map(code => reasons[code] || code)].filter(Boolean).join("；"), retry: !!(p.failed || p.error) };
  }

  updateClassification(snapshot) {
    this.classificationSnapshot = snapshot;
    this.renderBadge();
    if (this.open && this.records && !this.datePicker.open && document.activeElement !== $("#upstreamErrorDate")) this.render();
  }

  renderBadge() {
    const data = this.badgeData;
    if (!data && !this.badgeError) return;
    const sources = data?.unresolved_count ?? (data ? recordsFrom(data).reduce((sum, row) => sum + (row.error_count || 0), 0) : 0);
    const notices = data?.notice_count || 0, classification = this.classificationIssue();
    const count = sources + notices + Number(!!classification);
    $("#upstreamErrorSummary").hidden = count === 0 && !this.badgeError;
    $("#upstreamErrorBadge").textContent = count > 99 ? "99+" : count ? String(count) : this.badgeError ? "?" : "";
    this.button.classList.toggle("has-errors", count > 0);
    this.button.classList.toggle("no-errors", count === 0 && !this.badgeError);
    const parts = [sources ? `${sources} 个采集来源尚未恢复` : "", notices ? `${notices} 处翻译或入库问题待处理` : "",
      classification ? `新闻分类 · ${classification.message}` : "", this.badgeError ? "采集记录统计暂不可用" : ""].filter(Boolean);
    this.button.title = `${parts.join("；") || "当前无采集或模型处理异常"} · 点击查看采集记录`;
    this.button.setAttribute("aria-label", `采集记录：${this.button.title}`);
  }

  classificationCard() {
    const issue = this.classificationIssue();
    if (!issue) return "";
    return `<article class="upstream-error-card" data-upstream-model="classification">
      <div class="upstream-card-head"><div class="upstream-source-title"><strong>新闻分类 · Jev</strong></div><span class="upstream-error-tag">当前异常</span></div>
      <div class="section-stack"><div class="muted">${e(issue.message)}</div>
      <div class="button-row"><button class="reader-tool-btn" type="button" data-upstream-classification="settings">检查分类设置</button>
      <button class="reader-tool-btn" type="button" data-upstream-classification="${issue.retry ? "retry" : "refresh"}" aria-busy="${!!this.classificationBusy}"${this.classificationBusy ? " disabled" : ""}>${issue.retry ? "重试分类" : "刷新状态"}</button></div>
      ${this.classificationFeedback ? `<div class="inline-error" role="alert">${e(this.classificationFeedback)}</div>` : ""}</div>
    </article>`;
  }

  async classificationAction(button) {
    const action = button.dataset.upstreamClassification;
    if (action === "settings") { this.close(); await this.onClassificationSettings?.(); return; }
    if (this.classificationBusy) return;
    const restoreFocus = document.activeElement === button;
    this.classificationBusy = true;
    this.classificationFeedback = "";
    this.render();
    try {
      if (action === "retry") await this.onClassificationRetry?.();
      else await this.onClassificationRefresh?.();
    } catch (error) { this.classificationFeedback = errorText(error); }
    finally {
      this.classificationBusy = false;
      this.render();
      if (restoreFocus && this.open && (document.activeElement === document.body || this.content.contains(document.activeElement)))
        (this.content.querySelector(`[data-upstream-classification="${action}"]`) || this.content.querySelector(`[data-upstream-view="${this.view}"]`))?.focus({ preventScroll: true });
    }
  }

  activeNoticeCards() {
    const records = (this.activeNotices || []).map((record, index) => ({ ...record, index:`active-${index}`, batch_started_at:record.date }));
    return batchesFrom(records).map(batch => `<article class="upstream-error-card" data-upstream-current-notice>
      <div class="upstream-card-head"><div class="upstream-source-title"><strong>${e(batch.started.slice(0, 10))} 采集</strong></div><span class="upstream-error-tag">仍待处理</span></div>
      <div class="section-stack">${batch.records.flatMap(record => noticesFrom(record).map(notice => this.notice(record, notice))).join("")}</div>
    </article>`).join("");
  }

  setLoadStatus(message, failed = false) {
    this.loadMessage.textContent = message;
    this.loadMessage.parentElement.hidden = !message;
    this.loadMessage.parentElement.classList.toggle("is-error", failed);
    this.retryButton.hidden = !failed;
  }

  refreshRecords() {
    if (!this.open || !this.loadedDay || this.retrying.size || this.content.getAttribute("aria-busy") === "true" || this.datePicker.open
      || $("#upstreamErrorDate").value !== this.loadedDay || document.activeElement === $("#upstreamErrorDate")) return;
    return this.load(this.loadedDay);
  }

  async load(day = $("#upstreamErrorDate").value) {
    const sequence = ++this.sequence;
    const dateRevision = this.datePicker.inputRevision;
    this.requestedDay = day;
    this.updateDateControls();
    this.content.setAttribute("aria-busy", "true");
    // Keep the last successful view in place until the next date is ready.
    this.setLoadStatus("");
    if (!this.loadedDay) this.content.replaceChildren();
    try {
      const params = new URLSearchParams({ details: "1" });
      if (day) params.set("day", day);
      const data = await api(`/api/error-stats?${params}`);
      if (sequence !== this.sequence || !this.open) return;
      this.today = data.today || this.today;
      if (dateRevision === this.datePicker.inputRevision) this.datePicker.setValue(data.day);
      else this.datePicker.selectedDay = data.day;
      this.updateDateControls();
      this.records = recordsFrom(data);
      const shownNotices = new Set(this.records.flatMap(record => noticesFrom(record).map(notice => actionKey(record, notice.stage))));
      this.activeNotices = (data.active_notices || []).filter(record =>
        noticesFrom(record).some(notice => !shownNotices.has(actionKey(record, notice.stage))));
      this.stats = data.stats || {};
      this.render();
      if (this.loadedDay !== data.day) this.content.scrollTop = 0;
      this.loadedDay = data.day;
      this.content.setAttribute("aria-label", `${data.day} 的采集记录`);
      this.setLoadStatus("");
    } catch (error) {
      if (sequence === this.sequence && this.open) {
        this.setLoadStatus(`${errorText(error)}${this.loadedDay ? `；仍显示 ${this.loadedDay}` : ""}`, true);
        if (!this.loadedDay) this.content.innerHTML = '<div class="upstream-empty">暂时无法显示采集记录，请重新读取。</div>';
      }
    } finally {
      if (sequence === this.sequence) this.content.setAttribute("aria-busy", "false");
    }
  }

  render() {
    const focusedView = document.activeElement?.dataset.upstreamView;
    const focusedAction = document.activeElement?.dataset.upstreamActionKey;
    const focusedDetails = document.activeElement?.closest("summary")?.parentElement?.dataset.upstreamDetails;
    const focusedClassification = document.activeElement?.dataset.upstreamClassification;
    const records = this.records;
    const { fetched_count, duplicate_count, duplicate_unknown, ingested_count } = this.stats;
    const errorCount = this.stats.error_count ?? records.reduce((sum, row) => sum + (row.error_count || 0), 0);
    const batches = batchesFrom(records);
    const visible = this.view === "attention" ? batches.filter(batch => batch.records.some(needsAttention)) : batches;
    const html = `
      <div class="upstream-health-strip" role="group" aria-label="当日采集统计（北京时间）" tabindex="0">
        <div title="所选日期各来源返回的新闻候选，包含重复内容"><span class="upstream-stat-label">抓取</span><strong class="upstream-stat-value">${countText(fetched_count)}</strong></div>
        <div title="${duplicate_unknown ? `${duplicate_unknown} 条旧记录未保存去重数，无法还原完整数量${duplicate_count > 0 ? '；仅显示已知部分' : ''}` : "已存在于新闻库、待翻译或删除账本的重复新闻；不含内容过滤"}"><span class="upstream-stat-label">去重</span><strong class="upstream-stat-value">${aggregateCountText(duplicate_count, duplicate_unknown > 0)}</strong></div>
        <div title="所选日期实际新增入库的新闻；跨日完成的批次计入实际入库日"><span class="upstream-stat-label">纳入数据库</span><strong class="upstream-stat-value">${countText(ingested_count)}</strong></div>
        <div title="所选日期失败的来源采集记录数，后续恢复不删除历史错误；翻译和入库问题在提示旁处理"><span class="upstream-stat-label">错误</span><strong class="upstream-stat-value ${errorCount ? "warning" : "success"}">${errorCount} 条</strong></div>
      </div>
      <div class="upstream-classifier-bar">
        <div class="upstream-dim-switcher" role="group" aria-label="采集记录筛选">
          <button class="upstream-dim-btn" type="button" data-upstream-view="all" aria-pressed="${this.view === "all"}">全部信息</button>
          <button class="upstream-dim-btn" type="button" data-upstream-view="attention" aria-pressed="${this.view === "attention"}">仅错误与提示</button>
        </div>
      </div>
      <div class="upstream-errors-list">${this.classificationCard()}${this.activeNoticeCards()}
      ${visible.length ? visible.map(batch => this.card(batch)).join("")
        : records.length ? '<div class="upstream-empty"><strong>当天没有错误或提示。</strong></div>'
        : '<div class="upstream-empty"><strong>当天没有记录采集活动。</strong><p>可切换日期查看历史记录。</p></div>'}</div>`;
    // Commit the new view in one paint; unchanged responses keep their DOM and scroll position.
    if (html !== this.renderedHTML) {
      this.content.innerHTML = html;
      this.renderedHTML = html;
      if (focusedView !== undefined)
        [...this.content.querySelectorAll("[data-upstream-view]")].find(button => button.dataset.upstreamView === focusedView)?.focus({ preventScroll: true });
      if (focusedAction !== undefined)
        [...this.content.querySelectorAll("[data-upstream-action-key]")].find(button => button.dataset.upstreamActionKey === focusedAction && !button.disabled)?.focus({ preventScroll: true });
      if (focusedDetails !== undefined)
        [...this.content.querySelectorAll("[data-upstream-details]")].find(details => details.dataset.upstreamDetails === focusedDetails)?.querySelector("summary")?.focus({ preventScroll: true });
      if (focusedClassification !== undefined)
        (this.content.querySelector(`[data-upstream-classification="${focusedClassification}"]:not(:disabled)`) || this.content.querySelector(`[data-upstream-view="${this.view}"]`))?.focus({ preventScroll: true });
    }
  }

  card(batch) {
    const records = batch.records;
    const failures = records.filter(hasSourceError);
    const notices = new Map();
    for (const record of records) for (const notice of noticesFrom(record)) notices.set(actionKey(record, notice.stage), { record, notice });
    const status = failures.length && notices.size ? "错误与提示" : failures.length ? "采集错误" : notices.size ? "待处理提示"
      : records.every(record => record.status === "pending") ? "等待采集"
      : records.some(record => ["pending", "running"].includes(record.status)) ? "采集中"
      : records.some(record => record.publication_status === "pending") ? "等待整批入库" : "已完成";
    return `<article class="upstream-error-card" data-upstream-batch="${e(batch.key)}">
      <div class="upstream-card-head">
        <div class="upstream-source-title"><strong>${e(batch.started.slice(0, 19).replace("T", " "))} 采集</strong></div>
        <span class="${failures.length ? "upstream-error-tag" : notices.size ? "card-tag highlight" : "card-tag"}">${status}</span>
      </div>
      <div class="section-stack">
        <div class="muted collection-record-summary">抓取 ${metricText(records, "fetched_count")} · 去重 ${metricText(records, "duplicate_count")} · 纳入数据库 ${metricText(records, "ingested_count")} · 错误 ${records.reduce((sum, record) => sum + (record.error_count || 0), 0)} 条</div>
        ${failures.map(record => `<div class="upstream-log-box">
          <div class="upstream-log-head"><span>错误 · ${e(record.label)} · 等待下次采集</span><button class="upstream-log-copy-btn" type="button" data-upstream-copy="${record.index}" aria-label="复制 ${e(record.label)} 的报错日志">复制日志</button></div>
          <pre>${e(record.error || "未记录具体错误")}</pre><span class="upstream-copy-feedback" role="status"></span>
        </div>`).join("")}
        ${[...notices.values()].map(({ record, notice }) => this.notice(record, notice)).join("")}
        <details data-upstream-details="${e(batch.key)}"${this.expandedBatches.has(batch.key) ? " open" : ""}>
          <summary class="reader-tool-btn">来源明细（${records.length}）</summary>
          <div class="section-stack">${records.map(record => `<div class="field-item" data-upstream-source="${e(record.source_id || record.label)}">
            <div class="button-row"><strong class="muted">${e(record.label)}</strong><span class="card-tag">${e(recordStatus(record))}</span><time class="muted" datetime="${e(record.date)}T${e(record.time)}+08:00">${e(record.time)}</time></div>
            <div class="muted">抓取 ${countText(record.fetched_count)} · 去重 ${countText(record.duplicate_count)} · 纳入数据库 ${countText(record.ingested_count)}${record.filtered_count ? ` · 另过滤 ${record.filtered_count} 条` : ""}</div>
          </div>`).join("")}</div>
        </details>
      </div>
    </article>`;
  }

  notice(record, notice) {
    const key = actionKey(record, notice.stage), pending = this.retrying.has(key);
    const translation = notice.stage === "translation";
    const label = translation ? "重试翻译" : "重试入库";
    const message = translation ? `${record.label} · ${notice.count} 条新闻翻译失败` : "整批入库失败，译文已保留";
    return `<div class="field-item collection-record-notice">
      <div class="button-row"><span class="muted"><strong>提示</strong> · ${e(message)}</span>
        <button type="button" class="reader-tool-btn" data-upstream-action="${notice.stage}" data-upstream-action-key="${e(key)}" data-upstream-record="${record.index}" aria-label="${e(translation ? `${label} · ${record.label}` : label)}" aria-busy="${pending}"${pending ? " disabled" : ""}>${pending ? "正在提交…" : label}</button></div>
      <div class="muted">${e(notice.error).replace(/\n/g, "<br>")}</div>
      ${this.actionFeedback.has(key) ? `<div class="inline-error" role="alert">${e(this.actionFeedback.get(key))}</div>` : ""}
    </div>`;
  }

  async retryNotice(button) {
    const index = button.dataset.upstreamRecord;
    const record = index.startsWith("active-") ? this.activeNotices[Number(index.slice(7))] : this.records[Number(index)];
    const stage = button.dataset.upstreamAction;
    if (!record?.run_id || !noticesFrom(record).some(notice => notice.stage === stage)) return;
    const key = actionKey(record, stage);
    if (this.retrying.has(key)) return;
    const restoreFocus = document.activeElement === button;
    this.retrying.add(key);
    this.actionFeedback.delete(key);
    this.render();
    try {
      const data = await api("/api/collection/retry-translation", { method: "POST", body: {
        run_id: record.run_id, stage, ...(stage === "translation" ? { source_id: record.source_id } : {}),
      } });
      toast(data.retried ? stage === "translation" ? "已加入翻译重试队列" : "已加入入库重试队列" : "该问题已处理，记录已更新");
      this.retrying.delete(key);
      await this.refreshRecords();
      await this.refreshBadge();
      document.dispatchEvent(new Event("collection:changed"));
    } catch (error) {
      this.actionFeedback.set(key, errorText(error));
    } finally {
      this.retrying.delete(key);
      this.render();
      if (restoreFocus && this.open && (document.activeElement === document.body || this.content.contains(document.activeElement))) {
        const next = [...this.content.querySelectorAll("[data-upstream-action-key]")].find(node => node.dataset.upstreamActionKey === key)
          || this.content.querySelector(`[data-upstream-view="${this.view}"]`);
        next?.focus({ preventScroll: true });
      }
    }
  }

  async copyLog(button) {
    const log = this.records[Number(button.dataset.upstreamCopy)]?.error;
    const feedback = $(".upstream-copy-feedback", button.closest(".upstream-log-box"));
    try {
      await navigator.clipboard.writeText(log);
      feedback.textContent = "日志已复制";
    } catch {
      // Keep raw text selectable when clipboard permission or HTTPS is unavailable.
      const range = document.createRange();
      range.selectNodeContents($("pre", button.closest(".upstream-log-box")));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      feedback.textContent = "无法自动复制，已选中日志，请手动复制。";
    }
  }
}
