import { $, $$, api, errorText, escapeHTML as e } from "./ui.js";

const kinds = {
  daily_report: "日报完成",
  collection_error: "采集失败待处理",
  personal_reply: "个人助手回复",
  article_reply: "文章问答回复",
};
const deviceURL = "/api/browser-push/device";

function browserSupport() {
  const loopback = location.protocol === "http:" && location.hostname === "127.0.0.1";
  if (!window.isSecureContext || (location.protocol !== "https:" && !loopback))
    return "请通过 NR 的私有 HTTPS 地址或 Mac 本机开发入口使用浏览器通知。";
  const appleMobile = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (appleMobile && !navigator.standalone && !matchMedia("(display-mode: standalone)").matches)
    return "请先将 NR 添加到主屏幕，再从图标打开并启用通知。";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window))
    return "当前浏览器不支持系统通知。";
  return "";
}

export function notificationTarget(value) {
  let url;
  try { url = new URL(value, location.origin); } catch { return null; }
  if (url.origin !== location.origin || url.pathname !== "/") return null;
  const kind = url.searchParams.get("nr_push"), id = url.searchParams.get("id");
  if (!Object.hasOwn(kinds, kind) || !/^[a-f0-9-]{32,36}$/.test(id || "")) return null;
  const day = url.searchParams.get("day"), messageId = Number(url.searchParams.get("message"));
  if (kind === "collection_error" && !/^\d{4}-\d\d-\d\d$/.test(day || "")) return null;
  if (kind === "personal_reply" && (!Number.isSafeInteger(messageId) || messageId < 1)) return null;
  return { kind, id, day, messageId };
}

