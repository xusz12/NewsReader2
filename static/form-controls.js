import { escapeHTML as e } from "./ui.js";

export function selectField(label, path, value, options, placeholder = "沿用默认", { preserveUnknown = true } = {}) {
  const values = options.map((x) =>
    typeof x === "string" ? { value: x, label: x } : x,
  );
  if (preserveUnknown && value && !values.some((x) => x.value === value))
    values.push({ value, label: value, custom: true });
  const id = `setting-${path.replaceAll(".", "-")}`;
  return `<div class="field-item"><span class="field-label" id="${id}-label">${label}</span><select id="${id}" class="field-select" name="${path}" aria-labelledby="${id}-label">${placeholder === null ? "" : `<option value="">${placeholder}</option>`}${values.map((x) => `<option value="${e(x.value)}" ${x.custom ? 'data-custom-option="true"' : ""} ${x.value === value ? "selected" : ""}>${e(x.label || x.value)}</option>`).join("")}</select></div>`;
}

export function timePickers(label, attribute) {
  const fields = [["hour", 24, "小时", "时"], ["minute", 60, "分钟", "分"]];
  return `<div class="calendar-time">${fields.map(([part, count, suffix, unit]) => {
    const options = Array.from({length:count}, (_, index) => {
      const value = String(index).padStart(2, "0");
      return `<option value="${value}">${value} ${unit}</option>`;
    });
    return `<div class="field-item"><select class="field-select" ${attribute}="${part}" aria-label="${e(label + suffix)}">${options.join("")}</select></div>`;
  }).join("")}</div>`;
}
