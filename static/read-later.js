// Cache preparation never expresses the user's intention to read later.
export function readLaterPending(item) {
  return !!item.read_later_at;
}

export function readLaterState(item, saving = false) {
  let phase;
  if (item.read_later_done_at && !item.read_later_at) phase = "done";
  else if (item.archived_version && !item.detail_ready) phase = "unavailable";
  else if (!item.read_later_at) phase = "idle";
  else if (item.detail_ready) phase = "ready";
  else if (item.detail_status === "running") phase = "running";
  else if (saving) phase = item.read_later_at ? "pending" : "idle";
  else if (item.detail_status === "pending") phase = "pending";
  else if (item.read_later_at || ["failed", "skipped", "success"].includes(item.detail_status)) phase = "failed";
  else phase = "idle";
  const [label, hint] = {
    idle: ["稍后再看", "点击加入待看并抓取全文"],
    pending: ["排队中", "点击取消排队"],
    running: ["抓取中", "抓取已开始，完成后进入全文待看"],
    ready: ["全文待看", "点击标记为已看完"],
    failed: ["抓取失败", "点击重新抓取"],
    done: ["已看完", "点击重新加入待阅读"],
    unavailable: ["无历史正文", item.read_later_at ? "点击移出待阅读，或查看同链接新闻" : "请查看同链接新闻"],
  }[phase];
  return { phase, label, hint, active: ["pending", "running", "ready"].includes(phase) || (phase === "unavailable" && !!item.read_later_at) };
}
