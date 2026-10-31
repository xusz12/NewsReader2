export const defaultReportWindows = {
  morning: {start_day:-1, start:"21:30", end:"08:30"},
  noon: {start_day:0, start:"08:30", end:"14:30"},
  evening: {start_day:-1, start:"21:30", end:"21:30"},
  weekly: {start:"00:00", end:"00:00"},
  monthly: {start:"00:00", end:"00:00"},
};
export const windowDays = (kind, window) => kind === "weekly" ? ["上周一", "本周一"]
  : kind === "monthly" ? ["上月 1 日", "本月 1 日"] : [window.start_day === -1 ? "前日" : "当日", "当日"];
export const windowLabel = (kind, window) => {
  const [start, end] = windowDays(kind, window);
  return `${start} ${window.start}—${start === end ? "" : end+" "}${window.end}`;
};
export const windowError = (window, time) => window.start_day === 0 && window.start >= window.end
  ? "新闻起点必须早于终点" : time < window.end ? `生成时间不能早于新闻窗口结束（${window.end}）` : "";
