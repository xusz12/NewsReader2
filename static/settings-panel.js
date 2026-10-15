import {
  $,
  $$,
  api,
  escapeHTML as e,
  errorText,
  toast,
  busy,
  focusTrap,
} from "./ui.js";

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
    values.push({ value, label: `${value}（当前设置）` });
  return `<label class="field-item"><span class="field-label">${label}</span><select class="field-select" name="${path}"><option value="">${placeholder}</option>${values.map((x) => `<option value="${e(x.value)}" ${x.value === value ? "selected" : ""}>${e(x.label || x.value)}</option>`).join("")}</select></label>`;
}
export class SettingsPanel {
  constructor({ onSaved, onSources }) {
    this.onSaved = onSaved;
    this.onSources = onSources;
    this.open = false;
    this.dirty = false;
    this.section = "models";
    this.connectionSequence = 0;
    this.catalogSequence = 0;
    this.sourceCounts = [];
    $("#closeSettingsBtn").onclick = () => this.close();
    $("#settingsDrawerBackdrop").onclick = () => this.close();
    $("#settingsSaveBtn").onclick = (event) =>
      busy(event.currentTarget, () => this.save());
    $$(".settings-tab-btn").forEach(
      (button) =>
        (button.onclick = () => this.scrollTo(button.dataset.settingsTab)),
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
    this.open = true;
    $("#settingsRightDrawer").classList.add("show");
    $("#settingsRightDrawer").inert = false;
    $("#settingsDrawerBackdrop").classList.add("show");
    $("#leftNavSettingsBtn").classList.add("active");
    $("#closeSettingsBtn").focus();
    $("#feedView").inert = true;
    $(".topbar").inert = true;
    $("#mobileBottomNav").inert = true;
    if (this.saved) this.render();
    else {
      $("#settingsContent").innerHTML =
        '<div class="list-message">正在读取配置…</div>';
      await this.load();
      this.render();
    }
  }
  close() {
    this.open = false;
    $("#settingsRightDrawer").classList.remove("show");
    $("#settingsRightDrawer").inert = true;
    $("#settingsDrawerBackdrop").classList.remove("show");
    $("#leftNavSettingsBtn").classList.remove("active");
    $("#feedView").inert = false;
    $(".topbar").inert = false;
    $("#mobileBottomNav").inert = false;
    this.lastFocus?.focus();
    // Unsaved settings remain a draft when the drawer is closed.
  }
  handleKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
    } else focusTrap(event, $("#settingsRightDrawer"));
  }
  status(text, failed = false) {
    $("#settingsStatus").textContent = text;
    $("#settingsStatus").classList.toggle("error-state", failed);
  }
  scrollTo(section) {
    this.section = section;
    $$(".settings-tab-btn").forEach((button) =>
      button.classList.toggle("active", button.dataset.settingsTab === section),
    );
    const target = $(`[data-section="${section}"]`);
    if (target)
      $(".settings-main").scrollTo({
        top: target.offsetTop - 24,
        behavior: "smooth",
      });
  }
  payload() {
    const d = this.draft;
    return {
      llm: clone(d.llm),
      feed: clone(d.feed),
      agent: clone(d.agent),
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
    if (!input.name || !this.draft) return;
    const unchanged = get(this.draft, input.name) === input.value;
    if (unchanged && event.type !== "change") return;
    set(
      this.draft,
      input.name,
      input.type === "number" || input.name === "agent.session_ttl_hours"
        ? Number(input.value)
        : input.value,
    );
    this.dirty = true;
    this.status("有未保存的更改");
    this.connectionSequence++;
    this.connectionResult = "[READY] 配置已变化，请重新测试。";
    if ($("#connTestTerminal"))
      $("#connTestTerminal").textContent = this.connectionResult;
    if (["agent.codex.provider", "agent.codex.profile"].includes(input.name)) {
      this.catalog = null;
      this.catalogSequence++;
      this.draft.agent.codex.reasoning_effort = "";
    }
    if (input.name === "agent.codex.model" && event.type === "change") {
      this.draft.agent.codex.reasoning_effort = "";
      this.renderModels();
    }
    if (input.name === "llm.pi_chat.provider" && event.type === "change") {
      this.draft.llm.pi_chat.model = "";
      this.renderModels();
    }
  }
  render() {
    if (!this.draft) return;
    const d = this.draft;
    $("#settingsContent").innerHTML =
      `<section class="settings-section-card" data-section="models" id="modelSettings"></section>
      <section class="settings-section-card" data-section="deepseek"><div class="section-card-title"><span>DeepSeek 结构化精读与翻译管道</span></div>
      <p class="muted">为单篇新闻提供中文正文与结构化精读。</p>
      ${select("翻译模型", "llm.translation.model", d.llm.translation.model, d.model_catalogs?.translation?.options || [], "使用默认模型")}
      ${field("自定义模型（可选）", "llm.translation.model", d.llm.translation.model, 'maxlength="120"')}
      <div class="field-item"><label class="field-label" for="deepseekSecret">API 密钥</label><input class="field-input" id="deepseekSecret" type="password" autocomplete="new-password" placeholder="输入新的密钥，不显示已保存值"><p class="muted">${e(d.api_status?.deepseek?.configured ? "已配置密钥" : "尚未配置密钥")}</p><div class="button-row"><button class="btn-secondary" id="saveSecretBtn" type="button">保存密钥</button><button class="reader-action-btn danger" id="deleteSecretBtn" type="button" ${d.api_status?.deepseek?.configured ? "" : "disabled"}>删除密钥</button></div></div></section>
      <section class="settings-section-card" data-section="pipeline"><div class="section-card-title"><span>信源增量采集与 opencli 适配器</span></div><p class="muted">保存信源开关后更新新闻流，保留原有阅读状态。正文抓取由本机 opencli 执行，可在文章中查看结果。</p><div id="settingsSources">${this.sourceHTML()}</div><button class="btn-secondary" id="settingsReindexBtn" type="button">重新扫描 DailyNews</button></section>
      <section class="settings-section-card" data-section="storage"><div class="section-card-title"><span>本地 SQLite 数据库与存储路径</span></div>
      <div class="field-item"><span class="field-label">当前主数据库路径</span><code class="storage-path">${e(d.database.path)}</code></div>
      ${field("下次启动使用的数据库", "database.configured_path", d.database.configured_path, d.database.environment_override ? "disabled" : 'placeholder="已有 SQLite 文件的完整路径"')}
      <button class="btn-secondary" id="pickDatabaseBtn" type="button" ${d.database.environment_override || !d.database.native_picker_available ? "disabled" : ""}>选择文件…</button><p class="muted">${e(d.database.notice)}</p></section>
      <section class="settings-section-card" data-section="system"><div class="section-card-title"><span>系统状态与版本升级</span></div><div><strong>News Reader ${e($(".brand-version").textContent)}</strong><p class="muted">独立项目版本；配置本项目更新仓库后可检查稳定版本。</p></div><div class="button-row"><button class="btn-secondary" id="checkUpdateBtn" type="button">检查更新</button><button class="btn-primary" id="applyUpdateBtn" type="button" hidden>确认更新</button></div><div id="updateResult" role="status"></div><details><summary>版本记录</summary><div id="releaseNotes" class="muted">展开后读取</div></details></section>`;
    this.renderModels();
    this.renderUpdate();
    $("#settingsContent details").ontoggle = (event) => {
      if (event.target.open) this.loadReleaseNotes();
    };
    this.status(this.dirty ? "有未保存的更改" : "配置已加载");
  }
  renderModels() {
    if (!$("#modelSettings")) return;
    const d = this.draft,
      codex = d.agent.runtime === "codex";
    const catalog =
      this.catalog ||
      (codex ? d.model_catalogs?.codex : d.model_catalogs?.pi_chat) ||
      {};
    const model = catalog.options?.find((x) => x.value === d.agent.codex.model);
    const efforts = model?.reasoning_efforts || [];
    const savedEffort = d.agent.codex.reasoning_effort;
    $("#modelSettings").innerHTML =
      `<div class="section-card-title"><span>Chat 与新闻研究 Agent 模型</span></div><div class="field-item"><span class="field-label">运行方式 (Runtime) 选择</span><div class="runtime-switch-grid"><button class="runtime-card ${codex ? "selected" : ""}" type="button" data-runtime="codex" aria-pressed="${codex}"><span class="runtime-card-top">Codex CLI</span><span class="runtime-card-desc">使用本机模型、预设与只读文件沙盒配置。</span></button><button class="runtime-card ${!codex ? "selected" : ""}" type="button" data-runtime="pi" aria-pressed="${!codex}"><span class="runtime-card-top">Pi SDK / OAR</span><span class="runtime-card-desc">本机会话桥接、技能扩展与模型目录。</span></button></div></div>
    ${codex ? `<div class="form-group-grid">${field("服务商覆盖（可选）", "agent.codex.provider", d.agent.codex.provider)}${field("本机 Profile（可选）", "agent.codex.profile", d.agent.codex.profile)}</div><div class="form-group-grid">${select("模型选择 (Model)", "agent.codex.model", d.agent.codex.model, catalog.options || [], "沿用本机默认")}${select("推理强度 (Reasoning Effort)", "agent.codex.reasoning_effort", savedEffort, efforts, "沿用默认")}</div>${field("自定义模型（可选）", "agent.codex.model", d.agent.codex.model, 'maxlength="120"')}<p class="muted">${efforts.length ? "推理档位来自当前模型目录。" : "当前模型能力未确认；保留已保存档位，或沿用本机默认。"}</p>` : `<div class="form-group-grid">${select("服务商 (Provider)", "llm.pi_chat.provider", d.llm.pi_chat.provider, catalog.provider_options || [], "请选择服务商")}${select("模型选择 (Model)", "llm.pi_chat.model", d.llm.pi_chat.model, catalog.model_options_by_provider?.[d.llm.pi_chat.provider] || catalog.options || [], "请选择模型")}</div>`}
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
      this.catalog = null;
      this.connectionSequence++;
      this.catalogSequence++;
      this.connectionResult = "";
      this.renderModels();
      this.status("有未保存的更改");
      return;
    }
    const actions = {
      retrySettingsBtn: () => this.load(),
      refreshModelsBtn: () => this.refreshModels(),
      testConnectionBtn: () => this.testConnection(),
      saveSecretBtn: () => this.saveSecret(),
      deleteSecretBtn: () => this.deleteSecret(),
      pickDatabaseBtn: () => this.pickDatabase(),
      settingsReindexBtn: async () => {
        await api("/api/reindex", { method: "POST" });
        toast("新闻索引已刷新");
        this.onSaved();
      },
      clearSessionsBtn: async () => {
        if (confirm("清空全部临时 Agent 对话与任务？新闻和想法会保留。")) {
          await api("/api/agent/sessions", { method: "DELETE" });
          toast("临时会话已清空");
        }
      },
      checkUpdateBtn: () => this.checkUpdate(),
      applyUpdateBtn: () => this.applyUpdate(),
    };
    if (actions[button.id]) busy(button, actions[button.id]);
  }
  async save() {
    if (!this.draft) return;
    const payload = this.payload();
    const fingerprint = JSON.stringify(payload);
    this.status("正在保存…");
    try {
      const data = await api("/api/settings", { method: "PUT", body: payload });
      this.saved = data;
      if (fingerprint === JSON.stringify(this.payload())) {
        this.draft = clone(data);
        this.dirty = false;
      }
      this.status(
        this.dirty ? "已保存提交的配置，仍有新的更改未保存" : "设置已保存",
      );
      this.onSources();
      this.onSaved();
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
      this.catalog = data.catalog;
      this.catalogNotice =
        data.catalog.error || data.catalog.notice || "模型目录已刷新";
      this.renderModels();
    } catch (error) {
      this.catalogNotice = errorText(error);
      this.renderModels();
      throw error;
    }
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
    await api("/api/settings/secrets/deepseek", {
      method: "PUT",
      body: { key },
    });
    input.value = "";
    toast("密钥已保存");
    await this.load();
  }
  async deleteSecret() {
    if (!confirm("删除已保存的 DeepSeek API 密钥？")) return;
    await api("/api/settings/secrets/deepseek", { method: "DELETE" });
    toast("密钥已删除");
    await this.load();
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
  async checkUpdate() {
    this.update = await api("/api/update/check");
    this.renderUpdate();
  }
  renderUpdate() {
    if (!$("#updateResult")) return;
    const data = this.update;
    if (!data) return;
    if (data.enabled === false) {
      $("#updateResult").textContent =
        `当前 ${data.current_version}。${data.message}`;
      $("#applyUpdateBtn").hidden = true;
      return;
    }
    $("#updateResult").textContent = data.available
      ? `发现 ${data.latest.version} · ${data.latest.commit.slice(0, 8)}。${data.managed_launcher ? "确认后更新并重启。" : "请通过受控启动器启动后更新。"}`
      : `当前 ${data.current_version}，没有更高稳定版本。`;
    $("#applyUpdateBtn").hidden = !(data.available && data.managed_launcher);
  }
  async applyUpdate() {
    const d = this.update;
    if (
      !d?.available ||
      !confirm(
        `确认更新到 ${d.latest.version}（${d.latest.commit.slice(0, 8)}）并重启？`,
      )
    )
      return;
    const result = await api("/api/update/apply", {
      method: "POST",
      body: { check_token: d.check_token, ...d.latest },
    });
    $("#applyUpdateBtn").hidden = true;
    for (let i = 0; i < 90; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const status = await api("/api/update/status");
        if (status.run_id !== result.run_id) continue;
        $("#updateResult").textContent =
          `更新状态：${status.status}${status.error ? " · " + status.error : ""}`;
        if (
          ["healthy", "failed", "rolled_back", "rollback_failed"].includes(
            status.status,
          )
        )
          return;
      } catch {
        /* Server can be restarting. */
      }
    }
    $("#updateResult").textContent = "仍未确认重启结果，请检查服务状态。";
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
