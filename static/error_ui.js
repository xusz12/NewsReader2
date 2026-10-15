/* Evidence-based error presentation. Never copies raw upstream text or news. */
(() => {
  'use strict';
  const groups = {};
  function register(category, codes, message, action) {
    for (const code of codes.split(' ')) groups[code] = {category, message, action};
  }
  register('timeout', 'timeout codex_timeout agent_timeout provider_timeout summary_timeout TIMEOUT', '等待结果超时，本次未取得完整结果；超时并不代表网络已断开。', '可以稍后重试；若其他入口正常而此处持续超时，请复制错误信息反馈。');
  register('credentials', 'credentials codex_auth_required oar_auth_required authentication_error invalid_api_key', '所选服务拒绝了登录或凭据。', '请检查对应服务的本机登录或凭据配置；自定义服务不一定需要登录OpenAI。');
  register('capacity', 'capacity overloaded_error service_overloaded', '模型服务明确报告当前繁忙。', '请稍后重试，不必反复修改网络设置。');
  register('rate', 'rate rate_limit_exceeded rate_limit_error', '服务限制了当前请求频率。', '请稍后重试；此提示不等于账户额度已经耗尽。');
  register('quota', 'quota insufficient_quota quota_exceeded', '服务明确报告账户额度不足。', '请检查对应服务账户的额度，再发起请求。');
  register('model', 'model model_not_found model_not_supported oar_model_unavailable pi_model_not_selected', '当前模型尚未选择、未找到或不受服务支持。', '请打开模型管理核对运行方式和模型。');
  register('connection', 'connection ECONNREFUSED ECONNRESET ENOTFOUND ENETUNREACH', '服务连接未能建立或已断开。', '请检查对应服务地址、代理或服务是否启动，再重试；不能据此断定所有网络都不可用。');
  register('input', 'input summary_input_limit context_length_exceeded input_too_large invalid_summary_limits', '本次输入超过处理限制，或范围设置无效。', '请打开设置减少总结日期、条数范围，或核对输入限制后重试。');
  register('config', 'invalid_agent_runtime invalid_codex_model invalid_codex_profile invalid_codex_provider invalid_codex_reasoning_effort invalid_payload invalid_request_id invalid_database_path database_not_found database_not_sqlite database_path_not_found database_path_not_file database_path_not_sqlite database_path_controlled_by_environment keychain_unavailable', '当前设置或输入未通过检查。', '请打开对应设置检查输入；若有效配置仍无法保存，请复制错误信息反馈。');
  register('missing', 'codex_missing cli_missing oar_node_missing oar_node_version oar_dependency_missing cli_incompatible codex_probe_failed', '本机运行程序未安装或版本能力尚未满足要求。', '请检查本机安装和版本；不确定如何处理时复制错误信息反馈。');
  register('protocol', 'codex_protocol_error oar_protocol_error INVALID_JSON EMPTY_RESULT CONTENT_TOO_SHORT invalid_answer codex_empty_answer oar_empty_answer empty_archive_summary invalid_archive_summary', '没有收到完整、可用的结果，或结果格式未通过检查；原因尚未确定。', '可以重试；持续出现时请复制错误信息反馈，不能仅据此认定网络或应用有缺陷。');
  register('processing', 'summary_validation_failed', '模型执行后，结果未通过应用的正文或引用检查。', '请复制错误信息反馈；不要仅排查网络，也不要把部分输出当作成功结果。');
  register('save', 'summary_save_failed', '总结保存或可读取检查未完成。', '请复制错误信息反馈。');
  register('preview', 'summary_preview_invalid summary_settings_changed summary_database_changed summary_source_changed', '总结预览已过期，或数据库、设置、材料发生了变化。', '请刷新状态并重新预览；不要继续使用旧确认框。');
  register('busy', 'connection_test_busy summary_busy job_in_progress provider_busy', '已有请求正在处理。', '请等待当前任务结束，或使用该任务的停止按钮。');
  register('session', 'session_config_changed session_expired codex_session_invalid oar_legacy_session_missing oar_resources_changed oar_trust_changed oar_project_untrusted oar_extension_failed', '当前会话或本机资源配置已变化，无法按原配置继续。', '请检查本机配置或明确开启新会话；不会自动换模型或扩大权限。');
  register('context', 'detail_not_ready empty_archive_source note_too_long', '当前正文或可归档内容尚未满足处理条件。', '请等待正文就绪，或检查可归档内容后再操作。');
  register('limit', 'unsupported_source SKIPPED_SOURCE BLOOMBERG_VIDEO no_tool_mode_unsupported tool_mode_violation summary_tool_used', '此来源或执行方式超出当前功能支持范围。', '请查看原文或选择受支持的功能；反复重试通常不会解决这个限制。');
  register('api', 'agent_api_unavailable', '当前应用接口未能完成请求。', '请刷新页面；若持续出现，请复制错误信息反馈。');
  register('empty', 'summary_empty', '当前没有符合范围的未读新闻。', '请刷新新闻流，或等待新新闻到达。');
  register('unknown', 'summary_runtime_failed summary_launch_failed summary_interrupted codex_turn_failed codex_runtime_failed oar_runtime_failed oar_turn_failed provider_failed settings_save_failed settings_secret_save_failed settings_secret_delete_failed agent_job_failed archive_request_failed FETCH_FAILED SUBPROCESS_ERROR', '请求没有完成，原因尚未确定。', '请刷新确认状态；持续失败时复制错误信息反馈，不能仅据此判断网络或应用有缺陷。');
  register('config', 'invalid_codex_reasoning_effort unsupported_reasoning_effort', '所选推理强度不符合当前模型支持范围。', '请打开模型管理，选择受支持的推理强度或沿用默认。');
  register('config', 'invalid_codex_model', '模型名称格式无效。', '请在模型管理中选择模型，或核对其他模型名称。');
  register('config', 'invalid_codex_provider invalid_codex_profile invalid_codex_settings invalid_codex_sandbox', '本机连接覆盖或配置预设不符合输入要求。', '请检查模型管理的高级设置；不需要覆盖时留空继承本机配置。');
  register('config', 'database_path_must_exist database_path_not_readable database_path_not_writable database_path_not_sqlite', '所选数据库文件不存在、不可读写，或不是有效SQLite数据库。', '请在数据库设置选择已有、可读写的SQLite文件；不要删除真实数据库。');
  register('config', 'database_path_controlled_by_environment', '数据库位置由启动环境指定，不能在此覆盖。', '如需更换，请调整启动配置；不确定时复制错误信息反馈。');
  register('config', 'update_repository_not_configured', '此独立项目尚未配置更新仓库。', '在线更新未启用，请使用本项目后续发布的版本。');
  const contexts = {agent:'新闻问答',chat:'聊天',summary:'新闻总结',connection:'连接测试',detail:'新闻正文获取',settings:'设置保存'};
  function safeToken(x) {
    const s = String(x || '');
    // Unknown strings may be entire articles/keys. Retain only known codes or bounded HTTP codes.
    return groups[s] || /^http_\d{3}$/.test(s) ? s : 'unknown';
  }
  function explain(input, options = {}) {
    const x = typeof input === 'string' ? {error:input} : (input || {});
    let raw = x.code || x.error || x.message || 'unknown';
    // Only established legacy source prefixes, never arbitrary English substring attribution.
    if (/^SKIPPED_SOURCE: BLOOMBERG_VIDEO$/.test(raw)) raw='BLOOMBERG_VIDEO';
    else if (/^SKIPPED_SOURCE(?:: [A-Z_]+)?$/.test(raw)) raw='SKIPPED_SOURCE';
    const prefix = String(raw).split(':',1)[0];
    if (['INVALID_JSON','CONTENT_TOO_SHORT','EMPTY_RESULT'].includes(prefix)) raw=prefix;
    const code = safeToken(raw);
    const categoryCode = safeToken(x.category);
    const rule = groups[code]?.category === "unknown" && groups[categoryCode]
      ? groups[categoryCode] : groups[code] || groups[categoryCode];
    const http = Number(x.httpStatus || x.statusCode || options.httpStatus) || (/^http_\d{3}$/.test(code) ? Number(code.slice(5)) : 0);
    let message = rule?.message || '请求没有完成，原因尚未确定。';
    let action = rule?.action || '请刷新确认当前状态；持续失败时复制错误信息反馈，不必反复排查自己的网络。';
    let category = rule?.category || 'unknown';

    if (!rule && http === 429) { message='服务暂时限制了请求；尚不能确定是频率、容量还是额度原因。'; action='请稍后重试；持续出现时复制错误信息反馈。'; }
    if (!rule && http === 401) { message='请求未获服务授权；具体凭据原因尚未确定。'; action='请检查对应服务的登录或凭据，不要发送凭据给他人。'; }
    if (category === 'input' && options.context !== 'summary') action='请缩短本次输入，或明确开启新会话后重试。';
    const context=contexts[options.context] || '操作';
    let state='';
    if (options.context === 'summary') {
      state = options.confirmedUncommitted === true
        ? '服务端已确认任务未提交成功：总结未保存，本任务未改变本批未读状态。'
        : '结果状态尚未确认：请刷新查看任务及新闻状态，不要重复提交；不能据此判断是否已保存或标读。';
    } else if (options.context === 'settings') state='请重新读取设置核对是否保存成功，确认前不要重复提交。';
    const stage = ['request','generation','validation','save','fetch','settings','connection','session'].includes(options.stage) ? options.stage : 'request';
    const lines=['错误码：'+code, '阶段：'+stage, '分类：'+category];
    if (http >= 100 && http <= 599) lines.push('HTTP：'+http);
    // Diagnostic whitelist only; never raw exception, stderr, URLs, prompts, bodies, headers or config.
    const diag=x.diagnostic || x.diagnostics || {};
    for (const k of ['exit_code','seconds','terminal','stderr_present']) {
      const v=diag[k]; if (typeof v==='boolean' || (typeof v==='number' && Number.isFinite(v))) lines.push(k+'：'+v);
    }
    return {code,category,message:context+'未完成：'+message,action,state,details:lines.join('\n')};
  }
  async function copy(text, doc=globalThis.document, nav=globalThis.navigator) {
    // Only allow the already whitelisted details document, not callers' raw strings.
    const clean=String(text).split('\n').filter(l=>/^(错误码|阶段|分类|HTTP|exit_code|seconds|terminal|stderr_present)：[A-Za-z0-9_.:-]+$/.test(l)).join('\n');
    try { if (nav?.clipboard?.writeText) { await nav.clipboard.writeText(clean); return true; } } catch (_) {}
    const area=doc.createElement('textarea');area.value=clean;area.setAttribute('readonly','');area.style.position='fixed';area.style.left='-9999px';
    const previous=doc.activeElement;doc.body.appendChild(area);area.select();
    let ok=false;try {ok=!!doc.execCommand?.('copy');} catch (_) {} finally {area.remove();previous?.focus?.();}
    return ok;
  }
  function render(container, input, options={}) {
    if (!container) return;
    const e=explain(input,options);container.replaceChildren();
    const doc=container.ownerDocument || document;
    const text=doc.createElement('span');text.style.whiteSpace='pre-line';text.textContent=[e.message,e.action,e.state].filter(Boolean).join('\n');container.appendChild(text);
    const details=doc.createElement('details');details.className='user-error-details';
    const summary=doc.createElement('summary');summary.textContent='错误详情（反馈时使用）';details.appendChild(summary);
    const pre=doc.createElement('pre');pre.textContent=e.details;details.appendChild(pre);
    const button=doc.createElement('button');button.type='button';button.textContent='复制错误信息';
    const hint=doc.createElement('span');hint.setAttribute('role','status');
    button.addEventListener('click',async()=>{const ok=await copy(e.details);hint.textContent=ok?'已复制':'无法自动复制，请选中上方详情手动复制。';});
    details.appendChild(button);details.appendChild(hint);container.appendChild(details);
  }
  function fromResponse(payload, response, fallback) {
    const e=new Error(payload?.error || fallback || 'unknown');e.code=payload?.error || fallback;e.httpStatus=response?.status || 0;
    e.category=payload?.category;e.diagnostic=payload?.diagnostic;return e;
  }
  globalThis.NewsErrors={explain,render,copy,fromResponse};
})();
