import { api } from "./ui.js";

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

// Shared classification poller: settings and the header consume the same snapshot.
export class ClassificationProgress {
  constructor({ onChange }) {
    this.onChange = onChange;
    document.addEventListener("visibilitychange", () => {
      clearTimeout(this.timer);
      if (!document.hidden) void this.refresh();
    });
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
        const working = this.progress?.enabled !== false && (this.progress?.batch?.pending || this.progress?.batch?.running);
        this.timer = setTimeout(() => this.refresh(), working ? 2000 : 30000);
      }
    }
  }
  render() {
    this.onChange?.({progress:this.progress, error:this.error, updatedAt:this.updatedAt});
  }
}
