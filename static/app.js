import { confirmAction } from "./confirm-dialog.js";
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
import { SettingsPanel } from "./settings-panel.js?v=0.2.2";
import { SummaryPanel } from "./summary-panel.js";
import { SelectControls } from "./select-control.js?v=0.2.0";
import { AgentPanel } from "./agent-panel.js?v=0.2.0";
import { AgentNotifications } from "./agent-notifications.js?v=0.2.2";
import { ClassificationProgress } from "./classification-progress.js?v=0.2.2";
import { ResearchNotes } from "./research-notes.js";
import { StandaloneIdeas, standaloneTitle } from "./standalone-ideas.js?v=0.1.0";
import { ReaderHighlights, readerInline, readerMarkdown } from "./reader-highlights.js";
import { UpstreamErrors } from "./upstream-errors.js?v=0.2.2";
import { ItemStateQueue, ReadBatchWriter } from "./item-state.js";
import { readLaterPending, readLaterState } from "./read-later.js";

const state = {
  collection: "feed",
  filter: "unread",
  sortOrder: "default",
  source: "all",
  category: "all",
  query: "",
  mobileSearchOpen: false,
  items: [],
  selected: null,
  detail: null,
  page: 0,
  cursor: null,
  more: true,
  total: null,
  dateCounts: null,
  dateCountsScope: "",
  loading: false,
  sequence: 0,
  collectionSequence: 0,
  detailSequence: 0,
  detailRequestSequence: 0,
  favoritesTotal: null,
  removingFavorites: false,
  ideaType: "all",
  ideaSequence: 0,
  ideas: [],
  idea: null,
  readerViews: new Map(),
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
const shortcuts = { m: "read", a: "important", d: "read_later", f: "favorite" };
const shortcutAliases = { ArrowUp: "w", ArrowDown: "s", ArrowLeft: "a", ArrowRight: "d" };
const readWrites = new ReadBatchWriter(ids =>
  api("/api/news/mark-read-by-ids", { method: "POST", body: { item_ids: ids } }));
const readOperations = new Map();
const stateWrites = new ItemStateQueue({
  save: (id, body) => body.read === true && Object.keys(body).length === 1
    ? readWrites.add(id)
    : api(`/api/news/${encodeURIComponent(id)}/state`, { method: "PATCH", body }),
  onChange: (id, patch) => {
    applyReadCountChange(id, patch);
    const item = state.items.find(row => row.id === id);
    if (item) { Object.assign(item, patch); updateCard(item); }
    if (state.selected?.id === id) {
      Object.assign(state.selected, patch);
      if (state.detail) Object.assign(state.detail, patch);
      readerActions();
    }
    renderFavoritesAction();
  },
  onSaved: (id, saved) => {
    for (const [flag, { value, silent, complete }] of Object.entries(saved)) {
      const item = state.items.find(row => row.id === id) || (state.selected?.id === id ? state.selected : null);
      const removed = item?.detail_ready ? "正文已就绪，已进入全文待看" : item?.detail_status === "running" ? "抓取已开始，完成后会进入全文待看" : "已取消排队";
      if (!silent) toast(flag === "read_later" ? (value ? "已加入稍后再看" : complete ? "已看完" : removed) : `${value ? "已标记" : "已取消"}${stateLabels[flag]}`);
    }
    refreshBadges();
    if (saved.read_later) void refreshStateDetail(id);
    if (saved.favorite?.value === false && state.collection === "favorites" && !state.removingFavorites) {
      // Reload to keep offset pagination correct after removing a favorite.
      void loadFeed();
    }
  },
  onError: error => { toast(errorText(error)); refreshBadges(); },
});
const upstreamErrors = new UpstreamErrors();
let requestController,
  detailTimer,
  searchTimer,
  lastFocus;
const selects = new SelectControls(document.body);
const settings = new SettingsPanel({
  selects,
  onOpen: () => {
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
    sourceCatalogByCollection.clear();
    loadFeed();
    refreshBadges();
    if (agent.open) agent.refresh();
  },
  onSources: renderSources,
});
const researchNotes = new ResearchNotes({ onUpdate: updateResearchNotes });
const standaloneIdeas = new StandaloneIdeas({
  onSaved: (idea, oldId) => {
    // Invalidate a list request that began before this write.
    state.ideaSequence++;
    state.ideas = state.ideas.filter(row => row.idea_id !== oldId && row.idea_id !== idea.idea_id);
    state.ideas.push(idea);
    state.ideas.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    if (state.idea?.idea_id === oldId || state.idea?.idea_id === idea.idea_id) state.idea = idea;
    renderIdeas();
    if (state.collection === "notes") void loadIdeas({ quiet: true });
  },
  onDeleted: idea => {
    state.ideaSequence++;
    state.ideas = state.ideas.filter(row => row.idea_id !== idea.idea_id);
    if (state.idea?.idea_id === idea.idea_id) {
      state.idea = null;
      if (state.collection === "notes") {
        const next = visibleIdeas()[0];
        if (next && !singlePane.matches) selectIdea(next, false);
        else { standaloneIdeas.select(null); syncDetailPanels(); exitReader(); }
      }
    }
    renderIdeas();
  },
});
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
  const idea = state.ideas.find(row => row.idea_type === "article_note" && row.id === id);
  if (idea) {
    Object.assign(idea, patch, {
      note: result.note?.note || "", latest_note_preview: notes.at(-1)?.note || "", updated_at: result.note?.updated_at || idea.updated_at,
    });
    if (!notes.length) state.ideas = state.ideas.filter(row => row !== idea);
    if (state.collection === "notes") renderIdeas();
  }
  if (state.collection === "notes") void loadIdeas({ quiet: true });
  renderFeed();
  refreshBadges();
}
const agent = new AgentPanel({
  onArchive: (id, result) => {
    updateResearchNotes(id, result);
  },
});
const notifications = new AgentNotifications({
  agent,
  contextVersion: () => `${state.detailSequence}:${settings.open}`,
  readerVisible: () => !settings.open && !!state.detail && state.detail.item_id === state.selected?.id && readingArticle() &&
    (!singlePane.matches || mobileReaderOpen()) && !!$("#detailPanelFeed").getClientRects().length,
  onOpen: async (target, current) => {
    if (!current()) return false;
    if (["notes", "summaries"].includes(state.collection)) switchCollection("feed", { restoreReader: false });
    settings.close({ restoreFocus: false });
    const loaded = await selectNews(target.item, true, false, { notificationAgent: target.agent, navigationCurrent: current, retryNotification: () => notifications.openNotice(target.notification.id) });
    if (!loaded || !current() || agent.payload.session?.id !== target.agent.session.id) return false;
    const reply = $(`#agentChatStream [data-message-id="${CSS.escape(String(target.notification.message_id))}"]`);
    if (!reply) throw new Error("未能显示对应回复，通知已保留。");
    reply.scrollIntoView({ block: "center", behavior: "instant" });
    reply.tabIndex = -1;
    reply.focus({ preventScroll: true });
    return true;
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

const categoryLabels = { all: "全部", china: "中国", ai: "AI", politics: "政治与国际", economy: "经济与金融", business: "商业", science: "科技与科学", other: "其他" };
const classification = new ClassificationProgress({
  onSettings: async () => {
    if (!settings.open) await settings.toggle();
    settings.apiProvider = "jev";
    settings.renderTranslation();
    settings.selectSection("deepseek");
  },
});

function renderCategories() {
  const row = $("#newsCategoryFilters");
  row.hidden = state.collection !== "feed";
  // Keep the buttons mounted: keyboard focus and horizontal scroll survive counts updating.
  if (!row.children.length) row.innerHTML = Object.entries(categoryLabels).map(([key, label]) =>
    `<button type="button" class="filter-chip" data-category="${key}">${label}</button>`).join("");
  for (const button of row.children) {
    const active = button.dataset.category === state.category;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function refreshClassification() { return classification.refresh(); }

$("#newsCategoryFilters").addEventListener("keydown", event => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  event.stopPropagation();
  const buttons = [...event.currentTarget.children], index = buttons.indexOf(event.target);
  const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
    : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
  buttons[next].focus();
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
    category: state.collection === "feed" ? state.category : "all",
    q: state.query,
    sort_order: state.sortOrder,
    per: "30",
  };
}
function renderFilters() {
  renderCategories();
  renderFavoritesAction();
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
        `<button class="filter-chip ${state.filter === key ? "active" : ""}" data-filter="${key}" type="button" aria-pressed="${state.filter === key}">${label}${state.filter === key && (!state.loading || state.total !== null) ? ` (${state.total ?? "…"})` : ""}</button>`,
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
function newsDate(item) {
  return item.date_key || item.date || item.published_at?.slice(0, 10) || "unknown";
}
function cardTime(item) {
  return item.published_at?.match(/[T ](\d{2}:\d{2})/)?.[1] || item.time?.slice(0, 5) || "时间未知";
}
function feedDateCount(date) {
  const count = state.dateCountsScope === countScope() && state.dateCounts ? state.dateCounts[date] || 0 : null;
  return count == null ? "… 条" : `${count} 条`;
}
function renderFeedDateCounts() {
  for (const group of $$("#feedCardList .feed-date-group")) {
    $(".feed-date-count", group).textContent = feedDateCount(group.dataset.date);
  }
}
let dateHeadersFrame;
function scheduleFeedDateHeaders() {
  if (dateHeadersFrame) return;
  dateHeadersFrame = requestAnimationFrame(() => {
    dateHeadersFrame = null;
    const list = $("#feedCardList");
    if (!list.getClientRects().length) return;
    // Clip cards beneath each transparent sticky heading instead of adding a background.
    const clips = $$(".feed-date-group", list).map(group => {
      const heading = $(".feed-date-heading", group), cards = $(".feed-date-cards", group);
      const rect = cards.getBoundingClientRect();
      const clip = Math.max(0, Math.min(rect.height, heading.getBoundingClientRect().bottom - rect.top));
      return [cards, clip ? `inset(${clip}px -8px -8px)` : ""];
    });
    for (const [cards, clip] of clips) cards.style.clipPath = clip;
  });
}
function cardAction(item, flag) {
  const saving = stateWrites.has(item.id, flag);
  if (flag === "read_later") {
    const action = readLaterState(item, saving);
    return {
      ...action, saving,
      className: `card-action-btn ${action.active ? "active" : ""} ${action.phase === "done" ? "completed" : ""} ${["pending", "running"].includes(action.phase) ? "fetching" : ""}`,
      title: `${action.label} · ${action.hint}${saving ? " · 正在保存" : ""}`,
    };
  }
  const active = !!item[`${flag}_at`];
  const label = flag === "favorite" && active ? "取消收藏" : stateLabels[flag];
  return { active, saving, label, className: `card-action-btn ${active ? "active" : ""}`, title: `${label}${saving ? " · 正在保存" : ""}` };
}
function cardSnippet(item) {
  return (item.detail_ready ? item.ai_conclusion_zh || "" : item.summary || item.note_preview || "").trim();
}
function updateCard(item) {
  const node = $(`#feedCardList [data-id="${CSS.escape(item.id)}"]`);
  if (!node) return;
  const text = cardSnippet(item);
  let snippet = $(".news-card-snippet", node);
  if (!text) snippet?.remove();
  else {
    if (!snippet) {
      snippet = document.createElement("p");
      $(".news-card-title", node).after(snippet);
    }
    snippet.className = `news-card-snippet${item.detail_ready ? " news-card-conclusion" : ""}`;
    snippet.textContent = text;
  }
  node.classList.toggle("read", state.collection === "feed" && !!item.read_at);
  const time = $(".meta-time-row", node);
  const dot = $(".unread-dot", node);
  if (state.collection === "read_later" || item.read_at) dot?.remove();
  else if (!dot) time.insertAdjacentHTML("beforeend", '<span class="unread-dot" title="未读"></span>');
  for (const button of $$("[data-state]", node)) {
    const action = cardAction(item, button.dataset.state);
    button.className = action.className;
    button.setAttribute("aria-pressed", String(action.active));
    button.setAttribute("aria-busy", String(action.saving));
    button.title = action.title;
    $("span", button).textContent = action.label;
    button.disabled = button.dataset.state === "favorite" && state.removingFavorites;
  }
  scheduleFeedDateHeaders();
}
function card(item) {
  const active = item.id === state.selected?.id;
  const dimReadCard = state.collection === "feed" && item.read_at;
  const showUnreadDot = state.collection !== "read_later" && !item.read_at;
  const text = cardSnippet(item);
  return `<li class="news-card ${active ? "active is-active" : ""} ${dimReadCard ? "read" : ""}" data-id="${e(item.id)}" tabindex="0" aria-label="${e(item.title)}" aria-current="${active}">
    <div class="news-card-meta"><div class="meta-source-box">${sourceBadge(sourceKey(item))}<span>${e(item.source || item.source_name || "未知来源")}</span></div><div class="meta-time-row"><span>${e(cardTime(item))}</span>${showUnreadDot ? '<span class="unread-dot" title="未读"></span>' : ""}</div></div>
    <h3 class="news-card-title">${e(item.title || "无标题")}</h3>
    ${text ? `<p class="news-card-snippet${item.detail_ready ? " news-card-conclusion" : ""}">${e(text)}</p>` : ""}
    <div class="news-card-footer"><div class="card-footer-left">${item.category_label ? `<span class="news-category-tag" aria-label="分类：${e(item.category_label)}">${e(item.category_label)}</span>` : ""}${item.has_note ? `<span class="note-badge" title="${item.note_count || 1} 条想法" aria-label="${item.note_count || 1} 条想法">${icon("comment")}<span>${item.note_count || 1}</span></span>` : ""}</div>
    <div class="card-actions">${["important", "read_later", "favorite"].map((flag) => {
      const action = cardAction(item, flag);
      return `<button class="${action.className}" type="button" data-state="${flag}" aria-pressed="${action.active}" aria-busy="${action.saving}" title="${action.title}" ${flag === "favorite" && state.removingFavorites ? "disabled" : ""}>${icon(flag)}<span>${action.label}</span></button>`;
    }).join("")}</div></div></li>`;
}
function renderFeed() {
  const list = $("#feedCardList");
  const scroll = list.scrollTop;
  const groups = new Map();
  for (const item of state.items) {
    const date = newsDate(item);
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date).push(item);
  }
  list.innerHTML =
    [...groups].map(([date, items]) => `<li class="feed-date-group" data-date="${e(date)}">
      <div class="feed-date-heading"><h2 class="feed-date-title">${e(date === "unknown" ? "日期未知" : date)}</h2><span class="feed-date-count">${feedDateCount(date)}</span></div>
      <ul class="feed-date-cards">${items.map(card).join("")}</ul></li>`).join("") +
    (state.loading
      ? '<li class="list-message" role="status">正在读取新闻…</li>'
      : !state.items.length
        ? '<li class="list-message">没有符合条件的新闻。<br>试试其他筛选，或点击刷新索引。</li>'
        : state.more
          ? '<li class="load-more-row"><button class="btn-secondary" id="loadMoreBtn" type="button">加载更多</button></li>'
          : '<li class="list-message feed-end">已读到列表末尾</li>');
  list.scrollTop = scroll;
  scheduleFeedDateHeaders();
  renderFilters();
  requestAnimationFrame(trackFeedReading);
}
function renderFavoritesAction() {
  const button = $("#clearFavoritesBtn");
  button.hidden = state.collection !== "favorites";
  button.disabled = state.removingFavorites || (state.favoritesTotal === 0 && !stateWrites.hasAny("favorite"));
  button.setAttribute("aria-busy", String(state.removingFavorites));
  button.innerHTML = icon("favoriteOff");
}
async function clearFavorites() {
  if (state.collection !== "favorites" || state.removingFavorites) return;
  state.removingFavorites = true;
  renderFavoritesAction();
  state.items.forEach(updateCard);
  readerActions();
  try {
    await stateWrites.settle();
    const data = await api("/api/news/clear-favorites", { method: "POST" });
    // Publish the result so older detail/status responses cannot restore a star.
    const ids = new Set(state.items.map(item => item.id));
    if (state.selected) ids.add(state.selected.id);
    for (const id of ids) stateWrites.publish(id, "favorite", { favorite_at: null });
    if (state.collection === "favorites") await loadFeed();
    await refreshBadges();
    toast(`已取消 ${data.updated} 篇收藏`);
  } catch (error) { toast(errorText(error)); }
  finally {
    state.removingFavorites = false;
    renderFavoritesAction();
    state.items.forEach(updateCard);
    readerActions();
  }
}
async function loadFeed(append = false) {
  if (state.collection === "summaries") return summaries.load();
  if (state.collection === "notes") return loadIdeas();
  if (append && (state.loading || !state.more)) return;
  requestController?.abort();
  requestController = new AbortController();
  const sequence = ++state.sequence;
  const stateVersion = stateWrites.version;
  const countsSnapshot = countSnapshot();
  const scope = filterCountScope();
  if (totalScope !== scope) { totalScope = scope; state.total = null; }
  if (!append) {
    state.items = [];
    state.dateCounts = null;
    state.dateCountsScope = "";
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
    for (const item of data.items) stateWrites.reconcile(item.id, item, stateVersion);
    const known = new Set(state.items.map((item) => item.id));
    state.items.push(...data.items.filter((item) => !known.has(item.id)));
    state.page = data.page;
    if (countsAreCurrent(countsSnapshot)) {
      state.dateCounts = data.date_counts || {};
      state.dateCountsScope = countScope();
    } else refreshBadges();
    // Primary filter counts span categories; a category-limited page cannot replace them.
    if (countsAreCurrent(countsSnapshot) && params.get("category") === "all") state.total = data.total;
    else refreshBadges();
    state.more = data.has_more;
    state.cursor = data.next_cursor;
    state.loading = false;
    void refreshClassification();
    if (state.selected)
      state.selected =
        state.items.find((item) => item.id === state.selected.id) ||
        state.selected;
    renderFeed();
    readerActions();
    if (!state.selected && state.items.length && !singlePane.matches)
      selectNews(state.items[0], false);
    indexStatus.connectionFailed = false;
    renderIndexStatus();
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
    indexStatus.connectionFailed = true;
    renderIndexStatus();
  }
}
async function searchNews(sequence, signal, params = feedParams()) {
  const matches = [];
  let classificationRevision = 0;
  let page = 1,
    pages = 1;
  do {
    const data = await api(
      `/api/search?${new URLSearchParams({ ...params, feed_filters: "1", page: String(page), per: "100" })}`,
      { signal },
    );
    matches.push(...data.items);
    if (page === 1) classificationRevision = data.classification_revision || 0;
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
      if (params.read_filter === "unread") return readLaterPending(item);
      if (params.read_filter === "read")
        return !!item.read_later_done_at && !item.read_later_at;
      return !!item.read_later_at || !!item.read_later_done_at || !!item.detail_ready;
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
    date_counts: items.reduce((counts, item) => {
      const date = newsDate(item);
      counts[date] = (counts[date] || 0) + 1;
      return counts;
    }, {}),
    classification_revision: classificationRevision,
    total: items.length,
    page: 1,
    has_more: false,
    next_cursor: null,
  };
}
const indexStatus = { syncing: false, syncedAt: null, scanFailed: false, connectionFailed: false };
function renderIndexStatus() {
  const { syncing, syncedAt, scanFailed, connectionFailed } = indexStatus;
  const failed = !syncing && (scanFailed || connectionFailed);
  const message = syncing ? "正在扫描 DailyNews…"
    : scanFailed ? "索引刷新失败，可点击刷新重试"
    : connectionFailed ? "新闻库连接失败，请重试"
    : syncedAt ? `DailyNews 增量索引已同步（最近更新：${syncedAt}）`
    : "正在连接 DailyNews…";
  $("#pipelineStatus").textContent = message;
  const dot = $("#upstreamErrorsBtn .pipeline-dot");
  dot.classList.toggle("failed", failed);
  dot.classList.toggle("syncing", syncing);
}
let navSummary = null, totalScope = "", readCountsFrame;
const filterCountScope = () => JSON.stringify([state.collection, state.filter, state.source, state.query]);
const countScope = () => JSON.stringify([state.collection, state.filter, state.source, state.query, state.collection === "feed" ? state.category : "all"]);
const countSnapshot = () => ({ version: stateWrites.version, clean: !stateWrites.pending.size });
const countsAreCurrent = snapshot => snapshot.clean && !stateWrites.pending.size && snapshot.version === stateWrites.version;

function renderNavCounts() {
  if (!navSummary) return;
  $("#unreadBadge").textContent = navSummary.feed_unread || "";
  $("#readLaterNavBadge").textContent = navSummary.read_later_unread || "";
  for (const [id, count, label] of [
    ["mobUnreadBadge", navSummary.feed_unread, "新闻流"],
    ["mobReadLaterBadge", navSummary.read_later_unread, "稍后再看"],
  ]) {
    const badge = $(`#${id}`);
    const unread = Math.max(0, Number(count) || 0);
    badge.hidden = unread === 0;
    badge.textContent = unread > 99 ? "99+" : unread || "";
    badge.closest("button").setAttribute("aria-label", unread ? `${label}，${unread} 条未读` : label);
  }
  $("#starredNavBadge").textContent = navSummary.favorites || "";
  state.favoritesTotal = navSummary.favorites;
  renderFavoritesAction();
}

function renderReadCounts() {
  readCountsFrame = null;
  renderNavCounts();
  // Updating only the count text preserves keyboard focus and the list position.
  if (state.collection !== "feed") return;
  renderFeedDateCounts();
  const chip = $('#feedColFeed .filter-chip.active[data-filter="unread"]');
  if (chip && (!state.loading || state.total !== null)) chip.textContent = `未读 (${state.total ?? "…"})`;
  const sources = sourceCountsByCollection.get("feed");
  if (!sources) return;
  const counts = new Map(sources.map(source => [source.key, source.count]));
  counts.set("all", sources.reduce((sum, source) => sum + source.count, 0));
  for (const button of $$("[data-source]", $("#sourceFilters"))) {
    const count = counts.get(button.dataset.source) || 0;
    let badge = $(".nav-badge", button);
    if (!count) { badge?.remove(); continue; }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "nav-badge";
      button.append(badge);
    }
    badge.textContent = count;
  }
}

function applyReadCountChange(id, patch) {
  const operation = readOperations.get(id);
  if (!operation || !Object.hasOwn(patch, "read_at")) return;
  const delta = Number(!patch.read_at) - Number(!operation.read_at);
  operation.read_at = patch.read_at;
  // Other collections may include sources hidden from the feed. Their counts
  // are reconciled by the server instead of guessing feed membership.
  if (!delta || !operation.fromFeed) return;
  if (navSummary) navSummary.feed_unread = Math.max(0, navSummary.feed_unread + delta);
  const sources = sourceCountsByCollection.get("feed");
  if (sources) {
    let source = sources.find(row => row.key === operation.source);
    if (!source) {
      source = { key: operation.source, label: operation.label, count: 0 };
      sources.push(source);
    }
    source.count = Math.max(0, source.count + delta);
  }
  if (state.collection === "feed" && state.filter === "unread" &&
      totalScope === filterCountScope() && state.total !== null &&
      (state.source === "all" || state.source === operation.source) &&
      (!state.query || state.query === operation.query))
    state.total = Math.max(0, state.total + delta);
  if (state.collection === "feed" && state.filter === "unread" &&
      state.dateCountsScope === countScope() && state.dateCounts &&
      (state.source === "all" || state.source === operation.source) &&
      (state.category === "all" || state.category === operation.category) &&
      (!state.query || state.query === operation.query))
    state.dateCounts[operation.date] = Math.max(0, (state.dateCounts[operation.date] || 0) + delta);
  if (!readCountsFrame) readCountsFrame = requestAnimationFrame(renderReadCounts);
}

function setReadState(item, value, silent) {
  if (!readOperations.has(item.id))
    readOperations.set(item.id, {
      read_at: item.read_at, fromFeed: state.collection === "feed",
      source: sourceKey(item), label: item.source, query: state.query, category: item.category,
      date: newsDate(item),
    });
  return stateWrites.set(item, "read", value, silent).then(saved => {
    if (!stateWrites.has(item.id, "read")) readOperations.delete(item.id);
    return saved;
  });
}

let badgesTimer, badgesRunning = false, badgesPending = false;
const badgesWaiters = [];
function refreshBadges() {
  badgesPending = true;
  const promise = new Promise(resolve => badgesWaiters.push(resolve));
  // Throttle: new events do not push an already scheduled refresh further back.
  if (!badgesRunning && !badgesTimer) {
    badgesTimer = setTimeout(updateBadges, 120);
  }
  return promise;
}
async function updateBadges() {
  badgesTimer = null;
  // The visible counts already include pending reads. Reconcile only against
  // a settled snapshot, which cannot count the same read twice.
  if (stateWrites.pending.size) {
    badgesTimer = setTimeout(updateBadges, 120);
    return;
  }
  badgesRunning = true;
  badgesPending = false;
  const snapshot = countSnapshot();
  const feedSequence = state.sequence;
  const scope = filterCountScope();
  const dateScope = countScope();
  const params = new URLSearchParams({ ...feedParams(), category: "all", count_only: "1", feed_filters: "1" });
  const hasFeed = !["notes", "summaries"].includes(state.collection);
  const scopedDates = state.collection === "feed" && state.category !== "all";
  if (hasFeed && !scopedDates) params.set("date_counts", "1");
  const updateDates = data => {
    if (!countsAreCurrent(snapshot) || feedSequence !== state.sequence || dateScope !== countScope()) return;
    state.dateCounts = data.date_counts || {};
    state.dateCountsScope = dateScope;
    renderFeedDateCounts();
  };
  if (state.collection === "summaries") summaries.refreshScope();
  void loadSources();
  try {
    await Promise.allSettled([
      api("/api/nav-summary").then(data => {
        if (!countsAreCurrent(snapshot)) return;
        navSummary = data.summary;
        renderNavCounts();
      }),
      hasFeed ? api(`/api/${state.query ? "search" : "news"}?${params}`).then(count => {
        if (!countsAreCurrent(snapshot) || feedSequence !== state.sequence || scope !== filterCountScope()) return;
        totalScope = scope;
        state.total = count.total;
        if (!scopedDates) updateDates(count);
        renderFilters();
      }) : null,
      scopedDates ? api(`/api/${state.query ? "search" : "news"}?${new URLSearchParams({ ...feedParams(), count_only: "1", feed_filters: "1", date_counts: "1" })}`).then(updateDates) : null,
    ]);
  } finally {
    badgesRunning = false;
    if (badgesPending) badgesTimer = setTimeout(updateBadges, 120);
    else badgesWaiters.splice(0).forEach(resolve => resolve());
  }
}
let sourcesSequence = 0;
const sourceCountsByCollection = new Map();
const sourceCatalogByCollection = new Map();
async function loadSources() {
  const currentCollection = state.collection;
  const collection = currentCollection === "summaries" ? "feed" : currentCollection, sequence = ++sourcesSequence;
  const readFilter = ["feed", "read_later"].includes(collection) ? "unread" : "all";
  const snapshot = countSnapshot();
  try {
    // Source availability covers the whole collection; badges still count unread items.
    const [data, catalog] = await Promise.all([
      api(`/api/sources?collection=${collection}&read_filter=${readFilter}`),
      readFilter === "unread" && !sourceCatalogByCollection.has(currentCollection) ? api(`/api/sources?collection=${collection}&read_filter=all`) : null,
    ]);
    if (sequence !== sourcesSequence || currentCollection !== state.collection) return;
    if (countsAreCurrent(snapshot)) sourceCountsByCollection.set(currentCollection, data.sources);
    if (catalog || readFilter === "all" || !sourceCatalogByCollection.has(currentCollection))
      sourceCatalogByCollection.set(currentCollection, (catalog || data).sources);
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
  for (const source of sourceCatalogByCollection.get(state.collection) || [])
    if (!sources.some(item => item.key === source.key)) sources.push({ ...source, count: counts.get(source.key)?.count || 0 });
  $("#sourceFilters").innerHTML =
    `<div class="source-row"><button class="source-filter-button ${state.source === "all" ? "selected" : ""}" type="button" data-source="all"><span class="source-row-info"><span class="source-icon-badge all-sources">${icon("sources")}</span><span>全部来源</span></span>${badge(sourceCounts.reduce((n, s) => n + s.count, 0))}</button></div>` +
    sources
      .map((source) => {
        return `<div class="source-row"><button class="source-filter-button ${state.source === source.key ? "selected" : ""}" type="button" data-source="${e(source.key)}"><span class="source-row-info">${sourceBadge(source.key)}<span>${e(source.label)}</span></span>${badge(source.count)}</button></div>`;
      })
      .join("");
}
function switchCollection(collection, { restoreReader = true } = {}) {
  if (!labels[collection]) return;
  state.collectionSequence++;
  state.ideaSequence++;
  settings.close({ restoreFocus: false });
  rememberReader();
  clearTimeout(detailTimer);
  state.detailSequence++;
  state.detailRequestSequence++;
  readerHighlights.hide();
  clearTimeout(feedEndTimer);
  feedEndTimer = null;
  state.collection = collection;
  renderSources();
  loadSources();
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
  $("#feedColIdeas").classList.toggle("active", ideas);
  $("#feedColSummaries").classList.toggle("active", summary);
  syncDetailPanels();
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
    if (state.idea) selectIdea(state.idea, false);
    loadIdeas();
  } else {
    loadFeed();
    if (restoreReader && state.selected && state.detail) {
      renderReader();
      $("#detailScrollArea").scrollTop = state.readerViews.get(state.selected.id)?.scroll || 0;
      agent.select(state.selected, state.detail.agent);
      pollDetail(state.selected, state.detailSequence);
    } else if (restoreReader && state.selected) selectNews(state.selected, false, false, { skipRemember: true });
  }
  renderFilters();
}
function readingArticle() {
  return state.collection !== "summaries" && (state.collection !== "notes" || state.idea?.idea_type === "article_note");
}
function syncDetailPanels() {
  $("#detailPanelFeed").classList.toggle("active", readingArticle());
  $("#detailPanelIdeas").classList.toggle("active", state.collection === "notes" && !readingArticle());
  $("#detailPanelSummaries").classList.toggle("active", state.collection === "summaries");
  syncMobileLayers();
}
function rememberReader() {
  if (!state.selected || !state.detail || !$("#detailPanelFeed").classList.contains("active")) return;
  state.readerViews.set(state.selected.id, { mode: state.bodyMode, scroll: $("#detailScrollArea").scrollTop });
}
async function selectNews(item, mobile = true, openedByUser = false, { skipRemember = false, notificationAgent = null, navigationCurrent = () => true, retryNotification = null } = {}) {
  if (!item) return;
  // Only opening an already-readable card completes it. Default selection and
  // a fetch finishing in the open reader must leave the article pending.
  const completeOnOpen = openedByUser && state.collection === "read_later" && readLaterState(item).phase === "ready";
  const readLaterAt = item.read_later_at;
  const collectionSequence = state.collectionSequence;
  if (!skipRemember) rememberReader();
  clearTimeout(detailTimer);
  state.selected = item;
  state.detail = null;
  const view = state.readerViews.get(item.id);
  state.bodyMode = view?.mode || "translated";
  const sequence = ++state.detailSequence;
  const requestSequence = ++state.detailRequestSequence;
  const stateVersion = stateWrites.version;
  renderFeed();
  renderReader();
  $("#detailScrollArea").scrollTop = 0;
  agent.select(item, null, { navigating: !!notificationAgent });
  if (mobile) enterReader();
  try {
    const data = await api(`/api/news/${encodeURIComponent(item.id)}/detail`);
    if (sequence !== state.detailSequence || requestSequence !== state.detailRequestSequence || !navigationCurrent()) return false;
    stateWrites.reconcile(item.id, data, stateVersion);
    state.detail = data;
    updateDetailItem(item, data);
    renderReader();
    $("#detailScrollArea").scrollTop = view?.scroll || 0;
    renderFeed();
    if (completeOnOpen && collectionSequence === state.collectionSequence && state.collection === "read_later" &&
        !settings.open && !document.hidden && !document.querySelector("dialog[open]") &&
        (!singlePane.matches || mobileReaderOpen()) && $("#detailPanelFeed").getClientRects().length &&
        item.read_later_at === readLaterAt && readLaterState(item).phase === "ready" &&
        (data.detail?.content?.trim() || data.ai?.body_zh?.trim()))
      void toggleState(item, "read_later", false, true, true);
    agent.select(item, notificationAgent || data.agent, { pin: !!notificationAgent });
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
    return true;
  } catch (error) {
    if (sequence !== state.detailSequence || requestSequence !== state.detailRequestSequence || !navigationCurrent()) return false;
    $("#detailContent").innerHTML =
      `<div class="list-message error-state">${e(errorText(error))}<button class="btn-secondary" id="retryReaderBtn" type="button">重新加载</button></div>`;
    $("#retryReaderBtn").onclick = retryNotification || (() => selectNews(item, false, openedByUser));
    if (notificationAgent) throw error;
    return false;
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
    const stateVersion = stateWrites.version;
    const requestSequence = ++state.detailRequestSequence;
    try {
      const data = await api(`/api/news/${encodeURIComponent(item.id)}/detail`);
      if (sequence !== state.detailSequence || requestSequence !== state.detailRequestSequence) return;
      stateWrites.reconcile(item.id, data, stateVersion);
      const changed =
        data.detail_status !== state.detail.detail_status ||
        data.ai_status !== state.detail.ai_status ||
        data.detail?.content !== state.detail.detail?.content ||
        data.ai?.conclusion_zh !== state.detail.ai?.conclusion_zh ||
        data.ai?.body_zh !== state.detail.ai?.body_zh;
      state.detail = data;
      updateDetailItem(item, data);
      if (changed) {
        renderReader();
        agent.select(item, data.agent);
        renderFeed();
      }
      pollDetail(item, sequence);
    } catch {
      if (sequence === state.detailSequence && requestSequence === state.detailRequestSequence)
        toast("正文状态刷新失败，可点击重新加载。");
    }
  }, 3000);
}
function readerActions() {
  const item = state.selected;
  $$("[data-state]", $("#detailPanelFeed .detail-toolbar")).forEach(
    (button) => {
      button.disabled = !item || (button.dataset.state === "favorite" && state.removingFavorites);
      button.setAttribute("aria-busy", String(!!item && stateWrites.has(item.id, button.dataset.state)));
      button.classList.toggle("active", !!item?.[`${button.dataset.state}_at`]);
      button.setAttribute(
        "aria-pressed",
        String(!!item?.[`${button.dataset.state}_at`]),
      );
      if (button.dataset.state === "favorite") {
        const label = item?.favorite_at ? "取消收藏" : "收藏";
        $("span", button).textContent = label;
        button.title = `${label} (快捷键: f)`;
      }
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
    agent.syncPlacement();
    return;
  }
  const detail = payload?.detail;
  $("#agentDock").hidden = false;
  agent.syncPlacement();
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
  const pipelineError = (["failed", "skipped"].includes(payload?.detail_status) ? payload?.job?.last_error : "")
    || (payload?.ai_status === "failed" ? payload?.ai_job?.last_error : "");
  const thoughts = '<div id="researchNotesMount"></div>';
  const hasThoughts = hasContent || savedNotes.length || (payload && state.collection === "notes" && state.idea?.idea_type === "article_note");
  $("#detailContent").innerHTML =
    `<article class="article-header"><div class="article-meta-top"><div class="article-author-info">${sourceBadge(sourceKey(item))}<strong>${e(item.source || "未知来源")}</strong>${detail?.author ? ` · ${e(detail.author)}` : ""}</div><span>发布于 ${e(item.published_at || item.date || "日期未知")}</span></div><h1 class="article-title" id="articleMainTitle">${e(item.title)}</h1><div class="article-meta-bottom">${body ? `<span>估读：${Math.max(1, Math.ceil(body.length / 650))} 分钟</span>` : ""}<span>信源：${e(item.source_file?.split("/").at(-1) || item.source || "")}</span><button class="reader-action-btn" id="fetchArticleBtn" type="button" ${!payload || ["pending", "running"].includes(payload?.detail_status) || ["pending", "running"].includes(payload?.ai_status) ? "disabled" : ""}>${detail?.content ? "重新翻译" : "抓取全文"}</button>${hasThoughts && !hasInsights ? '<button class="reader-action-btn" id="writeArticleThoughtBtn" type="button">写想法</button>' : ""}</div></article>
    ${hasThoughts ? `<section class="digest-box ${hasInsights ? "" : "notes-only"}" aria-label="${hasInsights ? "本篇结构化精读" : "阅读笔记"}" ${!hasInsights && !savedNotes.length && state.collection !== "notes" ? "hidden" : ""}>${hasInsights ? `<div class="digest-header"><div class="digest-badge">${icon("read")}<span>DeepSeek 结构化精读</span></div><div class="digest-mode-tabs"><button class="digest-mode-btn ${state.bodyMode === "translated" ? "active" : ""}" data-body-mode="translated" type="button">中文精读</button><button class="digest-mode-btn ${state.bodyMode === "original" ? "active" : ""}" data-body-mode="original" type="button">英文原文</button></div></div>${ai?.conclusion_zh ? `<p class="digest-lead" data-highlight-kind="ai_conclusion_zh">${readerInline(ai.conclusion_zh)}</p>` : ""}${points.length ? `<ul class="digest-points" data-highlight-kind="ai_points_zh">${pointsHTML}</ul>` : ""}` : ""}${thoughts}</section>` : ""}
    ${body?.trim() ? `<div class="article-body" id="articleBodyContent" data-highlight-kind="${bodyKind}">${readerMarkdown(body)}</div>` : ""}
    ${detail?.media_images?.length ? `<div class="article-media">${detail.media_images.map((image) => (safeURL(image.cached_url || image.url) ? `<img src="${e(safeURL(image.cached_url || image.url))}" alt="${e(image.alt || "新闻配图")}" loading="lazy">` : "")).join("")}</div>` : ""}
    ${pipelineError ? `<div class="inline-error">${e(errorText({ error: pipelineError }))}</div>` : ""}`;
  researchNotes.mount($("#researchNotesMount"), item.id, savedNotes, notesFocus);
  readerHighlights.mount(item.id, { [bodyKind]: body || "", ai_conclusion_zh: ai?.conclusion_zh || "", ai_points_zh: points.join("") }, payload?.highlight_surfaces);
  const writeThought = $("#writeArticleThoughtBtn");
  if (writeThought) {
    writeThought.setAttribute("aria-controls", "researchNoteComposer");
    writeThought.onclick = () => researchNotes.toggle();
  }
  if (state.collection === "notes" && hasThoughts) {
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "reader-action-btn";
    jump.textContent = "查看想法";
    jump.onclick = () => {
      if (!researchNotes.entries.length) researchNotes.open();
      else $(".research-notes").scrollIntoView({ block: "start", behavior: "instant" });
    };
    $(".article-meta-bottom").append(jump);
  }
  const sequence = state.detailSequence;
  $("#fetchArticleBtn").onclick = (event) =>
    busy(event.currentTarget, async () => {
      await api(`/api/news/${encodeURIComponent(item.id)}/detail/retry`, {
        method: "POST",
        body: detail?.content ? { mode: "ai" } : {},
      });
      toast(detail?.content ? "翻译任务已提交" : "正文抓取已提交");
      if (sequence === state.detailSequence && readingArticle() && state.selected?.id === item.id)
        await selectNews(item, false);
    });
}
function toggleState(item, flag, force, silent = false, complete = false) {
  if (!item || (flag === "favorite" && state.removingFavorites)) return;
  let value = force ?? !item[`${flag}_at`];
  if (flag === "read_later" && force === undefined) {
    const { phase } = readLaterState(item, stateWrites.has(item.id, flag));
    if (phase === "running") {
      toast("正文正在抓取，完成后会进入全文待看");
      return;
    }
    complete = phase === "ready";
    value = !["ready", "pending"].includes(phase);
  }
  return flag === "read" ? setReadState(item, value, silent) : stateWrites.set(item, flag, value, silent, complete);
}
async function refreshStateDetail(id) {
  // Saving a flag never waits for the full article. Only the open reader needs
  // its body refreshed; other cards only need the lightweight job status.
  const refreshReader = readingArticle() && state.selected?.id === id;
  const sequence = state.detailSequence;
  const feedSequence = state.sequence;
  const stateVersion = stateWrites.version;
  const requestSequence = refreshReader ? ++state.detailRequestSequence : null;
  try {
    if (!refreshReader) {
      const data = await api(`/api/news/status?${new URLSearchParams({ ids: id })}`);
      if (feedSequence !== state.sequence) return;
      const item = state.items.find(row => row.id === id);
      const status = data.items.find(row => row.id === id);
      if (item && status) {
        Object.assign(item, stateWrites.reconcile(id, status, stateVersion));
        updateCard(item);
      }
      return;
    }
    const payload = await api(`/api/news/${encodeURIComponent(id)}/detail`);
    if (state.selected?.id !== id || sequence !== state.detailSequence || requestSequence !== state.detailRequestSequence || stateWrites.has(id, "read_later")) return;
    stateWrites.reconcile(id, payload, stateVersion);
    const item = state.selected;
    updateDetailItem(item, payload);
    state.detail = payload;
    renderReader();
    agent.select(item, payload.agent);
    pollDetail(item, sequence);
  } catch {
    if (!refreshReader || (state.selected?.id === id && sequence === state.detailSequence && requestSequence === state.detailRequestSequence))
      toast("标记已保存，正文状态刷新失败，可点击重新加载。");
  }
}
function updateDetailItem(item, payload) {
  const patch = { detail_ready: payload.detail_ready ?? !!payload.detail?.content, detail_status: payload.detail_status, ai_status: payload.ai_status, ai_conclusion_zh: payload.ai?.conclusion_zh || "", has_note: payload.has_note, note_count: payload.notes?.length || 0 };
  for (const flag of ["read", "important", "read_later", "read_later_done", "favorite"])
    if (Object.hasOwn(payload, `${flag}_at`)) patch[`${flag}_at`] = payload[`${flag}_at`];
  const loaded = state.items.find(row => row.id === item.id);
  let changed = false;
  for (const target of new Set([item, loaded, state.selected?.id === item.id ? state.selected : null])) {
    if (!target) continue;
    const wasPending = readLaterPending(target);
    Object.assign(target, patch);
    changed ||= wasPending !== readLaterPending(target);
  }
  updateCard(loaded || item);
  if (state.selected?.id === item.id) readerActions();
  if (changed) refreshBadges();
}
let statusRefreshing = false;
setInterval(async () => {
  if (statusRefreshing || document.hidden || settings.open) return;
  const ids = state.items.filter(item => ["pending", "running"].includes(item.detail_status) || ["pending", "running"].includes(item.ai_status) || (item.read_later_at && !item.detail_ready && !["failed", "skipped", "canceled"].includes(item.detail_status))).slice(0, 50).map(item => item.id);
  if (!ids.length) return;
  statusRefreshing = true;
  const stateVersion = stateWrites.version;
  const feedSequence = state.sequence;
  try {
    const data = await api(`/api/news/status?${new URLSearchParams({ ids: ids.join(",") })}`);
    if (feedSequence !== state.sequence) return;
    let changed = false;
    for (const row of data.items) {
      stateWrites.reconcile(row.id, row, stateVersion);
      const item = state.items.find(item => item.id === row.id);
      if (item) {
        const wasPending = readLaterPending(item);
        Object.assign(item, row);
        changed ||= wasPending !== readLaterPending(item);
        updateCard(item);
      }
    }
    if (changed) refreshBadges();
  } catch { /* The reader reports detailed fetch failures. Retry on the next tick. */ }
  finally { statusRefreshing = false; }
}, 3000);
function pipelineLabel(payload) {
  if (!payload) return "正在读取正文…";
  if (payload.detail_status === "pending" && payload.job?.next_attempt_at) return "等待重试正文…";
  const fetchLabels = { pending: "等待抓取正文", running: "正文抓取中…", failed: "正文抓取失败", canceled: "正文抓取已取消", skipped: "正文抓取已跳过" };
  if (fetchLabels[payload.detail_status]) return fetchLabels[payload.detail_status];
  if (!payload.detail?.content) return "尚未抓取正文";
  return ({ pending: "全文就绪 · 等待翻译", running: "全文就绪 · 正文翻译中…", failed: "全文就绪 · 翻译失败", canceled: "全文就绪 · 翻译已取消", skipped: "全文就绪 · 翻译已跳过" })[payload.ai_status] || (payload.ai?.body_zh ? "全文就绪 · 翻译完成" : "全文就绪 · 尚未翻译");
}
const enteredCards = new Set();
let lastFeedScroll = 0, feedEndTimer;
function feedVisible() {
  return state.collection === "feed" && !settings.open && !mobileReaderOpen() &&
    !document.hidden && !document.querySelector("dialog[open]") && $("#feedCardList").getBoundingClientRect().width > 0;
}
function markReadIds(ids) {
  const items = new Map(state.items.map(item => [item.id, item]));
  for (const id of ids) {
    const item = items.get(id);
    if (item && !item.read_at && !stateWrites.has(id, "read"))
      void setReadState(item, true, true);
  }
}
function trackFeedReading() {
  if (!feedVisible()) { clearTimeout(feedEndTimer); feedEndTimer = null; return; }
  const list = $("#feedCardList"), bounds = list.getBoundingClientRect();
  const down = list.scrollTop > lastFeedScroll;
  const ids = [];
  const items = new Map(state.items.map(item => [item.id, item]));
  for (const node of $$(".news-card", list)) {
    const id = node.dataset.id;
    if (items.get(id)?.read_at) continue;
    const rect = node.getBoundingClientRect();
    if (rect.bottom > bounds.top && rect.top < bounds.bottom) enteredCards.add(id);
    if (down && enteredCards.has(id) && rect.bottom <= bounds.top) ids.push(id);
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
  if (!later && state.loading) return toast("新闻列表正在加载，请稍后再试");
  const params = feedParams(), sequence = state.sequence, scope = countScope();
  const sourceName = params.source_filter === "all" ? "全部来源"
    : $(`#sourceFilters [data-source="${CSS.escape(params.source_filter)}"] .source-row-info > span:last-child`)?.textContent || params.source_filter;
  let items = [...state.items], failed = 0, marked = 0;
  // Earlier button saves must finish before the bulk operation supersedes them.
  await stateWrites.settle();
  if (!later) {
    params.read_filter = "unread";
    // Fetch the full scoped count, not just the loaded page or a stale badge.
    const preview = params.q
      ? await searchNews(sequence, undefined, params)
      : await api(`/api/news?${new URLSearchParams({ ...params, count_only: "1" })}`);
    if (sequence !== state.sequence || scope !== countScope()) return toast("新闻范围已变化，请重新点击全部已读");
    if (!preview.total) return toast(`${sourceName}：当前范围没有未读新闻`);
    if (params.q) items = preview.items;
    const sourceScope = params.source_filter === "all" ? sourceName : `「${sourceName}」`;
    const search = params.q ? `\n搜索条件：「${params.q}」` : "";
    const category = params.category !== "all" ? `\n新闻分类：「${categoryLabels[params.category]}」` : "";
    if (!await confirmAction({ title: "全部标为已读", trigger: "#markAllReadBtn", message: `确认将${sourceScope}的 ${preview.total} 条未读新闻标为已读？${category}${search}\n包含当前范围内尚未加载的新闻。`, action: "全部标为已读" })) return;
    if (sequence !== state.sequence || scope !== countScope()) return toast("新闻范围已变化，请重新点击全部已读");
    const current = params.q ? await searchNews(sequence, undefined, params) : await api(`/api/news?${new URLSearchParams({ ...params, count_only: "1" })}`);
    if (sequence !== state.sequence || scope !== countScope() || current.total !== preview.total ||
        (params.q && JSON.stringify(current.items.map(item => item.id).sort()) !== JSON.stringify(items.map(item => item.id).sort())))
      return toast("新闻范围已变化，请重新点击全部已读");
  }
  if (params.q) {
    if (later) {
      if (params.read_filter !== "read")
        for (const item of items.filter(readLaterPending))
          if (!await toggleState(item, "read_later", false, true, true)) failed++;
    } else {
      const result = await api("/api/news/mark-read-by-ids", { method: "POST", body: { item_ids: items.map(item => item.id) } });
      marked = result.marked;
    }
  } else {
    const result = await api(later ? "/api/news/clear-read-later" : "/api/news/mark-all-read", { method: "POST", body: params });
    marked = result.marked;
  }
  await loadFeed();
  if (state.selected) await selectNews(state.selected, false);
  refreshBadges();
  toast(failed ? `有 ${failed} 篇未能标为已看完，请重试` : later ? "当前范围已全部看完" : `${sourceName}：已将 ${marked} 条新闻标为已读`);
}
async function loadIdeas({ quiet = false } = {}) {
  const sequence = ++state.ideaSequence;
  if (!quiet) $("#ideasCardList").innerHTML =
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
        while (more && sequence === state.ideaSequence) {
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
    if (sequence !== state.ideaSequence || state.collection !== "notes") return;
    state.ideas = arrays
      .flat()
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    renderIdeas();
    if (!state.idea && visibleIdeas().length && !singlePane.matches)
      selectIdea(visibleIdeas()[0], false);
    else if (!state.idea) { standaloneIdeas.select(null); syncDetailPanels(); }
  } catch (error) {
    if (sequence === state.ideaSequence && state.collection === "notes")
      $("#ideasCardList").innerHTML =
        `<li class="list-message error-state">${e(errorText(error))}<button id="retryIdeasBtn" class="btn-secondary" type="button">重试</button></li>`;
  }
}
function visibleIdeas() {
  const q = $("#ideasSearchInput").value.trim().toLowerCase();
  return state.ideas.filter(idea =>
    (state.ideaType === "all" || idea.idea_type === `${state.ideaType}_note`) &&
    `${idea.title} ${idea.note}`.toLowerCase().includes(q),
  ).sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}
function renderIdeas() {
  const ideas = visibleIdeas(), list = $("#ideasCardList"), scroll = list.scrollTop;
  list.innerHTML = ideas.map(idea => {
    const article = idea.idea_type === "article_note";
    const latest = article ? idea.latest_note_preview || idea.note_preview : idea.note;
    return `<li class="news-card ${state.idea?.idea_id === idea.idea_id ? "active is-active" : ""}" data-idea-id="${e(idea.idea_id)}" tabindex="0"><div class="news-card-meta"><div class="meta-source-box"><span class="source-icon-badge idea">想</span>${e(idea.idea_context_label)}</div><span>${e(idea.updated_at?.slice(0, 16) || "")}</span></div><h3 class="news-card-title">${e(article ? idea.title : standaloneTitle(idea))}</h3><p class="news-card-snippet">${e(latest)}</p><div class="news-card-footer"><span class="card-tag">${e(idea.source || "独立想法")}</span>${article ? `<span class="note-badge" aria-label="${idea.note_count || 1} 条想法">${icon("note")} ${idea.note_count || 1}</span>` : ""}</div></li>`;
  }).join("") || `<li class="list-message">${$("#ideasSearchInput").value.trim() ? "没有匹配的想法，试试其他关键词。" : "暂无想法。点击“新想法”开始记录。"}</li>`;
  list.scrollTop = scroll;
}
function selectIdea(idea, mobile = true) {
  if (!idea) return;
  rememberReader();
  state.idea = idea;
  readerHighlights.hide();
  syncDetailPanels();
  if (idea.idea_type === "article_note") {
    selectNews(idea, mobile, false, { skipRemember: true });
  } else {
    clearTimeout(detailTimer);
    state.detailSequence++;
    state.detailRequestSequence++;
    agent.select(null, null);
    standaloneIdeas.select(idea);
    $("#ideaScrollArea").scrollTop = 0;
    if (mobile) enterReader();
  }
  renderIdeas();
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
  agent.syncPlacement();
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
    !(panel.id === "detailPanelFeed" && agent.open);
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
  syncFeedSearch();
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
  if (button?.dataset.category) {
    state.category = button.dataset.category;
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
    state.bodyMode = button.dataset.bodyMode;
    renderReader();
  }
  if (button?.dataset.closeShortcuts !== undefined) shortcutsDialog(false);
  if (button?.id === "loadMoreBtn") loadFeed(true);
  if (button?.id === "retryIdeasBtn") loadIdeas();
  if (button?.id === "newIdeaBtn")
    selectIdea({ idea_id: "draft", idea_type: "standalone_note", title: "", note: "" });
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
function syncFeedSearch() {
  const row = $("#feedSearchRow"), toggle = $("#feedSearchToggleBtn");
  const visible = !singlePane.matches || state.mobileSearchOpen;
  if (!visible && row.contains(document.activeElement)) toggle.focus({ preventScroll: true });
  row.inert = !visible;
  row.setAttribute("aria-hidden", String(!visible));
  // Keep the desktop field visible until the breakpoint handler can transfer its focus.
  $("#feedColFeed").classList.toggle("search-open", visible);
  toggle.setAttribute("aria-expanded", String(visible));
  const label = visible ? "收起新闻搜索" : "展开新闻搜索";
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
}
function setFeedSearchOpen(open, focus = false) {
  const input = $("#feedSearchInput");
  const resetSearch = !open && singlePane.matches &&
    Boolean(state.query || input.value || searchTimer !== undefined);
  if (resetSearch) {
    clearTimeout(searchTimer);
    searchTimer = undefined;
    state.query = "";
    input.value = "";
  }
  state.mobileSearchOpen = open;
  syncFeedSearch();
  if (open && focus) input.focus({ preventScroll: true });
  if (resetSearch) void loadFeed();
}
$("#feedSearchToggleBtn").onclick = () => setFeedSearchOpen(!state.mobileSearchOpen, !state.mobileSearchOpen);
$("#feedSearchInput").addEventListener("keydown", event => {
  if (singlePane.matches && event.key === "Escape") {
    event.preventDefault(); event.stopPropagation();
    setFeedSearchOpen(false);
  }
});
syncFeedSearch();
$("#feedSearchInput").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    searchTimer = undefined;
    void loadFeed();
  }, 250);
});
$("#ideasSearchInput").addEventListener("input", renderIdeas);
$("#feedCardList").addEventListener("scroll", () => {
  scheduleFeedDateHeaders();
  trackFeedReading();
  const list = $("#feedCardList");
  if (
    list.scrollTop + list.clientHeight >= list.scrollHeight - 250 &&
    list.scrollHeight > list.clientHeight
  )
    loadFeed(true);
});
new ResizeObserver(scheduleFeedDateHeaders).observe($("#feedCardList"));
$("#sortNewsBtn").onclick = () => { state.sortOrder = state.sortOrder === "default" ? "reverse" : "default"; loadFeed(); };
$("#markAllReadBtn").onclick = event => busy(event.currentTarget, markAllRead);
document.addEventListener("visibilitychange", trackFeedReading);
function syncVisibleCounts() {
  if (document.hidden) return;
  sourceCatalogByCollection.clear();
  refreshBadges();
  void refreshClassification();
}
document.addEventListener("visibilitychange", syncVisibleCounts);
window.addEventListener("focus", syncVisibleCounts);
setInterval(syncVisibleCounts, 30000);
$("#clearFavoritesBtn").onclick = clearFavorites;
$("#themeSelect").value = document.documentElement.dataset.theme || "system";
$("#themeSelect").onchange = (event) => {
  document.documentElement.dataset.theme = event.target.value;
  persist("newsreader_workbench_theme_mode", event.target.value);
};
$("#fontSizeSelect").value = document.documentElement.dataset.fontSize || "standard";
$("#fontSizeSelect").onchange = (event) => {
  document.documentElement.dataset.fontSize = event.target.value;
  scheduleFeedDateHeaders();
  persist("newsreader_workbench_font_size", event.target.value);
};
selects.refresh();
$("#leftNavSettingsBtn").onclick = () => settings.toggle();
$("#openNavBtn").onclick = openNav;
$(".drawer-close-btn").onclick = closeNav;
$("#navDrawerBackdrop").onclick = closeNav;
$$(".mobile-back-btn").forEach((button) => (button.onclick = exitReader));
$("#shortcutsModal").onclick = (event) => {
  if (event.target.id === "shortcutsModal") shortcutsDialog(false);
};
const refreshButton = $("#refreshIndexBtn");
function refreshIndex(notify = false) {
  return busy(refreshButton, async () => {
    indexStatus.syncing = true;
    renderIndexStatus();
    let scanError;
    try {
      await api("/api/reindex", { method: "POST" });
      indexStatus.syncedAt = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
      indexStatus.scanFailed = false;
    } catch (error) {
      scanError = error;
      indexStatus.scanFailed = true;
    }
    indexStatus.syncing = false;
    renderIndexStatus();
    sourceCatalogByCollection.clear();
    await Promise.all([loadFeed(), refreshBadges()]);
    if (scanError) {
      throw scanError;
    }
    if (notify) toast("新闻索引已刷新");
  });
}
refreshButton.onclick = () => refreshIndex(true);

document.addEventListener("keydown", (event) => {
  if (document.querySelector("#appConfirmDialog[open]") || summaries.modal.open || upstreamErrors.open || !notifications.panel.hidden || !classification.panel.hidden) return;
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
  const key = shortcutAliases[event.key] || event.key.toLowerCase();
  if (key === ",") {
    event.preventDefault();
    settings.toggle();
  } else if (state.collection === "summaries" && key !== "?") {
    summaries.handleKey(event);
  } else if (key === "/") {
    event.preventDefault();
    if (state.collection === "notes") $("#ideasSearchInput").focus();
    else {
      if (singlePane.matches) exitReader();
      setFeedSearchOpen(true, true);
    }
  } else if (key === "?") {
    event.preventDefault();
    shortcutsDialog(true);
  } else if (key === "q" && readingArticle()) {
    event.preventDefault();
    agent.toggle();
  } else if (shortcuts[key] && readingArticle()) {
    event.preventDefault();
    if (key === "m" && state.collection === "read_later") toggleState(state.selected, "read_later", false, false, true);
    else toggleState(state.selected, shortcuts[key]);
  } else if (["w", "s", "j", "k"].includes(key) && state.collection === "notes") {
    event.preventDefault();
    const ideas = visibleIdeas(), index = ideas.findIndex(idea => idea.idea_id === state.idea?.idea_id);
    const next = ideas[Math.max(0, Math.min(ideas.length - 1, index + (["s", "j"].includes(key) ? 1 : -1)))];
    if (next) {
      selectIdea(next, false);
      $(`[data-idea-id="${CSS.escape(next.idea_id)}"]`)?.scrollIntoView({ block: "nearest" });
    }
  } else if (["w", "s", "j", "k"].includes(key) && state.collection !== "notes") {
    event.preventDefault();
    const index = state.items.findIndex(
      (item) => item.id === state.selected?.id,
    );
    const next =
      state.items[
        Math.max(
          0,
          Math.min(state.items.length - 1, index + (["s", "j"].includes(key) ? 1 : -1)),
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
  const dirty = standaloneIdeas.dirty || researchNotes.dirty;
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
