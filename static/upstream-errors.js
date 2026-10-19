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

export class UpstreamErrors {
  constructor() {
    this.dialog = $("#upstreamErrorsDialog");
    this.button = $("#upstreamErrorsBtn");
    this.content = $("#upstreamErrorsContent");
    this.sequence = 0;
    this.dimension = "time";
    this.filter = "";
    this.button.onclick = () => {
      this.dialog.showModal();
      this.button.setAttribute("aria-expanded", "true");
      this.showToday();
    };
    $("#closeUpstreamErrorsBtn").onclick = () => this.dialog.close();
    $("#upstreamErrorDate").onchange = () => this.load();
    $("#upstreamTodayBtn").onclick = () => this.showToday();
    this.dialog.addEventListener("close", () => {
      ++this.sequence;
      this.button.setAttribute("aria-expanded", "false");
    });
    this.dialog.addEventListener("click", event => {
      if (event.target === this.dialog) {
        const box = this.dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) this.dialog.close();
      }
    });
    this.content.onclick = event => {
      const dimension = event.target.closest("[data-upstream-dimension]");
      const filter = event.target.closest("[data-upstream-filter]");
      const copy = event.target.closest("[data-upstream-copy]");
      if (dimension) { this.dimension = dimension.dataset.upstreamDimension; this.filter = ""; this.render(); }
      else if (filter) { this.filter = filter.dataset.upstreamFilter; this.render(); }
      else if (copy) this.copyLog(copy);
      else if (event.target.closest("[data-upstream-retry]")) this.load();
    };
    const sync = () => { if (!document.hidden) this.refreshBadge(); };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    setInterval(sync, 60000);
    sync();
  }

  showToday() {
    // Omit the day so the server resolves today's date afresh, including after midnight.
    $("#upstreamErrorDate").value = "";
    this.refreshBadge();
    this.load();
  }

  async refreshBadge() {
    if (this.badgeRequest) return this.badgeRequest;
    this.badgeRequest = (async () => {
      try {
        // The top bar counts today's recorded errors, independent of the dialog's date/filter.
        const data = await api("/api/error-stats?details=0");
        const count = recordsFrom(data).length;
        const badge = $("#upstreamErrorBadge");
        badge.hidden = count === 0;
        badge.textContent = count > 99 ? "99+" : count ? String(count) : "";
        this.button.classList.toggle("has-errors", count > 0);
        this.button.classList.toggle("no-errors", count === 0);
        this.button.title = count ? `当日 ${count} 条上游报错记录 · 点击查看` : "当日未记录上游报错 · 点击查看历史记录";
        this.button.setAttribute("aria-label", `上游数据报错集合：${this.button.title}`);
      } catch {
        $("#upstreamErrorBadge").hidden = false;
        $("#upstreamErrorBadge").textContent = "?";
        this.button.classList.remove("has-errors", "no-errors");
        this.button.title = "上游报错统计暂不可用 · 点击重试";
        this.button.setAttribute("aria-label", this.button.title);
      }
    })();
    try { await this.badgeRequest; } finally { this.badgeRequest = null; }
  }

  async load() {
    const sequence = ++this.sequence;
    const date = $("#upstreamErrorDate");
    this.content.setAttribute("aria-busy", "true");
    this.content.innerHTML = '<p class="upstream-empty">正在读取上游报错…</p>';
    try {
      const params = new URLSearchParams({ details: "1" });
      if (date.value) params.set("day", date.value);
      const data = await api(`/api/error-stats?${params}`);
      if (sequence !== this.sequence || !this.dialog.open) return;
      date.value = data.day;
      this.records = recordsFrom(data);
      this.filter = "";
      this.render();
    } catch (error) {
      if (sequence === this.sequence && this.dialog.open)
        this.content.innerHTML = `<div class="upstream-empty" role="status"><p>${e(errorText(error))}</p><button class="btn-secondary" type="button" data-upstream-retry>重新读取</button></div>`;
    } finally {
      if (sequence === this.sequence) this.content.setAttribute("aria-busy", "false");
    }
  }

  render() {
    const focused = document.activeElement?.dataset;
    const focusKey = focused?.upstreamDimension !== undefined ? "upstreamDimension" : "upstreamFilter";
    const focusValue = focused?.[focusKey];
    const records = this.records;
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
    this.content.innerHTML = `
      <div class="upstream-health-strip">
        <div><span class="upstream-stat-label">当日报错</span><strong class="upstream-stat-value ${records.length ? "warning" : "success"}">${records.length} 条</strong></div>
        <div><span class="upstream-stat-label">异常来源</span><strong class="upstream-stat-value">${new Set(records.map(row => row.label)).size} 个</strong></div>
        <div><span class="upstream-stat-label">报错批次</span><strong class="upstream-stat-value">${new Set(records.map(row => row.time)).size} 次</strong></div>
        <div><span class="upstream-stat-label">最近记录</span><strong class="upstream-stat-value">${e(records[0]?.time || "—")}</strong></div>
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
    if (focusValue !== undefined)
      [...this.content.querySelectorAll("button")].find(button => button.dataset[focusKey] === focusValue)?.focus({ preventScroll: true });
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
