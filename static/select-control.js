import { escapeHTML as e } from "./ui.js";

let nextMenuId = 0;

// Keep the native select as the form value, but render every visible interaction
// ourselves so the popup follows the application's theme on every platform.
export class SelectControls {
  constructor(root) {
    this.root = root;
    this.menu = document.createElement("div");
    this.menu.id = `select-menu-${++nextMenuId}`;
    this.menu.className = "select-menu";
    this.menu.setAttribute("role", "listbox");
    this.menu.hidden = true;
    document.body.append(this.menu);
    root.addEventListener("click", event => {
      const button = event.target.closest("[data-select-trigger]");
      if (!button || button.disabled) return;
      const select = button.previousElementSibling;
      if (this.button === button) this.close();
      else this.open(select, button);
    });
    root.addEventListener("keydown", event => this.keydown(event));
    root.addEventListener("focusin", event => {
      if (this.button && event.target !== this.button) this.close(false);
    });
    document.addEventListener("pointerdown", event => {
      if (this.button && !this.button.contains(event.target) && !this.menu.contains(event.target)) this.close(false);
    });
    document.addEventListener("scroll", event => {
      if (this.button && !this.menu.contains(event.target)) this.close(false);
    }, true);
    window.addEventListener("resize", () => this.close(false));
    window.visualViewport?.addEventListener("resize", () => this.close(false));
    this.menu.addEventListener("pointerdown", event => {
      if (event.pointerType !== "touch") event.preventDefault();
    });
    this.menu.addEventListener("click", event => {
      const option = event.target.closest("[role=option]");
      if (option && option.getAttribute("aria-disabled") !== "true") this.choose(Number(option.dataset.index));
    });
    this.menu.addEventListener("pointermove", event => {
      const option = event.target.closest("[role=option]");
      if (option && option.getAttribute("aria-disabled") !== "true") this.highlight(Number(option.dataset.index), false);
    });
  }
  refresh(scope = this.root) {
    if (this.button && !this.button.isConnected) this.close(false);
    for (const select of scope.querySelectorAll("select.field-select")) {
      let button = select.nextElementSibling;
      if (!button?.hasAttribute("data-select-trigger")) {
        const id = select.id;
        select.id = `${id}-native`;
        select.hidden = true;
        select.tabIndex = -1;
        select.setAttribute("aria-hidden", "true");
        button = document.createElement("button");
        button.type = "button";
        button.id = id;
        button.className = "field-select select-trigger";
        button.dataset.selectTrigger = select.name;
        button.setAttribute("role", "combobox");
        button.setAttribute("aria-haspopup", "listbox");
        button.setAttribute("aria-controls", this.menu.id);
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-labelledby", `${id}-label`);
        button.innerHTML = '<span class="select-value"></span><svg class="select-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
        select.after(button);
      }
      const label = select.selectedOptions[0]?.label || "请选择";
      button.querySelector(".select-value").textContent = label;
      button.title = label;
      button.disabled = select.disabled;
    }
  }
  open(select, button) {
    this.close(false);
    this.select = select;
    this.button = button;
    this.options = [...select.options].filter(option => !option.hidden);
    this.menu.setAttribute("aria-labelledby", button.getAttribute("aria-labelledby"));
    this.menu.innerHTML = this.options.map((option, index) =>
      `<div class="select-option" id="${this.menu.id}-${index}" role="option" data-index="${index}" aria-selected="${option.selected}" aria-disabled="${option.disabled}"><span>${e(option.label)}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg></div>`,
    ).join("");
    this.menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    const rect = button.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = viewport?.offsetTop || 0;
    const height = viewport?.height || window.innerHeight;
    const width = viewport?.width || window.innerWidth;
    const below = top + height - rect.bottom - 12;
    const above = rect.top - top - 12;
    const upward = below < Math.min(this.menu.scrollHeight, 280) && above > below;
    const maxHeight = Math.min(320, Math.max(48, upward ? above : below));
    const menuWidth = Math.min(Math.max(rect.width, 200), width - 16);
    Object.assign(this.menu.style, {
      width: `${menuWidth}px`,
      maxHeight: `${maxHeight}px`,
      left: `${Math.max(8, Math.min(rect.left, width - menuWidth - 8))}px`,
      top: `${upward ? rect.top - Math.min(this.menu.scrollHeight, maxHeight) - 4 : rect.bottom + 4}px`,
    });
    const selected = this.options.findIndex(option => option.selected && !option.disabled);
    this.highlight(selected >= 0 ? selected : this.options.findIndex(option => !option.disabled));
  }
  highlight(index, scroll = true) {
    if (!this.button || !Number.isInteger(index) || index < 0 || !this.options[index] || this.options[index].disabled) return;
    this.index = index;
    for (const option of this.menu.children) option.classList.toggle("focused", Number(option.dataset.index) === index);
    const node = this.menu.children[index];
    this.button.setAttribute("aria-activedescendant", node.id);
    if (scroll) {
      if (node.offsetTop < this.menu.scrollTop) this.menu.scrollTop = node.offsetTop;
      else if (node.offsetTop + node.offsetHeight > this.menu.scrollTop + this.menu.clientHeight)
        this.menu.scrollTop = node.offsetTop + node.offsetHeight - this.menu.clientHeight;
    }
  }
  choose(index) {
    const option = this.options?.[index];
    if (!option || option.disabled) return;
    const select = this.select, name = select.name;
    const changed = select.value !== option.value;
    select.value = option.value;
    this.close(false);
    if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
    this.refresh();
    this.root.querySelector(`[data-select-trigger="${CSS.escape(name)}"]`)?.focus({ preventScroll: true });
  }
  close(focus = true) {
    if (this.button) {
      this.button.setAttribute("aria-expanded", "false");
      this.button.removeAttribute("aria-activedescendant");
      if (focus && this.button.isConnected) this.button.focus({ preventScroll: true });
    }
    this.button = this.select = null;
    this.index = -1;
    this.query = "";
    this.menu.hidden = true;
  }
  keydown(event) {
    const button = event.target.closest("[data-select-trigger]");
    if (!button || button.disabled || event.isComposing || event.metaKey || event.ctrlKey) return;
    if (event.key === "Tab") { this.close(false); return; }
    if (event.key === "Escape") {
      if (this.button) { event.preventDefault(); event.stopPropagation(); this.close(); }
      return;
    }
    const movement = ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key);
    const commit = event.key === "Enter" || event.key === " ";
    const typing = event.key.length === 1 && !event.altKey;
    if (!movement && !commit && !typing) return;
    event.preventDefault();
    event.stopPropagation();
    if (!this.button) {
      this.open(button.previousElementSibling, button);
      if (commit || event.key.startsWith("Arrow")) return;
    } else if (commit) { this.choose(this.index); return; }
    const indices = this.options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0);
    if (movement) {
      const position = indices.indexOf(this.index);
      const next = event.key === "Home" ? 0 : event.key === "End" ? indices.length - 1 : Math.max(0, Math.min(indices.length - 1, position + (event.key === "ArrowDown" ? 1 : -1)));
      this.highlight(indices[next]);
    } else {
      this.query = Date.now() - (this.typedAt || 0) < 700 ? (this.query || "") + event.key : event.key;
      this.typedAt = Date.now();
      const query = this.query.toLocaleLowerCase();
      const next = indices.find(index => this.options[index].label.toLocaleLowerCase().startsWith(query));
      if (next !== undefined) this.highlight(next);
    }
  }
}
