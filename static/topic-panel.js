import { $, api, escapeHTML as e, errorText, markdown, toast, singlePane, clipDateGroupHeaders, ListSearch, icon } from "./ui.js";
import { confirmAction } from "./confirm-dialog.js";

const activeJob = job => job && ["pending", "running"].includes(job.status);
const statusLabel = { defining: "正在明确主题", draft: "待确认定义", initializing: "正在整理历史", ready: "持续追踪" };
const jobErrors = {
  topic_search_required: "未完成联网检索，请检查 Agent 的搜索能力后重试。",
  topic_interrupted: "上次整理中断，可以重试。",
  topic_invalid_result: "整理结果不完整，请重试。",
  topic_invalid_input: "模型返回的日期或内容不符合要求，请重试。",
  topic_source_changed: "新闻资料已变化，请重新整理。",
  topic_runtime_failed: "整理失败，请检查模型与 Agent 设置后重试。",
  topic_timeline_too_large: "现有脉络资料较多，暂时无法一次精简。",
};
const uuid = () => globalThis.crypto.randomUUID?.() || [...crypto.getRandomValues(new Uint8Array(20))].map(n => n.toString(16).padStart(2, "0")).join("");
const field = (name, label, value, limit, rows = 0) => '<label class="field-item"><span class="field-label">' + label + '</span>' +
  (rows ? '<textarea name="' + name + '" maxlength="' + limit + '" rows="' + rows + '" class="field-input">' + e(value) + '</textarea>'
    : '<input name="' + name + '" maxlength="' + limit + '" value="' + e(value) + '" class="field-input">') + '</label>';
const formHeading = (title, description = "") => '<div class="settings-detail-heading"><h2>' + e(title) + '</h2></div>' +
  (description ? '<p class="muted">' + e(description) + '</p>' : "");
const definitionFields = d => field("name","主题名称",d.name,120)+field("definition","主题定义",d.definition,1500,3)+
  '<div class="form-group-grid">'+field("include_scope","纳入范围",d.include_scope,1000,3)+field("exclude_scope","排除范围",d.exclude_scope,1000,3)+'</div>'+
  field("horizontal_rule","横向收录规则",d.horizontal_rule,2000,4);
const timelineIcon = '<svg class="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 6v6l3 2"/></svg>';

