/* Show the reported error in Chinese where known, without diagnosing its cause. */
(() => {
  'use strict';
  const groups = {};
  function register(category, codes, message) {
    for (const code of codes.split(' ')) groups[code] = {category, message};
  }
  register('credentials', 'missing_api_key', '尚未配置当前服务的 API 密钥。');
  register('model', 'api_test_failed', '模型连接测试失败。');
  register('model', 'api_test_timeout', '模型连接测试超时。');
  register('input', 'invalid_api_models', 'API 模型配置格式无效。');
  register('input', 'invalid_classification_concurrency', '分类并发上限必须是 1–32 的整数。');
  register('input', 'invalid_classification_unread_only', '仅对未读新闻分类必须为开启或关闭。');
  register('input', 'invalid_classification_enabled', '自动分类必须为开启或关闭。');
  register('input', 'invalid_classification_settings', '新闻分类设置格式无效。');
  register('input', 'classification_disabled', '自动分类已关闭，请先在新闻分类设置中开启。');
  register('credentials', 'credentials codex_auth_required oar_auth_required authentication_error invalid_api_key', '身份验证失败。');
  register('model', 'models_unavailable', '获取模型列表失败。');
  register('model', 'translation_test_failed', '翻译模型连接测试失败。');
  register('model', 'model model_not_found model_not_supported oar_model_unavailable', '所选模型不存在或不可用。');
  register('model', 'pi_model_not_selected', '尚未选择模型。');
  register('timeout', 'timeout codex_timeout agent_timeout provider_timeout summary_timeout TIMEOUT ETIMEDOUT', '请求超时。');
  register('capacity', 'capacity overloaded_error service_overloaded', '服务繁忙。');
  register('rate', 'rate rate_limit_exceeded rate_limit_error', '请求过于频繁。');
  register('quota', 'quota insufficient_quota quota_exceeded', '账户可用额度不足。');
  register('connection', 'connection ECONNREFUSED', '连接被拒绝。');
  register('connection', 'ECONNRESET', '连接已重置。');
  register('connection', 'ENOTFOUND', '服务地址解析失败。');
  register('connection', 'ENETUNREACH NETWORK_ERROR', '网络请求失败。');
  register('connection', 'BROWSER_CONNECT', '浏览器连接失败。');
  register('connection', 'HTTP_ERROR', '网页请求失败。');
  register('credentials', 'AUTH_REQUIRED', '需要登录。');
  register('input', 'input summary_input_limit context_length_exceeded input_too_large', '输入内容超过处理上限。');
  register('input', 'too_many_messages', '对话超过 200 条消息，无法完整归档，本次未保存。');
  register('input', 'message_too_long', '单条对话超过 20000 字符，无法完整归档，本次未保存。');
  register('input', 'quote_too_long', '引用超过 4000 字符上限。');
  register('input', 'invalid_summary_limits', '总结范围设置无效。');
  register('input', 'invalid_note note_too_long', '想法内容为空或超过长度限制。');
  register('input', 'invalid_model invalid_codex_model', '模型名称格式无效。');
  register('config', 'invalid_agent_runtime invalid_codex_provider invalid_codex_profile invalid_codex_settings invalid_codex_sandbox', '模型运行配置无效。');
  register('config', 'invalid_payload invalid_request_id', '请求参数无效。');
  register('config', 'invalid_codex_reasoning_effort unsupported_reasoning_effort', '当前模型不支持所选推理强度。');
  register('config', 'invalid_database_path database_not_found database_path_not_found database_path_must_exist', '数据库路径无效或文件不存在。');
  register('config', 'database_not_sqlite database_path_not_file database_path_not_sqlite', '所选文件不是有效的 SQLite 数据库。');
  register('config', 'database_path_not_readable database_path_not_writable', '数据库文件不可读写。');
  register('config', 'database_path_controlled_by_environment', '数据库路径由启动环境指定，无法修改。');
  register('config', 'keychain_unavailable', '系统钥匙串不可用。');
  register('config', 'update_repository_not_configured', '尚未配置更新仓库。');
  register('missing', 'codex_missing cli_missing oar_node_missing oar_dependency_missing', '缺少所需的本机程序或依赖。');
  register('missing', 'oar_node_version cli_incompatible', '本机程序版本不兼容。');
  register('missing', 'codex_probe_failed', '本机程序检测失败。');
  register('protocol', 'codex_protocol_error oar_protocol_error INVALID_JSON invalid_answer invalid_archive_summary', '返回结果格式无效。');
  register('protocol', 'EMPTY_RESULT empty_model_response codex_empty_answer oar_empty_answer empty_archive_summary', '返回内容为空。');
  register('protocol', 'CONTENT_TOO_SHORT', '抓取的正文长度不足。');
  register('protocol', 'EMPTY_TWITTER_THREAD', '返回的 X 推文线程为空。');
  register('processing', 'summary_validation_failed', '总结正文或引用校验失败。');
  register('save', 'summary_save_failed', '总结保存失败。');
  register('save', 'note_changed note_not_found', '这条想法已被修改或删除。');
  register('preview', 'summary_preview_invalid summary_settings_changed summary_database_changed summary_source_changed', '总结预览已失效。');
  register('busy', 'connection_test_busy summary_busy job_in_progress provider_busy', '已有任务正在处理。');
  register('session', 'notification_library_changed session_library_changed', '新闻库已改变，当前通知或会话不属于此库，请刷新后重试。');
  register('session', 'notification_session_unavailable', '这段会话已过期或被删除，通知已保留，可选择忽略。');
  register('input', 'notification_article_unavailable', '这篇新闻已不可访问，通知已保留，可选择忽略。');
  register('input', 'notification_not_found', '这条通知已不可用，请刷新通知列表。');
  register('session', 'legacy_session_unbound', '旧会话尚未关联新闻库，本次保留历史记录；继续提问后可在当前库管理。');
  register('session', 'session_config_changed session_expired session_not_found session_invalid codex_session_invalid oar_legacy_session_missing', '当前会话已失效或配置已改变。');
  register('session', 'oar_resources_changed oar_trust_changed oar_project_untrusted oar_extension_failed', '本机会话资源不可用或未获信任。');
  register('context', 'detail_not_ready', '新闻正文尚未就绪。');
  register('context', 'empty_archive_source', '没有可归档的内容。');
  register('limit', 'unsupported_source SKIPPED_SOURCE', '暂不支持此新闻来源。');
  register('limit', 'BLOOMBERG_VIDEO', '暂不支持抓取 Bloomberg 视频正文。');
  register('limit', 'no_tool_mode_unsupported tool_mode_violation summary_tool_used', '当前执行方式不符合工具使用限制。');
  register('api', 'agent_api_unavailable', 'Agent 接口不可用。');
  register('empty', 'summary_empty', '当前范围内没有未读新闻。');
  register('unknown', 'summary_runtime_failed summary_launch_failed', '新闻总结执行失败。');
  register('unknown', 'summary_interrupted', '新闻总结已中断。');
  register('unknown', 'codex_turn_failed codex_runtime_failed oar_runtime_failed oar_turn_failed provider_failed agent_job_failed', '模型请求失败。');
  register('unknown', 'settings_save_failed', '设置保存失败。');
  register('unknown', 'settings_secret_save_failed', '密钥保存失败。');
  register('unknown', 'settings_secret_delete_failed', '密钥删除失败。');
  register('unknown', 'archive_request_failed', '归档失败。');
  register('unknown', 'FETCH_FAILED', '正文抓取失败。');
  register('unknown', 'FETCH_ERROR DETAIL_JOB_ERROR', '正文抓取失败。');
  register('unknown', 'INTERRUPTED', '抓取任务已中断。');
  register('unknown', 'COMMAND_EXEC', '抓取命令执行失败。');
  register('unknown', 'SUBPROCESS_ERROR OPENCLI_ERROR', '抓取程序执行失败。');
  register('unknown', 'request_failed unknown', '请求失败。');
  const translations = {
    'Failed to fetch': '网络请求失败。',
    'fetch failed': '网络请求失败。',
    'Load failed': '加载失败。',
    'NetworkError when attempting to fetch resource.': '网络请求失败。',
    'Browser Bridge extension not connected': '浏览器桥接扩展未连接。',
    'Command failed': '命令执行失败。',
    'login required': '需要登录。',
    'Bloomberg served the bot-protection page': 'Bloomberg 返回了机器人验证页面。',
    'Browser connection dropped after the navigate command was dispatched': '网页导航开始后，浏览器连接断开。',
    'Tweet has no article content': '这条推文没有长文内容。',
    'executable missing': '找不到可执行程序。',
    'Expecting value': '未读取到有效的 JSON 内容。',
  };
  const httpMessages = {400:'请求参数无效',401:'身份验证失败',403:'无权访问',404:'请求的资源不存在',408:'请求超时',429:'请求过于频繁',500:'服务器内部错误',502:'网关错误',503:'服务暂不可用',504:'网关超时'};
  const sensitive = /authorization\s*[:=]|(?:api[_-]?key|access[_-]?token|password)\s*[:=]|\b(?:sk|sess)-[\w-]+|https?:\/\/[^/\s]+:[^/\s]+@/i;
  function safeToken(x) {
    const s = String(x || '');
    return groups[s] || /^http_\d{3}$/.test(s) ? s : 'unknown';
  }
  function publicMessage(value) {
    const text = String(value || '').trim();
    // Error messages are text, never HTML. Do not echo credentials from upstream failures.
    if (sensitive.test(text)) return '错误信息中的敏感内容已隐藏。';
    const http = text.match(/^HTTP (\d{3})$/)?.[1];
    return translations[text] || (http ? `${httpMessages[http] || '请求失败'}（HTTP ${http}）。` : text.length > 600 ? text.slice(0, 600) + '…' : text);
  }
  function explain(input, options = {}) {
    const x = typeof input === 'string' ? {error:input} : (input || {});
    let raw = String(x.code || x.error || x.message || 'unknown').trim();
    if (raw === 'SKIPPED_SOURCE: BLOOMBERG_VIDEO') raw = 'BLOOMBERG_VIDEO';
    const prefix = raw.split(':', 1)[0];
    const code = safeToken(prefix);
    const categoryCode = safeToken(x.category);
    const rule = groups[code]?.category === 'unknown' && categoryCode !== 'unknown'
      ? groups[categoryCode] : groups[code];
    const http = Number(x.httpStatus || x.statusCode || options.httpStatus) || (/^http_\d{3}$/.test(code) ? Number(code.slice(5)) : 0);
    const category = rule?.category || 'unknown';
    const reported = x.message && x.message !== raw ? x.message : raw.includes(':') ? raw.slice(raw.indexOf(':') + 1).trim() : '';
    let message;
    if (sensitive.test(raw)) message = publicMessage(raw);
    else if (reported && (code !== 'unknown' || raw === 'unknown' || /^[A-Za-z][\w.-]*$/.test(raw))) message = publicMessage(reported);
    else if (translations[raw]) message = translations[raw];
    else if (code !== 'unknown' && rule) message = rule.message;
    else if (/^http_\d{3}$/.test(raw) || raw === 'unknown') message = http ? `${httpMessages[http] || '请求失败'}（HTTP ${http}）。` : '请求失败。';
    else message = publicMessage(raw);
    // Preserve the error code's meaning alongside an unrecognized upstream detail.
    if (reported && raw.includes(':') && rule && code !== 'unknown' && !message.startsWith(rule.message)) message = `${rule.message} ${message}`;
    const stage = ['request','generation','validation','save','fetch','settings','connection','session'].includes(options.stage) ? options.stage : 'request';
    const lines = ['错误码：' + code, '阶段：' + stage, '分类：' + category];
    if (http >= 100 && http <= 599) lines.push('HTTP：' + http);
    const diag = x.diagnostic || x.diagnostics || {};
    for (const k of ['exit_code','seconds','terminal','stderr_present']) {
      const v = diag[k];
      if (typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v))) lines.push(k + '：' + v);
    }
    return {code, category, message, action:'', state:'', details:lines.join('\n')};
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
    const text=doc.createElement('span');text.style.whiteSpace='pre-line';text.textContent=e.message;container.appendChild(text);
    const details=doc.createElement('details');details.className='user-error-details';
    const summary=doc.createElement('summary');summary.textContent='错误详情（反馈时使用）';details.appendChild(summary);
    const pre=doc.createElement('pre');pre.textContent=e.details;details.appendChild(pre);
    const button=doc.createElement('button');button.type='button';button.textContent='复制错误信息';
    const hint=doc.createElement('span');hint.setAttribute('role','status');
    button.addEventListener('click',async()=>{const ok=await copy(e.details);hint.textContent=ok?'已复制':'无法自动复制，请选中上方详情手动复制。';});
    details.appendChild(button);details.appendChild(hint);container.appendChild(details);
  }
  function fromResponse(payload, response, fallback) {
    const e=new Error(payload?.message || payload?.error || fallback || 'unknown');e.code=payload?.error || fallback;e.httpStatus=response?.status || 0;
    e.category=payload?.category;e.diagnostic=payload?.diagnostic;return e;
  }
  globalThis.NewsErrors={explain,render,copy,fromResponse};
})();
