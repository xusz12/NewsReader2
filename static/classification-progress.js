import { $, api, busy } from "./ui.js";

export function classificationStatus(progress) {
  if (!progress) return "正在读取分类进度…";
  const p = progress, throttle = p.throttle;
  if (p.enabled === false) return p.running ? "自动分类已关闭，正在处理的请求会继续完成" : "自动分类已关闭，保留现有进度";
  if (!p.total) return "当前范围暂无新闻";
  if (!p.pending && !p.running && !p.failed && !p.error) return "当前范围已全部分类完成";
  if (!p.configured) return "等待在 API 管理中配置 Jev";
  if (p.error) return "分类已暂停，请检查 API 管理中的 Jev 配置后重试";
  if (throttle?.rate_limited && (p.pending || p.running)) return `限流降速 · 并发 ${throttle.effective_concurrency}/${throttle.configured_concurrency}${throttle.cooldown_seconds ? ` · ${throttle.cooldown_seconds} 秒后继续` : ""}`;
  return p.pending || p.running ? "正在分类" : "有失败项待重试";
}

// A determinate status indicator: only server counts advance the ring.
export class ClassificationProgress {
  constructor({ onSettings, onChange }) {
    this.root = $("#classificationProgress");
    this.indicator = $("#classificationProgressIndicator");
    this.panel = $("#classificationProgressPanel");
    this.onSettings = onSettings;
    this.onChange = onChange;
    this.root.addEventListener("pointerenter", event => {
      if (event.pointerType !== "touch") this.open();
    });
    this.root.addEventListener("pointerleave", () => {
      this.leaveTimer = setTimeout(() => {
        if (!this.root.contains(document.activeElement)) this.close();
      }, 150);
    });
    this.root.addEventListener("focusin", () => { if (!this.restoringFocus) this.open(); });
    this.root.addEventListener("focusout", event => {
      if (!this.root.contains(event.relatedTarget)) this.close();
    });
    this.indicator.addEventListener("pointerdown", event => {
      if (event.pointerType === "touch") {
        event.preventDefault();
        this.open();
      }
    });
    document.addEventListener("pointerdown", event => {
      if (!this.root.contains(event.target)) this.close();
    });
    document.addEventListener("topbar:popover-open", event => {
      if (event.detail !== this.panel.id) this.close();
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape" || this.panel.hidden) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const focused = this.panel.contains(document.activeElement);
      this.close();
      if (focused) {
        this.restoringFocus = true;
        this.indicator.focus({ preventScroll: true });
        this.restoringFocus = false;
      }
    }, true);
    $("#classificationProgressSettings").onclick = () => {
      this.close();
      return busy(null, this.onSettings);
    };
    $("#classificationProgressRetry").onclick = event => busy(event.currentTarget, async () => {
      await api("/api/news/classification/retry", { method: "POST" });
      await this.refresh();
    });
    document.addEventListener("visibilitychange", () => {
      clearTimeout(this.timer);
      if (document.hidden) this.close();
      else void this.refresh();
    });
  }
  open() {
    clearTimeout(this.leaveTimer);
    if (this.root.hidden || !this.panel.hidden) return;
    document.dispatchEvent(new CustomEvent("topbar:popover-open", { detail: this.panel.id }));
    this.panel.hidden = false;
    void this.refresh();
  }
  close() {
    clearTimeout(this.leaveTimer);
    this.panel.hidden = true;
  }
  async refresh() {
    if (document.hidden) return;
    if (this.refreshing) { this.refreshAgain = true; return; }
    clearTimeout(this.timer);
    this.refreshing = true;
    try {
      const progress = await api("/api/news/classification");
      // A settings change or index refresh may supersede an in-flight snapshot.
      if (!this.refreshAgain) { this.progress = progress; this.error = ""; this.updatedAt = new Date(); this.render(); }
    } catch {
      this.error = this.progress ? "进度暂时无法更新，显示上次结果" : "暂时无法读取分类进度，请重试";
      this.render();
    } finally {
      this.refreshing = false;
      if (this.refreshAgain) { this.refreshAgain = false; void this.refresh(); }
      else if (!document.hidden) {
        const working = this.progress?.enabled !== false && (this.progress?.pending || this.progress?.running);
        this.timer = setTimeout(() => this.refresh(), working ? 2000 : 30000);
      }
    }
  }
  render() {
    const p = this.progress;
    this.onChange?.({ progress: p, error: this.error, updatedAt: this.updatedAt });
    this.root.hidden = !p || p.enabled === false || !p.total || !(p.pending || p.running || p.failed);
    if (this.root.hidden) { this.close(); return; }
    const done = Math.min(p.total, Math.max(0, p.done));
    const percent = done / p.total * 100;
    this.indicator.setAttribute("aria-valuemax", String(p.total));
    this.indicator.setAttribute("aria-valuenow", String(done));
    this.indicator.setAttribute("aria-valuetext", `已分类 ${done}/${p.total} 条，${p.unread_only ? "仅未读新闻" : "全部新闻"}`);
    $("#classificationProgressArc").style.strokeDashoffset = String(100 - percent);
    $("#classificationProgressArc").style.opacity = done ? "1" : "0";
    $("#classificationProgressCount").textContent = `${done.toLocaleString()}/${p.total.toLocaleString()} 条`;
    $("#classificationProgressScope").textContent = `当前模式：${p.unread_only ? "仅未读" : "全部"}`;
    $("#classificationProgressDetails").textContent = `待处理 ${p.pending} · 处理中 ${p.running} · 失败 ${p.failed}`;
    $("#classificationProgressStatus").textContent = this.error || classificationStatus(p);
    $("#classificationProgressRetry").hidden = !p.configured || !(p.failed || p.error);
    $("#classificationProgressSettings").hidden = p.configured && !p.error;
  }
}
