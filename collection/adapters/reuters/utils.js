/**
 * Shared helpers for the reuters adapter.
 */
import { ArgumentError } from '@jackwener/opencli/errors';

const MIN_LIMIT = 1;
const MAX_LIMIT = 40;

export function parseLimit(raw, fallback = 10) {
    if (raw === undefined || raw === null || raw === '') return fallback;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
        throw new ArgumentError(`--limit must be an integer between ${MIN_LIMIT} and ${MAX_LIMIT}, got ${JSON.stringify(raw)}`);
    }
    if (parsed < MIN_LIMIT || parsed > MAX_LIMIT) {
        throw new ArgumentError(`--limit must be between ${MIN_LIMIT} and ${MAX_LIMIT}, got ${parsed}`);
    }
    return parsed;
}

/**
 * Build the in-page IIFE that fetches the Reuters search API.
 *
 * Returns a raw envelope `{ ok, status, body, error? }` so that error-handling
 * lives in node space (not silently swallowed by `catch(e) {}` inside the
 * browser).
 */
export function buildSearchScript(query, count) {
    return `
    (async () => {
      const apiQuery = JSON.stringify({
        keyword: ${JSON.stringify(query)},
        offset: 0,
        orderby: 'display_date:desc',
        size: ${count},
        website: 'reuters'
      });
      const apiUrl = 'https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2?query=' + encodeURIComponent(apiQuery);
      try {
        const resp = await fetch(apiUrl, { credentials: 'include' });
        const status = resp.status;
        const statusText = resp.statusText || '';
        const text = await resp.text();
        let body = null;
        let parseError = null;
        if ((text || '').trim()) {
          try { body = JSON.parse(text); } catch (e) { parseError = String((e && e.message) || e); }
        }
        return { ok: resp.ok, status, statusText, body, parseError, textPreview: (text || '').slice(0, 500) };
      } catch (e) {
        return { ok: false, status: 0, body: null, error: String((e && e.message) || e) };
      }
    })()
  `;
}

