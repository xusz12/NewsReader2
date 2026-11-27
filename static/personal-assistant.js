import { $, api, escapeHTML as e, errorText, icon, markdown, quoteSourceLabel, mobileViewport, resizeAutoFields } from "./ui.js";
import { keyboard, displayKey } from "./keyboard-shortcuts.js";
import { replyIsVisible } from "./agent-notifications.js?v=browser-push-1";

const base = "/api/personal-assistant", active = job => job && ["queued", "running"].includes(job.status);
const draftKey = "nr_personal_assistant_draft";
const phaseLabels = {
  compacting:"正在整理上下文", searching:"正在搜索网页…", fetching:"正在抓取来源网页…",
  tool:"正在查阅新闻库与记忆…", writing:"正在回复…",
};
// Local NR may be opened over HTTP on a private network, where randomUUID is
// absent even though getRandomValues remains available.
const requestId = () => crypto.randomUUID?.() || "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c =>
  (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));

export class PersonalAssistant {
  constructor({onLayoutChange}) {
    this.onLayoutChange = onLayoutChange;
    this.sequence = 0;
    this.noticeRevision = 0;
    this.followLatest = true;
    let draft;
    try { draft = JSON.parse(sessionStorage.getItem(draftKey)); } catch {}
    this.quote = draft?.quote || null;
    this.pendingRequest = draft?.pendingRequest || null;
    this.panel = $("#personalAssistantPanel"); this.trigger = $("#personalAssistantBtn");
    this.panel.innerHTML = `<div class="agent-header"><div class="agent-title-info"><div class="agent-title-line"><strong id="personalAssistantName">个人助手</strong><span class="agent-runtime-badge" id="personalAssistantRuntime"></span></div><span class="agent-caption" id="personalAssistantCaption">独立讨论 · 长期记忆 · NR 全库</span></div></div>
      <div class="agent-context" id="personalAssistantHistory" hidden><button type="button" class="reader-tool-btn" data-personal-main>返回主对话</button></div>
      <div class="agent-body agent-message-area"><div class="agent-chat-stream" id="personalAssistantMessages" role="log" aria-label="个人助手讨论" aria-live="off" tabindex="0"></div>
        <button type="button" class="agent-btn agent-jump-latest" id="personalAssistantJump" aria-controls="personalAssistantMessages" aria-label="回到底部" title="回到底部" hidden>${icon("arrowDown")}</button></div>
      <div class="agent-context"><p id="personalAssistantStatus" role="status" aria-live="polite"></p><button type="button" class="reader-tool-btn" id="personalAssistantReload" hidden>重新读取</button><button type="button" class="reader-tool-btn" id="personalAssistantRetry" hidden>重试本轮</button></div>
      <div class="agent-context" id="personalAssistantQuote" hidden></div>
      <div class="agent-input-row"><textarea class="agent-input" rows="1" data-auto-rows data-auto-min-rows="1" data-auto-max-rows="5" id="personalAssistantInput" aria-label="与个人助手对话" aria-describedby="personalAssistantInputHint" placeholder="聊聊新闻，或继续之前的想法…" maxlength="20000"></textarea><span class="sr-only" id="personalAssistantInputHint">电脑按 Enter 发送，Shift Enter 换行；手机按发送按钮或 Ctrl / Command Enter 发送。</span><button type="button" class="agent-send-btn" id="personalAssistantSend" aria-label="发送消息">${icon("send")}</button></div>`;
    this.input = $("#personalAssistantInput");
    this.stream = $("#personalAssistantMessages");
    this.jump = $("#personalAssistantJump");
    this.input.value = draft?.text || "";
    this.renderQuote();
    this.trigger.onclick = () => this.toggle();
    this.jump.onclick = () => this.jumpToLatest();
    this.stream.addEventListener("scroll", () => {
      this.followLatest = this.atBottom();
      this.scheduleMessageView();
    }, {passive:true});
    new ResizeObserver(() => {
      if (this.open && this.followLatest) this.stream.scrollTop = this.stream.scrollHeight;
      this.scheduleMessageView();
    }).observe(this.stream);
    this.lastMessageObserver = new IntersectionObserver(() => this.scheduleMessageView(), {root:this.stream, threshold:[0,1]});
    $("#personalAssistantRetry").onclick = () => this.send(this.snapshot?.job?.id);
    $("#personalAssistantReload").onclick = () => this.replyTarget ? this.load() : this.refresh();
    $("#personalAssistantSend").onclick = () => active(this.snapshot?.job) ? this.stop() : this.send();
    this.input.oninput = () => {this.saveDraft(); this.resize(); this.renderStatus();};
    this.input.onkeydown = event => {
      if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229 &&
          (event.ctrlKey || event.metaKey || (!mobileViewport.matches && !event.shiftKey))) {
        event.preventDefault(); void this.send();
      }
    };
    this.panel.addEventListener("click", event => {
      if (event.target.closest("[data-personal-older]")) void this.loadOlder();
      if (event.target.closest("[data-personal-main]")) void this.show();
      if (event.target.closest("[data-personal-remove-quote]")) {
        this.quote = null;
        this.saveDraft(); this.renderQuote(); this.resize();
        if (!mobileViewport.matches) this.input.focus({preventScroll:true});
      }
    });
    document.addEventListener("personal-assistant:profile", event => {this.profile=event.detail;this.renderIdentity();this.renderStatus();if(!this.snapshot?.messages.length)this.renderMessages();});
    this.renderIdentity();
    document.addEventListener("shortcuts:changed", () => this.renderIdentity());
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) clearTimeout(this.timer);
      else if (this.open && !this.replyTarget) void this.refresh();
    });
    mobileViewport.addEventListener("change", () => { if (this.open) this.syncWorkspace(); });
    window.addEventListener("resize", () => this.resize());
    window.visualViewport?.addEventListener("resize", () => this.resize());
    window.visualViewport?.addEventListener("scroll", () => this.resize());
    new ResizeObserver(() => this.resize()).observe(this.panel);
  }
  get open() { return !this.panel.hidden; }
  toggle() {
    if (this.open) this.close(true);
    else void this.show();
  }
  syncWorkspace() {
    const fullscreen = this.open && mobileViewport.matches;
    $(".app-root").classList.toggle("assistant-fullscreen", fullscreen);
    $("#feedView").inert = fullscreen;
    $("#mobileBottomNav").inert = fullscreen;
    this.onLayoutChange();
  }
  openDrawer() {
    document.dispatchEvent(new CustomEvent("topbar:popover-open", {detail:this.panel.id}));
    if (this.open) return;
    this.panel.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.renderReplyBadge();
    this.syncWorkspace();
    this.resize();
  }
  close(focus = false) {
    if (!this.open) return;
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.jump.hidden = true;
    this.renderReplyBadge();
    clearTimeout(this.timer);
    ++this.sequence;
    this.loading = false;
    this.syncWorkspace();
    if (focus) this.trigger.focus({preventScroll:true});
  }
  resize() {
    if (!this.open) return;
    const viewport = window.visualViewport;
    if (mobileViewport.matches && viewport) {
      const root = this.panel.parentElement;
      for (const [key, value] of Object.entries({top:viewport.offsetTop, height:viewport.height, left:viewport.offsetLeft, width:viewport.width})) {
        const name = "--assistant-" + key, pixels = value + "px";
        if (root.style.getPropertyValue(name) !== pixels) root.style.setProperty(name, pixels);
      }
    }
    const row=this.input.parentElement, rowStyle=getComputedStyle(row), inputStyle=getComputedStyle(this.input);
    const line=parseFloat(inputStyle.lineHeight), padding=parseFloat(inputStyle.paddingTop)+parseFloat(inputStyle.paddingBottom)+2;
    const fixed=[...this.panel.children].filter(node=>node!==row && node!==this.stream.parentElement).reduce((sum,node)=>sum+node.getBoundingClientRect().height,0);
    const available=this.panel.clientHeight-fixed-parseFloat(rowStyle.paddingTop)-parseFloat(rowStyle.paddingBottom)-line*2;
    this.input.dataset.autoMaxRows=Math.max(1,Math.min(5,Math.floor((available-padding)/line)));
    resizeAutoFields(this.panel);
  }
  saveDraft() {
    try {
      if (!this.input.value && !this.quote && !this.pendingRequest) sessionStorage.removeItem(draftKey);
      else sessionStorage.setItem(draftKey, JSON.stringify({text:this.input.value, quote:this.quote, pendingRequest:this.pendingRequest}));
    } catch {}
  }
  quoteCard(quote, removable = false) {
    const title = /^https?:\/\//i.test(quote.url)
      ? `<a href="${e(quote.url)}" target="_blank" rel="noopener noreferrer">${e(quote.title || quote.item_id)}</a>`
      : e(quote.title || quote.item_id);
    const label = quoteSourceLabel(quote.quote_source, quote.quote_paragraph);
    return `<blockquote class="thought-quote${removable ? " draft-quote" : ""}"><div><strong>${title} · ${e(label)}</strong><p>${e(quote.quote_text)}</p></div>${removable ? '<button class="note-delete-btn" type="button" data-personal-remove-quote aria-label="移除引文" title="移除引文">×</button>' : ""}</blockquote>`;
  }
  renderQuote() {
    const host = $("#personalAssistantQuote");
    host.hidden = !this.quote || !!this.replyTarget;
    host.innerHTML = this.quote ? this.quoteCard(this.quote, true) : "";
  }
  attachQuote(quote) {
    this.quote = quote;
    this.saveDraft();
    return this.show();
  }
  async show() {
    const fromHistory = !!this.replyTarget;
    if (fromHistory) this.snapshot = null;
    this.replyTarget = null;
    this.openDrawer();
    this.renderQuote(); this.renderStatus(); this.resize();
    if (!mobileViewport.matches) this.input.focus({preventScroll:true});
    else this.stream.focus({preventScroll:true});
    if (fromHistory) await this.load(true);
    else await this.refresh();
  }
  async load(toBottom = false) {
    const seq = ++this.sequence, target = this.replyTarget;
    clearTimeout(this.timer);
    const route = target ? encodeURIComponent(target.threadId) + (target.messageId ? "?message=" + encodeURIComponent(target.messageId) : "") : "main";
    this.loading = true; this.notice = "正在读取个人助手…"; this.renderStatus();
    try {
      const [{profile}, data] = await Promise.all([api(base+"/profile"), api(base+"/threads/"+route)]);
      if (seq !== this.sequence) return;
      this.profile = profile;
      this.notice = ""; this.loadFailed = false;
      this.applySnapshot(data, {toBottom}); this.resize();
    } catch (error) { if (seq === this.sequence) {this.notice=errorText(error);this.loadFailed=true;} }
    finally { if (seq === this.sequence) {this.loading=false;this.renderStatus();this.schedule();} }
  }
  renderIdentity() {
    $("#personalAssistantName").textContent = this.profile?.name || "个人助手";
    const runtime = (this.replyTarget || active(this.snapshot?.job)) ? this.snapshot?.thread?.runtime : this.profile?.runtime;
    $("#personalAssistantRuntime").textContent = {codex:"Codex",dsh:"DSH",pi:"Pi"}[runtime] || "";
    const label = this.profile?.name ? `与${this.profile.name}对话` : "个人助手";
    const bindings = keyboard.value.bindings.personalAssistant.filter(Boolean);
    this.trigger.title = bindings.length ? label + "（" + bindings.map(displayKey).join(" / ") + "）" : label;
    if (bindings.length) this.trigger.setAttribute("aria-keyshortcuts", bindings.map(key => key.replace(/^Ctrl\+/, "Control+")).join(" "));
    else this.trigger.removeAttribute("aria-keyshortcuts");
  }
  renderReplyBadge() {
    const unread = !!this.unreadJob && this.unreadJob !== this.readingNotice && !this.open;
    this.trigger.classList.toggle("has-unread", unread);
    this.trigger.setAttribute("aria-label", unread ? "个人助手，有新回复" : "个人助手");
    $("#personalAssistantDot").textContent = unread ? "1" : "";
  }
  setReplyNotice(jobId) {
    ++this.noticeRevision;
    const unread = jobId && jobId !== this.seenJob ? jobId : null;
    if (unread !== this.unreadJob) {
      this.unreadJob = unread;
      this.renderReplyBadge();
    }
    if (unread) this.scheduleMessageView();
  }
  async refreshReplyNotice() {
    if (this.checkingNotice || document.hidden) return;
    this.checkingNotice = true;
    const revision = this.noticeRevision;
    try {
      const data = await api(base+"/reply-notice");
      if (revision !== this.noticeRevision) return;
      this.setReplyNotice(data.unread_job_id);
      if (this.unreadJob && this.open && !this.loading && !this.sending && !this.replyTarget &&
          !active(this.snapshot?.job) && !this.snapshot?.messages.some(message => message.job_id === this.unreadJob)) {
        await this.refresh();
      }
    } catch {} finally { this.checkingNotice = false; }
  }
  async confirmReplySeen() {
    const id = this.unreadJob;
    if (!id || this.readingNotice || !this.open || document.hidden || !document.hasFocus()) return;
    if (!this.replyTarget && !this.atBottom()) return;
    const message = this.snapshot?.messages.find(message => message.job_id === id && message.role === "assistant" && message.status === "done");
    if (!message) return;
    const bubble = this.stream.querySelector(`[data-message-id="${message.id}"] .agent-bubble`);
    const box = bubble?.getBoundingClientRect(), viewport = this.stream.getBoundingClientRect();
    if (!box || box.top >= viewport.bottom || box.bottom <= viewport.top || !replyIsVisible(bubble)) return;
    this.readingNotice = id;
    try {
      const data = await api(base+"/reply-notice", {method:"POST", body:{seen_job_id:id}});
      this.seenJob = id;
      if (this.unreadJob === id) this.setReplyNotice(data.unread_job_id);
    } catch {} finally {
      this.readingNotice = null;
      this.renderReplyBadge();
      if (this.unreadJob && this.unreadJob !== id) this.scheduleMessageView();
    }
  }
  async openReply(threadId, messageId) {
    const target = {threadId, messageId};
    this.replyTarget = target; this.snapshot = null;
    this.openDrawer(); this.renderQuote();
    await this.load(true);
    if (!this.open || this.replyTarget !== target || this.snapshot?.thread?.id !== threadId ||
        !this.snapshot.messages.some(m=>String(m.id) === String(messageId) && m.role === "assistant" && m.status === "done")) return false;
    const reply = $(`#personalAssistantMessages [data-message-id="${CSS.escape(String(messageId))}"]`);
    if (!reply) return false;
    reply.tabIndex = -1;
    reply.scrollIntoView({block:"center"});
    reply.focus({preventScroll:true});
    return true;
  }
  atBottom() {
    return this.stream.scrollHeight - this.stream.scrollTop - this.stream.clientHeight <= 2;
  }
  scheduleMessageView() {
    if (!this.open || this.viewFrame) return;
    this.viewFrame = requestAnimationFrame(() => {
      this.viewFrame = null;
      if (!this.open) return;
      const last = this.stream.lastElementChild;
      const below = last?.hasAttribute("data-message-id") && last.getBoundingClientRect().bottom > this.stream.getBoundingClientRect().bottom + 2;
      this.jump.hidden = !(this.snapshot?.has_newer || below);
      void this.confirmReplySeen();
    });
  }
  async jumpToLatest() {
    this.stream.focus({preventScroll:true});
    if (this.replyTarget && this.snapshot?.has_newer) {
      this.replyTarget = {threadId:this.replyTarget.threadId};
      await this.load(true);
    } else {
      this.stream.scrollTop = this.stream.scrollHeight;
      this.followLatest = true;
      this.scheduleMessageView();
    }
  }
  applySnapshot(data, {toBottom = false, incremental = false} = {}) {
    const previous = this.snapshot;
    const sameThread = previous && previous.thread?.id === data.thread?.id;
    const changed = !incremental || !sameThread || data.reset || data.messages.length > 0;
    if (sameThread && !this.replyTarget && !data.reset) {
      const first = data.messages[0]?.id;
      if (incremental || previous.messages.some(message => message.id === first)) {
        const messages = first === undefined ? previous.messages :
          [...previous.messages.filter(message => message.id < first), ...data.messages];
        data = {...data, messages, has_more:previous.has_more};
      }
    }
    this.snapshot = data;
    this.setReplyNotice(data.unread_job_id);
    if (changed) {
      this.renderIdentity();
      this.renderMessages(toBottom);
    }
  }
  renderMessages(scroll = false) {
    const host=this.stream, nearBottom=this.atBottom();
    const messages=this.snapshot?.messages||[];
    if (!messages.length) {
      host.innerHTML=`<p class="agent-empty-caption">我是${e(this.profile?.name || "个人助手")}。可以直接提问，也可以从新闻或报告递入引文。</p>`;
      this.lastMessageObserver.disconnect();
      this.scheduleMessageView();
      return;
    }
    host.querySelector(".agent-empty-caption")?.remove();
    let older=host.querySelector("[data-personal-older]");
    if (this.snapshot.has_more && !older) {older=document.createElement("button");older.className="btn-secondary";older.dataset.personalOlder="";older.textContent="载入更早消息";host.prepend(older);}
    if (!this.snapshot.has_more) older?.remove();
    const nodes = new Map(Array.from(host.querySelectorAll("[data-message-id]"), node => [node.dataset.messageId, node]));
    const ids = new Set(messages.map(message => String(message.id)));
    for (const [id, node] of nodes) if (!ids.has(id)) node.remove();
    let next = host.querySelector("[data-message-id]");
    for(const message of messages) {
      let node=nodes.get(String(message.id));
      if(!node) {node=document.createElement("article");node.className=`agent-msg ${message.role}`;node.dataset.messageId=message.id;}
      if(node!==next) host.insertBefore(node,next);
      next=node.nextElementSibling;
      let text = message.content || "本轮没有生成回复";
      if (message.role === "assistant" && message.status !== "done") {
        text = active(message) ? "正在整理资料…" : "本轮没有生成回复";
      }
      const signature=JSON.stringify([text,message.status,message.quote,this.profile?.name]);
      if(node.signature !== signature) {
        node.innerHTML=`<span class="agent-caption">${message.role === "user" ? "你" : e(this.profile?.name || "个人助手")}${["failed","stopped"].includes(message.status) ? " · 未完成" : ""}</span><div class="agent-bubble">${message.quote ? this.quoteCard(message.quote) : ""}${markdown(text)}</div>`;
        node.signature=signature;
      }
    }
    this.lastMessageObserver.disconnect();
    this.lastMessageObserver.observe(host.lastElementChild);
    this.followLatest = scroll || nearBottom;
    if(this.followLatest) host.scrollTop=host.scrollHeight;
    this.scheduleMessageView();
    document.dispatchEvent(new Event("personal-assistant:rendered"));
  }
  renderProgress() {
    const job = this.snapshot?.job, status = $("#personalAssistantStatus");
    let label = this.notice || "";
    if (!label && !this.replyTarget) {
      if (active(job)) label = job.stop_requested ? "正在停止…" : phaseLabels[job.progress] || "正在查阅资料、整理记忆并回复…";
      else label = job?.error_text || (job?.status === "done" ? "本轮已完成 · 讨论已保存" : "");
    }
    if (status.textContent !== label) status.textContent = label;
  }
  renderStatus() {
    const job=this.snapshot?.job, busy=active(job), history=!!this.replyTarget;
    this.renderProgress();
    $("#personalAssistantHistory").hidden=!history;
    this.input.parentElement.hidden=history;
    $("#personalAssistantStatus").classList.toggle("error-state", !!this.loadFailed || job?.status === "failed");
    $("#personalAssistantRetry").hidden = history || this.sending || !["failed","stopped"].includes(job?.status);
    $("#personalAssistantReload").hidden=!this.loadFailed;
    const button=$("#personalAssistantSend"), label=busy?"停止回复":"发送消息";
    if (button.title !== label) {
      button.innerHTML=icon(busy?"stop":"send");
      button.setAttribute("aria-label",label);button.title=label;
    }
    button.disabled=history || this.sending || this.stopping || this.loading || (busy ? !!job.stop_requested : !this.input.value.trim());
  }
  async send(retryJob = null) {
    const text=this.input.value.trim(), quote=this.quote;
    if(this.replyTarget || (!text && !retryJob) || this.sending || this.loading || active(this.snapshot?.job)) return;
    ++this.sequence;
    this.sending=true;this.notice="正在保存消息…";this.renderStatus();clearTimeout(this.timer);
    const body=retryJob ? {retry_job:retryJob} : {text,quote};
    const key=JSON.stringify(body);
    if(this.pendingRequest?.key !== key) this.pendingRequest={id:requestId(),key};
    this.saveDraft();
    try {
      const data=await api(base+"/threads/main/messages",{method:"POST",body:{...body,request_id:this.pendingRequest.id}});
      this.pendingRequest=null;
      if (!retryJob && this.input.value.trim() === text && this.quote === quote) {
        this.input.value="";this.quote=null;
      }
      this.saveDraft();this.renderQuote();
      if (!this.replyTarget) {
        ++this.sequence;this.loading=false;this.notice="";this.loadFailed=false;
        this.applySnapshot(data, {toBottom:true});
      }
    } catch(error) {this.notice=errorText(error);this.loadFailed=true;}
    finally {this.sending=false;this.renderStatus();this.resize();this.schedule();}
  }
  async stop() {
    const id=this.snapshot?.thread?.id;
    if(!id || this.replyTarget || this.stopping)return;
    const seq = ++this.sequence;
    this.stopping = true;clearTimeout(this.timer);this.renderStatus();
    try {
      const data=await api(base+"/threads/"+id+"/stop",{method:"POST",body:{}});
      if (seq !== this.sequence) return;
      this.notice="";this.loadFailed=false;
      if (active(data.job) && data.job.id === this.snapshot.job?.id) this.snapshot.job = data.job;
      else this.applySnapshot(data);
    } catch(error){if (seq === this.sequence) this.notice=errorText(error);}
    finally {this.stopping=false;this.renderStatus();this.schedule();}
  }
  schedule() {
    clearTimeout(this.timer);
    if (this.open && !this.replyTarget && !document.hidden && active(this.snapshot?.job)) {
      this.timer = setTimeout(() => this.refresh(), 1100);
    }
  }
  refresh() {
    if (!this.open || this.replyTarget || document.hidden || this.loading || this.sending || this.stopping) return;
    if (!this.snapshot) return this.load(true);
    const seq = this.sequence;
    if (this.syncing?.seq === seq) return this.syncing.promise;
    clearTimeout(this.timer);
    const promise = this.syncThread(seq).finally(() => {
      if (this.syncing?.seq === seq) this.syncing = null;
      if (seq === this.sequence) this.schedule();
    });
    this.syncing = {seq, promise};
    return promise;
  }
  async syncThread(seq) {
    const {thread, job, messages} = this.snapshot;
    const path = base + "/threads/" + encodeURIComponent(thread?.id || "main");
    const last = messages.at(-1);
    let completed = false;
    try {
      if (active(job)) {
        const state = await api(path + "?status_only=1");
        if (seq !== this.sequence || document.hidden) return;
        if (state.job?.id === job.id) {
          if (active(state.job)) {
            this.snapshot.job = state.job;
            if (this.loadFailed || this.notice || job.stop_requested !== state.job.stop_requested) {
              this.notice="";this.loadFailed=false;this.renderStatus();
            } else this.renderProgress();
            return;
          }
          completed = state.job.status === "done";
        }
      }
      let after = last ? last.id + 1 : 0;
      if (last && job?.status !== "done") {
        const user = messages.at(-2);
        after = !completed && user?.job_id === last.job_id ? user.id : last.id;
      }
      const data = await api(path + "?after=" + after);
      if (seq !== this.sequence) return;
      this.notice="";this.loadFailed=false;
      this.applySnapshot(data, {incremental:true});
    } catch(error){if(seq===this.sequence){this.notice=errorText(error);this.loadFailed=true;}}
    if (seq === this.sequence) this.renderStatus();
  }
  async loadOlder() {
    const seq=this.sequence, id=this.snapshot?.thread?.id, first=this.snapshot?.messages[0]?.id;
    if(!first || (this.olderPage?.seq === seq && this.olderPage.first === first))return;
    const page = this.olderPage = {seq, first};
    try {
      const data=await api(base+"/threads/"+id+"?before="+first);
      if(seq!==this.sequence || this.snapshot?.thread?.id!==id || this.snapshot?.messages[0]?.id!==first)return;
      const host=this.stream, height=host.scrollHeight, top=host.scrollTop;
      this.snapshot.messages=[...data.messages,...this.snapshot.messages];this.snapshot.has_more=data.has_more;
      this.renderMessages();host.scrollTop=top+host.scrollHeight-height;
      this.followLatest=this.atBottom();
    } catch(error){if(seq===this.sequence){this.notice=errorText(error);this.renderStatus();}}
    finally {if (this.olderPage === page) this.olderPage = null;}
  }
}
