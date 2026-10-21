// Local calendar arithmetic: never round-trip date-only values through UTC.
export function localDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  if (!match || Number(match[1]) < 1) return null;
  const date = new Date(0);
  date.setHours(12, 0, 0, 0);
  date.setFullYear(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.getFullYear() === Number(match[1]) && date.getMonth() === Number(match[2]) - 1 && date.getDate() === Number(match[3]) ? date : null;
}
export const dateKey = date => `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const moveDay = (day, amount) => { const date = localDate(day); date.setDate(date.getDate() + amount); return dateKey(date); };
const monthKey = day => day.slice(0, 7);

export class DatePicker {
  constructor({ input, button, panel, error, getToday, onSelect, onInvalid }) {
    Object.assign(this, { input, button, panel, error, getToday, onSelect, onInvalid });
    this.inputRevision = 0;
    input.addEventListener('input', () => { this.inputDirty = true; this.inputRevision++; });
    input.onchange = () => { if (this.inputDirty) { this.inputDirty = false; this.commit(input.value); } };
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); this.inputDirty = false; this.commit(input.value); }
      if (event.key === 'ArrowDown') { event.preventDefault(); this.show(); }
    });
    button.onclick = () => this.open ? this.close(true) : this.show();
    panel.addEventListener('click', event => {
      const day = event.target.closest('[data-calendar-day]');
      if (day && !day.disabled) this.commit(day.dataset.calendarDay, true);
      const move = event.target.closest('[data-calendar-month]');
      if (move && !move.disabled) this.changeMonth(Number(move.dataset.calendarMonth));
      if (event.target.closest('[data-calendar-today]')) this.commit(getToday(), true);
    });
    panel.addEventListener('keydown', event => {
      const day = event.target.closest('[data-calendar-day]');
      if (!day) return;
      const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (event.key in offsets) {
        event.preventDefault();
        const target = moveDay(day.dataset.calendarDay, offsets[event.key]);
        if (!localDate(target) || target > getToday()) return;
        this.focusedDay = target; this.month = monthKey(target); this.render(); this.focusDay();
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); this.commit(day.dataset.calendarDay, true);
      }
    });
    document.addEventListener('pointerdown', event => {
      if (this.open && !panel.contains(event.target) && !button.contains(event.target) && event.target !== input) this.close();
    });
  }
  get open() { return !this.panel.hidden; }
  setValue(value) {
    const message = !localDate(value) ? '请输入有效日期（YYYY-MM-DD）。' : value > this.getToday() ? '日期不能晚于今天。' : '';
    this.error.textContent = message;
    this.input.setAttribute('aria-invalid', String(!!message));
    if (message) return false;
    this.input.value = value; this.selectedDay = value; this.inputDirty = false;
    if (this.open) this.render();
    return true;
  }
  commit(value, close = false) {
    if (localDate(value) && value <= this.getToday() && this.open) this.month = monthKey(value);
    if (!this.setValue(value)) { this.onInvalid?.(); return; }
    if (close) this.close(true);
    this.onSelect(value);
  }
  show() {
    const day = localDate(this.input.value) && this.input.value <= this.getToday() ? this.input.value : this.selectedDay || this.getToday();
    this.month = monthKey(day); this.focusedDay = day;
    this.panel.hidden = false; this.button.setAttribute('aria-expanded', 'true');
    this.render(); this.focusDay();
  }
  close(focus = false) {
    this.panel.hidden = true; this.button.setAttribute('aria-expanded', 'false');
    if (focus) this.button.focus({ preventScroll: true });
  }
  focusDay() { this.panel.querySelector(`[data-calendar-day="${this.focusedDay}"]`)?.focus({ preventScroll: true }); }
  changeMonth(offset) {
    const date = localDate(this.month + '-01'); date.setMonth(date.getMonth() + offset);
    const month = monthKey(dateKey(date));
    if (month > monthKey(this.getToday()) || date.getFullYear() < 1) return;
    this.month = month;
    this.focusedDay = month === monthKey(this.selectedDay || '') ? this.selectedDay : month + '-01';
    this.render();
    this.panel.querySelector(`[data-calendar-month="${offset}"]`)?.focus({ preventScroll: true });
  }
  render() {
    if (!this.open) return;
    const first = localDate(this.month + '-01'), today = this.getToday();
    const focused = this.panel.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.hasAttribute('data-calendar-day') ? 'data-calendar-day' : focused?.hasAttribute('data-calendar-month') ? 'data-calendar-month' : focused?.hasAttribute('data-calendar-today') ? 'data-calendar-today' : '';
    const focusValue = focused?.getAttribute(focusKey);
    const offset = (first.getDay() + 6) % 7;
    this.panel.innerHTML = `<div class="calendar-heading"><button type="button" data-calendar-month="-1" aria-label="上个月" ${this.month === '0001-01' ? 'disabled' : ''}>‹</button><strong aria-live="polite">${first.getFullYear()}年${first.getMonth() + 1}月</strong><button type="button" data-calendar-month="1" aria-label="下个月" ${this.month >= monthKey(today) ? 'disabled' : ''}>›</button></div><div class="calendar-week" aria-hidden="true">${['一','二','三','四','五','六','日'].map(day => `<span>${day}</span>`).join('')}</div><div class="calendar-grid" role="grid" aria-label="${first.getFullYear()}年${first.getMonth() + 1}月日期">${Array.from({ length: 42 }, (_, index) => {
      const date = new Date(first); date.setDate(1 - offset + index);
      const day = dateKey(date), current = day === today, selected = day === this.selectedDay;
      return `${index % 7 === 0 ? '<div role="row" class="calendar-row">' : ''}<button type="button" role="gridcell" data-calendar-day="${day}" aria-label="${day}" aria-selected="${selected}" ${current ? 'aria-current="date"' : ''} class="${monthKey(day) !== this.month ? 'calendar-other-month' : ''}" tabindex="${day === this.focusedDay ? '0' : '-1'}" ${day > today || !localDate(day) ? 'disabled' : ''}>${Number(day.slice(8))}</button>${index % 7 === 6 ? '</div>' : ''}`;
    }).join('')}</div><button type="button" class="calendar-today" data-calendar-today>今天 · ${today}</button>`;
    if (focusKey) this.panel.querySelector(`[${focusKey}="${focusValue}"]`)?.focus({ preventScroll: true });
    const box = this.panel.getBoundingClientRect(), viewport = window.visualViewport;
    this.panel.style.maxHeight = `${Math.max(80, (viewport?.offsetTop || 0) + (viewport?.height || innerHeight) - box.top - 12)}px`;
  }
}
