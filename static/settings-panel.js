import { SettingsDirectory, settingsPages, settingsItems } from "./settings-directory.js?v=access-address-1";
import { selectField as select, runtimeOptions, timePickers } from "./form-controls.js?v=0.6.6";
import { confirmAction } from "./confirm-dialog.js";
import { Accordion, disclosureChevron } from "./accordion.js?v=0.6.4";
import { settingsHelpHTML } from "./settings-help.js?v=access-address-1";
import { CollectionEnvironment } from "./collection-environment.js?v=0.6.4";
import { XAccountSettings } from "./x-account-settings.js?v=0.6.4";
import { PersonalAssistantSettings } from "./personal-assistant-settings.js?v=0.6.6";
import { browserPush } from "./browser-push.js?v=2";
import { AccessAddress } from "./access-address.js";
import { $, $$, api, escapeHTML as e, errorText, toast, busy, singlePane, drawerNavigation, formatBytes, storage, persist, resizeAutoFields } from "./ui.js";

import { UpdatePanel } from "./update-panel.js?v=0.3.0";
import { visibilityText } from "./summary-panel.js?v=0.6.6";
import { classificationStatus } from "./classification-progress.js?v=0.6.4";
import { ScheduleObserver } from "./schedule-observer.js?v=0.6.6";
import { defaultReportWindows, windowError } from "./report-windows.js";
import { keyboard, shortcutActions, defaultShortcuts, normalizeShortcuts, displayKey, shortcutKeyFromEvent } from "./keyboard-shortcuts.js";

const clone = (value) => structuredClone(value);
const reportModelFeatures = {reports:"日报模型（早／午／晚）", reports_periodic:"周报与月报模型"};
const modelFeatures = {chat:"文章问答模型", topics:"主题追踪模型", summaries:"新闻总结模型", ...reportModelFeatures};
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
const pagePaths = {
  collection: ["collection", "x_accounts", "classification", "llm.translation.provider", "llm.translation.fallback_enabled", "llm.translation.pi_fallback_enabled", "llm.translation.fallback_runtime"],
  research: ["news_summary", "report_schedule", "report_windows", "agent", "llm.pi_chat", ...Object.keys(modelFeatures).filter(key => key !== "chat").map(key => "feature_models." + key)],
  services: ["llm.translation.model", "api_models"],
  application: ["appearance.theme", "appearance.fontSize", "feed", "keyboard", "database.configured_path"],
};
function matchesPath(left, right, path) {
  const comparable = data => {
    const value = get(data, path);
    return path === "feed" ? [...(value?.hidden_source_subkeys || [])].sort() : value;
  };
  return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
}
const modelEditor = (feature, id, card = true) =>
  `<section id="${id}" class="${card ? "settings-section-card" : "section-stack"}" data-model-feature="${feature}" aria-label="${modelFeatures[feature]}"></section>`;
const field = (label, path, value, extra = "") =>
  `<label class="field-item"><span class="field-label">${label}</span><input class="field-input" name="${path}" value="${e(value)}" ${extra}></label>`;

