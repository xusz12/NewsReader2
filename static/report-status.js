import { errorText } from "./ui.js";

const statusLabels = {pending: "等待生成", running: "正在生成", failed: "生成失败", skipped: "已跳过", done: ""};
const errors = {
  report_index_failed: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_source_missing: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_not_directory: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_permission_denied: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_database_locked: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_disk_full: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_invalid_file: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_index_database_error: "旧文件采集阶段未完成；重试将直接使用当前新闻库。",
  report_intraday_expired: "启动时已错过本期，早报和午报不补生成。",
  report_model_unconfigured: "请先在设置中配置模型与 Agent。",
  report_model_selection_invalid: "请选择有效的 Runtime 和模型。",
  report_reasoning_unsupported: "所选模型不支持该推理程度，请重新选择。",
  report_model_catalog_failed: "模型目录读取失败，请检查本机配置后重试。",
  report_model_dependencies_missing: "Pi 运行依赖不可用，请确认已安装 Node.js 24+，并在项目目录执行 npm ci --ignore-scripts 后重试。",
  report_model_capacity_unknown: "无法确认所选模型的上下文容量，请检查模型目录或本机模型配置后重试。",
  report_database_changed: "当前新闻库已切换，请刷新报告列表后重试。",
  report_retry_unavailable: "暂时无法重试，请刷新报告列表后再试。",
  report_read_unavailable: "报告尚未生成完成，暂时无法标记阅读完成。",
  report_sources_unread: "部分来源新闻尚未保存为已浏览，请重新打开报告后重试。",
  report_preview_invalid: "该期尚未结束或排期无效，请重新选择一期。",
  report_preview_conflict: "本次请求已用于另一份报告，请重新打开试生成。",
  report_preview_only: "只有试生成报告可以在这里删除。",
  report_preview_running: "报告仍在生成，请完成后再删除。",
  report_window_invalid: "请选择有效的起止时间，结束时间须晚于开始时间，且不能晚于当前时间。",
  report_window_changed: "新闻窗口规则已更新，请刷新页面后重新选择日期。",
  report_manual_conflict: "本次请求已用于另一份报告，请重新打开新增日报。",
  report_delete_running: "报告仍在生成，请完成后再删除。",
  report_deleted: "这份报告已删除，如需重新生成，请重新打开新增日报。",
  report_model_failed: "生成失败，请检查模型与 Agent 设置后重试。",
  report_model_timeout: "模型响应超时，可以重试。",
  report_invalid_result: "生成内容未通过完整性校验，可以重试。",
  report_input_too_large: "本期资料超出处理上限，报告尚未生成。",
  report_repair_too_large: "待修正的内容超出模型单次容量，已保留完成的工作，可重试。",
  report_workspace_capacity: "当前模型无法容纳报告工作区的一次请求，已保留完成的工作。",
  report_coverage_incomplete: "仍有新闻未完成审阅，已保留完成的工作，可重试继续。",
  report_work_incomplete: "报告尚有整理或章节未完成，已保留完成的工作，可重试继续。",
  report_output_truncated: "模型输出达到长度上限，已保留完成的整理与章节，请调整推理程度后另建报告，或重试。",
  report_interrupted: "上次生成中断，可以重试。",
  report_snapshot_missing: "这份报告的新闻快照缺失，请恢复数据库备份后重试。",
  report_tools_not_allowed: "模型尝试了资料范围外的操作，报告未保存，可以重试。",
};
export const failureText = report => errors[report.error] || "生成暂未完成，请稍后重试。";
export const statusText = report => report.status === "pending" && report.error ? "等待重试" : statusLabels[report.status];
export const reportError = error => errors[error?.code || error?.message] || errorText(error);
export const originText = report => report.is_manual ? " · 手动生成" : report.is_preview ? " · 试生成" : "";
export const progressStages = {waiting_material:"等待新闻快照", reusing_material:"复用新闻快照", indexing:"读取新闻快照", capturing:"读取时间范围内新闻", preparing:"准备模型与材料",
  selecting:"分批筛选新闻", merging:"归并候选新闻", checking_material:"校验候选新闻", repairing_material:"修正候选新闻",
  reading:"整理全期新闻", delegating:"协作整理与写作", planning:"统筹主题与跨日进展", drafting:"编写报告章节", reviewing:"复核报告全文",
  writing:"编写报告", validating:"校验内容与来源", repairing:"修正报告", saving:"保存报告", done:"已完成"};
export const progressText = report => {
  const p = report.progress || {};
  const unit = ["planning", "drafting", "reviewing"].includes(p.stage) ? "章" : "批";
  return (progressStages[p.stage] || "正在准备生成") + (Number.isInteger(p.total) && p.total > 0 ? ` · 已完成 ${p.completed || 0} / ${p.total} ${unit}` : "");
};
