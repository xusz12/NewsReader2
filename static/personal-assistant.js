import { $, api, escapeHTML as e, errorText, icon, markdown, storage, persist, resizeAutoFields } from "./ui.js";
import { selectField } from "./form-controls.js";
import { AnchoredPopover } from "./anchored-popover.js";

const base = "/api/personal-assistant", active = job => job && ["queued", "running"].includes(job.status);
const lastThreadKey = "nr_personal_assistant_thread";
// Local NR may be opened over HTTP on a private network, where randomUUID is
// absent even though getRandomValues remains available.
const requestId = () => crypto.randomUUID?.() || "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c =>
  (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));

export class PersonalAssistant {
  constructor({selects, onSettings}) {
    this.selects = selects; this.onSettings = onSettings; this.drafts = new Map(); this.threads = []; this.sequence = 0;
    this.threadId = storage(lastThreadKey, "");
    this.panel = $("#personalAssistantPanel"); this.trigger = $("#personalAssistantBtn");
    this.panel.innerHTML = `<div class="agent-header"><div class="agent-title-info"><div class="agent-title-line"><strong id="personalAssistantName">个人助手</strong><span class="agent-runtime-badge" id="personalAssistantRuntime"></span></div><span class="agent-caption" id="personalAssistantCaption">独立讨论 · 长期记忆 · NR 全库</span></div>
      <div class="agent-actions"><button type="button" class="agent-btn" id="personalAssistantNew" aria-label="新讨论" title="新讨论">＋</button><button type="button" class="agent-btn" id="personalAssistantSettingsBtn" aria-label="个人助手设置" title="个人助手设置">${icon("settings")}</button><button type="button" class="agent-btn" id="personalAssistantClose" aria-label="关闭个人助手" title="关闭">✕</button></div></div>
      <div class="agent-context" id="personalThreadPicker"></div>
      <div class="agent-chat-stream" id="personalAssistantMessages" role="log" aria-label="个人助手讨论" aria-live="off" tabindex="0"></div>
      <div class="agent-context"><p id="personalAssistantStatus" role="status" aria-live="polite"></p><button type="button" class="reader-tool-btn" id="personalAssistantReload" hidden>重新读取</button></div>
      <div class="agent-input-row"><textarea class="agent-input" rows="1" data-auto-rows data-auto-min-rows="1" data-auto-max-rows="5" id="personalAssistantInput" aria-label="与个人助手对话" aria-describedby="personalAssistantInputHint" placeholder="聊聊新闻，或继续之前的想法…" maxlength="20000"></textarea><span class="sr-only" id="personalAssistantInputHint">电脑按 Enter 发送，Shift Enter 换行；手机按发送按钮或 Ctrl / Command Enter 发送。</span><button type="button" class="agent-send-btn" id="personalAssistantSend" aria-label="发送消息">${icon("send")}</button></div>`;
    this.input = $("#personalAssistantInput");
    this.popover = new AnchoredPopover({panel:this.panel, trigger:this.trigger,
      owns:target=>this.panel.contains(selects.button) && selects.menu.contains(target),
      onClose:()=>{clearTimeout(this.timer); this.timer=null;}});
    this.trigger.onclick = () => this.popover.open ? this.popover.close(true) : this.show();
    $("#personalAssistantClose").onclick = () => this.popover.close(true);
    $("#personalAssistantNew").onclick = () => this.newDiscussion();
    $("#personalAssistantSettingsBtn").onclick = () => {this.popover.close(); this.onSettings();};
    $("#personalAssistantReload").onclick = () => this.load();
    $("#personalAssistantSend").onclick = () => active(this.snapshot?.job) ? this.stop() : this.send();
    this.input.oninput = () => {this.drafts.set(this.threadId || "new", this.input.value); this.resize(); this.renderStatus();};
    this.input.onkeydown = event => {
      if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229 &&
          (event.ctrlKey || event.metaKey || (!matchMedia("(max-width: 920px)").matches && !event.shiftKey))) {
        event.preventDefault(); void this.send();
      }
    };
    this.panel.addEventListener("change", event => {
      if (event.target.name !== "personal.thread") return;
      if (event.target.value === "more") { void this.loadMoreThreads(); return; }
      if (!event.target.value) this.newDiscussion(); else void this.selectThread(event.target.value);
    });
    this.panel.addEventListener("click", event => {
      if (event.target.closest("[data-personal-older]")) void this.loadOlder();
      if (event.target.closest("[data-personal-name]")) {this.popover.close();this.onSettings();}
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
  async show() {
    this.popover.show(); this.resize();
    // Do not summon the mobile keyboard just by opening the assistant.
    if (!matchMedia("(max-width: 920px)").matches) this.input.focus({preventScroll:true});
    else $("#personalAssistantClose").focus({preventScroll:true});
    await this.load();
  }
  async load() {
    const seq = ++this.sequence; this.loading = true; this.notice = "正在读取个人助手…"; this.renderStatus();
    try {
      const [{profile}, listing] = await Promise.all([api(base+"/profile"), api(base+"/threads")]);
      if (seq !== this.sequence) return;
      this.profile = profile; this.threads = listing.threads; this.hasMoreThreads = listing.has_more; this.activeJob = listing.active_job;
      if (!this.creatingNew && !this.threadId) this.threadId = listing.active_job?.thread_id || this.threads[0]?.id || "";
      if (this.threadId) {
        try { const data = await api(base+"/threads/"+this.threadId); if (seq === this.sequence) this.snapshot = data; }
        catch (error) { if (error.httpStatus !== 404) throw error; this.threadId=""; this.snapshot=null; }
        if (seq !== this.sequence) return;
      } else this.snapshot = null;
      this.input.value = this.drafts.get(this.threadId || "new") || "";
      this.notice = ""; this.loadFailed = false; this.renderIdentity(); this.renderThreads(); this.renderMessages(true); this.resize();
    } catch (error) { if (seq === this.sequence) {this.notice=errorText(error);this.loadFailed=true;} }
    finally { if (seq === this.sequence) {this.loading=false;this.renderStatus();this.schedule();} }
  }
  renderIdentity() {
    $("#personalAssistantName").textContent = this.profile?.name || "个人助手";
    $("#personalAssistantRuntime").textContent = this.profile?.runtime === "codex" ? "Codex" : "Pi";
    this.trigger.title = this.profile?.name ? `与${this.profile.name}对话` : "个人助手";
  }
  renderThreads() {
    const options = this.threads.map(t=>({value:t.id,label:t.title + (t.current_library ? "" : " · 其他新闻库")}));
    if (this.snapshot && !options.some(x=>x.value === this.threadId)) options.unshift({value:this.threadId,label:this.snapshot.thread.title});
    if (this.hasMoreThreads) options.push({value:"more",label:"载入更早讨论…"});
    $("#personalThreadPicker").innerHTML = selectField("讨论", "personal.thread", this.threadId, options, "新讨论");
    this.selects.refresh();
  }
  async loadMoreThreads() {
    try {
      const listing = await api(base+"/threads?offset="+this.threads.length);
      const ids = new Set(this.threads.map(t=>t.id));
      this.threads.push(...listing.threads.filter(t=>!ids.has(t.id))); this.hasMoreThreads=listing.has_more; this.renderThreads();
    } catch (error) {this.notice=errorText(error);this.renderStatus();}
  }
  async selectThread(id) {
    this.threadId=id; this.creatingNew=false; persist(lastThreadKey,id); this.snapshot=null;
    this.input.value=this.drafts.get(id)||"";
    await this.load();
  }
  newDiscussion() {
    ++this.sequence; clearTimeout(this.timer); this.loading=false; this.threadId=""; this.creatingNew=true; this.snapshot=null;
    this.notice=""; this.loadFailed=false; persist(lastThreadKey,""); this.input.value=this.drafts.get("new")||"";
    this.renderThreads(); this.renderMessages(true); this.renderStatus(); this.resize(); this.schedule();
    if (!matchMedia("(max-width: 920px)").matches) this.input.focus({preventScroll:true});
  }
  renderMessages(scroll = false) {
    const host=$("#personalAssistantMessages"), nearBottom=host.scrollHeight-host.scrollTop-host.clientHeight<60;
    const messages=this.snapshot?.messages||[];
    if (!messages.length) {
      host.innerHTML=this.profile?.name
        ? `<p class="agent-empty-caption">我是${e(this.profile.name)}。<br>可以查阅 NR 新闻，和你梳理想法、依据与预判。<br>值得保留的理解会整理进长期记忆。</p>`
        : `<div class="agent-empty-caption section-stack"><p>为你的个人助手取一个名字，<br>再开始第一段讨论。</p><button type="button" class="btn-secondary" data-personal-name>为助手命名</button></div>`;
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
      const text=message.content || (active(this.snapshot.job) && message.role === "assistant" ? "正在整理资料…" : "本轮没有生成回复");
      const signature=JSON.stringify([text,message.status,this.profile?.name]);
      if(node.signature !== signature) {
        node.innerHTML=`<span class="agent-caption">${message.role === "user" ? "你" : e(this.profile?.name || "个人助手")}${["failed","stopped"].includes(message.status) ? " · 未完成" : ""}</span><div class="agent-bubble">${markdown(text)}</div>`;
        node.signature=signature;
      }
    }
    if(scroll||nearBottom) host.scrollTop=host.scrollHeight;
  }
  renderStatus() {
    const job=this.snapshot?.job, busy=active(job), other=active(this.activeJob) && this.activeJob.thread_id !== this.threadId;
    const needsName=!!this.profile && !this.profile.name?.trim();
    $("#personalAssistantStatus").textContent=this.notice || (needsName ? "名字由你定义，可在个人助手设置中填写" : busy ? (job.stop_requested ? "正在停止…" : "正在查阅资料、整理记忆并回复…") : other ? "另一条讨论正在回复，可切回查看或停止" : this.snapshot?.thread.current_library === false ? "这条讨论属于另一新闻库，请开启新讨论" : job?.error_text || (job?.status === "done" ? "本轮已完成 · 讨论已保存" : "每个讨论独立，长期记忆共用"));
    $("#personalAssistantStatus").classList.toggle("error-state", !!this.loadFailed || job?.status === "failed");
    $("#personalAssistantReload").hidden=!this.loadFailed;
    const button=$("#personalAssistantSend"); button.innerHTML=icon(busy?"stop":"send");
    button.setAttribute("aria-label",busy?"停止回复":"发送消息");button.title=busy?"停止回复":"发送消息";
    button.disabled=needsName || this.sending || this.loading || (busy ? !!job.stop_requested : other || !this.input.value.trim() || this.snapshot?.thread.current_library === false);
    this.input.disabled=needsName || !!this.sending || this.snapshot?.thread.current_library === false;
    $("#personalAssistantNew").disabled=needsName || !!this.sending;
    const picker=$("#personalThreadPicker select");
    if (picker && picker.disabled !== !!this.sending) {picker.disabled=!!this.sending;this.selects.refresh();}
  }
  async send() {
    const text=this.input.value.trim();
    if(!this.profile?.name?.trim() || !text || this.sending || this.loading || active(this.activeJob) || active(this.snapshot?.job)) return;
    this.sending=true;this.notice="正在保存消息…";this.renderStatus();clearTimeout(this.timer);
    const originalKey=this.threadId||"new";
    try {
      if(!this.threadId) {
        const {thread}=await api(base+"/threads",{method:"POST",body:{}});
        this.threadId=thread.id;this.creatingNew=false;this.threads.unshift(thread);persist(lastThreadKey,thread.id);
        this.drafts.set(thread.id,text);
      }
      this.pendingRequest ||= {id:requestId(),text,thread:this.threadId};
      if(this.pendingRequest.text !== text || this.pendingRequest.thread !== this.threadId) this.pendingRequest={id:requestId(),text,thread:this.threadId};
      this.snapshot=await api(base+"/threads/"+this.threadId+"/messages",{method:"POST",body:{text,request_id:this.pendingRequest.id}});
      this.pendingRequest=null;this.activeJob=active(this.snapshot.job)?this.snapshot.job:null;
      this.input.value="";this.drafts.delete(originalKey);this.drafts.delete(this.threadId);this.notice="";
      const thread=this.threads.find(t=>t.id===this.threadId); if(thread) Object.assign(thread,this.snapshot.thread);
      this.renderThreads();this.renderMessages(true);
    } catch(error) {this.notice=errorText(error);}
    finally {this.sending=false;this.renderStatus();this.resize();this.schedule();if(this.popover.open && !matchMedia("(max-width: 920px)").matches)this.input.focus({preventScroll:true});}
  }
  async stop() {
    if(!this.threadId)return;
    try {this.snapshot=await api(base+"/threads/"+this.threadId+"/stop",{method:"POST",body:{}});this.notice="";}
    catch(error){this.notice=errorText(error);}
    this.renderStatus();this.schedule();
  }
  schedule() {
    clearTimeout(this.timer);
    if(this.popover.open && !document.hidden && (active(this.activeJob)||active(this.snapshot?.job))) this.timer=setTimeout(()=>this.poll(),1000);
  }
  async poll() {
    const seq=this.sequence, id=this.activeJob?.thread_id||this.threadId;
    if(!id)return;
    try {
      const data=await api(base+"/threads/"+id);
      if(seq!==this.sequence)return;
      this.activeJob=active(data.job)?data.job:null;
      if(id===this.threadId) {
        const first=data.messages[0]?.id || 0, older=(this.snapshot?.messages||[]).filter(m=>m.id<first);
        this.snapshot={...data,messages:[...older,...data.messages],has_more:older.length ? this.snapshot.has_more : data.has_more};
        this.renderMessages();
      }
      this.notice="";this.loadFailed=false;
    } catch(error){if(seq===this.sequence){this.notice=errorText(error);this.loadFailed=true;}}
    if(seq===this.sequence){this.renderStatus();this.schedule();}
  }
  async loadOlder() {
    const id=this.threadId, first=this.snapshot?.messages[0]?.id;
    if(!first)return;
    try {
      const data=await api(base+"/threads/"+id+"?before="+first);
      if(this.threadId!==id)return;
      const host=$("#personalAssistantMessages"), height=host.scrollHeight, top=host.scrollTop;
      this.snapshot.messages=[...data.messages,...this.snapshot.messages];this.snapshot.has_more=data.has_more;
      host.replaceChildren();this.renderMessages();host.scrollTop=top+host.scrollHeight-height;
    } catch(error){this.notice=errorText(error);this.renderStatus();}
  }
}
