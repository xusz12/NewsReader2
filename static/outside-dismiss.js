// Consume the whole outside press, including the click sent after pointerup.
// Higher panels own dismissal when more than one panel is visible.
const panels = [];
let blockedPointer = null;
const clearPress = () => { blockedPointer = null; };
const consume = event => {
  event.preventDefault();
  event.stopImmediatePropagation();
};

export function dismissOnOutsidePress({ panel, trigger, isOpen, dismiss, priority = 0, contains = () => false }) {
  panels.push({ panel, trigger, isOpen, dismiss, priority, contains });
  panels.sort((a, b) => b.priority - a.priority);
}

document.addEventListener('pointerdown', event => {
  if (!event.isPrimary) return;
  // A new gesture must never inherit suppression from a cancelled/no-click one.
  clearPress();
  // Native confirmations and the shortcut modal sit above these panels.
  if (document.querySelector('dialog[open], #shortcutsModal.show')) return;
  const active = panels.find(entry => entry.isOpen());
  if (!active || active.panel.contains(event.target) || active.trigger?.contains(event.target) || active.contains(event.target)) return;
  blockedPointer = event.pointerId;
  consume(event);
  active.dismiss();
}, true);

for (const type of ['pointerup', 'mousedown', 'mouseup', 'click', 'auxclick', 'contextmenu']) {
  document.addEventListener(type, event => {
    if (blockedPointer === null) return;
    if ('pointerId' in event && event.pointerId >= 0 && event.pointerId !== blockedPointer) return;
    // Keyboard and programmatic clicks have no preceding pointer gesture.
    if (type === 'click' && event.detail === 0 && !event.pointerType) return;
    consume(event);
    if (type === 'click' || type === 'auxclick') clearPress();
  }, true);
}
document.addEventListener('pointercancel', clearPress, true);
document.addEventListener('keydown', clearPress, true);
window.addEventListener('blur', clearPress);
