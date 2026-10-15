import {
  $,
  $$,
  api,
  escapeHTML as e,
  safeURL,
  errorText,
  toast,
  busy,
  storage,
  persist,
  icon,
  sourceKey,
  sourceBadge,
  markdown,
  inlineMarkdown,
  focusTrap,
} from "./ui.js";
import { SettingsPanel } from "./settings-panel.js";
import { AgentPanel } from "./agent-panel.js";

const state = {
  collection: "feed",
  filter: "all",
  source: "all",
  query: "",
  items: [],
  selected: null,
  detail: null,
  page: 0,
  cursor: null,
  more: true,
  total: 0,
  loading: false,
  sequence: 0,
  detailSequence: 0,
  selectedIds: new Set(),
  pendingStates: new Set(),
  ideaType: "all",
  ideas: [],
  idea: null,
  noteDrafts: new Map(),
  bodyMode: "translated",
};
const labels = {
  feed: "新闻流",
  read_later: "稍后再看",
  important: "重要新闻",
  favorites: "收藏",
  notes: "想法",
};
const stateLabels = {
  read: "已读",
  important: "重要",
  read_later: "稍后再看",
  favorite: "收藏",
};
const shortcuts = { m: "read", i: "important", s: "read_later", f: "favorite" };
let requestController,
  detailTimer,
  searchTimer,
  selectionText = "",
  lastFocus;
const settings = new SettingsPanel({
  onSaved: () => {
    loadSources();
    loadFeed();
    refreshBadges();
  },
  onSources: renderSources,
});
const agent = new AgentPanel({
  onArchive: (id, result) => {
    const item = state.items.find((x) => x.id === id);
    if (item) Object.assign(item, result);
    if (state.selected?.id === id) {
      const draft = state.noteDrafts.get(id);
      if (draft !== undefined && draft !== (state.detail?.note?.note || "")) {
        toast("归档已保存；编辑中的想法草稿已保留，请合并后再保存。");
      } else {
        state.noteDrafts.delete(id);
        state.detail = { ...state.detail, note: result.note };
        const input = $("#articleNote");
        if (input) input.value = result.note?.note || "";
      }
    }
    renderFeed();
  },
});

