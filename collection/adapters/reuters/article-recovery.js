import { AuthRequiredError, CommandExecutionError, EmptyResultError, TimeoutError } from '@jackwener/opencli/errors';
import { buildArticleDetailScript, mapArticleDetail } from './utils.js';

// Includes navigation and every bridge call; leaves headroom in NewsReader's 45s timeout.
export const ARTICLE_BUDGET_MS = 30_000;
export const POLL_INTERVAL_MS = 1_000;
export const MAX_NAVIGATION_ATTEMPTS = 2;
export const MAX_EVALUATION_FAILURES = 3;

function isTransientBrowserError(error) {
    const code = String(error?.code || '').toUpperCase();
    if (['AUTH_REQUIRED', 'ARGUMENT', 'CONFIG', 'SESSION_BUSY'].includes(code)) return false;
    if (['BROWSER_CONNECT', 'TIMEOUT', 'NETWORK_ERROR', 'ECONNRESET', 'ETIMEDOUT',
        'TARGET_NAVIGATED', 'ATTACH_FAILED'].includes(code)) return true;
    return /navigation rejected|inspected target navigated|execution context (?:was )?destroyed|cannot find (?:default )?execution context|browser connection dropped|connection (?:reset|closed)|extension (?:disconnected|not connected)|attach failed|debugger is not attached|net::ERR_(?:CONNECTION_RESET|CONNECTION_CLOSED|TIMED_OUT)|timed? out/i.test(String(error?.message || error));
}

function browserFailure(error) {
    if (typeof error?.exitCode === 'number') return error;
    const wrapped = new CommandExecutionError(`Reuters article-detail browser error: ${error?.message || error}`);
    wrapped.cause = error;
    return wrapped;
}

export async function readArticleDetail(page, url) {
    const deadline = Date.now() + ARTICLE_BUDGET_MS;
    let lastState = 'navigation';
    const timeout = () => new TimeoutError(
        `Reuters article-detail (state=${lastState})`, ARTICLE_BUDGET_MS / 1000,
        'The page did not become ready within the total time budget. Retry later.',
    );
    const remaining = () => {
        const ms = deadline - Date.now();
        if (ms <= 0) throw timeout();
        return ms;
    };
    // A stalled bridge call must also respect the total budget. Do not start any
    // more operations after this expires; the bridge may still be finishing that call.
    const bounded = async (operation) => {
        const ms = remaining();
        let timer;
        try {
            const result = await Promise.race([
                Promise.resolve().then(operation),
                new Promise((_, reject) => { timer = setTimeout(() => reject(timeout()), ms); }),
            ]);
            remaining();
            return result;
        } finally {
            clearTimeout(timer);
        }
    };
    const pause = async () => {
        await new Promise(resolve => setTimeout(resolve, Math.min(POLL_INTERVAL_MS, remaining())));
        remaining();
    };

    for (let attempt = 1; attempt <= MAX_NAVIGATION_ATTEMPTS; attempt++) {
        try {
            await bounded(() => page.goto(url));
            break;
        } catch (error) {
            remaining();
            if (!isTransientBrowserError(error) || attempt === MAX_NAVIGATION_ATTEMPTS) {
                throw browserFailure(error);
            }
            await pause();
        }
    }

    // Poll in Node, issuing short, read-only evaluations. A redirect can destroy
    // an in-page polling loop; the next evaluation here attaches to the new document.
    const script = buildArticleDetailScript();
    let evaluationFailures = 0;
    lastState = 'loading';
    while (true) {
        let result;
        try {
            result = await bounded(() => page.evaluate(script));
        } catch (error) {
            remaining();
            evaluationFailures++;
            if (!isTransientBrowserError(error) || evaluationFailures >= MAX_EVALUATION_FAILURES) {
                throw browserFailure(error);
            }
            await pause();
            continue;
        }
        if (!result || result.ok !== true || result.error || !result.state?.kind) {
            throw new CommandExecutionError(`Reuters article-detail returned an invalid page result${result?.error ? ': ' + result.error : ''}`);
        }
        lastState = result.state.kind;
        if (['human_verification', 'access_required', 'blocked'].includes(lastState)) {
            const messages = {
                human_verification: 'Reuters requires interactive human verification',
                access_required: 'Reuters requires login or subscription access',
                blocked: 'Reuters has blocked access to this page',
            };
            throw new AuthRequiredError('www.reuters.com', messages[lastState],
                'Open the article in your browser and resolve the displayed access requirement before retrying.');
        }
        if (lastState === 'not_found') {
            throw new EmptyResultError('reuters article-detail', 'Reuters reports that this page was not found.');
        }
        if (lastState === 'ready') {
            const detail = mapArticleDetail(result.body?.article, result.body?.bodyText, url);
            if (!detail?.body || detail.body.length < 200) {
                throw new CommandExecutionError('Reuters reported readiness without a usable article body');
            }
            return detail;
        }
        if (!['loading', 'automatic_verification'].includes(lastState)) {
            throw new CommandExecutionError(`Reuters article-detail returned an unknown page state: ${lastState}`);
        }
        // Waiting for readiness never reloads the page or makes extra API requests.
        await pause();
    }
}
