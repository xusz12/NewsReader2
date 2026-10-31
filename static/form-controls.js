import { escapeHTML as e } from "./ui.js";

export function selectField(label, path, value, options, placeholder = "沿用默认") {
  const values = options.map((x) =>
    typeof x === "string" ? { value: x, label: x } : x,
  );
  if (value && !values.some((x) => x.value === value))
    values.push({ value, label: value, custom: true });
  const id = `setting-${path.replaceAll(".", "-")}`;
  return `<div class="field-item"><span class="field-label" id="${id}-label">${label}</span><select id="${id}" class="field-select" name="${path}" aria-labelledby="${id}-label">${placeholder === null ? "" : `<option value="">${placeholder}</option>`}${values.map((x) => `<option value="${e(x.value)}" ${x.custom ? 'data-custom-option="true"' : ""} ${x.value === value ? "selected" : ""}>${e(x.label || x.value)}</option>`).join("")}</select></div>`;
}
