import { $, escapeHTML as e, api, toast, errorText, busy, formatBytes } from './ui.js';
import { confirmAction } from './confirm-dialog.js';

export function normalizeXHandle(value) {
  let handle = value.trim();
  if (/^(https?:\/\/)?(www\.)?(x\.com|twitter\.com)\//i.test(handle)) {
    const url = new URL(handle.includes('://') ? handle : 'https://' + handle);
    handle = url.pathname.replace(/^\/+|\/+$/g, '');
    if (url.username || url.password || url.port) throw new Error('请输入 X 账号名或主页链接');
  } else handle = handle.replace(/^@/, '');
  if (!/^[a-z0-9_]{1,15}$/i.test(handle) || /^(home|explore|search|notifications|messages|settings|intent|share|i)$/i.test(handle))
    throw new Error('请输入有效的 X 账号名或主页链接');
  return handle.toLowerCase();
}

export class XAccountSettings {
  constructor(panel) {
    this.panel = panel; this.input = ''; this.pending = false; this.adding = false;
    this.testing = false; this.testSequence = 0; this.testResult = null; this.testController = null;
  }
  render() {
    const root = $('#xAccountSettings');
    if (!root) return;
    const accounts = this.panel.draft.x_accounts || [];
    root.innerHTML = `<div class="button-row"><button class="btn-secondary" id="showXAccountFormBtn" type="button" aria-expanded="${this.adding}" aria-controls="xAccountForm">添加账号</button></div>
      <section class="settings-section-card settings-section-card--compact" id="xAccountForm" ${this.adding ? '' : 'hidden'}>
      <label class="field-item"><span class="field-label">X 账号</span><input class="field-input" id="xAccountInput" placeholder="@账号或主页链接" value="${e(this.input)}" autocomplete="off" spellcheck="false"></label>
      <div class="button-row"><button class="btn-secondary" id="addXAccountBtn" type="button" aria-describedby="xAccountTestResult">添加</button><button class="btn-secondary" id="cancelXAccountBtn" type="button">取消</button></div>
      <p class="muted status-text" id="xAccountTestResult" role="status" aria-atomic="true" hidden></p></section>
      ${accounts.length ? '<div class="form-group-grid" id="xAccountList">' + accounts.map(account => `<section class="settings-section-card settings-section-card--compact" data-x-account="${e(account.handle)}">
        <div class="section-card-title"><span>${account.name && account.name.toLowerCase() !== account.handle ? `${e(account.name)} <span class="muted">@${e(account.handle)}</span>` : `@${e(account.name || account.handle)}`}</span><button class="reader-action-btn danger" type="button" data-remove-x="${e(account.handle)}" aria-label="移除账号 @${e(account.handle)}" ${this.pending ? 'disabled' : ''}>移除账号</button></div>
        <label class="settings-toggle settings-switch"><span>自动翻译</span><input type="checkbox" role="switch" data-x-option="auto_translate" aria-label="自动翻译 @${e(account.handle)}" ${account.auto_translate ? 'checked' : ''}></label>
        <label class="settings-toggle settings-switch"><span>自动抓取全文</span><input type="checkbox" role="switch" data-x-option="auto_fulltext" aria-label="自动抓取全文 @${e(account.handle)}" ${account.auto_fulltext ? 'checked' : ''}></label>
      </section>`).join('') + '</div>' : '<p class="muted">尚未添加 X 账号。</p>'}`;
    const input = $('#xAccountInput');
    input.oninput = () => { this.input = input.value; input.setCustomValidity(''); this.invalidateTest(); this.renderTest(); };
    input.onkeydown = event => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); this.add(); } };
    $('#addXAccountBtn').onclick = () => this.add();
    $('#showXAccountFormBtn').onclick = () => this.showForm(!this.adding);
    $('#cancelXAccountBtn').onclick = () => this.showForm(false);
    $('#xAccountForm').onkeydown = event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.showForm(false); }
    };
    root.querySelectorAll('[data-x-option]').forEach(toggle => {
      toggle.onchange = () => {
        const account = accounts.find(a => a.handle === toggle.closest('[data-x-account]').dataset.xAccount);
        account[toggle.dataset.xOption] = toggle.checked;
        this.panel.status('有未保存的更改');
      };
    });
    root.querySelectorAll('[data-remove-x]').forEach(button => {
      button.onclick = () => { void busy(button, () => this.remove(button)); };
    });
    this.renderTest();
  }
  renderTest() {
    const button = $('#addXAccountBtn'), result = $('#xAccountTestResult');
    if (!button || !result) return;
    button.textContent = this.testing ? '测试中...' : '添加';
    button.disabled = this.testing;
    button.setAttribute('aria-busy', String(this.testing));
    $('#xAccountInput').disabled = this.testing;
    result.hidden = !this.testResult;
    result.dataset.status = this.testResult?.status || '';
    result.textContent = this.testResult?.text || '';
  }
  invalidateTest() {
    this.testSequence++;
    this.testController?.abort(); this.testController = null;
    this.testing = false; this.testResult = null;
  }
  reset() { this.invalidateTest(); this.input = ''; this.adding = false; }
  focusAdd() { $(this.adding ? '#xAccountInput' : '#showXAccountFormBtn')?.focus({preventScroll:true}); }
  showForm(open) {
    this.adding = open;
    $('#xAccountForm').hidden = !open;
    $('#showXAccountFormBtn').setAttribute('aria-expanded', String(open));
    if (!open) {
      this.invalidateTest(); this.input = ''; $('#xAccountInput').value = ''; $('#xAccountInput').setCustomValidity('');
      this.renderTest();
    }
    this.focusAdd();
  }
  async add() {
    if (this.testing) return;
    const input = $('#xAccountInput');
    let handle;
    try {
      handle = normalizeXHandle(input.value);
      const accounts = this.panel.draft.x_accounts || [];
      if (accounts.some(a => a.handle === handle)) throw new Error('该账号已经在采集列表中');
      if (accounts.length >= 200) throw new Error('最多可添加 200 个账号');
    } catch (error) {
      this.testResult = {status:'failed', text:error.message}; this.renderTest();
      input.setCustomValidity(error.message); input.reportValidity(); return;
    }
    const panel = this.panel, draft = panel.draft, library = panel.saved.database.path;
    const sequence = ++this.testSequence;
    const controller = this.testController = new AbortController();
    const timeout = setTimeout(() => controller.abort(new Error('TIMEOUT')), 60000);
    this.testing = true;
    this.testResult = {status:'running', text:`正在测试 @${handle}，确认能否获取内容…`};
    this.renderTest();
    try {
      const data = await api('/api/settings/x-accounts/test', {method:'POST', signal:controller.signal, body:{
        handle, x_accounts_revision:panel.saved.x_accounts_revision,
      }});
      if (sequence !== this.testSequence) return;
      if (panel.draft !== draft || panel.saved.database.path !== library) throw new Error('配置已变化，请重新测试账号');
      if (data.handle !== handle || !Number.isInteger(data.count) || data.count < 1) throw new Error('x_account_test_failed');
      if (draft.x_accounts.some(a => a.handle === handle)) throw new Error('该账号已经在采集列表中');
      draft.x_accounts.push({handle, name:handle, auto_translate:false, auto_fulltext:false});
      this.testing = false; this.input = '';
      this.testResult = {status:'success', text:`测试通过：已获取 @${handle} 的 ${data.count} 条内容，账号已添加。`};
      const focused = $('#xAccountForm')?.contains(document.activeElement);
      this.render(); panel.status('有未保存的更改');
      if (focused && panel.open && panel.section === 'x') this.focusAdd();
    } catch (error) {
      if (sequence === this.testSequence) this.testResult = {status:'failed', text:`测试失败：${errorText(error)} 账号未添加。`};
    } finally {
      clearTimeout(timeout);
      if (sequence === this.testSequence) { this.testing = false; this.testController = null; this.renderTest(); }
    }
  }
  async remove(button) {
    if (this.pending) return;
    const panel = this.panel, handle = button.dataset.removeX, library = panel.saved.database.path;
    if (panel.saving) { toast('设置正在保存，请稍后再移除账号'); return; }
    const saved = panel.saved.x_accounts?.some(a => a.handle === handle);
    if (!saved) {
      panel.draft.x_accounts = panel.draft.x_accounts.filter(a => a.handle !== handle);
      this.render(); panel.status('有未保存的更改'); this.focusAdd(); return;
    }
    this.pending = true;
    try {
      const data = await api(`/api/settings/x-accounts/${encodeURIComponent(handle)}`);
      if (!panel.open || panel.section !== 'x' || !button.isConnected || panel.saved.database.path !== library) return;
      if (data.x_accounts_revision !== panel.saved.x_accounts_revision) throw new Error('账号列表已变化，请刷新页面后重试');
      const p = data.source;
      const choice = await confirmAction({title:`移除账号 @${handle}`, trigger:button,
        message:`移除后停止后续采集。\n历史数据：${p.news_count} 条新闻、${p.counts.article_details} 篇正文、${p.counts.article_ai} 篇译文、${p.note_count} 条想法、${p.counts.article_highlights} 处高亮，约 ${formatBytes(p.data_bytes)}。另有 ${p.pending_news_count} 条尚未发布内容。\n选择删除时，也会清除收藏、阅读标记和关联任务，且无法撤销。已生成的报告、主题研究成果及其他来源共用内容保留。`,
        action:'移除并保留数据', choices:[
          {value:'keep', label:'保留历史数据', action:'移除并保留数据'},
          {value:'delete', label:'同时删除历史数据', action:'移除并删除数据'},
        ]});
      if (!choice || panel.saved.database.path !== library) return;
      const result = await api(`/api/settings/x-accounts/${encodeURIComponent(handle)}`, {method:'DELETE', body:{
        delete_history:choice === 'delete', confirmation_token:p.confirmation_token, x_accounts_revision:data.x_accounts_revision,
      }});
      // Removal is an explicit action like source cleanup. Preserve every other
      // draft, including unsaved new accounts and per-account switch changes.
      panel.saved.x_accounts = result.x_accounts;
      panel.saved.x_accounts_revision = panel.draft.x_accounts_revision = result.x_accounts_revision;
      panel.draft.x_accounts = panel.draft.x_accounts.filter(a => a.handle !== handle);
      panel.saved.feed_source_subkeys = panel.draft.feed_source_subkeys = result.feed_source_subkeys;
      this.pending = false; this.render();
      $('#settingsSources').innerHTML = panel.sourceHTML();
      panel.status('账号已移除');
      panel.onSourceDeleted?.(result); panel.onSources?.(); panel.onSaved?.();
      toast(result.warning || (choice === 'delete' ? '账号及历史数据已删除' : '账号已移除，历史数据已保留'));
      if (panel.open && panel.section === 'x') this.focusAdd();
    } catch (error) { panel.status(errorText(error), true); throw error; }
    finally {
      this.pending = false;
      // A draft edit may redraw the list while the preview is loading.
      $('#xAccountSettings')?.querySelectorAll('[data-remove-x]').forEach(button => { button.disabled = false; });
    }
  }
}
