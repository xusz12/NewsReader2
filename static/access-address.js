import { $, api, copyText } from "./ui.js";

const addressLifetime = 30_000;

export class AccessAddress {
  constructor() {
    this.observer = new IntersectionObserver(([entry]) => {
      if (entry.target !== this.host) return;
      this.inView = entry.isIntersecting;
      this.syncVisibility();
    });
    document.addEventListener("visibilitychange", () => this.syncVisibility());
    for (const event of ["online", "offline"])
      window.addEventListener(event, () => { if (this.active) void this.refresh(); });
  }

  mount(host) {
    this.stop();
    this.observer.disconnect();
    this.inView = false;
    this.host = host;
    this.result = $("[data-access-result]", host);
    this.label = $("[data-access-label]", host);
    this.url = $("[data-access-url]", host);
    this.feedback = $("[data-access-feedback]", host);
    this.refreshButton = $("[data-access-refresh]", host);
    this.copyButton = $("[data-access-copy]", host);
    this.refreshButton.onclick = () => this.refresh();
    this.copyButton.onclick = () => this.copy();
    this.observer.observe(host);
  }

  sync(visible, revisit = false) {
    this.visible = visible;
    this.syncVisibility(revisit);
  }

  syncVisibility(revisit = false) {
    if (!this.visible || !this.inView || document.hidden) {
      if (this.active) this.stop();
      return;
    }
    if (!this.active || revisit) {
      this.active = true;
      void this.refresh();
    }
  }

  status(message, tone = "") {
    this.feedback.textContent = message;
    this.feedback.dataset.status = tone;
  }

  clear(message, tone) {
    clearTimeout(this.expiry);
    this.address = null;
    this.validUntil = 0;
    this.result.hidden = true;
    this.url.textContent = "";
    this.status(message, tone);
  }

  stop() {
    this.active = false;
    this.pending?.abort();
    this.pending = null;
    if (!this.host) return;
    this.refreshButton.disabled = false;
    this.clear("尚未检测。");
  }

  async refresh() {
    if (!this.active) return;
    this.pending?.abort();
    const request = new AbortController();
    this.pending = request;
    if (document.activeElement === this.copyButton) this.host.focus({preventScroll:true});
    this.refreshButton.disabled = true;
    this.clear("正在检测访问地址…", "running");
    try {
      const { address } = await api("/api/settings/access-address", {signal:request.signal});
      if (this.pending !== request) return;
      this.expiry = setTimeout(() => this.refresh(), addressLifetime);
      if (!address?.url) {
        this.status("当前只检测到本机入口。请在运行 NR 的 Mac 上登录 Tailscale，在尾网启用 HTTPS 证书后重新启动 NR。");
        return;
      }
      this.address = address;
      this.validUntil = Date.now() + addressLifetime;
      this.label.textContent = address.kind === "serve" ? "其他设备访问地址（私有 HTTPS）" : "其他设备阅读地址（HTTP）";
      this.url.textContent = address.url;
      this.result.hidden = false;
      this.status(address.kind === "serve" ? "其他设备连接同一 Tailscale 后可打开。"
        : "此地址可用于阅读。手机通知需要私有 HTTPS。请在 Mac 上连接独立 Tailscale 客户端，在尾网启用 HTTPS 证书后重新启动 NR。");
    } catch {
      if (this.pending === request) this.status("检测失败，请重新检测。", "failed");
    } finally {
      if (this.pending === request) {
        this.pending = null;
        this.refreshButton.disabled = false;
      }
    }
  }

  async copy() {
    const address = this.address;
    if (!address) return;
    if (Date.now() >= this.validUntil) return void this.refresh();
    const copied = await copyText(address.url);
    if (this.address === address)
      this.status(copied ? "已复制访问地址。" : "复制失败，可选中网址手动复制。", copied ? "success" : "failed");
  }
}
