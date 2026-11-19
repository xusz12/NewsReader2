import { $, api, busy, escapeHTML as e, toast } from "./ui.js";
import { classificationStatus } from "./classification-progress.js?v=translation-routing-1";
import { jobErrorText } from "./topic-status.js";
import { failureText, statusText, reportProgress } from "./report-status.js?v=0.5.16";

const phases = {define:"明确主题范围",discuss:"回复主题定义",initialize:"整理历史脉络",
  update:"整理新进展",milestone:"筛选重要进展",refine:"精简阶段进程"};
const retryTime = value => value ? new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)) : "";
const topicWorking = row => ["pending","running"].includes(row.job?.status) || row.matching.pending || row.matching.running || (row.matching.waiting_update && row.job?.status !== "failed");

const localNoticeKey = "newsreader_task_notice_dismissals";
const needsMatching = row => row.matching.pending || row.matching.running || row.matching.waiting_update || row.job?.kind === "milestone";

function matchingBlock(routing) {
  const provider = routing.matching_provider_label || "Jev";
  if (routing.matching_waiting) return ["等待恢复","等待判断服务恢复或配置密钥"];
  if (routing.using_backup) return null;
  if (routing.matching_configured === false) return ["等待配置",`等待配置 ${provider}`];
  if (routing.matching_error) return ["匹配暂停",`匹配已暂停，请检查 ${provider} 配置`];
  return null;
}

function topicTask(row, routing, block) {
  const {job, matching} = row;
  const counts = [["pending","待匹配"],["running","匹配中"],["waiting_update","待整理"],["failed","匹配失败"]]
    .filter(([key]) => matching[key]).map(([key, label]) => `${label} ${matching[key]} 条`).join(" · ");
  const [blocked, reason] = (needsMatching(row) && block) || [];
  const backup = needsMatching(row) && routing.using_backup && `使用备用服务 ${routing.matching_provider_label}`;
  const retry = job?.status === "pending" && job.next_attempt_at;
  let stage = matching.running ? "正在匹配新闻" : matching.pending ? "等待匹配新闻" : "等待整理新进展";
  if (job) {
    const phase=phases[job.kind] || "处理主题";
    if (job.status === "failed") stage = phase+"失败";
    else if (job.status === "running") stage = "正在"+phase;
    else stage = retry ? phase+" · 等待重试" : "等待"+phase;
  } else if (matching.failed) stage = "新闻匹配失败";
  else if (blocked) stage = blocked;
  let state = job?.status === "running" || matching.running ? "running" : "pending";
  if ((job?.status==="pending" && !job.error_dismissed && (retry || job.error)) || blocked) state = "warning";
  if (job?.status === "failed" || matching.failed) state = "failed";
  return {id:row.id, section:"taskProgressTopics", label:"主题", title:row.name || "新建主题", stage, state, failedMatches:matching.failed,
    error:[job?.error && job.status!=="running" && !job.error_dismissed && jobErrorText(job), reason].filter(Boolean).join(" · "),
    notes:[counts, backup, retry && `${retryTime(retry*1000)} 重试`].filter(Boolean)};
}

function reportTask(row) {
  const running = row.status === "running", progress = reportProgress(row);
  const label=row.kind_label || "报告", title=row.title || label;
  return {id:row.id, section:"taskProgressReports", kind:row.kind, label,
    title:row.kind_label && !title.includes(label) ? `${label} · ${title}` : title,
    stage:running ? progress.text : statusText(row), measurement:running && progress.count ? row.progress : null,
    state:row.status==="pending" && row.error && !row.error_dismissed ? "warning" : row.status,
    error:!running && row.error && !row.error_dismissed && failureText(row),
    notes:[row.next_retry_at && `${retryTime(row.next_retry_at)} 重试`, !running && row.progress?.stage && `上次进度 · ${progress.text}`, row.model_label].filter(Boolean)};
}

