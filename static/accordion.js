import { escapeHTML as e } from "./ui.js";

// Shared, multi-open accordion. Expansion belongs to the view, not settings data.
export class Accordion {
  constructor(root) {
    this.root = root;
    this.expanded = new Set();
    root.addEventListener("click", event => {
      const trigger = event.target.closest("[data-accordion-trigger]");
      if (!trigger || !root.contains(trigger)) return;
      const panel = root.querySelector(`#${CSS.escape(trigger.getAttribute("aria-controls"))}`);
      if (!panel) return;
      const open = trigger.getAttribute("aria-expanded") !== "true";
      trigger.setAttribute("aria-expanded", String(open));
      panel.hidden = !open;
      if (open) this.expanded.add(trigger.id);
      else this.expanded.delete(trigger.id);
    });
    root.addEventListener("keydown", event => {
      const trigger = event.target.closest("[data-accordion-trigger]");
      if (!trigger || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      // Native button activation must take priority over custom global shortcuts.
      if (["Enter", " "].includes(event.key)) {
        event.stopPropagation();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      const triggers = [...trigger.closest("[data-accordion]").querySelectorAll("[data-accordion-trigger]")];
      const index = triggers.indexOf(trigger);
      const next = event.key === "Home" ? 0 : event.key === "End" ? triggers.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + triggers.length) % triggers.length;
      event.preventDefault();
      triggers[next].focus();
    });
  }

  html(id, items, { headingLevel = 3 } = {}) {
    const heading = `h${Math.min(6, Math.max(2, headingLevel))}`;
    return `<div class="accordion" data-accordion>${items.map(item => {
      const trigger = `${id}-${item.id}`, panel = `${trigger}-content`, open = this.expanded.has(trigger);
      return `<div class="accordion-item">
        <${heading} class="accordion-heading"><button class="accordion-trigger" id="${e(trigger)}" data-accordion-trigger type="button" aria-expanded="${open}" aria-controls="${e(panel)}">
          <span>${e(item.question)}</span><svg class="accordion-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button></${heading}>
        <div class="accordion-content" id="${e(panel)}" aria-labelledby="${e(trigger)}" ${open ? "" : "hidden"}>${item.paragraphs.map(paragraph => `<p>${e(paragraph)}</p>`).join("")}</div>
      </div>`;
    }).join("")}</div>`;
  }
}
