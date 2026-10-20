import { Schema, Slice } from "prosemirror-model";
import { EditorState, TextSelection } from "prosemirror-state";
import { EditorView } from "prosemirror-view";
import { schema as commonmarkSchema, MarkdownParser, defaultMarkdownParser, defaultMarkdownSerializer } from "prosemirror-markdown";
import { baseKeymap, chainCommands, exitCode, setBlockType, toggleMark } from "prosemirror-commands";
import { keymap } from "prosemirror-keymap";
import { history, undo, redo } from "prosemirror-history";
import { InputRule, inputRules, undoInputRule, wrappingInputRule, textblockTypeInputRule } from "prosemirror-inputrules";
import { splitListItem, liftListItem, sinkListItem } from "prosemirror-schema-list";

// Pasted HTML and existing Markdown share the same restricted URL policy.
const safeLink = value => /^(https?:\/\/|mailto:)/i.test(value || "") && !/[\u0000-\u0020]/.test(value);
const safeImage = value => /^https?:\/\//i.test(value || "") && !/[\u0000-\u0020]/.test(value);
const link = {
  ...commonmarkSchema.spec.marks.get("link"),
  parseDOM: [{ tag: "a[href]", getAttrs: node => safeLink(node.getAttribute("href")) ? {
    href: node.getAttribute("href"), title: node.getAttribute("title"),
  } : false }],
  toDOM: mark => ["a", {
    href: safeLink(mark.attrs.href) ? mark.attrs.href : null,
    title: mark.attrs.title, rel: "noopener noreferrer", target: "_blank",
  }, 0],
};
const image = {
  ...commonmarkSchema.spec.nodes.get("image"),
  parseDOM: [{ tag: "img[src]", getAttrs: node => safeImage(node.getAttribute("src")) ? {
    src: node.getAttribute("src"), alt: node.getAttribute("alt"), title: node.getAttribute("title"),
  } : false }],
  toDOM: node => safeImage(node.attrs.src)
    ? ["img", { ...node.attrs, loading: "lazy", referrerpolicy: "no-referrer" }]
    : ["span", { class: "markdown-image-fallback" }, node.attrs.alt || "图片地址不可用"],
};
const schema = new Schema({
  nodes: commonmarkSchema.spec.nodes.update("image", image),
  marks: commonmarkSchema.spec.marks.update("link", link),
});
const parser = new MarkdownParser(schema, defaultMarkdownParser.tokenizer, defaultMarkdownParser.tokens);
const serialize = doc => defaultMarkdownSerializer.serialize(doc);
const size = text => [...text].length;
const editorBaseKeys = { ...baseKeymap };
delete editorBaseKeys["Mod-Enter"]; // Reserved for saving the idea, including inside code blocks.

