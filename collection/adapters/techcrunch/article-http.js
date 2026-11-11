// Keep internal recovery small: NewsReader already retries whole commands.
export const TOTAL_BUDGET_MS = 30_000;
export const REQUEST_TIMEOUT_MS = 8_000;
export const RETRY_DELAY_MS = 5_000;
const RETRY_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
const USER_AGENT = 'Mozilla/5.0 (compatible; opencli-custom)';

function failure(code, message) { return Object.assign(new Error(message), { code }); }

export function parseRetryAfter(value, now = Date.now()) {
  if (!value?.trim()) return null;
  const text = value.trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) return Math.ceil(Number(text) * 1000);
  const date = Date.parse(text);
  return Number.isFinite(date) ? Math.max(0, date - now) : null;
}

export function isVerificationHtml(html) {
  const title = (html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] || '').replace(/<[^>]+>/g, ' ');
  const hasArticle = /<div\b[^>]*class=["'][^"']*\bentry-content\b/i.test(html);
  const text = html.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').slice(0, 10000);
  // CAPTCHA library scripts alone are common on normal pages. Only consider
  // challenge markup when the expected article container is absent.
  return !hasArticle && (
    /verify (?:that )?you are human|human verification|press (?:and|&) hold|unusual (?:traffic|activity)|access denied|just a moment|checking your browser|captcha/i.test(title + ' ' + text)
    || /<(?:iframe|form)\b[^>]*(?:captcha-delivery\.com|challenges\.cloudflare\.com|recaptcha|hcaptcha|challenge-form)/i.test(html)
  );
}

function cooldownFailure(status, until) {
  // Deliberately use a terminal code/message for the current NewsReader queue.
  // Returning "HTTP 429/503" would schedule a retry after only 5s and violate
  // Retry-After. The user can retry after this explicit server deadline.
  return failure('RETRY_AFTER', `Server requested a cooldown until ${new Date(until).toISOString()} (status=${status}). Retry manually after that time.`);
}

async function request(url, timeoutMs) {
  const controller = new AbortController();
  let timer;
  let status = null;
  let retryUntil = null;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(url, { headers: { 'User-Agent': USER_AGENT }, signal: controller.signal });
        status = response.status;
        const retryAfter = parseRetryAfter(response.headers.get('retry-after'));
        retryUntil = retryAfter === null ? null : Date.now() + retryAfter;
        if ([401, 403, 404].includes(status)) {
          // No need to download an error page to classify these terminal codes.
          response.body?.cancel().catch(() => {});
          return { status, retryUntil, html: '' };
        }
        const html = await response.text();
        return { status, retryUntil, html };
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(failure('TIMEOUT', 'TechCrunch request or response body timed out'));
        }, timeoutMs);
      }),
    ]);
  } catch (error) {
    const normalized = controller.signal.aborted
      ? failure('TIMEOUT', 'TechCrunch request or response body timed out')
      : failure('NETWORK_ERROR', `TechCrunch request or response body failed: ${error?.message || error}`);
    throw Object.assign(normalized, { status, retryUntil });
  } finally { clearTimeout(timer); }
}

export async function fetchArticleHtml(url) {
  const deadline = Date.now() + TOTAL_BUDGET_MS;
  for (let attempt = 0; attempt < 2; attempt++) {
    let error;
    let retryUntil = null;
    let status = null;
    try {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw failure('TIMEOUT', 'TechCrunch total request budget expired');
      const result = await request(url, Math.min(REQUEST_TIMEOUT_MS, remaining));
      ({ status, retryUntil } = result);
      if (status === 401 || status === 403) throw failure('AUTH_REQUIRED', `TechCrunch access denied (status=${status}). Resolve access before retrying.`);
      if (status === 404) throw failure('NOT_FOUND', 'Article page returned 404');
      if (isVerificationHtml(result.html)) throw failure('AUTH_REQUIRED', 'TechCrunch requires human verification. Automatic HTTP retries cannot complete it.');
      if (status >= 200 && status < 300) return result.html;
      if (!RETRY_STATUSES.has(status)) throw failure('HTTP_ERROR', `HTTP ${status}`);
      error = failure(status === 408 ? 'NETWORK_ERROR' : 'HTTP_ERROR', `HTTP ${status}`);
    } catch (caught) {
      if (!['NETWORK_ERROR', 'TIMEOUT'].includes(caught.code)) throw caught;
      status = caught.status ?? status;
      retryUntil = caught.retryUntil ?? retryUntil;
      error = caught;
    }
    if (attempt === 1) {
      if (retryUntil !== null && retryUntil > Date.now()) throw cooldownFailure(status, retryUntil);
      throw error;
    }
    const delay = Math.max(RETRY_DELAY_MS, (retryUntil ?? 0) - Date.now());
    // Reserve one complete request slot. Do not knowingly start a request
    // that cannot fit, or let the outer 5s retry disregard a long cooldown.
    if (Date.now() + delay + REQUEST_TIMEOUT_MS > deadline) {
      if (retryUntil !== null) throw cooldownFailure(status, retryUntil);
      throw error;
    }
    await new Promise(resolve => setTimeout(resolve, delay));
  }
}