export class SettingsPanel {
  constructor({ onSaved, onSources, onSourceDeleted, onOpen, onViewChange, selects, appearanceControls, onClassificationRefresh, onClassificationRetry }) {
    this.onOpen = onOpen;
    this.onViewChange = onViewChange;
    this.onSaved = onSaved;
    this.onSources = onSources;
    this.onSourceDeleted = onSourceDeleted;
    this.onClassificationRefresh = onClassificationRefresh;
    this.onClassificationRetry = onClassificationRetry;
    this.appearanceControls = appearanceControls;
    this.open = false;
    this.navigationRevision = 0;
    this.dirty = false;
    this.directory = new SettingsDirectory($("#settingsDirectory"), $("#settingsSearchInput"), (id, target) => this.selectSection(id, {target}));
    const previous = storage("newsreader_settings_section", "pipeline");
    this.section = Object.hasOwn(settingsItems, previous) ? previous : "pipeline";
    this.savingSections = new Set();
    const main = $(".settings-main");
    main.addEventListener("scroll", () => {
      if (this.scrollFrame) return;
      this.scrollFrame = requestAnimationFrame(() => { this.scrollFrame = 0; this.followScroll(); });
    });
    this.personalAssistant = new PersonalAssistantSettings(selects, () => { this.refreshActions(); if (this.jumpTarget) this.finishJump(); });
    this.helpAccordion = new Accordion($("#settingsContent"));
    this.collectionEnvironment = new CollectionEnvironment();
    this.xAccounts = new XAccountSettings(this);
    this.schedules = new ScheduleObserver({
      isVisible: () => this.open && ["research", "application"].includes(this.page) && (!singlePane.matches || $("#feedView").classList.contains("settings-detail-active")),
      isSelected: () => ["schedules", "report-status", "cleanup"].includes(this.section),
      selects,
      getTimes: () => this.draft?.report_schedule,
      getWindows: () => this.draft?.report_windows,
      onTimeChange: (kind, value) => {
        this.draft.report_schedule[kind] = value;
        this.status("有未保存的更改");
      },
      onWindowChange: (kind, window) => {
        this.draft.report_windows ||= clone(defaultReportWindows);
        this.draft.report_windows[kind] = window;
        this.status("有未保存的更改");
      },
      onCancelEdit: () => {
        this.draft.report_schedule = clone(this.saved.report_schedule);
        this.draft.report_windows = clone(this.saved.report_windows);
        this.status("有未保存的更改");
      },
    });
    this.catalogs = new Map();
    this.accessAddress = new AccessAddress();
    this.modelStates = new Map();
    const refreshPi = () => { if (!document.hidden) this.refreshPiCatalog(); };
    window.addEventListener("focus", refreshPi);
    document.addEventListener("visibilitychange", refreshPi);
    this.apiStates = new Map();
    this.apiTestBatch = null;
    this.selects = selects;
    this.scheduleFieldsObserver = new ResizeObserver(() => {
      if (this.open && this.page === "collection" && $("#collectionSchedule")) resizeAutoFields($("#collectionSchedule"));
    });
    this.scheduleFieldsObserver.observe($(".settings-main"));
    this.updater = new UpdatePanel({ hasUnsavedChanges: () => this.hasUnsavedChanges(), contextStamp: () => JSON.stringify([this.draft, this.personalAssistant.draft, [...this.personalAssistant.editors], [...this.apiStates], this.open]) });
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
    $("#settingsSaveBtn").onclick = () => busy(null, () => this.save());
    $("#settingsHelpBtn").onclick = () => this.openSectionHelp();
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
        data.appearance = Object.fromEntries(Object.entries(this.appearanceControls).map(([key, control]) => [key, control.value]));
        this.saved = data;
        this.rememberModelCatalogs(data);
        if (!this.dirty) this.draft = clone(data);
        this.onSources?.();
        if (render && this.open) this.render();
        return data;
      } catch (error) {
        if (this.section !== "help") this.status(errorText(error), true);
        if (this.open && !this.saved) this.renderLoadState(true);
      } finally {
        this.loading = null;
      }
    })();
    return this.loading;
  }
  async toggle() {
    if (this.open) return this.close();
    return this.show();
  }
  async show(section) {
    if (this.open) return this.selectSection(section);
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
    if (this.saved && this.renderedSettings !== this.saved) this.render();
    this.selectSection(section ?? this.section, {reveal: Boolean(section)});
    if (!section || this.jumpTarget) {
      $(section ? "#settingsDetailTitle" : "#settingsListTitle").focus({preventScroll:true});
    }
    if (!this.saved) {
      this.renderLoadState();
      if (this.section !== "help") this.status("正在读取配置…");
      await this.load();
    }
  }
  close({ restoreFocus = true } = {}) {
    if (!this.open) return;
    this.open = false;
    this.schedules.stop();
    this.accessAddress.sync(false);
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
  hasUnsavedChanges() {
    return this.dirty || [...this.personalAssistant.editors.values()].some(editor => editor.draft !== editor.content)
      || [...this.apiStates.values()].some(state => !!state.secretDraft);
  }
  appearanceChanged(key, value) {
    if (!this.draft) return;
    if (this.draft.appearance[key] === this.saved.appearance[key]) this.draft.appearance[key] = value;
    this.saved.appearance[key] = value;
    const panel = $("#readingAppearance");
    if (panel) {
      $(`select[name="appearance.${key}"]`, panel).value = this.draft.appearance[key];
      this.selects.refresh(panel);
    }
    this.refreshActions();
  }
  get page() { return settingsItems[this.section].page; }
  pageDirty(page) {
    return (page === "research" && !!this.personalAssistant.dirty) ||
      (!!this.draft && (pagePaths[page] || []).some(path => !matchesPath(this.draft, this.saved, path)));
  }
  refreshActions() {
    this.dirty = false;
    for (const button of $$("[data-settings-page]")) {
      const dirty = this.pageDirty(button.dataset.settingsPage);
      button.querySelector("[data-unsaved]").hidden = !dirty;
      this.dirty ||= dirty;
    }
    const changed = this.pageDirty(this.page);
    const saving = this.savingSections.has(this.page);
    const actions = $(".settings-detail-actions");
    if (!changed && actions.contains(document.activeElement)) $("#settingsDetailTitle").focus({preventScroll:true});
    actions.hidden = !changed;
    $("#settingsResetBtn").disabled = !changed || !!saving;
    $("#settingsSaveBtn").disabled = !changed || !!saving;
    $("#settingsSaveBtn").setAttribute("aria-busy", String(!!saving));
    return changed;
  }
  status(text = "", failed = false, section = this.section) {
    const changed = this.refreshActions();
    if ((settingsItems[section]?.page || section) !== this.page) return;
    const node = $("#settingsStatus");
    node.textContent = text && text !== "有未保存的更改" ? text : changed ? "有未保存的更改" : "";
    node.classList.toggle("error-state", failed);
    node.classList.toggle("dirty-state", changed);
  }
  openSectionHelp(target = settingsItems[this.section].help) {
    if (this.section === "help") {
      const section = this.helpOrigin;
      this.selectSection(section);
      $("#settingsHelpBtn").focus();
      return;
    }
    this.helpOrigin = this.section;
    this.selectSection("help");
    const question = document.getElementById(target);
    if (!question) return;
    if (question.getAttribute("aria-expanded") !== "true") question.click();
    question.scrollIntoView({block:"start"});
    question.focus({preventScroll:true});
  }
  selectSection(section, {reveal = true, focus = reveal, target, position = true} = {}) {
    const item = settingsItems[section];
    if (!item) return;
    const previousPage = this.page;
    if (position) this.navigationRevision++;
    if (section !== this.section) this.selects.close(false);
    this.section = section;
    if (section !== "help") this.helpOrigin = null;
    persist("newsreader_settings_section", section);
    if (section === "help" && !$("#settingsHelp")) $("#settingsContent").insertAdjacentHTML("beforeend", this.helpHTML());
    const loadMessage = $("#settingsContent > .list-message");
    if (loadMessage) loadMessage.hidden = section === "help";
    for (const panel of $$("#settingsContent > [data-workspace]")) panel.hidden = panel.dataset.workspace !== this.page;
    this.directory.show(item);
    const title = settingsPages[this.page]?.title || item.title;
    $("#settingsDetailTitle").textContent = title;
    $("#settingsDetailTitle").title = title;
    const help = $("#settingsHelpBtn");
    help.hidden = section === "help" && !this.helpOrigin;
    help.textContent = section === "help" ? "返回设置项" : "帮助";
    if (reveal && singlePane.matches) {
      $("#feedView").classList.add("settings-detail-active");
      this.onViewChange?.();
    }
    this.status();
    if (this.draft && $("#collectionSchedule")) {
      if (this.page === "collection") {
        resizeAutoFields($("#collectionSchedule"));
        void this.collectionEnvironment.load();
        if (this.open && (previousPage !== this.page || section === "classification" || section === "progress")) void this.onClassificationRefresh?.();
      }
      if (this.page === "services") void this.collectionEnvironment.load();
      if (this.page === "research") {
        if (this.personalAssistant.host && (!this.personalAssistant.draft || section === "personal-assistant" || section === "memory")) void this.personalAssistant.load();
        if (previousPage !== this.page || section === "catchup" || !this.catchupScopeSequence) this.loadCatchupScope();
        this.refreshPiCatalog();
      }
      if (this.page === "application") this.updater.restore();
      if (this.open && section === "browser-push") void browserPush.load();
    }
    if (position) {
      this.jumpTarget = target || item.target;
      this.jumpFocus = focus;
    }
    this.finishJump();
    this.schedules.sync();
    this.syncAccessAddress(position);
  }
  syncAccessAddress(revisit = false) {
    const visible = this.open && this.page === "application" &&
      (!singlePane.matches || $("#feedView").classList.contains("settings-detail-active"));
    this.accessAddress.sync(visible, revisit && this.section === "access-address");
  }
  finishJump() {
    const anchor = this.jumpTarget && document.getElementById(this.jumpTarget);
    if (!anchor) return;
    const disclosure = anchor.closest("details[data-api-provider]");
    if (disclosure) disclosure.open = true;
    if (!anchor.getClientRects().length) return;
    const first = this.jumpTarget === settingsItems[settingsPages[this.page]?.first]?.target || this.page === "help";
    this.jumpTarget = null;
    const main = $(".settings-main");
    if (first) main.scrollTop = 0;
    else main.scrollTop += anchor.getBoundingClientRect().top - main.getBoundingClientRect().top - parseFloat(getComputedStyle(main).paddingTop);
    this.anchorScroll = main.scrollTop;
    if (this.jumpFocus) {
      const node = first ? $("#settingsDetailTitle") : anchor;
      if (!node.matches("button,input,select,textarea,summary,a[href],[tabindex]")) node.tabIndex = -1;
      node.focus({preventScroll:true});
    }
  }
  followScroll() {
    const main = $(".settings-main");
    if (!this.open || this.page === "help" || this.jumpTarget || Math.abs(main.scrollTop - this.anchorScroll) < 2 || !main.clientHeight) return;
    this.anchorScroll = main.scrollTop;
    const top = main.getBoundingClientRect().top + parseFloat(getComputedStyle(main).paddingTop) + 2;
    const items = Object.values(settingsItems).filter(item => item.page === this.page);
    let current = items[0];
    for (const item of items) {
      const element = document.getElementById(item.target);
      if (element?.getClientRects().length && element.getBoundingClientRect().top <= top) current = item;
    }
    if (current.id !== this.section) {
      this.section = current.id;
      this.directory.show(current);
      this.refreshPiCatalog();
    }
  }
  showCategories(focus = true) {
    this.selects.close(false);
    $("#feedView").classList.remove("settings-detail-active");
    this.schedules.sync();
    this.syncAccessAddress();
    this.onViewChange?.();
    if (focus) {
      const selected = $("#settingsSearchInput").value.trim() ? $("#settingsSearchInput") : $(`[data-settings-tab="${this.section}"]`);
      selected.focus({ preventScroll: true });
    }
  }
  resetDraft() {
    const page = this.page;
    if (!this.draft || !pagePaths[page] || this.savingSections.has(page)) return;
    for (const path of pagePaths[page]) set(this.draft, path, clone(get(this.saved, path)));
    if (page === "collection") this.xAccounts.reset();
    if (page === "research") {
      this.personalAssistant.reset();
      this.schedules.editing = false;
      this.modelStates.clear();
    }
    if (page === "services") {
      this.apiTestBatch = null;
      for (const state of this.apiStates.values()) {
        this.invalidateApiTest(state);
        state.refreshing = false;
        state.notice = "";
      }
    }
    this.render();
    this.status("已撤销本页更改");
  }
  modelPaths(feature = "chat") {
    return feature === "chat" ? {runtime:"agent.runtime",codex:"agent.codex",pi:"llm.pi_chat",dsh:"agent.dsh"}
      : {runtime:`feature_models.${feature}.runtime`,codex:`feature_models.${feature}.codex`,pi:`feature_models.${feature}.pi`,dsh:`feature_models.${feature}.dsh`};
  }
  modelConfig(feature = "chat") {
    if (feature === "chat") return {runtime:this.draft.agent.runtime,codex:this.draft.agent.codex,pi:this.draft.llm.pi_chat,dsh:this.draft.agent.dsh || {provider:"",model:""}};
    this.draft.feature_models ||= {};
    return this.draft.feature_models[feature] ||= clone(feature.startsWith("reports_") ? this.modelConfig("reports")
      : {runtime:this.draft.agent.runtime,codex:this.draft.agent.codex,pi:this.draft.llm.pi_chat,dsh:this.draft.agent.dsh || {provider:"",model:""}});
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
    if (input.closest("#personalAssistantSettings")) return;
    if (input.name?.startsWith("collection.")) {
      set(this.draft, input.name, input.type === "checkbox" ? input.checked : input.type === "number"
        ? input.valueAsNumber : input.value.split(/[，,、\s]+/).filter(Boolean));
      if (input.hasAttribute("data-auto-rows")) resizeAutoFields($("#collectionSchedule"));
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
    if (input.name === "classification.provider" || input.name === "classification.fallback_enabled") this.updateDecisionKeys();
    if (input.name === "classification.enabled") $("#classificationOptions").disabled = !value;
    const apiConfig = this.draft.api_providers.find(config => config.model_path === input.name);
    if (apiConfig) {
      const provider = apiConfig.id, state = this.apiState(provider);
      if (!unchanged) {
        this.invalidateApiTest(state);
        state.notice = "模型已变化，可以测试当前选择。";
        this.apiElement(provider, "translationStatus").textContent = state.notice;
        this.renderApiTests(provider);
      }
      this.syncModelFields(input, this.translationOptions(provider));
    }
    if (apiConfig || input.name.startsWith("llm.translation.")) this.updateTranslationRoute();
    if (input.name === paths.codex+".model") {
      if (!unchanged) {
        config.codex.reasoning_effort = "";
        this.renderReasoning(feature);
      }
    }
    if (input.name.startsWith("news_summary.")) this.loadCatchupScope(250);
    if (feature) this.invalidateModelTest(feature);
    if (config.runtime !== "codex" && input.name === paths[config.runtime]+".provider" && event.type === "change") {
      config[config.runtime].model = "";
      delete config[config.runtime].reasoning_effort;
      this.renderModels(feature);
      $(`[data-select-trigger="${paths[config.runtime]}.provider"]`)?.focus({preventScroll:true});
    }
    if (config.runtime !== "codex" && input.name === paths[config.runtime]+".model" && !unchanged) {
      delete config[config.runtime].reasoning_effort;
      this.renderReasoning(feature);
    }
    if (input.name === paths.runtime) {
      state.catalogNotice = ""; state.catalogSequence++; this.renderModels(feature);
      $(`[data-select-trigger="${paths.runtime}"]`)?.focus({preventScroll:true});
      this.refreshPiCatalog(feature);
    }
    if (feature) this.renderModelNotice(feature);
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
    return JSON.stringify([feature, runtime]);
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
  providerOptions(feature) {
    const config = this.modelConfig(feature), catalog = this.modelCatalog(feature);
    if (config.runtime === "dsh") return (catalog.options || []).filter(m => m.provider === config.dsh?.provider);
    return catalog.model_options_by_provider?.[config.pi.provider] || [];
  }
  canTestModel(feature) {
    const config = this.modelConfig(feature), selected = config[config.runtime];
    const model = (config.runtime !== "codex" ? this.providerOptions(feature) : this.codexOptions(feature)).find(option => option.value === selected.model);
    return !this.modelState(feature).testing && (config.runtime === "codex" && !selected.model && !selected.reasoning_effort || !!model && (!selected.reasoning_effort || model.reasoning_efforts?.includes(selected.reasoning_effort)));
  }
  modelNotice(feature) {
    const config = this.modelConfig(feature), catalog = this.modelCatalog(feature);
    const selected = config[config.runtime];
    const model = (config.runtime !== "codex" ? this.providerOptions(feature) : this.codexOptions(feature)).find(option => option.value === selected.model);
    const missing = config.runtime !== "codex" && selected.provider && !model;
    const unsupported = model && selected.reasoning_effort && !model.reasoning_efforts?.includes(selected.reasoning_effort);
    return [this.modelState(feature).catalogNotice || catalog.notice || catalog.error,
      config.runtime === "codex" && config.codex.model && !this.codexOptions(feature).some(option => option.value === config.codex.model)
        ? `已保存的 ${config.codex.model} 当前不在模型列表中，请刷新列表或重新选择。` : "",
      missing ? `已保存或选择的 ${selected.provider} / ${selected.model || "未选模型"} 当前不在可用目录中，请重新登录或选择模型。` : "",
      unsupported ? `当前模型已不支持保存的推理强度 ${selected.reasoning_effort}，请重新选择。` : ""].filter(Boolean).join(" ");
  }
  renderModelNotice(feature) {
    const notice = this.modelElement(feature, "modelsNotice");
    if (!notice) return;
    notice.textContent = this.modelNotice(feature); notice.hidden = !notice.textContent;
    this.modelElement(feature, "testConnectionBtn").disabled = !this.canTestModel(feature);
  }
  refreshPiCatalog(selectedFeature) {
    if (!this.open || this.page !== "research" || !this.draft) return;
    const target = document.getElementById(settingsItems[this.section].target);
    const editors = target?.matches("[data-model-feature]") ? [target] : target?.querySelectorAll("[data-model-feature]") || [];
    const features = selectedFeature ? [selectedFeature] : Array.from(editors, editor => editor.dataset.modelFeature);
    for (const feature of features) {
      if (!this.modelState(feature).testing && this.modelConfig(feature).runtime !== "codex")
        void this.refreshModels({feature, automatic:true}).catch(() => {});
    }
  }
  renderCodexSelection(feature) {
    const path = this.modelPaths(feature).codex, current = $(`select[name="${path}.model"]`);
    if (!current) return;
    const options = this.codexOptions(feature), value = this.modelConfig(feature).codex.model;
    const container = document.createElement("div");
    container.innerHTML = select("模型", path+".model", value, options, "沿用本机默认", {preserveUnknown:false});
    current.closest(".field-item").replaceWith(container.firstElementChild);
    this.renderModelNotice(feature);
    this.renderReasoning(feature);
  }
  renderReasoning(feature) {
    const runtime = this.modelConfig(feature).runtime, path = this.modelPaths(feature)[runtime], current = $(`select[name="${path}.reasoning_effort"]`);
    if (!current) return;
    const config = this.modelConfig(feature)[runtime];
    const efforts = (runtime !== "codex" ? this.providerOptions(feature) : this.codexOptions(feature)).find(option => option.value === config.model)?.reasoning_efforts || [];
    const container = document.createElement("div");
    container.innerHTML = select("推理强度", path+".reasoning_effort", config.reasoning_effort || "", efforts, "沿用默认", {preserveUnknown:false});
    container.querySelector("select").disabled = !efforts.length;
    current.closest(".field-item").replaceWith(container.firstElementChild);
    this.selects.refresh();
  }
  collectionTimeField(key, label) {
    return `<div class="field-item" data-collection-times="${key}">
      <div class="field-action-row"><label class="field-label" for="collectionTimes-${key}">${label}</label>
      <button class="reader-action-btn reader-action-btn--meta" type="button" data-time-picker aria-label="选择${label}" aria-expanded="false" aria-controls="collectionTime-${key}">选时间</button></div>
      <textarea class="field-input field-input--auto" id="collectionTimes-${key}" name="collection.${key}" rows="1" data-auto-rows="1:7" data-auto-min-rows="1" placeholder="08:00、14:30" spellcheck="false">${e(this.draft.collection[key].join("、"))}</textarea>
      <div class="section-stack" id="collectionTime-${key}" hidden>${timePickers(label, "data-time-part")}
      <div class="button-row"><button class="btn-secondary" type="button" data-add-time>加入时刻</button></div></div></div>`;
  }
  syncCollectionTimes() {
    for (const key of ["daily", "trading"]) {
      const input = $(`[name="collection.${key}"]`);
      if (input) input.value = this.draft.collection[key].join("、");
    }
    resizeAutoFields($("#collectionSchedule"));
  }
  render() {
    if (!this.draft) return;
    this.renderedSettings = this.saved;
    const d = this.draft;
    this.selects.close(false);
    $("#settingsContent").innerHTML =
      `<div class="settings-sheet" data-workspace="collection">
      <section class="settings-section-card" data-section="pipeline" id="collectionSchedule"><div class="section-card-title"><span>采集排期</span><span class="muted">北京时间</span></div>
      <label class="settings-toggle settings-switch"><span>启用定时采集</span><input type="checkbox" role="switch" name="collection.enabled" ${d.collection.enabled ? "checked" : ""}></label>
      <div class="form-group-grid">${this.collectionTimeField("daily", "每天采集时间")}${this.collectionTimeField("trading", "A 股交易日增加")}</div>
      <div class="button-row"><button class="btn-secondary" id="settingsCollectBtn" type="button">立即采集</button><span class="muted" id="settingsCollectionStatus" role="status"></span></div>
      <button class="reader-action-btn reader-action-btn--meta" id="collectionEnvironmentWarning" type="button" hidden></button></section>
      <div class="settings-section-card" data-section="x" id="xAccountSettings"></div>
      <section class="settings-section-card" data-section="translation" id="translationRouting">${this.translationRoutingHTML()}</section>
      <section class="settings-section-card" data-section="classification" id="classificationSettings">
      <div class="section-card-title"><span>自动分类与判断</span></div>
      <label class="settings-toggle settings-switch"><span>启用自动分类</span><input type="checkbox" role="switch" name="classification.enabled" ${d.classification?.enabled !== false ? "checked" : ""}></label>
      <fieldset id="classificationOptions" class="classification-options field-options" aria-label="自动分类选项" ${d.classification?.enabled === false ? "disabled" : ""}>
      <label class="settings-toggle settings-switch"><span>仅对未读新闻分类</span><input type="checkbox" role="switch" name="classification.unread_only" ${d.classification?.unread_only ? "checked" : ""}></label>
      ${field("分类并发上限", "classification.concurrency", d.classification?.concurrency ?? 8, 'type="number" min="1" max="32" step="1" required')}
      </fieldset>
      <fieldset class="field-options" id="decisionPreferences" aria-label="首选判断服务"><legend class="field-label">首选判断服务</legend>${d.api_providers.filter(p => ["jev", "openai-decisions"].includes(p.id)).map(p => `<label class="settings-toggle"><input type="radio" name="classification.provider" value="${p.id}" ${(d.classification?.provider || "jev") === p.id ? "checked" : ""}><span>${e(p.label)}</span></label>`).join("")}</fieldset>
      <label class="settings-toggle settings-switch"><span>自动使用备用判断服务</span><input type="checkbox" role="switch" name="classification.fallback_enabled" ${d.classification?.fallback_enabled ? "checked" : ""}></label>
      <p class="muted" id="decisionKeyStatus" role="status" hidden></p>
      <p class="muted" id="decisionRouteStatus" role="status" hidden></p>
      <button class="btn-secondary" id="classificationApiSettingsBtn" type="button">管理判断 API</button></section>
      <section class="settings-section-card" data-section="progress" id="classificationSettingsProgress" aria-labelledby="classificationSettingsProgressTitle">
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
      </div>
      <div class="settings-sheet" data-workspace="research">
      <section class="settings-section-card" data-section="catchup" id="summaryPreferences"><div class="section-card-title"><span>新闻总结</span></div>
      <div class="form-group-grid">${field("最多自然日（含起止）", "news_summary.max_days", d.news_summary?.max_days ?? 20, 'type="number" min="1" max="9007199254740991" step="1" required')}${field("最多未读条数", "news_summary.max_items", d.news_summary?.max_items ?? 6000, 'type="number" min="1" max="9007199254740991" step="1" required')}</div>
      <div class="catchup-scope-preview" id="settingsCatchupScope" role="status">正在读取范围预览…</div>
      <button class="btn-secondary" id="resetCatchupDefaultsBtn" type="button">恢复默认范围（20 天 / 6000 条）</button>
      ${modelEditor("summaries","summaryModelSettings",false)}</section>
      <div class="settings-sheet" data-section="schedules" id="scheduleSettings"></div>
      <div class="settings-sheet" data-section="report-models" id="reportModelSettings">${modelEditor("reports","reportDailyModel")}${modelEditor("reports_periodic","reportPeriodicModel")}</div>
      <div data-section="report-status" id="reportTaskStatus"></div>
      ${modelEditor("topics","topicModelSettings")}
      ${modelEditor("chat","modelSettings")}
      <div class="settings-sheet section-stack" data-section="personal-assistant" id="personalAssistantSettings"></div>
      </div>
      <div class="settings-sheet" data-workspace="services">
      <div class="settings-sheet" data-section="deepseek" id="translationSettings"></div>
      ${this.collectionEnvironment.html()}
      </div>
      <div class="settings-sheet" data-workspace="application">
      <section class="settings-section-card" data-section="appearance" id="readingAppearance"><div class="section-card-title"><span>主题与字号</span></div>
      <div class="form-group-grid">${Object.entries(this.appearanceControls).map(([key, control]) =>
        select(key === "theme" ? "阅读主题" : "正文字号", "appearance." + key, d.appearance[key], Array.from(control.options, option => ({value:option.value, label:option.textContent})), null)).join("")}</div></section>
      <section class="settings-section-card" data-section="access-address" id="accessAddressSettings" aria-labelledby="accessAddressTitle" tabindex="-1">
      <div class="section-card-title"><span id="accessAddressTitle">访问地址</span><button class="reader-action-btn reader-action-btn--meta" data-access-refresh type="button">重新检测</button></div>
      <div class="field-item" data-access-result hidden><span class="field-label">其他设备访问地址</span>
      <code class="storage-path" data-access-url></code>
      <div class="button-row"><button class="reader-action-btn reader-action-btn--meta" data-access-copy type="button">复制</button></div></div>
      <p class="muted status-text" data-access-feedback role="status">尚未检测。</p></section>
      <section class="settings-section-card" data-section="browser-push" id="browserPushSettings" aria-label="浏览器通知"></section>
      <section class="settings-section-card" data-section="display" id="sourceVisibility"><div class="section-card-title"><span>新闻流显示</span></div><div id="settingsSources"></div></section>
      <section class="settings-section-card" data-section="shortcuts" id="shortcutSettings"></section>
      <section class="settings-section-card" data-section="storage" id="databaseSettings"><div class="section-card-title"><span>数据库位置</span></div>
      <div class="field-item"><span class="field-label">当前主数据库路径</span><code class="storage-path">${e(d.database.path)}</code></div>
      ${field("下次启动使用的数据库", "database.configured_path", d.database.configured_path, d.database.environment_override ? "disabled" : 'placeholder="已有 SQLite 文件的完整路径"')}
      <button class="btn-secondary" id="pickDatabaseBtn" type="button" ${d.database.environment_override || !d.database.native_picker_available ? "disabled" : ""}>选择文件…</button><p class="muted">${e(d.database.notice)}</p></section>
      <div class="settings-sheet" data-section="cleanup" id="dataCleanup">
      <section class="settings-section-card" id="sourceCleanup"><div class="section-card-title"><span>清理来源数据</span></div><div id="settingsSourceCleanup"></div></section>
      <div id="reportSnapshotSettings"></div>
      <section class="settings-section-card" id="sessionCleanup"><div class="section-card-title"><span>临时问答会话</span></div>
      <button class="reader-action-btn danger" id="clearSessionsBtn" type="button">清空全部临时 Agent 会话</button></section></div>
      <section class="settings-section-card" data-section="system" id="systemSettings"><div class="section-card-title"><span>版本更新</span></div><div><strong id="updateCurrent"></strong><div class="field-item"><span class="field-label">更新源</span><code id="updateRepository" class="storage-path"></code></div></div><div class="button-row"><button class="btn-secondary" id="checkUpdateBtn" type="button">检查更新</button><button class="btn-primary" id="applyUpdateBtn" type="button" hidden>确认更新</button><button class="btn-primary" id="refreshUpdateBtn" type="button" hidden>刷新使用新版本</button><button class="btn-secondary" id="resumeUpdateBtn" type="button" hidden>继续查看进度</button></div><div id="updateResult" hidden role="status" aria-live="polite" aria-atomic="true"><strong id="updateConclusion"></strong><p id="updateExplanation"></p><time id="updateCheckedAt"></time></div><details><summary>版本记录</summary><div id="releaseNotes" class="muted">展开后读取</div></details></section>
      </div>${this.helpHTML()}`;
    this.renderModels();
    this.personalAssistant.mount($("#personalAssistantSettings"));
    this.xAccounts.render();
    this.renderTranslation();
    this.renderShortcuts();
    this.renderSourceLists();
    this.updater.render();
    this.accessAddress.mount($("#accessAddressSettings"));
    browserPush.mount($("#browserPushSettings"));
    this.renderClassificationProgress();
    this.updateCollectionStatus(this.collectionStatus);
    this.collectionEnvironment.render();
    this.schedules.mount($("#scheduleSettings"), $("#reportTaskStatus"), $("#reportSnapshotSettings"));
    this.selects.refresh($("#settingsContent"));
    this.selectSection(this.section, {reveal:false, position:false});
    $("#releaseNotes").closest("details").ontoggle = (event) => {
      if (event.target.open) this.loadReleaseNotes();
    };
    this.status();
  }
  helpHTML() {
    return `<div class="settings-sheet" data-workspace="help" data-section="help" id="settingsHelp">${settingsHelpHTML(this.helpAccordion)}</div>`;
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
    panel.innerHTML = `<div class="section-card-title"><span>阅读快捷键</span></div><label class="settings-toggle settings-switch"><span>显示底部快捷键栏</span><input type="checkbox" role="switch" name="keyboard.show_bar" ${prefs.show_bar ? "checked" : ""}></label>
      <p class="muted" id="shortcutInstructions">点选输入框，然后按下快捷键。</p>
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
      secretDraft: "", editingSecret: false, catalog: null, notice: "", testResult: null, sequence: 0,
      testing: null, refreshing: false, savingSecret: false,
    });
    return this.apiStates.get(provider);
  }
  apiConfig(provider) {
    return this.draft.api_providers.find(config => config.id === provider);
  }
  apiId(provider, control) { return provider === "deepseek" ? control : `${provider}-${control}`; }
  apiElement(provider, control) { return document.getElementById(this.apiId(provider, control)); }
  apiTestBlocker(provider) {
    const config = this.apiConfig(provider);
    if (this.apiState(provider).savingSecret) return "密钥正在更新，请稍后再试";
    if (!this.draft.api_status?.[provider]?.configured) return "未配置 API 密钥";
    if (provider !== "deepseek" && !config.default_model && !get(this.draft, config.model_path)?.trim()) return "尚未选择或填写模型";
    return "";
  }
  invalidateApiTest(state) {
    state.sequence++;
    state.testing = null;
    state.testResult = null;
  }
  translationOrder(settings = this.draft) {
    const config = settings.llm.translation, preferred = config.provider || "deepseek";
    const others = settings.api_providers.filter(p => p.translation_base_url && p.id !== preferred).map(p => p.id);
    return [preferred, ...(config.fallback_enabled !== false ? others : []),
      ...(config.pi_fallback_enabled !== false ? [config.fallback_runtime || "pi"] : [])];
  }
  translationRoutingHTML() {
    const config = this.draft.llm.translation;
    return `<div class="section-card-title"><span>正文翻译</span></div>
      ${field("新闻翻译并发上限", "collection.translation_concurrency", this.draft.collection.translation_concurrency ?? 4, 'type="number" min="1" max="32" step="1" required')}
      <fieldset class="field-options" aria-label="首选翻译服务"><legend class="field-label">首选翻译服务</legend>${this.draft.api_providers.filter(p => p.translation_base_url).map(p => `<label class="settings-toggle"><input type="radio" name="llm.translation.provider" value="${p.id}" ${config.provider === p.id ? "checked" : ""}><span>${e(p.label)}</span></label>`).join("")}</fieldset>
      <label class="settings-toggle settings-switch"><span>失败后切换其他 API</span><input type="checkbox" role="switch" name="llm.translation.fallback_enabled" ${config.fallback_enabled !== false ? "checked" : ""}></label>
      <label class="settings-toggle settings-switch"><span>API 均失败后使用后备运行时</span><input type="checkbox" role="switch" name="llm.translation.pi_fallback_enabled" ${config.pi_fallback_enabled !== false ? "checked" : ""}></label>
      ${select("翻译后备运行时", "llm.translation.fallback_runtime", config.fallback_runtime || "pi", runtimeOptions.filter(r => r.value !== "codex"), null)}
      <p class="muted" id="translationRouteDescription" role="status"></p>
      <div class="button-row"><button class="reader-action-btn reader-action-btn--meta" id="collectionApiSettingsBtn" type="button">管理翻译 API 密钥</button>
      <button class="reader-action-btn reader-action-btn--meta" id="apiFeatureSettingsBtn" type="button">配置后备问答模型</button></div>`;
  }
  updateTranslationRoute() {
    const description = $("#translationRouteDescription");
    if (!description) return;
    $("#apiFeatureSettingsBtn").hidden = this.draft.llm.translation.pi_fallback_enabled === false;
    const order = this.translationOrder(), labels = {pi:"Pi", dsh:"DSH", ...Object.fromEntries(this.draft.api_providers.map(p => [p.id, p.label]))};
    const missing = order.filter(p => !["pi", "dsh"].includes(p) && (!this.draft.api_status?.[p]?.configured || (p !== "deepseek" && !get(this.draft, this.apiConfig(p).model_path)?.trim())));
    description.textContent = `调用顺序：${order.map(p => labels[p]).join(" → ")}。${missing.length ? `${missing.map(p => labels[p]).join("、")} 尚未配置密钥或模型，无法调用。` : ""}`;
  }
  updateDecisionKeys() {
    const status = $("#decisionKeyStatus");
    if (!status || !this.draft) return;
    const selected = this.draft.classification.provider;
    const services = this.draft.api_providers.filter(p => !p.translation_base_url && (p.id === selected || this.draft.classification.fallback_enabled));
    const missing = services.filter(p => !this.draft.api_status[p.id]?.configured).sort((a, b) => Number(b.id === selected) - Number(a.id === selected));
    status.hidden = !missing.length;
    status.textContent = missing.length ? `${missing.map(p => p.label).join("、")} 缺少 API 密钥` : "";
    const button = $("#classificationApiSettingsBtn");
    button.textContent = missing.length ? `配置 ${missing[0].label} 密钥` : "管理判断 API";
    button.dataset.provider = missing[0]?.id || selected;
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
      $("[data-credential-status]", panel).textContent = this.draft.api_status[provider]?.configured ? "已配置密钥" : "未配置密钥";
      $("[data-provider-fields]", panel).innerHTML = this.apiProviderHTML(provider);
      this.renderApiTests(provider);
      this.selects.refresh(panel);
      if (active) {
        const focus = (focusSelector && $(focusSelector, panel)) || this.apiElement(provider, "editSecretBtn") || $(`#${provider}Secret`, panel);
        focus?.focus({ preventScroll: true });
        if (selection && focus?.tagName === "INPUT") focus.setSelectionRange(...selection);
      }
      return;
    }
    const groups = [["翻译服务", true], ["判断服务", false]];
    root.innerHTML = `<div class="section-stack">
      <div class="section-card-title"><span>API 密钥与连接</span><button class="reader-tool-btn" id="testAllApisBtn" type="button" aria-controls="apiTestSummary">测试全部服务</button></div>
      <div class="terminal-box" id="apiTestSummary" role="status" aria-atomic="true" tabindex="0" hidden></div></div>` + groups.map(([label, translation]) => {
      const cards = this.draft.api_providers.filter(config => !!config.translation_base_url === translation).map(({id,label}) =>
        `<details class="settings-section-card settings-section-card--compact settings-disclosure" data-api-provider="${id}" ${this.apiState(id).expanded ? "open" : ""}>
        <summary class="section-card-title" id="apiTitle-${id}"><span>${e(label)}</span><span class="muted" data-credential-status>${this.draft.api_status[id]?.configured ? "已配置密钥" : "未配置密钥"}</span>${disclosureChevron}</summary>
        <div class="section-stack" data-provider-fields>${this.apiProviderHTML(id)}</div></details>`);
      return `<section class="settings-sheet" aria-label="${label}"><h3 class="nav-title">${label}</h3>${cards.join("")}</section>`;
    }).join("");
    for (const details of $$("details[data-api-provider]", root)) {
      details.ontoggle = () => { this.apiState(details.dataset.apiProvider).expanded = details.open; };
    }
    this.renderApiTests();
    this.selects.refresh(root);
    this.updateDecisionKeys();
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
    return `${provider === "deepseek" ? '<p class="muted">Pi 和 DSH 使用此密钥搜索和抓取网页，按成功请求单独计费。</p>' : ""}<div class="field-item"><label class="field-label" ${editing ? `for="${provider}Secret"` : ""}>API 密钥</label>
      ${configured ? `<code class="masked-secret">${e(service.masked_key || "••••••••")}</code>` : ''}
      ${service.environment_override ? `<p class="muted">当前由 ${e(config.secret_name)} 环境变量提供；保存和删除仅影响本地钥匙串，环境变量仍优先生效。</p>` : ""}
      ${editing ? `<input class="field-input" id="${provider}Secret" data-api-secret="${provider}" type="password" autocomplete="new-password" value="${e(state.secretDraft)}" placeholder="输入新的 API 密钥" ${state.savingSecret ? "disabled" : ""}><div class="button-row"><button class="btn-secondary" ${action("saveSecretBtn")} type="button" ${state.savingSecret ? "disabled" : ""}>保存密钥</button>${configured ? `<button class="btn-secondary" ${action("cancelSecretBtn")} type="button">取消</button>` : ""}</div>` : `<div class="button-row"><button class="btn-secondary" ${action("editSecretBtn")} type="button">更新密钥</button><button class="reader-action-btn danger" ${action("deleteSecretBtn")} type="button" ${state.savingSecret ? "disabled" : ""}>删除密钥</button></div>`}</div>
      <div class="field-action-row">${select(config.translation_base_url ? "翻译模型" : "模型", config.model_path, model, options, configured ? (config.default_model ? `使用默认模型（${config.default_model}）` : provider !== "deepseek" ? "请选择模型" : "使用默认模型") : "请先配置 API 密钥")}<button class="btn-secondary" ${action("translationModelsBtn")} type="button" ${configured ? "" : "hidden"} ${state.refreshing ? "disabled" : ""}>${state.refreshing ? "正在获取…" : "获取模型"}</button></div>
      ${field("自定义模型（可选）", config.model_path, custom, 'maxlength="120"')}
      <p class="muted" id="${id("translationStatus")}" role="status">${e(state.notice)}</p>
      <div class="button-row"><button class="btn-secondary" ${action("translationTestBtn")} type="button">测试连接</button></div>
      <div class="terminal-box" id="${id("translationTestTerminal")}" role="status" hidden></div>`;
  }
  async refreshTranslationModels(provider) {
    const state = this.apiState(provider);
    if (state.refreshing) return;
    const seq = state.sequence;
    const request = state.refreshing = {};
    try {
      const data = await api(`/api/settings/apis/${provider}/models`, { method: "POST", body: {} });
      if (state !== this.apiState(provider) || seq !== state.sequence) return;
      state.catalog = data.catalog;
      state.notice = `已获取 ${data.catalog.options.length} 个模型`;
    } catch (error) {
      if (state === this.apiState(provider) && seq === state.sequence) state.notice = errorText(error);
    } finally {
      if (state.refreshing === request) {
        state.refreshing = false;
        this.renderTranslation(provider);
      }
    }
  }
  renderApiTests(provider) {
    const providers = provider ? [provider] : this.draft.api_providers.map(config => config.id);
    for (const id of providers) {
      const state = this.apiState(id), button = this.apiElement(id, "translationTestBtn");
      if (!button) continue;
      button.disabled = !!state.testing || !!this.apiTestBlocker(id);
      button.setAttribute("aria-busy", String(!!state.testing));
      button.textContent = state.testing ? "正在测试…" : "测试连接";
      const result = state.testResult;
      let text = "";
      if (state.testing) text = "[RUNNING] 正在测试模型连接…";
      else if (result) text = `[${result.passed ? "PASS" : "FAIL"}] ${result.message}`;
      updateTestResult(this.apiElement(id, "translationTestTerminal"), text, true);
    }
    this.renderApiTestSummary();
  }
  renderApiTestSummary() {
    const button = $("#testAllApisBtn"), report = $("#apiTestSummary");
    if (!button) return;
    const providers = this.draft.api_providers;
    const running = !!this.apiTestBatch?.running;
    button.disabled = running || !providers.length;
    button.setAttribute("aria-busy", String(running));
    button.textContent = running ? "正在测试全部服务…" : "测试全部服务";
    report.hidden = !this.apiTestBatch;
    if (report.hidden) return;
    let passed = 0, pending = 0;
    const failures = [], skipped = [];
    for (const {id, label} of providers) {
      const {testing, testResult} = this.apiState(id), blocker = this.apiTestBlocker(id);
      if (blocker) skipped.push(`${label} · 未测试 · ${blocker}`);
      else if (testing) pending++;
      else if (testResult?.passed) passed++;
      else if (testResult) failures.push(`${label} · 连接失败 · ${testResult.message}`);
      else skipped.push(`${label} · 未测试 · 请重新测试当前配置`);
    }
    const total = providers.length;
    let summary;
    if (pending) {
      summary = `正在测试 API 服务，已完成 ${total - pending}/${total} 项。`;
      report.dataset.status = "running";
    } else if (passed === total) {
      summary = `全部 ${total} 项 API 服务连接成功。`;
      report.dataset.status = "success";
    } else {
      const counts = [`共 ${total} 项服务`, `${passed} 项通过`];
      if (failures.length) counts.push(`${failures.length} 项连接失败`);
      if (skipped.length) counts.push(`${skipped.length} 项未测试`);
      summary = counts.join("，") + "。";
      report.dataset.status = failures.length ? "failed" : "ready";
    }
    const text = [summary, ...failures, ...skipped].join("\n");
    if (report.textContent !== text) report.textContent = text;
  }
  testApiConnection(provider) {
    const state = this.apiState(provider);
    if (this.apiTestBlocker(provider)) {
      state.testing = null;
      state.testResult = null;
      return;
    }
    if (state.testing) return state.testing;
    const config = this.apiConfig(provider), model = get(this.draft, config.model_path) || "";
    const request = api(`/api/settings/apis/${provider}/test`, {
      method: "POST", body: {model}, signal: AbortSignal.timeout(45000),
    }).then(data => {
      if (data.ok !== true || !data.model || !data.message || !Number.isFinite(data.elapsed_ms)) {
        return {passed:false, message:"未收到完整的连接测试结果"};
      }
      const message = `${data.message} · ${data.model} · ${data.elapsed_ms}ms`;
      return {passed:true, message:config.translation_base_url ? message + " · 仅验证连接" : message};
    }, error => ({
      passed: false, message: error.name === "TimeoutError" ? "模型连接测试超时，请重试。" : errorText(error),
    })).then(result => {
      if (state.testing !== request) return;
      state.testing = null;
      state.testResult = result;
      this.renderApiTests(provider);
    });
    state.testing = request;
    state.testResult = null;
    return request;
  }
  async testAllApis() {
    if (this.apiTestBatch?.running) return;
    const batch = this.apiTestBatch = {running:true};
    try {
      const requests = this.draft.api_providers.map(({id}) => this.testApiConnection(id));
      this.renderApiTests();
      await Promise.all(requests);
    } finally {
      batch.running = false;
      if (this.apiTestBatch === batch) this.renderApiTestSummary();
    }
  }
  renderModels(feature) {
    if (!feature) {
      for (const key of Object.keys(modelFeatures)) this.renderModels(key);
      return;
    }
    const panel = $(`[data-model-feature="${feature}"]`, $("#settingsContent"));
    if (!panel) return;
    const d = this.modelConfig(feature), paths = this.modelPaths(feature), codex = d.runtime === "codex", state = this.modelState(feature);
    const catalog = this.modelCatalog(feature), options = this.codexOptions(feature);
    const model = options.find(x=>x.value === d.codex.model);
    const selected = d[d.runtime] || {}, selectedPath = paths[d.runtime];
    const providers = d.runtime === "dsh" ? [...new Set((catalog.options || []).map(m => m.provider))] : catalog.provider_options || [];
    const notice = this.modelNotice(feature), piModel = this.providerOptions(feature).find(option => option.value === selected.model);
    const id = control => this.modelId(feature, control);
    const action = control => `id="${id(control)}" data-model-action="${control}"`;
    panel.innerHTML = `<div class="section-card-title"><span>${modelFeatures[feature]}</span></div><div class="form-group-grid">
      ${select("Runtime", paths.runtime, d.runtime, runtimeOptions, null)}
      ${codex ? `${select("模型",paths.codex+".model",d.codex.model,options,"沿用本机默认",{preserveUnknown:false})}${select("推理强度",paths.codex+".reasoning_effort",d.codex.reasoning_effort,model?.reasoning_efforts || [],"沿用默认",{preserveUnknown:false})}`
      : `${select("服务商",selectedPath+".provider",selected.provider,providers,"请选择服务商",{preserveUnknown:false})}${select("模型",selectedPath+".model",selected.model,this.providerOptions(feature),"请选择模型",{preserveUnknown:false})}${select("推理强度",selectedPath+".reasoning_effort",selected.reasoning_effort || "",piModel?.reasoning_efforts || [],"沿用默认",{preserveUnknown:false})}`}</div>
      <div class="button-row"><button class="btn-secondary" ${action("refreshModelsBtn")} type="button" ${state.pendingCatalog?'disabled':''}>刷新模型目录</button><button class="btn-secondary" ${action("testConnectionBtn")} type="button" ${this.canTestModel(feature)?'':'disabled'}>${state.testing?'测试中…':'运行连接测试'}</button></div>
      <p class="muted" id="${id("modelsNotice")}" role="status" ${notice?'':'hidden'}>${e(notice)}</p><div class="terminal-box" id="${id("connTestTerminal")}" data-status="${testResultStatus(state.connectionResult)}" role="status" ${state.connectionResult?'':'hidden'}>${e(state.connectionResult || "")}</div>
      ${feature === "chat" ? `<div class="form-group-grid" id="chatRetention">${select("临时会话保留时间","agent.session_ttl_hours",String(this.draft.agent.session_ttl_hours),[{value:"24",label:"24 小时"},{value:"72",label:"72 小时"}],null)}</div>` : ""}`;
    $(`select[name="${selectedPath}.reasoning_effort"]`, panel).disabled = !(codex ? model : piModel)?.reasoning_efforts?.length;
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
  }
  renderSourceLists() {
    const hidden = new Set(this.draft.feed.hidden_source_subkeys || []);
    const groups = this.draft.feed_source_subkeys?.groups || [];
    for (const [selector, cleanup] of [["#settingsSources", false], ["#settingsSourceCleanup", true]]) {
      const host = $(selector);
      if (!host) continue;
      host.innerHTML = groups.map(group => {
        const rows = group.items.map(item => {
          const name = `<span>${e(item.label)}</span><span class="muted">${e(item.count)}</span>`;
          const control = cleanup
            ? `${name}<button type="button" class="reader-action-btn reader-action-btn--meta danger" data-delete-source="${e(item.key)}" aria-label="删除信源 ${e(group.label)} · ${e(item.label)}">删除</button>`
            : `<label><input type="checkbox" data-setting-source="${e(item.key)}" ${hidden.has(item.key) ? "" : "checked"}>${name}</label>`;
          return `<div class="sub-source-item">${control}</div>`;
        });
        return `<fieldset class="settings-source-group"><legend>${e(group.label)}</legend>${rows.join("")}</fieldset>`;
      }).join("") || '<p class="muted">暂无来源。</p>';
    }
  }
  async deleteSource(button) {
    if (this.sourceDeletionPending) return;
    this.sourceDeletionPending = true;
    const key = button.dataset.deleteSource;
    const path = `/api/settings/sources/${encodeURIComponent(key)}`;
    const library = this.saved.database.path, revision = this.navigationRevision;
    const index = $$("[data-delete-source]").indexOf(button);
    button.textContent = "统计中…";
    try {
      const { source } = await api(path);
      if (!this.open || this.page !== "application" || this.navigationRevision !== revision || !button.isConnected || this.saved.database.path !== library) return;
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
        this.renderSourceLists();
        if (this.open && this.page === "application") {
          const buttons = $$("[data-delete-source]");
          (buttons[Math.min(index, buttons.length - 1)] || $("#settingsDetailTitle"))?.focus({preventScroll:true});
        }
      }
      this.onSourceDeleted?.(data);
      this.onSources?.();
      this.onSaved?.();
      document.dispatchEvent(new Event("tasks:changed"));
      this.status("信源已删除", false, "cleanup");
      toast(data.warning || `已删除 ${source.label}，共 ${data.deleted.news_count.toLocaleString()} 条新闻`);
    } catch (error) {
      this.status(errorText(error), true, "cleanup");
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
    const route = $("#decisionRouteStatus");
    route.hidden = !p?.fallback_enabled;
    if (!route.hidden) {
      if (p.waiting_for_service) route.textContent = "两家判断服务暂不可用，等待恢复或配置密钥。";
      else if (p.using_backup) route.textContent = `当前使用备用服务 ${p.provider_label}，等待首选 ${p.preferred_label} 恢复。`;
      else route.textContent = `当前使用首选服务 ${p.preferred_label}。`;
    }
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
      this.status();
    }
    if (!button) return;
    if (button.dataset.settingsHelp) return this.openSectionHelp(button.dataset.settingsHelp);
    const times = button.closest("[data-collection-times]");
    if (times && button.matches("[data-time-picker], [data-add-time]")) {
      const key = times.dataset.collectionTimes;
      const trigger = $("[data-time-picker]", times), picker = $("#collectionTime-" + key);
      if (button.hasAttribute("data-add-time")) {
        const time = $$("select[data-time-part]", times).map(select => select.value).join(":");
        const values = this.draft.collection[key];
        if (!values.includes(time)) values.push(time);
        this.syncCollectionTimes();
        this.status();
      }
      picker.hidden = !picker.hidden;
      trigger.setAttribute("aria-expanded", String(!picker.hidden));
      (picker.hidden ? trigger : $("[data-select-trigger]", picker)).focus({preventScroll:true});
      return;
    }
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
        translationTestBtn: () => {
          const request = this.testApiConnection(provider);
          this.renderApiTests(provider);
          return request;
        },
        saveSecretBtn: () => this.saveSecret(provider),
        deleteSecretBtn: () => this.deleteSecret(provider),
      };
      if (actions[button.dataset.apiAction]) void busy(button, actions[button.dataset.apiAction]).finally(() => this.renderApiTests(provider));
      return;
    }
    const actions = {
      testAllApisBtn: () => this.testAllApis(),
      checkCollectionEnvironmentBtn: () => this.collectionEnvironment.check("environment"),
      checkCollectionXBtn: () => this.collectionEnvironment.check("x"),
      collectionApiSettingsBtn: () => this.selectSection("deepseek", {target:"apiTitle-" + this.draft.llm.translation.provider}),
      collectionEnvironmentWarning: () => this.selectSection("environment", {target:"checkCollectionEnvironmentBtn"}),
      classificationRefreshBtn: () => this.onClassificationRefresh?.(),
      classificationRetryBtn: () => this.onClassificationRetry?.(),
      classificationApiSettingsBtn: () => this.selectSection("deepseek", {target:"apiTitle-" + button.dataset.provider}),
      apiFeatureSettingsBtn: () => this.selectSection("models"),
      retryCatchupScopeBtn: () => this.loadCatchupScope(),
      resetCatchupDefaultsBtn: () => {
        this.draft.news_summary = { max_days: 20, max_items: 6000 };
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
    const page = this.page;
    if (!this.pageDirty(page) || this.savingSections.has(page)) return;
    const paths = pagePaths[page].filter(path => !matchesPath(this.draft, this.saved, path));
    const profile = page === "research" && this.personalAssistant.dirty ? clone(this.personalAssistant.draft) : null;
    if (profile && !this.personalAssistant.validate()) return;
    if (paths.includes("x_accounts") && this.xAccounts.pending) return this.status("请先完成账号移除操作", true);
    if (paths.some(path => path === "report_schedule" || path === "report_windows")) {
      const times = this.draft.report_schedule;
      let message = !(times.morning < times.noon && times.noon < times.evening) ? "早报时间须早于午报，午报时间须早于晚报" : "";
      for (const [kind, window] of Object.entries(this.draft.report_windows)) {
        const error = windowError(window, times[kind]);
        if (error && !message) message = ({morning:"早报",noon:"午报",evening:"晚报",weekly:"周报",monthly:"月报"})[kind] + error;
      }
      if (message) {
        this.selectSection("schedules");
        this.schedules.setEditing(true);
        return this.status(message, true);
      }
    }
    const invalid = $$(`[data-workspace="${page}"] input[type="number"]`).find(input =>
      !Number.isInteger(input.valueAsNumber) || input.valueAsNumber < Number(input.min) || input.valueAsNumber > Number(input.max));
    if (invalid) {
      if (invalid.matches(":disabled")) {
        this.selectSection("classification");
        $('[name="classification.enabled"]').focus();
        return this.status("请先启用自动分类，将分类并发上限设为 1–32 的整数后再保存", true);
      }
      invalid.reportValidity();
      return this.status(invalid.name === "classification.concurrency" ? "分类并发上限必须是 1–32 的整数" :
        invalid.name === "collection.translation_concurrency" ? "新闻翻译并发上限必须是 1–32 的整数" : "总结范围必须填写有效的正整数", true);
    }
    const submitted = {};
    for (const path of paths) set(submitted, path, clone(get(this.draft, path)));
    const body = clone(submitted);
    delete body.keyboard;
    delete body.appearance;
    if (submitted.database) body.database = {path:submitted.database.configured_path};
    if (submitted.x_accounts) body.x_accounts_revision = this.draft.x_accounts_revision;
    const sources = this.saved.feed_source_subkeys, apiStatus = {...this.saved.api_status};
    this.savingSections.add(page);
    this.status("正在保存…");
    let written = false, message, failed = false;
    try {
      if (Object.keys(body).length) {
        const data = await api("/api/settings", {method:"PUT", body});
        written = true;
        for (const path of paths.filter(path => path !== "keyboard" && !path.startsWith("appearance."))) {
          if (matchesPath(this.draft, submitted, path)) set(this.draft, path, clone(get(data, path)));
          set(this.saved, path, clone(get(data, path)));
        }
        if (submitted.x_accounts) {
          this.saved.x_accounts_revision = this.draft.x_accounts_revision = data.x_accounts_revision;
          this.xAccounts.render();
        }
        if ((submitted.feed || submitted.x_accounts) && sources === this.saved.feed_source_subkeys) {
          this.saved.feed_source_subkeys = this.draft.feed_source_subkeys = data.feed_source_subkeys;
          this.renderSourceLists();
        }
        if (submitted.collection && matchesPath(this.draft, this.saved, "collection")) this.syncCollectionTimes();
        if (submitted.database) $('[name="database.configured_path"]').value = this.draft.database.configured_path || "";
        if ((submitted.report_schedule || submitted.report_windows) && ["report_schedule","report_windows"].every(path => matchesPath(this.draft,this.saved,path)))
          this.schedules.setEditing(false);
        if (page === "services") {
          for (const {id} of this.draft.api_providers) {
            const path = this.apiConfig(id).model_path;
            const normalized = get(submitted, path) !== undefined && get(submitted, path) !== get(data, path);
            if (this.saved.api_status[id] === apiStatus[id] && data.api_status?.[id] && !this.apiState(id).savingSecret) {
              this.saved.api_status[id] = this.draft.api_status[id] = data.api_status[id];
              if (normalized || JSON.stringify(apiStatus[id]) !== JSON.stringify(data.api_status[id])) {
                this.invalidateApiTest(this.apiState(id));
                this.renderTranslation(id);
              }
            }
          }
        }
        if (page === "research") this.rememberModelCatalogs(data);
        this.updateTranslationRoute();
        this.updateDecisionKeys();
        if (submitted.news_summary && this.page === "research") this.loadCatchupScope();
        if (submitted.report_schedule || submitted.report_windows) void this.schedules.refresh();
        this.onSources?.();
        this.onSaved?.();
      }
      for (const [key, value] of Object.entries(submitted.appearance || {})) {
        const control = this.appearanceControls[key];
        control.value = value;
        control.dispatchEvent(new Event("change", {bubbles:true}));
      }
      if (submitted.keyboard) {
        const unchanged = matchesPath(this.draft, submitted, "keyboard");
        const saved = keyboard.save(normalizeShortcuts(submitted.keyboard));
        this.saved.keyboard = clone(saved);
        if (unchanged) this.draft.keyboard = clone(saved);
        this.renderShortcuts();
      }
      if (profile) await this.personalAssistant.save(profile);
      message = this.pageDirty(page) ? "已保存提交的配置，仍有新的更改未保存" : "设置已保存";
      toast("设置已保存");
    } catch (error) {
      failed = true;
      message = written && profile ? "其他设置已保存，个人助手保存失败。" + errorText(error) : errorText(error);
      throw new Error(message);
    } finally {
      this.savingSections.delete(page);
      this.status(message, failed, page);
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
      const data = await api("/api/settings/models", {method:"POST", body:{...draft, refresh:!automatic}});
      if (!current()) return;
      const unchanged = JSON.stringify(this.modelCatalog(feature)) === JSON.stringify(data.catalog);
      if (draft.runtime !== "codex" || !data.catalog.error || data.catalog.options?.length || !this.catalogs.has(key))
        this.catalogs.set(key, clone(data.catalog));
      if (automatic && unchanged) return;
      state.catalogNotice = data.catalog.notice || data.catalog.error || "模型目录已刷新";
      this.invalidateModelTest(feature);
      render();
    } catch (error) {
      if (!current()) return;
      state.catalogNotice = errorText(error);
      if (draft.runtime !== "codex") this.catalogs.set(key, {error:state.catalogNotice});
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
      target.textContent = "请输入有效的正整数以计算范围预览。";
      target.removeAttribute("aria-busy");
      return;
    }
    target.textContent = "正在计算范围预览…";
    target.setAttribute("aria-busy", "true");
    const controller = this.catchupScopeController = new AbortController();
    this.catchupScopeTimer = setTimeout(async () => {
      try {
        const scope = await api("/api/news-summaries/scope", { method: "POST", body: { news_summary: limits }, signal: controller.signal });
        if (sequence !== this.catchupScopeSequence || !target.isConnected) return;
        const coverage = scope.summarized_count ? `实际覆盖 ${e(scope.start_date)} → ${e(scope.end_date)}` : "暂无未读新闻";
        target.innerHTML = `<div class="catchup-scope-heading"><strong>范围预览</strong><span>${e(scope.window_start)} → ${e(scope.window_end)}</span></div><p>${e(visibilityText(scope))}</p><p>按日期与条数纳入 ${scope.summarized_count} 条；日期排除 ${scope.excluded_by.date} 条，条数排除 ${scope.excluded_by.count} 条。${coverage}${scope.undated_summarized ? `；另含日期不明 ${scope.undated_summarized} 条` : ""}。</p><p class="muted">当前填写：${scope.limits.max_days} 个自然日、最多 ${scope.limits.max_items} 条。${scope.fallback_window ? "当前日期窗口内无新闻，已回溯至最近有新闻的日期。" : ""}</p>`;
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
    const target = [draft.runtime !== "codex" ? selected.provider : "Codex", selected.model || "本机默认", selected.reasoning_effort || "默认推理强度"].join(" · ");
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
    this.renderApiTests(provider);
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
    this.invalidateApiTest(state);
    state.notice = "";
    this.updateTranslationRoute();
    this.updateDecisionKeys();
  }
  async deleteSecret(provider) {
    const state = this.apiState(provider), config = this.apiConfig(provider);
    if (state.savingSecret) return;
    const status = this.saved.api_status[provider], draft = this.draft, secret = state.secretDraft, editing = state.editingSecret;
    if (!await confirmAction({ title: `删除 ${config.label} 密钥`, trigger: `#${this.apiId(provider, "deleteSecretBtn")}`, message: `删除已保存的 ${config.label} API 密钥？删除后，依赖该密钥的服务需重新配置。${status?.environment_override ? "环境变量提供的密钥仍会生效。" : ""}`, action: "删除密钥" })) return;
    if (this.apiState(provider) !== state || this.saved.api_status[provider] !== status || this.draft !== draft || state.secretDraft !== secret || state.editingSecret !== editing) return toast("密钥设置已变化，请重新操作");
    state.savingSecret = true;
    this.renderApiTests(provider);
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
          feed: { hidden_source_subkeys: [...hidden] },
        },
      });
      const previous = new Set(this.saved.feed.hidden_source_subkeys);
      const updated = new Set(data.feed.hidden_source_subkeys);
      const draft = new Set(this.draft.feed.hidden_source_subkeys);
      for (const key of new Set([...previous, ...updated])) {
        if (draft.has(key) === previous.has(key)) {
          if (updated.has(key)) draft.add(key);
          else draft.delete(key);
        }
      }
      this.saved.feed = data.feed;
      this.draft.feed.hidden_source_subkeys = [...draft];
      if ($("#settingsSources")) this.renderSourceLists();
      this.refreshActions();
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
