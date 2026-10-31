export const jobErrors = {
  topic_search_required: "未完成联网检索，请检查 Agent 的搜索能力后重试。",
  topic_interrupted: "上次整理中断，可以重试。",
  topic_invalid_result: "整理结果不完整，请重试。",
  topic_invalid_input: "模型返回的日期或内容不符合要求，请重试。",
  topic_source_changed: "新闻资料已变化，请重新整理。",
  topic_runtime_failed: "整理失败，请检查模型与 Agent 设置后重试。",
  topic_timeline_too_large: "现有脉络资料较多，暂时无法一次精简。",
};

export const jobErrorText = job => job?.kind === "milestone"
  ? "横向筛选失败，请检查 Jev 配置后重试。已收录新闻保留。"
  : jobErrors[job?.error] || "整理失败，请检查模型与 Agent 设置。";
