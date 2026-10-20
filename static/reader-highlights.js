import { $, $$, api, busy, escapeHTML as e, singlePane } from "./ui.js";

// Source offsets stay in the original Markdown. This avoids ambiguous text searches
// when a paragraph repeats, a link hides its URL, or inline formatting splits a quote.
const textSpan = (text, start) => text ? `<span data-reader-start="${start}">${e(text).replace(/\r/g, "&#13;")}</span>` : "";
export function readerInline(text, offset = 0) {
  let result = "", cursor = 0;
  const tokens = /`([^`\n]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  for (const match of text.matchAll(tokens)) {
    result += textSpan(text.slice(cursor, match.index), offset + cursor);
    if (match[1] !== undefined) result += `<code>${textSpan(match[1], offset + match.index + 1)}</code>`;
    else if (match[2] !== undefined) result += `<strong>${readerInline(match[2], offset + match.index + 2)}</strong>`;
    else result += `<a href="${e(match[4])}" target="_blank" rel="noopener noreferrer">${readerInline(match[3], offset + match.index + 1)}</a>`;
    cursor = match.index + match[0].length;
  }
  return result + textSpan(text.slice(cursor), offset + cursor);
}
export function readerMarkdown(raw) {
  let cursor = 0, paragraph = 0;
  return String(raw || "").split(/(?:\r?\n){2,}/).map(block => {
    if (!block) return "";
    const offset = raw.indexOf(block, cursor);
    cursor = offset + block.length;
    const attr = `data-quote-paragraph="${++paragraph}"`;
    if (block.startsWith("```")) {
      const prefix = block.match(/^```[^\n]*\n?/)?.[0] || "";
      const text = block.slice(prefix.length).replace(/```$/, "");
      return `<pre ${attr}><code>${textSpan(text, offset + prefix.length)}</code></pre>`;
    }
    const lines = [...block.matchAll(/[^\r\n]+/g)];
    if (/^#{1,6} /.test(block)) return lines.map(line => {
      const prefix = line[0].match(/^#+ /)?.[0] || "";
      const tag = prefix ? "h3" : "p";
      return `<${tag} ${attr}>${readerInline(line[0].slice(prefix.length), offset + line.index + prefix.length)}</${tag}>`;
    }).join("");
    if (/^>/.test(block)) return `<blockquote ${attr}>${lines.map(line => {
      const prefix = line[0].match(/^>\s?/)?.[0] || "";
      return readerInline(line[0].slice(prefix.length), offset + line.index + prefix.length);
    }).join("<br>")}</blockquote>`;
    if (/^(?:[-*]|\d+\.) /.test(block)) return `<ul ${attr}>${lines.map(line => {
      const prefix = line[0].match(/^(?:[-*]|\d+\.) /)?.[0] || "";
      return `<li>${readerInline(line[0].slice(prefix.length), offset + line.index + prefix.length)}</li>`;
    }).join("")}</ul>`;
    return `<p ${attr}>${readerInline(block, offset)}</p>`;
  }).join("");
}
const sourceName = kind => ({ ai_zh: "translated", ai_conclusion_zh: "conclusion", ai_points_zh: "points" })[kind] || "original";
const codePointOffset = (raw, utf16) => [...raw.slice(0, utf16)].length;

