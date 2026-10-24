import { $, $$, escapeHTML as e, toast } from "./ui.js";

export const shortcutActions = [
  { id: "previous", label: "上一条", keys: ["w", "ArrowUp"] },
  { id: "next", label: "下一条", keys: ["s", "ArrowDown"] },
  { id: "important", label: "标记重要", keys: ["a", "ArrowLeft"] },
  { id: "read_later", label: "稍后再看", keys: ["d", "ArrowRight"] },
  { id: "favorite", label: "收藏", keys: ["f"] },
  { id: "read", label: "标为已读／未读", keys: ["m"] },
  { id: "agent", label: "展开／收起 Agent", keys: ["q"] },
  { id: "search", label: "搜索", keys: ["/"] },
  { id: "settings", label: "打开／关闭设置", keys: [","] },
  { id: "help", label: "快捷键速查", keys: ["?"] },
];
const storageKey = "newsreader_workbench_shortcuts";
const names = { ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Space: "空格", Plus: "+" };
const aliases = { "↑": "ArrowUp", "↓": "ArrowDown", "←": "ArrowLeft", "→": "ArrowRight", "空格": "Space", " ": "Space", "+": "Plus" };
const namedKeys = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Plus", "Home", "End", "PageUp", "PageDown", "Backspace", "Delete", ...Array.from({ length: 12 }, (_, i) => `F${i + 1}`)];
const modifiers = ["Ctrl", "Alt", "Shift", "Meta"];
const shifted = { "/": "?", "1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&", "8": "*", "9": "(", "0": ")", "-": "_", "=": "Plus", "[": "{", "]": "}", "\\": "|", ";": ":", "'": '"', ",": "<", ".": ">" };

export const defaultShortcuts = () => ({ show_bar: true, bindings: Object.fromEntries(shortcutActions.map(action => [action.id, [...action.keys]])) });
export const displayKey = key => key.split("+").map(part => part === "Plus" && key.includes("+") ? "Plus" : names[part] || part).join("+");

