import { ArgumentError, AuthRequiredError, CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { createArticleBudget, navigateToArticle, isTransientBrowserError, browserFailure } from './article-recovery.js';

const SITE = 'Ars Technica';
const DOMAIN_RE = /^https?:\/\/(?:www\.)?arstechnica\.com\//i;
const CHALLENGE_RE = /verify (?:that )?you are human|captcha|access (?:to this page has been )?denied|unusual traffic|unusual activity|subscribe to continue|subscription required|sign in to continue|log in to continue|press (?:and|&) hold/i;

function normalizeText(value) {
  return String(value ?? '')
    .replace(/[\u200B-\u200D\uFEFF]/g, '')
    .replace(/,\s*opens new tab\.?/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function normalizeContentText(value) {
  return dedupeLines(
    String(value ?? '')
      .replace(/[\u200B-\u200D\uFEFF]/g, '')
      .replace(/\r\n?/g, '\n')
      .split(/\n+/)
      .map((line) => normalizeText(line))
      .filter(Boolean),
  ).join('\n\n');
}

function dedupeLines(lines) {
  const output = [];
  for (const line of lines) {
    if (!line) continue;
    if (output[output.length - 1] === line) continue;
    output.push(line);
  }
  return output;
}

function mapDetail(article, bodyText, fallbackUrl = null) {
  if (!article && !bodyText) return null;
  const title = normalizeText(article?.title);
  const publishedAt = normalizeText(article?.published_at);
  const author = normalizeText(article?.author);
  const canonicalUrl = normalizeText(article?.canonical_url) || normalizeText(fallbackUrl);
  const body = normalizeContentText(bodyText);

  return {
    title: title || null,
    published_at: publishedAt || null,
    authors: author || null,
    url: canonicalUrl || null,
    content: body || null,
    content_length: body ? body.length : 0,
  };
}

function buildArticleDetailScript() {
  return `
    (() => {
      try {
        const normalize = (value) => String(value ?? '')
          .replace(/[\\u200B-\\u200D\\uFEFF]/g, '')
          .replace(/,\\s*opens new tab\\.?/gi, '')
          .replace(/\\s{2,}/g, ' ')
          .trim();

        const dedupeLines = (lines) => {
          const output = [];
          for (const line of lines) {
            if (!line) continue;
            if (output[output.length - 1] === line) continue;
            output.push(line);
          }
          return output;
        };

        const title =
          normalize(document.querySelector('main#main article h1')?.textContent) ||
          normalize(document.querySelector('article h1')?.textContent) ||
          normalize(document.querySelector('h1')?.textContent) ||
          normalize(document.querySelector('meta[property="og:title"]')?.content) ||
          normalize(document.title);

        const canonicalUrl =
          normalize(document.querySelector('link[rel="canonical"]')?.href) ||
          normalize(document.querySelector('meta[property="og:url"]')?.content);

        const publishedAt =
          normalize(document.querySelector('main#main article time[title]')?.getAttribute('title')) ||
          normalize(document.querySelector('article time[title]')?.getAttribute('title')) ||
          normalize(document.querySelector('article time[datetime]')?.getAttribute('datetime')) ||
          normalize(document.querySelector('meta[property="article:published_time"]')?.content) ||
          normalize(document.querySelector('meta[name="article:published_time"]')?.content) ||
          normalize(document.querySelector('article time')?.textContent);

        const authorNodes = Array.from(document.querySelectorAll(
          'article a[rel="author"], article [rel="author"], article [class*="byline"], article a[href*="/author/"]'
        ))
          .map((node) => normalize(node.textContent))
          .map((text) => text.replace(/^By\\s+/i, '').trim())
          .filter(Boolean);
        const authors = Array.from(new Set(authorNodes)).join(', ');

        const container =
          document.querySelector('main#main article') ||
          document.querySelector('article') ||
          document.querySelector('main#main');

        const contentSelector = [
          'main#main article .post-content p',
          'main#main article .post-content h2',
          'main#main article .post-content h3',
          'main#main article .post-content h4',
          'main#main article .post-content blockquote',
          'article .post-content p',
          'article .post-content h2',
          'article .post-content h3',
          'article .post-content h4',
          'article .post-content blockquote'
        ].join(', ');
        const blockNodes = Array.from(document.querySelectorAll(contentSelector));
        const noiseAncestorSelector = [
          '.ars-interlude-container',
          '.ad-wrapper',
          '.ad',
          '.advertisement',
          'aside',
          'figure',
          'figcaption',
          'nav',
          'header',
          'footer',
          '[class*="caption"]',
          '[class*="comment"]',
          '[class*="newsletter"]',
          '[class*="popular"]',
          '[class*="related"]',
          '[class*="share"]',
          '[class*="social"]',
          '[class*="teaser"]',
          '[class*="video"]'
        ].join(', ');

        const isNoise = (text) => {
          if (!text) return true;
          return /^(Advertisement|Ars Video\\b.*|Sign up here|Subscribe|Read this next|Related|Most Popular|Latest Stories|Comments|Reader comments|Image Credits?)$/i.test(text)
            || /cookie policy|privacy policy|terms of use/i.test(text)
            || /newsletter|most popular|related stories|reader comments/i.test(text)
            || /^follow us on/i.test(text);
        };

        const bodyLines = [];
        for (const node of blockNodes) {
          if (node.closest(noiseAncestorSelector)) continue;
          const text = normalize(node.textContent);
          if (isNoise(text)) continue;
          if (/^Our standards:/i.test(text) || /^Purchase Licensing Rights/i.test(text)) break;
          bodyLines.push(text);
        }

        const bodyText = dedupeLines(bodyLines).join('\\n\\n');
        const pageText = normalize(document.body?.innerText || '');
        const h1Text = normalize(document.querySelector('main#main article h1')?.textContent);
        const paragraphCount = document.querySelectorAll('main#main article .post-content p, article .post-content p').length;
        const challengeText = ${CHALLENGE_RE.toString()}.test([
          location.href || '',
          document.title || '',
          pageText.slice(0, 5000),
        ].join('\\n'));
        const usableBody = Boolean(title && bodyText.length >= 200);
        const visible = (node) => {
          const style = getComputedStyle(node);
          return node.getClientRects().length > 0 && style.display !== 'none'
            && style.visibility !== 'hidden' && style.visibility !== 'collapse'
            && style.opacity !== '0';
        };
        // Visible widgets/dialogs are gates even when an article teaser exists.
        // Script tags and ordinary article text about CAPTCHA are not gates.
        const visibleVerification = Array.from(document.querySelectorAll(
          'iframe, [role="dialog"], #challenge-form, #px-captcha, .g-recaptcha, .h-captcha'
        )).some((node) => {
          if (!visible(node)) return false;
          if (node.closest('.post-content') && !node.closest('[role="dialog"]')) return false;
          if (node.tagName === 'IFRAME') {
            return /captcha-delivery\\.com|challenges\\.cloudflare\\.com|(?:recaptcha|hcaptcha)|verify (?:that )?you are human/i.test(
              [node.getAttribute('src'), node.getAttribute('title')].join(' ')
            );
          }
          return node.matches('#challenge-form, #px-captcha, .g-recaptcha, .h-captcha')
            || ${CHALLENGE_RE.toString()}.test(normalize(node.innerText || node.textContent));
        });
        const authRequired = visibleVerification || (!usableBody && challengeText);
        const notFound = !usableBody && /^(?:404(?:\\s|$)|page not found|not found)/i.test(title);

        return {
          ok: true,
          authRequired,
          notFound,
          diagnostics: {
            url: location.href || '',
            title: document.title || '',
            h1Text,
            paragraphCount,
            bodyLength: bodyText.length,
            challengeText,
          },
          body: {
            article: {
              title: title || null,
              published_at: publishedAt || null,
              author: authors || null,
              canonical_url: canonicalUrl || null,
            },
            bodyText,
          },
        };
      } catch (error) {
        return { ok: false, error: String((error && error.message) || error) };
      }
    })()
  `;
}

async function readArticlePayload(page) {
  const result = await page.evaluateOnce(buildArticleDetailScript());

  if (result?.error) {
    throw new CommandExecutionError(`ArsPublic article failed inside the page: ${result.error}`);
  }
  if (!result || result.ok !== true) {
    throw new CommandExecutionError('ArsPublic article returned no payload');
  }
  return result;
}

async function waitForArticlePayload(page, url, budget = createArticleBudget()) {
  let evaluationFailures = 0;
  while (true) {
    let result;
    try {
      result = await budget.bounded(() => readArticlePayload(page));
    } catch (error) {
      budget.remaining();
      evaluationFailures += 1;
      if (!isTransientBrowserError(error) || evaluationFailures >= 3) throw browserFailure(error);
      await budget.pause(1000);
      continue;
    }
    if (result.authRequired) {
      throw new AuthRequiredError('arstechnica.com', 'Ars Technica requires human verification or access permission',
        'Open the article in the browser used by opencli, resolve the displayed requirement, then retry.');
    }
    if (result.notFound) throw new EmptyResultError('ArsPublic article', 'Article page was not found');
    const detail = mapDetail(result.body?.article, result.body?.bodyText, url);
    if (detail?.title && detail?.content_length >= 200) return { result, detail };
    // Only wait for ordinary DOM loading. Polling never reloads the page.
    await budget.pause(1000);
  }
}

async function readArticle(page, url) {
  const budget = createArticleBudget();
  await navigateToArticle(page, url, budget);
  return waitForArticlePayload(page, url, budget);
}

cli({
  site: 'ArsPublic',
  name: 'article',
  description: 'Ars Technica article detail by URL (title, author, published_at, content)',
  access: 'read',
  domain: 'arstechnica.com',
  strategy: Strategy.UI,
  browser: true,
  navigateBefore: false,
  args: [
    { name: 'url', positional: true, required: true, help: 'Ars Technica article URL' },
  ],
  columns: ['source', 'title', 'url', 'published_at', 'author', 'content', 'content_length', 'status'],
  func: async (page, kwargs) => {
    const url = String(kwargs.url || '').trim();
    if (!url) throw new ArgumentError('URL cannot be empty');
    if (!DOMAIN_RE.test(url)) throw new ArgumentError('Only arstechnica.com article URL is supported');

    const { detail } = await readArticle(page, url);

    return [{
      source: SITE,
      title: detail.title,
      url: detail.url,
      published_at: detail.published_at,
      author: detail.authors,
      content: detail.content,
      content_length: detail.content_length,
      status: 'success',
    }];
  },
});

export { buildArticleDetailScript, mapDetail, waitForArticlePayload, readArticle };
