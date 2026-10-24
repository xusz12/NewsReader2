import { $, api, escapeHTML as e, errorText, busy } from "./ui.js";

const terminal = ["succeeded", "failed", "interrupted"];

// Checks the clipped, painted reply rather than a successful fetch or an open panel.
export function replyIsVisible(node) {
  if (!node || document.hidden || !document.hasFocus()) return false;
  // Container padding is not evidence that any of the answer was painted.
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
    acceptNode(text) {
      return text.textContent.trim() && !text.parentElement.closest('button,svg,[aria-hidden="true"]')
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    const range = document.createRange();
    range.selectNodeContents(text);
    for (const rect of range.getClientRects()) {
      let left = Math.max(0, rect.left), top = Math.max(0, rect.top);
      let right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
      let hidden = false;
      for (let parent = text.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if (style.visibility !== "visible" || style.display === "none" || Number(style.opacity) === 0 || parent.inert) { hidden = true; break; }
        const box = parent.getBoundingClientRect();
        if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
          left = Math.max(left, box.left); right = Math.min(right, box.right);
        }
        if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
          top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
        }
      }
      if (hidden || right - left < Math.min(8, rect.width) || bottom - top < Math.min(12, rect.height) || rect.width <= 0 || rect.height <= 0) continue;
      const painted = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
      if (painted && node.contains(painted)) return true;
    }
  }
  return false;
}