function canonicalKey(value) {
  if (typeof value !== "string" || !value.trim()) throw new Error("请按下快捷键，例如 g 或 Ctrl+Shift+g");
  const parts = value.trim() === "+" ? ["Plus"] : value.trim().split("+").map(part => part.trim());
  let key = parts.pop();
  key = aliases[key] || namedKeys.find(name => name.toLowerCase() === key.toLowerCase()) || key.toLowerCase();
  if (["escape", "esc", "tab", "enter"].includes(key)) throw new Error("Esc、Tab 和 Enter 保留用于关闭、焦点移动和确认");
  if (!namedKeys.includes(key) && !/^[a-z0-9`~!@#$%^&*()_\-=\[\]{};:'",.<>/?\\|]$/.test(key)) throw new Error("无法识别按键，请按下单个按键或组合键");
  const mods = parts.map(part => modifiers.find(mod => mod.toLowerCase() === part.toLowerCase()) || ({ cmd: "Meta", command: "Meta", control: "Ctrl", option: "Alt" }[part.toLowerCase()]));
  if (mods.some(mod => !mod) || new Set(mods).size !== mods.length) throw new Error("组合键格式无效，例如 Ctrl+Shift+g");
  if (mods.includes("Shift") && shifted[key]) key = shifted[key];
  // Shift is already represented by printable punctuation such as ? and +.
  const retainShift = /^[a-z]$/.test(key) || (namedKeys.includes(key) && key !== "Plus");
  return [...modifiers.filter(mod => mods.includes(mod) && (mod !== "Shift" || retainShift)), key].join("+");
}

export function shortcutKeyFromEvent(event) {
  if (event.isComposing || event.key === "Process" || event.getModifierState?.("AltGraph") || ["Control", "Alt", "Shift", "Meta"].includes(event.key)) return null;
  const base = aliases[event.key] || event.key;
  return canonicalKey([...modifiers.filter(mod => event[`${mod === "Ctrl" ? "ctrl" : mod.toLowerCase()}Key`]), base].join("+"));
}

export function normalizeShortcuts(value) {
  const defaults = defaultShortcuts(), used = new Map(), bindings = {};
  for (const action of shortcutActions) {
    const raw = value?.bindings?.[action.id] ?? defaults.bindings[action.id];
    try {
      if (!Array.isArray(raw) || raw.length > 2) throw new Error("每个操作最多设置两个快捷键，每个输入框录入一个");
      bindings[action.id] = raw.map(key => key === "" ? "" : canonicalKey(key));
      // Preserve an empty first slot while keeping unused trailing slots out of storage.
      while (bindings[action.id].at(-1) === "") bindings[action.id].pop();
      for (const key of bindings[action.id]) {
        if (!key) continue;
        if (used.has(key)) throw new Error(`${displayKey(key)} 已用于「${used.get(key)}」`);
        used.set(key, action.label);
      }
    } catch (error) { error.action = action.id; throw error; }
  }
  return { show_bar: value?.show_bar !== false, bindings };
}

export class KeyboardShortcuts {
  constructor() {
    this.value = this.read();
    this.render();
    $("#closeKeyboardBarBtn").onclick = () => {
      try {
        this.save({ ...this.snapshot(), show_bar: false });
        $("#leftNavSettingsBtn").focus({ preventScroll: true });
      } catch (error) { toast(error.message); }
    };
    window.addEventListener("storage", event => {
      if (event.key !== storageKey && event.key !== null) return;
      this.value = this.read();
      this.render();
      document.dispatchEvent(new Event("shortcuts:changed"));
    });
  }
  read() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      // Older versions allowed four alternatives; retain the first two per action.
      for (const keys of Object.values(saved?.bindings || {})) if (Array.isArray(keys)) keys.splice(2);
      return normalizeShortcuts(saved);
    }
    catch { return defaultShortcuts(); }
  }
  snapshot() { return structuredClone(this.value); }
  save(value) {
    const normalized = normalizeShortcuts(value);
    try { localStorage.setItem(storageKey, JSON.stringify(normalized)); }
    catch { throw new Error("浏览器无法保存快捷键偏好，请检查浏览器存储设置"); }
    this.value = normalized;
    this.render();
    document.dispatchEvent(new Event("shortcuts:changed"));
    return this.snapshot();
  }
  action(event) {
    if (event.defaultPrevented || event.isComposing || event.key === "Process" || event.getModifierState?.("AltGraph")) return null;
    if (event.target?.closest('input,textarea,select,[role="combobox"],[contenteditable]:not([contenteditable="false"])')) return null;
    let key;
    try { key = shortcutKeyFromEvent(event); } catch { return null; }
    return shortcutActions.find(action => this.value.bindings[action.id].includes(key))?.id || null;
  }
  keysHTML(id, limit) {
    const keys = this.value.bindings[id].filter(Boolean);
    return keys.length ? keys.slice(0, limit).map(key => `<kbd>${e(displayKey(key))}</kbd>`).join(" / ") : '<span class="muted">未设置</span>';
  }
  render() {
    $("#keyboardBar").hidden = !this.value.show_bar;
    $("#keyboardBarKeys").innerHTML = shortcutActions.filter(action => action.id !== "help" && this.value.bindings[action.id].length)
      .map(action => `<span class="kbd-item">${this.keysHTML(action.id, 1)} ${e(action.label)}</span>`).join("");
    $("#keyboardHelpKey").innerHTML = this.keysHTML("help", 1);
    $("#shortcutHelpRows").innerHTML = shortcutActions.map(action => `<div class="shortcut-help-row"><span>${e(action.label)}</span><span>${this.keysHTML(action.id)}</span></div>`).join("")
      + '<div class="shortcut-help-row"><span>关闭／返回</span><kbd>Esc</kbd></div><div class="shortcut-help-row"><span>打开聚焦条目</span><kbd>Enter</kbd></div>';
    $$("[data-shortcut-hint]").forEach(node => {
      const key = this.value.bindings[node.dataset.shortcutHint]?.find(Boolean);
      node.textContent = key ? displayKey(key) : "";
      node.hidden = !key;
    });
  }
}

export const keyboard = new KeyboardShortcuts();
