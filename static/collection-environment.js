import { $, api, escapeHTML as e } from "./ui.js";

const labels = {runtime:"opencli", adapters:"采集与正文命令", chrome:"Chrome / Chromium", bridge:"OpenCLI 浏览器扩展", x:"X 账号"};
const statuses = {success:"通过", failed:"未通过", unknown:"未确认", unchecked:"未检查"};
export class CollectionEnvironment {
  constructor() {
    this.results = {};
    this.checking = false;
    this.notice = "";
    this.revision = 0;
  }
  html() {
    return `<section class="settings-section-card" data-section="environment" id="collectionEnvironment" aria-labelledby="collectionEnvironmentTitle">
      <div class="section-card-title"><span id="collectionEnvironmentTitle">采集环境</span></div>
      <div class="button-row"><button class="btn-secondary" id="checkCollectionEnvironmentBtn" type="button">检查采集环境</button><button class="btn-secondary" id="checkCollectionXBtn" type="button">检查 X 登录</button></div>
      <p class="muted" id="collectionEnvironmentNotice" role="status" hidden></p>
      <div class="section-stack" id="collectionEnvironmentResults"></div>
      <div class="button-row"><button class="reader-action-btn reader-action-btn--meta" type="button" data-settings-help="help-collection-environment">安装与登录帮助</button></div>
      </section>`;
  }
  render() {
    const root = $("#collectionEnvironmentResults");
    if (!root) return;
    root.innerHTML = Object.entries(labels).map(([id, label]) => {
      const row = this.results[id] || {status:"unchecked", message:""};
      const at = row.checked_at ? new Date(row.checked_at).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai", hour12:false}) : "";
      const status = row.stale ? `待复查（上次${statuses[row.status] || statuses.unknown}）` : statuses[row.status] || statuses.unknown;
      const state = row.stale ? "unchecked" : row.status;
      return `<div class="field-item" data-environment-check="${id}"><span class="field-label"><span class="status-dot" data-status="${state}" aria-hidden="true"></span>${label} · <span class="status-text" data-status="${state}">${e(status)}</span></span>${row.message ? `<span>${e(row.message)}</span>` : ""}${at ? `<span class="muted">检查于 ${e(at)}（北京时间）${row.stale ? " · 浏览器已重新检查，请复查 X 登录" : ""}</span>` : ""}</div>`;
    }).join("");
    for (const id of ["checkCollectionEnvironmentBtn", "checkCollectionXBtn"]) {
      const button = $("#" + id);
      button.disabled = this.checking;
      button.setAttribute("aria-busy", String(this.checking));
    }
    const warning = $("#collectionEnvironmentWarning");
    const failed = Object.entries(this.results).filter(([, row]) => row.status === "failed" && !row.stale);
    warning.hidden = !failed.length;
    warning.textContent = failed.length ? `${failed.map(([id]) => labels[id]).join("、")} 检查未通过 · 查看` : "";
    const notice = $("#collectionEnvironmentNotice");
    notice.textContent = this.notice;
    notice.hidden = !this.notice;
  }
  async load() {
    if (this.loaded || this.loading || this.checking) return;
    const revision = this.revision;
    this.loading = true;
    try {
      const data = await api("/api/collection/environment");
      if (revision !== this.revision) return;
      if (!Array.isArray(data.checks) || data.checks.some(row => !labels[row.id] || !statuses[row.status] || typeof row.message !== "string" || !row.checked_at)) throw new Error("invalid_response");
      this.results = Object.fromEntries(data.checks.map(row => [row.id, row]));
      this.loaded = true;
    } catch {
      if (revision === this.revision) this.notice = "最近检查记录暂时无法读取；已有结果保留，可重新检查。";
    } finally {
      this.loading = false;
      this.render();
    }
  }
  async check(target) {
    if (this.checking) return;
    ++this.revision;
    this.checking = true;
    this.notice = target === "x" ? "正在检查 X 登录，最长约 60 秒…" : "正在检查内置命令和浏览器连接，最长约 35 秒…";
    this.render();
    try {
      const data = await api("/api/collection/environment/check", {method:"POST", body:{target}});
      if (!Array.isArray(data.checks) || !data.checked_at || data.checks.some(row => !labels[row.id] || !statuses[row.status] || typeof row.message !== "string")) throw new Error("invalid_response");
      // Retain the last account check, but do not present it as current after a
      // new browser/profile check. The server persists the same distinction.
      if (target === "environment" && this.results.x) this.results.x = {...this.results.x, stale:true};
      for (const row of data.checks) this.results[row.id] = {...row, checked_at:data.checked_at};
      if (Array.isArray(data.results) && data.results.every(row => labels[row.id] && statuses[row.status] && typeof row.message === "string" && row.checked_at))
        this.results = Object.fromEntries(data.results.map(row => [row.id, row]));
      this.loaded = true;
      this.notice = "检查完成。结果仅反映检查时的状态，不代表全部信源已通过实际抓取验证。";
    } catch {
      this.notice = "检查请求失败或已有检查正在进行。请稍后重试；已有结果保留，请注意检查时间。";
    } finally {
      this.checking = false;
      this.render();
    }
  }
}