export class ReaderHighlights {
  constructor({ onQuote, onUpdate }) {
    this.onQuote = onQuote;
    this.onUpdate = onUpdate;
    this.popover = $("#selectionPopover");
    // Keep the selected range intact until an action consumes it, including touch taps.
    this.popover.addEventListener("pointerdown", event => event.preventDefault());
    $("#highlightSelectionBtn").onclick = event => busy(event.currentTarget, () => this.save());
    $("#quoteNoteBtn").onclick = () => this.quote("note");
    $("#quoteAgentBtn").onclick = () => this.quote("agent");
    $("#detailScrollArea").addEventListener("mouseup", () => this.select());
    $("#detailScrollArea").addEventListener("click", event => {
      const mark = event.target.closest("[data-highlight-id]");
      if (mark && !getSelection()?.toString().trim()) this.selectMark(mark);
    });
    $("#detailScrollArea").addEventListener("keydown", event => {
      if (["Enter", " "].includes(event.key) && event.target.matches("[data-highlight-id]")) {
        event.preventDefault(); this.selectMark(event.target);
      }
    });
    document.addEventListener("selectionchange", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.select(), 120);
    });
    document.addEventListener("pointerdown", event => {
      clearTimeout(this.timer);
      if (!event.target.closest("#selectionPopover")) this.hide();
    });
    document.addEventListener("keydown", event => { if (event.key === "Escape") this.hide(); });
    $("#detailScrollArea").addEventListener("scroll", () => {
      if (!this.popover.classList.contains("show")) return;
      const rect = this.anchor?.getBoundingClientRect();
      const viewport = $("#detailScrollArea").getBoundingClientRect();
      if (rect && rect.bottom >= viewport.top && rect.top <= viewport.bottom) this.position(rect);
      else this.hide();
    }, { passive: true });
    const reposition = () => {
      if (this.popover.classList.contains("show") && this.anchor) this.position(this.anchor.getBoundingClientRect());
    };
    window.addEventListener("resize", reposition);
    window.visualViewport?.addEventListener("resize", reposition);
    window.visualViewport?.addEventListener("scroll", reposition);
  }
  hide() { this.popover.classList.remove("show"); }
  mount(id, bodies, surfaces = {}) {
    this.hide();
    clearTimeout(this.timer);
    this.selection = null;
    this.itemId = id;
    this.bodies = bodies;
    this.surfaces = surfaces;
    this.paint();
  }
  paint() {
    for (const root of $$("[data-highlight-kind]", $("#detailScrollArea"))) {
      const kind = root.dataset.highlightKind, raw = this.bodies[kind] || "";
      const offsets = [0];
      for (const character of raw) offsets.push(offsets.at(-1) + character.length);
      const entries = (this.surfaces[kind]?.highlights || []).filter(row => row.status === "active").map(row => ({
        ...row, start: offsets[row.resolved_start_offset], end: offsets[row.resolved_end_offset],
      })).sort((a, b) => a.start - b.start || a.end - b.end);
      const spans = $$("[data-reader-start]", root);
      for (const span of spans) {
        const start = Number(span.dataset.readerStart), text = span.textContent, end = start + text.length;
        let cursor = start, html = "";
        for (const entry of entries) {
          const a = Math.max(cursor, entry.start);
          const b = Math.min(end, entry.end);
          if (a >= b) continue;
          html += e(text.slice(cursor - start, a - start));
          const color = ["yellow", "green", "blue", "pink"].includes(entry.color) ? entry.color : "yellow";
          html += `<mark class="reader-highlight highlight-${color}" data-highlight-id="${entry.id}" tabindex="0" role="button" aria-label="高亮文本，点击管理">${e(text.slice(a - start, b - start))}</mark>`;
          cursor = b;
        }
        span.innerHTML = (html + e(text.slice(cursor - start))).replace(/\r/g, "&#13;");
      }
    }
  }
  select() {
    const selection = getSelection();
    if (!selection?.rangeCount || selection.isCollapsed || !selection.toString().trim()) {
      if (!(this.anchor instanceof Element)) this.hide();
      return;
    }
    const range = selection.getRangeAt(0);
    const element = range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer.parentElement : range.startContainer;
    const root = element.closest("[data-highlight-kind]");
    if (!root || !root.contains(range.endContainer)) { this.hide(); return; }
    const kind = root.dataset.highlightKind, raw = this.bodies[kind];
    if (!raw) return;
    const portions = [];
    for (const span of $$("[data-reader-start]", root)) {
      if (!range.intersectsNode(span)) continue;
      const clip = range.cloneRange();
      let prefix = 0;
      if (span.contains(range.startContainer)) {
        const before = document.createRange(); before.selectNodeContents(span);
        before.setEnd(range.startContainer, range.startOffset);
        prefix = before.toString().length;
      } else clip.setStart(span, 0);
      if (!span.contains(range.endContainer)) clip.setEnd(span, span.childNodes.length);
      const text = clip.toString();
      if (text) portions.push({ start: Number(span.dataset.readerStart) + prefix, text, block: span.closest("[data-quote-paragraph]") });
    }
    if (!portions.length) return;
    const first = portions[0], last = portions.at(-1);
    let text = "", previous;
    for (const part of portions) { text += (previous && previous !== part.block ? "\n" : "") + part.text; previous = part.block; }
    const start = codePointOffset(raw, first.start), end = codePointOffset(raw, last.start + last.text.length);
    const existing = (this.surfaces[kind]?.highlights || []).find(row => row.status === "active" && row.resolved_start_offset <= start && row.resolved_end_offset >= end);
    this.selection = { itemId: this.itemId, kind, start, end, selected_text: raw.slice(first.start, last.start + last.text.length), existing: existing?.id,
      quote: { text: text.trim().slice(0, 6000), source: sourceName(kind), paragraph: Number(first.block?.dataset.quoteParagraph) || null } };
    this.anchor = range.cloneRange();
    this.show(range.getBoundingClientRect());
  }
  selectMark(mark) {
    const root = mark.closest("[data-highlight-kind]"), kind = root.dataset.highlightKind;
    const entry = (this.surfaces[kind]?.highlights || []).find(row => String(row.id) === mark.dataset.highlightId);
    if (!entry) return;
    const text = $$(`[data-highlight-id="${entry.id}"]`, root).map(node => node.textContent).join("");
    this.selection = { itemId: this.itemId, kind, existing: entry.id,
      quote: { text: text.slice(0, 6000), source: sourceName(kind), paragraph: Number(mark.closest("[data-quote-paragraph]")?.dataset.quoteParagraph) || null } };
    this.anchor = mark;
    this.show(mark.getBoundingClientRect());
  }
  show(rect) {
    $("#highlightSelectionBtn").textContent = this.selection.existing ? "取消高亮" : "高亮";
    $("#highlightSelectionBtn").disabled = !this.selection.existing && !this.surfaces[this.selection.kind]?.body_ready;
    this.popover.classList.add("show");
    this.position(rect);
  }
  position(rect) {
    const docked = singlePane.matches || matchMedia("(pointer: coarse)").matches;
    this.popover.classList.toggle("selection-toolbar", docked);
    this.popover.style.position = "fixed";
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, bottom = top + (viewport?.height || innerHeight);
    this.popover.style.maxWidth = `${Math.max(0, width - 16)}px`;
    const nav = $(".mobile-bottom-nav");
    const navTop = nav?.getClientRects().length ? nav.getBoundingClientRect().top : bottom;
    const x = docked ? left + (width - this.popover.offsetWidth) / 2 : rect.left;
    const y = docked ? Math.min(bottom, navTop) - this.popover.offsetHeight - 8 : rect.top - this.popover.offsetHeight - 8;
    this.popover.style.left = `${Math.max(left + 8, Math.min(left + width - this.popover.offsetWidth - 8, x))}px`;
    this.popover.style.top = `${Math.max(top + 8, Math.min(bottom - this.popover.offsetHeight - 8, y))}px`;
  }
  quote(target) {
    if (this.selection?.itemId === this.itemId) this.onQuote(target, this.selection.quote);
    getSelection()?.removeAllRanges();
    this.hide();
  }
  async save() {
    const selected = this.selection;
    if (!selected || selected.itemId !== this.itemId) return;
    const base = `/api/news/${encodeURIComponent(selected.itemId)}/highlights`;
    const data = await api(selected.existing ? `${base}/${selected.existing}` : base, selected.existing
      ? { method: "DELETE" }
      : { method: "POST", body: { body_kind: selected.kind, start_offset: selected.start, end_offset: selected.end, selected_text: selected.selected_text, color: "yellow" } });
    this.onUpdate(selected.itemId, data);
    if (selected.itemId !== this.itemId) return;
    this.surfaces = data.highlight_surfaces || {};
    getSelection()?.removeAllRanges();
    this.paint();
    this.hide();
  }
}
