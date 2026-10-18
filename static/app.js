import {
  singlePane,
  drawerNavigation,
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
import { SummaryPanel } from "./summary-panel.js";
import { SelectControls } from "./select-control.js";
import { AgentPanel } from "./agent-panel.js";
import { ResearchNotes } from "./research-notes.js";
import { ReaderHighlights, readerInline, readerMarkdown } from "./reader-highlights.js";

const state = {
  collection: "feed",
  filter: "unread",
  sortOrder: "default",
  source: "all",
  query: "",
  items: [],
  selected: null,
  openedReadLaterId: null,
  detail: null,
  page: 0,
  cursor: null,
  more: true,
  total: 0,
  loading: false,
  sequence: 0,
  detailSequence: 0,
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
  summaries: "新闻总结",
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
  lastFocus;
const selects = new SelectControls(document.body);
const settings = new SettingsPanel({
  selects,
  onOpen: () => {
    stashNote();
    resetDetailSwipe();
    closeNav();
    $("#selectionPopover").classList.remove("show");
    clearTimeout(feedEndTimer);
    feedEndTimer = null;
  },
  onViewChange: () => {
    resetDetailSwipe();
    syncMobileLayers();
  },
  onSaved: () => {
    loadSources();
    loadFeed();
    refreshBadges();
    if (agent.open) agent.refresh();
  },
  onSources: renderSources,
});
const researchNotes = new ResearchNotes({ onUpdate: updateResearchNotes });
function updateResearchNotes(id, result) {
  const item = state.items.find(x => x.id === id);
  const notes = result.notes || (result.note ? [{ ...result.note, id: "legacy", author: "user" }] : []);
  const patch = { has_note: result.has_note, note_preview: result.note_preview, note_count: notes.length };
  if (item) Object.assign(item, patch);
  if (state.selected?.id === id) {
    Object.assign(state.selected, patch);
    state.detail = { ...state.detail, ...patch, note: result.note, notes };
  }
  researchNotes.update(id, { ...result, notes });
  renderFeed();
  refreshBadges();
}
const agent = new AgentPanel({
  onArchive: (id, result) => {
    updateResearchNotes(id, result);
  },
});
const readerHighlights = new ReaderHighlights({
  onQuote: (target, quote) => target === "note" ? researchNotes.open(quote) : agent.quote(quote.text),
  onUpdate: (id, result) => { if (state.selected?.id === id) state.detail = { ...state.detail, ...result }; },
});
const summaries = new SummaryPanel({
  isVisible: () => state.collection === "summaries" && !settings.open,
  enterReader,
  openSettings: async () => {
    if (!settings.open) await settings.toggle();
    settings.selectSection("catchup");
  },
  onCompleted: () => {
    refreshBadges();
    if (!["notes", "summaries"].includes(state.collection)) {
      loadFeed();
      if (state.selected) selectNews(state.selected, false);
    }
  },
});

function feedParams() {
  return {
    collection: state.collection,
    has_note: state.filter === "notes" ? "1" : "0",
    read_filter:
      state.filter === "unread"
        ? "unread"
        : state.filter === "read"
          ? "read"
          : "all",
    source_filter: state.source,
    q: state.query,
    sort_order: state.sortOrder,
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
      : state.collection === "feed"
        ? [["all", "全部"], ["unread", "未读"]]
        : state.collection === "important"
          ? [["all", "全部"], ["notes", "含想法"]]
          : [["all", "全部"]];
  const ascending = ["feed", "read_later"].includes(state.collection) === (state.sortOrder === "default");
  const sortLabel = `排序：${ascending ? "旧到新" : "新到旧"}，点击切换`;
  $("#sortNewsBtn").innerHTML = icon(ascending ? "sortAsc" : "sortDesc");
  $("#sortNewsBtn").setAttribute("aria-label", sortLabel);
  $("#sortNewsBtn").title = sortLabel;
  $("#markAllReadBtn").hidden = !["feed", "read_later"].includes(state.collection);
  const markLabel = state.collection === "read_later" ? "全部看完" : "全部已读";
  $("#markAllReadBtn").innerHTML = icon("checkAll");
  $("#markAllReadBtn").setAttribute("aria-label", markLabel);
  $("#markAllReadBtn").title = markLabel;
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
function cardTimestamp(item) {
  const date = item.published_at?.slice(0, 10) || item.date || item.date_key || "日期未知";
  const time = item.published_at?.slice(11, 16) || item.time;
  return [date, time].filter(Boolean).join(" ");
}
function card(item) {
  const active = item.id === state.selected?.id;
  const dimReadCard = state.collection === "feed" && item.read_at;
  const showUnreadDot = state.collection !== "read_later" && !item.read_at;
  const text = item.summary || item.note_preview || "";
  return `<li class="news-card ${active ? "active is-active" : ""} ${dimReadCard ? "read" : ""}" data-id="${e(item.id)}" tabindex="0" aria-label="${e(item.title)}" aria-current="${active}">
    <div class="news-card-meta"><div class="meta-source-box">${sourceBadge(sourceKey(item))}<span>${e(item.source || item.source_name || "未知来源")}</span></div><div class="meta-time-row"><span>${e(cardTimestamp(item))}</span>${showUnreadDot ? '<span class="unread-dot" title="未读"></span>' : ""}</div></div>
    <h3 class="news-card-title">${e(item.title || "无标题")}</h3>
    ${text ? `<p class="news-card-snippet">${e(text)}</p>` : ""}
    <div class="news-card-footer"><div class="card-footer-left">${item.has_note ? `<span class="note-badge" title="${item.note_count || 1} 条想法" aria-label="${item.note_count || 1} 条想法">${icon("comment")}<span>${item.note_count || 1}</span></span>` : ""}</div>
    <div class="card-actions">${["important", "read_later", "favorite"].map((flag) => {
      const later = flag === "read_later";
      const completed = later && !item.read_later_at && (item.detail_ready || item.read_later_done_at);
      const fetching = later && ["pending", "running"].includes(item.detail_status);
      const label = !later ? stateLabels[flag] : fetching ? "抓取中" : item.detail_status === "failed" && item.read_later_at ? "抓取失败" : completed ? "已看完" : item.read_later_at && item.detail_ready ? "全文待看" : item.read_later_at ? "待抓取" : "稍后再看";
      return `<button class="card-action-btn ${item[`${flag}_at`] ? "active" : ""} ${completed ? "completed" : ""} ${fetching ? "fetching" : ""}" type="button" data-state="${flag}" aria-pressed="${!!item[`${flag}_at`]}" title="${label}${later ? (item.read_later_at ? " · 点击标记看完" : " · 点击加入待看并抓取全文") : ""}" ${state.pendingStates.has(item.id) ? "disabled" : ""}>${icon(flag)}<span>${label}</span></button>`;
    }).join("")}</div></div></li>`;
}
function renderFeed() {
  const list = $("#feedCardList");
  const scroll = list.scrollTop;
  list.innerHTML =
    state.items.map(card).join("") +
    (state.loading
      ? '<li class="list-message" role="status">正在读取新闻…</li>'
      : !state.items.length
        ? '<li class="list-message">没有符合条件的新闻。<br>试试其他筛选，或点击刷新索引。</li>'
        : state.more
          ? '<li class="load-more-row"><button class="btn-secondary" id="loadMoreBtn" type="button">加载更多</button></li>'
          : '<li class="list-message feed-end">已读到列表末尾</li>');
  list.scrollTop = scroll;
  renderFilters();
  requestAnimationFrame(trackFeedReading);
}
async function loadFeed(append = false) {
  if (state.collection === "summaries") return summaries.load();
  if (state.collection === "notes") return loadIdeas();
  if (append && (state.loading || !state.more)) return;
  requestController?.abort();
  requestController = new AbortController();
  const sequence = ++state.sequence;
  if (!append) {
    state.items = [];
    state.page = 0;
    state.cursor = null;
    enteredCards.clear();
    clearTimeout(feedEndTimer);
    feedEndTimer = null;
    lastFeedScroll = 0;
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
    if (!state.selected && state.items.length && !singlePane.matches)
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
    if (params.has_note === "1" && !item.has_note) return false;
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
  const ascending = ["feed", "read_later"].includes(state.collection) === (state.sortOrder === "default");
  items.sort((a, b) => (ascending ? 1 : -1) * `${a.date_key || a.date || ""} ${a.published_at || ""} ${a.id}`.localeCompare(`${b.date_key || b.date || ""} ${b.published_at || ""} ${b.id}`));
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
let badgesSequence = 0;
async function refreshBadges() {
  summaries.refreshScope();
  const sequence = ++badgesSequence;
  try {
    const [data] = await Promise.all([api("/api/nav-summary"), loadSources()]);
    if (sequence !== badgesSequence) return;
    $("#unreadBadge").textContent = data.summary.feed_unread || "";
    $("#readLaterNavBadge").textContent = data.summary.read_later_unread || "";
    $("#starredNavBadge").textContent = data.summary.favorites || "";
    $("#mobUnreadBadge").textContent = data.summary.feed_unread || "0";
  } catch {
    /* Feed and settings report connectivity separately. */
  }
}
let sourcesSequence = 0;
const sourceCountsByCollection = new Map();
async function loadSources() {
  const currentCollection = state.collection;
  const collection = currentCollection === "summaries" ? "feed" : currentCollection, sequence = ++sourcesSequence;
  const readFilter = ["feed", "read_later"].includes(collection) ? "unread" : "all";
  try {
    const data = await api(`/api/sources?collection=${collection}&read_filter=${readFilter}`);
    if (sequence !== sourcesSequence || currentCollection !== state.collection) return;
    sourceCountsByCollection.set(currentCollection, data.sources);
    renderSources();
  } catch (error) {
    if (sequence !== sourcesSequence || currentCollection !== state.collection) return;
    $("#sourceFilters").textContent = errorText(error);
  }
}
function renderSources() {
  const groups = settings.saved?.feed_source_subkeys?.groups || [];
  const sourceCounts = sourceCountsByCollection.get(state.collection) || [];
  const showCount = ["feed", "read_later", "favorites"].includes(state.collection) && sourceCountsByCollection.has(state.collection);
  const badge = count => showCount && count > 0 ? `<span class="nav-badge">${count}</span>` : "";
  const counts = new Map(sourceCounts.map(source => [source.key, source]));
  const sources = groups.map(group => counts.get(group.source_key) || { key: group.source_key, label: group.label, count: 0 });
  for (const source of counts.values())
    if (!sources.some(item => item.key === source.key)) sources.push(source);
  $("#sourceFilters").innerHTML =
    `<div class="source-row"><button class="source-filter-button ${state.source === "all" ? "selected" : ""}" type="button" data-source="all"><span class="source-row-info"><span class="source-icon-badge all-sources">${icon("sources")}</span><span>全部来源</span></span>${badge(sourceCounts.reduce((n, s) => n + s.count, 0))}</button></div>` +
    sources
      .map((source) => {
        return `<div class="source-row"><button class="source-filter-button ${state.source === source.key ? "selected" : ""}" type="button" data-source="${e(source.key)}"><span class="source-row-info">${sourceBadge(source.key)}<span>${e(source.label)}</span></span>${badge(source.count)}</button></div>`;
      })
      .join("");
}
function switchCollection(collection) {
  if (!labels[collection]) return;
  settings.close({ restoreFocus: false });
  stashNote();
  clearTimeout(feedEndTimer);
  feedEndTimer = null;
  state.collection = collection;
  renderSources();
  loadSources();
  state.openedReadLaterId = null;
  state.filter = ["feed", "read_later"].includes(collection) ? "unread" : "all";
  state.sortOrder = "default";
  state.query = "";
  $("#feedSearchInput").value = "";
  $("#ideasSearchInput").value = "";
  state.sequence++;
  requestController?.abort();
  state.loading = false;
  closeNav();
  exitReader();
  const ideas = collection === "notes";
  const summary = collection === "summaries";
  $("#feedColFeed").classList.toggle("active", !ideas && !summary);
  $("#detailPanelFeed").classList.toggle("active", !ideas && !summary);
  $("#feedColIdeas").classList.toggle("active", ideas);
  $("#detailPanelIdeas").classList.toggle("active", ideas);
  $("#feedColSummaries").classList.toggle("active", summary);
  $("#detailPanelSummaries").classList.toggle("active", summary);
  syncMobileLayers();
  $$("[data-collection]").forEach((button) => {
    const active = button.dataset.collection === collection;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (summary) {
    agent.suspend();
    clearTimeout(detailTimer);
    state.detailSequence++;
    summaries.load();
  } else if (ideas) {
    agent.suspend();
    loadIdeas();
  } else {
    loadFeed();
    if (state.selected) agent.select(state.selected, state.detail?.agent);
  }
  renderFilters();
}
function stashNote() {
  const idea = $("#ideaNote");
  if (idea && state.idea) {
    if (idea.value === (state.idea.note || ""))
      state.noteDrafts.delete(state.idea.idea_id);
    else state.noteDrafts.set(state.idea.idea_id, idea.value);
  }
}
async function selectNews(item, mobile = true, userInitiated = false) {
  if (!item) return;
  if (userInitiated && state.collection === "read_later") state.openedReadLaterId = item.id;
  else if (state.selected?.id !== item.id) state.openedReadLaterId = null;
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
    stashNote();
    state.detail = data;
    for (const flag of ["read", "important", "read_later", "read_later_done", "favorite"])
      item[`${flag}_at`] = data[`${flag}_at`];
    updateDetailItem(item, data);
    renderReader();
    renderFeed();
    agent.select(item, data.agent);
    pollDetail(item, sequence);
    await completeOpenedReadLater(item, data, sequence);
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
    $("#detailContent").innerHTML =
      `<div class="list-message error-state">${e(errorText(error))}<button class="btn-secondary" id="retryReaderBtn" type="button">重新加载</button></div>`;
    $("#retryReaderBtn").onclick = () => selectNews(item, false);
  }
}
function pollDetail(item, sequence) {
  if (sequence !== state.detailSequence || state.selected?.id !== item.id) return;
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
        data.ai_status !== state.detail.ai_status ||
        data.detail?.content !== state.detail.detail?.content ||
        data.ai?.body_zh !== state.detail.ai?.body_zh;
      stashNote();
      state.detail = data;
      updateDetailItem(item, data);
      if (changed) {
        stashNote();
        renderReader();
        agent.select(item, data.agent);
        renderFeed();
      }
      await completeOpenedReadLater(item, data, sequence);
      pollDetail(item, sequence);
    } catch {
      if (sequence === state.detailSequence)
        toast("正文状态刷新失败，可点击重新加载。");
    }
  }, 3000);
}
async function completeOpenedReadLater(item, payload, sequence) {
  if (state.collection !== "read_later" || state.openedReadLaterId !== item.id ||
      state.selected?.id !== item.id || sequence !== state.detailSequence ||
      settings.open || document.hidden || (singlePane.matches && !mobileReaderOpen()) ||
      !$("#detailPanelFeed").getClientRects().length || !item.read_later_at ||
      !(payload.detail?.content?.trim() || payload.ai?.body_zh?.trim())) return;
  await toggleState(item, "read_later", false, true);
  if (!item.read_later_at && state.openedReadLaterId === item.id) state.openedReadLaterId = null;
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
  const url = safeURL(item?.url || "");
  $("#articleSourceLink").href = url || "#";
  $("#articleSourceLink").hidden = !item?.url || !url;
}
function renderReader() {
  const notesFocus = researchNotes.captureFocus();
  $("#selectionPopover").classList.remove("show");
  readerActions();
  const item = state.selected,
    payload = state.detail;
  if (!item) {
    readerHighlights.mount(null, {});
    $("#detailContent").innerHTML =
      '<div class="reader-empty"><span class="empty-book">▤</span><h2>从一篇新闻开始</h2><p>选择左侧新闻，阅读正文、查看分析。</p></div>';
    $("#agentDock").hidden = true;
    return;
  }
  const detail = payload?.detail;
  $("#agentDock").hidden = false;
  $("#detailPanelFeed .cache-label").textContent = pipelineLabel(payload);
  $("#detailPanelFeed .cache-label").title = $("#detailPanelFeed .cache-label").textContent;
  $("#detailPanelFeed .cache-time").textContent = detail?.fetched_at
    ? `${detail.fetched_at.slice(11, 16)} 抓取`
    : "";
  $("#detailPanelFeed .cache-pulse-dot").classList.toggle(
    "waiting",
    ["pending", "running"].includes(payload?.detail_status) || ["pending", "running"].includes(payload?.ai_status) || !detail?.content,
  );
  const ai = payload?.ai;
  let points = [];
  try {
    const value = JSON.parse(ai?.key_points_zh || "[]");
    points = Array.isArray(value) ? value.filter(point => typeof point === "string" && point.trim()) : [];
  } catch {
    points = (ai?.key_points_zh || "").split("\n").filter(Boolean);
  }
  const body =
    state.bodyMode === "original"
      ? detail?.content
      : ai?.body_zh || detail?.content;
  const hasContent = !!(detail?.content?.trim() || ai?.body_zh?.trim());
  const hasInsights = !!(ai?.conclusion_zh || points.length);
  const bodyKind = state.bodyMode === "translated" && ai?.body_zh ? "ai_zh" : sourceKey(item) === "x" ? "twitter_detail" : "original";
  let pointOffset = 0;
  const pointsHTML = points.map((point, i) => {
    const html = `<li class="digest-point-item" data-quote-paragraph="${i + 1}"><span class="point-index">${i + 1}</span><span>${readerInline(point, pointOffset)}</span></li>`;
    pointOffset += point.length;
    return html;
  }).join("");
  const savedNotes = payload?.notes || (payload?.note ? [{ ...payload.note, id: "legacy" }] : []);
  const thoughts = '<div id="researchNotesMount"></div>';
  $("#detailContent").innerHTML =
    `<article class="article-header"><div class="article-meta-top"><div class="article-author-info">${sourceBadge(sourceKey(item))}<strong>${e(item.source || "未知来源")}</strong>${detail?.author ? ` · ${e(detail.author)}` : ""}</div><span>发布于 ${e(item.published_at || item.date || "日期未知")}</span></div><h1 class="article-title" id="articleMainTitle">${e(item.title)}</h1><div class="article-meta-bottom">${body ? `<span>估读：${Math.max(1, Math.ceil(body.length / 650))} 分钟</span>` : ""}<span>信源：${e(item.source_file?.split("/").at(-1) || item.source || "")}</span><button class="reader-action-btn" id="fetchArticleBtn" type="button" ${!payload || ["pending", "running"].includes(payload?.detail_status) || ["pending", "running"].includes(payload?.ai_status) ? "disabled" : ""}>${detail?.content ? "重新翻译" : "抓取全文"}</button>${hasContent && !hasInsights ? '<button class="reader-action-btn" id="writeArticleThoughtBtn" type="button">写想法</button>' : ""}</div></article>
    ${hasContent ? `<section class="digest-box ${hasInsights ? "" : "notes-only"}" aria-label="${hasInsights ? "本篇结构化精读" : "阅读笔记"}" ${!hasInsights && !savedNotes.length ? "hidden" : ""}>${hasInsights ? `<div class="digest-header"><div class="digest-badge">${icon("read")}<span>DeepSeek 结构化精读</span></div><div class="digest-mode-tabs"><button class="digest-mode-btn ${state.bodyMode === "translated" ? "active" : ""}" data-body-mode="translated" type="button">中文精读</button><button class="digest-mode-btn ${state.bodyMode === "original" ? "active" : ""}" data-body-mode="original" type="button">英文原文</button></div></div>${ai?.conclusion_zh ? `<p class="digest-lead" data-highlight-kind="ai_conclusion_zh">${readerInline(ai.conclusion_zh)}</p>` : ""}${points.length ? `<ul class="digest-points" data-highlight-kind="ai_points_zh">${pointsHTML}</ul>` : ""}` : ""}${thoughts}</section>` : ""}
    ${body?.trim() ? `<div class="article-body" id="articleBodyContent" data-highlight-kind="${bodyKind}">${readerMarkdown(body)}</div>` : ""}
    ${detail?.media_images?.length ? `<div class="article-media">${detail.media_images.map((image) => (safeURL(image.cached_url || image.url) ? `<img src="${e(safeURL(image.cached_url || image.url))}" alt="${e(image.alt || "新闻配图")}" loading="lazy">` : "")).join("")}</div>` : ""}
    ${payload?.job?.last_error || payload?.ai_job?.last_error ? `<div class="inline-error">${e(errorText({ error: payload.job?.last_error || payload.ai_job.last_error }))}</div>` : ""}`;
  researchNotes.mount($("#researchNotesMount"), item.id, savedNotes, notesFocus);
  readerHighlights.mount(item.id, { [bodyKind]: body || "", ai_conclusion_zh: ai?.conclusion_zh || "", ai_points_zh: points.join("") }, payload?.highlight_surfaces);
  const writeThought = $("#writeArticleThoughtBtn");
  if (writeThought) {
    writeThought.setAttribute("aria-controls", "researchNoteComposer");
    writeThought.onclick = () => researchNotes.toggle();
  }
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
}
async function toggleState(item, flag, force, silent = false) {
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
    if (!silent) toast(flag === "read_later" ? (value ? "已加入稍后再看" : "已看完") : `${value ? "已标记" : "已取消"}${stateLabels[flag]}`);
    refreshBadges();
    if (flag === "read_later") {
      if (value && state.openedReadLaterId === item.id) state.openedReadLaterId = null;
      const payload = await api(`/api/news/${encodeURIComponent(item.id)}/detail`);
      updateDetailItem(item, payload);
      if (state.selected?.id === item.id) {
        stashNote();
        state.detail = payload;
        renderReader();
        agent.select(item, payload.agent);
        pollDetail(item, state.detailSequence);
      }
    }
  } catch (error) {
    toast(errorText(error));
  } finally {
    state.pendingStates.delete(item.id);
    renderFeed();
    readerActions();
  }
}
function updateDetailItem(item, payload) {
  Object.assign(item, { detail_ready: !!payload.detail?.content, detail_status: payload.detail_status, ai_status: payload.ai_status, has_note: payload.has_note, note_count: payload.notes?.length || 0 });
}
let statusRefreshing = false;
setInterval(async () => {
  if (statusRefreshing || document.hidden || settings.open) return;
  const ids = state.items.filter(item => ["pending", "running"].includes(item.detail_status) || (item.read_later_at && !item.detail_ready && !["failed", "skipped", "canceled"].includes(item.detail_status))).slice(0, 50).map(item => item.id);
  if (!ids.length) return;
  statusRefreshing = true;
  try {
    const data = await api(`/api/news/status?${new URLSearchParams({ ids: ids.join(",") })}`);
    for (const row of data.items) {
      const item = state.items.find(item => item.id === row.id);
      if (item) Object.assign(item, row);
    }
    renderFeed();
  } catch { /* The reader reports detailed fetch failures. Retry on the next tick. */ }
  finally { statusRefreshing = false; }
}, 3000);
function pipelineLabel(payload) {
  if (!payload) return "正在读取正文…";
  const fetchLabels = { pending: "等待抓取正文", running: "正文抓取中…", failed: "正文抓取失败", canceled: "正文抓取已取消", skipped: "正文抓取已跳过" };
  if (fetchLabels[payload.detail_status]) return fetchLabels[payload.detail_status];
  if (!payload.detail?.content) return "尚未抓取正文";
  return ({ pending: "全文就绪 · 等待翻译", running: "全文就绪 · 正文翻译中…", failed: "全文就绪 · 翻译失败", canceled: "全文就绪 · 翻译已取消", skipped: "全文就绪 · 翻译已跳过" })[payload.ai_status] || (payload.ai?.body_zh ? "全文就绪 · 翻译完成" : "全文就绪 · 尚未翻译");
}
const enteredCards = new Set();
const queuedReadIds = new Set();
let lastFeedScroll = 0, feedEndTimer, autoReadBusy = false;
function feedVisible() {
  return state.collection === "feed" && !settings.open && !mobileReaderOpen() &&
    !document.hidden && $("#feedCardList").getBoundingClientRect().width > 0;
}
async function markReadIds(ids) {
  for (const id of ids) queuedReadIds.add(id);
  if (!queuedReadIds.size || autoReadBusy) return;
  autoReadBusy = true;
  try {
    while (queuedReadIds.size) {
      const batch = [...queuedReadIds];
      queuedReadIds.clear();
      await api("/api/news/mark-read-by-ids", { method: "POST", body: { item_ids: batch } });
      for (const item of state.items) if (batch.includes(item.id)) item.read_at ||= new Date().toISOString();
      for (const id of batch) {
        if (state.collection === "read_later") continue;
        const card = $(`#feedCardList [data-id="${CSS.escape(id)}"]`);
        card?.classList.toggle("read", state.collection === "feed");
        if (card) $(".unread-dot", card)?.remove();
      }
    }
    refreshBadges();
  } catch (error) { queuedReadIds.clear(); toast(errorText(error)); }
  finally { autoReadBusy = false; }
}
function trackFeedReading() {
  if (!feedVisible()) { clearTimeout(feedEndTimer); feedEndTimer = null; return; }
  const list = $("#feedCardList"), bounds = list.getBoundingClientRect();
  const down = list.scrollTop > lastFeedScroll;
  const ids = [];
  for (const node of $$(".news-card", list)) {
    const rect = node.getBoundingClientRect(), id = node.dataset.id;
    if (rect.bottom > bounds.top && rect.top < bounds.bottom) enteredCards.add(id);
    if (down && enteredCards.has(id) && rect.bottom <= bounds.top && !state.items.find(item => item.id === id)?.read_at) ids.push(id);
  }
  lastFeedScroll = list.scrollTop;
  if (ids.length) markReadIds(ids);
  const end = $(".feed-end", list)?.getBoundingClientRect();
  const atEnd = !state.loading && !state.more && end && end.top < bounds.bottom && end.bottom > bounds.top;
  if (!atEnd) { clearTimeout(feedEndTimer); feedEndTimer = null; }
  else if (!feedEndTimer && state.items.some(item => !item.read_at)) {
    const sequence = state.sequence;
    feedEndTimer = setTimeout(() => {
      feedEndTimer = null;
      if (sequence === state.sequence && feedVisible() && !state.more) {
        const rect = $(".feed-end", list)?.getBoundingClientRect();
        if (rect && rect.top < list.getBoundingClientRect().bottom) markReadIds(state.items.filter(item => !item.read_at).map(item => item.id));
      }
    }, 5000);
  }
}
async function markAllRead() {
  const later = state.collection === "read_later";
  if (state.query) {
    const items = [...state.items];
    if (later) {
      for (const item of items.filter(item => item.read_later_at)) await api(`/api/news/${encodeURIComponent(item.id)}/state`, { method: "PATCH", body: { read_later: false } });
    } else await api("/api/news/mark-read-by-ids", { method: "POST", body: { item_ids: items.map(item => item.id) } });
  } else await api(later ? "/api/news/clear-read-later" : "/api/news/mark-all-read", { method: "POST", body: feedParams() });
  await loadFeed();
  if (state.selected) await selectNews(state.selected, false);
  refreshBadges();
  toast(later ? "当前范围已全部看完" : "当前范围已全部标为已读");
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
    if (!state.idea && state.ideas.length && !singlePane.matches)
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
          `<li class="news-card ${state.idea?.idea_id === idea.idea_id ? "active is-active" : ""}" data-idea-id="${e(idea.idea_id)}" tabindex="0"><div class="news-card-meta"><div class="meta-source-box"><span class="source-icon-badge idea">想</span>${e(idea.idea_context_label)}</div><span>${e(idea.updated_at?.slice(0, 16) || "")}</span></div><h3 class="news-card-title">${e(idea.idea_type === "standalone_note" ? idea.note?.split("\n")[0] : idea.title)}</h3><p class="news-card-snippet">${e(idea.note)}</p><div class="news-card-footer"><span class="card-tag">${e(idea.source || "独立想法")}</span></div></li>`,
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
  $("#mobileReaderBackBtn").setAttribute("aria-label", state.collection === "summaries" ? "返回总结列表" : state.collection === "notes" ? "返回想法列表" : "返回新闻列表");
  if (singlePane.matches)
    $("#feedView").classList.add("mobile-reader-active");
  syncMobileLayers();
  trackFeedReading();
}
function exitReader() {
  resetDetailSwipe();
  $("#feedView").classList.remove("mobile-reader-active");
  syncMobileLayers();
  requestAnimationFrame(trackFeedReading);
}
function mobileReaderOpen() {
  return singlePane.matches && $("#feedView").classList.contains("mobile-reader-active");
}
function syncMobileLayers() {
  const open = mobileReaderOpen() && !settings.open;
  for (const list of $$("#feedColFeed, #feedColIdeas, #feedColSummaries")) list.inert = open;
  for (const panel of $$("#detailPanelFeed, #detailPanelIdeas, #detailPanelSummaries"))
    panel.inert = singlePane.matches && (!open || !panel.classList.contains("active"));
  const settingsDetail = settings.open && $("#feedView").classList.contains("settings-detail-active");
  $("#feedColSettings").inert = singlePane.matches && settingsDetail;
  $("#detailPanelSettings").inert = singlePane.matches && !settingsDetail;
}
let detailSwipe = null;
function resetDetailSwipe() {
  if (!detailSwipe) return;
  detailSwipe.panel.style.transition = "";
  detailSwipe.panel.style.transform = "";
  detailSwipe = null;
}
function canSwipeDetail(panel) {
  if (!singlePane.matches || !panel.classList.contains("active") || getSelection()?.toString()) return false;
  if (panel.id === "detailPanelSettings")
    return settings.open && $("#feedView").classList.contains("settings-detail-active");
  return !settings.open &&
    $("#feedView").classList.contains("mobile-reader-active") &&
    !(!["notes", "summaries"].includes(state.collection) && agent.open);
}
for (const panel of $$("#detailPanelFeed, #detailPanelIdeas, #detailPanelSummaries, #detailPanelSettings")) {
  panel.addEventListener("touchstart", event => {
    resetDetailSwipe();
    if (!canSwipeDetail(panel) || event.touches.length !== 1 ||
        event.target.closest('input,textarea,select,button,a,[contenteditable="true"],#agentDock')) return;
    const touch = event.touches[0];
    if (touch.clientX > 40) return;
    detailSwipe = { panel, id: touch.identifier, x: touch.clientX, y: touch.clientY, dx: 0, dy: 0, horizontal: false };
  }, { passive: true });
  panel.addEventListener("touchmove", event => {
    if (!detailSwipe) return;
    const touch = event.touches[0];
    if (!canSwipeDetail(panel) || event.touches.length !== 1 || touch.identifier !== detailSwipe.id) {
      resetDetailSwipe();
      return;
    }
    const dx = touch.clientX - detailSwipe.x, dy = touch.clientY - detailSwipe.y;
    if (!detailSwipe.horizontal) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 6) return;
      if (dx <= 0 || Math.abs(dx) <= Math.abs(dy) * 1.5) {
        resetDetailSwipe();
        return;
      }
      detailSwipe.horizontal = true;
    }
    if (!event.cancelable) { resetDetailSwipe(); return; }
    event.preventDefault();
    detailSwipe.dx = dx;
    detailSwipe.dy = dy;
    const offset = Math.max(0, Math.min(dx, innerWidth * 0.9));
    panel.style.transition = "none";
    panel.style.transform = `translateX(${offset}px)`;
  }, { passive: false });
  panel.addEventListener("touchend", () => {
    if (!detailSwipe) return;
    const close = canSwipeDetail(panel) && detailSwipe.horizontal &&
      detailSwipe.dx > 72 && detailSwipe.dx > Math.abs(detailSwipe.dy) * 1.5;
    resetDetailSwipe();
    if (close) {
      if (panel.id === "detailPanelSettings") settings.showCategories();
      else exitReader();
    }
  }, { passive: true });
  panel.addEventListener("touchcancel", resetDetailSwipe, { passive: true });
}
singlePane.addEventListener("change", () => {
  resetDetailSwipe();
  syncMobileLayers();
});
function openNav() {
  $("#leftNavDrawer").inert = false;
  $("#leftNavDrawer").classList.add("drawer-open");
  $("#navDrawerBackdrop").classList.add("active");
}
function closeNav() {
  $("#leftNavDrawer").inert = drawerNavigation.matches;
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
    if (settings.open || state.collection === "summaries") switchCollection("feed");
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
    selectNews(state.items.find((item) => item.id === card.dataset.id), true, true);
  const ideaCard = event.target.closest("[data-idea-id]");
  if (ideaCard)
    selectIdea(
      state.ideas.find((idea) => idea.idea_id === ideaCard.dataset.ideaId),
    );
});
document.addEventListener("change", (event) => {
  const input = event.target;
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
  trackFeedReading();
  const list = $("#feedCardList");
  if (
    list.scrollTop + list.clientHeight >= list.scrollHeight - 250 &&
    list.scrollHeight > list.clientHeight
  )
    loadFeed(true);
});
$("#sortNewsBtn").onclick = () => { state.sortOrder = state.sortOrder === "default" ? "reverse" : "default"; loadFeed(); };
$("#markAllReadBtn").onclick = event => busy(event.currentTarget, markAllRead);
document.addEventListener("visibilitychange", trackFeedReading);
$("#themeSelect").value = document.documentElement.dataset.theme || "system";
$("#themeSelect").onchange = (event) => {
  document.documentElement.dataset.theme = event.target.value;
  persist("newsreader_workbench_theme_mode", event.target.value);
};
selects.refresh();
$("#leftNavSettingsBtn").onclick = () => settings.toggle();
$("#mobNavSettings").onclick = () => settings.toggle();
$("#openNavBtn").onclick = openNav;
$(".drawer-close-btn").onclick = closeNav;
$("#navDrawerBackdrop").onclick = closeNav;
$$(".mobile-back-btn").forEach((button) => (button.onclick = exitReader));
$("#shortcutsModal").onclick = (event) => {
  if (event.target.id === "shortcutsModal") shortcutsDialog(false);
};
$("#saveIdeaBtn").onclick = (event) => busy(event.currentTarget, saveIdea);
$("#copyIdeaBtn").onclick = (event) =>
  busy(event.currentTarget, async () => {
    await navigator.clipboard.writeText($("#ideaNote")?.value || "");
    toast("想法已复制");
  });
