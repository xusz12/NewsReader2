import { describe, expect, it } from 'vitest';
import { runInNewContext } from 'node:vm';
import { buildArticleDetailScript } from './utils.js';

const URL = 'https://www.reuters.com/markets/article-cleaning-example/';
const BEFORE = 'The company said quarterly revenue increased by 12%, supported by demand in Europe and Asia. Analysts said the figures were consistent with the forecasts published last month.';
const AFTER = 'The next paragraph contains the rest of the reported facts. The board will discuss the proposal on Friday, and the company has not changed its full-year outlook.';
const DAILY_BRIEFING = 'The Reuters Daily Briefing newsletter provides all the news you need to start your day. Sign up here.';

// Observed in the local Reuters cache; only the promotional copy is retained.
const PROMOTIONS = [
    'Make sense of global markets with the Trading Day newsletter. Sign up here.',
    'Jumpstart your morning with the latest legal news delivered straight to your inbox from The Daily Docket newsletter. Sign up here.',
    'The Misinformation Monitor newsletter rounds up international misinformation narratives, with a "Real or Fake?" quiz in every edition. Sign up here.',
    'Get a daily digest of breaking business news straight to your inbox with the Reuters Business newsletter. Sign up here.',
    'Get a look at the day ahead in European and global markets with the Morning Bid Europe newsletter. Sign up here.',
    'Get a look at the day ahead in U.S. and global markets with the Morning Bid U.S. newsletter. Sign up here.',
    'Get the latest news from India and how it matters to the world with the Reuters India File newsletter. Sign up here.',
    'Keep up with the latest medical breakthroughs and healthcare trends with the Reuters Health Rounds newsletter. Sign up here.',
    'Keep up with the latest medical breakthroughs and healthcare trends with the award-winning Reuters Health Rounds newsletter. Sign up here.',
    'Learn about the latest breakthroughs in AI and tech with the Reuters Artificial Intelligencer newsletter. Sign up here.',
    'Stay up to date on the key companies, data, and decisions in the ESG world with the Reuters Sustainable Finance newsletter. Sign up here.',
    'Stay up to date with the latest news, trends and innovations that are driving the global automotive industry with the Reuters Auto File newsletter. Sign up here.',
    'The Reuters Inside Track newsletter is your essential guide during the World Cup. Sign up here.',
    'The Reuters Power Up newsletter by columnist Ron Bousso provides everything you need to know about the global energy industry. Sign up here.',
    'The Reuters Power Up newsletter provides everything you need to know about the global energy industry. Sign up here.',
    'Want to receive the Morning Bid in your inbox every weekday morning? Sign up for the newsletter here. You can find ROI on the Reuters website, and you can follow us on LinkedIn and X',
    'The Reuters Iran Briefing newsletter keeps you informed with the latest developments and analysis of the Iran war. Sign up  here.',
    "The Week in Breakingviews newsletter offers insights and ideas from Reuters' global financial commentary team. Sign up here.",
    'Want to receive Trading Day in your inbox every weekday morning? Sign up for my newsletter here.',
    'Get weekly news and analysis on U.S. politics and how it matters to the world with the Reuters Politics U.S. newsletter. Sign up here.',
    'Want \u2060to receive the Morning Bid in your inbox every weekday morning? Sign up for the newsletter here. You can find ROI on the Reuters website, and you can follow us on LinkedIn and X',
    DAILY_BRIEFING,
];

function extract(paragraphs) {
    const nodes = paragraphs.map(value => typeof value === 'string' ? { textContent: value } : value);
    const document = {
        title: 'Headline | Reuters', readyState: 'complete',
        body: { innerText: nodes.map(node => node.textContent).join('\n\n') },
        getElementById: () => null,
        querySelector: selector => selector.includes('h1') ? { textContent: 'Headline' } : null,
        querySelectorAll: selector => {
            if (selector.includes('paragraph-')) return nodes;
            if (selector === 'iframe' || selector.includes('paywall')) return [];
            throw new Error(`Unexpected selector: ${selector}`);
        },
    };
    return runInNewContext(buildArticleDetailScript(), {
        document, window: { location: { href: URL } }, URL: globalThis.URL,
    });
}