class BrowserPush {
  constructor() {
    this.types = Object.keys(kinds);
    this.reported = new Set();
  }
  mount(host) {
    this.host = host;
    host.addEventListener("click", event => {
      if (event.target.closest("[data-push-toggle]")) void this.toggle();
      if (event.target.closest("[data-push-refresh]")) void this.load();
    });
    host.addEventListener("change", event => {
      if (event.target.matches("[data-push-kind]")) void this.saveTypes();
    });
    this.render();
  }
  render() {
    if (!this.host) return;
    const unavailable = browserSupport();
    const denied = !unavailable && Notification.permission === "denied";
    const status = this.error || unavailable || (denied ? "通知权限已关闭，请在浏览器或系统设置中恢复。" :
      this.busy ? "正在更新通知设置…" : this.loading ? "正在读取本浏览器的通知设置…" :
      this.registered ? "本浏览器已订阅。" : this.config?.available === false ? "此地址未启用浏览器通知。" : "本浏览器尚未订阅。");
    this.host.innerHTML = `<div class="section-card-title"><span>浏览器通知</span></div>
      <p class="muted" role="status">${e(status)}</p>
      ${this.registered ? `<fieldset class="field-options" aria-label="本浏览器接收的通知" ${this.busy ? "disabled" : ""}>
        ${Object.entries(kinds).map(([kind, label]) => `<label class="settings-toggle settings-switch"><span>${label}</span><input type="checkbox" role="switch" data-push-kind="${kind}" ${this.types.includes(kind) ? "checked" : ""}></label>`).join("")}
      </fieldset>` : ""}
      <div class="button-row">${this.registration && (this.registered || !denied) ?
        `<button class="${this.registered ? "btn-secondary" : "btn-primary"}" type="button" data-push-toggle ${this.busy || this.loading ? "disabled" : ""}>${this.registered ? "取消本浏览器订阅" : "启用本浏览器通知"}</button>` : ""}
      ${this.error && !unavailable ? '<button type="button" class="btn-secondary" data-push-refresh>重新读取</button>' : ""}</div>`;
  }
  async load() {
    if (this.loading || this.busy || browserSupport()) return;
    this.loading = true;
    this.error = "";
    this.render();
    try {
      this.config = await api("/api/browser-push/config");
      if (!this.config.available) { this.registration = null; this.registered = false; return; }
      await navigator.serviceWorker.register("/push-sw.js", { scope: "/" });
      this.registration = await navigator.serviceWorker.ready;
      this.subscription = await this.registration.pushManager.getSubscription();
      const result = this.subscription ? await this.device("POST") : {registered:false};
      this.registered = result.registered;
      this.stale = !!this.subscription && !this.registered;
      this.types = result.registered ? result.types : Object.keys(kinds);
    } catch (error) { this.error = errorText(error); }
    finally { this.loading = false; this.render(); this.scheduleSeen(); }
  }
  device(method, types) {
    return api(deviceURL, {method, body:{subscription:this.subscription.toJSON(), ...(types === undefined ? {} : {types})}});
  }
  async toggle() {
    if (this.busy || !this.registration) return;
    const restore = this.host.contains(document.activeElement);
    this.busy = true;
    this.error = "";
    this.render();
    try {
      if (this.registered) {
        await this.device("DELETE");
        this.registered = false;
        this.reported.clear();
        await this.subscription.unsubscribe();
        this.subscription = null;
        this.types = Object.keys(kinds);
      } else {
        const key = this.config.public_key.replace(/-/g, "+").replace(/_/g, "/");
        const appKey = Uint8Array.from(atob(key + "=".repeat((4 - key.length % 4) % 4)), value => value.charCodeAt(0));
        if (this.subscription && this.stale) {
          await this.subscription.unsubscribe();
          this.subscription = null;
        }
        if (!this.subscription) this.subscription = await this.registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:appKey});
        this.stale = false;
        const result = await this.device("PUT", this.types);
        this.registered = result.registered;
        this.types = result.types;
      }
    } catch (error) {
      if (Notification.permission !== "denied") this.error = errorText(error);
    } finally {
      this.busy = false;
      this.render();
      if (restore && document.activeElement === document.body) $("[data-push-toggle]", this.host)?.focus({preventScroll:true});
      this.scheduleSeen();
    }
  }
  async saveTypes() {
    if (this.busy || !this.registered) return;
    const focused = document.activeElement?.dataset.pushKind;
    const types = $$("[data-push-kind]:checked", this.host).map(node => node.dataset.pushKind);
    this.busy = true;
    this.error = "";
    this.render();
    try { this.types = (await this.device("PUT", types)).types; }
    catch (error) { this.error = errorText(error); }
    finally {
      this.busy = false;
      this.render();
      if (focused && document.activeElement === document.body)
        $(`[data-push-kind="${CSS.escape(focused)}"]`, this.host)?.focus({preventScroll:true});
    }
  }
  start({open, canOpen, viewedReplies}) {
    this.viewedReplies = viewedReplies;
    const destination = notificationTarget(location.href);
    if (destination) {
      history.replaceState(history.state, "", "/");
      void open(destination);
    }
    if (browserSupport()) return;
    navigator.serviceWorker.addEventListener("message", event => {
      if (event.data?.type !== "nr:push-open" || !event.ports[0]) return;
      const target = notificationTarget(event.data.url);
      const accepted = !!target && canOpen();
      event.ports[0].postMessage(accepted);
      if (accepted) void open(target);
    });
    for (const event of ["agent:rendered", "personal-assistant:rendered", "visibilitychange"])
      document.addEventListener(event, () => this.scheduleSeen());
    window.addEventListener("focus", () => this.scheduleSeen());
    for (const host of [$("#personalAssistantMessages"), $("#agentChatStream")])
      host?.addEventListener("scroll", () => this.scheduleSeen(), {passive:true});
    void navigator.serviceWorker.getRegistration("/").then(async registration => {
      const subscription = await registration?.pushManager.getSubscription();
      if (subscription && this.subscription === undefined) this.subscription = subscription;
      this.scheduleSeen();
    }).catch(() => {});
  }
  scheduleSeen() {
    if (this.seenFrame || this.sendingSeen || !this.subscription || !this.viewedReplies || document.hidden || !document.hasFocus()) return;
    this.seenFrame = requestAnimationFrame(() => { this.seenFrame = 0; void this.confirmSeen(); });
  }
  async confirmSeen() {
    const replies = this.viewedReplies().filter(({kind, id}) => !this.reported.has(`${kind}:${id}`)).slice(0, 12);
    if (!replies.length || !this.subscription) return;
    this.sendingSeen = true;
    try {
      await api("/api/browser-push/seen", {method:"POST", body:{subscription:this.subscription.toJSON(), replies}});
      if (this.reported.size > 256) this.reported.clear();
      for (const {kind, id} of replies) this.reported.add(`${kind}:${id}`);
    } catch {}
    finally { this.sendingSeen = false; }
  }
}

export const browserPush = new BrowserPush();