export class TopicPanel {
  constructor({ isVisible, enterReader, onViewChange, openArticle, closeArticle, readLaterAction, toggleReadLater, stateVersion, reconcileItem }) {
    Object.assign(this, { isVisible, enterReader, onViewChange, openArticle, closeArticle, readLaterAction, toggleReadLater, stateVersion, reconcileItem });
    this.entries = []; this.selected = ""; this.listSelected = ""; this.showDefinition = false; this.data = null; this.page = "topic"; this.reading = false;
    this.drafts = new Map(); this.draftChanges = new Set(); this.query = ""; this.requestId = ""; this.node = ""; this.filter = "";
    this.chatDrafts = new Map(); this.chatRequests = new Map(); this.chatErrors = new Map(); this.draftExpanded = new Set();
    this.listSequence = 0; this.detailSequence = 0; this.busy = false; this.loaded = false; this.viewed = new Map(); this.listFilter = "all";
    this.searchQuery = "";
    this.search = new ListSearch({ column: $("#feedColTopics"), label: "主题搜索", onCollapse: () => {
      $("#topicSearchInput").value = "";
      this.setSearchQuery("");
    } });
    this.search.input.addEventListener("input", event => this.setSearchQuery(event.target.value));
    this.content = $("#topicContent");
    $("#newTopicBtn").onclick = () => this.newTopic();
    $("#topicFilters").onclick = event => {
      const button = event.target.closest("[data-topic-filter]");
      if (button) this.setListFilter(button.dataset.topicFilter);
    };
    $("#topicFilters").onkeydown = event => {
      const buttons = [...$("#topicFilters").querySelectorAll("button")], index = buttons.indexOf(event.target);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
    };
    $("#topicReaderBackBtn").onclick = () => this.returnFromArticle();
    $("#topicCardList").onclick = event => {
      const card = event.target.closest("[data-topic-id]");
      if (card) this.select(card.dataset.topicId, true);
      if (event.target.closest("[data-topic-action='reload']")) this.load();
    };
    this.content.addEventListener("input", event => {
      if (event.target.id === "topicChatInput") {
        this.chatDrafts.set(this.selected,event.target.value); this.syncChatControls(); return;
      }
      if (event.target.name === "query") { this.query = event.target.value; this.requestId = ""; return; }
      const draft = this.drafts.get(this.selected);
      if (draft && event.target.name) { draft[event.target.name] = event.target.value; this.draftChanges.add(this.selected); }
    });
    this.content.addEventListener("change", event => {
      const draft = this.drafts.get(this.selected);
      if (draft && event.target.name) { draft[event.target.name] = event.target.value; this.draftChanges.add(this.selected); }
    });
    this.content.addEventListener("submit", event => {
      event.preventDefault(); if (!this.busy) event.target.id === "topicChatForm" ? this.sendMessage() : this.submit();
    });
    this.content.addEventListener("keydown", event => {
      if (event.target.id !== "topicChatInput" || event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
      if (event.shiftKey || (singlePane.matches && !event.ctrlKey && !event.metaKey)) return;
      event.preventDefault(); this.sendMessage();
    });
    this.content.addEventListener("toggle", event => {
      if (event.target.id === "topicDraftDetails" && event.target.isConnected) {
        event.target.open ? this.draftExpanded.add(this.selected) : this.draftExpanded.delete(this.selected);
      }
    }, true);
    this.content.addEventListener("click", event => this.click(event));
    $("#topicToolbar").addEventListener("click", event => this.click(event));
    this.bindTimelineControls();
    $("#topicScrollArea").addEventListener("scroll", () => this.scheduleDateHeaders(), {passive:true});
    this.contentObserver = new ResizeObserver(() => { this.scheduleDateHeaders(); this.syncTimelineSlider(); });
    this.contentObserver.observe(this.content);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && this.isVisible()) this.refresh(); });
  }
  schedule() {
    clearTimeout(this.timer);
    if (!this.isVisible() || document.hidden) return;
    const working = this.entries.some(t => activeJob(t.job)) || activeJob(this.data?.job) || Object.entries(this.data?.matching || {}).some(([k,n]) => ["pending","running"].includes(k) && n);
    this.timer = setTimeout(() => this.refresh(), working ? 2000 : 15000);
  }
  deactivate() { clearTimeout(this.timer); this.listSequence++; this.detailSequence++; }
  matchesList(topic) {
    return this.listFilter === "all" || (this.listFilter === "archived" ? !!topic.archived_at : topic.status === "ready" && !topic.archived_at);
  }
  visibleEntries() {
    const terms = this.searchQuery.toLocaleLowerCase().split(/\s+/u).filter(Boolean);
    return this.entries.filter(topic => {
      const text = [topic.name, topic.definition].filter(Boolean).join(" ").toLocaleLowerCase();
      return this.matchesList(topic) && terms.every(term => text.includes(term));
    });
  }
  setSearchQuery(value) {
    this.searchQuery = value.trim();
    this.renderList();
    $("#topicListScroll").scrollTop = 0;
  }
  async setListFilter(filter) {
    if (!["all", "tracking", "archived"].includes(filter)) return;
    const creating = ["new", "create", "complete", "edit"].includes(this.page);
    if (this.listFilter === filter && !creating) return;
    this.leaveDetail();
    this.listFilter = filter;
    if (!this.visibleEntries().some(topic => topic.id === this.selected)) {
      this.detailSequence++; this.selected = ""; this.data = null; this.page = "list";
      this.reading = false; this.closeArticle(); this.render(); this.onViewChange();
    }
    this.renderList(); $("#topicListScroll").scrollTop = 0;
    if (!creating && !this.selected && this.visibleEntries().length && !singlePane.matches)
      await this.select(this.visibleEntries()[0].id, false);
  }
  async refresh() {
    if (!this.isVisible() || document.hidden) return;
    await this.load({ quiet: true });
    if (this.selected && this.page !== "new") await this.fetchDetail({ quiet: true });
    this.schedule();
  }
  async load({ quiet = false } = {}) {
    const sequence = ++this.listSequence;
    try {
      const data = await api("/api/topics");
      if (sequence !== this.listSequence) return;
      this.entries = data.topics;
      const creating = ["new", "create", "complete", "edit"].includes(this.page);
      if (this.listSelected && !this.entries.some(t=>t.id===this.listSelected && this.matchesList(t))) this.listSelected = "";
      if(this.selected && (!this.entries.some(t=>t.id===this.selected) || (!creating && !this.entries.some(t=>t.id===this.selected && this.matchesList(t))))){
        this.detailSequence++;this.selected="";this.data=null;this.page="topic";this.reading=false;this.closeArticle();this.render();this.onViewChange();
      }
      this.configured = data.matching_configured; this.matchingError = data.matching_error; this.loaded = true;
      this.renderList();
      if (!this.selected && this.page === "topic" && this.visibleEntries().length && !singlePane.matches && this.isVisible())
        await this.select(this.visibleEntries()[0].id, false);
    } catch (error) {
      if (sequence !== this.listSequence) return;
      if (!quiet || !this.loaded) $("#topicCardList").innerHTML = '<div class="list-message" role="alert">' + e(errorText(error)) + '<button class="btn-secondary" data-topic-action="reload" type="button">重试</button></div>';
    } finally { this.schedule(); }
  }
  renderList() {
    $("#topicFilters").querySelectorAll("[data-topic-filter]").forEach(button => {
      const active = button.dataset.topicFilter === this.listFilter;
      button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active));
    });
    const entries = this.visibleEntries();
    const html = entries.length ? entries.map(t => '<button class="news-card topic-card' + (this.listSelected===t.id ? ' is-active' : '') + '" data-topic-id="' + e(t.id) + '" aria-pressed="' + (this.listSelected===t.id) + '" type="button">' +
      '<span class="news-card-meta"><span class="meta-source-box">'+e(t.archived_at ? "已归档" : statusLabel[t.status])+'</span><span class="meta-time-row">' +
      (t.job?.status==="failed" ? "整理失败" : t.new_count ? t.new_count+" 条新新闻" : t.news_count+" 条新闻") +
      (t.new_count ? '<span class="unread-dot" aria-label="有新新闻"></span>' : '') + '</span></span>' +
      '<strong class="news-card-title">' + e(t.name) + '</strong><span class="news-card-snippet">' + e(t.overview || t.definition || t.query) + '</span></button>').join("") :
      '<div class="list-message">' + (this.searchQuery ? "没有匹配的主题，试试其他关键词。" : {all:"还没有追踪主题",tracking:"暂无持续跟踪的主题",archived:"暂无已归档的主题"}[this.listFilter]) + '</div>';
    const list = $("#topicCardList");
    if (list.innerHTML !== html) {
      const focusId = document.activeElement?.dataset.topicId;
      list.innerHTML = html;
      if (focusId) list.querySelector('[data-topic-id="'+CSS.escape(focusId)+'"]')?.focus({preventScroll:true});
    }
  }
  newTopic() {
    this.detailSequence++; this.reading = false; this.closeArticle();
    this.node = ""; this.filter = ""; this.showDefinition = false;
    this.selected = ""; this.data = null; this.page = "new"; this.renderList(); this.render(); this.onViewChange(); this.enterReader();
    this.content.querySelector("textarea")?.focus({preventScroll:true});
  }
  async select(id, mobile = true) {
    const entry = this.entries.find(topic => topic.id === id);
    if (entry && !this.matchesList(entry)) this.listFilter = "all";
    this.selected = id; this.listSelected = id; this.node = ""; this.filter = ""; this.reading = false; this.closeArticle();
    this.showDefinition = false; this.data = null; this.page = "topic"; this.renderList(); this.render(); this.onViewChange();
    if (mobile) this.enterReader();
    await this.fetchDetail();
  }
  async fetchDetail({ quiet = false, append = false, forceRender = false } = {}) {
    if (!this.selected) return;
    const id = this.selected, sequence = ++this.detailSequence;
    const stateVersion = this.stateVersion();
    const params = new URLSearchParams();
    if (this.filter) params.set("node", this.filter);
    if (append) params.set("offset", this.data?.items.length || 0);
    else if (quiet) params.set("limit", Math.min(400, Math.max(40, this.data?.items.length || 0)));
    try {
      const data = await api("/api/topics/"+encodeURIComponent(id)+"?"+params);
      const keep = quiet && !append ? Math.min(this.data?.items.length || 0, data.total) : 0;
      while(data.items.length < keep && sequence === this.detailSequence){
        params.set("offset",data.items.length);params.set("limit",Math.min(400,keep-data.items.length));
        const next=await api("/api/topics/"+encodeURIComponent(id)+"?"+params);
        if(!next.items.length)break;
        data.items.push(...next.items);data.has_more=next.has_more;
      }
      if (sequence !== this.detailSequence || id !== this.selected) return;
      for (const item of data.items) this.reconcileItem(item, stateVersion);
      const previous = this.data, oldPage = this.page;
      if (append && previous) data.items = [...previous.items, ...data.items];
      this.data = data;
      if (data.topic.status !== "ready") {
        this.page = "create";
        if (data.topic.status==="draft" && !this.draftChanges.has(id)) this.drafts.set(id, {...data.topic});
      } else if (oldPage==="create" && previous?.topic.status==="initializing") this.page = "complete";
      if (!data.nodes.some(node => node.id === this.node)) this.node = data.nodes.at(-1)?.id || "";
      const changed = JSON.stringify({...previous, observed_at:0}) !== JSON.stringify({...data, observed_at:0});
      if (forceRender || !quiet || oldPage !== this.page || (this.page !== "edit" && (this.renderedMatching !== JSON.stringify([this.configured,this.matchingError]) || (changed && this.page !== "create") || previous?.topic.rules_revision !== data.topic.rules_revision || previous?.job?.id !== data.job?.id || previous?.job?.status !== data.job?.status || previous?.topic.status !== data.topic.status))) {
        this.renderList();
        this.render({preserveScroll:quiet || append});
      }
      if (this.isVisible() && data.topic.status==="ready" && this.viewed.get(id) !== data.total &&
          (!singlePane.matches || $("#feedView").classList.contains("mobile-reader-active"))) {
        this.viewed.set(id,data.total); api("/api/topics/"+id+"/seen", {method:"POST",body:{observed_at:data.observed_at}}).catch(()=>{});
      }
    } catch(error) {
      if(sequence!==this.detailSequence)return;
      if (this.filter && (error.code || error.message) === "topic_node_not_found") {
        this.filter = ""; this.node = ""; await this.fetchDetail({quiet}); return;
      }
      if (!quiet || !this.data) this.content.innerHTML = '<div class="list-message" role="alert">' + e(errorText(error)) + '<button class="btn-secondary" type="button" data-topic-action="retry-detail">重试</button></div>';
    } finally { this.schedule(); }
  }
  render({preserveScroll=false}={}) {
    this.renderToolbar();
    const area=$("#topicScrollArea"), scroll=area.scrollTop;
    const timelineScroll=this.content.querySelector(".topic-timeline")?.scrollLeft;
    const focus=document.activeElement;
    const focusSelector=focus && this.content.contains(focus) ? ["data-topic-node","data-topic-item","data-topic-read-later","data-related-topic","data-topic-action","name","id"].find(key=>focus.hasAttribute(key)) : null;
    const focusValue=focusSelector && focus.getAttribute(focusSelector);
    const selection=focusSelector && typeof focus.selectionStart === "number" ? [focus.selectionStart,focus.selectionEnd] : null;
    const chat=this.content.querySelector("#topicChatStream"), chatScroll=chat?.scrollTop || 0;
    const chatAtEnd=!chat || chat.scrollHeight-chat.scrollTop-chat.clientHeight<48;
    this.renderedMatching=JSON.stringify([this.configured,this.matchingError]);
    const t=this.data?.topic, job=this.data?.job;
    const error = job?.status==="failed" ? '<div class="button-row error-state topic-error" role="alert"><p>'+e(job.kind==="milestone" ? "横向筛选失败，请检查 Jev 配置后重试。已收录新闻保留。" : jobErrors[job.error] || "整理失败，请检查模型与 Agent 设置。")+'</p><button class="btn-secondary" type="button" data-topic-action="retry">重试</button></div>' : "";
    let html;
    if(this.page==="new") {
      html=formHeading("新建主题", "输入事件，联网生成主题定义。")+'<form class="settings-sheet" id="topicCreateForm">'+field("query","事件或主题",this.query,1500,5)+
        '<div class="button-row"><button class="btn-secondary" type="button" data-topic-action="cancel">返回主题</button><button class="btn-primary" type="submit">搜索并生成定义</button></div><p class="error-state topic-form-error" role="alert"></p></form>';
    } else if(!t) {
      html='<div class="reader-empty"><h2>'+(this.selected?'正在读取主题…':'选择一个主题')+'</h2>'+(!this.selected?'<p>梳理历史脉络，收录后续相关新闻。</p><button class="btn-primary" type="button" data-topic-action="new">新建主题</button>':'')+'</div>';
    } else if(this.page==="edit") {
      html=formHeading("编辑主题定义","保存后用于之后新入库的新闻，已有报道及节点保留。")+
        '<form class="settings-sheet" id="topicDefinitionForm">'+definitionFields(this.drafts.get(t.id))+
        '<div class="button-row"><button class="btn-secondary" type="button" data-topic-action="cancel-edit">取消</button><button class="btn-primary" type="submit">保存修改</button></div><p class="error-state topic-form-error" role="alert"></p></form>';
    } else if(this.page==="complete") {
      html=formHeading("主题已创建",t.name)+'<dl class="form-group-grid topic-success"><div class="field-item"><dt class="field-label">历史脉络</dt><dd>'+this.data.nodes.length+' 个关键节点</dd></div><div class="field-item"><dt class="field-label">后续时间线</dt><dd>'+(t.timeline_mode==="monthly"?'月度总结':'重要进展')+'</dd></div><div class="field-item"><dt class="field-label">开始追踪</dt><dd>从现在起的新入库新闻</dd></div></dl><div class="button-row"><button class="btn-secondary" type="button" data-topic-action="new">再建一个</button><button class="btn-primary" type="button" data-topic-action="open">查看主题</button></div>';
    } else if(this.page==="create") {
      if(t.status==="draft") {
        const d=this.drafts.get(t.id), waiting=activeJob(job), conflict=d.rules_revision!==t.rules_revision;
        html=formHeading("确认你的追踪范围")+
          '<div class="agent-chat-stream agent-chat-stream--inline" id="topicChatStream" role="log" aria-label="主题定义对话" aria-live="polite" aria-relevant="additions text">'+
          (this.data.messages || []).map(m=>'<div class="agent-msg '+(m.role==="user"?'user':'assistant')+'" data-message-id="'+e(m.id)+'"><div class="agent-bubble">'+markdown(m.content)+'</div></div>').join("")+
          (waiting?'<p class="muted" role="status">Agent 正在回复…</p>':'')+'</div>'+error+
          '<details id="topicDraftDetails"'+(this.draftExpanded.has(t.id)?' open':'')+'><summary class="btn-secondary">查看或直接修改当前定义</summary>'+
          '<form class="settings-sheet" id="topicCreateForm"><fieldset class="field-options" '+(waiting?'disabled':'')+'>'+definitionFields(d)+
          '<label class="field-item"><span class="field-label">历史起点</span><input type="date" name="start_date" class="field-input" value="'+e(d.start_date)+'" required></label>'+
          '</fieldset></form></details>'+
          (conflict?'<div class="button-row error-state" role="alert">定义已更新，当前编辑内容已保留。<button class="btn-secondary" type="button" data-topic-action="reload-draft">载入最新定义</button></div>':'')+
          '<form id="topicChatForm" class="agent-input-row"><textarea id="topicChatInput" class="agent-input" rows="2" maxlength="4000" aria-label="讨论主题定义" aria-describedby="topicChatHint" placeholder="询问细节，或说说你希望如何调整…" enterkeyhint="enter">'+e(this.chatDrafts.get(t.id)||'')+'</textarea><button class="agent-send-btn" type="submit">发送</button></form>'+
          '<span id="topicChatHint" class="sr-only">'+(singlePane.matches?'点发送或按 Ctrl / Command + Enter 发送，Enter 换行。':'Enter 发送，Shift + Enter 换行。')+'</span><p class="error-state topic-chat-error" role="alert">'+e(this.chatErrors.get(t.id)||'')+'</p>'+
          '<div class="button-row"><button class="btn-secondary" type="button" data-topic-action="cancel">返回主题</button><button class="btn-primary" type="submit" form="topicCreateForm">确认并整理历史</button></div><p class="error-state topic-form-error" role="alert"></p>';
      } else {
        html=formHeading(t.status==="defining"?'正在明确追踪范围':'正在梳理重要进展',t.name)+'<p class="muted" role="status">'+
          (activeJob(job)?(job.status==="pending"?'等待研究任务执行…':'正在联网研究，请稍候…'):'')+'</p>'+error+
          '<p class="muted">历史整理完成后，开始收录新入库的相关新闻。</p><div class="button-row"><button class="btn-secondary" type="button" data-topic-action="cancel">返回主题</button></div>';
      }
    } else {
      const nodes=this.data.nodes, node=nodes.find(n=>n.id===this.node), filteredNode=nodes.find(n=>n.id===this.filter);
      html='<div class="section-stack"><header class="settings-section-card settings-section-card--compact"><div class="article-meta-top"><span class="status-pill'+(t.archived_at?'':' is-active')+'">'+(t.archived_at?'已归档':'持续跟踪中')+'</span><time datetime="'+e(t.updated_at)+'">更新于 '+e(t.updated_at.replace('T',' ').slice(0,16))+'</time></div><h1 class="article-title">'+e(t.name)+'</h1>'+
        '<div class="article-body muted"><p>'+e(t.definition)+'</p></div></header>'+
        '<section class="settings-section-card settings-section-card--compact topic-history" aria-labelledby="topicHistoryTitle"><h2 class="section-card-title" id="topicHistoryTitle"><span class="section-title-label">'+timelineIcon+'重要进展</span><span class="reader-action-toolbar">'+
        (!t.archived_at && !t.refine_cooldown && nodes.filter(n=>n.kind!=="month").length>1 ? '<button class="btn-secondary" type="button" data-topic-action="refine" title="使用 Agent 合并已有阶段节点并改写概括，保留关联新闻，不联网" '+(activeJob(job)||job?.status==="failed"?'disabled':'')+'>精简脉络</button>' : '')+'</span></h2>'+
        '<div class="topic-timeline" id="topicTimeline" role="region" tabindex="0" aria-label="事件时间线，可使用底部滑块或方向键浏览">'+nodes.map(n=>'<button class="news-card topic-node'+(n.id===this.node?' is-active':'')+'" type="button" data-topic-node="'+e(n.id)+'" data-has-news="'+(n.news_count>0)+'" aria-pressed="'+(n.id===this.node)+'"><time class="meta-time-row">'+e(n.kind==="month"?n.date.slice(0,7):n.date)+'</time><span class="topic-node-track"><span class="topic-node-dot"></span></span><strong class="news-card-title">'+e(n.title)+'</strong><span class="news-card-meta">'+(n.news_count>0?'查看 '+n.news_count.toLocaleString()+' 条报道 →':(n.kind==='history'?'历史概述':'节点概述')+' · 暂无报道')+'</span></button>').join("")+'</div>'+
        '<input class="scroll-slider" id="topicTimelineSlider" type="range" min="0" max="1000" step="1" value="0" aria-label="重要进展浏览位置" aria-controls="topicTimeline">'+
        (node?'<div class="article-body" aria-live="polite"><p><strong>'+e(node.date)+' · '+e(node.title)+'</strong></p><p>'+e(node.body)+'</p></div>':'')+'</section>'+error+
        (activeJob(job)&&job.kind==="update"?'<p class="muted topic-status" role="status">正在整理新进展…</p>':'')+
        (activeJob(job)&&job.kind==="milestone"?'<p class="muted topic-status" role="status">正在筛选横向重要进展…</p>':'')+
        (activeJob(job)&&job.kind==="refine"?'<p class="muted topic-status" role="status">正在合并阶段里程碑…</p>':'')+
        (t.archived_at?'<p class="muted topic-status" role="status">已归档，停止收录新新闻。恢复后从新的入库新闻继续跟踪。</p>':!this.configured?'<p class="muted topic-status">Jev 尚未配置，相关新闻归属等待处理。请在 API 管理中配置。</p>':this.matchingError?'<div class="button-row error-state topic-error" role="status">Jev 暂时无法完成主题判断，请检查 API 配置后重试。<button class="btn-secondary" type="button" data-topic-action="retry">重试</button></div>':'')+
        (!t.archived_at&&this.data.matching.failed?'<div class="button-row error-state topic-error" role="status">'+this.data.matching.failed+' 条新闻判断失败。<button class="btn-secondary" type="button" data-topic-action="retry">重试</button></div>':'')+
        '<section class="settings-section-card settings-section-card--compact" aria-labelledby="topicNewsTitle"><h2 class="section-card-title" id="topicNewsTitle"><span class="section-title-label">'+timelineIcon+(this.filter?'相关报道':'新闻时间线')+' <span class="muted">'+this.data.total+' 条</span></span>'+
        (this.filter?'<button class="btn-secondary" type="button" data-topic-action="all">全部新闻</button>':'')+'</h2>'+(filteredNode?'<p class="muted">'+e(filteredNode.title)+'</p>':'')+this.newsHTML()+
        (this.data.has_more?'<div class="feed-end"><button class="btn-secondary" type="button" data-topic-action="more">加载更多</button></div>':'')+'</section></div>';
    }
    this.content.innerHTML = ["new","create","complete","edit"].includes(this.page) ? '<div class="settings-sheet">'+html+'</div>' : html;
    this.syncChatControls();
    const newChat=this.content.querySelector("#topicChatStream");
    if(newChat)newChat.scrollTop=!chat && this.data.messages.length<=2 ? 0 : chatAtEnd ? newChat.scrollHeight : chatScroll;
    if(preserveScroll) {
      area.scrollTop=scroll;
      const timeline=this.content.querySelector(".topic-timeline");
      if(timeline)timeline.scrollLeft=timelineScroll || 0;
      if(focusSelector){
        const restored=this.content.querySelector("["+focusSelector+'="'+CSS.escape(focusValue)+'"]');
        restored?.focus({preventScroll:true});
        if(selection && restored?.setSelectionRange)restored.setSelectionRange(...selection);
      }
    } else {
      area.scrollTop=0;
      const selected=this.content.querySelector('.topic-node[aria-pressed="true"]');
      if(selected)selected.parentElement.scrollLeft=Math.max(0,selected.offsetLeft-selected.parentElement.offsetLeft-24);
    }
    this.syncTimelineSlider();
    this.scheduleDateHeaders();
  }
  scheduleDateHeaders() {
    if (this.dateHeadersFrame) return;
    this.dateHeadersFrame = requestAnimationFrame(() => {
      this.dateHeadersFrame = null;
      if (this.isVisible()) clipDateGroupHeaders(this.content);
    });
  }
  renderToolbar() {
    const t = this.data?.topic, creating = ["new", "create", "complete"].includes(this.page);
    const ready = t?.status === "ready" && !creating;
    $("#topicDefinitionBtn").hidden = !ready;
    $("#topicDefinitionBtn").setAttribute("aria-expanded", String(ready && this.page==="edit"));
    const archive = $("#topicArchiveBtn");
    archive.hidden = !ready; archive.dataset.topicAction = t?.archived_at ? "restore" : "archive";
    archive.textContent = t?.archived_at ? "恢复跟踪" : "归档主题";
    $("#topicDeleteBtn").hidden = !t;
    const steps = $("#topicCreationSteps");
    steps.hidden = !creating;
    if (creating) {
      const step = this.page === "complete" ? 4 : t?.status === "initializing" ? 3 : this.page === "new" ? 1 : 2;
      const html = ["输入主题", "确认定义", "整理历史", "创建完成"].map((name, index) =>
        '<li class="'+(step === index+1 ? 'current' : step > index+1 ? 'done' : '')+'"'+(step === index+1 ? ' aria-current="step"' : '')+'><span aria-hidden="true">'+(step > index+1 ? '✓' : index+1)+'</span>'+name+'</li>').join("");
      if (steps.innerHTML !== html) steps.innerHTML = html;
    }
  }
  syncTimelineSlider() {
    const timeline=this.content.querySelector('.topic-timeline'), slider=this.content.querySelector('#topicTimelineSlider');
    if(!timeline || !slider)return;
    const max=Math.max(0,timeline.scrollWidth-timeline.clientWidth);
    slider.disabled=max<=1;
    slider.value=max>1 ? String(Math.round(timeline.scrollLeft/max*1000)) : '0';
    slider.setAttribute('aria-valuetext',max>1 ? '浏览位置 '+Math.round(Number(slider.value)/10)+'%' : '全部节点已显示');
  }
  bindTimelineControls() {
    this.content.addEventListener('input',event=>{
      if(event.target.id!=='topicTimelineSlider')return;
      const timeline=this.content.querySelector('.topic-timeline');
      if(timeline)timeline.scrollLeft=Number(event.target.value)/1000*(timeline.scrollWidth-timeline.clientWidth);
      this.syncTimelineSlider();
    });
    this.content.addEventListener('scroll',event=>{
      if(event.target.matches('.topic-timeline'))this.syncTimelineSlider();
    },true);
    this.content.addEventListener("keydown", event => {
      const timeline = event.target.closest(".topic-timeline");
      if (!timeline || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const buttons = [...timeline.querySelectorAll(".topic-node")];
      if (!buttons.length) return;
      const current = buttons.indexOf(event.target);
      const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        current < 0 ? (event.key === "ArrowRight" ? 0 : buttons.length - 1) :
        Math.max(0, Math.min(buttons.length - 1, current + (event.key === "ArrowRight" ? 1 : -1)));
      buttons[index].focus({preventScroll:true});
      timeline.scrollLeft = Math.max(0, buttons[index].offsetLeft - timeline.offsetLeft - 4);
    });
  }
  updateItemState(id, patch) {
    const item = this.data?.items.find(row => row.id === id);
    if (!item) return;
    Object.assign(item, patch);
    const button = this.content.querySelector('[data-topic-read-later="'+CSS.escape(id)+'"]');
    if (!button) return;
    for (const [key, value] of Object.entries(this.readLaterAttributes(item))) button.setAttribute(key, value);
  }
  readLaterAttributes(item) {
    const action = this.readLaterAction(item);
    return {
      class: action.className + " card-action-btn--icon card-action-btn--plain" + (item.detail_ready ? "" : " card-action-btn--reveal"),
      title: action.title,
      "aria-label": action.title,
      "aria-pressed": String(action.active),
      "aria-busy": String(action.saving),
    };
  }
  newsHTML() {
    if(!this.data.items.length)return '<div class="list-message">'+ (this.filter?'此节点暂无已收录新闻。':this.data.topic.archived_at?'此主题暂无已收录新闻。':'等待新的相关新闻。主题创建完成后入库的匹配新闻会显示在这里。')+'</div>';
    const groups = new Map();
    for (const item of this.data.items) {
      const date = item.date || "日期未知";
      if (!groups.has(date)) groups.set(date, []);
      groups.get(date).push(item);
    }
    return '<div class="feed-date-cards timeline-flow">'+[...groups].map(([date, items]) =>
      '<section class="feed-date-group"><div class="feed-date-heading"><h3 class="feed-date-title">'+e(date)+'</h3></div><div class="feed-date-cards">'+items.map(item => {
        const related = item.topics.filter(t=>t.id!==this.selected);
        const action = Object.entries(this.readLaterAttributes(item)).map(([key,value])=>key+'="'+e(value)+'"').join(' ');
        return '<article class="news-card news-card--timeline topic-news">'+
          '<div class="news-card-meta"><span class="meta-source-box">'+e(item.source_name||item.source)+'</span><span class="meta-time-row">'+
          '<button '+action+' type="button" data-state="read_later" data-topic-read-later="'+e(item.id)+'" '+(!item.available?'disabled':'')+'>'+icon('read_later')+'</button><time>'+e(item.time||"")+'</time></span></div>'+
          '<button class="news-card-open topic-news-open" type="button" data-topic-item="'+e(item.id)+'" '+(!item.available?'disabled':'')+'>'+
          '<strong class="news-card-title">'+e(item.title)+'</strong><span class="news-card-snippet">'+e(item.summary)+'</span></button>'+
          (related.length?'<div class="news-card-footer"><div class="card-footer-left">'+related.map(t=>'<button class="btn-secondary" type="button" data-related-topic="'+e(t.id)+'">亦收录于 '+e(t.name)+'</button>').join("")+'</div></div>':'')+'</article>';
      }).join("")+'</div></section>').join("")+'</div>';
  }
  syncChatControls() {
    const form=$("#topicChatForm"); if(!form)return;
    const blocked=this.busy || activeJob(this.data?.job) || this.drafts.get(this.selected)?.rules_revision!==this.data?.topic.rules_revision;
    const text=(this.chatDrafts.get(this.selected)||'').trim();
    form.querySelector('button').disabled=blocked || !text;
    $("#topicCreateForm fieldset").disabled=blocked;
    this.content.querySelector('[form="topicCreateForm"]').disabled=blocked || !!text || this.data?.job?.status==="failed";
  }
  async sendMessage() {
    const id=this.selected, message=(this.chatDrafts.get(id)||'').trim();
    if(!message || this.busy || activeJob(this.data?.job) || this.data?.topic.status!=="draft")return;
    const draft=this.drafts.get(id);
    if(draft.rules_revision!==this.data.topic.rules_revision)return;
    let body=this.chatRequests.get(id);
    if(!body || body.message!==message) {
      body={message,request_id:uuid(),rules_revision:draft.rules_revision,definition:{...draft,timeline_mode:"events"}};
      this.chatRequests.set(id,body);
    }
    this.busy=true;this.syncChatControls();
    this.chatErrors.delete(id);
    const errorBox=this.content.querySelector('.topic-chat-error');errorBox.textContent='';
    try {
      await api('/api/topics/'+id+'/messages',{method:'POST',body});
      this.chatRequests.delete(id);this.draftChanges.delete(id);
      if((this.chatDrafts.get(id)||'').trim()===message)this.chatDrafts.delete(id);
      if(this.selected===id){await this.fetchDetail({quiet:true});await this.load({quiet:true});}
    } catch(error) {
      this.chatErrors.set(id,errorText(error));
      if(errorBox.isConnected)errorBox.textContent=errorText(error);else toast(errorText(error));
      if(['topic_draft_changed','topic_invalid_input','topic_not_draft'].includes(error.code)) {
        this.chatRequests.delete(id);
        if(this.selected===id)await this.fetchDetail({quiet:true});
      }
    } finally {this.busy=false;this.syncChatControls();}
  }
  async submit() {
    const form=$("#topicCreateForm, #topicDefinitionForm");
    if(!form||this.busy)return;
    if(this.data?.topic.status==="draft" && (activeJob(this.data.job) || this.data.job?.status==="failed" || (this.chatDrafts.get(this.selected)||'').trim()))return;
    if(!form.checkValidity()) {const details=$("#topicDraftDetails");if(details)details.open=true;form.reportValidity();return;}
    this.busy=true;
    const selected=this.selected, sequence=this.detailSequence;
    const submit=form.querySelector('[type="submit"]') || this.content.querySelector('[form="topicCreateForm"]'); submit.disabled=true;
    this.syncChatControls();
    try {
      if(this.page==="new"){
        if(!this.query.trim())throw new Error("请输入想追踪的事件。");
        this.requestId ||= uuid();
        const data=await api("/api/topics",{method:"POST",body:{query:this.query,request_id:this.requestId}});
        this.query="";this.requestId="";
        if(sequence!==this.detailSequence){await this.load({quiet:true});return;}
        this.selected=data.topic.id;this.page="create";this.data=null;
      } else if(this.page==="edit") {
        await api("/api/topics/"+selected+"/definition",{method:"PATCH",body:this.drafts.get(selected)});
        this.draftChanges.delete(selected);this.drafts.delete(selected);
        if(sequence!==this.detailSequence){await this.load({quiet:true});return;}
        this.page="topic";
      } else {
        await api("/api/topics/"+this.selected+"/initialize",{method:"POST",body:{...this.drafts.get(this.selected),timeline_mode:"events"}});
        this.draftChanges.delete(selected);
        if(sequence!==this.detailSequence){await this.load({quiet:true});return;}
      }
      await this.fetchDetail();await this.load({quiet:true});
    } catch(error){const target=this.content.querySelector(".topic-form-error");if(target)target.textContent=errorText(error);else toast(errorText(error));}
    finally {this.busy=false;if(submit.isConnected)submit.disabled=false;this.syncChatControls();}
  }
  async click(event) {
    const b=event.target.closest("button");if(!b)return;
    if(b.dataset.topicReadLater){
      const item=this.data?.items.find(row=>row.id===b.dataset.topicReadLater);
      if(item?.available) await this.toggleReadLater(item);
      return;
    }
    if(b.dataset.topicNode){
      this.node=b.dataset.topicNode;
      if(this.data.nodes.find(n=>n.id===this.node)?.news_count>0){
        this.filter=this.node;
        await this.fetchDetail({quiet:true,forceRender:true});
      }else this.render({preserveScroll:true});
      return;
    }
    if(b.dataset.relatedTopic){await this.select(b.dataset.relatedTopic,true);return;}
    if(b.dataset.topicItem){
      const item=this.data.items.find(x=>x.id===b.dataset.topicItem);
      if(item?.available){this.lastArticle=item.id;this.reading=true;this.onViewChange();this.openArticle(item);}
      return;
    }
    switch(b.dataset.topicAction){
      case "new":this.newTopic();break;
      case "definition":
        if(this.page==="edit")this.page="topic";
        else {if(!this.draftChanges.has(this.selected))this.drafts.set(this.selected,{...this.data.topic});this.page="edit";}
        this.render();
        break;
      case "cancel-edit":
        this.drafts.delete(this.selected);this.draftChanges.delete(this.selected);this.page="topic";this.render();await this.fetchDetail({quiet:true});
        break;
      case "reload-draft":
        this.draftChanges.delete(this.selected);this.chatRequests.delete(this.selected);this.chatErrors.delete(this.selected);
        this.drafts.set(this.selected,{...this.data.topic});this.render({preserveScroll:true});
        break;
      case "all":this.filter="";await this.fetchDetail({quiet:true});break;
      case "more":await this.fetchDetail({append:true});break;
      case "retry-detail":await this.fetchDetail();break;
      case "open":
        this.drafts.delete(this.selected); this.page="topic"; this.listSelected=this.selected;
        if (!this.matchesList(this.data.topic)) this.listFilter="all";
        this.renderList(); this.render(); break;
      case "cancel":this.leaveDetail();await this.load();this.onViewChange();break;
      case "refine": {
        if (this.busy) return;
        const id = this.selected;
        this.busy = true; b.disabled = true;
        try {
          if(!await confirmAction({title:"精简重要进展",message:"精简可能合并部分节点，使它们不再单独显示，并改写节点标题和概括。已收录新闻及其关联会保留。成功后一个月内无法再次精简。确定继续？",action:"确认精简",trigger:b}))break;
          if(this.selected!==id || !this.isVisible())break;
          await api("/api/topics/"+id+"/refine", {method:"POST"});
          if (this.selected === id) {
            this.filter = ""; await this.fetchDetail({quiet:true});
          }
        } catch (error) {
          toast(errorText(error));
          if(this.selected===id && (error.code || error.message)==="topic_refine_cooldown")await this.fetchDetail({quiet:true});
        }
        finally { this.busy = false; if (b.isConnected) b.disabled = false; }
        break;
      }
      case "archive":
      case "restore": {
        if (this.busy) return;
        const id = this.selected, archived = b.dataset.topicAction === "archive";
        this.busy = true; b.disabled = true;
        try {
          await api("/api/topics/"+id+"/archive", {method:"POST", body:{archived}});
          await this.load({quiet:true});
          if (this.selected === id) await this.fetchDetail({quiet:true});
          toast(archived ? "主题已归档" : "已恢复跟踪");
        } catch (error) { toast(errorText(error)); }
        finally { this.busy = false; if (b.isConnected) b.disabled = false; }
        break;
      }
      case "delete": {
        const id=this.selected;
        if(!await confirmAction({title:"删除主题",message:"删除这个主题及其时间线？已收录的原始新闻和阅读状态会保留。",action:"删除主题",trigger:b}))break;
        try {
          await api("/api/topics/"+id,{method:"DELETE"});this.drafts.delete(id);this.draftChanges.delete(id);this.viewed.delete(id);
          this.chatDrafts.delete(id);this.chatRequests.delete(id);this.chatErrors.delete(id);this.draftExpanded.delete(id);
          this.page="list";this.selected="";this.data=null;this.detailSequence++;this.render();await this.load();this.onViewChange();
        }catch(error){toast(errorText(error));}
        break;
      }
      case "retry":
        if(this.busy)return;
        this.busy=true;b.disabled=true;
        try{await api("/api/topics/"+this.selected+"/retry",{method:"POST"});await this.fetchDetail({quiet:true});}
        catch(error){toast(errorText(error));}
        finally{this.busy=false;if(b.isConnected)b.disabled=false;this.syncChatControls();}
        break;
    }
  }
  leaveDetail(){
    if(["new","create","complete","edit"].includes(this.page)){
      this.detailSequence++;this.page="list";this.selected="";this.data=null;this.renderList();this.render();
    }
  }
  returnFromArticle(){
    if(!this.reading)return false;
    this.reading=false;this.closeArticle();this.onViewChange();this.enterReader();
    if(this.lastArticle)this.content.querySelector('[data-topic-item="'+CSS.escape(this.lastArticle)+'"]')?.focus({preventScroll:true});
    return true;
  }
  get dirty(){return !!this.query.trim() || this.draftChanges.size>0 || [...this.chatDrafts.values()].some(text=>text.trim());}
}