function feedParams() {
  const collection =
    { important: "important", later: "read_later", notes: "notes" }[
      state.filter
    ] || state.collection;
  return {
    collection,
    read_filter:
      state.filter === "unread"
        ? "unread"
        : state.filter === "read"
          ? "read"
          : "all",
    source_filter: state.source,
    q: state.query,
    sort_order: "reverse",
    per: "30",
  };
}
function renderFilters() {
  const filters =
    state.collection === "read_later"
      ? [
          ["all", "全部"],
          ["unread", "待阅读"],
          ["read", "已读"],
        ]
      : [
          ["all", "全部"],
          ["unread", "未读"],
          ["important", "高价值焦点"],
          ["later", "稍后再读"],
          ["notes", "含想法"],
        ];
  $("#feedColFeed .feed-filter-chips").innerHTML = filters
    .map(
      ([key, label]) =>
        `<button class="filter-chip ${state.filter === key ? "active" : ""}" data-filter="${key}" type="button" aria-pressed="${state.filter === key}">${label}${state.filter === key && !state.loading ? ` (${state.total})` : ""}</button>`,
    )
    .join("");
  $("#feedColIdeas .feed-filter-chips").innerHTML =
    [
      ["all", "全部"],
      ["article", "新闻想法"],
      ["standalone", "独立想法"],
    ]
      .map(
        ([key, label]) =>
          `<button class="filter-chip ${state.ideaType === key ? "active" : ""}" data-idea-type="${key}" type="button">${label}</button>`,
      )
      .join("") +
    '<button class="filter-chip" id="newIdeaBtn" type="button">＋ 新想法</button>';
}
function card(item) {
  const active = item.id === state.selected?.id;
  const checked = state.selectedIds.has(item.id);
  const text = item.summary || item.note_preview || "";
  return `<li class="news-card ${active ? "active" : ""} ${item.read_at ? "read" : ""}" data-id="${e(item.id)}" tabindex="0" aria-label="${e(item.title)}" aria-current="${active}">
    <div class="news-card-meta"><div class="meta-source-box">${sourceBadge(sourceKey(item))}<span>${e(item.source || item.source_name || "未知来源")}</span></div><div class="meta-time-row"><span>${e(item.time || item.published_at?.slice(11, 16) || "")}</span>${item.read_at ? "" : '<span class="unread-dot" title="未读"></span>'}</div></div>
    <h3 class="news-card-title">${e(item.title || "无标题")}</h3>
    ${text ? `<p class="news-card-snippet">${e(text)}</p>` : ""}
    <div class="news-card-footer"><div class="card-footer-left"><label class="card-selection" title="选择条目"><input type="checkbox" data-select="${e(item.id)}" aria-label="选择 ${e(item.title)}" ${checked ? "checked" : ""}></label><div class="card-status-badges">${item.has_note ? `<span class="card-icon-indicator" title="含想法">${icon("note")}</span>` : ""}${item.detail_ready ? `<span class="card-icon-indicator">${icon("read")} opencli</span>` : ""}</div></div>
    <div class="card-actions">${["important", "read_later", "favorite"].map((flag) => `<button class="card-action-btn ${item[`${flag}_at`] ? "active" : ""}" type="button" data-state="${flag}" aria-pressed="${!!item[`${flag}_at`]}" title="${stateLabels[flag]}" ${state.pendingStates.has(item.id) ? "disabled" : ""}>${icon(flag)}<span>${stateLabels[flag]}</span></button>`).join("")}</div></div></li>`;
}
function renderFeed() {
  const list = $("#feedCardList");
  const scroll = list.scrollTop;
  let lastDate = "";
  list.innerHTML =
    state.items
      .map((item) => {
        const date =
          item.date_key ||
          item.date ||
          item.published_at?.slice(0, 10) ||
          "日期未知";
        const heading =
          date !== lastDate
            ? `<li class="feed-date-heading">${e(date)}</li>`
            : "";
        lastDate = date;
        return heading + card(item);
      })
      .join("") +
    (state.loading
      ? '<li class="list-message" role="status">正在读取新闻…</li>'
      : !state.items.length
        ? '<li class="list-message">没有符合条件的新闻。<br>试试其他筛选，或点击刷新索引。</li>'
        : state.more
          ? '<li class="load-more-row"><button class="btn-secondary" id="loadMoreBtn" type="button">加载更多</button></li>'
          : '<li class="list-message feed-end">已读到列表末尾</li>');
  list.scrollTop = scroll;
  renderFilters();
  renderBatch();
}
async function loadFeed(append = false) {
  if (state.collection === "notes") return loadIdeas();
  if (append && (state.loading || !state.more)) return;
  requestController?.abort();
  requestController = new AbortController();
  const sequence = ++state.sequence;
  if (!append) {
    state.items = [];
    state.page = 0;
    state.cursor = null;
    state.selectedIds.clear();
    $("#feedCardList").scrollTop = 0;
  }
  state.loading = true;
  renderFeed();
  const params = new URLSearchParams({
    ...feedParams(),
    page: String(state.page + 1),
  });
  if (append && state.cursor) {
    params.set("cursor_date", state.cursor.date_key);
    params.set("cursor_published_at", state.cursor.published_at);
    params.set("cursor_id", state.cursor.id);
  }
  try {
    const data = state.query
      ? await searchNews(sequence, requestController.signal)
      : await api(`/api/news?${params}`, { signal: requestController.signal });
    if (sequence !== state.sequence) return;
    const known = new Set(state.items.map((item) => item.id));
    state.items.push(...data.items.filter((item) => !known.has(item.id)));
    state.page = data.page;
    state.total = data.total;
    state.more = data.has_more;
    state.cursor = data.next_cursor;
    state.loading = false;
    if (state.selected)
      state.selected =
        state.items.find((item) => item.id === state.selected.id) ||
        state.selected;
    renderFeed();
    readerActions();
    if (!state.selected && state.items.length && window.innerWidth > 920)
      selectNews(state.items[0], false);
    setIndexStatus(`新闻库已连接 · ${data.total} 篇`);
  } catch (error) {
    if (error.name === "AbortError" || sequence !== state.sequence) return;
    state.loading = false;
    renderFeed();
    const node = document.createElement("li");
    node.className = "list-message error-state";
    node.textContent = errorText(error);
    const retry = document.createElement("button");
    retry.className = "btn-secondary";
    retry.textContent = "重试";
    retry.onclick = () => loadFeed(append);
    node.append(retry);
    $("#feedCardList").append(node);
    setIndexStatus("新闻库连接失败，请重试", true);
  }
}
async function searchNews(sequence, signal) {
  const params = feedParams();
  const matches = [];
  let page = 1,
    pages = 1;
  do {
    const data = await api(
      `/api/search?${new URLSearchParams({ q: state.query, page: String(page), per: "100" })}`,
      { signal },
    );
    matches.push(...data.items);
    pages = data.pages;
    page++;
  } while (page <= pages && sequence === state.sequence);
  const items = matches.filter((item) => {
    if (
      params.source_filter !== "all" &&
      sourceKey(item) !== params.source_filter
    )
      return false;
    if (params.collection === "favorites" && !item.favorite_at) return false;
    if (params.collection === "important" && !item.important_at) return false;
    if (params.collection === "notes" && !item.has_note) return false;
    if (params.collection === "read_later") {
      if (params.read_filter === "unread") return !!item.read_later_at;
      if (params.read_filter === "read")
        return !!item.detail_ready && !item.read_later_at;
      return !!item.read_later_at || !!item.detail_ready;
    }
    return (
      params.read_filter === "all" ||
      (params.read_filter === "unread" ? !item.read_at : !!item.read_at)
    );
  });
  return {
    items,
    total: items.length,
    page: 1,
    has_more: false,
    next_cursor: null,
  };
}
function setIndexStatus(message, failed = false) {
  $("#pipelineStatus").innerHTML =
    `<span class="pipeline-dot ${failed ? "failed" : ""}"></span><span>${e(message)}</span>`;
}
async function refreshBadges() {
  try {
    const data = await api("/api/nav-summary");
    $("#unreadBadge").textContent = data.summary.feed_unread || "0";
    $("#readLaterNavBadge").textContent = data.summary.read_later_unread || "0";
    $("#mobUnreadBadge").textContent = data.summary.feed_unread || "0";
  } catch {
    /* Feed and settings report connectivity separately. */
  }
}
async function loadSources() {
  try {
    const data = await api("/api/sources?collection=feed&read_filter=all");
    settings.sourceCounts = data.sources;
    renderSources();
  } catch (error) {
    $("#sourceFilters").textContent = errorText(error);
  }
}
function renderSources() {
  const groups = settings.saved?.feed_source_subkeys?.groups || [];
  const hidden = new Set(settings.saved?.feed?.hidden_source_subkeys || []);
  const sources = [...(settings.sourceCounts || [])];
  for (const group of groups)
    if (!sources.some((source) => source.key === group.source_key))
      sources.push({ key: group.source_key, label: group.label, count: 0 });
  $("#sourceFilters").innerHTML =
    `<button class="source-row source-filter-button ${state.source === "all" ? "selected" : ""}" type="button" data-source="all"><span>全部来源</span><span class="nav-badge">${(settings.sourceCounts || []).reduce((n, s) => n + s.count, 0)}</span></button>` +
    sources
      .map((source) => {
        const group = groups.find((g) => g.source_key === source.key);
        const allHidden =
          group?.items?.length &&
          group.items.every((item) => hidden.has(item.key));
        return `<div class="source-group"><div class="source-row"><button class="source-filter-button ${state.source === source.key ? "selected" : ""}" type="button" data-source="${e(source.key)}"><span class="source-row-info">${sourceBadge(source.key)}<span>${e(source.label)}</span></span></button>${group?.items?.length ? `<button class="source-switch ${allHidden ? "" : "active"}" type="button" role="switch" aria-checked="${!allHidden}" aria-label="显示 ${e(source.label)}" data-source-toggle="${e(source.key)}"></button>` : `<span class="nav-badge">${source.count}</span>`}</div>${group?.items?.length > 1 ? `<details class="source-subgroup"><summary>二级栏目 (${group.items.length})</summary><div class="sub-sources-drawer show">${group.items.map((item) => `<label class="sub-source-item"><input type="checkbox" data-source-key="${e(item.key)}" ${hidden.has(item.key) ? "" : "checked"}><span>${e(item.label)}</span></label>`).join("")}</div></details>` : ""}</div>`;
      })
      .join("");
}
function switchCollection(collection) {
  if (!labels[collection]) return;
  stashNote();
  state.collection = collection;
  state.filter = "all";
  state.query = "";
  $("#feedSearchInput").value = "";
  $("#ideasSearchInput").value = "";
  state.sequence++;
  requestController?.abort();
  state.loading = false;
  closeNav();
  exitReader();
  const ideas = collection === "notes";
  $("#feedColFeed").classList.toggle("active", !ideas);
  $("#detailPanelFeed").classList.toggle("active", !ideas);
  $("#feedColIdeas").classList.toggle("active", ideas);
  $("#detailPanelIdeas").classList.toggle("active", ideas);
  $$("[data-collection]").forEach((button) => {
    const active = button.dataset.collection === collection;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (ideas) {
    agent.suspend();
    loadIdeas();
  } else {
    loadFeed();
    if (state.selected) agent.select(state.selected, state.detail?.agent);
  }
  renderFilters();
}
function stashNote() {
  const input = $("#articleNote");
  if (input && state.selected && state.detail) {
    if (input.value === (state.detail.note?.note || ""))
      state.noteDrafts.delete(state.selected.id);
    else state.noteDrafts.set(state.selected.id, input.value);
  }
  const idea = $("#ideaNote");
  if (idea && state.idea) {
    if (idea.value === (state.idea.note || ""))
      state.noteDrafts.delete(state.idea.idea_id);
    else state.noteDrafts.set(state.idea.idea_id, idea.value);
  }
}
async function selectNews(item, mobile = true) {
  if (!item) return;
  stashNote();
  clearTimeout(detailTimer);
  state.selected = item;
  state.detail = null;
  state.bodyMode = "translated";
  const sequence = ++state.detailSequence;
  renderFeed();
  renderReader();
  $("#detailScrollArea").scrollTop = 0;
  agent.select(item, null);
  if (mobile) enterReader();
  try {
    const data = await api(`/api/news/${encodeURIComponent(item.id)}/detail`);
    if (sequence !== state.detailSequence) return;
    state.detail = data;
    for (const flag of ["read", "important", "read_later", "favorite"])
      item[`${flag}_at`] = data[`${flag}_at`];
    renderReader();
    renderFeed();
    agent.select(item, data.agent);
    pollDetail(item, sequence);
    if (state.collection === "feed" && item.url)
      api("/api/reading-checkpoint", {
        method: "PUT",
        body: {
          scope: "feed",
          item_id: item.id,
          url: item.url,
          title: item.title,
        },
      }).catch(() => {});
  } catch (error) {
    if (sequence !== state.detailSequence) return;
    $("#detailScrollArea").innerHTML =
      `<div class="list-message error-state">${e(errorText(error))}<button class="btn-secondary" id="retryReaderBtn" type="button">重新加载</button></div>`;
    $("#retryReaderBtn").onclick = () => selectNews(item, false);
  }
}
function pollDetail(item, sequence) {
  clearTimeout(detailTimer);
  if (
    !["pending", "running"].includes(state.detail?.detail_status) &&
    !["pending", "running"].includes(state.detail?.ai_status)
  )
    return;
  detailTimer = setTimeout(async () => {
    try {
      const data = await api(`/api/news/${encodeURIComponent(item.id)}/detail`);
      if (sequence !== state.detailSequence) return;
      const changed =
        data.detail_status !== state.detail.detail_status ||
        data.ai_status !== state.detail.ai_status;
      state.detail = data;
      if (changed) {
        stashNote();
        renderReader();
        agent.select(item, data.agent);
      }
      pollDetail(item, sequence);
    } catch {
      if (sequence === state.detailSequence)
        toast("正文状态刷新失败，可点击重新加载。");
    }
  }, 3000);
}
function readerActions() {
  const item = state.selected;
  $$("[data-state]", $("#detailPanelFeed .detail-toolbar")).forEach(
    (button) => {
      button.disabled = !item || state.pendingStates.has(item.id);
      button.classList.toggle("active", !!item?.[`${button.dataset.state}_at`]);
      button.setAttribute(
        "aria-pressed",
        String(!!item?.[`${button.dataset.state}_at`]),
      );
    },
  );
  $("#readBtnLabel").textContent = item?.read_at ? "标记未读" : "标记已读";
  const url = safeURL(item?.url || "");
  $("#articleSourceLink").href = url || "#";
  $("#articleSourceLink").hidden = !item?.url || !url;
}
function renderReader() {
  readerActions();
  const item = state.selected,
    payload = state.detail;
  if (!item) {
    $("#detailScrollArea").innerHTML =
      '<div class="reader-empty"><span class="empty-book">▤</span><h2>从一篇新闻开始</h2><p>选择左侧新闻，阅读正文、记录想法。</p></div>';
    $("#agentDock").hidden = true;
    return;
  }
  const detail = payload?.detail;
  $("#agentDock").hidden = false;
  $("#detailPanelFeed .cache-label").textContent = !payload
    ? "正在读取正文…"
    : detail?.content
      ? "opencli 全文就绪"
      : ["pending", "running"].includes(payload.detail_status)
        ? "正文抓取中…"
        : payload.detail_status === "failed"
          ? "正文抓取失败"
          : "尚未抓取正文";
  $("#detailPanelFeed .cache-time").textContent = detail?.fetched_at
    ? `${detail.fetched_at.slice(11, 16)} 抓取`
    : "";
  $("#detailPanelFeed .cache-pulse-dot").classList.toggle(
    "waiting",
    !detail?.content,
  );
  const ai = payload?.ai;
  let points = [];
  try {
    const value = JSON.parse(ai?.key_points_zh || "[]");
    points = Array.isArray(value) ? value : [];
  } catch {
    points = (ai?.key_points_zh || "").split("\n").filter(Boolean);
  }
  const body =
    state.bodyMode === "original"
      ? detail?.content
      : ai?.body_zh || detail?.content;
  const note = state.noteDrafts.get(item.id) ?? payload?.note?.note ?? "";
  $("#detailScrollArea").innerHTML =
    `<article class="article-header"><div class="article-meta-top"><div class="article-author-info">${sourceBadge(sourceKey(item))}<strong>${e(item.source || "未知来源")}</strong>${detail?.author ? ` · ${e(detail.author)}` : ""}</div><span>发布于 ${e(item.published_at || item.date || "日期未知")}</span></div><h1 class="article-title" id="articleMainTitle">${e(item.title)}</h1><div class="article-meta-bottom">${body ? `<span>估读：${Math.max(1, Math.ceil(body.length / 650))} 分钟</span>` : ""}<span>信源：${e(item.source_file?.split("/").at(-1) || item.source || "")}</span><button class="reader-action-btn" id="fetchArticleBtn" type="button" ${!payload || ["pending", "running"].includes(payload?.detail_status) || ["pending", "running"].includes(payload?.ai_status) ? "disabled" : ""}>${detail?.content ? "重新翻译" : "抓取全文"}</button></div></article>
    ${ai && (ai.conclusion_zh || points.length) ? `<section class="digest-box" aria-label="本篇结构化精读"><div class="digest-header"><div class="digest-badge">${icon("read")}<span>DeepSeek 结构化精读</span></div><div class="digest-mode-tabs"><button class="digest-mode-btn ${state.bodyMode === "translated" ? "active" : ""}" data-body-mode="translated" type="button">中文精读</button><button class="digest-mode-btn ${state.bodyMode === "original" ? "active" : ""}" data-body-mode="original" type="button">英文原文</button></div></div>${ai.conclusion_zh ? `<p class="digest-lead">${inlineMarkdown(ai.conclusion_zh)}</p>` : ""}<ul class="digest-points">${points.map((point, i) => `<li class="digest-point-item"><span class="point-index">${i + 1}</span><span>${inlineMarkdown(String(point))}</span></li>`).join("")}</ul></section>` : ""}
    <div class="article-body" id="articleBodyContent">${body ? markdown(body) : `<p>${e(item.summary || "暂无正文。点击“抓取全文”获取，或查看原文。")}</p>`}</div>
    ${detail?.media_images?.length ? `<div class="article-media">${detail.media_images.map((image) => (safeURL(image.cached_url || image.url) ? `<img src="${e(safeURL(image.cached_url || image.url))}" alt="${e(image.alt || "新闻配图")}" loading="lazy">` : "")).join("")}</div>` : ""}
    ${payload?.job?.last_error || payload?.ai_job?.last_error ? `<div class="inline-error">${e(errorText({ error: payload.job?.last_error || payload.ai_job.last_error }))}</div>` : ""}
    <section class="research-workspace-box" aria-label="我的想法"><div class="research-box-title"><span>本篇研究沉淀 · 我的想法</span><span class="note-save-status" id="noteSaveStatus"></span></div><textarea class="note-textarea" id="articleNote" aria-label="我的想法" placeholder="记录你的理解、判断与问题…">${e(note)}</textarea><div class="note-actions"><span class="muted">仅保存在本地新闻库</span><button class="btn-primary" id="saveArticleNoteBtn" type="button" ${!payload || !item.url ? "disabled" : ""}>保存想法</button></div></section>`;
  $("#fetchArticleBtn").onclick = (event) =>
    busy(event.currentTarget, async () => {
      await api(`/api/news/${encodeURIComponent(item.id)}/detail/retry`, {
        method: "POST",
        body: detail?.content ? { mode: "ai" } : {},
      });
      toast(detail?.content ? "翻译任务已提交" : "正文抓取已提交");
      stashNote();
      await selectNews(item, false);
    });
  $("#saveArticleNoteBtn").onclick = (event) =>
    busy(event.currentTarget, () => saveArticleNote(item));
  $("#articleNote").oninput = (event) => {
    state.noteDrafts.set(item.id, event.target.value);
    $("#noteSaveStatus").textContent = "尚未保存";
  };
}
async function saveArticleNote(item) {
  const value = $("#articleNote").value;
  const data = await api(`/api/news/${encodeURIComponent(item.id)}/note`, {
    method: "PUT",
    body: { note: value },
  });
  Object.assign(item, {
    has_note: data.has_note,
    note_preview: data.note_preview,
  });
  if (state.noteDrafts.get(item.id) === value) state.noteDrafts.delete(item.id);
  if (state.selected?.id === item.id) {
    state.detail.note = data.note;
    $("#noteSaveStatus").textContent =
      $("#articleNote").value === value ? "已保存" : "尚未保存";
  }
  renderFeed();
  toast("想法已保存");
}
async function toggleState(item, flag, force) {
  if (!item || state.pendingStates.has(item.id)) return;
  state.pendingStates.add(item.id);
  readerActions();
  const value = force ?? !item[`${flag}_at`];
  try {
    const data = await api(`/api/news/${encodeURIComponent(item.id)}/state`, {
      method: "PATCH",
      body: { [flag]: value },
    });
    Object.assign(item, data.state || data);
    const loaded = state.items.find((row) => row.id === item.id);
    if (loaded && loaded !== item) Object.assign(loaded, data.state || data);
    // The API returns the authoritative timestamps at the top level.
    if (!Object.hasOwn(data, `${flag}_at`) && !data.state)
      item[`${flag}_at`] = value ? new Date().toISOString() : null;
    if (state.detail && state.selected?.id === item.id)
      state.detail[`${flag}_at`] = item[`${flag}_at`];
    toast(`${value ? "已标记" : "已取消"}${stateLabels[flag]}`);
    refreshBadges();
  } catch (error) {
    toast(errorText(error));
  } finally {
    state.pendingStates.delete(item.id);
    renderFeed();
    readerActions();
  }
}
function renderBatch() {
  const bar = $("#batchActionBar");
  const count = state.selectedIds.size;
  bar.classList.toggle("show", count > 0);
  bar.innerHTML = `<span>已选中 ${count} 篇</span><div class="batch-btn-group"><button class="batch-btn" data-batch="read" type="button">标为已读</button><button class="batch-btn" data-batch="read_later" type="button">稍后读</button><button class="batch-btn" data-batch="cancel" type="button">取消</button></div>`;
}
async function batchAction(flag, button) {
  if (flag === "cancel") {
    state.selectedIds.clear();
    renderFeed();
    return;
  }
  await busy(button, async () => {
    const ids = [...state.selectedIds];
    let failed = 0;
    for (const id of ids) {
      try {
        const data = await api(`/api/news/${encodeURIComponent(id)}/state`, {
          method: "PATCH",
          body: { [flag]: true },
        });
        Object.assign(
          state.items.find((x) => x.id === id) || {},
          data.state || data,
        );
        state.selectedIds.delete(id);
      } catch {
        failed++;
      }
    }
    renderFeed();
    readerActions();
    refreshBadges();
    toast(
      failed
        ? `${ids.length - failed} 篇已保存，${failed} 篇失败，可重试`
        : `${ids.length} 篇已保存`,
    );
  });
}
async function loadIdeas() {
  const sequence = ++state.sequence;
  $("#ideasCardList").innerHTML =
    '<li class="list-message" role="status">正在读取想法…</li>';
  try {
    // Load only the retained idea types. Backend pagination remains unchanged.
    const types =
      state.ideaType === "all" ? ["article", "standalone"] : [state.ideaType];
    const arrays = await Promise.all(
      types.map(async (type) => {
        let page = 1,
          more = true,
          rows = [];
        while (more && sequence === state.sequence) {
          const data = await api(
            `/api/ideas?type=${type}&page=${page}&per=100`,
          );
          rows.push(...data.items);
          more = data.has_more;
          page++;
        }
        return rows;
      }),
    );
    if (sequence !== state.sequence) return;
    state.ideas = arrays
      .flat()
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    renderIdeas();
    if (!state.idea && state.ideas.length && window.innerWidth > 920)
      selectIdea(state.ideas[0], false);
    else if (!state.idea)
      $("#ideaDetailBody").innerHTML =
        '<div class="reader-empty"><h2>把阅读变成自己的想法</h2><p>从新闻中记录，或新建一条独立想法。</p></div>';
  } catch (error) {
    if (sequence === state.sequence)
      $("#ideasCardList").innerHTML =
        `<li class="list-message error-state">${e(errorText(error))}<button id="retryIdeasBtn" class="btn-secondary" type="button">重试</button></li>`;
  }
}
function renderIdeas() {
  const q = $("#ideasSearchInput").value.trim().toLowerCase();
  const ideas = state.ideas.filter((idea) =>
    `${idea.title} ${idea.note}`.toLowerCase().includes(q),
  );
  $("#ideasCardList").innerHTML =
    ideas
      .map(
        (idea) =>
          `<li class="news-card ${state.idea?.idea_id === idea.idea_id ? "active" : ""}" data-idea-id="${e(idea.idea_id)}" tabindex="0"><div class="news-card-meta"><div class="meta-source-box"><span class="source-icon-badge idea">想</span>${e(idea.idea_context_label)}</div><span>${e(idea.updated_at?.slice(0, 16) || "")}</span></div><h3 class="news-card-title">${e(idea.idea_type === "standalone_note" ? idea.note?.split("\n")[0] : idea.title)}</h3><p class="news-card-snippet">${e(idea.note)}</p><div class="news-card-footer"><span class="card-tag">${e(idea.source || "独立想法")}</span></div></li>`,
      )
      .join("") ||
    '<li class="list-message">暂无想法。点击“新想法”开始记录。</li>';
}
function selectIdea(idea, mobile = true) {
  stashNote();
  state.idea = idea;
  const value = state.noteDrafts.get(idea.idea_id) ?? idea.note ?? "";
  $("#ideaDetailBody").innerHTML =
    `<div class="article-header"><div class="article-meta-top"><span>${e(idea.idea_context_label || "独立想法")}</span><span>${e(idea.updated_at || "")}</span></div><h1 class="article-title">${e(idea.idea_type === "article_note" ? idea.title : "记录一个新想法")}</h1></div><div class="research-workspace-box"><textarea id="ideaNote" class="note-textarea idea-editor" maxlength="${idea.idea_type === "article_note" ? 50000 : 5000}" aria-label="编辑想法" placeholder="写下你想记住的观点…">${e(value)}</textarea><div class="note-actions"><span id="ideaSaveStatus" class="muted"></span>${idea.idea_type === "article_note" ? '<button class="btn-secondary" id="ideaArticleBtn" type="button">阅读关联新闻 →</button>' : idea.standalone_id ? '<button class="reader-action-btn danger" id="deleteIdeaBtn" type="button">删除想法</button>' : ""}</div></div>`;
  $("#ideaNote").oninput = (event) => {
    state.noteDrafts.set(idea.idea_id, event.target.value);
    $("#ideaSaveStatus").textContent = "尚未保存";
  };
  renderIdeas();
  if (mobile) enterReader();
}
async function saveIdea() {
  const idea = state.idea;
  if (!idea) return;
  const value = $("#ideaNote").value;
  if (!value.trim() && idea.idea_type !== "article_note") {
    toast("先写下一点想法再保存");
    return;
  }
  const result = await api(
    idea.idea_type === "article_note"
      ? `/api/news/${encodeURIComponent(idea.id)}/note`
      : `/api/standalone-ideas${idea.standalone_id ? `/${idea.standalone_id}` : ""}`,
    {
      method:
        idea.idea_type === "article_note"
          ? "PUT"
          : idea.standalone_id
            ? "PATCH"
            : "POST",
      body: { note: value },
    },
  );
  if (state.noteDrafts.get(idea.idea_id) === value)
    state.noteDrafts.delete(idea.idea_id);
  Object.assign(idea, result.idea || { note: result.note?.note || "" });
  if (state.idea === idea)
    $("#ideaSaveStatus").textContent =
      $("#ideaNote").value === value ? "已保存" : "尚未保存";
  toast("想法已保存");
  await loadIdeas();
}
function enterReader() {
  if (window.innerWidth <= 920)
    $("#feedView").classList.add("mobile-reader-active");
}
function exitReader() {
  $("#feedView").classList.remove("mobile-reader-active");
}
function openNav() {
  $("#leftNavDrawer").inert = false;
  $("#leftNavDrawer").classList.add("drawer-open");
  $("#navDrawerBackdrop").classList.add("active");
}
function closeNav() {
  $("#leftNavDrawer").inert = window.innerWidth <= 920;
  $("#leftNavDrawer").classList.remove("drawer-open");
  $("#navDrawerBackdrop").classList.remove("active");
}
function shortcutsDialog(open) {
  const dialog = $("#shortcutsModal");
  dialog.classList.toggle("show", open);
  dialog.inert = !open;
  if (open) {
    lastFocus = document.activeElement;
    $("button", dialog).focus();
  } else lastFocus?.focus();
}

// Delegated handlers keep the dynamic cards and editor actions on one state source.
document.addEventListener("click", (event) => {
  const button = event.target.closest("button,a,input");
  if (button?.dataset.collection) switchCollection(button.dataset.collection);
  if (button?.dataset.filter) {
    state.filter = button.dataset.filter;
    loadFeed();
  }
  if (button?.dataset.ideaType) {
    state.ideaType = button.dataset.ideaType;
    renderFilters();
    loadIdeas();
  }
  if (button?.dataset.source) {
    state.source = button.dataset.source;
    renderSources();
    closeNav();
    loadFeed();
  }
  if (button?.dataset.sourceToggle)
    busy(button, () => settings.toggleSourceGroup(button.dataset.sourceToggle));
  if (button?.dataset.state) {
    const id = button.closest("[data-id]")?.dataset.id;
    toggleState(
      id ? state.items.find((x) => x.id === id) : state.selected,
      button.dataset.state,
    );
  }
  if (button?.dataset.batch) batchAction(button.dataset.batch, button);
  if (button?.dataset.bodyMode) {
    stashNote();
    state.bodyMode = button.dataset.bodyMode;
    renderReader();
  }
  if (button?.dataset.closeShortcuts !== undefined) shortcutsDialog(false);
  if (button?.id === "loadMoreBtn") loadFeed(true);
  if (button?.id === "retryIdeasBtn") loadIdeas();
  if (button?.id === "newIdeaBtn")
    selectIdea({ idea_id: "draft", idea_type: "standalone_note", note: "" });
  if (button?.id === "ideaArticleBtn") {
    const item = state.idea;
    switchCollection("feed");
    selectNews(item);
  }
  if (button?.id === "deleteIdeaBtn")
    busy(button, async () => {
      if (!confirm("删除这条独立想法？")) return;
      const id = state.idea.standalone_id;
      await api(`/api/standalone-ideas/${id}`, { method: "DELETE" });
      state.noteDrafts.delete(state.idea.idea_id);
      state.idea = null;
      await loadIdeas();
      toast("想法已删除");
    });
  const card = event.target.closest("#feedCardList [data-id]");
  if (card && !button)
    selectNews(state.items.find((item) => item.id === card.dataset.id));
  const ideaCard = event.target.closest("[data-idea-id]");
  if (ideaCard)
    selectIdea(
      state.ideas.find((idea) => idea.idea_id === ideaCard.dataset.ideaId),
    );
});
document.addEventListener("change", (event) => {
  const input = event.target;
  if (input.dataset.select) {
    if (input.checked) state.selectedIds.add(input.dataset.select);
    else state.selectedIds.delete(input.dataset.select);
    renderBatch();
  }
  if (input.dataset.sourceKey)
    busy(null, () =>
      settings.toggleSource(input.dataset.sourceKey, input.checked),
    );
});
$("#feedSearchInput").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadFeed(), 250);
});
$("#ideasSearchInput").addEventListener("input", renderIdeas);
$("#feedCardList").addEventListener("scroll", () => {
  const list = $("#feedCardList");
  if (
    list.scrollTop + list.clientHeight >= list.scrollHeight - 250 &&
    list.scrollHeight > list.clientHeight
  )
    loadFeed(true);
});
$("#themeSelect").value = document.documentElement.dataset.theme || "system";
$("#themeSelect").onchange = (event) => {
  document.documentElement.dataset.theme = event.target.value;
  persist("newsreader_workbench_theme_mode", event.target.value);
};
$("#leftNavSettingsBtn").onclick = () => settings.toggle();
$("#mobNavSettings").onclick = () => settings.toggle();
$("#openNavBtn").onclick = openNav;
$(".drawer-close-btn").onclick = closeNav;
$("#navDrawerBackdrop").onclick = closeNav;
$$(".mobile-back-btn").forEach((button) => (button.onclick = exitReader));
$("#openShortcutsBtn").onclick = () => shortcutsDialog(true);
$("#shortcutsModal").onclick = (event) => {
  if (event.target.id === "shortcutsModal") shortcutsDialog(false);
};
$("#saveIdeaBtn").onclick = (event) => busy(event.currentTarget, saveIdea);
$("#copyIdeaBtn").onclick = (event) =>
  busy(event.currentTarget, async () => {
    await navigator.clipboard.writeText($("#ideaNote")?.value || "");
    toast("想法已复制");
  });