export class TaskProgress {
  constructor({classification, onSettings, onTopic, onReport, onReports}) {
    Object.assign(this,{classification,onSettings,onTopic,onReport,onReports});
    this.localIssues={};
    this.dismissedLocal={};
    this.retryingMatches=new Set();
    try {
      const saved=JSON.parse(sessionStorage.getItem(localNoticeKey));
      if(saved && typeof saved==="object" && !Array.isArray(saved))this.dismissedLocal=saved;
    } catch {}
    this.root=$("#classificationProgress"); this.indicator=$("#classificationProgressIndicator"); this.panel=$("#classificationProgressPanel");
    this.root.addEventListener("pointerenter", event=>{if(event.pointerType!=="touch")this.open();});
    this.root.addEventListener("pointerleave",event=>{
      if(event.pointerType==="touch" || this.pinned)return;
      this.leaveTimer=setTimeout(()=>{if(!this.pinned && !this.root.contains(document.activeElement))this.close();},150);
    });
    this.root.addEventListener("focusin",()=>{if(!this.restoringFocus)this.open();});
    document.addEventListener("focusin",event=>{if(!this.root.contains(event.target))this.close();});
    this.indicator.addEventListener("click",()=>{
      if(this.pinned)this.close();
      else {this.pinned=true;this.open();}
    });
    $("#taskProgressClose").onclick=()=>{this.close();this.restoreFocus(this.indicator);};
    $("#taskProgressClear").onclick=()=>this.clearNotices();
    document.addEventListener("pointerdown",event=>{if(!this.root.contains(event.target))this.close();});
    document.addEventListener("topbar:popover-open",event=>{if(event.detail!==this.panel.id)this.close();});
    document.addEventListener("keydown",event=>{
      if(event.key!=="Escape" || this.panel.hidden)return;
      event.preventDefault();event.stopImmediatePropagation();
      const focused=this.panel.contains(document.activeElement);this.close();
      if(focused)this.restoreFocus(this.indicator);
    },true);
    $("#classificationProgressSettings").onclick=()=>{this.close();return busy(null,this.onSettings);};
    $("#classificationProgressRetry").onclick=event=>busy(event.currentTarget,async()=>{
      await api("/api/news/classification/retry",{method:"POST"});await this.classification.refresh();
    });
    $("#taskProgressRefresh").onclick=event=>busy(event.currentTarget,()=>Promise.all([this.classification.refresh(),this.refresh()]));
    $("#taskProgressMoreReports").onclick=()=>{this.close();return busy(null,this.onReports);};
    this.panel.addEventListener("click",event=>{
      const retry=event.target.closest("[data-retry-matching]");
      if(retry)return this.retryMatching(retry.dataset.retryMatching);
      const button=event.target.closest("[data-task-id]");if(!button)return;
      this.close();
      return busy(null,()=>button.dataset.taskType==="topic" ? this.onTopic(button.dataset.taskId) : this.onReport(button.dataset.taskId,button.dataset.kind));
    });
    document.addEventListener("visibilitychange",()=>{
      clearTimeout(this.timer);
      if(document.hidden)this.close();else void this.refresh();
    });
    document.addEventListener("tasks:changed",()=>void this.refresh());
  }
  restoreFocus(target) {
    this.restoringFocus=true;target?.focus({preventScroll:true});this.restoringFocus=false;
  }
  open() {
    clearTimeout(this.leaveTimer);
    if(this.root.hidden || !this.panel.hidden)return;
    document.dispatchEvent(new CustomEvent("topbar:popover-open",{detail:this.panel.id}));
    this.render(true);
    this.panel.hidden=false;this.panel.scrollTop=0;this.indicator.setAttribute("aria-expanded","true");
    void this.classification.refresh();void this.refresh();
  }
  close() {
    clearTimeout(this.leaveTimer);this.pinned=false;this.panel.hidden=true;this.indicator.setAttribute("aria-expanded","false");
  }
  updateClassification(snapshot) { this.classificationSnapshot=snapshot;this.render(); }
  saveLocalDismissals() {
    try { sessionStorage.setItem(localNoticeKey,JSON.stringify(this.dismissedLocal)); } catch {}
  }
  localNotice(key, known, signature) {
    if (known) {
      this.localIssues[key]=signature || "";
      if (this.dismissedLocal[key] && this.dismissedLocal[key]!==this.localIssues[key]) {
        delete this.dismissedLocal[key];
        this.saveLocalDismissals();
      }
    }
    return this.localIssues[key] && this.localIssues[key]!==this.dismissedLocal[key];
  }
  async clearNotices() {
    if(this.clearing)return;
    const progress=this.classificationSnapshot?.progress;
    const taskNotices=this.data?.notices || {};
    const classificationNotices=progress?.enabled!==false ? progress?.notices || {} : {};
    const token=Object.keys(taskNotices).length ? this.data.database_token : progress?.database_token;
    const notices={...taskNotices,...classificationNotices};
    for(const [key,value] of Object.entries(this.localIssues))if(value)this.dismissedLocal[key]=value;
    this.saveLocalDismissals();
    this.clearing=true;
    const button=$("#taskProgressClear");
    button.setAttribute("aria-busy","true");
    try {
      this.render();
      if(Object.keys(notices).length){
        if(classificationNotices.classification && progress.database_token!==token)throw new Error("task_database_changed");
        await api("/api/tasks/progress/dismiss",{method:"POST",body:{database_token:token,notices,batch_id:progress?.batch?.id}});
        await Promise.all([this.classification.refresh(),this.refresh()]);
      }
      toast("已清理异常提示");
    } catch {
      toast("任务异常清理失败，请重试");
      void this.classification.refresh();void this.refresh();
    } finally {
      this.clearing=false;button.removeAttribute("aria-busy");
    }
  }
  async retryMatching(topicId) {
    if(this.retryingMatches.has(topicId))return;
    const body={topic_id:topicId,database_token:this.data.database_token,notice_id:this.data.notices?.matching};
    this.retryingMatches.add(topicId);
    this.render();
    try {
      const {retried}=await api("/api/tasks/progress/retry-matching",{method:"POST",body});
      toast(retried ? `已将 ${retried} 条新闻重新加入主题匹配队列` : "这些匹配任务的状态已更新");
    } catch(error) {
      toast(error.code==="task_database_changed" ? "新闻库已切换，请在最新进度中重试" : "匹配重试请求未能确认，请查看最新进度");
    } finally {
      await this.refresh();
      this.retryingMatches.delete(topicId);
      this.render();
    }
  }
  refresh() {
    if(document.hidden)return;
    if(this.refreshing){this.refreshAgain=true;return this.refreshing;}
    clearTimeout(this.timer);
    this.refreshing=this.readProgress();
    return this.refreshing;
  }
  async readProgress() {
    try {
      const data=await api("/api/tasks/progress");
      if(!Array.isArray(data.topics?.items) || !Array.isArray(data.reports?.items) || !data.reports.counts)throw new Error("Invalid task progress");
      if(!this.refreshAgain){this.data=data;this.error="";this.render();}
    } catch {
      this.error=this.data?"主题追踪与日报进度暂时无法更新，显示上次结果":"暂时无法读取主题追踪与日报进度";
      this.render();
    } finally {
      this.refreshing=null;
      if(this.refreshAgain){this.refreshAgain=false;return this.refresh();}
      if(!document.hidden){
        const working=this.data?.topics.items.some(topicWorking) || this.data?.reports.counts.pending || this.data?.reports.counts.running;
        this.timer=setTimeout(()=>this.refresh(),working?2000:30000);
      }
    }
  }
  renderRows(container, rows, type) {
    const featured=rows.findIndex(row=>row.section===this.firstTask?.section && row.id===this.firstTask.id);
    if(featured>0)rows.unshift(...rows.splice(featured,1));
    const existing=new Map([...container.children].map(node=>[node.dataset.id,node]));
    for(const [index,row] of rows.entries()) {
      let node=existing.get(row.id);existing.delete(row.id);
      if(!node){node=document.createElement("li");node.className="agent-notice";node.dataset.id=row.id;
        node.innerHTML=`<button type="button" class="agent-notice-open" data-task-type="${type}" data-task-id="${e(row.id)}"></button>`+
          (type==="topic" ? `<button type="button" class="agent-notice-dismiss" data-retry-matching="${e(row.id)}" hidden>重试匹配</button>` : "");}
      const button=node.firstElementChild;
      const html=`<strong>${e(row.title)}</strong><span class="task-progress-stage status-text" data-status="${e(row.state)}">${e(row.stage)}</span>` +
        (row.error ? `<span class="status-text" data-status="failed">${e(row.error)}</span>` : "") +
        row.notes.map(note=>`<span class="agent-notice-preview">${e(note)}</span>`).join("");
      if(row.kind)button.dataset.kind=row.kind;
      if(button.innerHTML!==html)button.innerHTML=html;
      const retry=button.nextElementSibling;
      if(retry) {
        retry.hidden=!row.failedMatches;
        const pending=this.retryingMatches.has(row.id);
        retry.setAttribute("aria-label",`重试匹配 · ${row.title}`);
        retry.setAttribute("aria-disabled",String(pending));
        retry.setAttribute("aria-busy",String(pending));
        retry.textContent=pending ? "提交中…" : "重试匹配";
      }
      if(container.children[index]!==node)container.insertBefore(node,container.children[index] || null);
    }
    for(const node of existing.values())node.remove();
  }
  render(opening=false) {
    const focused=document.activeElement, focusInside=this.root.contains(focused);
    const topicButton=focused?.closest("[data-retry-matching]")?.previousElementSibling;
    const p=this.classificationSnapshot?.progress, batch=p?.batch;
    const failed=p?.unacknowledged_failed ?? batch?.failed;
    const showClassification=!!(p && p.enabled!==false && batch?.total && (batch.pending || batch.running || failed));
    const rows=this.data?.topics.items || [], routing=this.data?.topics;
    const block=rows.some(needsMatching) && matchingBlock(routing);
    const matchingIssue=this.localNotice("matchingService",this.data && !this.error,
      block && JSON.stringify([this.data.database_token,block,routing.matching_error]));
    const topics=rows.map(row=>topicTask(row,routing,matchingIssue && block));
    const reports=(this.data?.reports.items || []).map(reportTask);
    const reportTotal=this.data?.reports.total || 0;
    const taskCount=Number(showClassification)+topics.length+reportTotal;
    $("#taskProgressTitle").textContent=taskCount>1 ? `任务进度（${taskCount} 项）` : "任务进度";
    const classificationError=this.localNotice("classificationRead",this.classificationSnapshot,this.classificationSnapshot?.error && "offline") && this.classificationSnapshot?.error;
    const taskError=this.localNotice("taskRead",this.data || this.error,this.error && "offline") && this.error;
    const readErrors=[classificationError && `新闻分类 · ${classificationError}`,taskError].filter(Boolean);
    const stale=[showClassification && this.classificationSnapshot.error && !classificationError && "新闻分类",
      (topics.length || reportTotal) && this.error && !taskError && "主题与报告"].filter(Boolean);
    const service=showClassification && (p.waiting_for_service ? "等待恢复" : !p.configured ? "等待配置" : p.error ? "分类暂停" :
      p.throttle?.rate_limited ? "限流降速" : "");
    const classificationIssue=this.localNotice("classificationService",p && !this.classificationSnapshot.error,
      service && JSON.stringify([p.database_token,batch.id,p.provider_label,service,p.error]));
    $("#taskProgressClear").hidden=!(readErrors.length || matchingIssue || classificationIssue ||
      (p?.enabled!==false && p?.notices?.classification) || Object.keys(this.data?.notices || {}).length);
    const labels=[(showClassification || classificationError)&&"新闻分类",topics.length&&"主题追踪",reportTotal&&"日报"].filter(Boolean);
    this.root.hidden=!labels.length && !readErrors.length;
    $("#taskProgressClassification").hidden=!showClassification;
    $("#taskProgressTopics").hidden=!topics.length;
    $("#taskProgressReports").hidden=!reportTotal;
    $("#taskProgressError").hidden=!readErrors.length && !stale.length;
    const readStatus=$("#taskProgressErrorText");
    readStatus.classList.toggle("error-state",!!readErrors.length);
    readStatus.classList.toggle("muted",!readErrors.length);
    readStatus.textContent=[...readErrors,stale.length && `${stale.join("、")}显示上次进度`].filter(Boolean).join("。");
    $("#taskProgressTopicCount").textContent=`${topics.length} 个主题`;
    const counts=this.data?.reports.counts || {};
    $("#taskProgressReportCount").textContent=[["running","生成中"],["pending","等待"],["failed","失败"]]
      .filter(([key])=>counts[key]).map(([key,label])=>`${label} ${counts[key]}`).join(" · ");
    $("#taskProgressMoreReports").hidden=reportTotal<=reports.length;
    $("#taskProgressMoreReports").textContent="查看报告列表";
    let done=0,classificationTask;
    if(showClassification) {
      done=Math.min(batch.total,Math.max(0,batch.done));
      $("#classificationProgressCount").textContent=`此次已分类 ${done.toLocaleString()}/${batch.total.toLocaleString()} 条`;
      $("#classificationProgressScope").textContent=`当前模式：${p.unread_only?"仅未读":"全部"}`;
      const pendingNotice=failed>0 && failed<batch.failed ? `（${failed} 条待查看）` : "";
      $("#classificationProgressDetails").textContent=`待处理 ${batch.pending} · 处理中 ${batch.running} · 失败 ${batch.failed}${pendingNotice}`;
      $("#classificationProgressStatus").textContent=classificationStatus({...p,...batch});
      $("#classificationProgressStatus").hidden=!!service && !classificationIssue;
      const issue=failed ? `失败 ${failed} 条` : classificationIssue ? service : "";
      classificationTask={section:"taskProgressClassification", label:"分类", stage:issue || `${done}/${batch.total}`, state:failed || (classificationIssue && p.error) ? "failed" : issue ? "warning" : "running"};
      $("#classificationProgressStatus").dataset.status=classificationTask.state;
      $("#classificationProgressRetry").hidden=!p.configured || !(batch.failed || p.error);
      $("#classificationProgressSettings").hidden=p.configured && !p.error;
    }
    const label=labels.length===1 && !taskError ? labels[0]+"进度" : "任务进度";
    const tasks=[...reports,...topics];
    if(counts.failed && !reports.some(row=>row.state==="failed"))tasks.push({section:"taskProgressReports", label:"报告", stage:`失败 ${counts.failed} 份`, state:"failed"});
    if(classificationTask)tasks.push(classificationTask);
    const lead=readErrors.length ? {label:taskError ? "任务进度" : "分类进度", stage:"读取异常", state:"failed"} :
      tasks.find(row=>row.state==="failed") || tasks.find(row=>row.state==="warning") || tasks.find(row=>row.state==="running") || tasks[0];
    if(opening) {
      this.firstTask=lead;
      const section=lead?.section && document.getElementById(lead.section);
      if(section)$("#taskProgressError").after(section);
    }
    this.renderRows($("#taskProgressTopicList"),topics,"topic");
    this.renderRows($("#taskProgressReportList"),reports,"report");
    const measurement=showClassification && labels.length===1 && !this.error ? {completed:done,total:batch.total} : lead?.measurement;
    this.indicator.dataset.status=lead?.state || "";
    const description=[lead && `${lead.label} · ${lead.stage}`, showClassification && `此次已分类 ${done}/${batch.total} 条，${p.unread_only?"仅未读新闻":"全部新闻"}`,
      topics.length && `主题追踪 ${topics.length} 个主题`,reportTotal && `日报 ${reportTotal} 份`, ...readErrors].filter(Boolean).join("，");
    const meter=$("#taskProgressSummary");
    this.indicator.setAttribute("aria-label",label);this.panel.setAttribute("aria-label",label+"详情");
    meter.setAttribute("aria-label",label);meter.setAttribute("aria-valuetext",description);meter.textContent=description;
    this.indicator.dataset.mode=measurement?"determinate":"indeterminate";
    if(measurement){meter.setAttribute("aria-valuenow",String(measurement.completed));meter.setAttribute("aria-valuemax",String(measurement.total));}
    else{meter.removeAttribute("aria-valuenow");meter.removeAttribute("aria-valuemax");}
    const arc=$("#classificationProgressArc");
    arc.style.strokeDasharray=measurement?"100":"22 11";
    arc.style.strokeDashoffset=String(measurement?100-measurement.completed/measurement.total*100:0);
    arc.style.opacity=measurement && !measurement.completed?"0":"1";
    if(this.root.hidden)this.close();
    if(focusInside && (!focused.isConnected || focused.closest("[hidden]")))
      this.restoreFocus(this.root.hidden ? $("#upstreamErrorsBtn") : topicButton?.isConnected && !topicButton.closest("[hidden]") ? topicButton : this.indicator);
  }
}
