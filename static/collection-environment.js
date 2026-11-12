import { $, api, escapeHTML as e } from "./ui.js";

const labels = {runtime:"opencli", adapters:"采集与正文命令", chrome:"Chrome / Chromium", bridge:"OpenCLI 浏览器扩展", x:"X 账号"};
const statuses = {success:"通过", failed:"未通过", unknown:"未确认", unchecked:"未检查"};
const initial = {
  runtime:"使用随 NewsReader 安装的固定版本；无需单独安装全局 opencli。",
  adapters:"检查项目内 adapter 和全部必需命令，不使用个人 adapter。",
  chrome:"浏览器采集期间，运行 NewsReader 的电脑必须保持 Chrome 打开。",
  bridge:"需要在同一 Chrome 配置中安装并启用 OpenCLI Browser Bridge 扩展。",
  x:"需要在同一 Chrome 配置中登录 X；点击「检查 X 登录」验证。",
};

export class CollectionEnvironment {
  constructor() {
    this.results = {};
    this.checking = false;
    this.notice = "";
    this.revision = 0;
  }
  html() {
    return `<section class="settings-section-card" data-section="pipeline" id="collectionEnvironment" aria-labelledby="collectionEnvironmentTitle">
      <div class="section-card-title"><span id="collectionEnvironmentTitle">采集环境</span></div>
      <p class="muted">检查运行 NewsReader 的电脑，手机访问时仍检查服务所在电脑。公开 RSS 不依赖浏览器；Reuters、X 和部分正文抓取需要浏览器连接。</p>
      <div class="button-row"><button class="btn-secondary" id="checkCollectionEnvironmentBtn" type="button">检查采集环境</button><button class="btn-secondary" id="checkCollectionXBtn" type="button">检查 X 登录</button></div>
      <p class="muted">X 登录检查会访问 X，不采集入库，也不保存账号信息。</p>
      <p class="muted" id="collectionEnvironmentNotice" role="status" hidden></p>
      <div class="section-stack" id="collectionEnvironmentResults"></div>
      <p class="muted"><a href="https://github.com/jackwener/OpenCLI#quick-start" target="_blank" rel="noopener noreferrer">浏览器扩展安装说明</a> · <a href="https://x.com/login" target="_blank" rel="noopener noreferrer">打开 X 登录</a></p>
      <div class="field-item"><span class="field-label">Bypass Paywalls Clean（可选）</span><p class="muted">可按需在 Chrome 手动安装，以辅助部分新闻网站的正文读取。此处无法自动确认它是否安装或生效，安装后仍需验证目标文章能否完整读取。</p><a href="https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean" target="_blank" rel="noopener noreferrer">查看项目安装说明</a></div>
      </section>`;
  }
  render() {
    const root = $("#collectionEnvironmentResults");
    if (!root) return;
    root.innerHTML = Object.entries(labels).map(([id, label]) => {
      const row = this.results[id] || {status:"unchecked", message:initial[id]};
      const at = row.checked_at ? new Date(row.checked_at).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai", hour12:false}) : "";
      const status = row.stale ? `待复查（上次${statuses[row.status] || statuses.unknown}）` : statuses[row.status] || statuses.unknown;
      const state = row.stale ? "unchecked" : row.status;
      return `<div class="field-item" data-environment-check="${id}"><span class="field-label"><span class="status-dot" data-status="${state}" aria-hidden="true"></span>${label} · <span class="status-text" data-status="${state}">${e(status)}</span></span><span>${e(row.message)}</span>${at ? `<span class="muted">检查于 ${e(at)}（北京时间）${row.stale ? " · 浏览器已重新检查，请复查 X 登录" : ""}</span>` : ""}</div>`;
    }).join("");
    for (const id of ["checkCollectionEnvironmentBtn", "checkCollectionXBtn"]) {
      const button = $("#" + id);
      button.disabled = this.checking;
      button.setAttribute("aria-busy", String(this.checking));
    }
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
