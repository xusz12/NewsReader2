import { $, $$, api, escapeHTML as e, errorText, toast, busy, singlePane, drawerNavigation } from "./ui.js";

import { UpdatePanel } from "./update-panel.js";
import { visibilityText } from "./summary-panel.js";

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
  return `<div class="field-item"><span class="field-label" id="${id}-label">${label}</span><select id="${id}" class="field-select" name="${path}" aria-labelledby="${id}-label"><option value="">${placeholder}</option>${values.map((x) => `<option value="${e(x.value)}" ${x.custom ? 'data-custom-option="true"' : ""} ${x.value === value ? "selected" : ""}>${e(x.label || x.value)}</option>`).join("")}</select></div>`;
}
export class SettingsPanel {
  constructor({ onSaved, onSources, onOpen, onViewChange, selects }) {
    this.onOpen = onOpen;
    this.onViewChange = onViewChange;
    this.onSaved = onSaved;
    this.onSources = onSources;
    this.open = false;
    this.dirty = false;
    this.section = "models";
    this.connectionSequence = 0;
    this.catalogSequence = 0;
    this.catalogs = new Map();
    this.selects = selects;
    this.updater = new UpdatePanel({ hasUnsavedChanges: () => this.dirty || !!this.secretDraft });
    $("#closeSettingsBtn").onclick = () => this.close();
    $("#settingsResetBtn").onclick = () => this.resetDraft();
    $("#settingsCategoryBackBtn").onclick = () => this.showCategories();
    $("#settingsSearchInput").oninput = () => this.filterCategories();
    $("#clearSettingsSearchBtn").onclick = () => {
      $("#settingsSearchInput").value = "";
      this.filterCategories();
      $("#settingsSearchInput").focus();
    };
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
    if (
      event.target.closest('input,textarea,select,[role="combobox"],[contenteditable="true"]') ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      event.isComposing
    )
      return;
    if (event.key === ",") {
      event.preventDefault();
      this.close();
    } else if (event.key === "/") {
      event.preventDefault();
      this.showCategories(false);
      $("#settingsSearchInput").focus();
    } else if (["j", "k"].includes(event.key.toLowerCase())) {
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
              current + (event.key.toLowerCase() === "j" ? 1 : -1),
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
    $("#settingsStatus").textContent = text;
    $("#settingsStatus").classList.toggle("error-state", failed);
    $("#settingsStatus").classList.toggle("dirty-state", this.dirty);
    $("#settingsResetBtn").disabled = !this.dirty;
  }
  filterCategories() {
    const query = $("#settingsSearchInput").value.trim().toLocaleLowerCase();
    const buttons = $$("[data-settings-tab]");
    for (const button of buttons) {
      const text =
        `${button.textContent} ${button.dataset.search}`.toLocaleLowerCase();
      button.hidden = !query.split(/\s+/).every((word) => text.includes(word));
    }
    const count = buttons.filter((button) => !button.hidden).length;
    $("#settingsCategoryCount").textContent = `${count} 个配置分类`;
    $("#settingsSearchEmpty").hidden = count > 0;
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
    $("#settingsDetailDescription").textContent = selected.dataset.description;
    if (changed) $(".settings-main").scrollTop = 0;
    if (section === "system" && $("#updateResult")) this.updater.restore();
    if (section === "catchup" && $("#settingsCatchupScope")) this.loadCatchupScope();
    if (reveal && singlePane.matches) {
      $("#feedView").classList.add("settings-detail-active");
      this.onViewChange?.();
      // The panel is still sliding in from outside the viewport. Default focus
      // would scroll its overflow-hidden parent sideways to reveal the title.
      $("#settingsDetailTitle").focus({ preventScroll: true });
    }
  }
  showCategories(focus = true) {
    this.selects.close(false);
    $("#feedView").classList.remove("settings-detail-active");
    this.onViewChange?.();
    if (focus) {
      const selected = $(`[data-settings-tab="${this.section}"]`);
      (selected.hidden ? $("#settingsSearchInput") : selected).focus({ preventScroll: true });
    }
  }
  resetDraft() {
    if (!this.saved) return;
    this.draft = clone(this.saved);
    this.dirty = false;
    this.translationCatalog = null;
    this.translationSequence = (this.translationSequence || 0) + 1;
    this.translationNotice = "";
    this.translationTestResult = "";
    this.editingSecret = false;
    this.secretDraft = "";
    this.catalogNotice = "";
    this.catalogSequence++;
    this.connectionSequence++;
    this.connectionResult = "";
    this.render();
    this.status("已恢复已保存的配置");
  }
  payload() {
    const d = this.draft;
    return {
      llm: clone(d.llm),
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
    if (input.id === "deepseekSecret") { this.secretDraft = input.value; return; }
    if (!input.name || !this.draft) return;
    const value = input.type === "number" || input.name === "agent.session_ttl_hours"
      ? Number(input.value) : input.value;
    const unchanged = Object.is(get(this.draft, input.name), value);
    // Numeric fields emit change again on blur, after input already updated the draft.
    if (unchanged && (input.name.startsWith("news_summary.") || event.type !== "change")) return;
    set(this.draft, input.name, value);
    if (input.name === "llm.translation.model") {
      this.translationSequence = (this.translationSequence || 0) + 1;
      this.translationNotice = "模型已变化，可以测试当前选择。";
      this.translationTestResult = "[READY] 模型已变化，请重新测试当前选择。";
      $("#translationTestTerminal").textContent = this.translationTestResult;
      $("#translationStatus").textContent = this.translationNotice;
      this.syncModelFields(input, this.translationOptions());
    }
    if (input.name === "agent.codex.model") {
      this.syncModelFields(input, this.codexOptions());
      if (!unchanged) {
        this.draft.agent.codex.reasoning_effort = "";
        this.renderReasoning();
      }
    }
    this.dirty = true;
    this.status("有未保存的更改");
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
      <section class="settings-section-card" data-section="deepseek" id="translationSettings"></section>
      <section class="settings-section-card" data-section="catchup"><div class="section-card-title"><span>积压新闻总结范围</span></div>
      <div class="form-group-grid">${field("最多自然日（含起止）", "news_summary.max_days", d.news_summary?.max_days ?? 20, 'type="number" min="1" max="9007199254740991" step="1" required')}${field("最多未读条数", "news_summary.max_items", d.news_summary?.max_items ?? 6000, 'type="number" min="1" max="9007199254740991" step="1" required')}</div>
      <div class="catchup-scope-preview" id="settingsCatchupScope" role="status">正在读取当前生效范围…</div>
      <button class="btn-secondary" id="resetCatchupDefaultsBtn" type="button">恢复默认范围（20 天 / 6000 条）</button></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>信源增量采集与 opencli 适配器</span></div><p class="muted">保存信源开关后更新新闻流，保留原有阅读状态。正文抓取由本机 opencli 执行，可在文章中查看结果。</p><div id="settingsSources">${this.sourceHTML()}</div><button class="btn-secondary" id="settingsReindexBtn" type="button">重新扫描 DailyNews</button></section>
      <section class="settings-section-card" data-section="storage"><div class="section-card-title"><span>本地 SQLite 数据库与存储路径</span></div>
      <div class="field-item"><span class="field-label">当前主数据库路径</span><code class="storage-path">${e(d.database.path)}</code></div>
      ${field("下次启动使用的数据库", "database.configured_path", d.database.configured_path, d.database.environment_override ? "disabled" : 'placeholder="已有 SQLite 文件的完整路径"')}
      <button class="btn-secondary" id="pickDatabaseBtn" type="button" ${d.database.environment_override || !d.database.native_picker_available ? "disabled" : ""}>选择文件…</button><p class="muted">${e(d.database.notice)}</p></section>
      <section class="settings-section-card" data-section="system"><div class="section-card-title"><span>系统状态与版本升级</span></div><div><strong id="updateCurrent"></strong><p class="muted">从本项目仓库获取稳定版本，更新后自动重启；启动检查失败时尝试恢复旧版本。</p><div class="field-item"><span class="field-label">更新源</span><code id="updateRepository" class="storage-path"></code></div></div><div class="button-row"><button class="btn-secondary" id="checkUpdateBtn" type="button">检查更新</button><button class="btn-primary" id="applyUpdateBtn" type="button" hidden>确认更新</button><button class="btn-primary" id="refreshUpdateBtn" type="button" hidden>刷新使用新版本</button><button class="btn-secondary" id="resumeUpdateBtn" type="button" hidden>继续查看进度</button></div><div id="updateResult" role="status" aria-live="polite"></div><details><summary>版本记录</summary><div id="releaseNotes" class="muted">展开后读取</div></details></section>`;
    this.renderModels();
    this.renderTranslation();
    this.updater.render();
    this.selectSection(this.section, { reveal: false });
    $("#settingsContent details").ontoggle = (event) => {
      if (event.target.open) this.loadReleaseNotes();
    };
    this.status(this.dirty ? "有未保存的更改" : "配置已加载");
  }
  translationOptions() {
    if (!this.draft.api_status?.deepseek?.configured) return [];
    return (this.translationCatalog || this.draft.model_catalogs?.translation)?.options?.filter(option => option.source !== "saved") || [];
  }
  renderTranslation() {
    const d = this.draft, service = d.api_status?.deepseek || {};
    const configured = service.configured, editing = !configured || this.editingSecret;
    const options = this.translationOptions();
    const custom = options.some(option => option.value === d.llm.translation.model) ? "" : d.llm.translation.model;
    $("#translationSettings").innerHTML = `<div class="section-card-title"><span>DeepSeek 结构化精读与翻译管道</span></div>
      <p class="muted">为单篇新闻提供中文正文与结构化精读。</p>
      <div class="translation-model-row">${select("翻译模型", "llm.translation.model", d.llm.translation.model, options, configured ? "使用默认模型" : "请先配置 API 密钥")}<button class="btn-secondary" id="translationModelsBtn" type="button" ${configured ? "" : "hidden"}>获取模型</button></div>
      ${field("自定义模型（可选）", "llm.translation.model", custom, 'maxlength="120"')}
      <p class="muted" id="translationStatus" role="status">${e(configured ? this.translationNotice || "选择目录中的模型，或填写自定义模型。测试使用当前选择，无需先保存。" : "保存 API 密钥后可获取和测试模型；已填写的模型名称会保留。")}</p>
      <div class="field-item"><label class="field-label" for="deepseekSecret">API 密钥</label>
      ${configured ? `<code class="masked-secret">${e(service.masked_key || "••••••••")}</code>` : '<p class="muted">尚未配置密钥</p>'}
      ${service.environment_override ? '<p class="muted">当前由 DEEPSEEK_API_KEY 环境变量提供；保存和删除仅影响本地钥匙串，环境变量仍优先生效。</p>' : ""}
      ${editing ? `<input class="field-input" id="deepseekSecret" type="password" autocomplete="new-password" value="${e(this.secretDraft || "")}" placeholder="输入新的 API 密钥"><div class="button-row"><button class="btn-secondary" id="saveSecretBtn" type="button">保存密钥</button>${configured ? '<button class="btn-secondary" id="cancelSecretBtn" type="button">取消</button>' : ""}</div>` : '<div class="button-row"><button class="btn-secondary" id="editSecretBtn" type="button">更新密钥</button><button class="reader-action-btn danger" id="deleteSecretBtn" type="button">删除密钥</button></div>'}</div>
      <div class="connection-test-section"><div><strong>翻译模型独立连接测试</strong><p class="muted">使用当前选择测试，无需先保存。</p></div><button class="btn-primary" id="translationTestBtn" type="button" ${configured ? "" : "disabled"}>测试模型</button><div class="terminal-box" id="translationTestTerminal" role="status">${e(this.translationTestResult || "[READY] 连接测试器就绪 · 点击上方按钮发起探测")}</div></div>`;
    this.selects.refresh($("#translationSettings"));
  }
  async refreshTranslationModels() {
    const seq = this.translationSequence = (this.translationSequence || 0) + 1;
    const data = await api("/api/settings/translation/models", { method: "POST", body: {} });
    if (seq !== this.translationSequence) return;
    this.translationCatalog = data.catalog;
    this.translationNotice = `已获取 ${data.catalog.options.length} 个模型`;
    this.renderTranslation();
  }
  async testTranslation() {
    const model = this.draft.llm.translation.model;
    const seq = this.translationSequence = (this.translationSequence || 0) + 1;
    $("#translationTestTerminal").textContent = "[RUNNING] 正在测试当前模型…";
    try {
      const data = await api("/api/settings/translation/test", { method: "POST", body: { model } });
      if (seq === this.translationSequence && model === this.draft.llm.translation.model) this.translationTestResult = `[PASS] ${data.message} · ${data.model} · ${data.elapsed_ms}ms`;
    } catch (error) {
      if (seq === this.translationSequence) this.translationTestResult = `[FAIL] ${errorText(error)}`;
    }
    if (seq === this.translationSequence) $("#translationTestTerminal").textContent = this.translationTestResult;
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
      `<div class="section-card-title"><span>Chat、新闻研究 Agent 与积压总结模型</span></div><div class="field-item"><span class="field-label">运行方式 (Runtime) 选择</span><div class="runtime-switch-grid"><button class="runtime-card ${codex ? "selected" : ""}" type="button" data-runtime="codex" aria-pressed="${codex}"><span class="runtime-card-top">Codex CLI</span><span class="runtime-card-desc">使用本机模型、预设与只读文件沙盒配置。</span></button><button class="runtime-card ${!codex ? "selected" : ""}" type="button" data-runtime="pi" aria-pressed="${!codex}"><span class="runtime-card-top">Pi SDK / OAR</span><span class="runtime-card-desc">本机会话桥接、技能扩展与模型目录。</span></button></div></div>
    ${codex ? `<div class="form-group-grid">${field("服务商覆盖（可选）", "agent.codex.provider", d.agent.codex.provider)}${field("本机 Profile（可选）", "agent.codex.profile", d.agent.codex.profile)}</div><div class="form-group-grid">${select("模型选择 (Model)", "agent.codex.model", d.agent.codex.model, codexOptions, "沿用本机默认")}${select("推理强度 (Reasoning Effort)", "agent.codex.reasoning_effort", savedEffort, efforts, "沿用默认")}</div>${field("自定义模型（可选）", "agent.codex.model", custom, 'maxlength="120"')}<p class="muted" id="modelCapabilitiesHint">${efforts.length ? "推理档位来自当前模型目录。" : "当前模型能力未确认；保留已保存档位，或沿用本机默认。"}</p>` : `<div class="form-group-grid">${select("服务商 (Provider)", "llm.pi_chat.provider", d.llm.pi_chat.provider, catalog.provider_options || [], "请选择服务商")}${select("模型选择 (Model)", "llm.pi_chat.model", d.llm.pi_chat.model, catalog.model_options_by_provider?.[d.llm.pi_chat.provider] || catalog.options || [], "请选择模型")}</div>`}
    <button class="btn-secondary" id="refreshModelsBtn" type="button">刷新模型目录</button><p class="muted" id="modelsNotice">${e(this.catalogNotice || catalog.notice || "候选目录不代表实际调用权限。")}</p>
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
      retryCatchupScopeBtn: () => this.loadCatchupScope(),
      resetCatchupDefaultsBtn: () => {
        this.draft.news_summary = { max_days: 20, max_items: 6000 };
        this.dirty = true;
        this.render();
      },
      retrySettingsBtn: () => this.load(),
      refreshModelsBtn: () => this.refreshModels(),
      testConnectionBtn: () => this.testConnection(),
      editSecretBtn: () => { this.editingSecret = true; this.renderTranslation(); $("#deepseekSecret").focus(); },
      cancelSecretBtn: () => { this.secretDraft = ""; this.editingSecret = false; this.renderTranslation(); },
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
        if (confirm("清空当前新闻库的临时 Agent 对话与任务？新闻、想法和尚未归属的旧会话会保留。")) {
          const result = await api("/api/agent/sessions", { method: "DELETE" });
          toast(result.preserved_legacy ? "当前库临时会话已清空；尚未归属的旧会话已保留" : "当前库临时会话已清空");
        }
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
    const invalid = $$('#settingsContent input[type="number"]').find(input => !input.checkValidity());
    if (invalid) {
      this.selectSection(invalid.closest("[data-section]").dataset.section);
      invalid.reportValidity();
      this.status("总结范围必须填写有效的正整数", true);
      return;
    }
    const payload = this.payload();
    const fingerprint = JSON.stringify(payload);
    this.status("正在保存…");
    try {
      const data = await api("/api/settings", { method: "PUT", body: payload });
      this.saved = data;
      this.rememberModelCatalogs(data);
      if (fingerprint === JSON.stringify(this.payload())) {
        this.draft = clone(data);
        this.dirty = false;
      }
      this.status(
        this.dirty ? "已保存提交的配置，仍有新的更改未保存" : "设置已保存",
      );
      this.onSources();
      this.onSaved();
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
    const input = $("#deepseekSecret");
    const key = input.value.trim();
    if (!key) {
      toast("请输入新的 API 密钥");
      return;
    }
    const data = await api("/api/settings/secrets/deepseek", {
      method: "PUT",
      body: { key },
    });
    input.value = "";
    toast("密钥已保存");
    this.applySecretResponse(data);
  }
  applySecretResponse(data) {
    this.secretDraft = "";
    this.rememberModelCatalogs(data);
    this.saved.api_status = clone(data.api_status);
    this.draft.api_status = clone(data.api_status);
    this.saved.model_catalogs = clone(data.model_catalogs);
    this.draft.model_catalogs = clone(data.model_catalogs);
    this.editingSecret = false;
    this.translationCatalog = null;
    this.translationSequence = (this.translationSequence || 0) + 1;
    this.translationNotice = "";
    this.translationTestResult = "";
    this.renderTranslation();
  }
  async deleteSecret() {
    if (!confirm("删除已保存的 DeepSeek API 密钥？")) return;
    const data = await api("/api/settings/secrets/deepseek", { method: "DELETE" });
    toast("密钥已删除");
    this.applySecretResponse(data);
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
