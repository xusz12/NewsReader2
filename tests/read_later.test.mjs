import test from 'node:test';
import assert from 'node:assert/strict';
import { readLaterPending, readLaterState } from '../static/read-later.js';

test('cached full text has one state regardless of the legacy queue flag', () => {
  for (const read_later_at of [null, 'queued']) {
    const item = { detail_ready: true, read_later_at };
    assert.equal(readLaterPending(item), true);
    assert.equal(readLaterState(item).phase, 'ready');
    assert.equal(readLaterState(item).active, true);
    assert.equal(readLaterState(item).hint, '点击标记为已看完');
  }
});
test('explicit completion stays done even if a fetch returns later', () => {
  const item = { detail_ready: true, read_later_done_at: 'done', detail_status: 'running' };
  assert.equal(readLaterState(item).phase, 'done');
  assert.equal(readLaterPending(item), false);
  assert.equal(readLaterState({ ...item, read_later_at: 'queued', read_later_done_at: null }).phase, 'ready');
});
test('queued cancellation is immediately idle while saving; running work stays running', () => {
  assert.equal(readLaterState({ read_later_at: 'queued', detail_status: 'pending' }).phase, 'pending');
  assert.equal(readLaterState({ read_later_at: null, detail_status: 'pending' }, true).phase, 'idle');
  assert.equal(readLaterState({ read_later_at: null, detail_status: 'canceled' }).label, '稍后再看');
  assert.equal(readLaterState({ read_later_at: null, detail_status: 'running' }, true).phase, 'running');
});
test('failure always offers retry, whether or not an old queue flag exists', () => {
  for (const read_later_at of [null, 'queued']) {
    assert.equal(readLaterState({ read_later_at, detail_status: 'failed' }).phase, 'failed');
    assert.equal(readLaterState({ read_later_at, detail_status: 'failed' }).hint, '点击重新抓取');
  }
});

test('missing or stopped historical jobs never pretend to be queued', () => {
  for (const detail_status of ['failed', 'skipped', 'canceled', 'success', null]) {
    const item = { detail_status, read_later_at: 'legacy', detail_ready: false };
    assert.equal(readLaterState(item).phase, 'failed');
    assert.equal(readLaterState(item, true).phase, 'pending');
  }
});
