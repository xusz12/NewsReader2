import { newsUpdateBadge, escapeHTML as e, safeURL, icon } from "./ui.js";

export function sourceURL(value) {
  try {
    const url = new URL(value);
    return !url.username && !url.password ? safeURL(url.href) : "";
  } catch { return ""; }
}

export function sourceActionAttributes(item, flag, cardAction) {
  const action = cardAction(item, flag);
  return {class: `${action.className} card-action-btn--icon card-action-btn--plain`,
    title: action.title, "aria-label": action.title,
    "aria-pressed": String(action.active), "aria-busy": String(action.saving)};
}

export function updateSourceState(container, items, id, patch, cardAction) {
  const matches = Object.values(items || {}).filter(item => item.id === id);
  for (const item of matches) Object.assign(item, patch);
  if (!matches.length) return;
  for (const button of container.querySelectorAll(`[data-source-id="${CSS.escape(id)}"]`))
    for (const [key,value] of Object.entries(sourceActionAttributes(matches[0], button.dataset.sourceState, cardAction)))
      button.setAttribute(key, value);
}

function publishedTime(source, item) {
  const date = source.date ? `${source.date}T${source.time || "00:00:00"}` : "";
  for (const value of [source.published_at, item?.published_at, date]) {
    if (typeof value !== "string" || !value.trim()) continue;
    const iso = value.trim().replace(" ", "T");
    // Legacy timestamps without an offset use the application's Beijing time.
    const stamp = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso
      : /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso+"T00:00:00+08:00" : iso+"+08:00");
    if (Number.isFinite(stamp)) return stamp;
  }
  return Infinity;
}

export function chronologicalSources(sources, items = {}) {
  return sources.map(source => ({source, time:publishedTime(source, items[source.ref])}))
    .sort((a,b) => a.time === b.time ? 0 : a.time-b.time).map(row => row.source);
}

export function readerSources(sources, {className = "", name = "", label = "来源", dateLabel,
  items = {}, cardAction} = {}) {
  return `<details class="reader-sources ${className}" ${name ? `name="${e(name)}"` : ""}><summary class="reader-tool-btn">${e(label)} · ${sources.length}篇<svg class="reader-source-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary><div class="reader-source-list"><ul>${chronologicalSources(sources, items).map(source => {
    const item = items[source.ref], url = sourceURL(source.url);
    const title = e(source.title), date = dateLabel ? dateLabel(source) : source.date || "日期未知";
    const link = item ? `<button class="news-card-open reader-source-open" type="button" data-source-item="${e(item.id)}">${title}</button>`
      : url ? `<a class="reader-source-open" href="${e(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<span>${title}</span>`;
    const actions = item && cardAction ? ["important", "read_later"].map(flag => {
      const attributes = Object.entries(sourceActionAttributes(item, flag, cardAction)).map(([key,value])=>`${key}="${e(value)}"`).join(" ");
      return `<button ${attributes} type="button" data-source-state="${flag}" data-source-id="${e(item.id)}" data-state="${flag}">${icon(flag)}</button>`;
    }).join("") : "";
    return `<li>${link}<div class="news-card-meta"><span class="meta-source-box"><span class="reader-source-meta">${e(source.source_name || source.source || "未知来源")} · ${e(date)}</span>${newsUpdateBadge(item)}</span>${actions ? `<span class="meta-time-row">${actions}</span>` : ""}</div></li>`;
  }).join("")}</ul></div></details>`;
}
