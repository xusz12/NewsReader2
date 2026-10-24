import { $, api, escapeHTML as e, errorText, toast, markdown, safeURL, singlePane } from "./ui.js";

const running = job => !!job && ["pending", "generating", "checking"].includes(job.status);
const runtimeLabel = data => `${data?.runtime === "pi" ? "Pi Agent" : "Codex Agent"} · ${data?.model || "沿用本机默认"}`;
const range = data => `${data.start_date || data.window_start} → ${data.end_date || data.window_end}`;
const count = value => Number(value || 0).toLocaleString("zh-CN");
const bytes = value => Number(value) < 1024 * 1024
  ? `${(Number(value || 0) / 1024).toFixed(1)} KiB`
  : `${(Number(value) / 1024 / 1024).toFixed(2)} MiB`;
function sourceURL(value) {
  try {
    const url = new URL(value);
    return !url.username && !url.password ? safeURL(url.href) : "";
  } catch { return ""; }
}
function requestUUID() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  const data = crypto.getRandomValues(new Uint8Array(16));
  data[6] = (data[6] & 15) | 64; data[8] = (data[8] & 63) | 128;
  const hex = Array.from(data, b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export function visibilityText(scope) {
  return `新闻流可见未读 ${count(scope.feed_visible_unread_count ?? scope.locked_count)} 条；总结候选未读 ${count(scope.locked_count)} 条，其中含已关闭信源 ${count(scope.hidden_source_unread_count)} 条。关闭信源仍参与总结候选及成功后标读；Bloomberg 视频不在本次范围。`;
}
export function scopeText(scope) {
  const excluded = scope.excluded_by || {};
  return `${visibilityText(scope)}\n纳入总结 ${count(scope.summarized_count)} 条；另有 ${count(scope.excluded_count)} 条不进入总结，但成功后也将标为已读。成功后标读共 ${count(scope.locked_count)} 条。\n排除原因：日期 ${count(excluded.date)}、条数 ${count(excluded.count)}、输入大小 ${count(excluded.size)}。\n日期缺失或无效 ${count(scope.undated_count)} 条，其中纳入 ${count(scope.undated_summarized)} 条。`;
}

export class SummaryPanel {
  constructor({ isVisible, enterReader, openSettings, onCompleted }) {
    Object.assign(this, { isVisible, enterReader, openSettings, onCompleted });
    this.entries = []; this.total = 0; this.query = "";
    this.token = ""; this.selected = ""; this.revision = 0; this.listSequence = 0; this.detailSequence = 0; this.scopeSequence = 0;
    this.watched = new Set(); this.completed = new Set();
    this.modal = $("#summaryModal");
    $("#catchupStartBtn").onclick = () => this.prepare();
    $("#catchupSettingsBtn").onclick = () => this.openSettings();
    $("#cancelSummaryBtn").onclick = () => this.cancel();
    $("#confirmSummaryBtn").onclick = () => this.confirm();
    $("#discardSummaryBtn").onclick = $("#closeSummaryModalBtn").onclick = () => this.dismiss();
    this.modal.addEventListener("cancel", event => { event.preventDefault(); this.dismiss(); });
    this.modal.addEventListener("click", event => { if (event.target === this.modal) this.dismiss(); });
    $("#summariesSearchInput").oninput = event => {
      this.query = event.target.value.trim();
      clearTimeout(this.searchTimer);
      this.listSequence++; // Invalidate any result for the previous query immediately.
      this.searchTimer = setTimeout(() => this.load(), 250);
    };
    $("#summaryFilters").onclick = event => {
      const button = event.target.closest("[data-summary-filter]");
      if (!button) return;
      this.load();
    };
    $("#summaryCardList").onclick = event => {
      const card = event.target.closest("[data-summary-id]");
      if (card) this.select(card.dataset.summaryId, true);
      if (event.target.closest("#moreSummariesBtn")) this.load({ append: true });
      if (event.target.closest("#retrySummariesBtn")) this.load();
    };
    $("#summaryContent").onclick = event => {
      const citation = event.target.closest("[data-citation]");
      const ref = citation?.dataset.citation;
      if (ref) {
        const node = $("#summaryContent").querySelector(`[data-summary-ref="${CSS.escape(ref)}"]`);
        if (node) {
          const sources = node.closest("details");
          this.citationReturn = { citation, scrollTop: $("#summaryScrollArea").scrollTop, sourcesOpen: sources.open };
          $("#summaryReturnBtn").hidden = false;
          sources.open = true;
          node.scrollIntoView({ block: "center" });
          node.focus({ preventScroll: true });
        }
      }
      if (event.target.closest("#retrySummaryDetailBtn")) this.select(this.selected);
    };
    $("#summaryReturnBtn").onclick = () => this.returnToCitation();
    $("#exportSummaryBtn").onclick = () => this.export();
    this.controls();
  }
  clearCitationReturn() {
    this.citationReturn = null;
    $("#summaryReturnBtn").hidden = true;
  }
  returnToCitation() {
    const origin = this.citationReturn;
    if (!origin) return;
    if (origin.citation.isConnected) {
      $("#summaryContent .summary-sources").open = origin.sourcesOpen;
      $("#summaryScrollArea").scrollTop = origin.scrollTop;
      origin.citation.focus({ preventScroll: true });
    }
    this.clearCitationReturn();
  }
  remember(value) {
    this.requestId = value;
    try {
      const key = `newsreader-summary-request-${this.token}`;
      if (value) sessionStorage.setItem(key, value); else sessionStorage.removeItem(key);
    } catch { /* The in-memory ID still makes retries idempotent. */ }
  }
  requestIdForToken() {
    if (this.requestId) return this.requestId;
    try { this.requestId = sessionStorage.getItem(`newsreader-summary-request-${this.token}`); } catch {}
    if (!this.requestId) this.remember(requestUUID());
    return this.requestId;
  }
  bindToken(token) {
    if (this.token && this.token !== token) {
      this.revision++; this.detailSequence++; this.scopeSequence++;
      clearTimeout(this.timer); this.modal.close(); clearInterval(this.expiryTimer);
      this.entries = []; this.selected = ""; this.detail = null; this.job = null; this.scope = null; this.preview = null; this.requestId = null;
      this.watched.clear(); this.completed.clear();
      this.clearCitationReturn();
      $("#summaryContent").innerHTML = '<div class="summary-empty"><h1>已切换新闻库</h1><p>请选择当前新闻库中的总结。</p></div>';
      $("#summaryToolbarTitle").textContent = "新闻总结";
    }
    this.token = token;
  }
  async refreshScope() {
    const sequence = ++this.scopeSequence;
    try {
      const data = await api("/api/news-summaries/scope");
      if (sequence !== this.scopeSequence) return;
      this.bindToken(data.database_token); this.scope = data;
      $("#catchupMetrics").innerHTML = `<span><strong>${count(data.locked_count)}</strong> 篇候选</span><span><strong>${count(data.limits.max_days)}</strong> 自然日</span><span>上限 <strong>${count(data.limits.max_items)}</strong> 条</span>`;
      $("#catchupRuntime").textContent = runtimeLabel(data);
      this.controls();
    } catch (error) {
      if (sequence !== this.scopeSequence) return;
      if (!this.scope) $("#catchupMetrics").textContent = "范围读取失败，请刷新重试";
    }
  }
  controls() {
    const active = running(this.job);
    $("#catchupStartBtn").disabled = !!this.busy || active || !this.token || !this.scope || this.scope.locked_count === 0;
    $("#catchupStartBtn").textContent = this.busy === "prepare" ? "正在计算范围…" : active ? "正在总结…" : "总结并全部已读";
    $("#cancelSummaryBtn").hidden = !active;
    $("#cancelSummaryBtn").disabled = !!this.busy || !!this.job?.cancel_requested;
    $("#cancelSummaryBtn").textContent = this.job?.cancel_requested ? "正在停止…" : "停止本次总结";
    $("#catchupStatusLabel").textContent = active ? "本批未读已锁定" : this.scope?.locked_count === 0 ? "积压已清空" : "未读积压 · 待总结";
    $("#exportSummaryBtn").disabled = !this.detail || this.detail.id !== this.selected;
  }
  showError(error, confirmed = false, target = $("#summaryProgress")) {
    if (globalThis.NewsErrors) NewsErrors.render(target, error, { context: "summary", confirmedUncommitted: confirmed });
    else target.textContent = errorText(error);
  }
  async load({ append = false, select = "" } = {}) {
    if (append && this.loading) return;
    const sequence = ++this.listSequence, revision = this.revision;
    this.loading = true; this.controls();
    if (!this.entries.length) $("#summaryCardList").innerHTML = '<li class="list-message" role="status">正在读取历史总结…</li>';
    const params = new URLSearchParams({ offset: append ? this.entries.length : 0, q: this.query });
    try {
      const data = await api(`/api/news-summaries?${params}`);
      if (sequence !== this.listSequence || revision !== this.revision) return;
      this.bindToken(data.database_token);
      const existing = append ? this.entries : [];
      this.entries = [...existing, ...data.items.filter(item => !existing.some(old => old.id === item.id))];
      this.total = data.total; this.archiveTotal = data.archive_total ?? data.total;
      this.renderList();
      await this.receiveJob(data.active || data.latest_job);
      if (sequence !== this.listSequence) return;
      if (select || (!this.selected && this.isVisible() && !singlePane.matches && this.entries.length))
        this.select(select || this.entries[0].id);
    } catch (error) {
      if (sequence !== this.listSequence || revision !== this.revision) return;
      this.showError(error);
      if (!this.entries.length) $("#summaryCardList").innerHTML = '<li class="list-message">历史总结读取失败<button class="btn-secondary" id="retrySummariesBtn" type="button">重试</button></li>';
    } finally {
      if (sequence === this.listSequence) { this.loading = false; this.controls(); }
    }
    this.refreshScope();
  }
  renderList() {
    $('[data-summary-filter="all"]').textContent = `全部 (${count(this.archiveTotal)})`;
    $("#summaryCardList").innerHTML = this.entries.map(item => `<li class="news-card ${item.id === this.selected ? "active" : ""}" data-summary-id="${e(item.id)}" tabindex="0" role="button" aria-current="${item.id === this.selected}" aria-label="${e(item.title)}">
      <div class="news-card-meta"><div class="meta-source-box"><span class="summary-mark">总</span><span>新闻总结</span></div></div>
      <h3 class="news-card-title">${e(item.title)}</h3><p class="news-card-snippet">${e((item.excerpt || "").replace(/\[\[N\d+\]\]/g, "").replace(/[#*`>]/g, "").trim())}</p>
      <div class="summary-card-footer"><span>涵盖 ${count(item.item_count)} 篇</span><time>${e(item.created_at?.replace("T", " ").slice(0, 16))}</time></div></li>`).join("") || `<li class="list-message">${this.query ? "没有符合条件的总结，试试其他关键词或日期范围。" : "还没有历史总结。<br>从上方开始整理未读新闻。"}</li>`;
    if (this.entries.length < this.total) $("#summaryCardList").insertAdjacentHTML("beforeend", '<li class="load-more-row"><button class="btn-secondary" id="moreSummariesBtn" type="button">加载更多总结</button></li>');
  }
  async select(id, mobile = false) {
    const sequence = ++this.detailSequence, token = this.token;
    this.clearCitationReturn();
    this.selected = id; this.detail = null; this.renderList(); this.controls();
    if (mobile) this.enterReader();
    $("#summaryContent").innerHTML = '<div class="list-message" role="status">正在读取报告…</div>';
    $("#summaryToolbarTitle").textContent = "正在读取报告…";
    try {
      const data = await api(`/api/news-summaries/${encodeURIComponent(id)}`);
      if (sequence !== this.detailSequence || token !== this.token) return;
      if (data.summary.database_token !== token) { await this.load(); return; }
      this.detail = data.summary; this.renderDetail(); this.controls();
      $("#summaryScrollArea").scrollTop = 0;
    } catch (error) {
      if (sequence !== this.detailSequence || token !== this.token) return;
      $("#summaryToolbarTitle").textContent = "报告读取失败";
      $("#summaryContent").innerHTML = `<div class="list-message error-state">${e(errorText(error))}<button class="btn-secondary" id="retrySummaryDetailBtn" type="button">重新读取报告</button></div>`;
    }
  }
  renderDetail() {
    const s = this.detail, scope = s.validation?.scope, sources = s.sources || [];
    const config = { runtime: s.config.runtime, model: s.config.runtime === "codex" ? s.config.codex?.model : s.config.pi_model };
    const dates = [s.start_date, s.end_date].map(d => /^\d{4}-\d{2}-\d{2}$/.test(d) ? Date.parse(`${d}T00:00:00Z`) : NaN);
    const days = dates.every(Number.isFinite) ? Math.round((dates[1] - dates[0]) / 86400000) + 1 : "—";
    $("#summaryToolbarTitle").textContent = s.title;
    $("#summaryContent").innerHTML = `<header class="summary-report-header"><div class="summary-report-kicker"><span>NEWSREADER · CATCH UP</span></div><h1>${e(s.title)}</h1><p>${e(runtimeLabel(config))} · ${e(s.created_at.replace("T", " "))}</p><div class="summary-report-stats"><div><strong>${count(s.item_count)}</strong><span>纳入总结</span></div><div><strong>${count(scope?.locked_count ?? s.item_count)}</strong><span>本批标读</span></div><div><strong>${days}</strong><span>覆盖自然日</span></div>${scope?.input_budget ? `<div><strong>${bytes(scope.input_budget.generation_bytes)}</strong><span>生成输入大小</span></div>` : ""}</div></header>
      <div class="summary-prose" id="summaryProse">${markdown(s.body)}</div>
      <details class="summary-audit"><summary>本次范围与材料说明</summary>${scope ? `<p>${e(scopeText(scope)).replaceAll("\n", "<br>")}</p>` : ""}<p>全文 ${sources.filter(i => i.material === "全文").length} 条 · 摘要 ${sources.filter(i => i.material === "摘要").length} 条 · 仅标题 ${sources.filter(i => i.material === "仅标题").length} 条。日期为报道日期。</p><p>${e(s.validation?.limitation || "")}</p></details>
      <details class="summary-sources"><summary>引用来源 · ${sources.length} 条</summary>${sources.map(i => `<section data-summary-ref="${e(i.ref)}" tabindex="-1"><span class="settings-eyebrow">${e(i.ref)} · ${e(i.date || "报道日期不明")} · ${e(i.source_name || i.source)}</span><h3>${e(i.title)}</h3>${i.summary ? `<p>${e(i.summary)}</p>` : ""}${sourceURL(i.url) ? `<a href="${e(sourceURL(i.url))}" target="_blank" rel="noopener noreferrer">查看原新闻 ↗</a>` : ""}</section>`).join("")}</details>`;
    // Replace references only in text nodes; model HTML is escaped by markdown().
    const refs = new Map(sources.map(i => [i.ref, i]));
    const walker = document.createTreeWalker($("#summaryProse"), NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (node.parentElement.closest("a,code")) continue;
      const matches = [...node.textContent.matchAll(/\[\[(N\d{4,})\]\]/g)];
      if (!matches.length) continue;
      const fragment = document.createDocumentFragment(); let offset = 0;
      for (const match of matches) {
        fragment.append(node.textContent.slice(offset, match.index));
        const source = refs.get(match[1]);
        if (source) {
          const button = document.createElement("button"); button.type = "button"; button.className = "summary-citation"; button.dataset.citation = match[1];
          button.textContent = `[${match[1]}]`; button.title = `${source.date || "报道日期不明"} · ${source.title}`; fragment.append(button);
        } else fragment.append(match[0]);
        offset = match.index + match[0].length;
      }
      fragment.append(node.textContent.slice(offset)); node.replaceWith(fragment);
    }
  }
  async receiveJob(job, attach = false) {
    if (job && job.database_token !== this.token) return;
    if (job && this.job?.id === job.id && !running(this.job) && running(job)) return;
    this.job = job;
    if (job && (attach || running(job))) this.watched.add(job.id);
    this.controls();
    const labels = { pending: "已锁定未读，等待生成", generating: "正在生成总结", checking: "正在检查正文与引用", failed: "总结失败，本批未读保持不变", cancelled: "已停止，本批未读保持不变" };
    $("#summaryProgress").textContent = job && job.status !== "completed" ? `${labels[job.status] || job.status}${job.cancel_requested && running(job) ? " · 正在停止" : ""} · ${count(job.item_count)} 篇` : "";
    if (job?.status === "failed") this.showError(job, true);
    clearTimeout(this.timer);
    if (running(job)) { this.schedule(); return; }
    if (job?.status === "completed" && this.watched.has(job.id) && !this.completed.has(job.id)) {
      this.completed.add(job.id); this.remember(null);
      toast("总结已归档，本批未读已标记为已读"); this.onCompleted();
      await this.load({ select: this.isVisible() ? job.id : "" });
    }
  }
  schedule(delay = 1200) {
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      const job = this.job, token = this.token;
      if (!running(job)) return;
      try {
        const data = await api(`/api/news-summaries/jobs/${encodeURIComponent(job.id)}`);
        if (token !== this.token || this.job?.id !== job.id) return;
        if (data.job.database_token !== token) { await this.load(); return; }
        await this.receiveJob(data.job);
      } catch (error) {
        if (token !== this.token || this.job?.id !== job.id) return;
        this.showError(error); this.schedule(4000);
      }
    }, delay);
  }
  async prepare() {
    if (this.busy || running(this.job)) return;
    this.busy = "prepare"; this.revision++; this.controls();
    const token = this.token;
    try {
      const data = await api("/api/news-summaries/prepare", { method: "POST", body: { request_id: this.requestIdForToken(), database_token: token } });
      if (token !== this.token) return;
      if (data.job) { this.remember(null); await this.receiveJob(data.job, true); return; }
      if (!data.preview || data.preview.database_token !== token) throw new Error("summary_database_changed");
      this.preview = data.preview; this.submitted = false; this.renderPreview(); this.modal.showModal();
      $("#discardSummaryBtn").focus();
      this.expiryTimer = setInterval(() => this.updateConfirmation(), 1000);
    } catch (error) { if (token === this.token) this.showError(error); }
    finally { this.busy = ""; this.controls(); }
  }
  renderPreview() {
    const p = this.preview, scope = p.scope;
    $("#summaryModalBody").innerHTML = `<p>本次已锁定点击时的全部未读新闻，使用 <strong>${e(runtimeLabel(p))}</strong> 生成补课总结，并归入「新闻总结」。</p><section class="catchup-scope-preview"><div class="catchup-scope-heading"><strong>实际总结日期范围</strong><span>${e(range(p))}</span></div><p>${e(scopeText(scope)).replaceAll("\n", "<br>")}</p></section><p class="muted">限制：${count(scope.limits.max_days)} 个自然日、${count(scope.limits.max_items)} 条；按实际生成请求大小筛选。${scope.input_budget ? `本次输入 ${bytes(scope.input_budget.generation_bytes)}。` : ""}${scope.fallback_window ? "当前日期窗口内无新闻，已回溯至最近有新闻的日期。" : ""}</p><p class="catchup-settings-hint">范围可在 <button class="catchup-range-link" id="modalSummarySettingsBtn" type="button">设置 › 积压新闻总结范围</button> 中调整。</p><p class="muted" id="summaryExpiryNotice" role="status" hidden></p><div id="summaryModalError" role="alert"></div>`;
    $("#modalSummarySettingsBtn").onclick = async () => { if (this.busy) return; await this.dismiss(); this.openSettings(); };
    this.updateConfirmation();
  }
  updateConfirmation() {
    const expired = !this.preview || Date.now() / 1000 >= this.preview.expires_at;
    $("#confirmSummaryBtn").disabled = !!this.busy && this.busy !== "prepare" || expired;
    $("#confirmSummaryBtn").textContent = this.busy === "submit" ? "正在启动…" : "确认启动并标读全部";
    for (const id of ["#closeSummaryModalBtn", "#discardSummaryBtn", "#modalSummarySettingsBtn"]) if ($(id)) $(id).disabled = this.busy === "submit";
    const notice = $("#summaryExpiryNotice");
    if (notice) {
      notice.hidden = !expired;
      notice.textContent = expired ? "预览已过期，请取消后重新预览。" : "";
    }
  }
  async dismiss() {
    if (this.busy === "submit") return;
    const preview = this.preview;
    this.modal.close(); clearInterval(this.expiryTimer); this.preview = null;
    if (preview && !this.submitted) {
      this.remember(null);
      await api(`/api/news-summaries/previews/${encodeURIComponent(preview.id)}/discard`, { method: "POST", body: { database_token: preview.database_token } }).catch(() => {});
    }
    if (this.submitted) this.load(); // A lost POST response must be reconciled, never assumed cancelled.
    this.controls();
  }
  async confirm() {
    if (this.busy || !this.preview || Date.now() / 1000 >= this.preview.expires_at) { this.updateConfirmation(); return; }
    this.busy = "submit"; this.revision++; this.submitted = true; this.controls(); this.updateConfirmation();
    const token = this.token;
    try {
      const data = await api("/api/news-summaries", { method: "POST", body: { request_id: this.requestIdForToken(), database_token: token, preview_id: this.preview.id } });
      if (token !== this.token) return;
      this.modal.close(); clearInterval(this.expiryTimer); this.preview = null; this.remember(null);
      await this.receiveJob(data.job, true);
    } catch (error) {
      if (token !== this.token) return;
      this.showError(error, false, $("#summaryModalError"));
      if (["summary_settings_changed", "summary_preview_invalid", "summary_database_changed", "summary_source_changed"].includes(error.code)) {
        this.remember(null); this.preview.expires_at = 0;
      }
    } finally { this.busy = ""; this.controls(); this.updateConfirmation(); }
  }
  async cancel() {
    if (this.busy || !running(this.job)) return;
    const job = this.job, token = this.token;
    this.busy = "cancel"; this.revision++; this.controls();
    try {
      const data = await api(`/api/news-summaries/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST", body: { database_token: token } });
      if (token === this.token && this.job?.id === job.id) await this.receiveJob(data.job, true);
    } catch (error) { if (token === this.token) this.showError(error); }
    finally { this.busy = ""; this.controls(); }
  }
  handleKey(event, action) {
    const key = event.key.toLowerCase();
    if (action === "search") { event.preventDefault(); $("#summariesSearchInput").focus(); }
    if (!action && !event.ctrlKey && !event.metaKey && !event.altKey && ["enter", " "].includes(key) && event.target.matches("[data-summary-id]")) {
      event.preventDefault(); this.select(event.target.dataset.summaryId, true);
    }
    if (["previous", "next"].includes(action)) {
      event.preventDefault();
      const index = this.entries.findIndex(item => item.id === this.selected);
      const item = this.entries[Math.max(0, Math.min(this.entries.length - 1, index + (action === "next" ? 1 : -1)))];
      if (item) { this.select(item.id); $(`[data-summary-id="${CSS.escape(item.id)}"]`)?.scrollIntoView({ block: "nearest" }); }
    }
  }
  export() {
    if (!this.detail) return;
    const s = this.detail;
    const text = `# ${s.title}\n\n${s.created_at} · ${s.item_count} 篇\n\n${s.body}\n\n## 本次范围\n\n${s.validation?.scope ? scopeText(s.validation.scope) : ""}\n\n## 引用来源\n\n${s.sources.map(i => `- [${i.ref}] ${i.date || "报道日期不明"} · ${i.source_name || i.source} · ${i.title}${sourceURL(i.url) ? `\n  ${sourceURL(i.url)}` : ""}`).join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `新闻总结-${s.id}.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