/** Serialized into the page; keep this helper self-contained. */
export function cleanReutersParagraph(value, nextValue = '') {
    const normalize = text => String(text || '')
        .replace(/[\u200B-\u200D\u2060\uFEFF]/g, '')
        .replace(/,\s*opens new tab\.?/gi, '')
        .replace(/\s+/g, ' ')
        .trim();
    const text = normalize(value);
    // Match promotional sentence templates AND their signup CTA, never just
    // "newsletter" or a newsletter name. U.S. is an abbreviation, not a
    // sentence boundary; other full stops must not swallow intervening news.
    const descriptions = [
        /(?:Get (?:a daily digest of|a look at|the latest news from|weekly news and analysis on)|Jumpstart your morning with|Keep up with|Learn about|Make sense of|Stay up to date (?:on|with))\b(?:U\.S\.|[^.!?])*?\bnewsletter\./,
        /The Misinformation Monitor newsletter rounds up international misinformation narratives, with a ["“]Real or Fake\?["”] quiz in every edition\./,
        /The Reuters (?:U\.S\.|[^.!?])*?\bnewsletter (?:by columnist [^.!?]*? )?(?:is your essential guide|provides everything you need to know|keeps you informed)[^.!?]*\./,
        /The Reuters Daily Briefing newsletter provides all the news you need to start your day\./,
        /The Week in Breakingviews newsletter offers insights and ideas from Reuters['’] global financial commentary team\./,
        /Want to receive (?:U\.S\.|[^.!?])*?\bin your inbox[^.!?]*\?/,
    ];
    const signup = /Sign up (?:here|for (?:the|our|my) newsletter here)\b\.?\\?/;
    const nextIsSignup = new RegExp(`^${signup.source}$`, 'i').test(normalize(nextValue));
    // A separate signup paragraph may complete the promotional sentence only
    // when that sentence is at the end of this paragraph.
    const requiredSignup = nextIsSignup ? `(?:\\s*${signup.source}|$)` : `\\s*${signup.source}`;
    const followLinks = /(?:\s+You can find ROI on the Reuters website, and you can follow us on LinkedIn and X\.?)?/;
    const promotion = new RegExp(`(?:${descriptions.map(pattern => pattern.source).join('|')})${requiredSignup}${followLinks.source}`, 'gi');
    return normalize(text.replace(promotion, (match, offset, original) => {
        // Preserve attributed or quoted pitches. Only standalone promotional
        // sentences at a paragraph/sentence boundary are removed.
        const prefix = original.slice(0, offset);
        if (offset !== 0 && !/[.!?]\s+$/.test(prefix)) return match;
        let closingQuote = '';
        for (let i = 0; i < prefix.length; i++) {
            const char = prefix[i];
            if (prefix[i - 1] === '\\') continue;
            if (closingQuote) {
                // Apostrophes within words do not close a single-quoted passage.
                const apostrophe = /['’]/.test(char) && /\p{L}/u.test(prefix[i - 1] || '') && /\p{L}/u.test(prefix[i + 1] || '');
                if (char === closingQuote && !apostrophe) closingQuote = '';
            } else if (char === '"' || char === '“' || char === '‘') {
                closingQuote = ({ '"': '"', '“': '”', '‘': '’' })[char];
            } else if (char === "'" && (i === 0 || /[\s([{—-]/.test(prefix[i - 1]))) {
                closingQuote = "'";
            }
        }
        if (closingQuote) return match;
        return '';
    }));
}

/**
 * In-page IIFE that walks the Reuters article page DOM and surfaces
 * canonical metadata + body text. Returns a raw `{ ok, body, error? }`
 * envelope so node-side handling can throw typed errors.
 */
export function buildArticleDetailScript() {
    return `
    (() => {
      try {
        const meta = document.getElementById('fusion-metadata');
        let fusion = null;
        if (meta) {
          try { fusion = JSON.parse(meta.textContent || '{}'); } catch (e) { fusion = null; }
        }
        const article = fusion?.globalContent || null;
        const domArticle = article || {};
        if (!domArticle.title) {
          const h1 = document.querySelector('article h1[data-testid="Heading"]');
          if (h1) domArticle.title = (h1.textContent || '').trim();
        }
        if (!domArticle.authors) {
          const byline = document.querySelector('article [data-testid="AuthorName"]');
          if (byline) domArticle.authors = [(byline.textContent || '').replace(/^By\\s+/i, '').trim()].filter(Boolean);
        }
        if (!domArticle.display_date) {
          const timeEl = document.querySelector('article time[data-testid="DateLine"]');
          if (timeEl) {
            const dt = timeEl.getAttribute('datetime') || timeEl.textContent || '';
            domArticle.display_date = dt.trim();
          }
        }
        const cleanParagraph = (${cleanReutersParagraph.toString()});
        const isNoiseParagraph = (text) => {
          if (!text) return true;
          return /^sign up (?:here|for (?:the|our|my) newsletter here)[.\\\\]*$/i.test(text)
            || /^subscribe(?: now| today| to (?:continue reading|read more|our newsletter))?[.!]*$/i.test(text)
            || /^advertisement(?:\\s*[-–—]\\s*scroll to continue)?[.!]*$/i.test(text)
            || /^recommended(?: for you)?[.!]*$/i.test(text);
        };
        const isHardStopParagraph = (text) => {
          if (!text) return false;
          return /^our standards:/i.test(text)
            || /^purchase licensing rights/i.test(text)
            || /^read next\\b/i.test(text)
            || /^thomson reuters\\b/i.test(text);
        };
        const isAuthorBio = (text) => {
          if (!text) return false;
          return /\\bis a journalist\\b/i.test(text)
            || /^our standards:/i.test(text);
        };
        // A primary video can insert its notice, speakers and transcript inside
        // <article>. Those paragraphs are separate from the reported story.
        // Filter by their DOM container, never by words in legitimate reporting.
        const isVideoTranscript = (node) => {
          for (let current = node; current; current = current.parentElement) {
            const testId = current.getAttribute?.('data-testid') || '';
            const classes = current.getAttribute?.('class') || '';
            if (/^VideoTranscript(?:Scroll)?$/.test(testId) || classes.includes('video-transcript-module__')) return true;
          }
          return false;
        };
        // Reuters detail/factbox stories also use ArticleList > ul/ol > li >
        // div[Body]. Select that explicit body block, never every article li
        // (which would include navigation, sharing and text-size controls).
        const readListBlocks = (root) => {
          const blocks = [];
          let pending = '';
          const flush = () => {
            if (pending.trim()) blocks.push(pending);
            pending = '';
          };
          const visit = (node) => {
            if (node.nodeType === 3) {
              pending += node.textContent || '';
              return;
            }
            if (node.nodeType !== 1) return;
            if (isVideoTranscript(node)) return;
            if (/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|NAV|ASIDE|FIGURE|FIGCAPTION|BUTTON|SVG)$/.test(node.tagName)) return;
            if (node.tagName === 'BR') { pending += ' '; return; }
            const boundary = /^(UL|OL|LI|P|DIV|BLOCKQUOTE|H[1-6])$/.test(node.tagName);
            if (boundary) flush();
            for (const child of node.childNodes) visit(child);
            if (boundary) flush();
          };
          visit(root);
          flush();
          return blocks;
        };
        const rawParagraphs = [];
        const bodyNodes = document.querySelectorAll('article [data-testid^="paragraph-"], article p, article [data-testid="ArticleList"]');
        for (const node of bodyNodes) {
          if (isVideoTranscript(node)) continue;
          // The list traversal owns its nested lists and paragraphs. Reading
          // those again through the selector would duplicate the same text.
          if (node.parentElement?.closest('[data-testid="ArticleList"]')) continue;
          if (node.getAttribute?.('data-testid') === 'ArticleList') {
            rawParagraphs.push(...readListBlocks(node));
          } else if ((node.textContent || '').trim()) {
            rawParagraphs.push(node.textContent);
          }
        }
        const cleanedParagraphs = [];
        for (let index = 0; index < rawParagraphs.length; index++) {
          const text = cleanParagraph(rawParagraphs[index], rawParagraphs[index + 1]);
          if (isNoiseParagraph(text)) continue;
          if (isHardStopParagraph(text)) break;
          if (/^reporting by\\b/i.test(text)) {
            cleanedParagraphs.push(text);
            break;
          }
          if (isAuthorBio(text)) break;
          cleanedParagraphs.push(text);
        }
        const bodyText = cleanedParagraphs.length ? cleanedParagraphs.join('\\n\\n') : null;
        const state = (${inspectArticlePage.toString()})(bodyText);
        const authRequired = ['human_verification', 'access_required', 'blocked'].includes(state.kind);
        return { ok: true, state, authRequired, body: { article: Object.keys(domArticle).length ? domArticle : null, bodyText } };
      } catch (e) {
        return { ok: false, error: String((e && e.message) || e) };
      }
    })()
  `;
}

/** Serialized into the page by buildArticleDetailScript; keep this self-contained. */
export function inspectArticlePage(bodyText) {
    const href = String(window.location.href || '');
    const title = String(document.title || '');
    const pageText = String(document.body?.innerText || '').slice(0, 8000);
    const hasBody = typeof bodyText === 'string' && bodyText.trim().length >= 200;
    const visible = (element) => {
        if (element.hidden || element.getAttribute('aria-hidden') === 'true') return false;
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && element.getClientRects().length > 0;
    };
    const url = new URL(href);
    const isReuters = /(^|\.)reuters\.com$/i.test(url.hostname);
    const isDataDome = (host) => /(^|\.)captcha-delivery\.com$/i.test(host);
    const visibleFrames = Array.from(document.querySelectorAll('iframe')).filter(visible);
    const frames = visibleFrames.map(frame => {
        try { return new URL(frame.getAttribute('src') || '', href); } catch { return null; }
    }).filter(frame => frame && isDataDome(frame.hostname));
    const challengeUrls = isDataDome(url.hostname) ? [...frames, url] : frames;
    const interactive = challengeUrls.some(frame => /\/captcha(?:\/|$)/i.test(frame.pathname))
        || (!hasBody && /slide (?:the |to )|drag (?:the )?(?:slider|puzzle)|press and hold|complete the captcha|check the box to (?:confirm|verify)/i.test(pageText));
    const automatic = challengeUrls.length > 0
        || visibleFrames.some(frame => /datadome|device check/i.test(frame.getAttribute('title') || ''))
        || /device check|checking your browser|just a moment/i.test(title)
        || (!hasBody && /device check|checking your browser|verifying your browser|verify you are human|human verification|datadome|captcha/i.test(title + '\n' + pageText));
    // Reuters also puts a paywall-* class on its ordinary MainContent wrapper.
    // Require an actual visible access prompt, not the article's containing layout.
    const paywall = Array.from(document.querySelectorAll('[data-testid*="paywall" i], [class*="paywall" i], [id*="paywall" i]'))
        .some(element => visible(element) && !element.querySelector('article')
            && /subscribe|subscription|sign in|log in|register|unlock/i.test(element.innerText || ''))
        || (!hasBody && /subscribe to (?:continue|read)|subscription required|sign in to (?:continue|read|access)|log in to (?:continue|read|access)/i.test(pageText));
    const blocked = !hasBody && /access (?:to this page has been )?denied|you have been blocked|unusual traffic|request (?:was )?blocked/i.test(title + '\n' + pageText);
    const notFound = !hasBody && /page (?:was )?not found|page (?:you are looking for )?(?:does not|doesn't) exist/i.test(title + '\n' + pageText);
    let kind = 'loading';
    if (interactive) kind = 'human_verification';
    else if (challengeUrls.length > 0) kind = 'automatic_verification';
    else if (blocked) kind = 'blocked';
    else if (paywall) kind = 'access_required';
    else if (automatic) kind = 'automatic_verification';
    else if (notFound) kind = 'not_found';
    else if (isReuters && hasBody) kind = 'ready';
    return { kind, readyState: document.readyState, bodyLength: (bodyText || '').length };
}

export function isAuthStatus(status) {
    return Number(status) === 401 || Number(status) === 403;
}

export function looksAuthWallText(value) {
    const text = String(value ?? '').toLowerCase();
    return /datadome|captcha|verify you are human|human verification|access to this page has been denied|unusual traffic|subscribe to continue|subscription required|sign in to (continue|read|access)|log in to (continue|read|access)/.test(text);
}

function pickAuthors(a) {
    if (!Array.isArray(a)) return null;
    const names = a
        .map((au) => (typeof au === 'string' ? au : au?.name || au?.byline || ''))
        .map((s) => String(s).trim())
        .filter(Boolean);
    return names.length ? names.join(', ') : null;
}

function trimOrNull(v) {
    const s = String(v ?? '').trim();
    return s ? s : null;
}

function dateOnly(v) {
    const s = String(v ?? '').trim();
    if (!s) return null;
    return s.split('T')[0];
}

function articleUrl(canonical) {
    if (!canonical) return null;
    const c = String(canonical);
    if (/^https?:\/\//i.test(c)) return c;
    return 'https://www.reuters.com' + c;
}

export function mapSearchArticles(body, limit) {
    const articles = body?.result?.articles || body?.articles || [];
    if (!Array.isArray(articles)) return [];
    return articles
        .filter((a) => a && typeof a === 'object')
        .slice(0, limit)
        .map((a, i) => ({
            rank: i + 1,
            title: trimOrNull(a.title || a.headlines?.basic),
            date: dateOnly(a.display_date || a.published_time),
            section: trimOrNull(a.taxonomy?.section?.name),
            section_path: trimOrNull(a.taxonomy?.section?.path),
            authors: pickAuthors(a.authors),
            url: articleUrl(a.canonical_url),
        }))
        .filter((row) => row.title && row.url);
}

export function mapArticleDetail(article, bodyText, fallbackUrl = null) {
    if (!article && !bodyText) return null;
    return {
        title: trimOrNull(article?.title || article?.headlines?.basic),
        date: dateOnly(article?.display_date || article?.published_time),
        section: trimOrNull(article?.taxonomy?.section?.name),
        section_path: trimOrNull(article?.taxonomy?.section?.path),
        authors: pickAuthors(article?.authors),
        description: trimOrNull(article?.description?.basic || article?.subheadlines?.basic),
        word_count: Number.isFinite(article?.word_count) ? article.word_count : null,
        url: articleUrl(article?.canonical_url) || trimOrNull(fallbackUrl),
        body: trimOrNull(bodyText),
    };
}

export const __test__ = { MIN_LIMIT, MAX_LIMIT };
