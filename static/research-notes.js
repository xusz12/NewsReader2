import { $, api, escapeHTML as e, icon, markdown, errorText, toast } from "./ui.js";

const quoteLabel = (source, paragraph) =>
  source === "conclusion" ? "引用精读结论" : source === "points" ? `引用精读要点${paragraph ? `第 ${paragraph} 条` : ""}` :
    `引用${source === "translated" ? "译文" : "原文"}${paragraph ? `第 ${paragraph} 段` : ""}`;

export class ResearchNotes {
  constructor({ onUpdate }) {
    this.onUpdate = onUpdate;
    this.drafts = new Map();
    this.publishing = new Set();
    this.deleting = new Set();
    this.element = document.createElement("section");
    this.element.className = "digest-thoughts research-notes";
    this.element.setAttribute("aria-label", "研读想法");
    this.element.addEventListener("input", event => {
      if (event.target.id !== "researchNoteInput") return;
      const draft = this.draft();
      draft.text = event.target.value;
      draft.error = "";
      $(".research-note-error", this.element)?.remove();
      $("#publishResearchNoteBtn", this.element).disabled = !draft.text.trim() || this.publishing.has(this.itemId);
    });
    this.element.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      const action = button.dataset.noteAction;
      if (action === "write") this.toggle();
      if (action === "cancel") this.cancel();
      if (action === "publish") this.publish();
      if (action === "remove-quote") { this.draft().quote = null; this.render(); $("#researchNoteInput", this.element)?.focus(); }
      if (action === "delete") this.remove(button.dataset.deleteNote);
    });
    this.element.addEventListener("keydown", event => {
      if (event.isComposing || !this.draft().open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        this.cancel();
      } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        event.stopPropagation();
        this.publish();
      }
    });
  }
  draft() {
    if (!this.drafts.has(this.itemId)) this.drafts.set(this.itemId, { text: "", quote: null, open: false, error: "" });
    return this.drafts.get(this.itemId);
  }
  get dirty() {
    return this.publishing.size > 0 || [...this.drafts.values()].some(draft => draft.text.trim() || draft.quote);
  }
  captureFocus() {
    const input = $("#researchNoteInput", this.element);
    if (input && document.activeElement === input)
      return { id: this.itemId, type: "input", start: input.selectionStart, end: input.selectionEnd, scroll: input.scrollTop };
    const button = document.activeElement;
    return this.element.contains(button) && button.dataset.noteAction
      ? { id: this.itemId, type: "button", action: button.dataset.noteAction, noteId: button.dataset.deleteNote }
      : null;
  }
  mount(container, id, entries, focus) {
    this.itemId = id;
    this.entries = entries;
    if (!container) return;
    container.append(this.element);
    this.render(focus);
  }
  update(id, result) {
    if (this.itemId !== id) return;
    this.entries = result.notes || [];
    this.render();
  }
  open(quote = null, { scrollToEditor = true } = {}) {
    if (!this.itemId || !this.element.isConnected) return;
    const draft = this.draft();
    draft.open = true;
    if (quote) draft.quote = quote;
    this.render();
    const input = $("#researchNoteInput", this.element);
    input.focus({ preventScroll: true });
    if (scrollToEditor) $("#researchNoteComposer", this.element).scrollIntoView({ block: "nearest", behavior: "instant" });
  }
  toggle() {
    if (!this.itemId || !this.element.isConnected) return;
    if (this.draft().open) this.cancel();
    else this.open(null, { scrollToEditor: false });
  }
  cancel() {
    this.draft().open = false;
    this.render();
    this.writeButton()?.focus({ preventScroll: true });
  }
  writeButton() {
    const button = $("[data-note-action=write]", this.element);
    return button?.getClientRects().length ? button : $("#writeArticleThoughtBtn");
  }
  render(focus = this.captureFocus()) {
    const draft = this.draft(), pending = this.publishing.has(this.itemId);
    $("#writeArticleThoughtBtn")?.setAttribute("aria-expanded", String(draft.open));
    const empty = !this.entries.length;
    const compact = empty && !draft.open;
    this.element.classList.toggle("notes-empty", empty);
    const notesOnly = this.element.closest(".notes-only");
    if (notesOnly) notesOnly.hidden = compact;
    this.element.innerHTML = `
      <header class="thoughts-heading">
        ${empty ? "" : `<h3>${icon("lightbulb")}<span>研读想法 <span class="thoughts-heading-en">(Research Notes)</span></span><span class="thoughts-count">${this.entries.length} 条</span></h3>`}
        <button class="thoughts-write-btn" type="button" data-note-action="write" aria-expanded="${draft.open}" aria-controls="researchNoteComposer"><span aria-hidden="true">${draft.open ? "−" : "＋"}</span> 写想法</button>
      </header>
      <ol class="thoughts-timeline">${this.entries.map(entry => this.card(entry)).join("")}</ol>
      <div class="research-note-composer" id="researchNoteComposer" ${draft.open ? "" : "hidden"}>
        <span class="thought-avatar" aria-hidden="true">我</span>
        <div class="research-note-form">
          ${draft.quote ? `<div class="thought-quote draft-quote"><div><strong>${e(quoteLabel(draft.quote.source, draft.quote.paragraph))}</strong><p>${e(draft.quote.text)}</p></div><button class="note-delete-btn" type="button" data-note-action="remove-quote" aria-label="移除引用" title="移除引用">×</button></div>` : ""}
          <textarea id="researchNoteInput" rows="3" maxlength="50000" aria-label="写下研读想法" placeholder="写下当前时间节点的研读想法…" aria-describedby="researchNoteHint">${e(draft.text)}</textarea>
          ${draft.error ? `<p class="research-note-error" role="alert">${e(draft.error)}</p>` : ""}
          <div class="research-note-actions"><span id="researchNoteHint">⌘ / Ctrl + Enter 发布 · Esc 取消</span><button class="thoughts-write-btn" type="button" data-note-action="cancel">取消</button><button class="thoughts-publish-btn" id="publishResearchNoteBtn" type="button" data-note-action="publish" ${pending || !draft.text.trim() ? "disabled" : ""} aria-busy="${pending}">${pending ? "发布中…" : "发布想法"}</button></div>
        </div>
      </div>`;
    if (focus?.id === this.itemId && focus.type === "input" && draft.open) {
      const input = $("#researchNoteInput", this.element);
      input.focus({ preventScroll: true });
      input.setSelectionRange(focus.start, focus.end);
      input.scrollTop = focus.scroll;
    } else if (focus?.id === this.itemId && focus.type === "button") {
      const selector = focus.noteId ? `[data-delete-note="${CSS.escape(focus.noteId)}"]` : `[data-note-action="${focus.action}"]`;
      const target = $(selector, this.element);
      (target?.getClientRects().length && !target.disabled ? target : this.writeButton())?.focus({ preventScroll: true });
    }
  }
  card(entry) {
    const agent = entry.author === "agent";
    const stamp = entry.created_at || "";
    const shortDate = stamp.replace("T", " ").slice(5, 16);
    return `<li class="thought-timeline-entry">
      <span class="thought-avatar ${agent ? "agent-avatar" : ""}" aria-hidden="true">${agent ? icon("favorite") : "我"}</span>
      <article class="saved-note ${agent ? "agent-note" : ""}" data-note-id="${e(entry.id)}">
        <header class="saved-note-header"><div class="thought-author"><strong>${agent ? "新闻研究 Agent" : "我（研究员）"}</strong>${agent ? `<span class="thought-agent-badge">${icon("favorite")} AI 研读归档</span>` : ""}</div><div class="saved-note-meta"><time datetime="${e(stamp.replace(" ", "T"))}" title="${e(stamp)}">${e(shortDate)}</time><button class="note-delete-btn" type="button" data-note-action="delete" data-delete-note="${e(entry.id)}" aria-label="删除想法" title="删除想法" ${this.deleting.has(entry.id) ? "disabled" : ""}>${icon("trash")}</button></div></header>
        <div class="saved-note-content">${markdown(agent ? entry.archive_summary || entry.note : entry.note)}</div>
        ${entry.quote_text ? `<blockquote class="thought-quote"><strong>${e(quoteLabel(entry.quote_source, entry.quote_paragraph))}</strong><p>${e(entry.quote_text)}</p></blockquote>` : ""}
      </article>
    </li>`;
  }
  async publish() {
    const id = this.itemId, draft = this.draft();
    if (!draft.text.trim() || this.publishing.has(id)) return;
    const sent = { note: draft.text, quote: draft.quote ? { ...draft.quote } : null };
    this.publishing.add(id);
    draft.error = "";
    this.render();
    try {
      const result = await api(`/api/news/${encodeURIComponent(id)}/notes`, { method: "POST", body: sent });
      if (draft.text === sent.note && JSON.stringify(draft.quote) === JSON.stringify(sent.quote)) {
        Object.assign(draft, { text: "", quote: null, open: false });
      }
      this.onUpdate(id, result);
      if (this.itemId === id && !draft.open) this.writeButton()?.focus({ preventScroll: true });
      toast("想法已发布");
    } catch (error) {
      draft.error = errorText(error);
    } finally {
      this.publishing.delete(id);
      if (this.itemId === id) this.render();
    }
  }
  async remove(noteId) {
    if (this.deleting.has(noteId)) return;
    const id = this.itemId;
    this.deleting.add(noteId);
    this.render();
    try {
      const result = await api(`/api/news/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" });
      this.onUpdate(id, result);
      if (this.itemId === id && !this.captureFocus()) this.writeButton()?.focus({ preventScroll: true });
    } catch (error) {
      toast(errorText(error));
    } finally {
      this.deleting.delete(noteId);
      if (this.itemId === id) this.render();
    }
  }
}
