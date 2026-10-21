import { confirmAction } from "./confirm-dialog.js";
import { $, api, escapeHTML as e, errorText, toast } from "./ui.js";
import { IdeaMarkdownEditor } from "./vendor/idea-markdown-editor.js?v=0.0.15-md1";

const markdownExamples = [
  ["标题", "## 标题"],
  ["粗体", "**粗体文字**"],
  ["斜体", "*斜体文字*"],
  ["无序列表", "- 列表内容"],
  ["有序列表", "1. 列表内容"],
  ["引用", "> 引用内容"],
  ["行内代码", "`代码`"],
  ["链接", "[链接文字](https://example.com)"],
  ["代码块", "```\n代码内容\n```"],
];

async function copyMarkdown(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* Fall back for browsers without clipboard permission. */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.style.cssText = "position:fixed;left:-9999px;top:0";
  const previous = document.activeElement;
  document.body.appendChild(area);
  area.select();
  try { return !!document.execCommand?.("copy"); }
  catch { return false; }
  finally { area.remove(); previous?.focus({ preventScroll: true }); }
}

export const standaloneTitle = idea =>
  idea.title?.trim() || idea.note?.trim().split("\n")[0].replace(/^#+\s*/, "").slice(0, 120) || "独立想法";

// Each editor keeps its own draft and request state when the reader is reused.
export class StandaloneIdeas {
  constructor({ onSaved, onDeleted }) {
    this.onSaved = onSaved;
    this.onDeleted = onDeleted;
    this.drafts = new Map();
    this.markdownHelpOpen = true;
    this.element = $("#ideaDetailBody");
    this.element.addEventListener("focusin", () => this.updateHelpVisibility());
    this.element.addEventListener("focusout", () => {
      // Wait for the next control (including a reference example) to receive focus.
      requestAnimationFrame(() => this.updateHelpVisibility());
    });
    this.element.addEventListener("input", event => {
      const draft = this.current;
      if (!draft || event.target.id !== "ideaTitle") return;
      draft.title = event.target.value;
      draft.error = "";
      this.updateControls();
    });
    this.element.addEventListener("click", async event => {
      const button = event.target.closest("[data-markdown-example]");
      if (!button) return;
      const [label, snippet] = markdownExamples[Number(button.dataset.markdownExample)];
      const copied = await copyMarkdown(snippet);
      if (!copied && button.isConnected) {
        const range = document.createRange();
        range.selectNodeContents(button.querySelector("code"));
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      toast(copied ? `已复制${label}语法，可粘贴到编辑区` : "无法自动复制，请选中示例手动复制");
    });
    $("#saveIdeaBtn").onclick = () => this.save();
    $("#deleteIdeaBtn").onclick = () => this.remove();
    $("#detailPanelIdeas").addEventListener("keydown", event => {
      if (event.isComposing || this.editor?.view.composing || !this.current) return;
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        event.stopPropagation();
        this.save();
      }
    });
    this.render();
  }
  changed(draft) {
    return draft.title !== (draft.item.title || "") || draft.note !== (draft.item.note || "");
  }
  updateHelpVisibility() {
    const help = $(".standalone-markdown-help", this.element);
    if (!help) return;
    const focused = document.activeElement;
    help.hidden = !this.element.contains(focused) || !focused.closest(".standalone-fields, .standalone-markdown-help");
  }
  get dirty() {
    return [...this.drafts.values()].some(draft => draft.saving || this.changed(draft));
  }
  select(idea) {
    this.releaseEditor();
    if (!idea) {
      this.current = null;
      this.render();
      return;
    }
    let draft = this.drafts.get(idea.idea_id);
    if (!draft) {
      draft = { item: idea, title: idea.title || "", note: idea.note || "", error: "", saving: false, deleting: false };
      this.drafts.set(idea.idea_id, draft);
    } else if (!draft.saving && !this.changed(draft)) {
      // A refreshed server body must not reuse an older ProseMirror document.
      if (draft.note !== (idea.note || "")) draft.editorState = null;
      Object.assign(draft, { item: idea, title: idea.title || "", note: idea.note || "" });
    }
    this.current = draft;
    this.render();
  }
  updateControls() {
    const draft = this.current;
    const available = !!draft;
    $("#ideaToolbarTag").textContent = available ? "独立想法" : "想法与灵感";
    $("#ideaToolbarTime").textContent = !draft ? "" : draft.deleting ? "删除中…" : draft.saving ? "保存中…" : this.changed(draft) ? "尚未保存" : draft.item.standalone_id ? "已保存" : "新想法";
    $("#saveIdeaBtn").disabled = !available || draft.saving || draft.deleting || !draft.note.trim() || [...draft.note].length > 5000 || !this.changed(draft);
    $("#saveIdeaBtn").textContent = draft?.saving ? "保存中…" : draft?.item.standalone_id ? "保存修改" : "保存想法";
    $("#saveIdeaBtn").setAttribute("aria-busy", String(!!draft?.saving));
    $("#deleteIdeaBtn").hidden = !draft?.item.standalone_id;
    $("#deleteIdeaBtn").disabled = !available || draft.saving || draft.deleting;
    const count = $("#ideaWordCount");
    if (count && draft) count.textContent = `${[...(this.editor?.text ?? draft.note).replace(/\s/g, "")].length} 字`;
    const error = $("#ideaSaveError");
    if (error) { error.textContent = draft?.error || ""; error.hidden = !draft?.error; }
  }
  render() {
    this.releaseEditor();
    const draft = this.current;
    if (!draft) {
      this.element.innerHTML = '<div class="reader-empty"><h2>把阅读变成自己的想法</h2><p>选择一条想法，或点击“新想法”开始记录。</p></div>';
      this.updateControls();
      return;
    }
    const { item, title, note } = draft;
    this.element.innerHTML = `<article class="standalone-editor" aria-label="独立想法轻量编辑">
      <div class="standalone-meta"><time>${e(item.created_at?.slice(0, 10) || "新想法")}</time><span>个人笔记</span></div>
      <div class="standalone-fields">
        <label class="sr-only" for="ideaTitle">想法标题（选填）</label>
        <input id="ideaTitle" class="standalone-title" maxlength="200" autocomplete="off" placeholder="标题（选填）" value="${e(title)}" ${draft.deleting ? "disabled" : ""}>
        <div class="standalone-rule" aria-hidden="true"></div>
        <div id="ideaEditorMount"></div>
      </div>
      <p id="ideaSaveError" class="inline-error" role="alert" hidden></p>
      <footer class="standalone-footer"><span id="ideaWordCount"></span><span id="ideaEditorHint">Markdown 即时排版 · ⌘ / Ctrl + Enter 保存</span></footer>
      <div class="standalone-updated">${item.updated_at ? `更新于 ${e(item.updated_at)}` : "保存后会出现在想法列表中"}</div>
      <details class="standalone-markdown-help" hidden ${this.markdownHelpOpen ? "open" : ""}>
        <summary>Markdown 语法速查</summary>
        <div tabindex="-1">
        <p>点击示例复制，粘贴到编辑区即可排版。</p>
        <div class="markdown-example-grid">
          ${markdownExamples.map(([label, snippet], index) => `<button type="button" class="markdown-example" data-markdown-example="${index}" aria-label="复制${e(label)}语法" title="点击复制${e(label)}语法"><span>${e(label)}</span><code>${e(snippet)}</code></button>`).join("")}
        </div>
        <p class="markdown-help-tip">标题、列表和引用在行首输入，符号后加空格；代码块内按 Shift + Enter 回到普通段落。</p>
        </div>
      </details>
    </article>`;
    $(".standalone-markdown-help", this.element).ontoggle = event => {
      this.markdownHelpOpen = event.currentTarget.open;
    };
    this.editor = new IdeaMarkdownEditor($("#ideaEditorMount"), {
      markdown: note, state: draft.editorState, baseline: item.note || "", disabled: draft.deleting,
      onChange: (note, state) => {
        draft.note = note;
        draft.editorState = state;
        draft.error = [...note].length > 5000 ? "正文最多 5000 个字符（含 Markdown 标记）。" : "";
        this.updateControls();
      },
      onLimit: () => {
        draft.error = "正文最多 5000 个字符（含 Markdown 标记），此次输入未添加。";
        this.updateControls();
      },
    });
    this.updateControls();
  }
  releaseEditor() {
    if (!this.editor) return;
    if (this.current) this.current.editorState = this.editor.state;
    this.editor.destroy();
    this.editor = null;
  }
  async save() {
    const draft = this.current;
    if (!draft || this.editor?.view.composing || draft.saving || draft.deleting || !draft.note.trim() || [...draft.note].length > 5000 || !this.changed(draft)) return;
    const sent = { title: draft.title, note: draft.note };
    const oldKey = draft.item.idea_id;
    const id = draft.item.standalone_id;
    draft.saving = true;
    draft.error = "";
    this.updateControls();
    try {
      const result = await api(`/api/standalone-ideas${id ? `/${id}` : ""}`, { method: id ? "PATCH" : "POST", body: sent });
      Object.assign(draft.item, result.idea);
      // A response acknowledges only the text sent, never newer input.
      if (draft.title === sent.title) draft.title = result.idea.title;
      if (draft.note === sent.note) draft.note = result.idea.note;
      if (oldKey !== result.idea.idea_id) {
        this.drafts.delete(oldKey);
        this.drafts.set(result.idea.idea_id, draft);
      }
      this.onSaved(draft.item, oldKey);
      if (this.current === draft) {
        const title = $("#ideaTitle");
        if (title && title.value === sent.title) title.value = draft.title;
        this.editor.setBaseline(result.idea.note);
        $(".standalone-updated", this.element).textContent = `更新于 ${result.idea.updated_at}`;
      }
      toast("想法已保存");
    } catch (error) {
      draft.error = errorText(error);
    } finally {
      draft.saving = false;
      if (this.current === draft) this.updateControls();
    }
  }
  async remove() {
    const draft = this.current;
    if (!draft?.item.standalone_id || draft.saving || draft.deleting) return;
    const id = draft.item.standalone_id;
    const stamp = JSON.stringify([draft.title, draft.note, draft.item.updated_at]);
    if (!await confirmAction({ title: "删除独立想法", trigger: "#deleteIdeaBtn", message: `删除「${standaloneTitle(draft.item)}」？此操作会移除这条独立想法及其正文。`, action: "删除想法" })) return;
    if (this.current !== draft || draft.item.standalone_id !== id || draft.saving || draft.deleting || stamp !== JSON.stringify([draft.title, draft.note, draft.item.updated_at])) return toast("想法已变化，请重新操作");
    draft.deleting = true;
    draft.error = "";
    this.render();
    try {
      await api(`/api/standalone-ideas/${id}`, { method: "DELETE" });
      this.drafts.delete(draft.item.idea_id);
      this.onDeleted(draft.item);
      toast("想法已删除");
    } catch (error) {
      draft.error = errorText(error);
    } finally {
      draft.deleting = false;
      if (this.current === draft) this.render();
    }
  }
}