function inlineRule(pattern) {
  return new InputRule(pattern, (state, match, start, end) => {
    const inline = parser.parse(match[0]).firstChild;
    if (!inline || inline.type !== schema.nodes.paragraph ||
        !inline.content.content.some(node => node.marks.length || node.type === schema.nodes.image)) return null;
    const tr = state.tr.replaceWith(start, end, inline.content);
    tr.setSelection(TextSelection.near(tr.doc.resolve(start + inline.content.size)));
    return tr.setStoredMarks([]);
  }, { inCodeMark: false });
}
function rules() {
  return inputRules({ rules: [
    wrappingInputRule(/^\s*>\s$/, schema.nodes.blockquote),
    wrappingInputRule(/^\s*([-+*])\s$/, schema.nodes.bullet_list, { tight: true }),
    wrappingInputRule(/^(\d+)\.\s$/, schema.nodes.ordered_list, match => ({ order: +match[1], tight: true }),
      (match, node) => node.childCount + node.attrs.order === +match[1]),
    textblockTypeInputRule(/^(#{1,6})\s$/, schema.nodes.heading, match => ({ level: match[1].length })),
    textblockTypeInputRule(/^```([\w-]*)\s$/, schema.nodes.code_block, match => ({ params: match[1] })),
    inlineRule(/(?<![\\*])\*\*(\S(?:[^*\n]*?\S)?)\*\*$/),
    inlineRule(/(?<![\\_\w])__(\S(?:[^_\n]*?\S)?)__$/),
    inlineRule(/(?<![\\*])\*([^*\s](?:[^*\n]*?[^*\s])?)\*$/),
    inlineRule(/(?<![\\_\w])_([^_\s](?:[^_\n]*?[^_\s])?)_$/),
    inlineRule(/(?<![\\`])`([^`\n]+)`$/),
    inlineRule(/(?<!\\)!?\[[^\]\n]*\]\([^\s)]+(?: "[^"]*")?\)$/),
  ] });
}
function startFence(state, dispatch) {
  const { $from, empty } = state.selection;
  if (!empty || $from.parent.type !== schema.nodes.paragraph || $from.parentOffset !== $from.parent.content.size) return false;
  const match = /^```([\w-]*)$/.exec($from.parent.textContent);
  if (!match) return false;
  if (dispatch) dispatch(state.tr.delete($from.start(), $from.end()).setBlockType($from.start(), $from.start(), schema.nodes.code_block, { params: match[1] }));
  return true;
}
function commands() {
  const bindings = {
    "Mod-z": undo, "Mod-Shift-z": redo, "Mod-y": redo,
    "Mod-b": toggleMark(schema.marks.strong), "Mod-i": toggleMark(schema.marks.em),
    "Mod-`": toggleMark(schema.marks.code),
    Backspace: undoInputRule,
    Enter: chainCommands(startFence, splitListItem(schema.nodes.list_item)),
    Tab: sinkListItem(schema.nodes.list_item), "Shift-Tab": liftListItem(schema.nodes.list_item),
    "Shift-Enter": chainCommands(exitCode, (state, dispatch) => {
      if (dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.hard_break.create()).scrollIntoView());
      return true;
    }),
    "Mod-Alt-0": setBlockType(schema.nodes.paragraph),
  };
  for (let level = 1; level <= 6; level++) bindings[`Mod-Alt-${level}`] = setBlockType(schema.nodes.heading, { level });
  return keymap(bindings);
}

export class IdeaMarkdownEditor {
  constructor(place, { markdown, state, baseline = markdown, disabled = false, onChange, onLimit }) {
    this.setBaseline(baseline);
    this.view = new EditorView(place, {
      state: state || EditorState.create({
        doc: parser.parse(markdown),
        plugins: [rules(), commands(), keymap(editorBaseKeys), history()],
      }),
      editable: () => !disabled,
      attributes: state => ({
        id: "ideaNote", role: "textbox", "aria-label": "编辑想法", "aria-multiline": "true",
        "aria-describedby": "ideaEditorHint", "data-placeholder": "写下此刻的想法…",
        class: `standalone-body${state.doc.childCount === 1 && state.doc.firstChild.type === schema.nodes.paragraph && !state.doc.firstChild.content.size ? " is-empty" : ""}`,
      }),
      dispatchTransaction: transaction => {
        const next = this.view.state.apply(transaction);
        const changed = !next.doc.eq(this.view.state.doc);
        const markdown = changed ? this.markdown(next.doc) : null;
        if (changed && size(markdown) > 5000 && size(markdown) >= size(this.markdown(this.view.state.doc))) {
          onLimit();
          this.view.updateState(this.view.state);
          return;
        }
        this.view.updateState(next);
        if (changed) onChange(markdown, next);
      },
      clipboardTextSerializer: slice => serialize(schema.topNodeType.create(null, slice.content)),
      handlePaste: (view, event) => {
        const text = event.clipboardData?.getData("text/plain");
        if (text === undefined || text === null) return false;
        // HTML is parsed by the schema; raw HTML in Markdown remains literal text.
        if (event.clipboardData.getData("text/html") || view.state.selection.$from.parent.type.spec.code) return false;
        view.dispatch(view.state.tr.replaceSelection(Slice.maxOpen(parser.parse(text).content)).scrollIntoView());
        return true;
      },
      handleClickOn: (_view, _pos, node, _nodePos, event) => {
        // Links stay editable on a normal click.
        const link = node.marks.find(mark => mark.type === schema.marks.link);
        if (link && (event.metaKey || event.ctrlKey) && safeLink(link.attrs.href)) {
          window.open(link.attrs.href, "_blank", "noopener,noreferrer");
          return true;
        }
        return false;
      },
    });
  }
  setBaseline(markdown) {
    this.baseline = markdown;
    this.baselineDoc = parser.parse(markdown);
  }
  markdown(doc = this.view.state.doc) {
    return doc.eq(this.baselineDoc) ? this.baseline : serialize(doc);
  }
  get state() { return this.view.state; }
  get text() { return this.view.state.doc.textContent; }
  destroy() { this.view.destroy(); }
}
