import { selectField as select } from "./form-controls.js";
import { confirmAction } from "./confirm-dialog.js";
import { Accordion } from "./accordion.js";
import { settingsHelpHTML } from "./settings-help.js?v=0.5.6";
import { CollectionEnvironment } from "./collection-environment.js?v=0.5.6";
import { $, $$, api, escapeHTML as e, errorText, toast, busy, singlePane, drawerNavigation, formatBytes } from "./ui.js";

import { UpdatePanel } from "./update-panel.js?v=0.3.0";
import { visibilityText } from "./summary-panel.js?v=0.5.4";
import { classificationStatus } from "./classification-progress.js?v=translation-routing-1";
import { ScheduleObserver } from "./schedule-observer.js?v=0.5.0";
import { defaultReportWindows, windowError } from "./report-windows.js";
import { keyboard, shortcutActions, defaultShortcuts, normalizeShortcuts, displayKey, shortcutKeyFromEvent } from "./keyboard-shortcuts.js";

const clone = (value) => structuredClone(value);
const reportModelFeatures = {reports:"日报（早／午／晚）", reports_periodic:"周报／月报"};
const modelFeatures = {chat:"Chat", topics:"主题追踪", summaries:"新闻总结", ...reportModelFeatures};
const testResultStatus = (text) => ({SUCCESS:"success", PASS:"success", FAILED:"failed", FAIL:"failed", RUNNING:"running"})[/^\[(\w+)\]/.exec(text)?.[1]] || "ready";
function updateTestResult(terminal, text, hideEmpty = false) {
  if (!terminal) return;
  terminal.textContent = text;
  terminal.dataset.status = testResultStatus(text);
  terminal.hidden = hideEmpty && !text;
}
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

