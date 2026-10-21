// One themed confirmation at a time; all text supplied by callers stays plain text.
let dialog, pending;
function createDialog() {
  dialog = document.createElement('dialog');
  dialog.id = 'appConfirmDialog';
  dialog.className = 'catchup-dialog app-confirm';
  dialog.setAttribute('aria-labelledby', 'appConfirmTitle');
  dialog.setAttribute('aria-describedby', 'appConfirmMessage');
  dialog.innerHTML = `<div class="modal-header"><h2 class="modal-title" id="appConfirmTitle"></h2><button type="button" class="modal-close-btn" id="appConfirmClose" aria-label="取消并关闭">✕</button></div><div class="modal-body" id="appConfirmMessage"></div><div class="modal-footer"><button type="button" class="btn-secondary" id="appConfirmCancel" autofocus>取消</button><button type="button" class="btn-primary" id="appConfirmAccept"></button></div>`;
  document.body.append(dialog);
  const close = () => dialog.close();
  dialog.querySelector('#appConfirmClose').onclick = close;
  dialog.querySelector('#appConfirmCancel').onclick = close;
  dialog.querySelector('#appConfirmAccept').onclick = () => {
    if (!pending || pending.accepted) return;
    pending.accepted = true;
    dialog.querySelector('#appConfirmAccept').disabled = true;
    dialog.close();
  };
  dialog.addEventListener('cancel', () => { if (pending) pending.accepted = false; });
  dialog.addEventListener('close', () => {
    const current = pending;
    if (!current) return;
    pending = null;
    current.resolve(current.accepted);
    requestAnimationFrame(() => {
      const trigger = current.trigger?.isConnected ? current.trigger : current.triggerId && document.getElementById(current.triggerId);
      if (!pending && !dialog.open && trigger && !trigger.disabled && trigger.getClientRects().length && !trigger.closest('[inert]')) trigger.focus({ preventScroll: true });
    });
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close();
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const buttons = [...dialog.querySelectorAll('button:not(:disabled)')];
    const next = buttons[(buttons.indexOf(document.activeElement) + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length];
    event.preventDefault(); next.focus();
  });
}
export function confirmAction({ title, message, action, trigger: source }) {
  if (pending) return Promise.resolve(false);
  if (!dialog) createDialog();
  const trigger = typeof source === "string" ? document.querySelector(source) : source || document.activeElement;
  return new Promise(resolve => {
    pending = { resolve, trigger, triggerId: trigger?.id, accepted: false };
    dialog.querySelector('#appConfirmTitle').textContent = title;
    dialog.querySelector('#appConfirmMessage').textContent = message;
    const accept = dialog.querySelector('#appConfirmAccept');
    accept.textContent = action; accept.disabled = false;
    document.dispatchEvent(new CustomEvent('topbar:popover-open', { detail: dialog.id }));
    try { dialog.showModal(); dialog.querySelector('#appConfirmCancel').focus({ preventScroll: true }); }
    catch { pending = null; resolve(false); }
  });
}
