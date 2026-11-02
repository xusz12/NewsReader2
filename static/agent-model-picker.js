import { api, escapeHTML as e, errorText } from "./ui.js";
import { selectField } from "./form-controls.js";
import { AnchoredPopover } from "./anchored-popover.js";

// Session-local choices. A change preserves the transcript and applies to the next turn.
export class AgentModelPicker {
  constructor({host, selects, context, onUpdate, onBusy}) {
    Object.assign(this, {host, selects, context, onUpdate, onBusy});
    this.catalogs = {}; this.revision = 0;
    host.innerHTML = '<button class="agent-btn" id="agentModelTrigger" type="button" aria-label="选择当前会话的模型与推理程度"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg></button>';
    this.trigger = host.querySelector("button");
    this.panel = document.createElement("div");
    this.panel.id = "agentModelPopover";
    this.panel.className = "anchored-popover";
    this.panel.hidden = true; this.panel.tabIndex = -1;
    this.panel.setAttribute("role", "dialog");
    this.panel.setAttribute("aria-labelledby", "agentModelTitle");
    this.panel.innerHTML = '<div class="popover-heading"><h2 class="reader-section-heading" id="agentModelTitle">当前会话模型</h2><button class="agent-btn" type="button" data-agent-model-close aria-label="关闭模型选择" title="关闭模型选择">×</button></div><div class="popover-form"></div>';
    document.body.append(this.panel);
    this.fields = this.panel.querySelector(".popover-form");
    this.popover = new AnchoredPopover({panel:this.panel, trigger:this.trigger,
      owns:target => this.panel.contains(selects.button) && selects.menu.contains(target),
      onClose:() => {if (this.panel.contains(selects.button)) selects.close(false);},
    });
    this.trigger.addEventListener("click", () => {
      if (this.popover.open) this.popover.close(true);
      else {
        this.popover.show();
        (this.fields.querySelector('[data-select-trigger="agentModel"]:not(:disabled)') || this.panel).focus({preventScroll:true});
      }
    });
    this.panel.addEventListener("change", event => void this.change(event.target));
    this.panel.addEventListener("click", event => {
      if (event.target.closest("[data-agent-model-retry]")) void this.load(true);
      if (event.target.closest("[data-agent-model-close]")) this.popover.close(true);
    });
  }
  close() { this.popover.close(); }
  contains(target) {
    return this.popover.open && (this.panel.contains(target) ||
      this.panel.contains(this.selects.button) && this.selects.menu.contains(target));
  }
  sync() {
    const c = this.context(), session = c.session;
    if (!c.open || c.item?.id !== this.itemId) this.close();
    this.itemId = c.item?.id;
    const key = JSON.stringify([c.item?.id, session?.id, session?.provider, session?.model, session?.reasoning_effort]);
    if (key !== this.key) {
      this.key = key; this.revision++; this.catalogs = {}; this.choices = null; this.error = ""; this.blocked = false;
    }
    this.render();
    if (c.open && c.item && !this.loading && !this.choices && !this.error) void this.load();
  }
  async load(retry = false) {
    const c = this.context();
    if (!c.item || this.loading) return;
    const revision = this.revision, runtime = this.choices?.runtime;
    if (!retry && runtime && this.catalogs[runtime]) return;
    const params = new URLSearchParams();
    if (runtime) params.set("runtime", runtime);
    if (c.session) params.set("session_id", c.session.id);
    this.loading = true; this.error = ""; this.render();
    try {
      const data = await api(`/api/news/${encodeURIComponent(c.item.id)}/agent/model-options?${params}`);
      if (revision !== this.revision) return;
      this.choices ||= data.choices;
      this.catalogs[data.runtime] = data.catalog;
      this.error = data.catalog.error || "";
    } catch (error) { if (revision === this.revision) this.error = errorText(error); }
    finally {
      this.loading = false;
      if (revision === this.revision) this.render();
      else this.sync();
    }
  }
  render() {
    const c = this.context(), choices = this.choices, focused = this.fields.contains(document.activeElement) ? document.activeElement.id : "";
    const config = choices?.[choices.runtime] || c.session || {};
    const summary = `${config.model || "默认模型"} · ${config.reasoning_effort || "默认推理"}`;
    this.trigger.title = this.error ? `当前会话模型 · 重试：${this.error}` : `当前会话模型：${summary}`;
    this.trigger.setAttribute("aria-description", this.trigger.title);
    this.trigger.setAttribute("aria-busy", String(!!(this.loading || this.saving)));
    this.trigger.disabled = !c.item || !!(c.busy || this.saving);
    const next = document.createElement("div");
    if (choices) {
      const runtime = choices.runtime, config = choices[runtime], codex = runtime === "codex";
      const catalog = this.catalogs[runtime], models = catalog?.options || [];
      const options = models.map(m=>codex ? m : {...m,value:JSON.stringify([m.provider,m.value]),label:`${m.label || m.value} · ${m.provider}`});
      const value = codex ? config.model : config.model ? JSON.stringify([config.provider,config.model]) : "";
      if (value && !options.some(m=>m.value===value)) options.push({value,label:config.model});
      const model = models.find(m=>m.value === config.model && (codex || m.provider === config.provider));
      const label = text => `<span class="sr-only">Agent </span>${text}`;
      next.innerHTML = `<div class="form-group-grid">${selectField(label("Runtime"),"agentRuntime",runtime,[{value:"codex",label:"Codex"},{value:"pi",label:"Pi"}],null)}${selectField(label("模型"),"agentModel",value,options,codex?"默认模型":"选择模型")}${selectField(label("推理程度"),"agentEffort",config.reasoning_effort || "",model?.reasoning_efforts || [],"默认")}</div>`;
      for (const select of next.querySelectorAll("select")) {
        select.disabled = !!(c.busy || this.saving || this.loading || !catalog ||
          select.name === "agentEffort" && !model?.reasoning_efforts?.length);
        const previous = this.fields.querySelector(`select[name="${select.name}"]`);
        if (!previous) continue;
        if (previous.innerHTML !== select.innerHTML || previous.disabled !== select.disabled) {
          if (this.selects.select === previous) this.selects.close(false);
          previous.innerHTML = select.innerHTML;
        }
        previous.value = select.value; previous.disabled = select.disabled;
        select.closest(".field-item").replaceWith(previous.closest(".field-item"));
      }
    }
    next.insertAdjacentHTML("beforeend", `<div class="agent-model-status muted" role="status" ${this.loading || this.error || this.saving ? "" : "hidden"}>${e(this.loading ? "正在读取模型…" : this.saving ? "正在切换…" : this.error)}${this.error ? '<button class="reader-action-btn reader-action-btn--meta" type="button" data-agent-model-retry>重试</button>' : ""}</div>`);
    this.fields.replaceChildren(...next.childNodes); this.selects.refresh(this.fields);
    if (focused && this.popover.open) document.getElementById(focused)?.focus({preventScroll:true});
  }
  async change(input) {
    if (!this.choices || this.context().busy || this.saving || this.loading) return;
    const revision = this.revision, c = this.context();
    const choices = this.choices;
    if (input.name === "agentRuntime") {
      // Do not send through the old executor while the new runtime is still being selected.
      this.blocked = true; choices.runtime = input.value; this.onBusy(); this.render(); await this.load();
      if (revision !== this.revision) return;
      if (this.error) {this.choices = null; this.blocked = false; this.onBusy(); return;}
    } else if (input.name === "agentModel") {
      const config = choices[choices.runtime];
      const [provider,model] = choices.runtime === "pi" && input.value ? JSON.parse(input.value) : ["",input.value];
      config.model = model; config.reasoning_effort = "";
      if (choices.runtime === "pi") config.provider = provider;
    } else if (input.name === "agentEffort") choices[choices.runtime].reasoning_effort = input.value;
    else return;
    const selected = choices[choices.runtime];
    if (choices.runtime === "pi" && (!selected.model || !selected.provider)) {this.render(); return;}
    this.saving = true; this.error = ""; this.onBusy(); this.render();
    try {
      const data = await api(`/api/news/${encodeURIComponent(c.item.id)}/agent/session/model`, {method:"PATCH", body:{session_id:c.session?.id || "", selection:{
        runtime:choices.runtime, model:selected.model || "", reasoning_effort:selected.reasoning_effort || "",
        ...(choices.runtime === "pi" ? {provider:selected.provider} : {}),
      }}});
      if (revision !== this.revision) return;
      this.onUpdate(data);
    } catch (error) {
      if (revision === this.revision) {this.choices = null; this.error = errorText(error);}
    } finally {this.saving = false; this.blocked = false; this.onBusy(); this.render();}
  }
}
