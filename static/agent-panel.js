import { confirmAction } from "./confirm-dialog.js";
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
} from "./ui.js";
const activeStatuses = ["queued", "pending", "running", "streaming", "stopping"];
export class AgentPanel {
  constructor({ onArchive }) {
    this.onArchive = onArchive;
    this.open = storage("newsreader_workbench_agent_collapsed", "true") === "false";
    this.payload = {};
    this.drafts = new Map();
    this.draftVersions = new Map();
    this.pendingSends = new Set();
    this.initializing = new Map();
    this.sequence = 0;
    $("#agentHeader").addEventListener("click", (event) => {
      if (!event.target.closest("button")) this.toggle();
    });
    $("#agentDockToggleBtn").onclick = () => this.toggle();
    $("#agentExpandToggleBtn").onclick = () => {
      $("#agentDock").classList.toggle("maximized");
      $("#agentExpandToggleBtn").setAttribute(
        "aria-label",
        $("#agentDock").classList.contains("maximized")
          ? "还原窗口"
          : "扩大窗口",
      );
    };
    $("#agentNewBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.newSession());
    $("#archiveAgentBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.archive());
    $("#archiveAgentBtn").setAttribute("aria-label", "提炼结论追加到想法");
    $("#agentSendBtn").onclick = () => this.active() ? this.stop() : this.send();
    $("#detailPanelFeed").addEventListener("pointerdown", event => {
      if (this.open && !event.target.closest("#agentDock")) this.collapse();
    });
    $("#agentPromptInput").addEventListener("keydown", (event) => {
      const mobile = matchMedia("(max-width: 920px)").matches;
      if (event.key === "Enter" && !event.isComposing && !this.composing && event.keyCode !== 229 &&
          (event.ctrlKey || event.metaKey || (!mobile && !event.shiftKey))) {
        event.preventDefault();
        this.send();
      }
    });
    const input = $("#agentPromptInput"), dock = $("#agentDock");
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
    $("#agentChatStream").addEventListener("click", (event) => {
      const button = event.target.closest("[data-retry-job]");
      if (button) busy(button, () => this.retry(button.dataset.retryJob));
    });
    this.render();
  }
  active() {
    return (this.payload.jobs || []).find((job) =>
      activeStatuses.includes(job.status),
    );
  }
  get sending() {
    return this.pendingSends.has(this.item?.id);
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
    const floating = matchMedia("(max-width: 920px)").matches && this.open && !dock.hidden &&
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
    const rows = mobile ? 1 : 2;
    if (input.rows !== rows) input.rows = rows;
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
      const body = $(".agent-body");
      const spacing = node => {
        if (!node.getClientRects().length) return 0;
        const css = getComputedStyle(node);
        return ["paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth", "marginTop", "marginBottom"]
          .reduce((sum, property) => sum + (parseFloat(css[property]) || 0), 0);
      };
      const reserved = Array.from(body.children).reduce((sum, node) => sum + spacing(node), spacing(body));
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
  clearStream() {
    this.stream?.close();
    this.stream = null;
    clearTimeout(this.pollTimer);
    this.streamJob = null;
  }
  suspend() {
    this.clearStream();
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
    if (payload && (!this.pinnedSessionId || payload.session?.id === this.pinnedSessionId)) this.payload = payload;
    this.render();
    if (this.open && this.active()) this.startStream(this.active().id);
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
    await this.refresh();
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
      this.payload = data.agent || data;
      this.render();
      if (this.open && this.payload.context_available) await this.ensureSession();
      if (this.open && this.active()) this.startStream(this.active().id);
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
      this.payload = data.agent || data;
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
    dock.classList.toggle("minimized", !this.open);
    dock.classList.toggle("mobile-sheet-open", this.open);
    $(".agent-body").inert = !this.open;
    $("#agentDockToggleBtn").setAttribute("aria-expanded", String(this.open));
    $("#agentDockToggleBtn").setAttribute(
      "aria-label",
      this.open ? "收起新闻研究 Agent" : "展开新闻研究 Agent",
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
    const stopping = active?.status === "stopping" || this.stopping;
    $("#agentSendBtn").disabled = stopping || (!active && (!available || this.sending || initializing));
    $("#agentSendBtn").innerHTML = icon(active ? "stop" : "send");
    $("#agentSendBtn").setAttribute("aria-label", active ? "停止研究" : "发送");
    $("#agentSendBtn").title = active ? "停止研究" : "发送";
    $("#agentPromptInput").placeholder = available
      ? titleOnly ? "围绕新闻标题提问…" : "围绕本文研究与追问…"
      : "请先选择新闻，或稍后重试";
    $("#agentNewBtn").disabled = !available || !!active || this.sending || initializing;
    $("#archiveAgentBtn").disabled =
      !!active ||
      this.sending ||
      this.archiving ||
      !(this.payload.messages || []).some(
        (message) => message.role === "assistant" && message.content,
      );
    $("#agentStatus").textContent =
      (initializing ? "正在准备会话…" : this.message) ||
      (active
        ? (active.status === "stopping" ? "正在停止…" : ({ thinking: "正在分析原文…", searching: "正在检索来源…", fetching: "正在读取来源…", tool: "正在核查资料…", retrying: "服务暂未响应，正在重试…", summarizing: "正在汇总已有证据…", writing: "正在生成回答…" })[active.progress] || ({ queued: "等待执行…", pending: "等待执行…", running: "正在研究…", streaming: "正在生成回答…" })[active.status])
        : this.payload.context_updated
          ? this.hasHistory() ? "资料已更新，可新建会话读取最新内容" : "资料已更新，首次发送将使用最新内容"
          : available
            ? ({ succeeded: "本轮已完成", failed: "本轮未完成 · 可重试", stopped: "已停止", interrupted: "任务已中断 · 可重试" })[latest?.status] || (titleOnly ? "就绪 · 仅引用新闻标题" : "就绪 · 围绕本文研究")
            : "新闻资料尚不可用");
    $("#agentStatus").title = $("#agentStatus").textContent;
    $("#agentRuntimeBadge").title = $("#agentRuntimeBadge").textContent;
    $("#agentQuote").hidden = !this.quoteText;
    $("#agentQuote").textContent = this.quoteText
      ? `引用：${this.quoteText}`
      : "";
    const stream = $("#agentChatStream"),
      atBottom =
        stream.scrollHeight - stream.scrollTop - stream.clientHeight < 60;
    const jobs = new Map((this.payload.jobs || []).map((job) => [job.id, job]));
    stream.innerHTML =
      (this.payload.messages || [])
        .map((message) => {
          const job = jobs.get(message.job_id);
          const failed =
            message.role === "assistant" &&
            job &&
            ["failed", "stopped", "interrupted"].includes(job.status);
          if (message.role === "assistant" && !message.content && !failed) return "";
          return `<div data-message-id="${e(message.id)}" data-job-id="${e(message.job_id)}" data-role="${e(message.role)}" class="agent-msg ${message.role === "user" ? "user" : "assistant"}"><div class="agent-bubble">${message.quote_text ? `<blockquote class="agent-message-quote"><span class="agent-quote-label">引用文本</span>${e(message.quote_text)}</blockquote>` : ""}${markdown(message.content || "")}</div>${failed ? `<div class="agent-error">${e(job.status === "stopped" ? "已停止，保留已生成的内容" : job.status === "interrupted" ? "服务重启中断了任务" : errorText({ error: job.error || "request_failed" }))}<button class="btn-secondary" type="button" data-retry-job="${e(job.id)}" ${active ? "disabled" : ""}>重试</button></div>` : ""}</div>`;
        })
        .join("") ||
      `<p class="agent-empty-caption">${available ? titleOnly ? "输入你的问题，围绕标题开始临时聊天。" : "围绕本文提问，或选择原文引用提问。" : "选择新闻后即可开始研究。"}</p>`;
    if (atBottom) stream.scrollTop = stream.scrollHeight;
    document.dispatchEvent(new Event("agent:rendered"));
  }
  mergeJob(job) {
    const jobs = (this.payload.jobs ||= []);
    const index = jobs.findIndex((x) => x.id === job.id);
    if (index >= 0) jobs[index] = job;
    else jobs.push(job);
    const message = this.payload.messages?.find(
      (x) => x.job_id === job.id && x.role === "assistant",
    );
    if (message) {
      message.content = job.answer_text || "";
      message.status = job.status;
    }
    this.render();
    if (!activeStatuses.includes(job.status)) {
      this.clearStream();
      this.refresh();
    }
  }
  startStream(jobId) {
    if (!this.open || !this.item || this.streamJob === jobId) return;
    this.clearStream();
    this.streamJob = jobId;
    const id = this.item.id,
      sequence = this.sequence,
      path = `/api/news/${encodeURIComponent(id)}/agent/jobs/${encodeURIComponent(jobId)}`;
    const poll = async () => {
      if (sequence !== this.sequence || !this.open) return;
      try {
        const data = await api(path);
        if (sequence !== this.sequence) return;
        this.mergeJob(data.job);
        if (activeStatuses.includes(data.job.status))
          this.pollTimer = setTimeout(poll, 1800);
      } catch (error) {
        if (sequence === this.sequence) {
          this.message = "连接暂时中断，正在重新获取任务状态…";
          this.render();
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
      if (sequence !== this.sequence) return;
      try {
        const data = JSON.parse(event.data);
        if (data.ok && data.job) this.mergeJob(data.job);
      } catch {
        /* Ignore incomplete frames. */
      }
    };
    stream.onerror = () => {
      stream.close();
      if (sequence === this.sequence && this.open) {
        this.stream = null;
        poll();
      }
    };
  }
  async send() {
    if (!this.item || this.sending || this.active() || this.initializing.has(this.item.id) || this.preparing === this.sequence) return;
    if (this.payload.context_available !== true && !this.payload.session) return;
    const prompt = $("#agentPromptInput").value;
    const question = prompt.trim();
    if (!question) return;
    const id = this.item.id,
      sequence = this.sequence,
      quote = this.quoteText || "",
      draftVersion = this.draftVersions.get(id) || 0;
    this.pendingSends.add(id);
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
      this.payload = data.agent || data;
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
    if (!this.item || this.active()) return;
    const id = this.item.id, sequence = this.sequence, sessionId = this.payload.session?.id;
    const stamp = JSON.stringify([this.payload.messages, this.payload.jobs]);
    if (this.payload.messages?.length && !await confirmAction({ title: "新建 Agent 会话", trigger: "#agentNewBtn", message: "新建会话会删除本篇新闻的旧临时对话与任务。新闻和已保存的想法不受影响。", action: "删除旧对话并新建" })) return;
    if (this.item?.id !== id || sequence !== this.sequence || this.payload.session?.id !== sessionId || stamp !== JSON.stringify([this.payload.messages, this.payload.jobs]) || this.active() || this.sending)
      return toast("新闻或会话已变化，请重新操作");
    const data = await api(
      `/api/news/${encodeURIComponent(id)}/agent/session`,
      { method: "POST", body: { new_session: true } },
    );
    if (sequence !== this.sequence) return;
    this.pinnedSessionId = "";
    this.payload = data.agent || data;
    this.quoteText = "";
    this.message = "新会话已准备好";
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
    if (this.active()) return;
    const sequence = this.sequence;
    const data = await api(
      `/api/news/${encodeURIComponent(this.item.id)}/agent/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" },
    );
    if (sequence !== this.sequence) return;
    this.payload = data.agent || data;
    this.message = "";
    this.render();
    if (this.active()) this.startStream(this.active().id);
  }
  async archive() {
    const id = this.item?.id;
    if (!id || this.active() || this.archiving) return;
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
    this.archiving = true;
    this.message = "正在提炼结论并追加到想法…";
    this.render();
    try {
      const result = await api(
        `/api/news/${encodeURIComponent(id)}/chat/archive`,
        {
          method: "POST",
          body: {
            messages,
            agent_session_id: this.payload.session?.id || "",
            model: this.payload.session?.model || "",
          },
        },
      );
      this.onArchive(id, result);
      toast("结论已追加到本篇想法");
    } finally {
      this.archiving = false;
      this.message = "";
      this.render();
    }
  }
}
