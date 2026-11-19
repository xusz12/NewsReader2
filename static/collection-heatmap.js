import { dateKey, localDate } from "./date-picker.js?v=feature-pickers-1";

export function heatStatus(count) {
  if (count == null) return "empty";
  if (count === 0) return "success";
  return count <= 3 ? "warning" : "failed";
}

const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
const offsets = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 };

function calendarHTML(start, end) {
  const weeks = [], months = [];
  const last = localDate(end);
  for (const date = localDate(start); date <= last; date.setDate(date.getDate() + 1)) {
    const weekday = date.getDay();
    if (!weeks.length || weekday === 0) weeks.push([]);
    const week = weeks.length - 1;
    if (!months.length || date.getDate() === 1) {
      if (months.at(-1)?.week === week) months.pop();
      months.push({ week, label: `${date.getMonth() + 1}月` });
    }
    weeks[week][weekday] = dateKey(date);
  }
  const headings = months.map(({ week, label }, index) => {
    const span = (months[index + 1]?.week ?? weeks.length) - week;
    return `<th scope="colgroup" colspan="${span}">${span >= 3 ? label : ""}</th>`;
  }).join("");
  const rows = weekdays.map((label, row) => `<tr><th scope="row" aria-label="星期${label}">${row % 2 ? `周${label}` : ""}</th>
    ${weeks.map(week => week[row]
      ? `<td><button type="button" class="upstream-heat-tile" data-heat-day="${week[row]}" aria-pressed="false" tabindex="-1"></button></td>`
      : '<td></td>').join("")}</tr>`).join("");
  return `<table class="upstream-heat-grid" role="grid" aria-labelledby="upstreamHeatmapTitle" aria-describedby="upstreamHeatmapKeys"><thead><tr><td></td>${headings}</tr></thead><tbody>${rows}</tbody></table>`;
}

export class CollectionHeatmap {
  constructor(root, selectDay) {
    this.root = root;
    this.scroller = root.querySelector(".upstream-heat-scroll");
    this.point = root.querySelector("[data-heat-point]");
    this.cells = new Map();
    this.scroller.onclick = event => {
      const day = event.target.dataset.heatDay;
      if (!day) return;
      event.target.focus({ preventScroll: true });
      selectDay(day);
    };
    this.scroller.onkeydown = event => {
      const day = event.target.dataset.heatDay;
      if (!day || !(event.key in offsets)) return;
      event.preventDefault();
      event.stopPropagation();
      const date = localDate(day);
      if ((event.key === "ArrowUp" && date.getDay() === 0) || (event.key === "ArrowDown" && date.getDay() === 6)) return;
      date.setDate(date.getDate() + offsets[event.key]);
      this.cells.get(dateKey(date))?.focus();
    };
    this.scroller.addEventListener("focusin", event => {
      if (!event.target.dataset.heatDay) return;
      this.pointedDay = "";
      this.setTabStop(event.target);
      this.reveal(event.target);
      this.showPoint();
    });
    this.scroller.addEventListener("focusout", event => {
      if (!this.scroller.contains(event.relatedTarget)) {
        this.setTabStop(this.cells.get(this.selected));
        this.showPoint();
      }
    });
    this.scroller.onpointerover = event => {
      this.pointedDay = event.target.dataset.heatDay;
      this.showPoint();
    };
    this.scroller.onpointerleave = () => {
      this.pointedDay = "";
      this.showPoint();
    };
    new ResizeObserver(() => this.reveal(this.tabStop)).observe(this.scroller);
  }

  setTabStop(cell) {
    if (!cell || cell === this.tabStop) return;
    if (this.tabStop) this.tabStop.tabIndex = -1;
    cell.tabIndex = 0;
    this.tabStop = cell;
  }

  reveal(cell) {
    if (!cell || !this.scroller.clientWidth) return;
    const viewport = this.scroller.getBoundingClientRect();
    const axis = this.scroller.querySelector("tbody th").getBoundingClientRect();
    const rect = cell.getBoundingClientRect();
    if (rect.right > viewport.right - 2) this.scroller.scrollLeft += rect.right - viewport.right + 2;
    else if (rect.left < axis.right + 2) this.scroller.scrollLeft += rect.left - axis.right - 2;
  }

  showPoint() {
    const cell = this.cells.get(this.pointedDay) || this.cells.get(document.activeElement?.dataset.heatDay) || this.cells.get(this.selected);
    if (this.point.textContent !== (cell?.title || "")) this.point.textContent = cell?.title || "";
  }

  update(range, selected, today) {
    this.root.hidden = !range?.errors_by_day;
    if (this.root.hidden) return;
    const { start, end, errors_by_day } = range;
    const focusedDay = document.activeElement?.dataset.heatDay;
    const newRange = this.start !== start || this.end !== end;
    if (newRange) {
      this.pointedDay = "";
      this.start = start;
      this.end = end;
      this.scroller.innerHTML = calendarHTML(start, end);
      this.cells = new Map([...this.scroller.querySelectorAll("[data-heat-day]")].map(cell => [cell.dataset.heatDay, cell]));
      this.root.querySelector("[data-heat-range]").textContent = `${start.slice(0, 7)} – ${end.slice(0, 7)}`;
    }
    for (const [day, cell] of this.cells) {
      const count = errors_by_day[day];
      const title = `${day}${day === today ? "（今天）" : ""} · ${count == null ? "无采集记录" : `${count} 次报错`}`;
      if (cell.title === title) continue;
      cell.title = title;
      cell.setAttribute("aria-label", title);
      cell.dataset.status = heatStatus(count);
    }
    if (newRange || selected !== this.selected) {
      this.cells.get(this.selected)?.setAttribute("aria-pressed", "false");
      this.selected = selected;
      const cell = this.cells.get(selected);
      cell?.setAttribute("aria-pressed", "true");
      const focusCell = this.cells.get(focusedDay) || cell;
      this.setTabStop(focusCell);
      if (newRange && focusedDay) focusCell?.focus({ preventScroll: true });
      this.reveal(focusCell);
    }
    this.showPoint();
  }
}
