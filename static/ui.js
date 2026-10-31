// Keep these boundaries aligned with the responsive rules in style.css.
export const singlePane = matchMedia("(max-width: 920px)");
export const drawerNavigation = matchMedia("(max-width: 1279px)");
// Shared presentation helpers. News, notes and model output are untrusted text.
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [
  ...root.querySelectorAll(selector),
];
export const formatBytes = value => Number(value) < 1024 * 1024
  ? `${(Number(value || 0) / 1024).toFixed(1)} KiB`
  : `${(Number(value) / 1024 / 1024).toFixed(2)} MiB`;
export const escapeHTML = (value = "") =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export function safeURL(value) {
  try {
    const url = new URL(value, location.origin);
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
// Transparent sticky date headings use the same clipping in every news list.
export function clipDateGroupHeaders(container) {
  const clips = [...container.querySelectorAll(".feed-date-group")].map(group => {
    const heading = $(".feed-date-heading", group), cards = $(".feed-date-cards", group);
    const rect = cards.getBoundingClientRect();
    const clip = Math.max(0, Math.min(rect.height, heading.getBoundingClientRect().bottom - rect.top));
    return [cards, clip ? `inset(${clip}px -8px -8px)` : ""];
  });
  for (const [cards, clip] of clips) cards.style.clipPath = clip;
}
// List views share search visibility, focus and keyboard behavior at the same breakpoint.
export class ListSearch {
  constructor({ column, label, onCollapse }) {
    Object.assign(this, { column, label, onCollapse, mobileOpen: false });
    this.row = $(".feed-search-row", column);
    this.input = $(".feed-search-input", column);
    this.toggle = $(".feed-search-toggle", column);
    this.toggle.onclick = () => this.setOpen(!this.mobileOpen, !this.mobileOpen);
    this.input.addEventListener("keydown", event => {
      if (singlePane.matches && event.key === "Escape" && !event.isComposing) {
        event.preventDefault(); event.stopPropagation(); this.setOpen(false);
      }
    });
    singlePane.addEventListener("change", () => this.sync());
    this.sync();
  }
  sync() {
    const visible = !singlePane.matches || this.mobileOpen;
    if (!visible && this.row.contains(document.activeElement)) this.toggle.focus({ preventScroll: true });
    this.row.inert = !visible;
    this.row.setAttribute("aria-hidden", String(!visible));
    this.column.classList.toggle("search-open", visible);
    this.toggle.setAttribute("aria-expanded", String(visible));
    const label = (visible ? "收起" : "展开") + this.label;
    this.toggle.setAttribute("aria-label", label); this.toggle.title = label;
  }
  setOpen(open, focus = false) {
    if (!open && singlePane.matches) this.onCollapse?.();
    this.mobileOpen = open;
    this.sync();
    if (open && focus) this.input.focus({ preventScroll: true });
  }
}
export async function api(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(path, {
    method,
    signal,
    cache: "no-store",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data || data.ok === false) {
    if (globalThis.NewsErrors)
      throw NewsErrors.fromResponse(data, response, `http_${response.status}`);
    throw new Error(data?.error || `请求失败（HTTP ${response.status}）`);
  }
  if (method !== "GET" && /^\/api\/(topics|reports)(\/|$)/.test(path) && !path.endsWith("/seen"))
    document.dispatchEvent(new Event("tasks:changed"));
  return data;
}
export function errorText(error) {
  if (globalThis.NewsErrors) {
    const info = NewsErrors.explain(error);
    return info.message;
  }
  return error?.message || "请求失败，请重试。";
}
let toastTimer;
export function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 4000);
}
export async function busy(button, work) {
  if (button?.disabled) return;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    return await work();
  } catch (error) {
    toast(errorText(error));
  } finally {
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}
export function storage(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
export function persist(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Private browsing can disable storage. */
  }
}
const paths = {
  spark: '<path d="m12 2 2.7 7.3L22 12l-7.3 2.7L12 22l-2.7-7.3L2 12l7.3-2.7Z" fill="currentColor" stroke="none"/>',
  lightbulb: '<path d="M9 18h6m-6 3h6M8 15c0-2-3-3-3-7a7 7 0 0 1 14 0c0 4-3 5-3 7v1H8z"/>',
  comment: '<path d="M21 15a2 2 0 0 1-2 2H7l-5 4V5a2 2 0 0 1 2-2h15a2 2 0 0 1 2 2z"/>',
  sortAsc: '<path d="M4 4v16m-3-3 3 3 3-3M11 5h4m-4 7h7m-7 7h10"/>',
  sortDesc: '<path d="M4 20V4M1 7l3-3 3 3M11 5h10m-10 7h7m-7 7h4"/>',
  checkAll: '<path d="m2 12 4 4L16 6m-6 10 4 4L24 10"/>',
  sources: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  send: '<path d="M12 20V4m-7 7 7-7 7 7"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  read: '<polyline points="20 6 9 17 4 12"/>',
  important:
    '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
  read_later:
    '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  favorite:
    '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  favoriteOff:
    '<path d="m2 2 20 20M9.6 6.9 12 2l3.1 6.3L22 9.3l-4.4 4.3M7.1 8.5 2 9.3l5 4.8L5.8 21l6.2-3.2 6.2 3.2-.8-4.7"/>',
  note: '<path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/>',
};
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">${paths[name] || paths.note}</svg>`;
export function sourceKey(item) {
  const value = `${item.source_type} ${item.source} ${item.url}`.toLowerCase();
  if (/reuters/.test(value)) return "reuters";
  if (/bloomberg/.test(value)) return "bloomberg";
  if (/techcrunch/.test(value)) return "techcrunch";
  if (/arstechnica|ars technica/.test(value)) return "ars";
  if (/twitter|\/\/x\.com/.test(value)) return "x";
  try {
    return "host:" + new URL(item.url).hostname;
  } catch {
    return "other";
  }
}
export const sourceBadge = (key) =>
  `<span class="source-icon-badge ${key === "x" ? "twitter" : escapeHTML(key)}">${{ reuters: "R", bloomberg: "B", techcrunch: "TC", ars: "AT", x: "𝕏" }[key] || "N"}</span>`;
export function inlineMarkdown(text) {
  return escapeHTML(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}
export function markdown(text = "") {
  // Deliberately small text renderer: no raw HTML, scripts, embeds or remote images.
  const blocks = String(text || "")
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/);
  let inCode = false;
  return blocks
    .map((block) => {
      if (block.startsWith("```") || inCode) {
        inCode = !block.endsWith("```");
        return `<pre><code>${escapeHTML(block.replace(/^```[^\n]*\n?/, "").replace(/```$/, ""))}</code></pre>`;
      }
      if (/^#{1,6} /.test(block))
        return block
          .split("\n")
          .map((line) =>
            /^#{1,6} /.test(line)
              ? `<h3>${inlineMarkdown(line.replace(/^#+ /, ""))}</h3>`
              : `<p>${inlineMarkdown(line)}</p>`,
          )
          .join("");
      if (/^>/.test(block))
        return `<blockquote>${inlineMarkdown(block.replace(/^>\s?/gm, "")).replace(/\n/g, "<br>")}</blockquote>`;
      if (/^(?:[-*]|\d+\.) /.test(block))
        return `<ul>${block
          .split("\n")
          .map(
            (line) =>
              `<li>${inlineMarkdown(line.replace(/^(?:[-*]|\d+\.) /, ""))}</li>`,
          )
          .join("")}</ul>`;
      return `<p>${inlineMarkdown(block).replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
}
export function focusTrap(event, container) {
  if (event.key !== "Tab") return;
  const controls = $$(
    'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    container,
  ).filter((el) => el.getClientRects().length);
  const first = controls[0],
    last = controls.at(-1);
  if (!first) return;
  if (
    event.shiftKey &&
    (document.activeElement === first ||
      !container.contains(document.activeElement))
  ) {
    event.preventDefault();
    last.focus();
  } else if (
    !event.shiftKey &&
    (document.activeElement === last ||
      !container.contains(document.activeElement))
  ) {
    event.preventDefault();
    first.focus();
  }
}
// Measure a hidden visual copy so resizing never collapses the focused field.
export function resizeAutoFields(root) {
  const fields = [...root.querySelectorAll('textarea[data-auto-rows]')].filter(field => field.getClientRects().length);
  const heights = new Map();
  for (const field of fields) {
    const css = getComputedStyle(field), line = parseFloat(css.lineHeight);
    const padding = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
    const border = parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth);
    const copy = field.cloneNode();
    copy.removeAttribute('id'); copy.removeAttribute('name'); copy.removeAttribute('data-auto-rows');
    copy.setAttribute('aria-hidden', 'true'); copy.tabIndex = -1; copy.value = field.value;
    Object.assign(copy.style, {position:'absolute', visibility:'hidden', pointerEvents:'none',
      width:field.getBoundingClientRect().width+'px', height:'0px', minHeight:'0', maxHeight:'none'});
    field.parentElement.append(copy);
    heights.set(field, Math.ceil(Math.max(line * 2 + padding + border,
      Math.min(line * 7 + padding + border, copy.scrollHeight + border))));
    copy.remove();
  }
  for (const field of fields) {
    const group = field.dataset.equalHeight;
    const height = group ? Math.max(...fields.filter(other => other.dataset.equalHeight === group).map(other => heights.get(other))) : heights.get(field);
    if (field.style.height !== height+'px') field.style.height = height+'px';
  }
}
