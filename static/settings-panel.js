import { confirmAction } from "./confirm-dialog.js";
import { $, $$, api, escapeHTML as e, errorText, toast, busy, singlePane, drawerNavigation } from "./ui.js";

import { UpdatePanel } from "./update-panel.js?v=0.3.0";
import { visibilityText } from "./summary-panel.js?v=0.3.0";
import { classificationStatus } from "./classification-progress.js?v=0.3.2";
import { ScheduleObserver } from "./schedule-observer.js?v=reports-manual-1";
import { keyboard, shortcutActions, defaultShortcuts, normalizeShortcuts, displayKey, shortcutKeyFromEvent } from "./keyboard-shortcuts.js";

const clone = (value) => structuredClone(value);
const get = (object, path) =>
  path.split(".").reduce((v, key) => v?.[key], object);
function set(object, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  let target = object;
  for (const key of keys) target = target[key] ??= {};
  target[last] = value;
}
const field = (label, path, value, extra = "") =>
  `<label class="field-item"><span class="field-label">${label}</span><input class="field-input" name="${path}" value="${e(value)}" ${extra}></label>`;
function select(label, path, value, options, placeholder = "沿用默认") {
  const values = options.map((x) =>
    typeof x === "string" ? { value: x, label: x } : x,
  );
  if (value && !values.some((x) => x.value === value))
    values.push({ value, label: value, custom: true });
  const id = `setting-${path.replaceAll(".", "-")}`;
  return `<div class="field-item"><span class="field-label" id="${id}-label">${label}</span><select id="${id}" class="field-select" name="${path}" aria-labelledby="${id}-label">${placeholder === null ? "" : `<option value="">${placeholder}</option>`}${values.map((x) => `<option value="${e(x.value)}" ${x.custom ? 'data-custom-option="true"' : ""} ${x.value === value ? "selected" : ""}>${e(x.label || x.value)}</option>`).join("")}</select></div>`;
}
export class SettingsPanel {
  constructor({ onSaved, onSources, onOpen, onViewChange, selects, onClassificationRefresh, onClassificationRetry, onReportOpen }) {
    this.onOpen = onOpen;
    this.onViewChange = onViewChange;
    this.onSaved = onSaved;
    this.onSources = onSources;
    this.onClassificationRefresh = onClassificationRefresh;
    this.onClassificationRetry = onClassificationRetry;
    this.open = false;
    this.dirty = false;
    this.section = "system";
    this.schedules = new ScheduleObserver({
      isVisible: () => this.open && this.section === "schedules" && (!singlePane.matches || $("#feedView").classList.contains("settings-detail-active")),
      onReportOpen,
    });
    this.connectionSequence = 0;
    this.catalogSequence = 0;
    this.catalogs = new Map();
    this.apiProvider = "deepseek";
    this.apiStates = new Map();
    this.selects = selects;
    this.updater = new UpdatePanel({ hasUnsavedChanges: () => this.dirty || [...this.apiStates.values()].some(s => !!s.secretDraft), contextStamp: () => JSON.stringify([this.draft, [...this.apiStates], this.open]) });
    $("#closeSettingsBtn").onclick = () => this.close();
    $("#settingsResetBtn").onclick = () => this.resetDraft();
    $("#settingsCategoryBackBtn").onclick = () => this.showCategories();
    document.addEventListener("shortcuts:changed", () => {
      if (!this.saved) return;
      const next = keyboard.snapshot(), saved = this.saved.keyboard || next;
      this.draft.keyboard ??= clone(saved);
      if (this.draft.keyboard.show_bar === saved.show_bar) this.draft.keyboard.show_bar = next.show_bar;
      for (const action of shortcutActions) {
        if (JSON.stringify(this.draft.keyboard.bindings[action.id]) === JSON.stringify(saved.bindings[action.id]))
          this.draft.keyboard.bindings[action.id] = clone(next.bindings[action.id]);
      }
      this.saved.keyboard = next;
      this.renderShortcuts();
      this.status("有未保存的更改");
    });
    $("#settingsSaveBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.save());
    $$("[data-settings-tab]").forEach(
      (button) =>
        (button.onclick = () => this.selectSection(button.dataset.settingsTab)),
    );
    $("#settingsContent").addEventListener("input", (event) =>
      this.updateDraft(event),
    );
    $("#settingsContent").addEventListener("change", (event) =>
      this.updateDraft(event),
    );
    $("#settingsContent").addEventListener("click", (event) =>
      this.click(event),
    );
  }
  async load(render = true) {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        const data = await api("/api/settings");
        data.keyboard = keyboard.snapshot();
        this.saved = data;
        this.rememberModelCatalogs(data);
        if (!this.dirty) this.draft = clone(data);
        this.onSources?.();
        if (render && this.open) this.render();
        return data;
      } catch (error) {
        this.status(errorText(error), true);
        if (this.open && !this.saved)
          $("#settingsContent").innerHTML =
            '<div class="list-message">设置读取失败<button class="btn-secondary" id="retrySettingsBtn" type="button">重试</button></div>';
      } finally {
        this.loading = null;
      }
    })();
    return this.loading;
  }
  async toggle() {
    if (this.open) return this.close();
    this.lastFocus = document.activeElement;
    this.onOpen?.();
    this.open = true;
    const view = $("#feedView");
    this.wasReader = view.classList.contains("mobile-reader-active");
    this.previousPanels = $$(
      "#feedView > .feed-column.active, #feedView > .detail-panel.active",
    );
    this.previousNavigation = $$("[data-collection].active");
    this.previousPanels.forEach((panel) => panel.classList.remove("active"));
    this.previousNavigation.forEach((button) => {
      button.classList.remove("active");
      button.removeAttribute("aria-current");
    });
    view.classList.remove("mobile-reader-active", "settings-detail-active");
    view.classList.add("settings-active");
    ["#feedColSettings", "#detailPanelSettings"].forEach((id) =>
      $(id).classList.add("active"),
    );
    $("#leftNavSettingsBtn").classList.add("active");
    $("#leftNavSettingsBtn").setAttribute("aria-current", "page");
    this.onViewChange?.();
    this.selectSection(this.section, { reveal: false });
    $("#settingsListTitle").focus();
    if (this.saved) this.render();
    else {
      $("#settingsContent").innerHTML =
        '<div class="list-message" role="status">正在读取配置…</div>';
      this.status("正在读取配置…");
      await this.load();
      this.render();
    }
  }
  close({ restoreFocus = true } = {}) {
    if (!this.open) return;
    this.open = false;
    this.schedules.stop();
    this.selects.close(false);
    $("#feedView").classList.remove(
      "settings-active",
      "settings-detail-active",
    );
    $("#feedView").classList.toggle("mobile-reader-active", this.wasReader);
    ["#feedColSettings", "#detailPanelSettings"].forEach((id) =>
      $(id).classList.remove("active"),
    );
    this.previousPanels.forEach((panel) => panel.classList.add("active"));
    this.previousNavigation.forEach((button) => {
      button.classList.add("active");
      button.setAttribute("aria-current", "page");
    });
    $("#leftNavSettingsBtn").classList.remove("active");
    $("#leftNavSettingsBtn").removeAttribute("aria-current");
    this.onViewChange?.();
    if (restoreFocus) {
      const target = this.lastFocus?.getClientRects().length && !this.lastFocus.closest('[inert]')
        ? this.lastFocus
        : $(
            drawerNavigation.matches ? "#openNavBtn" : "#leftNavSettingsBtn",
          );
      target.focus({ preventScroll: true });
    }
    // Drafts and reading positions survive navigation between workspaces.
  }
  handleKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
      return;
    }
    const action = keyboard.action(event);
    if (action === "settings") {
      event.preventDefault();
      this.close();
    } else if (["previous", "next"].includes(action)) {
      event.preventDefault();
      const buttons = $$("[data-settings-tab]").filter(
        (button) => !button.hidden,
      );
      const current = buttons.findIndex(
        (button) => button.dataset.settingsTab === this.section,
      );
      const next =
        buttons[
          Math.max(
            0,
            Math.min(
              buttons.length - 1,
              current + (action === "next" ? 1 : -1),
            ),
          )
        ];
      if (next) {
        this.selectSection(next.dataset.settingsTab, { reveal: false });
        this.showCategories(false);
        next.focus();
      }
    }
  }
  status(text, failed = false) {
    this.syncDirty();
    $("#settingsStatus").textContent = !this.dirty && text === "有未保存的更改" ? "无未保存的更改" : text;
    $("#settingsStatus").classList.toggle("error-state", failed);
    $("#settingsStatus").classList.toggle("dirty-state", this.dirty);
    $("#settingsResetBtn").disabled = !this.dirty;
    const actions = $(".settings-detail-actions");
    if (!this.dirty && actions.contains(document.activeElement)) $("#settingsDetailTitle").focus({ preventScroll: true });
    actions.hidden = !this.dirty;
  }
  syncDirty() {
    if (!this.saved || !this.draft) return;
    const comparable = data => {
      const payload = this.payload(data);
      payload.feed.hidden_source_subkeys = [...(payload.feed.hidden_source_subkeys || [])].sort();
      return JSON.stringify([payload, normalizeShortcuts(data.keyboard)]);
    };
    this.dirty = comparable(this.draft) !== comparable(this.saved);
  }
  selectSection(section, { reveal = true } = {}) {
    const selected = $(`[data-settings-tab="${section}"]`);
    if (!selected) return;
    const changed = this.section !== section;
    if (changed) this.selects.close(false);
    this.section = section;
    $$("[data-settings-tab]").forEach((button) => {
      const active = button === selected;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", String(active));
    });
    $$("#settingsContent > [data-section]").forEach((panel) => {
      panel.hidden = panel.dataset.section !== section;
    });
    $("#settingsDetailTitle").textContent = selected.dataset.title;
    $("#settingsDetailTitle").title = selected.dataset.title;
    $("#settingsDetailDescription").textContent = selected.dataset.description || '';
    $("#settingsDetailDescription").hidden = !selected.dataset.description;
    if (changed) $(".settings-main").scrollTop = 0;
    if (section === "system" && $("#updateResult")) this.updater.restore();
    if (section === "catchup" && $("#settingsCatchupScope")) this.loadCatchupScope();
    if (section === "classification" && this.open) void this.onClassificationRefresh?.();
    if (reveal && singlePane.matches) {
      $("#feedView").classList.add("settings-detail-active");
      this.onViewChange?.();
      // The panel is still sliding in from outside the viewport. Default focus
      // would scroll its overflow-hidden parent sideways to reveal the title.
      $("#settingsDetailTitle").focus({ preventScroll: true });
    }
    this.schedules.sync();
  }
  showCategories(focus = true) {
    this.selects.close(false);
    $("#feedView").classList.remove("settings-detail-active");
    this.schedules.sync();
    this.onViewChange?.();
    if (focus) {
      const selected = $(`[data-settings-tab="${this.section}"]`);
      selected.focus({ preventScroll: true });
    }
  }
  resetDraft() {
    if (!this.saved) return;
    this.draft = clone(this.saved);
    this.dirty = false;
    this.apiStates.clear();
    this.catalogNotice = "";
    this.catalogSequence++;
    this.connectionSequence++;
    this.connectionResult = "";
    this.render();
    this.status("已恢复已保存的配置");
  }
  payload(d = this.draft) {
    return {
      llm: clone(d.llm),
      api_models: clone(d.api_models || { jev: { model: "" } }),
      classification: clone(d.classification || { enabled: true, concurrency: 8, unread_only: false }),
      feed: clone(d.feed),
      agent: clone(d.agent),
      news_summary: clone(d.news_summary || { max_days: 20, max_items: 6000 }),
      database: { path: d.database.configured_path || "" },
    };
  }
  modelDraft() {
    return {
      runtime: this.draft.agent.runtime,
      codex: clone(this.draft.agent.codex),
      pi: clone(this.draft.llm.pi_chat),
    };
  }
  updateDraft(event) {
    const input = event.target;
    if (input.name?.startsWith("keyboard.")) {
      set(this.draft, input.name, input.type === "checkbox" ? input.checked : input.value);
      this.status("有未保存的更改");
      $("#shortcutValidation").textContent = "";
      $$("#shortcutSettings [aria-invalid]").forEach(node => node.removeAttribute("aria-invalid"));
      return;
    }
    if (input.matches("[data-api-secret]")) { this.apiState().secretDraft = input.value; return; }
    if (input.name === "managedApiProvider") {
      if (this.apiProvider !== input.value) {
        this.apiProvider = input.value;
        this.renderTranslation();
        $('[data-select-trigger="managedApiProvider"]').focus({ preventScroll: true });
      }
      return;
    }
    if (!input.name || !this.draft) return;
    const value = input.type === "checkbox" ? input.checked : input.type === "number" || input.name === "agent.session_ttl_hours"
      ? Number(input.value) : input.value;
    const unchanged = Object.is(get(this.draft, input.name), value);
    // Numeric fields emit change again on blur, after input already updated the draft.
    if (unchanged && (input.name.startsWith("news_summary.") || event.type !== "change")) return;
    set(this.draft, input.name, value);
    if (input.name === "classification.enabled") $("#classificationOptions").disabled = !value;
    if (input.name === this.apiConfig().model_path) {
      const state = this.apiState();
      state.sequence++;
      state.notice = "模型已变化，可以测试当前选择。";
      state.testResult = "[READY] 模型已变化，请重新测试当前选择。";
      $("#translationTestTerminal").textContent = state.testResult;
      $("#translationStatus").textContent = state.notice;
      this.syncModelFields(input, this.translationOptions());
    }
    if (input.name === "agent.codex.model") {
      this.syncModelFields(input, this.codexOptions());
      if (!unchanged) {
        this.draft.agent.codex.reasoning_effort = "";
        this.renderReasoning();
      }
    }
    if (input.name.startsWith("news_summary.")) this.loadCatchupScope(250);
    this.connectionSequence++;
    this.connectionResult = "[READY] 配置已变化，请重新测试。";
    if ($("#connTestTerminal"))
      $("#connTestTerminal").textContent = this.connectionResult;
    if (["agent.codex.provider", "agent.codex.profile"].includes(input.name)) {
      this.catalogNotice = "";
      this.catalogSequence++;
      this.draft.agent.codex.reasoning_effort = "";
      this.renderCodexSelection();
    }
    if (input.name === "llm.pi_chat.provider" && event.type === "change") {
      this.draft.llm.pi_chat.model = "";
      this.renderModels();
    }
    this.status("有未保存的更改");
  }
  syncModelFields(input, options) {
    const dropdown = $(`select[name="${input.name}"]`, $("#settingsContent"));
    const custom = $(`input[name="${input.name}"]`, $("#settingsContent"));
    if (input === dropdown) custom.value = options.some(option => option.value === input.value) ? "" : input.value;
    else {
      for (const option of dropdown.querySelectorAll("[data-custom-option]"))
        if (option.value !== input.value) option.remove();
      if (![...dropdown.options].some(option => option.value === input.value)) {
        const option = new Option(input.value, input.value);
        option.dataset.customOption = "true";
        dropdown.add(option);
      }
      dropdown.value = input.value;
    }
    this.selects.refresh();
  }
  catalogKey(runtime = this.draft.agent.runtime, config = this.draft.agent.codex) {
    return JSON.stringify(runtime === "codex" ? [runtime, config.provider || "", config.profile || ""] : [runtime]);
  }
  rememberModelCatalogs(data) {
    for (const [runtime, name] of [["codex", "codex"], ["pi", "pi_chat"]]) {
      const catalog = data.model_catalogs?.[name];
      const key = this.catalogKey(runtime, data.agent.codex);
      if (catalog && (catalog.options?.length || catalog.provider_options?.length || !this.catalogs.has(key)))
        this.catalogs.set(key, clone(catalog));
    }
  }
  modelCatalog(runtime = this.draft.agent.runtime) {
    return this.catalogs.get(this.catalogKey(runtime)) || {};
  }
  codexOptions() {
    return this.modelCatalog("codex").options?.filter(option => option.source !== "saved") || [];
  }
  renderCodexSelection() {
    const current = $('select[name="agent.codex.model"]');
    if (!current) return;
    const options = this.codexOptions(), value = this.draft.agent.codex.model;
    const container = document.createElement("div");
    container.innerHTML = select("模型选择 (Model)", "agent.codex.model", value, options, "沿用本机默认");
    current.closest(".field-item").replaceWith(container.firstElementChild);
    $('input[name="agent.codex.model"]').value = options.some(option => option.value === value) ? "" : value;
    $("#modelsNotice").textContent = this.modelCatalog().notice || "候选目录不代表实际调用权限。";
    this.renderReasoning();
  }
  renderReasoning() {
    const current = $('select[name="agent.codex.reasoning_effort"]');
    if (!current) return;
    const efforts = this.codexOptions().find(option => option.value === this.draft.agent.codex.model)?.reasoning_efforts || [];
    const container = document.createElement("div");
    container.innerHTML = select("推理强度 (Reasoning Effort)", "agent.codex.reasoning_effort", this.draft.agent.codex.reasoning_effort, efforts, "沿用默认");
    current.closest(".field-item").replaceWith(container.firstElementChild);
    $("#modelCapabilitiesHint").textContent = efforts.length ? "推理档位来自当前模型目录。" : "当前模型能力未确认；保留已保存档位，或沿用本机默认。";
    this.selects.refresh();
  }
  render() {
    if (!this.draft) return;
    const d = this.draft;
    this.selects.close(false);
    $("#settingsContent").innerHTML =
      `<section class="settings-section-card" data-section="models" id="modelSettings"></section>
      <div class="settings-sheet" data-section="schedules" id="scheduleSettings"></div>
      <section class="settings-section-card" data-section="shortcuts" id="shortcutSettings"></section>
      <section class="settings-section-card" data-section="deepseek" id="translationSettings"></section>
      <section class="settings-section-card" data-section="classification" id="classificationSettingsProgress" aria-labelledby="classificationSettingsProgressTitle">
      <div class="section-card-title"><span id="classificationSettingsProgressTitle">分类进度</span><button class="reader-tool-btn" id="classificationRefreshBtn" type="button">刷新进度</button></div>
      <p id="classificationSettingsStatus" role="status">正在读取分类进度…</p>
      <p class="error-state" id="classificationSettingsProgressError" role="alert" hidden></p>
      <div class="comparison-summary" id="classificationSettingsCompletion" hidden>
      <div class="proportion-chart" id="classificationCoverageChart" role="img" aria-label="新闻分类覆盖率">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle class="classification-progress-track" cx="12" cy="12" r="9"/>
      <circle class="classification-progress-arc" id="classificationCoverageArc" cx="12" cy="12" r="9" pathLength="100" transform="rotate(-90 12 12)"/></svg>
      <strong class="progress-count" id="classificationCoveragePercent"></strong></div>
      <div class="field-item"><span class="field-label">已分类 / 全部新闻</span><strong class="progress-count" id="classificationSettingsCompletionText"></strong><span class="muted" id="classificationCoverageRemaining"></span></div></div>
      <span class="field-label" id="classificationSettingsQueueLabel" hidden></span>
      <div class="catchup-metrics" id="classificationSettingsCounts" hidden>${[['done','已完成'],['pending','待处理'],['running','处理中'],['failed','失败']].map(([key,label])=>`<span>${label} <strong data-classification-count="${key}">—</strong></span>`).join('')}</div>
      <div class="button-row"><span class="muted" id="classificationSettingsUpdated"></span><button class="btn-secondary" id="classificationRetryBtn" type="button" hidden>重试失败项</button></div></section>
      <section class="settings-section-card" data-section="classification" id="classificationSettings">
      <div class="section-card-title"><span>自动分类</span></div>
      <label class="settings-toggle settings-switch"><span>启用自动分类</span><input type="checkbox" role="switch" name="classification.enabled" ${d.classification?.enabled !== false ? "checked" : ""}></label>
      <p class="muted">保存后生效。关闭后停止新的分类请求，已完成的结果保留；正在处理的请求会继续完成。</p>
      <fieldset id="classificationOptions" class="classification-options field-options" aria-label="自动分类选项" ${d.classification?.enabled === false ? "disabled" : ""}>
      <label class="settings-toggle"><input type="checkbox" role="switch" name="classification.unread_only" ${d.classification?.unread_only ? "checked" : ""}><span>仅对未读新闻分类</span></label>
      ${field("分类并发上限", "classification.concurrency", d.classification?.concurrency ?? 8, 'type="number" min="1" max="32" step="1" required')}
      <p class="muted">默认 8，可设置 1–32。遇到限流自动降速，稳定后逐步恢复。</p>
      </fieldset>
      <p class="muted">分类使用 API 管理中的 Jev 密钥与模型。</p>
      <button class="btn-secondary" id="classificationApiSettingsBtn" type="button">管理 Jev API</button></section>
      <section class="settings-section-card" data-section="catchup"><div class="section-card-title"><span>积压新闻总结范围</span></div>
      <div class="form-group-grid">${field("最多自然日（含起止）", "news_summary.max_days", d.news_summary?.max_days ?? 20, 'type="number" min="1" max="9007199254740991" step="1" required')}${field("最多未读条数", "news_summary.max_items", d.news_summary?.max_items ?? 6000, 'type="number" min="1" max="9007199254740991" step="1" required')}</div>
      <div class="catchup-scope-preview" id="settingsCatchupScope" role="status">正在读取当前生效范围…</div>
      <button class="btn-secondary" id="resetCatchupDefaultsBtn" type="button">恢复默认范围（20 天 / 6000 条）</button></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>信源增量采集与 opencli 适配器</span></div><p class="muted">保存信源开关后更新新闻流，保留原有阅读状态。正文抓取由本机 opencli 执行，可在文章中查看结果。</p><div id="settingsSources">${this.sourceHTML()}</div><button class="btn-secondary" id="settingsReindexBtn" type="button">重新扫描 DailyNews</button></section>
      <section class="settings-section-card" data-section="storage"><div class="section-card-title"><span>本地 SQLite 数据库与存储路径</span></div>
      <div class="field-item"><span class="field-label">当前主数据库路径</span><code class="storage-path">${e(d.database.path)}</code></div>
      ${field("下次启动使用的数据库", "database.configured_path", d.database.configured_path, d.database.environment_override ? "disabled" : 'placeholder="已有 SQLite 文件的完整路径"')}
      <button class="btn-secondary" id="pickDatabaseBtn" type="button" ${d.database.environment_override || !d.database.native_picker_available ? "disabled" : ""}>选择文件…</button><p class="muted">${e(d.database.notice)}</p></section>
      <section class="settings-section-card" data-section="system"><div class="section-card-title"><span>系统状态与版本升级</span></div><div><strong id="updateCurrent"></strong><p class="muted">从本项目仓库获取稳定版本，更新后自动重启；启动检查失败时尝试恢复旧版本。</p><div class="field-item"><span class="field-label">更新源</span><code id="updateRepository" class="storage-path"></code></div></div><div class="button-row"><button class="btn-secondary" id="checkUpdateBtn" type="button">检查更新</button><button class="btn-primary" id="applyUpdateBtn" type="button" hidden>确认更新</button><button class="btn-primary" id="refreshUpdateBtn" type="button" hidden>刷新使用新版本</button><button class="btn-secondary" id="resumeUpdateBtn" type="button" hidden>继续查看进度</button></div><div id="updateResult" hidden role="status" aria-live="polite" aria-atomic="true"><strong id="updateConclusion"></strong><p id="updateExplanation"></p><time id="updateCheckedAt"></time></div><details><summary>版本记录</summary><div id="releaseNotes" class="muted">展开后读取</div></details></section>`;
    this.renderModels();
    this.renderTranslation();
    this.renderShortcuts();
    this.updater.render();
    this.renderClassificationProgress();
    this.schedules.mount($("#scheduleSettings"));
    this.selectSection(this.section, { reveal: false });
    $("#settingsContent details").ontoggle = (event) => {
      if (event.target.open) this.loadReleaseNotes();
    };
    this.status(this.dirty ? "有未保存的更改" : "配置已加载");
  }
  renderShortcuts() {
    const panel = $("#shortcutSettings");
    if (!panel || !this.draft) return;
    const prefs = this.draft.keyboard || keyboard.snapshot();
    panel.innerHTML = `<label class="settings-toggle settings-switch"><span>显示底部快捷键栏</span><input type="checkbox" role="switch" name="keyboard.show_bar" ${prefs.show_bar ? "checked" : ""}></label>
      <p class="muted" id="shortcutInstructions">点击输入框后，直接按下按键或组合键。每项最多两个快捷键，清空两个框可停用。按 Esc 结束录入，Tab 切换输入框。</p>
      <div class="shortcut-settings-list">${shortcutActions.map(action => `<div class="shortcut-setting-row" role="group" aria-labelledby="shortcut-label-${action.id}"><span id="shortcut-label-${action.id}">${e(action.label)}</span>${[0, 1].map(index => `<div class="shortcut-key-field"><input id="shortcut-${action.id}-${index}" class="field-input" data-shortcut-action="${action.id}" data-shortcut-slot="${index}" value="${e(displayKey(prefs.bindings[action.id]?.[index] || ""))}" aria-label="${e(action.label)}快捷键 ${index + 1}" aria-describedby="shortcutInstructions shortcutValidation" autocomplete="off" spellcheck="false" readonly inputmode="none" placeholder="按下快捷键"><button class="shortcut-clear-btn" data-clear-shortcut="${action.id}" data-shortcut-slot="${index}" aria-label="清除${e(action.label)}快捷键 ${index + 1}" type="button" ${prefs.bindings[action.id]?.[index] ? "" : "disabled"}>×</button></div>`).join("")}</div>`).join("")}</div>
      <p class="inline-error" id="shortcutValidation" role="alert"></p>
      <button class="btn-secondary" id="resetShortcutsBtn" type="button">恢复默认快捷键</button>`;
    $("#resetShortcutsBtn").onclick = () => {
      this.draft.keyboard.bindings = defaultShortcuts().bindings;
      this.renderShortcuts();
      this.status("有未保存的更改");
    };
    $$("[data-shortcut-action]", panel).forEach(input => {
      input.onkeydown = event => {
        event.stopPropagation();
        if (event.key === "Tab" && !event.ctrlKey && !event.altKey && !event.metaKey) return;
        event.preventDefault();
        if (event.key === "Escape") { input.blur(); return; }
        if (event.repeat) return;
        try {
          const key = shortcutKeyFromEvent(event);
          if (key) this.setShortcut(input, key);
        } catch (error) { this.shortcutError(input, error.message); }
      };
    });
    $$("[data-clear-shortcut]", panel).forEach(button => { button.onclick = () => {
      const input = $(`#shortcut-${button.dataset.clearShortcut}-${button.dataset.shortcutSlot}`);
      this.setShortcut(input, "");
      input.focus();
    }; });
  }
  shortcutError(input, message) {
    $("#shortcutValidation").textContent = message;
    input.setAttribute("aria-invalid", "true");
  }
  setShortcut(input, key) {
    const action = input.dataset.shortcutAction, index = Number(input.dataset.shortcutSlot);
    const prefs = clone(this.draft.keyboard);
    const conflict = key && shortcutActions.find(other => prefs.bindings[other.id].some((value, slot) => value === key && (other.id !== action || slot !== index)));
    if (conflict) return this.shortcutError(input, `${displayKey(key)} 已用于「${conflict.label}」，请先清除原绑定或选择其他按键`);
    const slots = [prefs.bindings[action][0] || "", prefs.bindings[action][1] || ""];
    slots[index] = key;
    prefs.bindings[action] = slots;
    this.draft.keyboard = normalizeShortcuts(prefs);
    input.value = displayKey(key);
    input.nextElementSibling.disabled = !key;
    $("#shortcutValidation").textContent = "";
    $$("#shortcutSettings [aria-invalid]").forEach(node => node.removeAttribute("aria-invalid"));
    this.status("有未保存的更改");
  }
  translationOptions() {
    if (!this.draft.api_status?.[this.apiProvider]?.configured) return [];
    const key = this.apiProvider === "deepseek" ? "translation" : this.apiProvider;
    return (this.apiState().catalog || this.draft.model_catalogs?.[key])?.options?.filter(option => option.source !== "saved") || [];
  }
  apiState(provider = this.apiProvider) {
    if (!this.apiStates.has(provider)) this.apiStates.set(provider, {
      secretDraft: "", editingSecret: false, catalog: null, notice: "", testResult: "", sequence: 0,
      testing: false, refreshing: false, savingSecret: false,
    });
    return this.apiStates.get(provider);
  }
  apiConfig(provider = this.apiProvider) {
    return this.draft.api_providers.find(config => config.id === provider);
  }
  renderTranslation() {
    const d = this.draft, provider = this.apiProvider, config = this.apiConfig(), state = this.apiState();
    const service = d.api_status?.[provider] || {};
    const configured = service.configured, editing = !configured || state.editingSecret;
    const options = this.translationOptions();
    const model = get(d, config.model_path) || "";
    const custom = options.some(option => option.value === model) ? "" : model;
    $("#translationSettings").innerHTML = `${select("API 服务", "managedApiProvider", provider, d.api_providers.map(p => ({ value: p.id, label: p.label })), null)}
      <p class="muted">${e(config.description)}</p>
      <div class="translation-model-row">${select(provider === "deepseek" ? "翻译模型" : "模型", config.model_path, model, options, configured ? (config.default_model ? `使用默认模型（${config.default_model}）` : "使用默认模型") : "请先配置 API 密钥")}<button class="btn-secondary" id="translationModelsBtn" type="button" ${configured ? "" : "hidden"} ${state.refreshing ? "disabled" : ""}>${state.refreshing ? "正在获取…" : "获取模型"}</button></div>
      ${field("自定义模型（可选）", config.model_path, custom, 'maxlength="120"')}
      <p class="muted" id="translationStatus" role="status">${e(state.notice || (configured ? "选择目录中的模型，或填写自定义模型。" : "保存 API 密钥后可获取和测试模型；已填写的模型名称会保留。"))}</p>
      <div class="field-item"><label class="field-label" ${editing ? `for="${provider}Secret"` : ""}>API 密钥</label>
      ${configured ? `<code class="masked-secret">${e(service.masked_key || "••••••••")}</code>` : '<p class="muted">尚未配置密钥</p>'}
      ${service.environment_override ? `<p class="muted">当前由 ${e(config.secret_name)} 环境变量提供；保存和删除仅影响本地钥匙串，环境变量仍优先生效。</p>` : ""}
      ${editing ? `<input class="field-input" id="${provider}Secret" data-api-secret type="password" autocomplete="new-password" value="${e(state.secretDraft)}" placeholder="输入新的 API 密钥" ${state.savingSecret ? "disabled" : ""}><div class="button-row"><button class="btn-secondary" id="saveSecretBtn" type="button" ${state.savingSecret ? "disabled" : ""}>保存密钥</button>${configured ? '<button class="btn-secondary" id="cancelSecretBtn" type="button">取消</button>' : ""}</div>` : `<div class="button-row"><button class="btn-secondary" id="editSecretBtn" type="button">更新密钥</button><button class="reader-action-btn danger" id="deleteSecretBtn" type="button" ${state.savingSecret ? "disabled" : ""}>删除密钥</button></div>`}</div>
      <div class="connection-test-section"><div><strong>模型连接测试</strong><p class="muted">使用已保存的密钥和当前选择的模型测试，无需先保存模型。</p></div><button class="btn-primary" id="translationTestBtn" type="button" ${configured && !state.testing ? "" : "disabled"}>${state.testing ? "正在测试…" : "测试模型"}</button><div class="terminal-box" id="translationTestTerminal" role="status">${e(state.testResult || "[READY] 连接测试器就绪 · 点击上方按钮发起探测")}</div></div>`;
    this.selects.refresh($("#translationSettings"));
  }
  async refreshTranslationModels() {
    const provider = this.apiProvider, state = this.apiState();
    if (state.refreshing) return;
    const seq = state.sequence;
    state.refreshing = true;
    try {
      const data = await api(`/api/settings/apis/${provider}/models`, { method: "POST", body: {} });
      if (state !== this.apiState(provider) || seq !== state.sequence) return;
      state.catalog = data.catalog;
      state.notice = `已获取 ${data.catalog.options.length} 个模型`;
    } catch (error) {
      if (state === this.apiState(provider) && seq === state.sequence) state.notice = errorText(error);
    } finally {
      state.refreshing = false;
      if (provider === this.apiProvider && state === this.apiState(provider)) this.renderTranslation();
    }
  }
  async testTranslation() {
    const provider = this.apiProvider, state = this.apiState(), model = get(this.draft, this.apiConfig().model_path) || "";
    if (state.testing) return;
    const seq = state.sequence;
    state.testing = true;
    state.testResult = "[RUNNING] 正在测试当前模型…";
    $("#translationTestTerminal").textContent = state.testResult;
    try {
      const data = await api(`/api/settings/apis/${provider}/test`, { method: "POST", body: { model } });
      if (state === this.apiState(provider) && seq === state.sequence) state.testResult = `[PASS] ${data.message} · ${data.model} · ${data.elapsed_ms}ms`;
    } catch (error) {
      if (state === this.apiState(provider) && seq === state.sequence) state.testResult = `[FAIL] ${errorText(error)}`;
    } finally {
      state.testing = false;
      if (provider === this.apiProvider && state === this.apiState(provider)) {
        $("#translationTestTerminal").textContent = state.testResult;
        $("#translationTestBtn").disabled = !this.draft.api_status?.[provider]?.configured;
        $("#translationTestBtn").textContent = "测试模型";
      }
    }
  }
  renderModels() {
    if (!$("#modelSettings")) return;
    const d = this.draft,
      codex = d.agent.runtime === "codex";
    const catalog = this.modelCatalog();
    const codexOptions = this.codexOptions();
    const model = codexOptions.find((x) => x.value === d.agent.codex.model);
    const custom = model ? "" : d.agent.codex.model;
    const efforts = model?.reasoning_efforts || [];
    const savedEffort = d.agent.codex.reasoning_effort;
    $("#modelSettings").innerHTML =
      `<div class="section-card-title"><span>运行方式与模型</span></div>
    <div class="field-item"><span class="field-label">运行方式 (Runtime) 选择</span><div class="runtime-switch-grid"><button class="runtime-card ${codex ? "selected" : ""}" type="button" data-runtime="codex" aria-pressed="${codex}"><span class="runtime-card-top">Codex CLI</span><span class="runtime-card-desc">使用本机模型、预设与只读文件沙盒配置。</span></button><button class="runtime-card ${!codex ? "selected" : ""}" type="button" data-runtime="pi" aria-pressed="${!codex}"><span class="runtime-card-top">Pi SDK / OAR</span><span class="runtime-card-desc">本机会话桥接、技能扩展与模型目录。</span></button></div></div>
    ${codex ? `<div class="form-group-grid">${field("服务商覆盖（可选）", "agent.codex.provider", d.agent.codex.provider)}${field("本机 Profile（可选）", "agent.codex.profile", d.agent.codex.profile)}</div><div class="form-group-grid">${select("模型选择 (Model)", "agent.codex.model", d.agent.codex.model, codexOptions, "沿用本机默认")}${select("推理强度 (Reasoning Effort)", "agent.codex.reasoning_effort", savedEffort, efforts, "沿用默认")}</div>${field("自定义模型（可选）", "agent.codex.model", custom, 'maxlength="120"')}<p class="muted" id="modelCapabilitiesHint">${efforts.length ? "推理档位来自当前模型目录。" : "当前模型能力未确认；保留已保存档位，或沿用本机默认。"}</p>` : `<div class="form-group-grid">${select("服务商 (Provider)", "llm.pi_chat.provider", d.llm.pi_chat.provider, catalog.provider_options || [], "请选择服务商")}${select("模型选择 (Model)", "llm.pi_chat.model", d.llm.pi_chat.model, catalog.model_options_by_provider?.[d.llm.pi_chat.provider] || catalog.options || [], "请选择模型")}</div>`}
    <button class="btn-secondary" id="refreshModelsBtn" type="button">刷新模型目录</button><p class="muted" id="modelsNotice">${e(this.catalogNotice || catalog.notice || "候选目录不代表实际调用权限。")}</p>
    <div class="form-group-grid" id="modelUsage" aria-label="应用中的使用范围">
      <div class="field-item"><span class="field-label">Chat 与 Agent</span><span>新闻问答、研究及对话归档</span></div>
      <div class="field-item"><span class="field-label">主题追踪</span><span>主题定义、历史整理与进程精简</span></div>
      <div class="field-item"><span class="field-label">积压新闻总结</span><span>新闻归纳与总结生成</span></div>
      <div class="field-item"><span class="field-label">日报、周报、月报</span><span>早午晚报、周期回顾及手动生成</span></div>
    </div>
    <div class="connection-test-section"><div><strong>模型可用性独立连接测试</strong><p class="muted">使用当前未保存选择测试，不写入新闻或聊天历史。</p></div><button class="btn-primary" id="testConnectionBtn" type="button" ${this.testing ? "disabled" : ""}>${this.testing ? "测试中…" : "运行连接测试"}</button><div class="terminal-box" id="connTestTerminal" role="status">${e(this.connectionResult || "[READY] 连接测试器就绪 · 点击上方按钮发起探测")}</div></div>
    ${select(
      "临时会话保留时间",
      "agent.session_ttl_hours",
      String(d.agent.session_ttl_hours),
      [
        { value: "24", label: "24 小时" },
        { value: "72", label: "72 小时" },
      ],
    )}<button class="reader-action-btn danger" id="clearSessionsBtn" type="button">清空全部临时 Agent 会话</button>`;
    const placeholder = $('[name="agent.session_ttl_hours"] option[value=""]');
    if (placeholder) placeholder.remove();
    this.selects.refresh($("#modelSettings"));
  }
  sourceHTML() {
    const hidden = new Set(this.draft.feed.hidden_source_subkeys || []);
    return (
      (this.draft.feed_source_subkeys?.groups || [])
        .map(
          (group) =>
            `<fieldset class="settings-source-group"><legend>${e(group.label)}</legend>${group.items.map((item) => `<label class="sub-source-item"><input type="checkbox" data-setting-source="${e(item.key)}" ${hidden.has(item.key) ? "" : "checked"}><span>${e(item.label)}</span><span class="muted">${item.count}</span></label>`).join("")}</fieldset>`,
        )
        .join("") || '<p class="muted">索引新闻后，这里会显示可配置的信源。</p>'
    );
  }
  updateClassificationProgress(snapshot) {
    this.classificationSnapshot = snapshot;
    this.renderClassificationProgress();
  }
  renderClassificationProgress() {
    if (!$("#classificationSettingsProgress")) return;
    const {progress:p, error, updatedAt} = this.classificationSnapshot || {};
    const status=$("#classificationSettingsStatus");
    const complete=p && p.enabled!==false && p.total>0 && !p.pending && !p.running && !p.failed && !p.error;
    status.hidden=!!complete;
    status.textContent=complete ? '' : p ? classificationStatus(p) : error ? "尚未取得分类进度" : classificationStatus();
    const warning=$("#classificationSettingsProgressError");
    warning.hidden=!error; warning.textContent=error || '';
    for (const id of ['Counts','QueueLabel']) $("#classificationSettings"+id).hidden=!p;
    const coverage=p?.coverage;
    $("#classificationSettingsCompletion").hidden=!coverage;
    const retry=$("#classificationRetryBtn");
    retry.hidden=!p || p.enabled===false || !p.configured || !(p.failed || p.error);
    $("#classificationSettingsUpdated").textContent=updatedAt ? '更新于 '+updatedAt.toLocaleTimeString('zh-CN',{hour12:false}) : '';
    if(!p)return;
    if(coverage) {
      const total=coverage.total, done=Math.min(total,Math.max(0,coverage.done)), ratio=total ? done/total*100 : 0;
      const percent=total ? (Math.floor(ratio*10)/10).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%' : '—';
      $("#classificationCoveragePercent").textContent=percent;
      $("#classificationSettingsCompletionText").textContent=`${done.toLocaleString()} / ${total.toLocaleString()} 条`;
      $("#classificationCoverageRemaining").textContent=total ? `未分类 ${(total-done).toLocaleString()} 条` : '暂无新闻';
      $("#classificationCoverageChart").setAttribute('aria-label',`全部新闻 ${total} 条，已分类 ${done} 条，未分类 ${total-done} 条`);
      $("#classificationCoverageArc").style.strokeDashoffset=String(100-ratio);
      $("#classificationCoverageArc").style.opacity=done ? '1' : '0';
    }
    $("#classificationSettingsQueueLabel").textContent=p.unread_only ? '仅未读新闻队列' : '分类队列';
    const settled=['pending','running','failed'].every(key=>p[key]===0);
    for(const element of $$('[data-classification-count]')) {
      const key=element.dataset.classificationCount;
      element.textContent=p[key].toLocaleString()+' 条';
      element.parentElement.dataset.status=key==='done' ? (settled?'success':'') : p[key]>0 ? (key==='failed'?'danger':'warning') : '';
    }
  }
  click(event) {
    const button = event.target.closest("button");
    const input = event.target;
    if (input.dataset.settingSource) {
      const hidden = new Set(this.draft.feed.hidden_source_subkeys || []);
      input.checked
        ? hidden.delete(input.dataset.settingSource)
        : hidden.add(input.dataset.settingSource);
      this.draft.feed.hidden_source_subkeys = [...hidden];
      this.dirty = true;
      this.status("有未保存的更改");
    }
    if (!button) return;
    if (button.dataset.runtime) {
      this.draft.agent.runtime = button.dataset.runtime;
      this.dirty = true;
      this.catalogNotice = "";
      this.connectionSequence++;
      this.catalogSequence++;
      this.connectionResult = "";
      this.renderModels();
      this.status("有未保存的更改");
      return;
    }
    const actions = {
      classificationRefreshBtn: () => this.onClassificationRefresh?.(),
      classificationRetryBtn: () => this.onClassificationRetry?.(),
      classificationApiSettingsBtn: () => {
        this.apiProvider = "jev";
        this.renderTranslation();
        this.selectSection("deepseek");
      },
      retryCatchupScopeBtn: () => this.loadCatchupScope(),
      resetCatchupDefaultsBtn: () => {
        this.draft.news_summary = { max_days: 20, max_items: 6000 };
        this.dirty = true;
        this.render();
      },
      retrySettingsBtn: () => this.load(),
      refreshModelsBtn: () => this.refreshModels(),
      testConnectionBtn: () => this.testConnection(),
      editSecretBtn: () => { this.apiState().editingSecret = true; this.renderTranslation(); $("[data-api-secret]").focus(); },
      cancelSecretBtn: () => { this.apiState().secretDraft = ""; this.apiState().editingSecret = false; this.renderTranslation(); },
      translationModelsBtn: () => this.refreshTranslationModels(),
      translationTestBtn: () => this.testTranslation(),
      saveSecretBtn: () => this.saveSecret(),
      deleteSecretBtn: () => this.deleteSecret(),
      pickDatabaseBtn: () => this.pickDatabase(),
      settingsReindexBtn: async () => {
        await api("/api/reindex", { method: "POST" });
        toast("新闻索引已刷新");
        this.onSaved();
      },
      clearSessionsBtn: async () => {
        const saved = this.saved, path = saved.database.path;
        if (!await confirmAction({ title: "清空临时 Agent 会话", trigger: "#clearSessionsBtn", message: "清空当前新闻库的临时 Agent 对话与任务？新闻、想法和尚未归属的旧会话会保留。", action: "清空临时会话" })) return;
        if (this.saved !== saved || this.saved.database.path !== path) return toast("新闻库已变化，请重新操作");
        const current = await api("/api/settings");
        if (current.database.path !== path || this.saved !== saved) return toast("新闻库已变化，请重新操作");
        const result = await api("/api/agent/sessions", { method: "DELETE" });
        toast(result.preserved_legacy ? "当前库临时会话已清空；尚未归属的旧会话已保留" : "当前库临时会话已清空");
      },
      checkUpdateBtn: () => this.updater.check(),
      applyUpdateBtn: () => this.updater.apply(),
      refreshUpdateBtn: () => this.updater.refresh(),
      resumeUpdateBtn: () => this.updater.poll(),
    };
    if (["checkUpdateBtn", "applyUpdateBtn", "refreshUpdateBtn", "resumeUpdateBtn"].includes(button.id)) {
      actions[button.id]();
    } else if (actions[button.id]) busy(button, actions[button.id]);
  }
  async save() {
    if (!this.draft) return;
    let keyboardDraft;
    try { keyboardDraft = normalizeShortcuts(this.draft.keyboard); }
    catch (error) {
      this.selectSection("shortcuts");
      $("#shortcutValidation").textContent = error.message;
      $(`#shortcut-${error.action}-0`).setAttribute("aria-invalid", "true");
      $(`#shortcut-${error.action}-0`).focus();
      this.status("快捷键配置有误，请检查后保存", true);
      return;
    }
    // Disabled fields skip browser validation, but their retained values are still saved.
    const invalid = $$('#settingsContent input[type="number"]').find(input => input.name === "classification.concurrency"
      ? !Number.isInteger(input.valueAsNumber) || input.valueAsNumber < Number(input.min) || input.valueAsNumber > Number(input.max)
      : !input.checkValidity());
    if (invalid) {
      this.selectSection(invalid.closest("[data-section]").dataset.section);
      if (invalid.matches(":disabled") && invalid.name === "classification.concurrency") {
        $('[name="classification.enabled"]').focus();
        this.status("请先启用自动分类，将分类并发上限设为 1–32 的整数后再保存", true);
        return;
      }
      invalid.reportValidity();
      this.status(invalid.name === "classification.concurrency" ? "分类并发上限必须是 1–32 的整数" : "总结范围必须填写有效的正整数", true);
      return;
    }
    const payload = this.payload();
    const fingerprint = JSON.stringify([payload, this.draft.keyboard]);
    const keyboardUnchanged = JSON.stringify(keyboardDraft) === JSON.stringify(keyboard.snapshot());
    this.status("正在保存…");
    try {
      // Browser-only preferences do not need an unrelated model/database write.
      const serverUnchanged = !keyboardUnchanged && JSON.stringify(payload) === JSON.stringify(this.payload(this.saved));
      const data = serverUnchanged ? clone(this.saved) : await api("/api/settings", { method: "PUT", body: payload });
      const unchanged = fingerprint === JSON.stringify([this.payload(), this.draft.keyboard]);
      data.keyboard = keyboardUnchanged ? keyboard.snapshot() : keyboard.save(keyboardDraft);
      this.saved = data;
      this.rememberModelCatalogs(data);
      if (unchanged) {
        this.draft = clone(data);
        this.dirty = false;
      }
      this.syncDirty();
      this.status(
        this.dirty ? "已保存提交的配置，仍有新的更改未保存" : "设置已保存",
      );
      if (!serverUnchanged) { this.onSources(); this.onSaved(); }
      if (this.section === "catchup") this.loadCatchupScope();
      toast("设置已保存");
    } catch (error) {
      this.status(errorText(error), true);
      throw error;
    }
  }
  async refreshModels() {
    const draft = this.modelDraft();
    const fingerprint = JSON.stringify([
      draft.runtime,
      draft.codex.provider,
      draft.codex.profile,
    ]);
    const seq = ++this.catalogSequence;
    try {
      const data = await api("/api/settings/models", {
        method: "POST",
        body: draft,
      });
      if (
        seq !== this.catalogSequence ||
        fingerprint !==
          JSON.stringify([
            this.draft.agent.runtime,
            this.draft.agent.codex.provider,
            this.draft.agent.codex.profile,
          ])
      )
        return;
      if (!data.catalog.error || data.catalog.options?.length || !this.catalogs.has(this.catalogKey()))
        this.catalogs.set(this.catalogKey(), clone(data.catalog));
      this.catalogNotice =
        data.catalog.error || data.catalog.notice || "模型目录已刷新";
      this.renderModels();
    } catch (error) {
      this.catalogNotice = errorText(error);
      this.renderModels();
      throw error;
    }
  }
  loadCatchupScope(delay = 0) {
    clearTimeout(this.catchupScopeTimer);
    this.catchupScopeController?.abort();
    const sequence = this.catchupScopeSequence = (this.catchupScopeSequence || 0) + 1;
    const target = $("#settingsCatchupScope");
    if (!target || !this.draft) return;
    const limits = clone(this.draft.news_summary || { max_days: 20, max_items: 6000 });
    if (![limits.max_days, limits.max_items].every(n => Number.isSafeInteger(n) && n > 0)) {
      target.textContent = "请输入有效的正整数以计算当前生效范围。";
      target.removeAttribute("aria-busy");
      return;
    }
    target.textContent = "正在计算当前生效范围…";
    target.setAttribute("aria-busy", "true");
    const controller = this.catchupScopeController = new AbortController();
    this.catchupScopeTimer = setTimeout(async () => {
      try {
        const scope = await api("/api/news-summaries/scope", { method: "POST", body: { news_summary: limits }, signal: controller.signal });
        if (sequence !== this.catchupScopeSequence || !target.isConnected) return;
        const coverage = scope.summarized_count ? `实际覆盖 ${e(scope.start_date)} → ${e(scope.end_date)}` : "暂无未读新闻";
        target.innerHTML = `<div class="catchup-scope-heading"><strong>当前生效范围</strong><span>${e(scope.window_start)} → ${e(scope.window_end)}</span></div><p>${e(visibilityText(scope))}</p><p>按日期与条数纳入 ${scope.summarized_count} 条；日期排除 ${scope.excluded_by.date} 条，条数排除 ${scope.excluded_by.count} 条。${coverage}${scope.undated_summarized ? `；另含日期不明 ${scope.undated_summarized} 条` : ""}。</p><p class="muted">当前填写：${scope.limits.max_days} 个自然日、最多 ${scope.limits.max_items} 条。${scope.fallback_window ? "当前日期窗口内无新闻，已回溯至最近有新闻的日期。" : ""}</p>`;
      } catch (error) {
        if (sequence === this.catchupScopeSequence && target.isConnected && error.name !== "AbortError") target.innerHTML = `<p>${e(errorText(error))}</p><button class="btn-secondary" id="retryCatchupScopeBtn" type="button">重新读取范围</button>`;
      } finally {
        if (sequence === this.catchupScopeSequence && target.isConnected) target.removeAttribute("aria-busy");
      }
    }, delay);
  }
  async testConnection() {
    const draft = this.modelDraft(),
      fingerprint = JSON.stringify(draft),
      seq = ++this.connectionSequence;
    this.testing = true;
    this.connectionResult = "[RUNNING] 正在测试当前模型连接…";
    $("#connTestTerminal").textContent = this.connectionResult;
    try {
      const data = await api("/api/settings/connection-test", {
        method: "POST",
        body: draft,
      });
      if (
        seq !== this.connectionSequence ||
        fingerprint !== JSON.stringify(this.modelDraft())
      )
        return;
      this.connectionResult =
        data.status === "success"
          ? `[SUCCESS] ${data.message || "连接成功"} · ${data.elapsed_ms}ms · ${data.checked_at || ""}`
          : `[FAILED] ${data.message || data.error || data.category || "模型请求未成功"}`;
    } catch (error) {
      if (seq === this.connectionSequence)
        this.connectionResult = `[FAILED] ${errorText(error)}`;
    } finally {
      this.testing = false;
      if ($("#connTestTerminal"))
        $("#connTestTerminal").textContent = this.connectionResult;
      const button = $("#testConnectionBtn");
      if (button) {
        button.disabled = false;
        button.textContent = "运行连接测试";
      }
    }
  }
  async saveSecret() {
    const provider = this.apiProvider, state = this.apiState(), submitted = state.secretDraft;
    if (state.savingSecret) return;
    const key = submitted.trim();
    if (!key) {
      toast("请输入新的 API 密钥");
      return;
    }
    state.savingSecret = true;
    try {
      const data = await api(`/api/settings/secrets/${provider}`, { method: "PUT", body: { key } });
      toast(`${this.apiConfig(provider).label} 密钥已保存`);
      this.applySecretResponse(data, provider);
      if (state === this.apiState(provider) && state.secretDraft === submitted) {
        state.secretDraft = "";
        state.editingSecret = false;
      }
    } finally {
      state.savingSecret = false;
      if (provider === this.apiProvider) this.renderTranslation();
    }
  }
  applySecretResponse(data, provider) {
    const catalogKey = provider === "deepseek" ? "translation" : provider;
    for (const settings of [this.saved, this.draft]) {
      settings.api_status[provider] = clone(data.api_status[provider]);
      settings.model_catalogs[catalogKey] = clone(data.model_catalogs[catalogKey]);
    }
    const state = this.apiState(provider);
    state.catalog = null;
    state.sequence++;
    state.notice = "";
    state.testResult = "";
  }
  async deleteSecret() {
    const provider = this.apiProvider, state = this.apiState(), config = this.apiConfig();
    if (state.savingSecret) return;
    const status = this.saved.api_status[provider], draft = this.draft, secret = state.secretDraft, editing = state.editingSecret;
    if (!await confirmAction({ title: `删除 ${config.label} 密钥`, trigger: "#deleteSecretBtn", message: `删除已保存的 ${config.label} API 密钥？删除后，依赖该密钥的服务需重新配置。${status?.environment_override ? "环境变量提供的密钥仍会生效。" : ""}`, action: "删除密钥" })) return;
    if (this.apiProvider !== provider || this.apiState(provider) !== state || this.saved.api_status[provider] !== status || this.draft !== draft || state.secretDraft !== secret || state.editingSecret !== editing) return toast("密钥设置已变化，请重新操作");
    state.savingSecret = true;
    try {
      const data = await api(`/api/settings/secrets/${provider}`, { method: "DELETE" });
      toast(`${config.label} 密钥已删除`);
      this.applySecretResponse(data, provider);
      if (state.secretDraft === secret && state.editingSecret === editing) {
        state.secretDraft = "";
        state.editingSecret = false;
      }
    } finally {
      state.savingSecret = false;
      if (provider === this.apiProvider) this.renderTranslation();
    }
  }
  async pickDatabase() {
    const data = await api("/api/settings/database/pick", { method: "POST" });
    this.draft.database.configured_path = data.path;
    this.dirty = true;
    this.render();
  }
  async toggleSource(key, visible) {
    if (!this.saved) await this.load(false);
    if (!this.saved) throw new Error("请先读取信源设置");
    return this.changeSources((hidden) => {
      visible ? hidden.delete(key) : hidden.add(key);
    });
  }
  async toggleSourceGroup(key) {
    if (!this.saved) await this.load(false);
    if (!this.saved) throw new Error("请先读取信源设置");
    return this.changeSources((hidden) => {
      const items =
        this.saved.feed_source_subkeys.groups.find((g) => g.source_key === key)
          ?.items || [];
      const enable = items.every((item) => hidden.has(item.key));
      for (const item of items)
        enable ? hidden.delete(item.key) : hidden.add(item.key);
    });
  }
  async changeSources(change) {
    // Serialize writes so quickly toggled sources cannot overwrite one another.
    const operation = async () => {
      const hidden = new Set(this.saved.feed.hidden_source_subkeys || []);
      change(hidden);
      const data = await api("/api/settings", {
        method: "PUT",
        body: {
          llm: this.saved.llm,
          agent: this.saved.agent,
          feed: { hidden_source_subkeys: [...hidden] },
        },
      });
      data.keyboard = keyboard.snapshot();
      this.saved = data;
      if (!this.dirty) this.draft = clone(data);
      this.onSources();
      this.onSaved();
    };
    this.sourceWrite = (this.sourceWrite || Promise.resolve())
      .catch(() => {})
      .then(operation);
    try {
      await this.sourceWrite;
    } catch (error) {
      this.onSources();
      throw error;
    }
  }
  async loadReleaseNotes() {
    try {
      const data = await api("/api/release-notes");
      $("#releaseNotes").textContent =
        data.items
          .map(
            (item) =>
              `${item.version || item.title || ""}\n${item.body || (item.lines || item.items || []).join("\n")}`,
          )
          .join("\n\n") || "暂无版本记录";
    } catch (error) {
      $("#releaseNotes").textContent = errorText(error);
    }
  }
}
