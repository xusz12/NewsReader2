import { $, api, busy, escapeHTML as e } from "./ui.js";
import { classificationStatus } from "./classification-progress.js?v=tasks-1";
import { jobErrorText } from "./topic-status.js";
import { failureText, statusText, progressText } from "./report-status.js?v=reports-parallel-1";

const phases = {define:"明确主题范围",discuss:"回复主题定义",initialize:"整理历史脉络",update:"整理新进展",milestone:"筛选重要进展",refine:"精简阶段进程"};
const retryTime = value => value ? new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)) : "";
const topicWorking = row => ["pending","running"].includes(row.job?.status) || row.matching.pending || row.matching.running || (row.matching.waiting_update && row.job?.status !== "failed");

export class TaskProgress {
  constructor({classification, onSettings, onTopic, onReport, onReports}) {
    Object.assign(this,{classification,onSettings,onTopic,onReport,onReports});
    this.root=$("#classificationProgress"); this.indicator=$("#classificationProgressIndicator"); this.panel=$("#classificationProgressPanel");
    this.root.addEventListener("pointerenter", event=>{if(event.pointerType!=="touch")this.open();});
    this.root.addEventListener("pointerleave",()=>{
      this.leaveTimer=setTimeout(()=>{if(!this.root.contains(document.activeElement))this.close();},150);
    });
    this.root.addEventListener("focusin",()=>{if(!this.restoringFocus)this.open();});
    this.root.addEventListener("focusout",event=>{if(!this.root.contains(event.relatedTarget))this.close();});
    this.indicator.addEventListener("pointerdown",event=>{if(event.pointerType==="touch"){event.preventDefault();this.open();}});
    this.indicator.addEventListener("keydown",event=>{
      if(["Enter"," "].includes(event.key)){event.preventDefault();this.open();}
    });
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
    $("#taskProgressRefresh").onclick=event=>busy(event.currentTarget,()=>this.refresh());
    $("#taskProgressMoreReports").onclick=()=>{this.close();return busy(null,this.onReports);};
    this.panel.addEventListener("click",event=>{
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
    this.panel.hidden=false;this.indicator.setAttribute("aria-expanded","true");
    void this.classification.refresh();void this.refresh();
  }
  close() {
    clearTimeout(this.leaveTimer);this.panel.hidden=true;this.indicator.setAttribute("aria-expanded","false");
  }
  updateClassification(snapshot) { this.classificationSnapshot=snapshot;this.render(); }
  async refresh() {
    if(document.hidden)return;
    if(this.refreshing){this.refreshAgain=true;return;}
    clearTimeout(this.timer);this.refreshing=true;
    try {
      const data=await api("/api/tasks/progress");
      if(!Array.isArray(data.topics?.items) || !Array.isArray(data.reports?.items) || !data.reports.counts)throw new Error("Invalid task progress");
      if(!this.refreshAgain){this.data=data;this.error="";this.render();}
    } catch {
      this.error=this.data?"主题追踪与日报进度暂时无法更新，显示上次结果":"暂时无法读取主题追踪与日报进度";
      this.render();
    } finally {
      this.refreshing=false;
      if(this.refreshAgain){this.refreshAgain=false;void this.refresh();}
      else if(!document.hidden){
        const working=this.data?.topics.items.some(topicWorking) || this.data?.reports.counts.pending || this.data?.reports.counts.running;
        this.timer=setTimeout(()=>this.refresh(),working?2000:30000);
      }
    }
  }
  renderRows(container, rows, type, content) {
    const existing=new Map([...container.children].map(node=>[node.dataset.id,node]));
    for(const [index,row] of rows.entries()) {
      let node=existing.get(row.id);existing.delete(row.id);
      if(!node){node=document.createElement("li");node.className="agent-notice";node.dataset.id=row.id;
        node.innerHTML=`<button type="button" class="agent-notice-open" data-task-type="${type}" data-task-id="${e(row.id)}"></button>`;}
      const button=node.firstElementChild, html=content(row);
      if(row.kind)button.dataset.kind=row.kind;
      if(button.innerHTML!==html)button.innerHTML=html;
      if(container.children[index]!==node)container.insertBefore(node,container.children[index] || null);
    }
    for(const node of existing.values())node.remove();
  }
  topicContent(row) {
    const job=row.job, matching=row.matching, parts=[];
    if(job) {
      const phase=phases[job.kind] || "处理主题";
      parts.push(job.status==="failed" ? `${phase}失败` : job.status==="pending" ? `等待${phase}` : `正在${phase}`);
      if(job.error)parts.push(jobErrorText(job));
      if(job.status==="pending" && job.next_attempt_at)parts.push(`${retryTime(job.next_attempt_at*1000)} 重试`);
    }
    const counts=[["pending","待匹配"],["running","匹配中"],["waiting_update","待整理"],["failed","匹配失败"]]
      .filter(([key])=>matching[key]).map(([key,label])=>`${label} ${matching[key]} 条`).join(" · ");
    if(counts)parts.push(counts);
    if(matching.pending || matching.running || matching.waiting_update || job?.kind==="milestone") {
      if(this.data.topics.matching_configured===false)parts.push("等待配置 Jev");
      else if(this.data.topics.matching_error)parts.push("匹配已暂停，请检查 Jev 配置");
    }
    return `<strong>${e(row.name || "新建主题")}</strong><span class="agent-notice-preview">${e(parts.join(" · "))}</span>`;
  }
  reportContent(row) {
    const parts=[row.model_label, row.status==="running"?progressText(row):statusText(row)].filter(Boolean);
    if(row.error)parts.push(failureText(row));
    if(row.next_retry_at)parts.push(`${retryTime(row.next_retry_at)} 重试`);
    return `<strong>${e(row.title || row.kind_label)}</strong><span class="agent-notice-preview">${e(parts.join(" · "))}</span>`;
  }
  render() {
    const focused=document.activeElement, focusInside=this.root.contains(focused);
    const p=this.classificationSnapshot?.progress, batch=p?.batch;
    const showClassification=!!(p && p.enabled!==false && batch?.total && (batch.pending || batch.running || batch.failed));
    const topics=this.data?.topics.items || [], reports=this.data?.reports.items || [];
    const reportTotal=this.data?.reports.total || 0;
    const labels=[showClassification&&"新闻分类",topics.length&&"主题追踪",reportTotal&&"日报"].filter(Boolean);
    this.root.hidden=!labels.length && !this.error;
    $("#taskProgressClassification").hidden=!showClassification;
    $("#taskProgressTopics").hidden=!topics.length;
    $("#taskProgressReports").hidden=!reportTotal;
    $("#taskProgressError").hidden=!this.error;
    $("#taskProgressErrorText").textContent=this.error || "";
    $("#taskProgressTopicCount").textContent=`${topics.length} 个主题`;
    const counts=this.data?.reports.counts || {};
    $("#taskProgressReportCount").textContent=[["running","生成中"],["pending","等待"],["failed","失败"]]
      .filter(([key])=>counts[key]).map(([key,label])=>`${label} ${counts[key]}`).join(" · ");
    $("#taskProgressMoreReports").hidden=reportTotal<=reports.length;
    $("#taskProgressMoreReports").textContent="查看报告列表";
    this.renderRows($("#taskProgressTopicList"),topics,"topic",row=>this.topicContent(row));
    this.renderRows($("#taskProgressReportList"),reports,"report",row=>this.reportContent(row));
    let done=0,percent=0;
    if(showClassification) {
      done=Math.min(batch.total,Math.max(0,batch.done));percent=done/batch.total*100;
      $("#classificationProgressCount").textContent=`此次已分类 ${done.toLocaleString()}/${batch.total.toLocaleString()} 条`;
      $("#classificationProgressScope").textContent=`当前模式：${p.unread_only?"仅未读":"全部"}`;
      $("#classificationProgressDetails").textContent=`待处理 ${batch.pending} · 处理中 ${batch.running} · 失败 ${batch.failed}`;
      $("#classificationProgressStatus").textContent=this.classificationSnapshot.error || classificationStatus({...p,...batch});
      $("#classificationProgressRetry").hidden=!p.configured || !(batch.failed || p.error);
      $("#classificationProgressSettings").hidden=p.configured && !p.error;
    }
    const determinate=showClassification && labels.length===1 && !this.error;
    const label=labels.length===1?labels[0]+"进度":"任务进度";
    const description=determinate ? `此次已分类 ${done}/${batch.total} 条，${p.unread_only?"仅未读新闻":"全部新闻"}`
      : [...(showClassification?[`新闻分类 ${done}/${batch.total} 条`]:[]),...(topics.length?[`主题追踪 ${topics.length} 个主题`]:[]),...(reportTotal?[`日报 ${reportTotal} 份`]:[]),...(this.error?[this.error]:[])].join("；");
    this.indicator.setAttribute("aria-label",label);this.panel.setAttribute("aria-label",label+"详情");
    this.indicator.setAttribute("aria-valuetext",description);$("#taskProgressSummary").textContent=description;
    this.indicator.dataset.mode=determinate?"determinate":"indeterminate";
    if(determinate){this.indicator.setAttribute("aria-valuenow",String(done));this.indicator.setAttribute("aria-valuemax",String(batch.total));}
    else{this.indicator.removeAttribute("aria-valuenow");this.indicator.removeAttribute("aria-valuemax");}
    const arc=$("#classificationProgressArc");
    arc.style.strokeDasharray=determinate?"100":"22 11";
    arc.style.strokeDashoffset=String(determinate?100-percent:0);
    arc.style.opacity=determinate&&!done?"0":"1";
    if(this.root.hidden)this.close();
    if(focusInside && (!focused.isConnected || focused.closest("[hidden]")))this.restoreFocus(this.root.hidden?$("#refreshIndexBtn"):this.indicator);
  }
}