export class AgentNotifications {
  constructor({ agent, onOpen, contextVersion, readerVisible }) {
    Object.assign(this, { agent, onOpen, contextVersion, readerVisible });
    this.rows = [];
    this.scope = "";
    this.navigation = 0;
    this.acknowledging = new Set();
    this.failedReads = new Map();
    this.button = $("#agentNotificationsBtn");
    this.panel = $("#agentNotificationsPanel");
    this.button.onclick = () => this.toggle();
    document.addEventListener("topbar:popover-open", event => { if (event.detail !== this.panel.id) this.close(); });
    $("#agentNotificationsReadAll").onclick = () => this.markAll();
    this.panel.addEventListener("click", event => {
      const dismiss = event.target.closest("[data-dismiss-notice]");
      const open = event.target.closest("[data-open-notice]");
      if (dismiss) busy(dismiss, () => this.confirm(JSON.parse(dismiss.dataset.dismissNotice), true));
      else if (open) void this.openNotice(open.dataset.openNotice);
    });
    document.addEventListener("pointerdown", event => {
      if (!event.target.closest("#agentNotifications")) this.close();
    });
    document.addEventListener("keydown", event => {
      if (!this.panel.hidden && event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation(); this.close(); this.button.focus();
      }
    }, true);
    const resume = () => {
      clearTimeout(this.timer);
      if (!document.hidden && document.hasFocus()) { void this.refresh(); this.scheduleSeen(); }
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("blur", () => clearTimeout(this.timer));
    window.addEventListener("resize", () => { this.syncMobileVisibility(); this.scheduleSeen(); });
    document.addEventListener("agent:rendered", () => this.scheduleSeen());
    $("#agentChatStream").addEventListener("scroll", () => this.scheduleSeen());
    this.render();
    void this.refresh();
  }
  async refresh() {
    if (document.hidden || !document.hasFocus()) return;
    if (this.refreshing) { this.refreshAgain = true; return; }
    clearTimeout(this.timer);
    this.refreshing = true;
    const revision = this.revision || 0;
    try {
      const data = await api("/api/agent/notifications");
      // A GET started before a successful acknowledgement must not resurrect its rows.
      if (revision === (this.revision || 0)) {
        this.rows = data.notifications;
        this.scope = data.db_scope;
        this.error = "";
        this.render(); this.scheduleSeen();
      } else this.refreshAgain = true;
    } catch (error) {
      this.error = `通知暂时无法更新：${errorText(error)}`;
      this.render();
    } finally {
      this.refreshing = false;
      if (this.refreshAgain) { this.refreshAgain = false; void this.refresh(); }
      else if (!document.hidden && document.hasFocus()) this.timer = setTimeout(() => this.refresh(), 5000);
    }
  }
  toggle() {
    this.panel.hidden = !this.panel.hidden;
    this.button.setAttribute("aria-expanded", String(!this.panel.hidden));
    if (!this.panel.hidden) { document.dispatchEvent(new CustomEvent("topbar:popover-open", { detail: this.panel.id })); void this.refresh(); this.render(); }
  }
  close() {
    this.panel.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
  }
  syncMobileVisibility() {
    if (this.rows.length || !matchMedia("(max-width: 920px)").matches) return;
    // A disabled dismiss/read-all button temporarily moves focus to the document body.
    const lostPanelFocus = !this.panel.hidden && document.activeElement === document.body;
    if (this.button.parentElement.contains(document.activeElement) || lostPanelFocus) $("#refreshIndexBtn").focus({ preventScroll: true });
    this.close();
  }
  render() {
    this.syncMobileVisibility();
    const groups = new Map();
    for (const row of this.rows) {
      if (!groups.has(row.session_id)) groups.set(row.session_id, []);
      groups.get(row.session_id).push(row);
    }
    this.button.classList.toggle("has-unread", !!this.rows.length);
    this.button.setAttribute("aria-label", this.rows.length ? `Agent 回复通知，${this.rows.length}条待查看` : "Agent 回复通知，无待查看消息");
    $("#agentNotificationsCount").textContent = this.rows.length ? String(this.rows.length) : "";
    const announcement = this.rows.length ? `${this.rows.length}条 Agent 消息待查看` : "暂无待查看的 Agent 消息";
    if ($("#agentNotificationsLive").textContent !== announcement) $("#agentNotificationsLive").textContent = announcement;
    $("#agentNotificationsReadAll").disabled = !this.rows.length || !!this.clearingAll;
    $("#agentNotificationsReadAll").setAttribute("aria-busy", String(!!this.clearingAll));
    $("#agentNotificationsStatus").textContent = this.error || "";
    const html = [...groups.values()].map(rows => {
      const latest = rows[0];
      const preview = latest.kind === "failed" ? `本轮未完成：${errorText({ error: latest.error })}` : latest.preview;
      return `<li class="agent-notice"><button type="button" class="agent-notice-open" data-open-notice="${e(latest.id)}"><strong>${e(latest.title)}</strong><span class="agent-notice-preview">${e(preview)}</span><span class="agent-notice-meta"><time>${e(latest.created_at)}</time>${rows.length > 1 ? ` · ${rows.length}条待看回复` : ""}</span></button><button type="button" class="agent-notice-dismiss" aria-label="忽略 ${e(latest.title)} 的通知" data-dismiss-notice="${e(JSON.stringify(rows.map(row => row.id)))}">忽略</button></li>`;
    }).join("") || '<li class="agent-notifications-empty">暂无待查看的 Agent 消息</li>';
    const list = $("#agentNotificationsList");
    if (this.listHTML !== html) { list.innerHTML = html; this.listHTML = html; }
  }
  async openNotice(id) {
    const token = ++this.navigation;
    const version = this.contextVersion();
    const scope = this.scope;
    this.opening = token;
    this.controller?.abort();
    this.controller = new AbortController();
    const current = () => token === this.navigation && scope === this.scope;
    try {
      const data = await api(`/api/agent/notifications/${encodeURIComponent(id)}/target?${new URLSearchParams({ db_scope: scope })}`, { signal: this.controller.signal });
      if (!current() || version !== this.contextVersion()) return;
      if (await this.onOpen(data, current)) {
        if (!current()) return;
        this.failedReads.delete(id);
        this.close();
        this.error = "";
      }
    } catch (error) {
      if (current() && error.name !== "AbortError") {
        this.failedReads.set(id, this.contextVersion());
        this.error = `未能打开对应消息，通知已保留：${errorText(error)}`; this.render();
      }
    } finally {
      if (this.opening === token) { this.opening = null; this.scheduleSeen(); }
    }
  }
  scheduleSeen() {
    cancelAnimationFrame(this.seenFrame);
    this.seenFrame = requestAnimationFrame(() => this.confirmVisible());
  }
  confirmVisible() {
    if (this.opening || !this.readerVisible() || !this.agent.open || document.hidden || !document.hasFocus()) return;
    const session = this.agent.payload.session;
    if (!session || session.db_scope !== this.scope) return;
    const ids = this.rows.filter(row => {
      if (this.failedReads.get(row.id) === this.contextVersion()) return false;
      this.failedReads.delete(row.id);
      if (this.acknowledging.has(row.id) || row.session_id !== session.id || row.item_id !== this.agent.item?.id) return false;
      const job = this.agent.payload.jobs?.find(job => job.id === row.job_id);
      const message = this.agent.payload.messages?.find(message => message.id === row.message_id && message.job_id === row.job_id && message.role === "assistant");
      if (!job || !terminal.includes(job.status) || message?.status !== job.status) return false;
      return replyIsVisible($(`#agentChatStream [data-message-id="${CSS.escape(String(row.message_id))}"][data-job-id="${CSS.escape(row.job_id)}"]`));
    }).map(row => row.id);
    if (ids.length) void this.confirm(ids, false).catch(() => {});
  }
  async markAll() {
    if (this.clearingAll || !this.rows.length) return;
    const ids = this.rows.map(row => row.id);
    this.clearingAll = true; this.render();
    try { await this.confirm(ids, true); }
    catch { /* confirm keeps the failure visible in the list. */ }
    finally { this.clearingAll = false; this.render(); }
  }
  async confirm(ids, dismiss) {
    ids = ids.filter(id => !this.acknowledging.has(id));
    if (!ids.length) return;
    const scope = this.scope;
    ids.forEach(id => this.acknowledging.add(id));
    try {
      await api("/api/agent/notifications/read", { method: "POST", body: { db_scope: scope, ids, dismiss } });
      if (scope !== this.scope) return;
      this.revision = (this.revision || 0) + 1;
      const confirmed = new Set(ids);
      this.rows = this.rows.filter(row => !confirmed.has(row.id));
      ids.forEach(id => this.failedReads.delete(id));
      this.error = ""; this.render();
      void this.refresh();
    } catch (error) {
      this.error = `通知已保留：${errorText(error)}`; this.render();
      throw error;
    } finally {
      ids.forEach(id => this.acknowledging.delete(id));
    }
  }
}
