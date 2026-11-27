import { AgentModelPicker } from "./agent-model-picker.js?v=0.6.6";
import { confirmAction } from "./confirm-dialog.js";
import { dismissOnOutsidePress } from "./outside-dismiss.js?v=report-layout-1";
import {
  $,
  api,
  escapeHTML as e,
  errorText,
  toast,
  busy,
  markdown,
  storage,
  persist,
  icon,
  bindAgentWindowSize,
} from "./ui.js";
const activeStatuses = ["queued", "pending", "running", "streaming", "stopping"];
const unfinishedStatuses = ["failed", "stopped", "interrupted"];
const phaseLabels = {
  thinking: "正在分析原文…", searching: "正在检索来源…", fetching: "正在读取来源…",
  tool: "正在核查资料…", retrying: "服务暂未响应，正在重试…",
  summarizing: "正在汇总已有证据…", writing: "正在生成回答…",
};
const reconnecting = "连接暂时中断，正在重新获取任务状态…";

function progressLabel(job) {
  if (job.status === "stopping" || job.cancel_requested) return "正在停止…";
  return phaseLabels[job.progress] || ({queued:"等待执行…", pending:"等待执行…", streaming:"正在生成回答…"}[job.status]) || "正在研究…";
}
export class AgentPanel {
  constructor({ onArchive, selects }) {
    this.onArchive = onArchive;
    this.open = storage("newsreader_workbench_agent_collapsed", "true") === "false";
    this.payload = {};
    this.drafts = new Map();
    this.draftVersions = new Map();
    this.pendingSends = new Set();
    this.archives = new Map();
    this.initializing = new Map();
    this.sequence = 0;
    $("#agentHeader").addEventListener("click", (event) => {
      if (!event.target.closest("button")) this.toggle();
    });
    $("#agentDockToggleBtn").onclick = () => this.toggle();
    bindAgentWindowSize($("#agentDock"), $("#agentExpandToggleBtn"));
    $("#agentNewBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.newSession());
    $("#archiveAgentBtn").onclick = () => this.archive();
    $("#archiveAgentBtn").setAttribute("aria-label", "提炼结论追加到想法");
    $("#agentSendBtn").onclick = () => this.active() ? this.stop() : this.send();
    $("#agentPromptInput").addEventListener("keydown", (event) => {
      const mobile = matchMedia("(max-width: 920px)").matches;
      if (event.key === "Enter" && !event.isComposing && !this.composing && event.keyCode !== 229 &&
          (event.ctrlKey || event.metaKey || (!mobile && !event.shiftKey))) {
        event.preventDefault();
        this.send();
      }
    });
    const input = $("#agentPromptInput"), dock = $("#agentDock");
    dismissOnOutsidePress({
      panel: dock,
      isOpen: () => this.conversationVisible,
      dismiss: () => this.collapse(),
      contains: target => target.closest("#personalAssistantBtn, #personalAssistantPanel") ||
        this.modelPicker?.contains(target) || dock.contains(selects.button) && selects.menu.contains(target),
    });
    this.dockHome = dock.parentElement;
    this.dockAnchor = document.createComment("Agent dock home");
    dock.before(this.dockAnchor);
    this.inputMeasure = input.cloneNode();
    this.inputMeasure.removeAttribute("id");
    this.inputMeasure.removeAttribute("aria-label");
    this.inputMeasure.removeAttribute("aria-describedby");
    this.inputMeasure.className = "agent-input agent-input-measure";
    this.inputMeasure.tabIndex = -1;
    this.inputMeasure.inert = true;
    this.inputMeasure.setAttribute("aria-hidden", "true");
    this.inputMeasure.placeholder = "";
    this.inputMeasure.rows = 1;
    document.body.append(this.inputMeasure);
    input.addEventListener("compositionstart", () => { this.composing = true; });
    input.addEventListener("compositionend", () => { this.composing = false; this.scheduleInputSize(); });
    input.addEventListener("input", () => { this.draftChanged(); this.scheduleInputSize(); });
    input.addEventListener("focus", () => this.scheduleInputSize());
    window.addEventListener("resize", () => this.scheduleInputSize());
    window.visualViewport?.addEventListener("resize", () => this.scheduleInputSize());
    window.visualViewport?.addEventListener("scroll", () => this.scheduleInputSize());
    this.inputObserver = new ResizeObserver(entries => {
      const width = entries[0].contentRect.width;
      if (width !== this.inputWidth) { this.inputWidth = width; this.scheduleInputSize(); }
    });
    this.inputObserver.observe(input);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.clearStream();
      else if (this.open && this.active()) this.startStream(this.active().id);
    });
    $("#agentChatStream").addEventListener("click", (event) => {
      const button = event.target.closest("[data-retry-job]");
      if (button) busy(button, () => this.retry(button.dataset.retryJob));
    });
    this.modelPicker = new AgentModelPicker({host:$("#agentModelControls"), selects,
      context:() => ({item:this.item, session:this.payload.session, open:this.open, busy:!!this.active() || this.sending || this.archiving || this.initializing.has(this.item?.id) || this.preparing === this.sequence}),
      onUpdate:data => {this.applySnapshot(data); this.message = "当前会话模型已切换，下一轮生效"; this.render();},
      onBusy:() => this.render(),
    });
    this.render();
  }
  setHome(panel) {
    if (this.dockHome === panel) return;
    this.dockHome = panel; panel.append(this.dockAnchor);
    this.syncPlacement();
  }
  active() {
    return (this.payload.jobs || []).find((job) =>
      activeStatuses.includes(job.status),
    );
  }
  get sending() {
    return this.pendingSends.has(this.item?.id);
  }
  get conversationVisible() {
    const dock = $("#agentDock");
    return this.open && !dock.hidden && !dock.closest('[inert]') &&
      dock.getClientRects().length > 0 && getComputedStyle(dock).visibility === 'visible';
  }
  get archiveState() {
    const state = this.archives.get(this.item?.id);
    return state?.sessionId === (this.payload.session?.id || "") ? state : null;
  }
  get archiving() {
    return !!this.archiveState?.pending;
  }
  draftChanged() {
    if (this.item) this.draftVersions.set(this.item.id, (this.draftVersions.get(this.item.id) || 0) + 1);
  }
  scheduleInputSize() {
    cancelAnimationFrame(this.inputSizeFrame);
    this.inputSizeFrame = requestAnimationFrame(() => this.resizeInput());
  }
  syncPlacement() {
    const dock = $("#agentDock"), feed = $("#feedView");
    const floating = matchMedia("(max-width: 920px)").matches && this.open && !dock.hidden && !feed.inert &&
      this.dockHome.classList.contains("active") && feed.classList.contains("mobile-reader-active") &&
      !feed.classList.contains("settings-active");
    // A fixed child of the transformed reader is still reader-relative and
    // clipped by its scrolling ancestors. Mount the mobile sheet at body level.
    // Only move it when entering/leaving the sheet, never during keyboard events.
    if (floating && dock.parentElement !== document.body) document.body.append(dock);
    else if (!floating && dock.parentElement !== this.dockHome) this.dockAnchor.after(dock);
    dock.classList.toggle("mobile-agent-overlay", floating);
    this.scheduleInputSize();
  }
  resizeInput() {
    const input = $("#agentPromptInput"), dock = $("#agentDock");
    const mobile = matchMedia("(max-width: 920px)").matches;
    const viewport = window.visualViewport;
    const height = viewport?.height || innerHeight, offset = viewport?.offsetTop || 0;
    if (input.rows !== 1) input.rows = 1;
    if (mobile && dock.classList.contains("mobile-agent-overlay")) {
      const nav = $(".mobile-bottom-nav")?.getBoundingClientRect();
      let bottom = offset + height;
      if (nav?.height && nav.top < bottom && nav.bottom > offset) bottom = nav.top;
      const top = Math.max(offset + 8, $(".topbar").getBoundingClientRect().bottom, bottom - document.documentElement.clientHeight * .70);
      for (const [name, value] of Object.entries({ top, height: Math.max(0, bottom - top), left: viewport?.offsetLeft || 0, width: viewport?.width || innerWidth })) {
        const pixels = `${value}px`;
        if (dock.style.getPropertyValue(`--agent-mobile-${name}`) !== pixels) dock.style.setProperty(`--agent-mobile-${name}`, pixels);
      }
    }
    if (!input.getClientRects().length) return;
    const style = getComputedStyle(input), line = parseFloat(style.lineHeight);
    const extra = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    let limit = Math.max(line + extra, Math.min(line * (mobile ? 5 : 8) + extra, height / 3));
    if (mobile && this.open) {
      const body = $(".agent-body", dock);
      const spacing = node => {
        if (!node.getClientRects().length) return 0;
        const css = getComputedStyle(node);
        return ["paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth", "marginTop", "marginBottom"]
          .reduce((sum, property) => sum + (parseFloat(css[property]) || 0), 0);
      };
      const reserved = Array.from(body.children).reduce((sum, node) => {
        const css = getComputedStyle(node);
        const content = node.matches(".agent-chat-stream,.agent-input-row") ? 0
          : Math.max(0, node.clientHeight - (parseFloat(css.paddingTop) || 0) - (parseFloat(css.paddingBottom) || 0));
        return sum + spacing(node) + content;
      }, spacing(body));
      // Short landscape/keyboard viewports must also leave room for the
      // surrounding chrome, including the chat/context padding.
      limit = Math.max(line + extra, Math.min(limit, body.clientHeight - reserved));
    }
    // Never shrink the focused control to auto/zero for measurement: WebKit
    // reveals the caret while the keyboard pans, creating a scroll/layout loop.
    const measure = this.inputMeasure;
    for (const property of ["width", "font", "lineHeight", "letterSpacing", "paddingTop", "paddingBottom", "paddingLeft", "paddingRight", "borderTopWidth", "borderBottomWidth", "borderLeftWidth", "borderRightWidth", "boxSizing"]) {
      if (measure.style[property] !== style[property]) measure.style[property] = style[property];
    }
    measure.value = input.value || " ";
    const measured = measure.scrollHeight + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const target = Math.min(limit, Math.max(line * input.rows + extra, measured));
    if (input.style.height !== `${target}px`) input.style.height = `${target}px`;
    const overflow = measured > target ? "auto" : "hidden";
    if (input.style.overflowY !== overflow) input.style.overflowY = overflow;
    $("#agentInputHint").textContent = mobile ? "Enter 换行，点击发送或按 Ctrl / Cmd + Enter 发送" : "Enter 发送，Shift + Enter 换行；Ctrl / Cmd + Enter 也可发送";
  }
  hasHistory() {
    return !!(this.payload.messages?.length || this.payload.jobs?.length);
  }
  applySnapshot(payload) {
    const previous = this.payload;
    this.payload = payload;
    if (payload === previous || !payload.session?.id || payload.session.id !== previous.session?.id) return;
    const lastId = Math.max(0, ...(payload.messages || []).map(message => Number(message.id) || 0));
    const newer = previous.messages?.filter(message => message.id > lastId);
    if (newer?.length) {
      payload.messages = [...(payload.messages || []), ...newer];
      const addedJobs = new Set(newer.map(message => message.job_id));
      for (const job of payload.jobs || []) addedJobs.delete(job.id);
      const missingJobs = (previous.jobs || []).filter(job => addedJobs.has(job.id));
      payload.jobs = [...(payload.jobs || []), ...missingJobs];
    }
    const pending = this.active();
    if (!pending) return;
    const settled = previous.jobs?.find(job => job.id === pending.id && !activeStatuses.includes(job.status));
    if (settled) {
      Object.assign(pending, settled);
      this.updateReply(pending);
    }
  }
  clearStream() {
    this.stream?.close();
    this.stream = null;
    clearTimeout(this.pollTimer);
    this.subscription = null;
  }
  suspend() {
    this.clearStream();
    this.modelPicker?.close();
  }
  select(item, payload, { pin = false, navigating = false } = {}) {
    const previousPin = this.pinnedSessionId;
    if (navigating) {
      this.clearStream(); this.sequence++; this.preparing = null;
      this.pinnedSessionId = ""; this.payload = {};
    }
    if (this.item?.id !== item?.id || !payload) this.pinnedSessionId = "";
    if (pin) {
      this.pinnedSessionId = payload?.session?.id || "";
      this.open = true;
      persist("newsreader_workbench_agent_collapsed", "false");
    }
    if (previousPin !== this.pinnedSessionId) {
      this.clearStream(); this.sequence++; this.preparing = null;
    }
    const changed = this.item?.id !== item?.id;
    const contextWasAvailable = this.payload.context_available;
    if (changed) {
      if (this.item)
        this.drafts.set(this.item.id, {
          prompt: $("#agentPromptInput").value,
          quote: this.quoteText || "",
        });
      this.clearStream();
      this.sequence++;
      this.item = item;
      this.payload = {};
      this.message = "";
      this.preparing = null;
      const draft = this.drafts.get(item?.id) || {};
      $("#agentPromptInput").value = draft.prompt || "";
      this.quoteText = draft.quote || "";
    }
    if (payload && (!this.pinnedSessionId || payload.session?.id === this.pinnedSessionId)) this.applySnapshot(payload);
    this.render();
    if (this.open && this.active()) this.startStream(this.active().id);
    else this.clearStream();
    if (this.open && this.payload.context_available && (changed || !contextWasAvailable || !this.payload.session)) this.ensureSession();
  }
  toggle() {
    return this.open ? this.collapse() : this.expand();
  }
  async expand() {
    if (!this.item) return;
    this.open = true;
    persist("newsreader_workbench_agent_collapsed", "false");
    const sequence = this.sequence;
    const job = this.active();
    if (job) {
      this.render();
      this.startStream(job.id);
    } else await this.refresh();
    if (sequence === this.sequence && this.open && !matchMedia("(max-width: 920px)").matches)
      $("#agentPromptInput").focus({ preventScroll: true });
  }
  collapse() {
    const restoreFocus = $("#agentDock").contains(document.activeElement);
    this.open = false;
    persist("newsreader_workbench_agent_collapsed", "true");
    this.render();
    if (restoreFocus) $("#agentDockToggleBtn").focus({ preventScroll: true });
    this.clearStream();
  }
  async refresh() {
    if (!this.item) return;
    const id = this.item.id,
      sequence = this.sequence;
    this.preparing = this.open && !this.hasHistory() ? sequence : null;
    this.render();
    try {
      const data = await api(
        `/api/news/${encodeURIComponent(id)}/agent/session${this.pinnedSessionId ? `?${new URLSearchParams({ session_id: this.pinnedSessionId, db_scope: this.payload.session.db_scope })}` : ""}`,
      );
      if (sequence !== this.sequence) return;
      this.applySnapshot(data.agent || data);
      this.render();
      if (this.open && this.payload.context_available) await this.ensureSession();
      if (this.open && this.active()) this.startStream(this.active().id);
      else this.clearStream();
    } catch (error) {
      if (sequence === this.sequence) {
        this.message = errorText(error);
        this.render();
      }
    } finally {
      if (sequence === this.sequence) {
        this.preparing = null;
        this.render();
      }
    }
  }
  async ensureSession() {
    const id = this.item?.id, sequence = this.sequence;
    if (!id || this.pinnedSessionId || this.hasHistory() || !this.payload.context_available) return this.payload.session;
    let pending = this.initializing.get(id);
    if (!pending) {
      pending = api(`/api/news/${encodeURIComponent(id)}/agent/session`, { method: "POST", body: {} });
      this.initializing.set(id, pending);
    }
    this.render();
    try {
      const data = await pending;
      if (sequence !== this.sequence) return;
      this.applySnapshot(data.agent || data);
      this.message = "";
      return this.payload.session;
    } catch (error) {
      if (sequence === this.sequence) this.message = errorText(error);
    } finally {
      if (this.initializing.get(id) === pending) this.initializing.delete(id);
      if (sequence === this.sequence) this.render();
    }
  }
  render() {
    this.syncPlacement();
    const dock = $("#agentDock");
    this.modelPicker?.sync();
    dock.classList.toggle("minimized", !this.open);
    dock.classList.toggle("mobile-sheet-open", this.open);
    $(".agent-body", dock).inert = !this.open;
    $("#agentDockToggleBtn").setAttribute("aria-expanded", String(this.open));
    $("#agentDockToggleBtn").setAttribute(
      "aria-label",
      this.open ? "收起文章问答" : "展开文章问答",
    );
    $("#agentRuntimeBadge").textContent = this.payload.session
      ? `${this.payload.session.runtime || "Agent"} · ${this.payload.session.model || "默认模型"}`
      : "独立新闻会话";
    const active = this.active(),
      latest = (this.payload.jobs || []).at(-1),
      available =
        this.payload.context_available === true || !!this.payload.session;
    const context = this.payload.session || this.payload;
    const titleOnly = available && context.context_level === "title_only";
    $("#agentContext").hidden = !titleOnly;
    $("#agentContextTitle").textContent = titleOnly ? `引用新闻标题：${context.context_title || this.item?.title || ""}` : "";
    $("#agentContextTitle").title = $("#agentContextTitle").textContent;
    const initializing = this.initializing.has(this.item?.id) || this.preparing === this.sequence;
    const choosingModel = this.modelPicker?.saving || this.modelPicker?.blocked;
    const stopping = active?.status === "stopping" || this.stopping;
    $("#agentSendBtn").disabled = stopping || (!active && (!available || this.sending || this.archiving || initializing || choosingModel));
    $("#agentSendBtn").innerHTML = icon(active ? "stop" : "send");
    $("#agentSendBtn").setAttribute("aria-label", active ? "停止研究" : "发送");
    $("#agentSendBtn").title = active ? "停止研究" : "发送";
    $("#agentPromptInput").placeholder = available
      ? titleOnly ? "围绕新闻标题提问…" : "围绕本文研究与追问…"
      : "请先选择新闻，或稍后重试";
    $("#agentNewBtn").disabled = !available || !!active || this.sending || this.archiving || initializing || choosingModel;
    $("#archiveAgentBtn").hidden = !!this.item?.report_id;
    $("#archiveAgentBtn").setAttribute("aria-busy", String(this.archiving));
    $("#archiveAgentBtn").disabled =
      !!active ||
      this.sending ||
      this.archiving ||
      !(this.payload.messages || []).some(
        (message) => message.role === "assistant" && message.content,
      );
    this.displayStatus(
      (initializing ? "正在准备会话…" : this.archiveState?.message || this.message) ||
      (active
        ? progressLabel(active)
        : this.payload.context_updated
          ? this.hasHistory() ? "资料已更新，可新建会话读取最新内容" : "资料已更新，首次发送将使用最新内容"
          : available
            ? ({ succeeded: "本轮已完成", failed: "本轮未完成 · 可重试", stopped: "已停止", interrupted: "任务已中断 · 可重试" })[latest?.status] || (titleOnly ? "就绪 · 仅引用新闻标题" : "就绪 · 围绕本文研究")
            : "新闻资料尚不可用"));
    $("#agentRuntimeBadge").title = $("#agentRuntimeBadge").textContent;
    $("#agentQuote").hidden = !this.quoteText;
    $("#agentQuote").textContent = this.quoteText
      ? `引用：${this.quoteText}`
      : "";
    this.renderMessages(active, available, titleOnly);
    document.dispatchEvent(new Event("agent:rendered"));
  }
  displayStatus(text) {
    const status = $("#agentStatus");
    if (status.textContent !== text) {
      status.textContent = text;
      status.title = text;
    }
  }
  renderMessages(active, available, titleOnly) {
    const stream = $("#agentChatStream"), messages = this.payload.messages || [];
    const atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight <= 2;
    if (!messages.length) {
      const text = available ? titleOnly ? "输入你的问题，围绕标题开始临时聊天。" : "围绕本文提问，或选择原文引用提问。" : "选择新闻后即可开始研究。";
      if (stream.firstElementChild?.className !== "agent-empty-caption" || stream.textContent !== text) {
        const caption = document.createElement("p");
        caption.className = "agent-empty-caption";
        caption.textContent = text;
        stream.replaceChildren(caption);
      }
      return;
    }
    stream.querySelector(".agent-empty-caption")?.remove();
    const jobs = new Map((this.payload.jobs || []).map((job) => [job.id, job]));
    const nodes = new Map(Array.from(stream.children, node => [node.dataset.messageId, node]));
    let next = stream.firstElementChild;
    for (const message of messages) {
      const id = String(message.id ?? `${message.job_id}-${message.role}`);
      const job = jobs.get(message.job_id);
      const state = message.role === "assistant" ? job?.status || message.status : null;
      const failed = unfinishedStatuses.includes(state);
      const text = activeStatuses.includes(state) ? "正在整理资料…" : failed ? "本轮没有生成回复" : message.content || "";
      const error = failed ? ({stopped:"已停止", interrupted:"服务重启中断了任务"}[state] || errorText({error:job?.error || "request_failed"})) : "";
      const node = nodes.get(id) || document.createElement("div");
      if (node.dataset.jobId !== (message.job_id || "") || node.dataset.role !== message.role) {
        node.dataset.messageId = id;
        node.dataset.jobId = message.job_id || "";
        node.dataset.role = message.role;
        node.className = `agent-msg ${message.role}`;
      }
      nodes.delete(id);
      if (node !== next) stream.insertBefore(node, next);
      next = node.nextElementSibling;
      const fields = [text, message.quote_text, error, failed && !!active, job?.id];
      if (node.renderedFields?.every((value, index) => value === fields[index])) continue;
      const quote = message.quote_text ? `<blockquote class="agent-message-quote"><span class="agent-quote-label">引用文本</span>${e(message.quote_text)}</blockquote>` : "";
      const retry = failed && job ? `<button class="btn-secondary" type="button" data-retry-job="${e(job.id)}" ${active ? "disabled" : ""}>重试</button>` : "";
      node.innerHTML = `<div class="agent-bubble">${quote}${markdown(text)}</div>${error ? `<div class="agent-error">${e(error)}${retry}</div>` : ""}`;
      node.renderedFields = fields;
    }
    for (const node of nodes.values()) node.remove();
    if (atBottom) stream.scrollTop = stream.scrollHeight;
  }
  mergeJob(update) {
    const job = this.payload.jobs?.find(entry => entry.id === update.id);
    if (!job || !activeStatuses.includes(job.status) || (update.session_id && update.session_id !== this.payload.session?.id)) {
      if (this.subscription?.jobId === update.id) this.clearStream();
      return;
    }
    if (job.status === "stopping" && activeStatuses.includes(update.status) && update.status !== "stopping") return;
    Object.assign(job, update);
    job.answer_text = update.status === "succeeded" ? update.answer_text || "" : "";
    if (this.message === reconnecting) this.message = "";
    if (activeStatuses.includes(job.status)) {
      this.displayStatus(this.archiveState?.message || this.message || progressLabel(job));
      if (job.cancel_requested || job.status === "stopping") $("#agentSendBtn").disabled = true;
      return;
    }
    if (this.subscription?.jobId === job.id) this.clearStream();
    if (this.updateReply(job)) this.render();
    else void this.refresh();
  }
  updateReply(job) {
    const message = this.payload.messages?.find(row => row.job_id === job.id && row.role === "assistant");
    if (message) {
      message.content = job.status === "succeeded" ? job.answer_text || "" : "";
      message.status = job.status;
    }
    return message;
  }
  startStream(jobId) {
    if (!this.open || !this.item || document.hidden || this.subscription?.jobId === jobId) return;
    this.clearStream();
    const subscription = this.subscription = {jobId};
    const id = this.item.id,
      path = `/api/news/${encodeURIComponent(id)}/agent/jobs/${encodeURIComponent(jobId)}`;
    const poll = async () => {
      if (this.subscription !== subscription) return;
      try {
        const data = await api(path + "?updates=1");
        if (this.subscription !== subscription) return;
        this.mergeJob(data.job);
        if (this.subscription === subscription)
          this.pollTimer = setTimeout(poll, 1100);
      } catch (error) {
        if (this.subscription === subscription) {
          if (error.httpStatus === 404) {
            this.clearStream();
            await this.refresh();
            return;
          }
          this.message = reconnecting;
          this.displayStatus(reconnecting);
          this.pollTimer = setTimeout(poll, 3500);
        }
      }
    };
    if (typeof EventSource === "undefined") {
      poll();
      return;
    }
    const stream = new EventSource(`${path}/stream`);
    this.stream = stream;
    stream.onmessage = (event) => {
      if (this.subscription !== subscription) return;
      try {
        const data = JSON.parse(event.data);
        if (data.ok && data.job) this.mergeJob(data.job);
        else if (data.error === "job_not_found") {
          this.clearStream();
          void this.refresh();
        }
      } catch {
        /* Ignore incomplete frames. */
      }
    };
    stream.onerror = () => {
      stream.close();
      if (this.subscription === subscription) {
        this.stream = null;
        poll();
      }
    };
  }
  async send() {
    if (!this.item || this.modelPicker?.saving || this.modelPicker?.blocked || this.sending || this.archiving || this.active() || this.initializing.has(this.item.id) || this.preparing === this.sequence) return;
    if (this.payload.context_available !== true && !this.payload.session) return;
    const prompt = $("#agentPromptInput").value;
    const question = prompt.trim();
    if (!question) return;
    const id = this.item.id,
      sequence = this.sequence,
      quote = this.quoteText || "",
      draftVersion = this.draftVersions.get(id) || 0;
    this.pendingSends.add(id);
    this.archives.delete(id);
    $("#agentPromptInput").value = "";
    this.quoteText = "";
    this.drafts.delete(id);
    this.message = "正在创建研究任务…";
    this.render();
    try {
      let session = this.payload.session;
      if (!session) {
        const data = await api(
          `/api/news/${encodeURIComponent(id)}/agent/session`,
          { method: "POST", body: {} },
        );
        session = (data.agent || data).session;
      }
      // The backend refreshes unused sessions; established conversations retain their executor.
      const data = await api(`/api/news/${encodeURIComponent(id)}/agent/jobs`, {
        method: "POST",
        body: {
          session_id: session.id,
          question,
          quote_text: quote,
          quote_source: quote ? "selection" : "",
        },
      });
      if (sequence !== this.sequence) return;
      this.applySnapshot(data.agent || data);
      this.message = "";
      if (this.active()) this.startStream(this.active().id);
    } catch (error) {
      // Restore an unsent question only if the user has not edited its next draft.
      if ((this.draftVersions.get(id) || 0) === draftVersion) {
        this.drafts.set(id, { prompt, quote });
        if (this.item?.id === id) {
          $("#agentPromptInput").value = prompt;
          this.quoteText = quote;
        }
      }
      if (sequence === this.sequence) this.message = errorText(error);
    } finally {
      this.pendingSends.delete(id);
      if (sequence === this.sequence) {
        this.render();
      } else if (this.item?.id === id) {
        this.refresh();
      }
    }
  }
  quote(text) {
    this.draftChanged();
    this.quoteText = text;
    return this.expand();
  }
  async newSession() {
    if (!this.item || this.active() || this.archiving) return;
    const id = this.item.id, sequence = this.sequence, sessionId = this.payload.session?.id;
    const stamp = JSON.stringify([this.payload.messages, this.payload.jobs]);
    if (this.payload.messages?.length && !await confirmAction({ title: "新建 Agent 会话", trigger: "#agentNewBtn", message: "新建会话会删除当前内容的旧临时对话与任务。正文和已保存的想法不受影响。", action: "删除旧对话并新建" })) return;
    if (this.item?.id !== id || sequence !== this.sequence || this.payload.session?.id !== sessionId || stamp !== JSON.stringify([this.payload.messages, this.payload.jobs]) || this.active() || this.sending || this.archiving)
      return toast("新闻或会话已变化，请重新操作");
    const data = await api(
      `/api/news/${encodeURIComponent(id)}/agent/session`,
      { method: "POST", body: { new_session: true } },
    );
    if (sequence !== this.sequence) return;
    this.pinnedSessionId = "";
    this.archives.delete(id);
    this.applySnapshot(data.agent || data);
    this.quoteText = "";
    this.message = "新会话已准备好，使用设置中的默认模型";
    this.render();
  }
  async stop() {
    const active = this.active();
    if (!active || this.stopping) return;
    const sequence = this.sequence;
    this.stopping = true;
    this.render();
    try {
      const data = await api(`/api/news/${encodeURIComponent(this.item.id)}/agent/jobs/${encodeURIComponent(active.id)}/stop`, { method: "POST" });
      if (sequence !== this.sequence) return;
      this.mergeJob(data.job);
      this.message = "";
    } catch (error) {
      if (sequence === this.sequence) this.message = errorText(error);
    } finally {
      this.stopping = false;
      this.render();
    }
  }
  async retry(jobId) {
    if (this.active() || this.archiving) return;
    const sequence = this.sequence;
    const data = await api(
      `/api/news/${encodeURIComponent(this.item.id)}/agent/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" },
    );
    if (sequence !== this.sequence) return;
    this.archives.delete(this.item.id);
    this.applySnapshot(data.agent || data);
    this.message = "";
    this.render();
    if (this.active()) this.startStream(this.active().id);
  }
  async archive() {
    if (this.item?.report_id) return;
    const id = this.item?.id;
    if (!id || this.active() || this.sending || this.archiving || this.modelPicker?.saving) return;
    const messages = (this.payload.messages || [])
      .filter(
        (x) => ["user", "assistant"].includes(x.role) && x.content?.trim(),
      )
      .map((x) => ({
        role: x.role,
        content: x.content,
        ...(x.role === "user" && x.quote_text?.trim()
          ? { quote_text: x.quote_text }
          : {}),
      }));
    const state = { sessionId: this.payload.session?.id || "", pending: true,
      message: "正在提炼结论并追加到想法…" };
    this.archives.set(id, state);
    this.render();
    let saved = false;
    try {
      const result = await api(
        `/api/news/${encodeURIComponent(id)}/chat/archive`,
        {
          method: "POST",
          body: {
            messages,
            agent_session_id: state.sessionId,
            model: this.payload.session?.model || "",
          },
        },
      );
      saved = true;
      this.onArchive(id, result);
      state.message = "结论已追加到本篇想法";
      toast("结论已追加到本篇想法");
    } catch (error) {
      state.message = saved ? "结论已保存，页面更新失败，请刷新查看。" : `归档失败 · ${errorText(error)}`;
      toast(state.message);
    } finally {
      state.pending = false;
      this.render();
    }
  }
}
