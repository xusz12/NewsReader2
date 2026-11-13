import { $, api, escapeHTML as e, errorText } from "./ui.js";
import { selectField } from "./form-controls.js";
import { confirmAction } from "./confirm-dialog.js";

const base = "/api/personal-assistant";
const field = (label, key, value, extra = "") => `<label class="field-item"><span class="field-label">${label}</span><input class="field-input" data-personal-field="${key}" value="${e(value)}" ${extra}></label>`;

export class PersonalAssistantSettings {
  constructor(selects) { this.selects = selects; this.files = []; this.editors = new Map(); this.path = "MEMORY.md"; this.sequence = 0; }
  mount(host) {
    this.host = host;
    host.oninput = event => this.change(event);
    host.onchange = event => this.change(event);
    host.onclick = event => {
      const button = event.target.closest("[data-personal-action]");
      if (!button) return;
      const actions = {save:()=>this.save(), reset:()=>{this.draft=structuredClone(this.saved);this.notice="";this.render();},
        catalog:()=>this.loadCatalog(), refresh:()=>this.load(), memorySave:()=>this.saveMemory(), memoryReset:()=>this.resetMemory(), memoryDelete:()=>this.deleteMemory()};
      void actions[button.dataset.personalAction]?.();
    };
    this.render();
  }
  get dirty() { return this.draft && JSON.stringify(this.draft) !== JSON.stringify(this.saved); }
  async load() {
    if (this.loading) return;
    this.loading = true;
    try {
      const [profile, memory] = await Promise.all([api(base+"/profile"), api(base+"/memory")]);
      if (!this.dirty) this.draft = structuredClone(profile.profile);
      this.saved = profile.profile;
      this.files = memory.files;
      this.notice = "";
      this.render();
      await this.loadMemory(this.path);
      if (!this.catalog) void this.loadCatalog();
    } catch (error) { this.notice = errorText(error); this.render(); }
    finally { this.loading = false; }
  }
  async loadCatalog() {
    if (!this.draft) return;
    const seq = ++this.sequence, draft = structuredClone(this.draft);
    this.catalogLoading = true; this.catalogNotice = "正在读取模型…"; this.render();
    try {
      const data = await api(base+"/model-options", {method:"POST", body:draft});
      if (seq !== this.sequence) return;
      this.catalog = data.catalog; this.catalogNotice = data.catalog.notice || "模型列表已更新";
    } catch (error) { if (seq === this.sequence) this.catalogNotice = errorText(error); }
    finally { if (seq === this.sequence) { this.catalogLoading = false; this.render(); } }
  }
  render() {
    if (!this.host) return;
    const focused = this.host.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset.personalField, memoryFocus = focused?.hasAttribute("data-personal-memory");
    const selection = focused && "selectionStart" in focused ? [focused.selectionStart, focused.selectionEnd] : null;
    this.advancedOpen = this.host.querySelector("details")?.open ?? this.advancedOpen;
    if (!this.draft) {
      this.host.innerHTML = `<div class="list-message" role="status">${e(this.notice || "正在读取个人助手…")}</div><button type="button" class="btn-secondary" data-personal-action="refresh">重新读取</button>`;
      return;
    }
    const d = this.draft, runtime = d.runtime, c = d[runtime], options = this.catalog?.options || [];
    const providers = [...new Set(options.map(x=>x.provider).filter(Boolean))];
    const models = runtime === "pi" ? options.filter(x=>x.provider === c.provider) : options;
    const selected = models.find(x=>x.value === c.model);
    const efforts = selected?.reasoning_efforts || selected?.supported_reasoning_efforts || [];
    this.host.innerHTML = `<section class="settings-section-card">
      <div class="section-card-title"><span>身份与工作区</span></div>
      <fieldset class="form-fieldset section-stack" ${this.saving ? "disabled" : ""}>
      ${field("助手的名字", "name", d.name, 'maxlength="40" required placeholder="请输入你为助手取的名字"')}
      <label class="field-item"><span class="field-label">助手职责</span><textarea class="field-input" data-personal-field="instructions" rows="3" maxlength="4000">${e(d.instructions)}</textarea></label>
      <div class="field-item"><span class="field-label">本地工作区</span><code class="storage-path">${e(d.workspace)}</code></div>
      <p class="muted">身份、讨论和记忆长期保存在本机。模型仍通过所选服务运行，查询到的资料会提供给该模型。</p>
      <div class="section-card-title"><span>个人助手模型</span></div>
      <div class="form-group-grid">${selectField("Runtime", "personal.runtime", runtime, [{value:"pi",label:"Pi"},{value:"codex",label:"Codex"}], null)}
      ${runtime === "pi" ? selectField("服务商", "personal.pi.provider", c.provider, providers, "请选择") : ""}
      ${selectField("模型", `personal.${runtime}.model`, c.model, models, runtime === "pi" ? "请选择模型" : "沿用本机默认")}
      ${selectField("推理强度", `personal.${runtime}.reasoning_effort`, c.reasoning_effort || "", efforts.map(x=>typeof x === "string" ? x : {value:x.reasoning_effort || x.value,label:x.description || x.label || x.reasoning_effort}), "沿用默认")}</div>
      ${runtime === "codex" ? `<details ${this.advancedOpen ? "open" : ""}><summary>高级配置</summary><div class="form-group-grid">${field("Profile（可选）", "codex.profile", c.profile, 'autocomplete="off"')}${field("服务商（可选）", "codex.provider", c.provider || "", 'autocomplete="off"')}</div></details>` : ""}
      <div class="button-row"><button type="button" class="btn-secondary" data-personal-action="catalog" ${this.catalogLoading ? "disabled" : ""}>刷新模型</button><span class="muted" role="status">${e(this.catalogNotice || "")}</span></div>
      <p class="muted">切换模型会重新建立运行上下文，名字、讨论记录和长期记忆继续保留。此处只设置个人助手。</p>
      <div class="button-row"><button type="button" class="btn-primary" data-personal-action="save">${this.saving ? "正在保存…" : "保存个人助手"}</button><button type="button" class="btn-secondary" data-personal-action="reset">撤销更改</button></div></fieldset>
      <p class="status-text muted" id="personalSettingsStatus" role="status">${e(this.notice || "")}</p></section>
      <section class="settings-section-card"><div class="section-card-title"><span>长期记忆</span><button type="button" class="reader-tool-btn" data-personal-action="refresh">刷新</button></div>
      <p class="muted">助手会整理你明确表达的偏好、观点与判断依据。你可以查看、纠正或删除；删除记忆不会删除原始讨论。</p>
      ${selectField("记忆文件", "personal.memory", this.path, this.files.map(f=>({value:f.path,label:f.path === "MEMORY.md" ? "长期记忆索引" : f.path.slice(6,-3)})), null)}
      <div id="personalMemoryEditor"></div></section>`;
    this.renderMemory(); this.syncButtons(); this.selects.refresh();
    const restored = focusKey ? this.host.querySelector(`[data-personal-field="${focusKey}"]`) : memoryFocus ? this.host.querySelector("[data-personal-memory]") : null;
    if (restored && !restored.matches(":disabled")) {
      restored.focus({preventScroll:true});
      if (selection) restored.setSelectionRange(...selection);
    }
  }
  syncButtons() {
    if (!this.host) return;
    for (const action of ["save", "reset"]) {
      const button = this.host.querySelector(`[data-personal-action="${action}"]`);
      if (button) button.disabled = !this.dirty || !!this.saving;
    }
    const editor = this.editors.get(this.path);
    for (const action of ["memorySave", "memoryReset"]) {
      const button = this.host.querySelector(`[data-personal-action="${action}"]`);
      if (button) button.disabled = !editor || editor.draft === editor.content || !!this.memorySaving;
    }
  }
  change(event) {
    const input = event.target;
    if (input.dataset.personalMemory !== undefined) {
      this.editors.get(this.path).draft = input.value; this.syncButtons(); return;
    }
    if (input.name === "personal.memory") {
      if (event.type === "change") { this.path = input.value; void this.loadMemory(this.path); }
      return;
    }
    const key = input.dataset.personalField || input.name?.replace(/^personal\./, "");
    if (!key || !this.draft) return;
    const parts = key.split("."), field = parts.pop();
    const target = parts.reduce((v,k)=>v[k], this.draft);
    if (target[field] === input.value) return;
    target[field] = input.value;
    if (key === "pi.provider") { this.draft.pi.model = ""; delete this.draft.pi.reasoning_effort; }
    if (key.endsWith(".model")) delete this.draft[this.draft.runtime].reasoning_effort;
    this.notice = "有未保存的更改"; this.syncButtons();
    $("#personalSettingsStatus").textContent = this.notice;
    if (["runtime", "pi.provider", "pi.model", "codex.model"].includes(key)) {
      this.render(); this.host.querySelector(`[data-select-trigger="${input.name}"]`)?.focus({preventScroll:true});
      if (key === "runtime") { this.catalog = null; void this.loadCatalog(); }
    }
    if (["codex.profile", "codex.provider"].includes(key)) { ++this.sequence; this.catalog = null; this.catalogLoading = false; this.catalogNotice = "配置已变化，请刷新模型"; }
  }
  async save() {
    if (this.saving || !this.dirty) return;
    const name = this.host.querySelector('[data-personal-field="name"]');
    name.setCustomValidity(name.value.trim() ? "" : "请为助手取一个名字");
    if (!name.reportValidity()) return;
    this.saving = true; this.render();
    try {
      const {profile} = await api(base+"/profile", {method:"PUT", body:this.draft});
      this.saved = profile; this.draft = structuredClone(profile); this.notice = "个人助手已保存";
      document.dispatchEvent(new CustomEvent("personal-assistant:profile", {detail:profile}));
    } catch (error) { this.notice = errorText(error); }
    finally { this.saving = false; this.render(); }
  }
  async loadMemory(path) {
    const existing = this.editors.get(path);
    if (existing && existing.draft !== existing.content) { this.renderMemory(); return; }
    this.memoryNotice = "正在读取记忆…"; this.renderMemory();
    try {
      const {file} = await api(base+"/memory/"+encodeURI(path));
      if (this.editors.get(path)?.draft !== this.editors.get(path)?.content) return;
      this.editors.set(path, {...file, draft:file.content}); this.memoryNotice = "";
    } catch (error) { this.memoryNotice = errorText(error); }
    this.renderMemory();
  }
  renderMemory() {
    const host = $("#personalMemoryEditor", this.host);
    if (!host) return;
    const editor = this.editors.get(this.path);
    host.innerHTML = `${editor ? `<fieldset class="form-fieldset section-stack" ${this.memorySaving ? "disabled" : ""}>
      <label class="field-item"><span class="field-label">${e(this.path)}</span><textarea class="field-input" data-personal-memory rows="10" aria-label="记忆内容">${e(editor.draft)}</textarea></label>
      <div class="button-row"><button type="button" class="btn-primary" data-personal-action="memorySave">保存记忆</button><button type="button" class="btn-secondary" data-personal-action="memoryReset">撤销更改</button><button type="button" class="btn-secondary" data-personal-action="memoryDelete">${this.path === "MEMORY.md" ? "清空索引" : "删除记忆"}</button></div></fieldset>` : ""}
      <p class="status-text muted" role="status">${e(this.memoryNotice || "")}</p>`;
    this.syncButtons();
  }
  resetMemory() { const editor = this.editors.get(this.path); if (editor) editor.draft = editor.content; this.renderMemory(); }
  async saveMemory(deleting = false) {
    const path = this.path, editor = this.editors.get(path);
    if (!editor || this.memorySaving) return;
    this.memorySaving = true; this.renderMemory();
    try {
      const {file} = await api(base+"/memory/"+encodeURI(path), {method:deleting ? "DELETE" : "PUT", body:{content:editor.draft, revision:editor.revision}});
      this.editors.set(path, {...file, draft:file.content});
      // Memory edits invalidate native context; carry the new revision into any
      // unsaved identity draft without discarding the user's other changes.
      const [{profile}, {files}] = await Promise.all([api(base+"/profile"), api(base+"/memory")]);
      this.saved.epoch = profile.epoch; this.draft.epoch = profile.epoch; this.files = files;
      if (deleting) { this.editors.delete(path); this.path = "MEMORY.md"; await this.loadMemory(this.path); }
      this.memoryNotice = deleting ? "记忆已删除，原始讨论保留" : "记忆已保存";
    } catch (error) { this.memoryNotice = errorText(error); }
    finally { this.memorySaving = false; this.render(); }
  }
  async deleteMemory() {
    if (await confirmAction({title:this.path === "MEMORY.md" ? "清空长期记忆索引" : "删除这篇记忆", message:this.path === "MEMORY.md" ? "清空索引后，专题笔记和原始讨论仍会保留。" : "删除这篇笔记和索引中的链接，原始讨论仍会保留。", action:"确认删除"})) await this.saveMemory(true);
  }
}