function expectBody(paragraphs, expected) {
    const result = extract(paragraphs);
    expect(result.ok).toBe(true);
    expect(result.body.bodyText).toBe(expected);
    expect(result.state.kind).toBe('ready');
}

describe('Reuters newsletter cleanup in the generated extraction script', () => {
    for (const position of [0, 1, 2]) {
        it.each(PROMOTIONS)(`removes a promotion at paragraph ${position} without losing other paragraphs: %s`, promotion => {
            const paragraphs = [BEFORE, AFTER];
            paragraphs.splice(position, 0, promotion);
            expectBody(paragraphs, `${BEFORE}\n\n${AFTER}`);
        });
    }

    it('removes multiple interleaved and repeated promotions without stopping extraction', () => {
        expectBody([PROMOTIONS[0], BEFORE, PROMOTIONS[1], PROMOTIONS[2], AFTER, PROMOTIONS[0]], `${BEFORE}\n\n${AFTER}`);
    });

    it.each(PROMOTIONS.slice(0, 3))('preserves both sides when promotional sentences share a body paragraph: %s', promotion => {
        expectBody([`${BEFORE} ${promotion} ${AFTER}`], `${BEFORE} ${AFTER}`);
    });

    it('removes repeated adjacent promotional sentences from a shared paragraph', () => {
        expectBody([`${BEFORE} ${PROMOTIONS[0]} ${PROMOTIONS[0]} ${AFTER}`], `${BEFORE} ${AFTER}`);
    });

    it.each(PROMOTIONS.slice(0, 3))('handles a signup link extracted as the following paragraph: %s', promotion => {
        const description = promotion.replace(/ Sign up here\.$/, '');
        expectBody([BEFORE, description, 'Sign up here.', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('handles the columnist signup wording as a separate paragraph', () => {
        expectBody([BEFORE, 'Want to receive Trading Day in your inbox every weekday morning?', 'Sign up for my newsletter here.', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('normalizes non-breaking spaces, line breaks, zero-width characters and a trailing backslash', () => {
        const promotion = PROMOTIONS[0].replace(/ /g, '\u00a0').replace('Trading', 'Tra\u200bding').replace('Sign', '\nSign') + '\\';
        expectBody([BEFORE, promotion, AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('recognizes a signup link with the Reuters opens-new-tab accessibility suffix', () => {
        const promotion = PROMOTIONS[0].replace('Sign up here.', 'Sign up here, opens new tab.');
        expectBody([BEFORE, promotion, AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('matches promotional templates with a different newsletter name and letter case', () => {
        expectBody([BEFORE, 'LEARN ABOUT THE LATEST DEVELOPMENTS WITH THE FUTURE TECHNOLOGY NEWSLETTER. SIGN UP HERE.', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it.each([
        'The Trading Day newsletter reported that global stocks fell on Tuesday.',
        'The Misinformation Monitor newsletter examined how the claims spread online.',
        'The Reuters Iran Briefing newsletter published an investigation on Tuesday.',
        'The company reported that newsletter subscriptions and advertising revenue increased.',
        'Recommended changes to the subscription rules will be discussed next week.',
        'Advertisement spending by retailers increased by 12%, the report said.',
        'An editor said the phrase "Sign up here" appeared on the registration form.',
        `The publisher quoted its own pitch: "${PROMOTIONS[0]}"`,
        `"${PROMOTIONS[2]}" was the text cited in the complaint.`,
        `The complaint quoted an email: "Welcome to our service. ${PROMOTIONS[0]}"`,
        `The complaint quoted an email: “Welcome to our service. ${PROMOTIONS[0]}”`,
        `The complaint quoted an email: 'It's a new service. ${PROMOTIONS[0]}'`,
        `The complaint quoted an email: ‘It’s a new service. ${PROMOTIONS[0]}’`,
        'Make sense of global markets with the Trading Day newsletter.',
        'Get a daily digest of the data. The newsletter reported that inflation fell. Sign up here.',
    ])('preserves reporting, quotations and ambiguous copy: %s', paragraph => {
        expectBody([BEFORE, paragraph, AFTER], `${BEFORE}\n\n${paragraph}\n\n${AFTER}`);
    });

    it('does not remove an ordinary paragraph because the next paragraph is a signup prompt', () => {
        expectBody([BEFORE, 'Sign up here.', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('still removes a promotion following a completed quotation', () => {
        expectBody([`${BEFORE} The company said "it is ready". ${PROMOTIONS[0]} ${AFTER}`], `${BEFORE} The company said "it is ready". ${AFTER}`);
    });

    it('continues after ordinary ad and recommendation labels', () => {
        expectBody([BEFORE, 'Advertisement - Scroll to continue', 'Recommended', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it('does not accept a page containing only promotional paragraphs as an article', () => {
        const result = extract(PROMOTIONS);
        expect(result.ok).toBe(true);
        expect(result.body.bodyText).toBeNull();
        expect(result.state.kind).toBe('loading');
    });

    it('removes Daily Briefing from a shared paragraph without losing surrounding reporting', () => {
        expectBody([`${BEFORE} ${DAILY_BRIEFING} ${AFTER}`], `${BEFORE} ${AFTER}`);
    });

    it('pairs the Daily Briefing description with a separate signup paragraph', () => {
        expectBody([BEFORE, DAILY_BRIEFING.replace(' Sign up here.', ''), 'Sign up here.', AFTER], `${BEFORE}\n\n${AFTER}`);
    });

    it.each([
        DAILY_BRIEFING.replace(' Sign up here.', ''),
        `The editor quoted the pitch: "${DAILY_BRIEFING}"`,
        'The Reuters Daily Briefing newsletter reported on the proposed rules on Friday.',
    ])('preserves ambiguous Daily Briefing copy and reporting: %s', paragraph => {
        expectBody([BEFORE, paragraph, AFTER], `${BEFORE}\n\n${paragraph}\n\n${AFTER}`);
    });
});

describe('Reuters embedded video transcript exclusion', () => {
    function transcript(text, fallbackClass = false) {
        const container = {getAttribute: name => name === 'data-testid' && !fallbackClass ? 'VideoTranscript'
            : name === 'class' && fallbackClass ? 'video-transcript-module__scroller__example' : null};
        return {textContent: text, parentElement: container};
    }
    it.each([0, 1, 2])('excludes video notice, speaker and transcript at article position %s', position => {
        const paragraphs = [BEFORE, AFTER];
        paragraphs.splice(position, 0, transcript('This transcript was produced with AI technology.'),
            transcript('Speaker 1:'), transcript('Separate video narration. '.repeat(20)),
            transcript('Reporting by the video team'));
        expectBody(paragraphs, `${BEFORE}\n\n${AFTER}`);
    });
    it('recognizes the observed video transcript CSS container when test IDs are missing', () => {
        expectBody([BEFORE, transcript('Separate video narration.', true), AFTER], `${BEFORE}\n\n${AFTER}`);
    });
    it('preserves reporting about transcripts outside the video component', () => {
        const paragraph = 'Speaker 1: The court said the video transcript was important evidence in the case.';
        expectBody([BEFORE, paragraph, AFTER], `${BEFORE}\n\n${paragraph}\n\n${AFTER}`);
    });
    it('does not accept a long video transcript as a ready news article', () => {
        const result = extract([transcript('Separate video narration. '.repeat(30))]);
        expect(result.ok).toBe(true);
        expect(result.body.bodyText).toBeNull();
        expect(result.state.kind).toBe('loading');
    });
});
