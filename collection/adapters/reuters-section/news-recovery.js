import { AuthRequiredError, CliError, CommandExecutionError, EmptyResultError, TimeoutError } from '@jackwener/opencli/errors';

export const TOTAL_BUDGET_MS = 30_000;
export const REQUEST_TIMEOUT_MS = 8_000;
export const MAX_REQUESTS = 3;
const RETRY_DELAYS_MS = [2_000, 5_000];
const POLL_MS = 1_000;
const REUTERS_HOME = 'https://www.reuters.com';

// Serialized into the browser; keep self-contained and read-only.
export function inspectListPage() {
  const url = new URL(window.location.href);
  const title = String(document.title || '');
  const text = String(document.body?.innerText || '').slice(0, 8000);
  const visible = element => !element.hidden && element.getAttribute('aria-hidden') !== 'true'
    && window.getComputedStyle(element).display !== 'none'
    && window.getComputedStyle(element).visibility !== 'hidden' && element.getClientRects().length > 0;
  const frames = Array.from(document.querySelectorAll('iframe')).filter(visible);
  const isDataDome = host => /(^|\.)captcha-delivery\.com$/i.test(host);
  const challengeUrls = frames.map(frame => {
    try { return new URL(frame.getAttribute('src') || '', url.href); } catch { return null; }
  }).filter(frame => frame && isDataDome(frame.hostname));
  if (isDataDome(url.hostname)) challengeUrls.push(url);
  // Avoid interpreting a news headline about verification/subscriptions as a gate.
  const gateText = !document.querySelector('main') || text.length < 1000 ? text : '';
  const interactive = challengeUrls.some(frame => /\/captcha(?:\/|$)/i.test(frame.pathname))
    || /drag (?:the )?(?:slider|puzzle)|slide (?:the |to )|press and hold|complete the captcha|check the box to (?:confirm|verify)/i.test(gateText);
  const automatic = challengeUrls.length > 0
    || frames.some(frame => /datadome|device check/i.test(frame.getAttribute('title') || ''))
    || /device check|checking your browser|just a moment/i.test(title)
    || /datadome|captcha|verify you are human|verifying your browser|human verification/i.test(gateText);
  let kind = 'loading';
  if (interactive) kind = 'human_verification';
  else if (automatic) kind = 'automatic_verification';
  else if (/access (?:to this page has been )?denied|you have been blocked|unusual traffic|request (?:was )?blocked/i.test(title + '\n' + gateText)) kind = 'blocked';
  else if (/subscribe to (?:continue|read)|subscription required|sign in to (?:continue|read|access)|log in to (?:continue|read|access)/i.test(gateText)) kind = 'access_required';
  else if (/(^|\.)reuters\.com$/i.test(url.hostname) && /reuters/i.test(title)
      && document.readyState !== 'loading') kind = 'ready';
  return { kind };
}

export function buildFetchScript(alias, count, timeoutMs) {
  const query = JSON.stringify({ collection_alias: alias, exclude: '', size: count, website: 'reuters' });
  const apiUrl = REUTERS_HOME + '/pf/api/v3/content/fetch/articles-by-collection-alias-or-id-v1?query=' + encodeURIComponent(query);
  return `(async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ${timeoutMs});
    try {
      const response = await fetch(${JSON.stringify(apiUrl)}, { credentials: 'include', signal: controller.signal });
      const retryAfter = response.headers.get('Retry-After');
      if (!response.ok) {
        await response.body?.cancel();
        return { ok: false, status: response.status, retryAfter };
      }
      const text = await response.text();
      try { return { ok: true, status: response.status, data: JSON.parse(text), retryAfter }; }
      catch { return { ok: false, status: response.status, parseError: true, textPreview: text.slice(0, 500), retryAfter }; }
    } catch (error) {
      return { ok: false, status: 0, timeout: controller.signal.aborted, error: String(error?.message || error) };
    } finally { clearTimeout(timer); }
  })()`;
}

export function formatItems(data, count) {
  const root = data?.result || data?.data?.data || data?.data || data;
  const items = root?.articles || root?.content_elements || root?.list;
  if (!Array.isArray(items)) throw new CommandExecutionError('Unexpected Reuters list response structure');
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
  return items.slice(0, count).map((article, index) => {
    if (!article || typeof article !== 'object') return null;
    const title = String(article.title || article.headlines?.basic || article.basic_headline || '').trim();
    const rawUrl = article.canonical_url || article.url;
    if (!title || !rawUrl) return null;
    let url;
    try { url = new URL(String(rawUrl), REUTERS_HOME); } catch { return null; }
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    const rawDate = article.display_date || article.published_time || article.first_publish_date || article.updated_date || '';
    const date = new Date(rawDate);
    const time = !rawDate ? '' : Number.isNaN(date.getTime()) ? String(rawDate) : formatter.format(date).replace(/\//g, '-');
    return { rank: index + 1, title, time, url: url.href };
  }).filter(Boolean);
}

export function retryAfterMs(value, now) {
  if (value === undefined || value === null || String(value).trim() === '') return 0;
  const text = String(value).trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text) * 1000;
  const date = Date.parse(text);
  return Number.isNaN(date) ? 0 : Math.max(0, date - now);
}

function transient(error) {
  const code = String(error?.code || '').toUpperCase();
  if (['AUTH_REQUIRED', 'ARGUMENT', 'CONFIG', 'SESSION_BUSY'].includes(code)) return false;
  return ['BROWSER_CONNECT', 'TIMEOUT', 'NETWORK_ERROR', 'ECONNRESET', 'ETIMEDOUT', 'TARGET_NAVIGATED', 'ATTACH_FAILED'].includes(code)
    || /navigation rejected|inspected target navigated|execution context (?:was )?destroyed|cannot find (?:default )?execution context|browser connection dropped|connection (?:reset|closed)|extension (?:disconnected|not connected)|attach failed|debugger is not attached|timed? out/i.test(String(error?.message || error));
}

