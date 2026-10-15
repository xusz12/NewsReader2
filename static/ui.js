// Shared presentation helpers. News, notes and model output are untrusted text.
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [
  ...root.querySelectorAll(selector),
];
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
    if (globalThis.NewsErrors && data)
      throw NewsErrors.fromResponse(data, response, "request_failed");
    throw new Error(data?.error || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}
export function errorText(error) {
  if (globalThis.NewsErrors) {
    const info = NewsErrors.explain(error);
    return `${info.message} ${info.action || ""}`.trim();
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
  read: '<polyline points="20 6 9 17 4 12"/>',
  important:
    '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
  read_later:
    '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  favorite:
    '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
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
