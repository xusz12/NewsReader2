import { CliError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';

// NewsReader allows 45s per invocation and retries tasks after 5s / 15s.
// Leave time for browser setup/cleanup; permit only one additional navigation.
export const ARTICLE_BUDGET_MS = 30_000;
export const NAVIGATION_RETRY_MS = 5_000;

export function isTransientBrowserError(error) {
  const code = String(error?.code || '').toUpperCase();
  if (['AUTH_REQUIRED', 'ARGUMENT', 'CONFIG', 'SESSION_BUSY'].includes(code)) return false;
  return ['BROWSER_CONNECT', 'TIMEOUT', 'NETWORK_ERROR', 'ECONNRESET', 'ETIMEDOUT',
    'TARGET_NAVIGATED', 'ATTACH_FAILED'].includes(code)
    || /navigation rejected|inspected target navigated|execution context (?:was )?destroyed|cannot find (?:default )?execution context|browser connection dropped|connection (?:reset|closed)|extension (?:disconnected|not connected)|attach failed|debugger is not attached|net::ERR_(?:CONNECTION_RESET|CONNECTION_CLOSED|TIMED_OUT)|timed? out/i.test(String(error?.message || error));
}

export function browserFailure(error) {
  if (isTransientBrowserError(error)) {
    // Normalize transient DOM failures too: NewsReader recognizes this code.
    return new CliError('BROWSER_CONNECT', `Ars article browser temporarily unavailable: ${error?.message || error}`,
      'Retry later using the existing task retry schedule.');
  }
  return typeof error?.exitCode === 'number' ? error
    : new CommandExecutionError(`Ars article browser error: ${error?.message || error}`);
}

export function createArticleBudget() {
  const deadline = Date.now() + ARTICLE_BUDGET_MS;
  const timeout = () => new TimeoutError('Ars article navigation and DOM loading', ARTICLE_BUDGET_MS / 1000,
    'The article did not become ready within the total budget. Retry later.');
  const remaining = () => {
    const ms = deadline - Date.now();
    if (ms <= 0) throw timeout();
    return ms;
  };
  return {
    remaining,
    async bounded(operation) {
      const ms = remaining();
      let timer;
      try {
        const result = await Promise.race([
          Promise.resolve().then(operation),
          new Promise((_, reject) => {
            timer = setTimeout(() => reject(timeout()), ms);
          }),
        ]);
        remaining();
        return result;
      } finally { clearTimeout(timer); }
    },
    async pause(ms) {
      await new Promise(resolve => setTimeout(resolve, Math.min(ms, remaining())));
      remaining();
    },
  };
}

export async function navigateToArticle(page, url, budget) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await budget.bounded(() => page.goto(url));
      return;
    } catch (error) {
      budget.remaining();
      if (!isTransientBrowserError(error) || attempt === 1) throw browserFailure(error);
      await budget.pause(NAVIGATION_RETRY_MS);
    }
  }
}
