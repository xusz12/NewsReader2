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

export function localDateTime(value) {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})$/.exec(value || "");
  return match && localDate(match[1]) && Number(match[2]) < 24 && Number(match[3]) < 60
    ? `${match[1]}T${match[2]}:${match[3]}` : "";
}

function dayGrid({month, today, max = today, selectedDay, focusedDay, rangeEnd = ""}) {
  const first = localDate(month + '-01'), offset = (first.getDay() + 6) % 7;
  return `<div class="calendar-week" aria-hidden="true">${['一','二','三','四','五','六','日'].map(day => `<span>${day}</span>`).join('')}</div><div class="calendar-grid" role="grid" aria-label="${first.getFullYear()}年${first.getMonth() + 1}月日期">${Array.from({ length: 42 }, (_, index) => {
      const date = new Date(first); date.setDate(1 - offset + index);
      const day = dateKey(date), current = day === today, selected = day === selectedDay;
      const inRange = rangeEnd && day > selectedDay && day <= rangeEnd;
      return `${index % 7 === 0 ? '<div role="row" class="calendar-row">' : ''}<button type="button" role="gridcell" data-calendar-day="${day}" aria-label="${day}" aria-selected="${selected}" ${current ? 'aria-current="date"' : ''} class="${monthKey(day) !== month ? 'calendar-other-month' : ''} ${inRange ? 'calendar-in-range' : ''}" tabindex="${day === focusedDay ? '0' : '-1'}" ${day > max || !localDate(day) ? 'disabled' : ''}>${Number(day.slice(8))}</button>${index % 7 === 6 ? '</div>' : ''}`;
    }).join('')}</div>`;
}

// Embedded calendar for forms. Uses the same day grid and theme as DatePicker;
// a plain text input allows direct date/year entry without a second picker.
export class Calendar {
  constructor({panel, input, getToday, getMax, rangeDays = 1, mode = "date", onSelect}) {
    Object.assign(this, {panel, input, getToday, getMax, rangeDays, mode, onSelect});
    this.sync();
    panel.addEventListener("click", event => {
      const day = event.target.closest("[data-calendar-day]");
      const month = event.target.closest("[data-calendar-value]");
      const move = event.target.closest("[data-calendar-month]");
      if (day && !day.disabled) this.choose(day.dataset.calendarDay);
      if (month && !month.disabled) this.choose(month.dataset.calendarValue);
      if (move && !move.disabled) this.move(Number(move.dataset.calendarMonth));
    });
    panel.addEventListener("keydown", event => {
      const cell = event.target.closest("[role=gridcell]");
      if (!cell) return;
      const columns = mode === "month" ? 3 : 7;
      const offsets = {ArrowLeft:-1, ArrowRight:1, ArrowUp:-columns, ArrowDown:columns};
      if (!(event.key in offsets)) return;
      event.preventDefault(); event.stopPropagation();
      const value = mode === "month" ? cell.dataset.calendarValue : cell.dataset.calendarDay;
      const date = localDate(mode === "month" ? value+"-01" : value);
      if (mode === "month") date.setMonth(date.getMonth()+offsets[event.key]);
      else date.setDate(date.getDate()+offsets[event.key]);
      const next = mode === "month" ? dateKey(date).slice(0,7) : dateKey(date);
      if (!localDate(dateKey(date)) || next > getMax()) return;
      this.cursor = next; this.month = next.slice(0,7); this.render(); this.focus();
    });
  }
  sync() {
    const value = this.input.value, valid = localDate(this.mode === "month" ? value+"-01" : value);
    if (valid && value <= this.getMax()) this.cursor = value;
    else this.cursor ||= this.getMax();
    this.month = this.cursor.slice(0,7);
    this.render();
  }
  choose(value) {
    this.input.value = value; this.cursor = value; this.month = value.slice(0,7);
    this.onSelect(value); this.render(); this.focus();
  }
  focus() {
    this.panel.querySelector(`[${this.mode === "month" ? "data-calendar-value" : "data-calendar-day"}="${this.cursor}"]`)?.focus({preventScroll:true});
  }
  move(offset) {
    const date = localDate(this.month+"-01");
    date.setMonth(date.getMonth()+offset*(this.mode === "month" ? 12 : 1));
    this.month = dateKey(date).slice(0,7);
    this.cursor = this.mode === "month" ? this.month : this.month+"-01";
    this.render();
    this.panel.querySelector(`[data-calendar-month="${offset}"]`)?.focus({preventScroll:true});
  }
  render() {
    const year = this.month.slice(0,4), monthly = this.mode === "month", max = this.getMax();
    const atMax = monthly ? year >= max.slice(0,4) : this.month >= max.slice(0,7);
    const atMin = monthly ? year === "0001" : this.month === "0001-01";
    const unit = monthly ? "年" : "个月";
    const heading = monthly ? `${Number(year)}年` : `${Number(year)}年${Number(this.month.slice(5))}月`;
    const selectedDay = this.input.value;
    this.panel.innerHTML = `<div class="calendar-heading"><button type="button" data-calendar-month="-1" aria-label="上一${unit}" ${atMin?'disabled':''}>‹</button><strong aria-live="polite">${heading}</strong><button type="button" data-calendar-month="1" aria-label="下一${unit}" ${atMax?'disabled':''}>›</button></div>` + (monthly
      ? `<div class="calendar-month-grid" role="grid" aria-label="${Number(year)}年月份">${Array.from({length:12}, (_,index) => {
        const value = `${year}-${String(index+1).padStart(2,"0")}`;
        return `${index%3===0?'<div class="calendar-row" role="row">':''}<button type="button" role="gridcell" data-calendar-value="${value}" aria-label="${value}" aria-selected="${value === selectedDay}" tabindex="${value === this.cursor?'0':'-1'}" ${value>max?'disabled':''}>${index+1}月</button>${index%3===2?'</div>':''}`;
      }).join("")}</div>`
      : dayGrid({month:this.month, today:this.getToday(), max, selectedDay, focusedDay:this.cursor,
        rangeEnd:this.rangeDays > 1 && localDate(selectedDay) ? moveDay(selectedDay,this.rangeDays-1) : ""}));
  }
}

