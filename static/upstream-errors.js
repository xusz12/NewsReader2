import { DatePicker, dateKey, localDate } from "./date-picker.js";
import { $, api, escapeHTML as e, errorText, icon } from "./ui.js";

const sourceIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h2M10 14h8m-8 4h5M10 6h8v4h-8Z"/></svg>';

const recordsFrom = data => (data.days || []).flatMap(day => day.groups.flatMap(group =>
  (group.entries || (group.labels || []).map(label => ({ label: label.replace(/ error$/, "") })))
    .map(entry => ({ ...entry, date: day.date, time: group.time, error: entry.error || "未记录具体错误" }))
)).sort((a, b) => `${b.date} ${b.time}`.localeCompare(`${a.date} ${a.time}`))
  .map((record, index) => ({ ...record, index }));

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
  constructor() {
    this.panel = $("#upstreamErrorsPanel");
    this.button = $("#upstreamErrorsBtn");
    this.content = $("#upstreamErrorsContent");
    this.loadMessage = $("#upstreamLoadMessage");
    this.retryButton = $("[data-upstream-retry]", this.panel);
    this.retryButton.onclick = () => this.load(this.requestedDay);
    this.loadedDay = "";
    this.sequence = 0;
    this.dimension = "time";
    this.filter = "";
    this.today = dateKey(new Date());
    this.button.onclick = event => this.open ? this.close() : this.show(event.detail === 0);
    this.button.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") { event.preventDefault(); this.show(true); }
    });
    $("#closeUpstreamErrorsBtn").onclick = () => this.close(true);
    document.addEventListener("pointerdown", event => {
      if (this.open && !this.panel.contains(event.target) && !this.button.contains(event.target)) this.close();
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
      const dimension = event.target.closest("[data-upstream-dimension]");
      const filter = event.target.closest("[data-upstream-filter]");
      const copy = event.target.closest("[data-upstream-copy]");
      if (dimension) { this.dimension = dimension.dataset.upstreamDimension; this.filter = ""; this.render(); }
      else if (filter) { this.filter = filter.dataset.upstreamFilter; this.render(); }
      else if (copy) this.copyLog(copy);
    };
    const sync = () => { if (!document.hidden) this.refreshBadge(); };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    setInterval(sync, 60000);
    sync();
  }

  get open() { return !this.panel.hidden; }

  show(keyboard = false) {
    document.dispatchEvent(new CustomEvent("topbar:popover-open", { detail: this.panel.id }));
    this.panel.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.position(); this.showToday();
    if (keyboard) $("#upstreamErrorDate").focus({ preventScroll: true });
  }

  close(restoreFocus = false) {
    if (!this.open) return;
    this.datePicker.close();
    this.panel.hidden = true;
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
    this.panel.style.height = `${Math.min(760, available)}px`;
    this.panel.style.maxHeight = `${available}px`;
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
      button.textContent = offset === 0 ? `今天 (${shortDate})` : offset === -1 ? `昨天 (${shortDate})` : shortDate;
      button.setAttribute("aria-pressed", String(selected === day));
      button.setAttribute("aria-label", `${offset === 0 ? "今天" : offset === -1 ? "昨天" : "前天"}，${day}`);
    });
    $("#upstreamNextDayBtn").disabled = selected >= this.today;
  }

  async refreshBadge() {
    if (this.badgeRequest) return this.badgeRequest;
    this.badgeRequest = (async () => {
      try {
        // The top bar counts today's recorded errors, independent of the panel's date/filter.
        const data = await api("/api/error-stats?details=0");
        const count = recordsFrom(data).length;
        const badge = $("#upstreamErrorBadge");
        $("#upstreamErrorSummary").hidden = count === 0;
        badge.textContent = count > 99 ? "99+" : count ? String(count) : "";
        this.today = data.today || data.day || this.today;
        this.updateDateControls();
        this.button.classList.toggle("has-errors", count > 0);
        this.button.classList.toggle("no-errors", count === 0);
        this.button.title = count ? `当日 ${count} 条上游报错记录 · 点击查看` : "当日未记录上游报错 · 点击查看历史记录";
        this.button.setAttribute("aria-label", `上游数据报错集合：${this.button.title}`);
      } catch {
        $("#upstreamErrorSummary").hidden = false;
        $("#upstreamErrorBadge").textContent = "?";
        this.button.classList.remove("has-errors", "no-errors");
        this.button.title = "上游报错统计暂不可用 · 点击重试";
        this.button.setAttribute("aria-label", this.button.title);
      }
    })();
    try { await this.badgeRequest; } finally { this.badgeRequest = null; }
  }

  setLoadStatus(message, failed = false) {
    this.loadMessage.textContent = message;
    this.loadMessage.parentElement.classList.toggle("is-error", failed);
    this.retryButton.hidden = !failed;
  }

  async load(day = $("#upstreamErrorDate").value) {
    const sequence = ++this.sequence;
    const dateRevision = this.datePicker.inputRevision;
    this.requestedDay = day;
    const date = $("#upstreamErrorDate");
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
      this.stats = data.stats || {};
      this.isToday = !data.today || data.day === data.today;
      this.filter = "";
      this.render();
      if (this.loadedDay !== data.day) this.content.scrollTop = 0;
      this.loadedDay = data.day;
      this.content.setAttribute("aria-label", `${data.day} 的上游报错记录`);
      this.setLoadStatus("");
    } catch (error) {
      if (sequence === this.sequence && this.open) {
        this.setLoadStatus(`${errorText(error)}${this.loadedDay ? `；仍显示 ${this.loadedDay}` : ""}`, true);
        if (!this.loadedDay) this.content.innerHTML = '<div class="upstream-empty">暂时无法显示上游报错，请重新读取。</div>';
      }
    } finally {
      if (sequence === this.sequence) this.content.setAttribute("aria-busy", "false");
    }
  }

  render() {
    const focused = document.activeElement?.dataset;
    const focusKey = focused?.upstreamDimension !== undefined ? "upstreamDimension" : "upstreamFilter";
    const focusValue = focused?.[focusKey];
    const records = this.records;
    const { availability, available_sources, total_sources, ingested_count } = this.stats;
    const availabilityTitle = Number.isFinite(availability)
      ? `可用来源 ${available_sources} / ${total_sources}；无更新计为可用，发生过报错的来源计为异常`
      : "当天暂无来源抓取记录";
    const groups = new Map();
    records.forEach(record => {
      const key = this.dimension === "time" ? record.time : record.label;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(record);
    });
    const visible = this.filter ? groups.get(this.filter) || [] : records;
    const groupIcon = this.dimension === "time" ? icon("read_later") : sourceIcon;
    const chips = [["", this.dimension === "time" ? "全部时间" : "全部来源", records.length],
      ...[...groups].map(([key, items]) => [key, key, items.length])];
    const html = `
      <div class="upstream-health-strip">
        <div title="${e(availabilityTitle)}"><span class="upstream-stat-label">当日抓取可用率</span><strong class="upstream-stat-value ${Number.isFinite(availability) ? (availability < 100 ? "warning" : "success") : ""}">${Number.isFinite(availability) ? `${availability}%` : "—"}</strong></div>
        <div><span class="upstream-stat-label">异常来源</span><strong class="upstream-stat-value">${new Set(records.map(row => row.label)).size} 个</strong></div>
        <div title="所选日期采集文件中实际已入库的资讯，包含其他日期发布的新闻"><span class="upstream-stat-label">${this.isToday ? "今日" : "当日"}已入库资讯</span><strong class="upstream-stat-value">${Number.isFinite(ingested_count) ? `${ingested_count} 条` : "—"}</strong></div>
        <div><span class="upstream-stat-label">当日报错</span><strong class="upstream-stat-value ${records.length ? "warning" : "success"}">${records.length} 条</strong></div>
      </div>
      <div class="upstream-classifier-bar">
        <div class="upstream-dim-row">
          <div class="upstream-dim-switcher" role="group" aria-label="报错分类方式">
            <button class="upstream-dim-btn" type="button" data-upstream-dimension="time" aria-pressed="${this.dimension === "time"}">${icon("read_later")}按报错时间分类</button>
            <button class="upstream-dim-btn" type="button" data-upstream-dimension="source" aria-pressed="${this.dimension === "source"}">${sourceIcon}按信源来源分类</button>
          </div>
          <span class="upstream-showing-count">当前展示 ${visible.length} / ${records.length} 条报错</span>
        </div>
        <div class="upstream-filter-chips" role="group" aria-label="筛选报错记录">${chips.map(([key, label, count]) => `<button class="upstream-chip-btn" type="button" data-upstream-filter="${e(key)}" aria-pressed="${this.filter === key}">${e(label)} <span>(${count})</span></button>`).join("")}</div>
      </div>
      <div class="upstream-errors-list">${records.length ? [...groups].filter(([key]) => !this.filter || key === this.filter).map(([key, items]) => `
        <section class="upstream-error-group">
          <h3 class="upstream-group-header"><span>${groupIcon}${e(key)}</span><span>${items.length} 条报错</span></h3>
          ${items.map(record => this.card(record)).join("")}
        </section>`).join("") : '<div class="upstream-empty"><strong>当天没有记录上游报错。</strong><p>可切换日期查看历史记录。</p></div>'}</div>`;
    // Commit the new view in one paint; unchanged responses keep their DOM and scroll position.
    if (html !== this.renderedHTML) {
      this.content.innerHTML = html;
      this.renderedHTML = html;
      if (focusValue !== undefined)
        [...this.content.querySelectorAll("button")].find(button => button.dataset[focusKey] === focusValue)?.focus({ preventScroll: true });
    }
  }

  card(record) {
    return `<article class="upstream-error-card">
      <div class="upstream-card-head">
        <div class="upstream-source-title"><span class="upstream-source-icon" aria-hidden="true">${e([...record.label][0] || "!")}</span><strong>${e(record.label)}</strong></div>
        <span class="upstream-error-tag">${e(errorBadge(record.error))}</span>
        <time datetime="${e(record.date)}T${e(record.time)}">${e(record.time)}</time>
      </div>
      <div class="upstream-log-box">
        <div class="upstream-log-head"><span>原始报错日志 (Raw Log)</span><button class="upstream-log-copy-btn" type="button" data-upstream-copy="${record.index}" aria-label="复制 ${e(record.label)} 的报错日志">复制日志</button></div>
        <pre>${e(record.error)}</pre>
        <span class="upstream-copy-feedback" role="status"></span>
      </div>
    </article>`;
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