function browserFailure(error) {
  if (typeof error?.exitCode === 'number') return error;
  const wrapped = new CommandExecutionError(`Reuters list browser error: ${error?.message || error}`);
  wrapped.cause = error;
  return wrapped;
}

function accessError(state) {
  const message = {
    human_verification: 'Reuters requires interactive human verification',
    access_required: 'Reuters requires login or subscription access',
    blocked: 'Reuters has blocked access to this page',
  }[state];
  return message ? new AuthRequiredError('www.reuters.com', message,
    'Open Reuters in your browser and resolve the displayed access requirement before retrying.') : null;
}

export async function readSectionNews(page, alias, count) {
  const deadline = Date.now() + TOTAL_BUDGET_MS;
  let lastState = 'navigation';
  let requests = 0;
  const timeout = () => new TimeoutError(`Reuters list (state=${lastState}, requests=${requests})`, TOTAL_BUDGET_MS / 1000,
    'The total time budget expired. Retry later.');
  const remaining = () => {
    const ms = deadline - Date.now();
    if (ms <= 0) throw timeout();
    return ms;
  };
  const bounded = async operation => {
    const ms = remaining();
    let timer;
    try {
      const result = await Promise.race([
        Promise.resolve().then(operation),
        new Promise((_, reject) => { timer = setTimeout(() => reject(timeout()), ms); }),
      ]);
      remaining();
      return result;
    } finally { clearTimeout(timer); }
  };
  const pause = async () => {
    await new Promise(resolve => setTimeout(resolve, Math.min(POLL_MS, remaining())));
    remaining();
  };

  for (let attempt = 1; attempt <= 2; attempt++) {
    try { await bounded(() => page.goto(REUTERS_HOME)); break; }
    catch (error) {
      remaining();
      if (!transient(error) || attempt === 2) throw browserFailure(error);
      await pause();
    }
  }

  let stateFailures = 0;
  const readState = async () => {
    while (true) {
      let state;
      try { state = await bounded(() => page.evaluate(`(${inspectListPage.toString()})()`)); }
      catch (error) {
        remaining();
        if (!transient(error) || ++stateFailures >= 3) throw browserFailure(error);
        await pause();
        continue;
      }
      if (!['ready', 'loading', 'automatic_verification', 'human_verification', 'access_required', 'blocked'].includes(state?.kind)) {
        throw new CommandExecutionError('Reuters list returned an invalid page state');
      }
      lastState = state.kind;
      const denied = accessError(lastState);
      if (denied) throw denied;
      return state;
    }
  };

  let nextRequestAt = 0;
  while (requests < MAX_REQUESTS) {
    const state = await readState();
    if (state.kind !== 'ready' || Date.now() < nextRequestAt) {
      await pause();
      continue;
    }
    const requestTimeout = Math.min(REQUEST_TIMEOUT_MS, remaining());
    requests++;
    let result, failure;
    try {
      // evaluateOnce avoids the core's automatic replay after a target navigation.
      // Every API attempt is counted here and spaced by our backoff policy.
      result = await bounded(() => page.evaluateOnce(buildFetchScript(alias, count, requestTimeout)));
    } catch (error) {
      remaining();
      if (!transient(error)) throw browserFailure(error);
      failure = browserFailure(error);
    }
    // A 403 can create a challenge AFTER the request starts. Never reuse the
    // state sampled before fetch; also check for challenges on 200 responses.
    const after = await readState();
    let retryable = Boolean(failure);
    if (!failure) {
      if (!result || typeof result.ok !== 'boolean' || !Number.isInteger(result.status)) {
        throw new CommandExecutionError('Reuters list returned an invalid fetch result');
      }
      if (result.ok) {
        const items = formatItems(result.data, count);
        if (items.length) return items;
        failure = new EmptyResultError('ReutersBrowser news', `No usable articles for ${alias}`);
        retryable = true;
      } else if (result.status === 401 || result.status === 403) {
        failure = new AuthRequiredError('www.reuters.com', `Reuters list HTTP ${result.status} for ${alias} after ${requests} request(s)`);
        retryable = true;
      } else if (result.timeout) {
        failure = new TimeoutError('Reuters list API request', requestTimeout / 1000);
        retryable = true;
      } else if ([0, 408, 429, 500, 502, 503, 504].includes(result.status)) {
        failure = new CliError('NETWORK_ERROR', `Reuters list ${result.status ? 'HTTP ' + result.status : 'network failure'} for ${alias}`, 'Retry later.', 75);
        retryable = true;
      } else if (result.parseError) {
        failure = new CommandExecutionError(`Reuters list returned non-JSON content for ${alias}`);
        retryable = after.kind === 'automatic_verification'
          || /captcha-delivery\.com|datadome|device check|verify you are human/i.test(result.textPreview || '');
      } else if (result.status === 404) {
        failure = new EmptyResultError('ReutersBrowser news', `Reuters collection not found: ${alias}`);
      } else {
        failure = new CommandExecutionError(`Reuters list HTTP ${result.status} for ${alias}`);
      }
    }
    if (!retryable || requests >= MAX_REQUESTS) throw failure;
    nextRequestAt = Date.now() + Math.max(RETRY_DELAYS_MS[requests - 1], retryAfterMs(result?.retryAfter, Date.now()));
  }
}