// One shared calendar and styled time selectors edit the focused endpoint.
// Plain text inputs keep manual entry available without opening native pickers.
export class DateTimePicker {
  constructor({panel, inputs, selects, getMax, onSelect}) {
    Object.assign(this, {panel, inputs, selects, getMax, onSelect});
    this.input = inputs[0];
    this.dateInput = document.createElement("input");
    panel.innerHTML = '<div data-datetime-calendar></div><div class="calendar-time" data-datetime-time></div>';
    this.calendar = new Calendar({panel:panel.querySelector("[data-datetime-calendar]"), input:this.dateInput,
      getToday:() => getMax().slice(0,10), getMax:() => getMax().slice(0,10),
      onSelect:date => this.choose(date+"T"+this.value().slice(11))});
    for (const input of inputs) {
      input.addEventListener("focus", () => this.sync(input));
      input.addEventListener("input", () => this.sync(input));
    }
    panel.addEventListener("change", event => {
      const part = event.target.dataset.datetimePart;
      if (!part) return;
      const value = this.value();
      this.choose(value.slice(0,11)+(part === "hour" ? event.target.value : value.slice(11,13))+":"+
        (part === "minute" ? event.target.value : value.slice(14,16)));
    });
    this.sync();
  }
  value() { return localDateTime(this.input.value) || this.getMax(); }
  sync(input = this.input) {
    this.input = input;
    this.panel.setAttribute("role","group");
    this.panel.setAttribute("aria-label", "选择"+(input.closest("label")?.querySelector(".field-label")?.textContent || "时间"));
    for (const endpoint of this.inputs) endpoint.dataset.calendarActive = String(endpoint === input);
    this.dateInput.value = this.value().slice(0,10);
    this.calendar.sync(); this.renderTime();
  }
  choose(value) {
    this.input.value = (value > this.getMax() ? this.getMax() : value).replace("T"," ");
    this.onSelect(this.input);
    this.dateInput.value = this.value().slice(0,10);
    this.renderTime();
  }
  renderTime() {
    const value = this.value(), max = this.getMax(), today = value.slice(0,10) === max.slice(0,10);
    const time = this.panel.querySelector("[data-datetime-time]");
    // Keep the active picker mounted so choosing an option preserves keyboard focus.
    if (!time.firstElementChild) time.innerHTML = ["hour","minute"].map((part,index) =>
      `<label class="field-item"><span class="field-label">${index ? "分钟" : "小时"}</span><select class="field-select" data-datetime-part="${part}" aria-label="${index ? "分钟" : "小时"}"></select></label>`).join("");
    for (const [part,count,start] of [["hour",24,11],["minute",60,14]]) {
      const select = time.querySelector(`[data-datetime-part="${part}"]`);
      select.innerHTML = Array.from({length:count}, (_,index) => {
        const option = String(index).padStart(2,"0");
        const disabled = today && (part === "hour" ? option > max.slice(11,13) : value.slice(11,13) === max.slice(11,13) && option > max.slice(14,16));
        return `<option value="${option}" ${option === value.slice(start,start+2)?"selected":""} ${disabled?"disabled":""}>${option}</option>`;
      }).join("");
    }
    this.selects.refresh(time);
  }
}

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
    document.addEventListener('scroll', event => {
      if (event.target.contains?.(this.input)) this.position();
    }, true);
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
    this.panel.innerHTML = `<div class="calendar-heading"><button type="button" data-calendar-month="-1" aria-label="上个月" ${this.month === '0001-01' ? 'disabled' : ''}>‹</button><strong aria-live="polite">${first.getFullYear()}年${first.getMonth() + 1}月</strong><button type="button" data-calendar-month="1" aria-label="下个月" ${this.month >= monthKey(today) ? 'disabled' : ''}>›</button></div>${dayGrid({month:this.month, today, selectedDay:this.selectedDay, focusedDay:this.focusedDay})}<button type="button" class="calendar-today" data-calendar-today>今天 · ${today}</button>`;
    if (focusKey) this.panel.querySelector(`[${focusKey}="${focusValue}"]`)?.focus({ preventScroll: true });
    this.position();
  }
  position() {
    if (!this.open) return;
    // Fixed positioning keeps the calendar outside the content-height panel's clipping area.
    const anchor = this.input.parentElement.getBoundingClientRect(), viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    const belowTop = Math.max(top + 12, Math.min(anchor.bottom + 6, top + height - 12));
    const aboveBottom = Math.max(top + 12, Math.min(anchor.top - 6, top + height - 12));
    const below = top + height - 12 - belowTop, above = aboveBottom - top - 12;
    const naturalHeight = this.panel.scrollHeight + 2;
    const openBelow = below >= naturalHeight || below >= above;
    const available = Math.max(0, openBelow ? below : above);
    this.panel.style.maxHeight = `${available}px`;
    this.panel.style.left = `${Math.max(left + 12, Math.min(anchor.left, left + width - this.panel.offsetWidth - 12))}px`;
    this.panel.style.top = `${openBelow ? belowTop : aboveBottom - Math.min(naturalHeight, available)}px`;
  }
}
