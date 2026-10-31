import { api, escapeHTML as e, singlePane, formatBytes } from "./ui.js";
import { confirmAction } from "./confirm-dialog.js";
import { statusText, progressText } from "./report-status.js?v=reports-parallel-1";
import { defaultReportWindows, windowDays, windowLabel, windowError } from "./report-windows.js";

const time = value => value ? new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(value)) : "尚无记录";
const schedulerLabels = {running:"正常运行", starting:"正在启动", delayed:"检查已延迟", error:"排期检查失败", stopped:"已停止", not_running:"未运行"};
const phaseLabels = {indexing:"正在刷新索引、读取新闻", editing:"正在整理材料、生成报告"};
const errors = {
  report_calendar_failed:"交易日历读取失败，请检查 reports/trading-calendar.json 的文件权限与内容。",
  report_schedule_failed:"排期检查失败，请检查数据库是否可访问；系统会自动重试。",
};
const field = (label, value) => `<div class="field-item"><span class="field-label">${e(label)}</span><span>${e(value)}</span></div>`;
const status = row => row.status === "done" ? "已完成" : statusText(row);
const timePickers = (label, attributes) => `<div class="calendar-time">${[["hour",24,"小时","时"],["minute",60,"分钟","分"]].map(([part,count,suffix,unit]) => `<div class="field-item"><select class="field-select" ${attributes}="${part}" aria-label="${e(label+suffix)}">${Array.from({length:count},(_,i) => {const value=String(i).padStart(2,"0");return `<option value="${value}">${value} ${unit}</option>`;}).join("")}</select></div>`).join("")}</div>`;