export class SettingsPanel {
  constructor({ onSaved, onSources, onSourceDeleted, onOpen, onViewChange, selects, onClassificationRefresh, onClassificationRetry }) {
    this.onOpen = onOpen;
    this.onViewChange = onViewChange;
    this.onSaved = onSaved;
    this.onSources = onSources;
    this.onSourceDeleted = onSourceDeleted;
    this.onClassificationRefresh = onClassificationRefresh;
    this.onClassificationRetry = onClassificationRetry;
    this.open = false;
    this.dirty = false;
    this.section = "system";
    this.helpAccordion = new Accordion($("#settingsContent"));
    this.collectionEnvironment = new CollectionEnvironment();
    this.schedules = new ScheduleObserver({
      isVisible: () => this.open && this.section === "schedules" && (!singlePane.matches || $("#feedView").classList.contains("settings-detail-active")),
      selects,
      getTimes: () => this.draft?.report_schedule,
      getWindows: () => this.draft?.report_windows,
      onTimeChange: (kind, value) => {
        this.draft.report_schedule ||= this.payload().report_schedule;
        this.draft.report_schedule[kind] = value;
        this.status("有未保存的更改");
      },
      onWindowChange: (kind, window) => {
        this.draft.report_windows ||= clone(defaultReportWindows);
        this.draft.report_windows[kind] = window;
        this.status("有未保存的更改");
      },
      onCancelEdit: () => {
        this.draft.report_schedule = clone(this.payload(this.saved).report_schedule);
        this.draft.report_windows = clone(this.payload(this.saved).report_windows);
        this.status("有未保存的更改");
      },
    });
    this.catalogs = new Map();
    this.modelStates = new Map(); this.modelAdvanced = new Map();
    const refreshPi = () => { if (!document.hidden) this.refreshPiCatalog(); };
    window.addEventListener("focus", refreshPi);
    document.addEventListener("visibilitychange", refreshPi);
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
    $("#settingsContent").addEventListener("toggle", event => {
      if (event.target.matches("[data-model-advanced]")) this.modelAdvanced.set(event.target.dataset.feature,event.target.open);
    }, true);
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
        if (this.open && !this.saved) this.renderLoadState(true);
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
      this.renderLoadState();
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
    // Help remains available even when reading configuration fails.
    if (section === "help" && !$("#settingsHelp")) {
      $("#settingsContent").insertAdjacentHTML("beforeend", this.helpHTML());
    }
    const loadMessage = $("#settingsContent > .list-message");
    if (loadMessage) loadMessage.hidden = section === "help";
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
    if (changed && section === "models" && this.draft) this.renderModels();
    if (section === "pipeline" && this.saved) {
      this.renderCollectionServices();
      void this.collectionEnvironment.load();
    }
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
    if (section === "models") this.refreshPiCatalog();
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
    this.schedules.editing = false;
    this.apiStates.clear();
    this.modelStates.clear();
    this.render();
    this.status("已恢复已保存的配置");
  }
  payload(d = this.draft) {
    return {
      collection: clone(d.collection),
      llm: clone(d.llm),
      api_models: clone(d.api_models || { jev: { model: "" } }),
      classification: clone(d.classification || { enabled: true, concurrency: 8, unread_only: false }),
      feed: clone(d.feed),
      agent: clone(d.agent),
      feature_models: clone(d.feature_models || {}),
      report_schedule: clone(d.report_schedule || {morning:"08:30",noon:"14:30",evening:"21:30",weekly:"08:30",monthly:"08:30"}),
      report_windows: clone(d.report_windows || defaultReportWindows),
      news_summary: clone(d.news_summary || { max_days: 20, max_items: 6000 }),
      database: { path: d.database.configured_path || "" },
    };
  }
  modelPaths(feature = "chat") {
    return feature === "chat" ? {runtime:"agent.runtime",codex:"agent.codex",pi:"llm.pi_chat"}
      : {runtime:`feature_models.${feature}.runtime`,codex:`feature_models.${feature}.codex`,pi:`feature_models.${feature}.pi`};
  }
  modelConfig(feature = "chat") {
    if (feature === "chat") return {runtime:this.draft.agent.runtime,codex:this.draft.agent.codex,pi:this.draft.llm.pi_chat};
    this.draft.feature_models ||= {};
    return this.draft.feature_models[feature] ||= clone(feature.startsWith("reports_") ? this.modelConfig("reports")
      : {runtime:this.draft.agent.runtime,codex:this.draft.agent.codex,pi:this.draft.llm.pi_chat});
  }
  modelState(feature) {
    if (!this.modelStates.has(feature)) this.modelStates.set(feature, {connectionSequence:0, catalogSequence:0, connectionResult:"", catalogNotice:"", testing:false});
    return this.modelStates.get(feature);
  }
  modelId(feature, control) { return feature === "chat" ? control : `${control}-${feature}`; }
  modelElement(feature, control) { return document.getElementById(this.modelId(feature, control)); }
  modelDraft(feature) { return clone(this.modelConfig(feature)); }
  invalidateModelTest(feature) {
    const state = this.modelState(feature);
    state.connectionSequence++; state.connectionResult = "";
    const terminal = this.modelElement(feature, "connTestTerminal");
    updateTestResult(terminal, "", true);
  }
  updateDraft(event) {
    const input = event.target;
    if (input.name?.startsWith("collection.")) {
      set(this.draft, input.name, input.type === "checkbox" ? input.checked : input.type === "number"
        ? input.valueAsNumber : input.value.split(/[，,、\s]+/).filter(Boolean));
      this.status("有未保存的更改");
      return;
    }
    if (input.name?.startsWith("keyboard.")) {
      set(this.draft, input.name, input.type === "checkbox" ? input.checked : input.value);
      this.status("有未保存的更改");
      $("#shortcutValidation").textContent = "";
      $$("#shortcutSettings [aria-invalid]").forEach(node => node.removeAttribute("aria-invalid"));
      return;
    }
    if (input.matches("[data-api-secret]")) { this.apiState(input.dataset.apiSecret).secretDraft = input.value; return; }
    if (!input.name || !this.draft) return;
    const feature = input.closest("[data-model-feature]")?.dataset.modelFeature;
    const paths = this.modelPaths(feature), config = this.modelConfig(feature), state = feature ? this.modelState(feature) : null;
    const value = input.type === "checkbox" ? input.checked : input.type === "number" || input.name === "agent.session_ttl_hours"
      ? Number(input.value) : input.value;
    const unchanged = Object.is(get(this.draft, input.name), value);
    // Numeric fields emit change again on blur, after input already updated the draft.
    if (unchanged && (input.name.startsWith("news_summary.") || event.type !== "change")) return;
    set(this.draft, input.name, value);
    if (input.name === "classification.enabled") $("#classificationOptions").disabled = !value;
    const apiConfig = this.draft.api_providers.find(config => config.model_path === input.name);
    if (apiConfig) {
      const provider = apiConfig.id, state = this.apiState(provider);
      state.sequence++;
      state.notice = "模型已变化，可以测试当前选择。";
      state.testResult = "[READY] 模型已变化，请重新测试当前选择。";
      updateTestResult(this.apiElement(provider, "translationTestTerminal"), state.testResult);
      this.apiElement(provider, "translationStatus").textContent = state.notice;
      this.apiElement(provider, "translationTestBtn").disabled = !this.canTestApi(provider);
      this.syncModelFields(input, this.translationOptions(provider));
    }
    if (apiConfig || input.name.startsWith("llm.translation.")) this.updateTranslationRoute();
    if (input.name === paths.codex+".model") {
      this.syncModelFields(input, this.codexOptions(feature));
      if (!unchanged) {
        config.codex.reasoning_effort = "";
        this.renderReasoning(feature);
      }
    }
    if (input.name.startsWith("news_summary.")) this.loadCatchupScope(250);
    if (feature) this.invalidateModelTest(feature);
    if ([paths.codex+".provider", paths.codex+".profile"].includes(input.name)) {
      state.catalogNotice = "";
      state.catalogSequence++;
      config.codex.reasoning_effort = "";
      this.renderCodexSelection(feature);
    }
    if (input.name === paths.pi+".provider" && event.type === "change") {
      config.pi.model = "";
      delete config.pi.reasoning_effort;
      this.renderModels(feature);
      $(`[data-select-trigger="${paths.pi}.provider"]`)?.focus({preventScroll:true});
    }
    if (input.name === paths.pi+".model" && !unchanged) {
      delete config.pi.reasoning_effort;
      this.renderReasoning(feature);
      this.renderModelNotice(feature);
    }
    if (input.name === paths.pi+".reasoning_effort") this.renderModelNotice(feature);
    if (input.name === paths.runtime) {
      state.catalogNotice = ""; state.catalogSequence++; this.renderModels(feature);
      $(`[data-select-trigger="${paths.runtime}"]`)?.focus({preventScroll:true});
      this.refreshPiCatalog();
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
  catalogKey(runtime, config, feature = "chat") {
    return JSON.stringify(runtime === "codex" ? [feature, runtime, config.provider || "", config.profile || ""] : [feature, runtime]);
  }
  rememberModelCatalogs(data) {
    for (const feature of Object.keys(modelFeatures)) {
      const config = feature === "chat" ? data.agent : data.feature_models?.[feature];
      if (!config) continue;
      for (const runtime of ["codex", "pi"]) {
        const catalog = runtime === "pi" ? data.model_catalogs?.pi_chat
          : feature === "chat" ? data.model_catalogs?.codex : data.feature_model_catalogs?.[feature];
        const key = this.catalogKey(runtime, config.codex, feature);
        if (catalog && (runtime === "pi" || catalog.options?.length || !this.catalogs.has(key))) this.catalogs.set(key, clone(catalog));
      }
    }
  }
  modelCatalog(feature = "chat", runtime = this.modelConfig(feature).runtime) {
    return this.catalogs.get(this.catalogKey(runtime, this.modelConfig(feature).codex, feature)) || {};
  }
  codexOptions(feature) { return this.modelCatalog(feature, "codex").options?.filter(option => option.source !== "saved") || []; }
  piOptions(feature) { return this.modelCatalog(feature, "pi").model_options_by_provider?.[this.modelConfig(feature).pi.provider] || []; }
  canTestModel(feature) {
    const config = this.modelConfig(feature), model = this.piOptions(feature).find(option => option.value === config.pi.model);
    return !this.modelState(feature).testing && (config.runtime === "codex" || !!model && (!config.pi.reasoning_effort || model.reasoning_efforts?.includes(config.pi.reasoning_effort)));
  }
  modelNotice(feature) {
    const config = this.modelConfig(feature), catalog = this.modelCatalog(feature);
    const model = this.piOptions(feature).find(option => option.value === config.pi.model);
    const missing = config.runtime === "pi" && config.pi.provider && !model;
    const unsupported = config.runtime === "pi" && model && config.pi.reasoning_effort && !model.reasoning_efforts?.includes(config.pi.reasoning_effort);
    return [this.modelState(feature).catalogNotice || catalog.error || catalog.notice,
      missing ? `已保存或选择的 ${config.pi.provider} / ${config.pi.model || "未选模型"} 当前不在可用目录中，请重新登录或选择模型。` : "",
      unsupported ? `当前模型已不支持保存的推理强度 ${config.pi.reasoning_effort}，请重新选择。` : ""].filter(Boolean).join(" ");
  }
  renderModelNotice(feature) {
    const notice = this.modelElement(feature, "modelsNotice");
    if (!notice) return;
    notice.textContent = this.modelNotice(feature); notice.hidden = !notice.textContent;
    this.modelElement(feature, "testConnectionBtn").disabled = !this.canTestModel(feature);
  }
  refreshPiCatalog() {
    if (!this.open || this.section !== "models" || !this.draft) return;
    for (const feature of Object.keys(modelFeatures)) {
      if (!this.modelState(feature).testing && this.modelConfig(feature).runtime === "pi")
        void this.refreshModels({feature, automatic:true}).catch(() => {});
    }
  }
  renderCodexSelection(feature) {
    const path = this.modelPaths(feature).codex, current = $(`select[name="${path}.model"]`);
    if (!current) return;
    const options = this.codexOptions(feature), value = this.modelConfig(feature).codex.model;
    const container = document.createElement("div");
    container.innerHTML = select("模型", path+".model", value, options, "沿用本机默认");
    current.closest(".field-item").replaceWith(container.firstElementChild);
    $(`input[name="${path}.model"]`).value = options.some(option => option.value === value) ? "" : value;
    this.renderModelNotice(feature);
    this.renderReasoning(feature);
  }
  renderReasoning(feature) {
    const runtime = this.modelConfig(feature).runtime, path = this.modelPaths(feature)[runtime], current = $(`select[name="${path}.reasoning_effort"]`);
    if (!current) return;
    const config = this.modelConfig(feature)[runtime];
    const efforts = (runtime === "pi" ? this.piOptions(feature) : this.codexOptions(feature)).find(option => option.value === config.model)?.reasoning_efforts || [];
    const container = document.createElement("div");
    container.innerHTML = select("推理强度", path+".reasoning_effort", config.reasoning_effort || "", efforts, "沿用默认", {preserveUnknown:runtime !== "pi"});
    if (runtime === "pi") container.querySelector("select").disabled = !efforts.length;
    current.closest(".field-item").replaceWith(container.firstElementChild);
    this.selects.refresh();
  }
  render() {
    if (!this.draft) return;
    const d = this.draft;
    this.selects.close(false);
    $("#settingsContent").innerHTML =
      `<div class="settings-sheet" data-section="models" id="modelSettings"></div>
      <div class="settings-sheet" data-section="schedules" id="scheduleSettings"></div>
      ${this.helpHTML()}
      <section class="settings-section-card" data-section="shortcuts" id="shortcutSettings"></section>
      <div class="settings-sheet" data-section="deepseek" id="translationSettings"></div>
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
      <p class="muted">下方 Jev 配置同时用于新闻分类、主题归属和重要进展筛选。</p>
      <button class="btn-secondary" id="classificationApiSettingsBtn" type="button">管理 Jev API</button></section>
      <section class="settings-section-card" data-section="classification" id="classificationApiSettings" data-api-provider="jev" aria-labelledby="apiTitle-jev"></section>
      <section class="settings-section-card" data-section="catchup"><div class="section-card-title"><span>积压新闻总结范围</span></div>
      <div class="form-group-grid">${field("最多自然日（含起止）", "news_summary.max_days", d.news_summary?.max_days ?? 20, 'type="number" min="1" max="9007199254740991" step="1" required')}${field("最多未读条数", "news_summary.max_items", d.news_summary?.max_items ?? 6000, 'type="number" min="1" max="9007199254740991" step="1" required')}</div>
      <div class="catchup-scope-preview" id="settingsCatchupScope" role="status">正在读取当前生效范围…</div>
      <button class="btn-secondary" id="resetCatchupDefaultsBtn" type="button">恢复默认范围（20 天 / 6000 条）</button></section>
      ${this.collectionEnvironment.html()}
      <section class="settings-section-card" data-section="pipeline" id="collectionServices" aria-labelledby="collectionServicesTitle"></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>自动采集新闻</span></div>
      <label class="settings-toggle settings-switch"><span>启用定时采集</span><input type="checkbox" role="switch" name="collection.enabled" ${d.collection?.enabled !== false ? "checked" : ""}></label>
      <p class="muted">北京时间；后台服务运行时执行，关闭网页不影响。保存后生效。暂停定时采集仍可手动采集。</p>
      ${field("每天采集时间", "collection.daily", (d.collection?.daily || []).join("、"), 'placeholder="00:01、04:00、08:00"')}
      ${field("A 股交易日增加", "collection.trading", (d.collection?.trading || []).join("、"), 'placeholder="09:00、10:00、14:30"')}
      <p class="muted">使用 24 小时格式 HH:MM，以顿号、逗号或空格分隔；同一时间只执行一次。</p>
      ${field("新闻翻译并发上限", "collection.translation_concurrency", d.collection?.translation_concurrency ?? 4, 'type="number" min="1" max="32" step="1" required')}
      <p class="muted">默认 4，可设置 1–32。保存后应用于后续翻译请求；正在执行的请求会继续完成。同一轮采集结束且全部翻译成功后统一入库。</p>
      <p id="settingsCollectionStatus" role="status"></p>
      <div class="button-row"><button class="btn-secondary" id="settingsCollectBtn" type="button">立即采集</button></div></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>固定采集来源</span></div>
      <p class="muted">当前版本使用以下固定栏目与账号，通过本机 opencli 采集。采集与翻译均在后台运行；整批完成翻译后统一进入新闻库。</p><ul id="settingsCollectionSources"></ul></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>新闻流显示与数据清理</span></div><p class="muted">勾选只控制新闻流显示，不启停采集。删除会清除该来源当前数据，后续新新闻仍会采集。</p><div id="settingsSources">${this.sourceHTML()}</div></section>
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
    this.updateCollectionStatus(this.collectionStatus);
    this.collectionEnvironment.render();
    this.schedules.mount($("#scheduleSettings"));
    this.selectSection(this.section, { reveal: false });
    $("#releaseNotes").closest("details").ontoggle = (event) => {
      if (event.target.open) this.loadReleaseNotes();
    };
    this.status(this.dirty ? "有未保存的更改" : "配置已加载");
  }
  helpHTML() {
    return `<div class="settings-sheet" data-section="help" id="settingsHelp">${settingsHelpHTML(this.helpAccordion)}</div>`;
  }
  renderLoadState(failed = false) {
    let message = $("#settingsContent > .list-message");
    if (!message) {
      message = document.createElement("div");
      message.className = "list-message";
      message.setAttribute("role", "status");
      $("#settingsContent").prepend(message);
    }
    message.innerHTML = failed
      ? '设置读取失败<button class="btn-secondary" id="retrySettingsBtn" type="button">重试</button>'
      : '正在读取配置…';
    message.hidden = this.section === "help";
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
  translationOptions(provider) {
    if (!this.draft.api_status?.[provider]?.configured) return [];
    const key = provider === "deepseek" ? "translation" : provider;
    return (this.apiState(provider).catalog || this.draft.model_catalogs?.[key])?.options?.filter(option => option.source !== "saved") || [];
  }
  apiState(provider) {
    if (!this.apiStates.has(provider)) this.apiStates.set(provider, {
      secretDraft: "", editingSecret: false, catalog: null, notice: "", testResult: "", sequence: 0,
      testing: false, refreshing: false, savingSecret: false,
    });
    return this.apiStates.get(provider);
  }
  apiConfig(provider) {
    return this.draft.api_providers.find(config => config.id === provider);
  }
  apiId(provider, control) { return provider === "deepseek" ? control : `${provider}-${control}`; }
  apiElement(provider, control) { return document.getElementById(this.apiId(provider, control)); }
  canTestApi(provider) {
    const config = this.apiConfig(provider);
    return !!this.draft.api_status?.[provider]?.configured && !this.apiState(provider).testing
      && (provider === "deepseek" || !!(get(this.draft, config.model_path) || config.default_model));
  }
  translationOrder(settings = this.draft) {
    const config = settings.llm.translation, preferred = config.provider || "deepseek";
    const others = settings.api_providers.filter(p => p.translation_base_url && p.id !== preferred).map(p => p.id);
    return [preferred, ...(config.fallback_enabled !== false ? others : []),
      ...(config.pi_fallback_enabled !== false ? ["pi"] : [])];
  }
  translationRoutingHTML() {
    const config = this.draft.llm.translation;
    return `<div class="section-card-title"><span>正文翻译</span></div>
      <fieldset class="field-options" aria-label="首选翻译服务"><legend class="field-label">首选翻译服务</legend>${this.draft.api_providers.filter(p => p.translation_base_url).map(p => `<label class="settings-toggle"><input type="radio" name="llm.translation.provider" value="${p.id}" ${config.provider === p.id ? "checked" : ""}><span>${e(p.label)}</span></label>`).join("")}</fieldset>
      <label class="settings-toggle settings-switch"><span>失败后切换其他 API</span><input type="checkbox" role="switch" name="llm.translation.fallback_enabled" ${config.fallback_enabled !== false ? "checked" : ""}></label>
      <label class="settings-toggle settings-switch"><span>API 均失败后尝试 Pi</span><input type="checkbox" role="switch" name="llm.translation.pi_fallback_enabled" ${config.pi_fallback_enabled !== false ? "checked" : ""}></label>
      <p class="muted" id="translationRouteDescription" role="status"></p>
      <p class="muted">保存后用于新翻译任务；已有译文保持不变，可在正文点击「重新翻译」。各服务的翻译模型在下方设置。Pi 后备使用 Chat 中保存的 Pi 服务商与模型。</p>`;
  }
  updateTranslationRoute() {
    const description = $("#translationRouteDescription");
    if (!description) return;
    const order = this.translationOrder(), labels = {pi:"Pi", ...Object.fromEntries(this.draft.api_providers.map(p => [p.id, p.label]))};
    const missing = order.filter(p => p !== "pi" && (!this.draft.api_status?.[p]?.configured || (p !== "deepseek" && !get(this.draft, this.apiConfig(p).model_path)?.trim())));
    description.textContent = `调用顺序：${order.map(p => labels[p]).join(" → ")}。${missing.length ? `${missing.map(p => labels[p]).join("、")} 尚未配置密钥或模型，无法调用。` : ""}`;
  }
  apiUsageHTML() {
    const saved = this.saved;
    const legacy = { runtime: saved.agent.runtime, codex: saved.agent.codex, pi: saved.llm.pi_chat };
    const features = [
      ["chat", "Chat 与对话归档", "失败保留对话，可重试；不会自动切换服务。"],
      ["topics", "主题研究与整理", "失败保留已有内容，可重试；新闻归属与重要进展筛选另由 Jev 执行。"],
      ["summaries", "积压新闻总结", "失败不保存成功结果、不标记新闻已读，可重新生成。"],
      ...Object.entries(reportModelFeatures).map(([key, label]) => [key, label, ""]),
    ];
    return `<div class="section-card-title"><span id="apiUsageTitle">功能与后备措施</span></div>
      <p class="muted">正文翻译按上方设置调用；Jev 在「新闻分类」管理。以下为其他功能已保存的模型配置，修改并保存后更新。</p>
      <div class="section-stack">${features.map(([key, label, fallback]) => {
        const config = key === "chat" ? legacy : saved.feature_models?.[key] || legacy;
        const model = config.runtime === "codex" ? `Codex CLI · ${config.codex?.model || "本机默认模型"}${config.codex?.provider ? `（${config.codex.provider}）` : ""}`
          : `Pi · ${config.pi?.provider && config.pi?.model ? `${config.pi.provider} / ${config.pi.model}` : "尚未选择服务商与模型"}`;
        return `<div class="field-item" data-api-usage="${key}"><span class="field-label">${label}</span><span>${e(model)}</span>${fallback ? `<p class="muted">${fallback}</p>` : ""}</div>`;
      }).join("")}</div>
      <p class="muted">报告失败自动重试，最多尝试 3 次，之后可手动重试；重试沿用该报告创建时的模型。</p>
      <p class="muted">正文翻译的 Pi 后备仅在启用时使用，即使 Chat 当前选择 Codex，也读取其中保存的 Pi 配置。上述其他功能不会自动切换服务商；已有资料仍可阅读。</p>
      <div class="button-row"><button class="btn-secondary" id="apiFeatureSettingsBtn" type="button">管理功能模型</button></div>`;
  }
  renderTranslation(provider) {
    const root = $("#translationSettings");
    if (!root) return;
    if (provider) {
      const panel = $(`[data-api-provider="${provider}"]`, $("#settingsContent"));
      if (!panel) return;
      const active = panel.contains(document.activeElement) ? document.activeElement : null;
      const focusSelector = active?.id ? `#${CSS.escape(active.id)}` : active?.name ? `${active.tagName.toLowerCase()}[name="${CSS.escape(active.name)}"]` : null;
      const selection = active?.tagName === "INPUT" && active.selectionStart !== null ? [active.selectionStart, active.selectionEnd] : null;
      panel.innerHTML = this.apiProviderHTML(provider);
      this.selects.refresh(panel);
      if (active) {
        const focus = (focusSelector && $(focusSelector, panel)) || this.apiElement(provider, "editSecretBtn") || $(`#${provider}Secret`, panel);
        focus?.focus({ preventScroll: true });
        if (selection && focus?.tagName === "INPUT") focus.setSelectionRange(...selection);
      }
      return;
    }
    root.innerHTML = `<section class="settings-section-card" id="translationRouting">${this.translationRoutingHTML()}</section>`
      + this.draft.api_providers.filter(config => config.id !== "jev").map(config => `<section class="settings-section-card" data-api-provider="${e(config.id)}" aria-labelledby="apiTitle-${e(config.id)}">${this.apiProviderHTML(config.id)}</section>`).join("")
      + `<section class="settings-section-card" id="apiUsage" aria-labelledby="apiUsageTitle">${this.apiUsageHTML()}</section>`;
    this.selects.refresh(root);
    this.renderTranslation("jev");
    this.updateTranslationRoute();
  }
  apiProviderHTML(provider) {
    const d = this.draft, config = this.apiConfig(provider), state = this.apiState(provider);
    const service = d.api_status?.[provider] || {};
    const configured = service.configured, editing = !configured || state.editingSecret;
    const options = this.translationOptions(provider);
    const model = get(d, config.model_path) || "";
    const custom = options.some(option => option.value === model) ? "" : model;
    const id = control => this.apiId(provider, control);
    const action = control => `id="${id(control)}" data-api-action="${control}"`;
    return `<div class="section-card-title"><span id="apiTitle-${provider}" tabindex="-1">${e(config.label)}</span><span class="muted">${configured ? "已配置密钥" : "未配置密钥"}</span></div>
      <div class="field-item"><span class="field-label">使用位置</span><p class="muted">${e(config.description)}</p></div>
      <div class="field-item"><span class="field-label">失效后</span><p class="muted">${e(config.fallback || "失败后可重试。")}</p></div>
      ${config.docs_url ? `<p class="muted"><a href="${e(config.docs_url)}" target="_blank" rel="noopener noreferrer">${e(config.label)} API 文档</a></p>` : ""}
      <div class="translation-model-row">${select(config.translation_base_url ? "翻译模型" : "模型", config.model_path, model, options, configured ? (config.default_model ? `使用默认模型（${config.default_model}）` : provider !== "deepseek" ? "请选择模型" : "使用默认模型") : "请先配置 API 密钥")}<button class="btn-secondary" ${action("translationModelsBtn")} type="button" ${configured ? "" : "hidden"} ${state.refreshing ? "disabled" : ""}>${state.refreshing ? "正在获取…" : "获取模型"}</button></div>
      ${field("自定义模型（可选）", config.model_path, custom, 'maxlength="120"')}
      <p class="muted" id="${id("translationStatus")}" role="status">${e(state.notice || (configured ? "选择目录中的模型，或填写自定义模型。" : "保存 API 密钥后可获取和测试模型；已填写的模型名称会保留。"))}</p>
      <div class="field-item"><label class="field-label" ${editing ? `for="${provider}Secret"` : ""}>API 密钥</label>
      ${configured ? `<code class="masked-secret">${e(service.masked_key || "••••••••")}</code>` : '<p class="muted">尚未配置密钥</p>'}
      ${service.environment_override ? `<p class="muted">当前由 ${e(config.secret_name)} 环境变量提供；保存和删除仅影响本地钥匙串，环境变量仍优先生效。</p>` : ""}
      ${editing ? `<input class="field-input" id="${provider}Secret" data-api-secret="${provider}" type="password" autocomplete="new-password" value="${e(state.secretDraft)}" placeholder="输入新的 API 密钥" ${state.savingSecret ? "disabled" : ""}><div class="button-row"><button class="btn-secondary" ${action("saveSecretBtn")} type="button" ${state.savingSecret ? "disabled" : ""}>保存密钥</button>${configured ? `<button class="btn-secondary" ${action("cancelSecretBtn")} type="button">取消</button>` : ""}</div>` : `<div class="button-row"><button class="btn-secondary" ${action("editSecretBtn")} type="button">更新密钥</button><button class="reader-action-btn danger" ${action("deleteSecretBtn")} type="button" ${state.savingSecret ? "disabled" : ""}>删除密钥</button></div>`}</div>
      <div class="connection-test-section"><div><strong>模型连接测试</strong><p class="muted">使用已保存的密钥和当前选择的模型测试，无需先保存模型。</p></div><button class="btn-primary" ${action("translationTestBtn")} type="button" ${this.canTestApi(provider) ? "" : "disabled"}>${state.testing ? "正在测试…" : "测试模型"}</button><div class="terminal-box" id="${id("translationTestTerminal")}" data-status="${testResultStatus(state.testResult)}" role="status">${e(state.testResult || "[READY] 连接测试器就绪 · 点击上方按钮发起探测")}</div></div>`;
  }
  async refreshTranslationModels(provider) {
    const state = this.apiState(provider);
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
      if (state === this.apiState(provider)) this.renderTranslation(provider);
    }
  }
  async testTranslation(provider) {
    const state = this.apiState(provider), model = get(this.draft, this.apiConfig(provider).model_path) || "";
    if (state.testing) return;
    const seq = state.sequence;
    state.testing = true;
    state.testResult = "[RUNNING] 正在测试当前模型…";
    updateTestResult(this.apiElement(provider, "translationTestTerminal"), state.testResult);
    try {
      const data = await api(`/api/settings/apis/${provider}/test`, { method: "POST", body: { model } });
      if (state === this.apiState(provider) && seq === state.sequence) state.testResult = `[PASS] ${data.message} · ${data.model} · ${data.elapsed_ms}ms`;
    } catch (error) {
      if (state === this.apiState(provider) && seq === state.sequence) state.testResult = `[FAIL] ${errorText(error)}`;
    } finally {
      state.testing = false;
      if (state === this.apiState(provider)) {
        updateTestResult(this.apiElement(provider, "translationTestTerminal"), state.testResult);
        this.apiElement(provider, "translationTestBtn").disabled = !this.canTestApi(provider);
        this.apiElement(provider, "translationTestBtn").textContent = "测试模型";
      }
    }
  }
  renderModels(feature) {
    const host = $("#modelSettings");
    if (!host) return;
    if (!feature) {
      if (!host.children.length) host.innerHTML = Object.entries(modelFeatures).map(([key,label]) =>
        `<section class="settings-section-card" data-model-feature="${key}" aria-label="${label}"></section>`).join("");
      for (const key of Object.keys(modelFeatures)) this.renderModels(key);
      return;
    }
    const panel = $(`[data-model-feature="${feature}"]`, host);
    if (!panel) return;
    const d = this.modelConfig(feature), paths = this.modelPaths(feature), codex = d.runtime === "codex", state = this.modelState(feature);
    const catalog = this.modelCatalog(feature), options = this.codexOptions(feature);
    const model = options.find(x=>x.value === d.codex.model), custom = model ? "" : d.codex.model;
    const notice = this.modelNotice(feature), piModel = this.piOptions(feature).find(option => option.value === d.pi.model);
    const id = control => this.modelId(feature, control);
    const action = control => `id="${id(control)}" data-model-action="${control}"`;
    panel.innerHTML = `<div class="section-card-title"><span>${modelFeatures[feature]}</span></div><div class="form-group-grid">
      ${select("Runtime", paths.runtime, d.runtime, [{value:"codex",label:"Codex CLI"},{value:"pi",label:"Pi SDK / OAR"}], null)}</div>
      ${codex ? `<div class="form-group-grid">${select("模型",paths.codex+".model",d.codex.model,options,"沿用本机默认")}${select("推理强度",paths.codex+".reasoning_effort",d.codex.reasoning_effort,model?.reasoning_efforts || [],"沿用默认")}</div>
      <details id="${id("modelAdvanced")}" data-model-advanced data-feature="${feature}" ${this.modelAdvanced.get(feature)?'open':''}><summary class="reader-tool-btn">高级设置</summary><div class="section-stack">${field("自定义模型",paths.codex+".model",custom,'maxlength="120"')}<div class="form-group-grid">${field("服务商覆盖",paths.codex+".provider",d.codex.provider)}${field("Profile",paths.codex+".profile",d.codex.profile)}</div></div></details>`
      : `<div class="form-group-grid">${select("服务商",paths.pi+".provider",d.pi.provider,catalog.provider_options || [],"请选择服务商",{preserveUnknown:false})}${select("模型",paths.pi+".model",d.pi.model,this.piOptions(feature),"请选择模型",{preserveUnknown:false})}${select("推理强度",paths.pi+".reasoning_effort",d.pi.reasoning_effort || "",piModel?.reasoning_efforts || [],"沿用默认",{preserveUnknown:false})}</div><p class="muted">Pi 使用自身的登录或环境配置。使用 Go 订阅时，请在 Pi 中登录 opencode-go；API 管理中的密钥仅用于正文翻译。</p>`}
      <div class="button-row"><button class="btn-secondary" ${action("refreshModelsBtn")} type="button" ${state.pendingCatalog?'disabled':''}>刷新模型目录</button><button class="btn-primary" ${action("testConnectionBtn")} type="button" ${this.canTestModel(feature)?'':'disabled'}>${state.testing?'测试中…':'运行连接测试'}</button></div>
      <p class="muted" id="${id("modelsNotice")}" role="status" ${notice?'':'hidden'}>${e(notice)}</p><div class="terminal-box" id="${id("connTestTerminal")}" data-status="${testResultStatus(state.connectionResult)}" role="status" ${state.connectionResult?'':'hidden'}>${e(state.connectionResult || "")}</div>
      ${feature === "chat" ? `<div class="form-group-grid">${select("临时会话保留时间","agent.session_ttl_hours",String(this.draft.agent.session_ttl_hours),[{value:"24",label:"24 小时"},{value:"72",label:"72 小时"}],null)}</div><button class="reader-action-btn danger" id="clearSessionsBtn" type="button">清空全部临时 Agent 会话</button>` : ""}`;
    if (!codex) $(`select[name="${paths.pi}.reasoning_effort"]`, panel).disabled = !piModel?.reasoning_efforts?.length;
    this.selects.refresh(panel);
  }
  updateCollectionStatus(data) {
    if (data) this.collectionStatus = data;
    const node = $("#settingsCollectionStatus");
    if (!node || !data) return;
    const time = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai",hour12:false}) : "暂无";
    node.textContent = `${data.running ? "采集中" : data.schedule.enabled ? "定时采集已启用" : "定时采集已暂停"} · 下次：${time(data.next_run_at)}${data.schedule_error ? " · 交易日历未就绪，常规采集正常" : ""}`;
    const button = $("#settingsCollectBtn");
    if (button) button.disabled = data.running;
    const statuses = {none:"尚未执行",pending:"待采集",running:"采集中",done:"成功",failed:"失败"};
    $("#settingsCollectionSources").innerHTML = data.sources.map(source => `<li>${e(source.label)} · ${e(statuses[source.status] || source.status)}${source.status === "done" ? `，新增 ${source.added} 条` : ""}${source.error ? `（${e(source.error)}）` : ""}</li>`).join("");
  }
  renderCollectionServices() {
    const panel = $("#collectionServices"), saved = this.saved;
    if (!panel || !saved) return;
    const order = this.translationOrder(saved);
    panel.innerHTML = `<div class="section-card-title"><span id="collectionServicesTitle">采集服务配置</span></div>
      <p class="muted">采集字段翻译与正文翻译共用以下已保存的服务配置，按顺序尝试。配置齐全不代表连接成功；可进入对应设置运行连接测试。</p>
      <div class="section-stack">${order.map((provider, index) => {
        const config = saved.api_providers.find(item => item.id === provider);
        const model = provider === "pi" ? saved.llm.pi_chat : get(saved, config.model_path);
        const label = provider === "pi" ? "Pi" : config.label;
        const missing = provider === "pi"
          ? (!model?.provider || !model?.model ? "未选择服务商或模型" : "已选择模型；登录授权与连通性需在模型设置中验证")
          : [!saved.api_status?.[provider]?.configured && "缺少 API 密钥", !model?.trim() && "未选择模型"].filter(Boolean).join("、") || "密钥与模型已配置，连接尚需测试";
        const selected = provider === "pi" ? [model?.provider, model?.model].filter(Boolean).join(" / ") : model;
        return `<div class="field-item" data-collection-service="${e(provider)}"><span class="field-label">${index ? "后备" : "首选"} · ${e(label)}</span><span>${e(selected || "尚未配置模型")}</span><span class="muted">${e(missing)}</span></div>`;
      }).join("")}</div>
      <div class="button-row"><button class="btn-secondary" id="collectionApiSettingsBtn" type="button">配置翻译 API</button><button class="btn-secondary" id="collectionModelSettingsBtn" type="button">配置 Pi 模型</button></div>
      <p class="muted">翻译服务不可用时，采集原文暂存，等待翻译完成后整批入库。</p>`;
  }
  sourceHTML() {
    const hidden = new Set(this.draft.feed.hidden_source_subkeys || []);
    return (
      (this.draft.feed_source_subkeys?.groups || [])
        .map(
          (group) =>
            `<fieldset class="settings-source-group"><legend>${e(group.label)}</legend>${group.items.map((item) => `<div class="sub-source-item"><label><input type="checkbox" data-setting-source="${e(item.key)}" ${hidden.has(item.key) ? "" : "checked"}><span>${e(item.label)}</span><span class="muted">${item.count}</span></label><button type="button" class="reader-action-btn danger" data-delete-source="${e(item.key)}" aria-label="删除信源 ${e(group.label)} · ${e(item.label)}">删除</button></div>`).join("")}</fieldset>`,
        )
        .join("") || '<p class="muted">新闻入库后，这里会显示可配置的信源。</p>'
    );
  }
  async deleteSource(button) {
    if (this.sourceDeletionPending) return;
    this.sourceDeletionPending = true;
    const key = button.dataset.deleteSource;
    const path = `/api/settings/sources/${encodeURIComponent(key)}`;
    const library = this.saved.database.path;
    const index = $$("[data-delete-source]").indexOf(button);
    button.textContent = "统计中…";
    try {
      const { source } = await api(path);
      if (!this.open || this.section !== "pipeline" || !button.isConnected || this.saved.database.path !== library) return;
      button.textContent = "删除";
      const counts = source.counts;
      const message = [
        `信源：${source.source_label} · ${source.label}`,
        `标识：${source.key}`,
        `新闻数目：${source.news_count.toLocaleString()} 条`,
        `尚未发布的采集内容：${source.pending_news_count || 0} 条`,
        `新闻日期：${source.first_date} 至 ${source.last_date}`,
        `数据大小：约 ${formatBytes(source.data_bytes)}（数据内容 ${formatBytes(source.database_bytes)}，缓存文件 ${formatBytes(source.file_bytes)}）`,
        `相关数据：${counts.article_details} 篇正文、${counts.article_ai} 篇译文、${source.note_count} 条想法、${counts.article_highlights} 处高亮、${counts.agent_sessions} 个临时会话。`,
        "",
        "将永久清除该信源在当前新闻库中的上述数据、阅读标记及关联任务。已删除的新闻不会重新导入，该信源后续的新新闻仍会正常收录。此操作无法撤销。",
        "已生成的报告和主题研究成果保留。其他信源共用的正文与缓存保留。",
        "数据大小按内容估算，数据库文件不一定立即缩小。",
      ].join("\n");
      if (!await confirmAction({title:"删除信源及全部新闻数据", message, action:"永久删除", trigger:button})) return;
      if (this.saved.database.path !== library) throw new Error("新闻库已变化，请重新确认");
      button.textContent = "删除中…";
      const data = await api(path, {method:"DELETE", body:{confirmation_token:source.confirmation_token}});
      // Update only source metadata. Model, path and source visibility drafts survive.
      this.saved.feed_source_subkeys = clone(data.feed_source_subkeys);
      this.draft.feed_source_subkeys = clone(data.feed_source_subkeys);
      if ($("#settingsSources")) {
        $("#settingsSources").innerHTML = this.sourceHTML();
        const buttons = $$("[data-delete-source]");
        (buttons[Math.min(index, buttons.length - 1)] || $("#settingsCollectBtn"))?.focus({preventScroll:true});
      }
      this.onSourceDeleted?.(data);
      this.onSources?.();
      this.onSaved?.();
      document.dispatchEvent(new Event("tasks:changed"));
      this.status(this.dirty ? "有未保存的更改" : "信源已删除");
      toast(data.warning || `已删除 ${source.label}，共 ${data.deleted.news_count.toLocaleString()} 条新闻`);
    } catch (error) {
      this.status(errorText(error), true);
      throw error;
    } finally {
      this.sourceDeletionPending = false;
      button.textContent = "删除";
    }
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
    if (button.dataset.deleteSource) {
      void busy(button, () => this.deleteSource(button));
      return;
    }
    const feature = button.closest("[data-model-feature]")?.dataset.modelFeature;
    if (feature && button.dataset.modelAction) {
      const actions = {refreshModelsBtn: () => this.refreshModels({feature}), testConnectionBtn: () => this.testConnection(feature)};
      if (actions[button.dataset.modelAction]) void busy(button, actions[button.dataset.modelAction]).finally(() => this.renderModelNotice(feature));
      return;
    }
    const provider = button.closest("[data-api-provider]")?.dataset.apiProvider;
    if (provider && button.dataset.apiAction) {
      const actions = {
        editSecretBtn: () => { this.apiState(provider).editingSecret = true; this.renderTranslation(provider); $(`#${provider}Secret`).focus(); },
        cancelSecretBtn: () => { this.apiState(provider).secretDraft = ""; this.apiState(provider).editingSecret = false; this.renderTranslation(provider); this.apiElement(provider, "editSecretBtn").focus(); },
        translationModelsBtn: () => this.refreshTranslationModels(provider),
        translationTestBtn: () => this.testTranslation(provider),
        saveSecretBtn: () => this.saveSecret(provider),
        deleteSecretBtn: () => this.deleteSecret(provider),
      };
      if (actions[button.dataset.apiAction]) void busy(button, actions[button.dataset.apiAction]).finally(() => {
        const test = this.apiElement(provider, "translationTestBtn");
        if (test) test.disabled = !this.canTestApi(provider);
      });
      return;
    }
    const actions = {
      checkCollectionEnvironmentBtn: () => this.collectionEnvironment.check("environment"),
      checkCollectionXBtn: () => this.collectionEnvironment.check("x"),
      collectionApiSettingsBtn: () => { this.selectSection("deepseek"); $("#settingsDetailTitle").focus({preventScroll:true}); },
      collectionModelSettingsBtn: () => { this.selectSection("models"); $("#settingsDetailTitle").focus({preventScroll:true}); },
      classificationRefreshBtn: () => this.onClassificationRefresh?.(),
      classificationRetryBtn: () => this.onClassificationRetry?.(),
      classificationApiSettingsBtn: () => {
        $("#apiTitle-jev").scrollIntoView({ block: "start" });
        $("#apiTitle-jev").focus({ preventScroll: true });
      },
      apiFeatureSettingsBtn: () => this.selectSection("models"),
      retryCatchupScopeBtn: () => this.loadCatchupScope(),
      resetCatchupDefaultsBtn: () => {
        this.draft.news_summary = { max_days: 20, max_items: 6000 };
        this.dirty = true;
        this.render();
      },
      retrySettingsBtn: () => this.load(),
      pickDatabaseBtn: () => this.pickDatabase(),
      settingsCollectBtn: async () => {
        await api("/api/collection", {method:"POST"});
        toast("采集任务已加入后台队列");
        this.updateCollectionStatus(await api("/api/collection"));
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
    const times = this.draft.report_schedule;
    if (times && !(times.morning < times.noon && times.noon < times.evening)) {
      this.selectSection("schedules");
      this.schedules.setEditing(true);
      this.status("早报时间须早于午报，午报时间须早于晚报", true);
      return;
    }
    const windows = this.draft.report_windows || defaultReportWindows;
    const invalidWindow = times && Object.entries(windows).map(([kind,window]) => [kind,windowError(window,times[kind])]).find(([,error]) => error);
    const reportChanged = ["report_schedule","report_windows"].some(key => JSON.stringify(this.payload()[key]) !== JSON.stringify(this.payload(this.saved)[key]));
    if (invalidWindow && reportChanged) {
      this.selectSection("schedules"); this.schedules.setEditing(true);
      const [kind,error] = invalidWindow;
      this.status(`${{morning:"早报",noon:"午报",evening:"晚报",weekly:"周报",monthly:"月报"}[kind]}${error}`, true);
      return;
    }
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
      this.status(invalid.name === "classification.concurrency" ? "分类并发上限必须是 1–32 的整数"
        : invalid.name === "collection.translation_concurrency" ? "新闻翻译并发上限必须是 1–32 的整数"
        : "总结范围必须填写有效的正整数", true);
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
      const usage = $("#apiUsage");
      if (usage) usage.innerHTML = this.apiUsageHTML();
      if (unchanged) {
        this.draft = clone(data);
        this.dirty = false;
        this.schedules.setEditing(false);
      }
      this.syncDirty();
      this.updateTranslationRoute();
      this.status(
        this.dirty ? "已保存提交的配置，仍有新的更改未保存" : "设置已保存",
      );
      if (!serverUnchanged) { this.onSources(); this.onSaved(); }
      if (this.section === "catchup") this.loadCatchupScope();
      if (this.section === "schedules") void this.schedules.refresh();
      toast("设置已保存");
    } catch (error) {
      this.status(errorText(error), true);
      throw error;
    }
  }
  async refreshModels({ feature = "chat", automatic = false } = {}) {
    const draft = this.modelDraft(feature), state = this.modelState(feature);
    const key = this.catalogKey(draft.runtime, draft.codex, feature);
    if (automatic && state.pendingCatalog?.key === key) return;
    const pending = state.pendingCatalog = {key};
    const seq = ++state.catalogSequence;
    const current = () => state === this.modelState(feature) && seq === state.catalogSequence &&
      key === this.catalogKey(this.modelConfig(feature).runtime, this.modelConfig(feature).codex, feature);
    const render = () => {
      const panel = $(`[data-model-feature="${feature}"]`), active = document.activeElement;
      const focused = panel?.contains(active) ? active.dataset.selectTrigger || active.id : "";
      this.renderModels(feature);
      if (focused) ($(`[data-select-trigger="${CSS.escape(focused)}"]`) || document.getElementById(focused))?.focus({preventScroll:true});
    };
    const button = this.modelElement(feature, "refreshModelsBtn");
    if (button) button.disabled = true;
    try {
      const data = await api("/api/settings/models", {method:"POST", body:draft});
      if (!current()) return;
      const unchanged = JSON.stringify(this.modelCatalog(feature)) === JSON.stringify(data.catalog);
      if (draft.runtime === "pi" || !data.catalog.error || data.catalog.options?.length || !this.catalogs.has(key))
        this.catalogs.set(key, clone(data.catalog));
      if (automatic && unchanged) return;
      state.catalogNotice = data.catalog.error || data.catalog.notice || "模型目录已刷新";
      this.invalidateModelTest(feature);
      render();
    } catch (error) {
      if (!current()) return;
      state.catalogNotice = errorText(error);
      if (draft.runtime === "pi") this.catalogs.set(key, {error:state.catalogNotice});
      this.invalidateModelTest(feature);
      render();
      throw error;
    } finally {
      if (state === this.modelState(feature) && state.pendingCatalog === pending) {
        state.pendingCatalog = null;
        const button = this.modelElement(feature, "refreshModelsBtn");
        if (button) button.disabled = false;
      }
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
  async testConnection(feature) {
    if (!this.canTestModel(feature)) return;
    const state = this.modelState(feature), draft = this.modelDraft(feature), fingerprint = JSON.stringify(draft), seq = ++state.connectionSequence;
    state.testing = true;
    const selected = draft[draft.runtime];
    const target = [draft.runtime === "pi" ? selected.provider : "Codex", selected.model || "本机默认", selected.reasoning_effort || "默认推理强度"].join(" · ");
    state.connectionResult = `[RUNNING] ${target} · 正在测试…`;
    const terminal = this.modelElement(feature, "connTestTerminal");
    updateTestResult(terminal, state.connectionResult, true);
    this.modelElement(feature, "testConnectionBtn").disabled = true;
    const current = () => state === this.modelState(feature) && seq === state.connectionSequence && fingerprint === JSON.stringify(this.modelDraft(feature));
    try {
      const data = await api("/api/settings/connection-test", {method:"POST", body:draft});
      if (!current()) return;
      state.connectionResult = data.status === "success"
        ? `[SUCCESS] ${target} · ${data.message || "连接成功"} · ${data.elapsed_ms}ms · ${data.checked_at || ""}`
        : `[FAILED] ${target} · ${data.message || data.error || data.category || "模型请求未成功"}`;
    } catch (error) {
      if (current()) state.connectionResult = `[FAILED] ${errorText(error)}`;
    } finally {
      state.testing = false;
      if (state === this.modelState(feature)) {
        const terminal = this.modelElement(feature, "connTestTerminal"), button = this.modelElement(feature, "testConnectionBtn");
        updateTestResult(terminal, state.connectionResult, true);
        if (button) { button.disabled = !this.canTestModel(feature); button.textContent = "运行连接测试"; }
      }
    }
  }
  async saveSecret(provider) {
    const state = this.apiState(provider), submitted = state.secretDraft;
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
      this.renderTranslation(provider);
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
    this.updateTranslationRoute();
  }
  async deleteSecret(provider) {
    const state = this.apiState(provider), config = this.apiConfig(provider);
    if (state.savingSecret) return;
    const status = this.saved.api_status[provider], draft = this.draft, secret = state.secretDraft, editing = state.editingSecret;
    if (!await confirmAction({ title: `删除 ${config.label} 密钥`, trigger: `#${this.apiId(provider, "deleteSecretBtn")}`, message: `删除已保存的 ${config.label} API 密钥？删除后，依赖该密钥的服务需重新配置。${status?.environment_override ? "环境变量提供的密钥仍会生效。" : ""}`, action: "删除密钥" })) return;
    if (this.apiState(provider) !== state || this.saved.api_status[provider] !== status || this.draft !== draft || state.secretDraft !== secret || state.editingSecret !== editing) return toast("密钥设置已变化，请重新操作");
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
      this.renderTranslation(provider);
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
