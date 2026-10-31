// Small non-modal form popover, shared across workbench views.
import { dismissOnOutsidePress } from "./outside-dismiss.js?v=report-layout-1";

export class AnchoredPopover {
  constructor({panel, trigger, onClose, owns = () => false}) {
    Object.assign(this, {panel, trigger, onClose, owns});
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", panel.id);
    trigger.setAttribute("aria-expanded", "false");
    dismissOnOutsidePress({panel, trigger, priority:2, contains:owns,
      isOpen:() => this.open, dismiss:() => this.close(true)});
    document.addEventListener("topbar:popover-open", event => {
      if (event.detail !== panel.id) this.close();
    });
    // A nested select handles its own Escape before this bubbles to document.
    document.addEventListener("keydown", event => {
      if (!this.open || event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation(); this.close(true);
      }
    });
    document.addEventListener("focusin", event => {
      if (this.open && !panel.contains(event.target) && event.target !== trigger && !owns(event.target)) this.close();
    });
    window.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("resize", () => this.position());
    window.visualViewport?.addEventListener("scroll", () => this.position());
    new ResizeObserver(() => this.position()).observe(panel);
  }
  get open() { return !this.panel.hidden; }
  show() {
    document.dispatchEvent(new CustomEvent("topbar:popover-open", {detail:this.panel.id}));
    this.panel.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.position();
  }
  close(focus = false) {
    if (!this.open) return;
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.onClose?.();
    if (focus && this.trigger.isConnected && this.trigger.getClientRects().length) this.trigger.focus({preventScroll:true});
  }
  position() {
    if (!this.open) return;
    const anchor = this.trigger.getBoundingClientRect(), viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const gap = 8, margin = 12;
    const below = Math.max(0, top + height - margin - anchor.bottom - gap);
    const above = Math.max(0, anchor.top - gap - top - margin);
    const natural = this.panel.scrollHeight + 2;
    const openBelow = below >= Math.min(natural, 480) || below >= above;
    const available = openBelow ? below : above;
    this.panel.style.maxHeight = `${available}px`;
    this.panel.style.maxWidth = `${Math.max(0,width-margin*2)}px`;
    this.panel.style.left = `${Math.max(left+margin, Math.min(anchor.right-this.panel.offsetWidth,left+width-this.panel.offsetWidth-margin))}px`;
    this.panel.style.top = `${openBelow ? anchor.bottom+gap : anchor.top-gap-Math.min(natural,available)}px`;
  }
}