// Polling never starts tasks or saves configuration. Editable controls remain
// mounted across refreshes; drafts belong to the shared settings save/reset flow.
export class ScheduleObserver {
  constructor({isVisible, getTimes, getWindows, onTimeChange, onWindowChange, onCancelEdit, selects}) {
    Object.assign(this, {isVisible, getTimes, getWindows, onTimeChange, onWindowChange, onCancelEdit, selects, sequence: 0, editing: false});
    document.addEventListener("visibilitychange", () => this.sync());
    singlePane.addEventListener("change", () => this.sync());
  }
  mount(host) {
    this.stop();
    this.host = host; this.data = null; this.failed = false;
    this.pauseError = "";
    this.storageMessage = ""; this.storageError = "";
    host.innerHTML = `<section class="settings-section-card" aria-labelledby="scheduleStatusTitle" data-schedule-status></section>
      <section class="settings-section-card" aria-labelledby="scheduleRulesTitle" data-schedule-rules hidden>
        <div class="section-card-title"><span id="scheduleRulesTitle">报告排期</span><div class="reader-action-toolbar"><button class="reader-tool-btn" type="button" data-schedule-pause aria-label="暂停定时" title="暂停定时"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path data-schedule-pause-icon d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg></button><button class="reader-tool-btn" type="button" data-schedule-edit aria-expanded="false">编辑</button></div></div>
        <p class="muted" role="status" data-schedule-paused hidden>已暂停自动生成，期间错过的期次不补生成。手动生成、已创建任务和快照清理照常。</p>
        <p class="error-state" role="alert" data-schedule-pause-error hidden></p>
        <p class="error-state" role="alert" data-schedule-calendar hidden>交易日历未就绪，早报与午报暂缓生成，请更新交易日历。</p>
        <div class="section-stack" data-schedule-list></div>
        <p class="muted" data-schedule-edit-note hidden>开始生成与新闻窗口独立设置。保存后用于后续期次，已有报告不变。</p>
      </section>
      <section class="settings-section-card" aria-labelledby="scheduleStorageTitle" data-schedule-storage hidden>
        <div class="section-card-title"><span id="scheduleStorageTitle">报告材料快照</span><span class="muted" data-snapshot-size></span></div>
        <p class="muted" data-snapshot-protected hidden></p>
        <div class="button-row"><button class="reader-action-btn danger" type="button" id="clearReportSnapshotsBtn" data-snapshot-clear>清除报告材料快照</button></div>
        <p class="muted" role="status" data-snapshot-result hidden></p>
        <p class="error-state" role="alert" data-snapshot-error hidden></p>
      </section>`;
    host.onclick = event => {
      if (event.target.closest("[data-schedule-pause]")) void this.changePause();
      if (event.target.closest("[data-snapshot-clear]")) void this.clearSnapshots();
      if (event.target.closest("[data-schedule-edit]")) {
        if (this.editing) this.onCancelEdit?.();
        this.setEditing(!this.editing);
      }
      if (event.target.closest("[data-schedule-refresh]") && !this.loading) void this.refresh();
    };
    host.onchange = event => {
      const input = event.target.closest("[data-schedule-part], [data-window-part], [data-window-day]");
      if (!input || !this.editing) return;
      const row = input.closest("[data-schedule-kind]"), kind = row.dataset.scheduleKind;
      if (input.matches("[data-schedule-part]")) {
        const value = [...row.querySelectorAll("[data-schedule-part]")].map(select => select.value).join(":");
        this.onTimeChange?.(kind, value);
      } else {
        const window = {...(this.getWindows?.()?.[kind] || defaultReportWindows[kind])};
        for (const boundary of ["start", "end"]) window[boundary] = [...row.querySelectorAll(`[data-window-boundary="${boundary}"] select[data-window-part]`)].map(select => select.value).join(":");
        if (row.querySelector("[data-window-day]")) window.start_day = Number(row.querySelector("[data-window-day]").value);
        this.onWindowChange?.(kind, window);
      }
      this.renderRules();
    };
    this.render();
  }
  setEditing(editing) {
    this.selects?.close(false);
    this.editing = editing;
    this.renderRules();
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
  async changePause() {
    if (this.pausePending || this.clearingSnapshots || !this.data?.database_token) return;
    const token = this.data.database_token, paused = !this.data.schedule_control?.paused;
    this.pausePending = true; this.pauseError = "";
    clearTimeout(this.timer); this.controller?.abort(); this.sequence++; this.loading = false;
    this.render();
    try {
      const data = await api("/api/reports/schedule-pause", {method:"POST", body:{paused, database_token:token}});
      if (this.data?.database_token === token) {
        this.data.schedule_control = data.schedule_control;
        for (const row of this.data.schedules) if (row.editable !== false) {
          row.paused = data.schedule_control.paused;
          if (row.paused) row.next_run_at = null;
        }
      }
    } catch (error) {
      if (this.data?.database_token === token) this.pauseError = (error.code || error.message) === "report_database_changed"
        ? "新闻库已切换，请刷新状态后重试。" : "未能确认定时操作结果，请刷新状态后重试。";
    } finally {
      this.pausePending = false; this.render();
      if (this.visible()) void this.refresh();
    }
  }
  async clearSnapshots() {
    if (this.clearingSnapshots || this.pausePending || !this.data?.snapshot_storage?.clearable_count) return;
    const token = this.data.database_token;
    this.clearingSnapshots = true; this.render();
    try {
      if (!await confirmAction({title:"清除报告材料快照", trigger:"#clearReportSnapshotsBtn", action:"清除快照",
        message:`清除 ${formatBytes(this.data.snapshot_storage.clearable_bytes)} 闲置快照？报告正文和来源保留，后续同时间范围的报告将重新取材。`})) return;
      if (this.data?.database_token !== token) return;
      clearTimeout(this.timer); this.controller?.abort(); this.sequence++; this.loading = false;
      this.storageError = ""; this.storageMessage = "";
      this.render();
      const data = await api("/api/reports/snapshots/clear", {method:"POST", body:{database_token:token}});
      if (this.data?.database_token === token) {
        this.data.snapshot_storage = data.snapshot_storage;
        this.storageMessage = `已清除 ${formatBytes(data.freed_bytes)}`;
      }
    } catch (error) {
      if (this.data?.database_token === token) {
        const code = error.code || error.message;
        this.storageError = code === "report_database_changed" ? "新闻库已切换，请刷新状态后重试。"
          : code === "report_materials_busy" ? "索引正在更新，请稍后清理。" : "未能确认清理结果，请刷新状态后重试。";
      }
    } finally {
      this.clearingSnapshots = false; this.render();
      if (this.visible()) void this.refresh();
    }
  }
  async refresh() {
    if (!this.visible() || this.pausePending || this.clearingSnapshots) return;
    clearTimeout(this.timer); this.controller?.abort();
    this.controller = new AbortController();
    const sequence = ++this.sequence;
    this.loading = true; this.render();
    try {
      const data = await api("/api/reports/schedule-status", {signal: this.controller.signal});
      if (sequence !== this.sequence || !this.visible()) return;
      if (this.data && this.data.database_token !== data.database_token) {
        this.storageError = ""; this.storageMessage = ""; this.pauseError = "";
      }
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
  renderStorage() {
    const host = this.host, storage = this.data?.snapshot_storage;
    host.querySelector("[data-schedule-storage]").hidden = !this.data;
    host.querySelector("[data-snapshot-size]").textContent = storage ? formatBytes(storage.bytes) : "大小暂不可用";
    const protectedSize = host.querySelector("[data-snapshot-protected]");
    protectedSize.textContent = storage?.protected_bytes ? `任务使用中 ${formatBytes(storage.protected_bytes)}` : "";
    protectedSize.hidden = !storage?.protected_bytes;
    const button = host.querySelector("[data-snapshot-clear]");
    button.disabled = !!this.clearingSnapshots || !!this.pausePending || !this.data?.database_token || !storage?.clearable_count;
    button.textContent = this.clearingSnapshots ? "正在清除…" : "清除报告材料快照";
    host.querySelector("[data-snapshot-result]").textContent = this.storageMessage;
    host.querySelector("[data-snapshot-result]").hidden = !this.storageMessage;
    host.querySelector("[data-snapshot-error]").textContent = this.storageError;
    host.querySelector("[data-snapshot-error]").hidden = !this.storageError;
  }
  renderRules() {
    if (!this.data) return;
    const host = this.host, list = host.querySelector("[data-schedule-list]");
    host.querySelector("[data-schedule-rules]").hidden = false;
    host.querySelector("[data-schedule-calendar]").hidden = !this.data.calendar_warning;
    const pause = host.querySelector("[data-schedule-pause]");
    const paused = !!this.data.schedule_control?.paused;
    pause.setAttribute("aria-label", this.pausePending ? "正在更改定时状态" : paused ? "恢复定时" : "暂停定时");
    pause.title = paused ? "恢复定时" : "暂停定时";
    pause.dataset.tone = paused ? "success" : "danger";
    pause.setAttribute("aria-busy", String(!!this.pausePending));
    pause.querySelector("path").setAttribute("d", paused ? "M6 3l15 9-15 9z" : "M6 4h4v16H6zM14 4h4v16h-4z");
    pause.disabled = !!this.pausePending || !!this.clearingSnapshots || !this.data.database_token;
    host.querySelector("[data-schedule-paused]").hidden = !this.data.schedule_control?.paused;
    const pauseError = host.querySelector("[data-schedule-pause-error]");
    pauseError.textContent = this.pauseError || ""; pauseError.hidden = !this.pauseError;
    const edit = host.querySelector("[data-schedule-edit]");
    edit.textContent = this.editing ? "取消编辑" : "编辑";
    edit.setAttribute("aria-expanded", String(this.editing));
    host.querySelector("[data-schedule-edit-note]").hidden = !this.editing;
    const rows = [...this.data.schedules].sort((a,b) => (a.next_run_at || "9999").localeCompare(b.next_run_at || "9999"));
    const focus = list.contains(document.activeElement) ? document.activeElement : null;
    for (const [index, row] of rows.entries()) {
      const editable = row.editable !== false;
      let node = list.querySelector(`[data-schedule-kind="${CSS.escape(row.kind)}"]`);
      if (!node) {
        node = document.createElement("div"); node.className = "field-item"; node.dataset.scheduleKind = row.kind;
        const days = editable ? windowDays(row.kind, defaultReportWindows[row.kind]) : [];
        node.innerHTML = `<strong data-schedule-rule></strong>
          ${editable ? `<div class="section-stack" data-schedule-inputs hidden>
            <div class="field-item"><span class="field-label">开始生成</span>${timePickers(row.kind_label,"data-schedule-part")}</div>
            <div class="form-group-grid">${["start","end"].map((boundary,i) => `<div class="field-item" data-window-boundary="${boundary}">
              <span class="field-label">新闻${i ? "终点" : "起点"}</span>
              ${i === 0 && "start_day" in defaultReportWindows[row.kind] ? `<select class="field-select" data-window-day aria-label="${e(row.kind_label)}新闻起点日期"><option value="-1">前日</option><option value="0">当日</option></select>` : `<span class="muted">${days[i]}</span>`}
              ${timePickers(row.kind_label+(i ? "新闻终点" : "新闻起点"),"data-window-part")}</div>`).join("")}</div>
          </div>` : ""}
          <span class="muted" data-schedule-window hidden></span><span data-schedule-next></span><span class="muted" data-schedule-latest></span>
          <span class="error-state" role="alert" data-schedule-error hidden></span>`;
      }
      const value = editable ? this.getTimes?.()?.[row.kind] || row.time || row.rule.slice(-5) : "";
      const frequency = row.frequency || row.rule.replace(/\s*\d\d:\d\d$/, "");
      node.querySelector("[data-schedule-rule]").textContent = `${row.kind_label} · ${frequency}${!this.editing && value ? ` ${value}` : ""}`;
      if (editable) node.querySelector("[data-schedule-inputs]").hidden = !this.editing;
      const window = editable ? this.getWindows?.()?.[row.kind] || row.window || defaultReportWindows[row.kind] : null;
      node.querySelectorAll("[data-schedule-part]").forEach((select,i) => {select.value = value.split(":")[i];});
      if (window) {
        for (const boundary of ["start", "end"]) node.querySelectorAll(`[data-window-boundary="${boundary}"] [data-window-part]`).forEach((select,i) => {select.value = window[boundary].split(":")[i];});
        const day = node.querySelector("[data-window-day]"); if (day) day.value = String(window.start_day);
      }
      node.querySelector("[data-schedule-window]").hidden = !window;
      node.querySelector("[data-schedule-window]").textContent = window ? `新闻窗口：${windowLabel(row.kind,window)}` : "";
      node.querySelector("[data-schedule-next]").textContent = row.paused ? "已暂停" : `下次排期：${row.next_run_at ? time(row.next_run_at) : "等待交易日历更新"}`;
      node.querySelector("[data-schedule-latest]").textContent = editable
        ? row.latest ? `最近一期：${time(row.latest.scheduled_at)} · ${status(row.latest)}` : "尚无自动生成记录"
        : row.last_run_at ? `上次清理：${time(row.last_run_at)} · ${row.removed_count} 份材料` : "清理 7 天前的闲置快照，保留报告正文与来源";
      const invalidWindow = window && windowError(window,value);
      node.querySelector("[data-schedule-error]").hidden = !row.error && !invalidWindow;
      node.querySelector("[data-schedule-error]").textContent = invalidWindow ? invalidWindow+"。"
        : row.error ? "清理失败，请检查数据库及磁盘空间；5 分钟后自动重试。" : "";
      // Ordinary polling changes text only. Keep an open Picker and its focus.
      if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);
    }
    this.selects?.refresh(list);
    if (focus && focus !== document.activeElement) focus.focus({preventScroll:true});
  }
  render() {
    const host = this.host;
    if (!host) return;
    const focus = host.contains(document.activeElement) ? document.activeElement : null;
    const refreshFocused = focus?.hasAttribute("data-schedule-refresh");
    const data = this.data;
    host.querySelector("[data-schedule-status]").innerHTML = `
      <div class="section-card-title"><span id="scheduleStatusTitle">运行状态</span><button class="reader-tool-btn" type="button" data-schedule-refresh aria-disabled="${!!this.loading}">${this.loading ? "刷新中…" : "刷新状态"}</button></div>
      ${this.failed ? `<p class="error-state" role="alert">${data ? "状态暂时无法更新，以下为上次结果。" : "任务状态读取失败，请检查服务连接及数据库后重试。"}</p>` : ""}
      ${data ? `<div class="form-group-grid">${field("排期服务", schedulerLabels[data.scheduler.status] || "状态未知")}${field("生成服务", data.worker_running ? `正常运行${data.concurrency ? ` · 最多并行 ${data.concurrency} 份` : ""}` : "未运行")}${field("最近检查", time(data.scheduler.last_checked_at))}${field("启用时间", time(data.activated_at))}</div>
      ${data.scheduler.error ? `<p class="error-state" role="alert">${e(errors[data.scheduler.error] || errors.report_schedule_failed)}</p>` : ""}
      ${["not_running","stopped"].includes(data.scheduler.status) || !data.worker_running ? '<p class="error-state">任务服务未运行，请重启应用后查看。</p>' : data.scheduler.status === "delayed" ? '<p class="error-state">排期检查超过 90 秒未更新，请稍后刷新；若持续出现，请重启应用。</p>' : ""}
      <div class="catchup-metrics" aria-label="报告任务队列">${[["running","生成中","warning"],["pending","等待中","warning"],["failed","失败","danger"]].map(([key,label,color]) => `<span data-status="${data.queue[key] ? color : ""}">${label} <strong data-schedule-count="${key}">${e(data.queue[key])}</strong></span>`).join("")}</div>
      ${data.retrying ? `<p class="muted">其中 ${e(data.retrying)} 份等待自动重试。</p>` : ""}
      ${(data.current_reports || (data.current ? [data.current] : [])).map(row=>`<div class="field-item"><span>${e(row.title)}${row.model_label ? ` · ${e(row.model_label)}` : ""} · ${e(row.progress?.stage ? progressText(row) : phaseLabels[row.phase] || "正在生成")}</span></div>`).join("")}
      <p class="muted">普通停机恢复后补晚报、周报和月报；主动暂停期间不补生成。</p>
      <p class="muted" data-schedule-updated>更新于 ${e(time(data.observed_at))} · 本页每 10 秒刷新</p>` : this.failed ? "" : '<p role="status">正在读取任务状态…</p>'}
    `;
    this.renderRules();
    this.renderStorage();
    if (refreshFocused) host.querySelector("[data-schedule-refresh]")?.focus({preventScroll:true});
  }
}
