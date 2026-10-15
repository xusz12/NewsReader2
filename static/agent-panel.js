import {
  $,
  $$,
  api,
  escapeHTML as e,
  errorText,
  toast,
  busy,
  markdown,
  storage,
  persist,
} from "./ui.js";
const activeStatuses = ["queued", "pending", "running", "streaming"];
export class AgentPanel {
  constructor({ onArchive }) {
    this.onArchive = onArchive;
    this.open = storage("newsreader_workbench_agent_collapsed", "true") === "false";
    this.payload = {};
    this.drafts = new Map();
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
    $("#agentStopBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.stop());
    $("#archiveAgentBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.archive());
    $("#archiveAgentBtn").setAttribute("aria-label", "提炼结论追加到想法");
    $("#agentSendBtn").onclick = () => this.send();
    $("#agentPromptInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        this.send();
      }
    });
    $$(".suggestion-chip").forEach(
      (button) =>
        (button.onclick = () => {
          $("#agentPromptInput").value = button.dataset.prompt;
          $("#agentPromptInput").focus();
        }),
    );
    $(".mobile-sheet-handle-bar").onclick = () => this.collapse();
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
  clearStream() {
    this.stream?.close();
    this.stream = null;
    clearTimeout(this.pollTimer);
    this.streamJob = null;
  }
  suspend() {
    this.clearStream();
  }
  select(item, payload) {
    const changed = this.item?.id !== item?.id;
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
      this.sending = false;
      const draft = this.drafts.get(item?.id) || {};
      $("#agentPromptInput").value = draft.prompt || "";
      this.quoteText = draft.quote || "";
    }
    if (payload) this.payload = payload;
    this.render();
    if (this.open && this.active()) this.startStream(this.active().id);
  }
  async toggle() {
    if (!this.item) return;
    this.open = !this.open;
    persist("newsreader_workbench_agent_collapsed", String(!this.open));
    this.render();
    if (this.open) {
      await this.refresh();
      $("#agentPromptInput").focus({ preventScroll: true });
    } else this.clearStream();
  }
  collapse() {
    this.open = false;
    persist("newsreader_workbench_agent_collapsed", "true");
    this.render();
    this.clearStream();
  }
  async refresh() {
    if (!this.item) return;
    const id = this.item.id,
      sequence = this.sequence;
    try {
      const data = await api(
        `/api/news/${encodeURIComponent(id)}/agent/session`,
      );
      if (sequence !== this.sequence) return;
      this.payload = data.agent || data;
      this.render();
      if (this.open && this.active()) this.startStream(this.active().id);
    } catch (error) {
      if (sequence === this.sequence) {
        this.message = errorText(error);
        this.render();
      }
    }
  }
  render() {
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
      available =
        this.payload.context_available === true || !!this.payload.session;
    $("#agentPromptInput").disabled = !available || !!active || this.sending;
    $("#agentSendBtn").disabled = !available || !!active || this.sending;
    $("#agentPromptInput").placeholder = available
      ? "基于本文原文提问…"
      : "全文抓取完成后即可提问";
    $("#agentNewBtn").disabled = !available || !!active || this.sending;
    $("#agentStopBtn").hidden = !active;
    $("#archiveAgentBtn").disabled =
      !!active ||
      this.sending ||
      this.archiving ||
      !(this.payload.messages || []).some(
        (message) => message.role === "assistant" && message.content,
      );
    $("#agentStatus").textContent =
      this.message ||
      (active
        ? "后台研究中…"
        : this.payload.context_updated
          ? "正文已更新，可新建会话读取最新内容"
          : available
            ? "问题在后台处理，可继续阅读"
            : "等待正文就绪");
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
          return `<div class="agent-msg ${message.role === "user" ? "user" : "assistant"}"><div class="agent-bubble">${message.quote_text ? `<blockquote>${e(message.quote_text)}</blockquote>` : ""}${markdown(message.content || (activeStatuses.includes(job?.status) ? "正在研究…" : ""))}</div>${failed ? `<div class="agent-error">${e(job.status === "stopped" ? "已停止，保留已生成的内容" : job.status === "interrupted" ? "服务重启中断了任务" : errorText({ error: job.error || "request_failed" }))}<button class="btn-secondary" type="button" data-retry-job="${e(job.id)}" ${active ? "disabled" : ""}>重试</button></div>` : ""}</div>`;
        })
        .join("") ||
      `<div class="agent-msg assistant"><div class="agent-bubble">${available ? "这是本篇新闻独立的临时研究会话。你可以围绕原文提问，或选择正文中的文字引用提问。" : "先抓取完整正文，再开始研究。"}</div></div>`;
    if (atBottom) stream.scrollTop = stream.scrollHeight;
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
    if (!this.item || this.sending || this.active()) return;
    const question = $("#agentPromptInput").value.trim();
    if (!question) return;
    const id = this.item.id,
      sequence = this.sequence,
      quote = this.quoteText || "";
    this.sending = true;
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
      // A runtime change requires the explicit New session action; never delete a conversation implicitly.
      const data = await api(`/api/news/${encodeURIComponent(id)}/agent/jobs`, {
        method: "POST",
        body: {
          session_id: session.id,
          question,
          quote_text: quote,
          quote_source: "正文",
        },
      });
      this.drafts.delete(id);
      if (sequence !== this.sequence) return;
      this.payload = data.agent || data;
      $("#agentPromptInput").value = "";
      this.quoteText = "";
      this.message = "";
      if (this.active()) this.startStream(this.active().id);
    } catch (error) {
      if (sequence === this.sequence) this.message = errorText(error);
    } finally {
      if (sequence === this.sequence) {
        this.sending = false;
        this.render();
      }
    }
  }
  quote(text) {
    this.quoteText = text;
    this.open = true;
    persist("newsreader_workbench_agent_collapsed", "false");
    this.render();
    this.refresh();
    $("#agentPromptInput").focus();
  }
  async newSession() {
    if (!this.item || this.active()) return;
    if (
      this.payload.messages?.length &&
      !confirm("新建会话会删除本篇的旧临时对话，是否继续？")
    )
      return;
    const id = this.item.id,
      sequence = this.sequence;
    const data = await api(
      `/api/news/${encodeURIComponent(id)}/agent/session`,
      { method: "POST", body: { new_session: true } },
    );
    if (sequence !== this.sequence) return;
    this.payload = data.agent || data;
    this.quoteText = "";
    this.message = "新会话已准备好";
    this.render();
  }
  async stop() {
    const active = this.active();
    if (!active) return;
    const sequence = this.sequence;
    const data = await api(
      `/api/news/${encodeURIComponent(this.item.id)}/agent/jobs/${encodeURIComponent(active.id)}/stop`,
      { method: "POST" },
    );
    if (sequence !== this.sequence) return;
    this.mergeJob(data.job);
    this.message = "已请求停止";
    this.render();
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
    if (!id || this.active()) return;
    const messages = (this.payload.messages || [])
      .filter(
        (x) => ["user", "assistant"].includes(x.role) && x.content?.trim(),
      )
      .map((x) => ({ role: x.role, content: x.content }));
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
