import { errorText } from "./ui.js";

const statusLabels = {pending: "等待生成", running: "正在生成", failed: "生成失败", skipped: "已跳过", done: ""};
const errors = {
  report_index_failed: "索引刷新失败，请检查新闻目录后重试。",
  report_index_source_missing: "索引刷新失败：新闻目录或文件不存在，请检查路径及本地同步状态。",
  report_index_not_directory: "索引刷新失败：配置的新闻路径不是文件夹，请在设置中修正。",
  report_index_permission_denied: "索引刷新失败：没有权限读取新闻目录或文件，请检查访问权限。",
  report_index_database_locked: "索引刷新失败：新闻数据库正在被其他操作占用。",
  report_index_disk_full: "索引刷新失败：磁盘空间不足，请释放空间后重试。",
  report_index_invalid_file: "索引刷新失败：新闻文件的编码或格式无法解析。",
  report_index_database_error: "索引刷新失败：无法读写新闻数据库，请检查数据库文件和磁盘状态。",
  report_intraday_expired: "启动时已错过本期，早报和午报不补生成。",
  report_model_unconfigured: "请先在设置中配置模型与 Agent。",
  report_model_capacity_unknown: "无法确认所选模型的上下文容量，请检查模型目录或本机模型配置后重试。",
  report_database_changed: "当前新闻库已切换，请刷新报告列表后重试。",
  report_retry_unavailable: "暂时无法重试，请刷新报告列表后再试。",
  report_preview_invalid: "该期尚未结束或排期无效，请重新选择一期。",
  report_preview_conflict: "本次请求已用于另一份报告，请重新打开试生成。",
  report_preview_only: "只有试生成报告可以在这里删除。",
  report_preview_running: "报告仍在生成，请完成后再删除。",
  report_window_invalid: "请选择有效的起止时间，结束时间须晚于开始时间，且不能晚于当前时间。",
  report_manual_conflict: "本次请求已用于另一份报告，请重新打开新增日报。",
  report_delete_running: "报告仍在生成，请完成后再删除。",
  report_deleted: "这份报告已删除，如需重新生成，请重新打开新增日报。",
  report_model_failed: "生成失败，请检查模型与 Agent 设置后重试。",
  report_model_timeout: "模型响应超时，可以重试。",
  report_invalid_result: "生成内容未通过完整性校验，可以重试。",
  report_input_too_large: "本期资料超出处理上限，报告尚未生成。",
  report_interrupted: "上次生成中断，可以重试。",
  report_tools_not_allowed: "模型尝试了资料范围外的操作，报告未保存，可以重试。",
};
export const failureText = report => errors[report.error] || "生成暂未完成，请稍后重试。";
export const statusText = report => report.status === "pending" && report.error ? "等待重试" : statusLabels[report.status];
export const reportError = error => errors[error?.code || error?.message] || errorText(error);
export const originText = report => report.is_manual ? " · 手动生成" : report.is_preview ? " · 试生成" : "";
export const progressStages = {indexing:"刷新新闻索引", capturing:"读取时间范围内新闻", preparing:"准备模型与材料",
  selecting:"分批筛选新闻", merging:"归并候选新闻", checking_material:"校验候选新闻", repairing_material:"修正候选新闻",
  writing:"编写报告", validating:"校验内容与来源", repairing:"修正报告", saving:"保存报告", done:"已完成"};
export const progressText = report => {
  const p = report.progress || {};
  return (progressStages[p.stage] || "正在准备生成") + (Number.isInteger(p.total) && p.total > 0 ? ` · 已完成 ${p.completed || 0} / ${p.total} 批` : "");
};
