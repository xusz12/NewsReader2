import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.matchMedia = () => ({matches:false});
const {chronologicalSources, readerSources} = await import('../static/reader-sources.js');

test('sources use actual publication instants, preserve ties and leave citations unchanged', () => {
  const sources = Object.freeze([
    {ref:'old',title:'旧报道',published_at:'2026-11-02T23:00:00+08:00'},
    {ref:'first',title:'北京时间',published_at:'2026-11-03T10:00:00+08:00'},
    {ref:'second',title:'UTC时间',published_at:'2026-11-03T02:00:00Z'},
    {ref:'new',title:'最新报道',published_at:'2026-11-03T09:00:00-04:00'},
  ].map(Object.freeze));
  const ordered = chronologicalSources(sources);
  assert.deepEqual(ordered.map(s=>s.ref), ['new','first','second','old']);
  assert.deepEqual(sources.map(s=>s.ref), ['old','first','second','new']);
  const html = readerSources(sources);
  assert.ok(html.indexOf('最新报道') < html.indexOf('北京时间'));
  assert.ok(html.indexOf('UTC时间') < html.indexOf('旧报道'));
});

test('legacy dates fall back to live publication time or Beijing dates; unknown dates come last', () => {
  const sources = [
    {ref:'unknown',published_at:'invalid'},
    {ref:'date',date:'2026-11-02'},
    {ref:'live',date:'2026-11-03'},
    {ref:'naive',published_at:'2026-11-03 09:30:00'},
    {ref:'time',date:'2026-11-03',time:'08:00'},
    {ref:'missing'},
    {ref:'archived',published_at:'2026-11-01T10:00:00+08:00'},
  ];
  const items = {live:{published_at:'2026-11-03T10:00:00+08:00'},archived:{published_at:'2099-01-01T00:00:00Z'}};
  assert.deepEqual(chronologicalSources(sources,items).map(s=>s.ref), ['live','naive','time','date','archived','unknown','missing']);
});
