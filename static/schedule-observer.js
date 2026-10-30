import { api, escapeHTML as e, singlePane } from "./ui.js";
import { failureText, statusText, progressText } from "./report-status.js?v=reports-manual-1";

const time = value => value ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(value)) : "尚无记录";
const schedulerLabels = {running:"正常运行", starting:"正在启动", delayed:"检查已延迟", error:"排期检查失败", stopped:"已停止", not_running:"未运行"};
const triggerLabels = {scheduled:"自动生成", manual:"手动生成", preview:"试生成", imported:"样稿"};
const phaseLabels = {indexing:"正在刷新索引、读取新闻", editing:"正在整理材料、生成报告"};
const errors = {
  report_calendar_failed:"交易日历读取失败，请检查 reports/trading-calendar.json 的文件权限与内容。",
  report_schedule_failed:"排期检查失败，请检查数据库是否可访问；系统会自动重试。",
};
const field = (label, value) => `<div class="field-item"><span class="field-label">${e(label)}</span><span>${e(value)}</span></div>`;
const status = row => row.status === "done" ? "已完成" : statusText(row);

// Observation never starts a task or changes configuration. Poll only while
// this settings detail is visible, without re-rendering the settings draft.
export class ScheduleObserver {
  constructor({isVisible, onReportOpen}) {
    Object.assign(this, {isVisible, onReportOpen, sequence: 0});
    document.addEventListener("visibilitychange", () => this.sync());
    singlePane.addEventListener("change", () => this.sync());
  }
  mount(host) {
    this.stop();
    this.host = host; this.data = null; this.failed = false;
    host.onclick = event => {
      if (event.target.closest("[data-schedule-refresh]") && !this.loading) void this.refresh();
      const button = event.target.closest("[data-schedule-report]");
      if (button) this.onReportOpen?.({id: button.dataset.scheduleReport, kind: button.dataset.kind});
    };
    this.render();
  }
  visible() {return !!this.host?.isConnected && this.isVisible() && !document.hidden;}
  sync() {
    if (!this.visible()) return this.stop();
    if (!this.active) {this.active = true; void this.refresh();}
  }
  stop() {
    this.active = false; this.sequence++;
    clearTimeout(this.timer); this.controller?.abort();
  }
  async refresh() {
    if (!this.visible()) return;
    clearTimeout(this.timer); this.controller?.abort();
    this.controller = new AbortController();
    const sequence = ++this.sequence;
    this.loading = true; this.render();
    try {
      const data = await api("/api/reports/schedule-status", {signal: this.controller.signal});
      if (sequence !== this.sequence || !this.visible()) return;
      this.data = data; this.failed = false;
    } catch (error) {
      if (sequence !== this.sequence || error.name === "AbortError") return;
      this.failed = true;
    } finally {
      if (sequence === this.sequence && this.visible()) {
        this.loading = false; this.render();
        this.timer = setTimeout(() => this.refresh(), 10000);
      }
    }
  }
  record(row) {
    return `<button class="news-card" type="button" data-schedule-report="${e(row.id)}" data-kind="${e(row.kind)}">
      <span class="news-card-meta"><span>${e(row.kind_label)} · ${e(triggerLabels[row.trigger] || "自动生成")}</span><span>${e(status(row))}</span></span>
      <span class="news-card-title">${e(row.title)}</span>
      <span class="news-card-snippet">${e(time(row.period_start))} — ${e(time(row.period_end))}</span>
      ${row.phase ? `<span class="news-card-snippet">${e(row.progress?.stage ? progressText(row) : phaseLabels[row.phase])}</span>` : ""}
      ${row.error ? `<span class="news-card-snippet ${row.status === "skipped" ? "" : "error-state"}">${e(failureText(row))}</span>` : ""}
      <span class="news-card-footer"><span>${row.next_retry_at ? `重试 ${e(time(row.next_retry_at))} · 已尝试 ${e(row.attempts)} 次` : `更新 ${e(time(row.updated_at))}`}</span></span>
    </button>`;
  }
  render() {
    const host = this.host;
    if (!host) return;
    const focus = host.contains(document.activeElement) ? document.activeElement : null;
    const focusId = focus?.dataset.scheduleReport;
    const refreshFocused = focus?.hasAttribute("data-schedule-refresh");
    const data = this.data;
    host.innerHTML = `<section class="settings-section-card" aria-labelledby="scheduleStatusTitle">
      <div class="section-card-title"><span id="scheduleStatusTitle">运行状态</span><button class="reader-tool-btn" type="button" data-schedule-refresh aria-disabled="${!!this.loading}">${this.loading ? "刷新中…" : "刷新状态"}</button></div>
      ${this.failed ? `<p class="error-state" role="alert">${data ? "状态暂时无法更新，以下为上次结果。" : "任务状态读取失败，请检查服务连接及数据库后重试。"}</p>` : ""}
      ${data ? `<div class="form-group-grid">${field("排期服务", schedulerLabels[data.scheduler.status] || "状态未知")}${field("生成服务", data.worker_running ? "正常运行" : "未运行")}${field("最近检查", time(data.scheduler.last_checked_at))}${field("启用时间", time(data.activated_at))}</div>
      ${data.scheduler.error ? `<p class="error-state" role="alert">${e(errors[data.scheduler.error] || errors.report_schedule_failed)}</p>` : ""}
      ${["not_running","stopped"].includes(data.scheduler.status) || !data.worker_running ? '<p class="error-state">任务服务未运行，请重启应用后查看。</p>' : data.scheduler.status === "delayed" ? '<p class="error-state">排期检查超过 90 秒未更新，请稍后刷新；若持续出现，请重启应用。</p>' : ""}
      <div class="catchup-metrics" aria-label="报告任务队列">${[["running","生成中","warning"],["pending","等待中","warning"],["failed","失败","danger"]].map(([key,label,color]) => `<span data-status="${data.queue[key] ? color : ""}">${label} <strong data-schedule-count="${key}">${e(data.queue[key])}</strong></span>`).join("")}</div>
      ${data.retrying ? `<p class="muted">其中 ${e(data.retrying)} 份等待自动重试，时间见执行记录。</p>` : ""}
      ${data.current ? `<div class="field-item"><span class="field-label">当前任务</span><span>${e(data.current.title)} · ${e(data.current.progress?.stage ? progressText(data.current) : phaseLabels[data.current.phase] || "正在生成")}</span></div>` : ""}
      <p class="muted">应用运行时执行；恢复后补晚报、周报和月报。</p>
      <p class="muted" data-schedule-updated>更新于 ${e(time(data.observed_at))} · 本页每 10 秒刷新</p>` : this.failed ? "" : '<p role="status">正在读取任务状态…</p>'}
    </section>
    ${data ? `<section class="settings-section-card" aria-labelledby="scheduleRulesTitle">
      <div class="section-card-title"><span id="scheduleRulesTitle">报告排期 · 北京时间</span></div>
      ${data.calendar_warning ? '<p class="error-state" role="alert">交易日历未就绪，早报与午报暂缓生成，请更新交易日历。</p>' : ""}
      <div class="section-stack">${data.schedules.map(row => `<div class="field-item" data-schedule-kind="${e(row.kind)}"><strong>${e(row.kind_label)} · ${e(row.rule)}</strong><span>下次排期：${row.next_run_at ? e(time(row.next_run_at)) : "等待交易日历更新"}</span><span class="muted">${row.latest ? `最近一期：${e(time(row.latest.scheduled_at))} · ${e(status(row.latest))}` : "尚无自动生成记录"}</span></div>`).join("")}</div>
    </section>
    <section class="settings-section-card" aria-labelledby="scheduleHistoryTitle"><div class="section-card-title"><span id="scheduleHistoryTitle">最近执行</span><span class="muted">最近 20 条 · 点击查看报告</span></div>
      <div class="feed-date-cards">${data.recent.length ? data.recent.map(row => this.record(row)).join("") : '<p class="muted">尚无执行记录</p>'}</div>
    </section>` : ""}`;
    if (focusId) host.querySelector(`[data-schedule-report="${CSS.escape(focusId)}"]`)?.focus({preventScroll:true});
    else if (refreshFocused) host.querySelector("[data-schedule-refresh]")?.focus({preventScroll:true});
  }
}