const refreshButton = $("#refreshIndexBtn");
function refreshIndex(notify = false) {
  return busy(refreshButton, async () => {
    setIndexStatus("正在扫描 DailyNews…");
    let scanError;
    try {
      await api("/api/reindex", { method: "POST" });
    } catch (error) {
      scanError = error;
    }
    await Promise.all([loadFeed(), refreshBadges()]);
    if (scanError) {
      setIndexStatus("索引刷新失败，可点击刷新重试", true);
      throw scanError;
    }
    if (notify) toast("新闻索引已刷新");
  });
}
refreshButton.onclick = () => refreshIndex(true);

document.addEventListener("keydown", (event) => {
  if (summaries.modal.open) return;
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
  if (
    $("#leftNavDrawer").classList.contains("drawer-open") &&
    event.key === "Escape"
  ) {
    closeNav();
    return;
  }
  if (settings.open) {
    settings.handleKey(event);
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
    event.target.closest('input,textarea,select,[role="combobox"],[contenteditable="true"]') ||
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
  } else if (state.collection === "summaries" && key !== "?") {
    summaries.handleKey(event);
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
    if (key === "m" && state.collection === "read_later") toggleState(state.selected, "read_later", false);
    else toggleState(state.selected, shortcuts[key]);
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
      selectNews(next, false, true);
      $(`[data-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({
        block: "nearest",
      });
    }
  } else if (key === "enter" && event.target.matches("[data-id]"))
    selectNews(state.items.find((item) => item.id === event.target.dataset.id), true, true);
  else if (key === "enter" && event.target.matches("[data-idea-id]"))
    selectIdea(
      state.ideas.find((idea) => idea.idea_id === event.target.dataset.ideaId),
    );
});
window.addEventListener("beforeunload", (event) => {
  stashNote();
  const dirty = state.noteDrafts.size > 0 || researchNotes.dirty;
  if (dirty || settings.dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("#navFeedBtn").setAttribute("aria-current", "page");
closeNav();
syncMobileLayers();
drawerNavigation.addEventListener("change", closeNav);
singlePane.addEventListener("change", () => {
  if (singlePane.matches || settings.open) return;
  if (state.collection === "summaries") {
    if (!summaries.selected && summaries.entries.length) summaries.select(summaries.entries[0].id);
  } else if (state.collection === "notes") {
    if (!state.idea && state.ideas.length) selectIdea(state.ideas[0]);
  } else if (!state.selected && state.items.length) selectNews(state.items[0], false);
});
state.loading = true;
renderFeed();
renderReader();
Promise.allSettled([
  refreshIndex(),
  settings.load(false),
  summaries.load(),
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
