import test from 'node:test';
import assert from 'node:assert/strict';
import { ItemStateQueue } from '../static/item-state.js';

function fixture() {
  const item = { id: 'article', important_at: null, favorite_at: null, read_later_at: null, read_later_done_at: 'previous' };
  const requests = [], saved = [], errors = [];
  const queue = new ItemStateQueue({
    save: (id, body) => new Promise((resolve, reject) => requests.push({ id, body, resolve, reject })),
    onChange: (id, patch) => Object.assign(item, patch),
    onSaved: (id, changes) => saved.push(changes),
    onError: error => errors.push(error),
  });
  return { item, queue, requests, saved, errors };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('optimistic flags save serially and ignore unrelated fields in responses', async () => {
  const { item, queue, requests } = fixture();
  const done = queue.set(item, 'important', true);
  queue.set(item, 'favorite', true);
  assert.ok(item.important_at && item.favorite_at);
  assert.equal(requests.length, 1);
  requests[0].resolve({ important_at: 'saved', favorite_at: null });
  await tick();
  assert.ok(item.favorite_at);
  assert.equal(item.important_at, 'saved');
  assert.deepEqual(requests[1].body, { favorite: true });
  requests[1].resolve({ favorite_at: 'saved-favorite' });
  await done;
  assert.equal(item.favorite_at, 'saved-favorite');
  assert.equal(queue.hasAny('favorite'), false);
});

test('rapid toggles retain the latest intent and coalesce unsent clicks', async () => {
  const { item, queue, requests, saved } = fixture();
  const done = queue.set(item, 'favorite', true);
  queue.set(item, 'favorite', false);
  queue.set(item, 'favorite', true);
  queue.set(item, 'favorite', false);
  requests[0].resolve({ favorite_at: 'old-save' });
  await tick();
  assert.equal(item.favorite_at, null);
  assert.equal(saved.length, 0);
  assert.deepEqual(requests[1].body, { favorite: false });
  requests[1].resolve({ favorite_at: null });
  await done;
  assert.equal(requests.length, 2);
  assert.equal(item.favorite_at, null);
});

test('failure rolls back both read-later timestamps and releases saving state', async () => {
  const { item, queue, requests, errors } = fixture();
  const done = queue.set(item, 'read_later', true);
  assert.ok(item.read_later_at);
  assert.equal(item.read_later_done_at, null);
  requests[0].reject(new Error('offline'));
  await done;
  assert.equal(item.read_later_at, null);
  assert.equal(item.read_later_done_at, 'previous');
  assert.equal(queue.has(item.id, 'read_later'), false);
  assert.equal(errors.length, 1);
});

test('failed older request does not roll back a newer click or another flag', async () => {
  const { item, queue, requests } = fixture();
  const done = queue.set(item, 'favorite', true);
  queue.set(item, 'favorite', false);
  queue.set(item, 'favorite', true);
  queue.set(item, 'important', true);
  const latest = item.favorite_at;
  requests[0].reject(new Error('offline'));
  await tick();
  assert.equal(item.favorite_at, latest);
  assert.ok(item.important_at);
  assert.deepEqual(requests[1].body, { favorite: true, important: true });
  requests[1].resolve({ favorite_at: 'favorite-saved', important_at: 'important-saved' });
  await done;
  assert.equal(item.favorite_at, 'favorite-saved');
  assert.equal(item.important_at, 'important-saved');
});

test('failed later click rolls back to the previous successful save', async () => {
  const { item, queue, requests } = fixture();
  const done = queue.set(item, 'favorite', true);
  queue.set(item, 'favorite', false);
  requests[0].resolve({ favorite_at: 'saved' });
  await tick();
  requests[1].reject(new Error('offline'));
  await done;
  assert.equal(item.favorite_at, 'saved');
});

test('stale reads cannot overwrite pending, saved or rolled-back flags; fresh reads still sync', async () => {
  const { item, queue, requests } = fixture();
  const before = queue.version;
  const done = queue.set(item, 'important', true);
  const during = queue.version;
  assert.ok(queue.reconcile(item.id, { important_at: null }, during).important_at);
  requests[0].resolve({ important_at: 'saved' });
  await done;
  for (const version of [before, during])
    assert.equal(queue.reconcile(item.id, { important_at: null }, version).important_at, 'saved');
  assert.equal(queue.reconcile(item.id, { important_at: null }, queue.version).important_at, null);
  const failure = queue.set(item, 'important', false);
  const duringFailure = queue.version;
  requests[1].reject(new Error('offline'));
  await failure;
  assert.equal(queue.reconcile(item.id, { important_at: null }, duringFailure).important_at, 'saved');
});

test('different articles save concurrently', async () => {
  const { item, queue, requests } = fixture();
  const one = queue.set(item, 'important', true);
  const two = queue.set({ id: 'other', favorite_at: null }, 'favorite', true);
  assert.equal(requests.length, 2);
  requests[0].resolve({ important_at: 'saved' });
  requests[1].resolve({ favorite_at: 'saved' });
  await Promise.all([one, two]);
});

test('removing read-later never implies reading completion, even with cached content', async () => {
  const { item, queue, requests } = fixture();
  Object.assign(item, { read_later_at: 'queued', read_later_done_at: null, detail_ready: true });
  const done = queue.set(item, 'read_later', false);
  assert.equal(item.read_later_done_at, null);
  assert.deepEqual(requests[0].body, { read_later: false });
  requests[0].resolve({ read_later_at: null, read_later_done_at: null, detail_status: 'success' });
  await done;
  assert.equal(item.read_later_done_at, null);
  assert.equal(item.detail_status, 'success');
});

test('explicit completion is sent separately and rapid re-add clears it', async () => {
  const { item, queue, requests } = fixture();
  const done = queue.set(item, 'read_later', false, true, true);
  assert.ok(item.read_later_done_at);
  assert.deepEqual(requests[0].body, { read_later: false, read_later_done: true });
  queue.set(item, 'read_later', true);
  requests[0].resolve({ read_later_at: null, read_later_done_at: 'done' });
  await tick();
  assert.equal(item.read_later_done_at, null);
  assert.deepEqual(requests[1].body, { read_later: true });
  requests[1].resolve({ read_later_at: 'queued', read_later_done_at: null });
  await done;
  assert.equal(item.read_later_done_at, null);
});

test('completion reports persistence failure to bulk callers and settle waits for pending saves', async () => {
  const { item, queue, requests } = fixture();
  Object.assign(item, { read_later_at: 'queued', read_later_done_at: null });
  const failed = queue.set(item, 'read_later', false, true, true);
  let settled = false;
  const draining = queue.settle().then(() => { settled = true; });
  await tick();
  assert.equal(settled, false);
  requests[0].reject(new Error('offline'));
  assert.equal(await failed, false);
  await draining;
  assert.equal(settled, true);
  const saved = queue.set(item, 'read_later', false, true, true);
  requests[1].resolve({ read_later_at: null, read_later_done_at: 'done' });
  assert.equal(await saved, true);
});