const refreshButton = document.createElement("button");
refreshButton.id = "refreshIndexBtn";
refreshButton.className = "topbar-tool-btn";
refreshButton.type = "button";
refreshButton.title = "刷新新闻索引";
refreshButton.setAttribute("aria-label", "刷新新闻索引");
refreshButton.innerHTML = icon("refresh");
$("#openShortcutsBtn").before(refreshButton);
refreshButton.onclick = () =>
  busy(refreshButton, async () => {
    setIndexStatus("正在扫描 DailyNews…");
    await api("/api/reindex", { method: "POST" });
    await Promise.all([loadFeed(), loadSources(), refreshBadges()]);
    toast("新闻索引已刷新");
  });

document.addEventListener("keydown", (event) => {
  if (settings.open) {
    settings.handleKey(event);
    return;
  }
  if ($("#shortcutsModal").classList.contains("show")) {
    if (event.key === "Escape") shortcutsDialog(false);
    else focusTrap(event, $("#shortcutsModal"));
    return;
  }
  if (
    $("#leftNavDrawer").classList.contains("drawer-open") &&
    event.key === "Tab"
  ) {
    focusTrap(event, $("#leftNavDrawer"));
    return;
  }
  if (event.key === "Escape") {
    closeNav();
    agent.collapse();
    exitReader();
    $("#selectionPopover").classList.remove("show");
    return;
  }
  if (
    event.target.closest('input,textarea,select,[contenteditable="true"]') ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey ||
    event.isComposing
  )
    return;
  const key = event.key.toLowerCase();
  if (key === ",") {
    event.preventDefault();
    settings.toggle();
  } else if (key === "/") {
    event.preventDefault();
    (state.collection === "notes"
      ? $("#ideasSearchInput")
      : $("#feedSearchInput")
    ).focus();
  } else if (key === "?") {
    event.preventDefault();
    shortcutsDialog(true);
  } else if (key === "a" && state.collection !== "notes") {
    event.preventDefault();
    agent.toggle();
  } else if (shortcuts[key] && state.collection !== "notes") {
    event.preventDefault();
    toggleState(state.selected, shortcuts[key]);
  } else if (["j", "k"].includes(key) && state.collection !== "notes") {
    event.preventDefault();
    const index = state.items.findIndex(
      (item) => item.id === state.selected?.id,
    );
    const next =
      state.items[
        Math.max(
          0,
          Math.min(state.items.length - 1, index + (key === "j" ? 1 : -1)),
        )
      ];
    if (next) {
      selectNews(next, false);
      $(`[data-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({
        block: "nearest",
      });
    }
  } else if (key === "enter" && event.target.matches("[data-id]"))
    selectNews(state.items.find((item) => item.id === event.target.dataset.id));
  else if (key === "enter" && event.target.matches("[data-idea-id]"))
    selectIdea(
      state.ideas.find((idea) => idea.idea_id === event.target.dataset.ideaId),
    );
});
$("#detailScrollArea").addEventListener("mouseup", () => {
  const selection = window.getSelection();
  const text = selection?.toString().trim();
  if (
    !text ||
    !selection.rangeCount ||
    !$("#articleBodyContent")?.contains(selection.anchorNode)
  )
    return;
  selectionText = text.slice(0, 6000);
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  const popover = $("#selectionPopover");
  popover.classList.add("show");
  popover.style.position = "fixed";
  popover.style.left = `${Math.max(8, Math.min(window.innerWidth - 280, rect.left))}px`;
  popover.style.top = `${Math.max(8, rect.top - 44)}px`;
});
$("#quoteAgentBtn").onclick = () => {
  agent.quote(selectionText);
  $("#selectionPopover").classList.remove("show");
};
$("#quoteNoteBtn").onclick = () => {
  const input = $("#articleNote");
  if (input) {
    input.value += (input.value ? "\n\n" : "") + "> " + selectionText;
    input.dispatchEvent(new Event("input"));
    input.focus();
    input.scrollIntoView({ block: "center" });
  }
  $("#selectionPopover").classList.remove("show");
};
document.addEventListener("pointerdown", (event) => {
  if (!event.target.closest("#selectionPopover"))
    $("#selectionPopover").classList.remove("show");
});
window.addEventListener("beforeunload", (event) => {
  stashNote();
  const dirty = state.noteDrafts.size > 0;
  if (dirty || settings.dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("#navFeedBtn").setAttribute("aria-current", "page");
closeNav();
matchMedia("(max-width: 920px)").addEventListener("change", closeNav);
renderFilters();
renderReader();
setIndexStatus("正在连接新闻库…");
Promise.allSettled([
  loadFeed(),
  loadSources(),
  refreshBadges(),
  settings.load(false),
]).then(() => renderSources());
api("/api/version")
  .then((data) => {
    const version = data.version || data.current_version;
    if (version) {
      $(".brand-version").textContent = String(version).startsWith("v")
        ? version
        : `v${version}`;
      document.title = `News Reader ${$(".brand-version").textContent}`;
    }
  })
  .catch(() => {});
