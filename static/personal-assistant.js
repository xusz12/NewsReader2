import { $, api, escapeHTML as e, errorText, icon, markdown, quoteSourceLabel, singlePane, resizeAutoFields, bindAgentWindowSize } from "./ui.js";
import { AnchoredPopover } from "./anchored-popover.js";

const base = "/api/personal-assistant", active = job => job && ["queued", "running"].includes(job.status);
const draftKey = "nr_personal_assistant_draft";
// Local NR may be opened over HTTP on a private network, where randomUUID is
// absent even though getRandomValues remains available.
const requestId = () => crypto.randomUUID?.() || "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c =>
  (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));

export class PersonalAssistant {
  constructor({onSettings}) {
    this.onSettings = onSettings;
    this.sequence = 0;
    let draft;
    try { draft = JSON.parse(sessionStorage.getItem(draftKey)); } catch {}
    this.quote = draft?.quote || null;
    this.pendingRequest = draft?.pendingRequest || null;
    this.panel = $("#personalAssistantPanel"); this.trigger = $("#personalAssistantBtn");
    this.panel.innerHTML = `<div class="agent-header"><div class="agent-title-info"><div class="agent-title-line"><strong id="personalAssistantName">个人助手</strong><span class="agent-runtime-badge" id="personalAssistantRuntime"></span></div><span class="agent-caption" id="personalAssistantCaption">独立讨论 · 长期记忆 · NR 全库</span></div>
      <div class="agent-actions"><button type="button" class="agent-btn" id="personalAssistantSettingsBtn" aria-label="个人助手设置" title="个人助手设置">${icon("settings")}</button><button type="button" class="agent-btn" id="personalAssistantExpand"></button><button type="button" class="agent-btn" id="personalAssistantClose" aria-label="关闭个人助手" title="关闭">✕</button></div></div>
      <div class="agent-context" id="personalAssistantHistory" hidden><button type="button" class="reader-tool-btn" data-personal-main>返回主对话</button></div>
      <div class="agent-chat-stream" id="personalAssistantMessages" role="log" aria-label="个人助手讨论" aria-live="off" tabindex="0"></div>
      <div class="agent-context"><p id="personalAssistantStatus" role="status" aria-live="polite"></p><button type="button" class="reader-tool-btn" id="personalAssistantReload" hidden>重新读取</button><button type="button" class="reader-tool-btn" id="personalAssistantRetry" hidden>重试本轮</button></div>
      <div class="agent-context" id="personalAssistantQuote" hidden></div>
      <div class="agent-input-row"><textarea class="agent-input" rows="1" data-auto-rows data-auto-min-rows="1" data-auto-max-rows="5" id="personalAssistantInput" aria-label="与个人助手对话" aria-describedby="personalAssistantInputHint" placeholder="聊聊新闻，或继续之前的想法…" maxlength="20000"></textarea><span class="sr-only" id="personalAssistantInputHint">电脑按 Enter 发送，Shift Enter 换行；手机按发送按钮或 Ctrl / Command Enter 发送。</span><button type="button" class="agent-send-btn" id="personalAssistantSend" aria-label="发送消息">${icon("send")}</button></div>`;
    this.input = $("#personalAssistantInput");
    this.input.value = draft?.text || "";
    this.renderQuote();
    this.popover = new AnchoredPopover({panel:this.panel, trigger:this.trigger,
      onClose:()=>{clearTimeout(this.timer); this.timer=null;}});
    this.trigger.onclick = () => this.popover.open ? this.popover.close(true) : this.show();
    $("#personalAssistantClose").onclick = () => this.popover.close(true);
    bindAgentWindowSize(this.panel, $("#personalAssistantExpand"), () => this.popover.position());
    $("#personalAssistantSettingsBtn").onclick = () => {this.popover.close(); this.onSettings();};
    $("#personalAssistantRetry").onclick = () => this.send(this.snapshot?.job?.id);
    $("#personalAssistantReload").onclick = () => this.load();
    $("#personalAssistantSend").onclick = () => active(this.snapshot?.job) ? this.stop() : this.send();
    this.input.oninput = () => {this.saveDraft(); this.resize(); this.renderStatus();};
    this.input.onkeydown = event => {
      if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229 &&
          (event.ctrlKey || event.metaKey || (!singlePane.matches && !event.shiftKey))) {
        event.preventDefault(); void this.send();
      }
    };
    this.panel.addEventListener("click", event => {
      if (event.target.closest("[data-personal-older]")) void this.loadOlder();
      if (event.target.closest("[data-personal-main]")) void this.show();
      if (event.target.closest("[data-personal-remove-quote]")) {
        this.quote = null;
        this.saveDraft(); this.renderQuote(); this.resize();
        if (!singlePane.matches) this.input.focus({preventScroll:true});
      }
    });
    document.addEventListener("personal-assistant:profile", event => {this.profile=event.detail;this.renderIdentity();this.renderStatus();if(!this.snapshot?.messages.length)this.renderMessages();});
    document.addEventListener("visibilitychange", () => {if (!document.hidden && this.popover.open) void this.load();});
    window.addEventListener("resize", () => this.resize());
    window.visualViewport?.addEventListener("resize", () => this.resize());
    new ResizeObserver(() => this.resize()).observe(this.panel);
  }
  resize() {
    if (!this.popover.open) return;
    const row=this.input.parentElement, rowStyle=getComputedStyle(row), inputStyle=getComputedStyle(this.input);
    const line=parseFloat(inputStyle.lineHeight), padding=parseFloat(inputStyle.paddingTop)+parseFloat(inputStyle.paddingBottom)+2;
    const fixed=[...this.panel.children].filter(node=>node!==row && node.id!=="personalAssistantMessages").reduce((sum,node)=>sum+node.getBoundingClientRect().height,0);
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
    this.replyTarget = null;
    this.popover.show();
    this.renderQuote(); this.renderStatus(); this.resize();
    if (!singlePane.matches) this.input.focus({preventScroll:true});
    else $("#personalAssistantClose").focus({preventScroll:true});
    await this.load();
  }
  async load() {
    const seq = ++this.sequence, target = this.replyTarget;
    const route = target ? encodeURIComponent(target.threadId) + "?message=" + encodeURIComponent(target.messageId) : "main";
    this.loading = true; this.notice = "正在读取个人助手…"; this.renderStatus();
    try {
      const [{profile}, data] = await Promise.all([api(base+"/profile"), api(base+"/threads/"+route)]);
      if (seq !== this.sequence) return;
      this.profile = profile; this.snapshot = data;
      this.notice = ""; this.loadFailed = false;
      this.renderIdentity(); this.renderMessages(true); this.resize();
    } catch (error) { if (seq === this.sequence) {this.notice=errorText(error);this.loadFailed=true;} }
    finally { if (seq === this.sequence) {this.loading=false;this.renderStatus();this.schedule();} }
  }
  renderIdentity() {
    $("#personalAssistantName").textContent = this.profile?.name || "个人助手";
    const runtime = (this.replyTarget || active(this.snapshot?.job)) ? this.snapshot?.thread?.runtime : this.profile?.runtime;
    $("#personalAssistantRuntime").textContent = {codex:"Codex",dsh:"DSH",pi:"Pi"}[runtime] || "";
    this.trigger.title = this.profile?.name ? `与${this.profile.name}对话` : "个人助手";
  }
  async openReply(threadId, messageId) {
    const target = {threadId, messageId};
    this.replyTarget = target; this.snapshot = null;
    this.popover.show(); this.renderQuote();
    await this.load();
    if (this.replyTarget !== target || this.snapshot?.thread?.id !== threadId ||
        !this.snapshot.messages.some(m=>String(m.id) === String(messageId) && m.role === "assistant" && m.status === "done")) return false;
    const reply = $(`#personalAssistantMessages [data-message-id="${CSS.escape(String(messageId))}"]`);
    if (!reply) return false;
    reply.tabIndex = -1;
    reply.scrollIntoView({block:"center"});
    reply.focus({preventScroll:true});
    return true;
  }
  renderMessages(scroll = false) {
    const host=$("#personalAssistantMessages"), nearBottom=host.scrollHeight-host.scrollTop-host.clientHeight<60;
    const messages=this.snapshot?.messages||[];
    if (!messages.length) {
      host.innerHTML=`<p class="agent-empty-caption">我是${e(this.profile?.name || "个人助手")}。可以直接提问，也可以从新闻或报告递入引文。</p>`;
      return;
    }
    host.querySelector(".agent-empty-caption")?.remove();
    let older=host.querySelector("[data-personal-older]");
    if (this.snapshot.has_more && !older) {older=document.createElement("button");older.className="btn-secondary";older.dataset.personalOlder="";older.textContent="载入更早消息";host.prepend(older);}
    if (!this.snapshot.has_more) older?.remove();
    const ids=new Set(messages.map(m=>String(m.id)));
    for(const node of host.querySelectorAll("[data-message-id]")) if(!ids.has(node.dataset.messageId)) node.remove();
    for(const message of messages) {
      let node=host.querySelector(`[data-message-id="${message.id}"]`);
      if(!node) {node=document.createElement("article");node.className=`agent-msg ${message.role}`;node.dataset.messageId=message.id;host.append(node);}
      const text=message.content || (active(message) && message.role === "assistant" ? "正在整理资料…" : "本轮没有生成回复");
      const signature=JSON.stringify([text,message.status,message.quote,this.profile?.name]);
      if(node.signature !== signature) {
        node.innerHTML=`<span class="agent-caption">${message.role === "user" ? "你" : e(this.profile?.name || "个人助手")}${["failed","stopped"].includes(message.status) ? " · 未完成" : ""}</span><div class="agent-bubble">${message.quote ? this.quoteCard(message.quote) : ""}${markdown(text)}</div>`;
        node.signature=signature;
      }
    }
    if(scroll||nearBottom) host.scrollTop=host.scrollHeight;
    document.dispatchEvent(new Event("personal-assistant:rendered"));
  }
  renderStatus() {
    const job=this.snapshot?.job, busy=active(job), history=!!this.replyTarget;
    $("#personalAssistantHistory").hidden=!history;
    this.input.parentElement.hidden=history;
    $("#personalAssistantStatus").textContent=this.notice || (history ? "" : busy ? (job.stop_requested ? "正在停止…" :
      ({compacting:"正在整理上下文",searching:"正在搜索网页…",fetching:"正在抓取来源网页…",writing:"正在回复…"}[job.progress] || "正在查阅资料、整理记忆并回复…")) :
      job?.error_text || (job?.status === "done" ? "本轮已完成 · 讨论已保存" : ""));
    $("#personalAssistantStatus").classList.toggle("error-state", !!this.loadFailed || job?.status === "failed");
    $("#personalAssistantRetry").hidden = history || this.sending || !["failed","stopped"].includes(job?.status);
    $("#personalAssistantReload").hidden=!this.loadFailed;
    const button=$("#personalAssistantSend"); button.innerHTML=icon(busy?"stop":"send");
    button.setAttribute("aria-label",busy?"停止回复":"发送消息");button.title=busy?"停止回复":"发送消息";
    button.disabled=history || this.sending || this.loading || (busy ? !!job.stop_requested : !this.input.value.trim());
  }
  async send(retryJob = null) {
    const text=this.input.value.trim(), quote=this.quote;
    if(this.replyTarget || (!text && !retryJob) || this.sending || this.loading || active(this.snapshot?.job)) return;
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
        ++this.sequence;this.loading=false;this.snapshot=data;this.notice="";this.loadFailed=false;
        this.renderIdentity();this.renderMessages(true);
      }
    } catch(error) {this.notice=errorText(error);this.loadFailed=true;}
    finally {this.sending=false;this.renderStatus();this.resize();this.schedule();}
  }
  async stop() {
    const id=this.snapshot?.thread?.id;
    if(!id || this.replyTarget)return;
    try {
      const data=await api(base+"/threads/"+id+"/stop",{method:"POST",body:{}});
      if (!this.replyTarget) {this.snapshot=data;this.notice="";this.renderIdentity();this.renderMessages();}
    } catch(error){this.notice=errorText(error);}
    this.renderStatus();this.schedule();
  }
  schedule() {
    clearTimeout(this.timer);
    if(this.popover.open && !this.replyTarget && !document.hidden && active(this.snapshot?.job)) this.timer=setTimeout(()=>this.poll(),800);
  }
  async poll() {
    const seq=this.sequence, id=this.snapshot?.thread?.id;
    if(!id)return;
    try {
      const data=await api(base+"/threads/"+id);
      if(seq!==this.sequence)return;
      const first=data.messages[0]?.id || 0, older=(this.snapshot?.messages||[]).filter(m=>m.id<first);
      this.snapshot={...data,messages:[...older,...data.messages],has_more:older.length ? this.snapshot.has_more : data.has_more};
      this.renderIdentity();this.renderMessages();this.notice="";this.loadFailed=false;
    } catch(error){if(seq===this.sequence){this.notice=errorText(error);this.loadFailed=true;}}
    if(seq===this.sequence){this.renderStatus();this.schedule();}
  }
  async loadOlder() {
    const id=this.snapshot?.thread?.id, first=this.snapshot?.messages[0]?.id;
    if(!first)return;
    try {
      const data=await api(base+"/threads/"+id+"?before="+first);
      if(this.snapshot?.thread?.id!==id || this.snapshot?.messages[0]?.id!==first)return;
      const host=$("#personalAssistantMessages"), height=host.scrollHeight, top=host.scrollTop;
      this.snapshot.messages=[...data.messages,...this.snapshot.messages];this.snapshot.has_more=data.has_more;
      host.replaceChildren();this.renderMessages();host.scrollTop=top+host.scrollHeight-height;
    } catch(error){this.notice=errorText(error);this.renderStatus();}
  }
}
