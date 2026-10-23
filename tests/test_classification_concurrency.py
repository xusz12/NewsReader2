import sqlite3
import threading
import time
from email.utils import formatdate

import pytest

import news_classification as nc
from test_news_classification import conn, insert, response


@pytest.mark.parametrize('value,expected', [('30', 30), ('-1', 0), ('bad', 0),
    ('nan', 0), ('inf', 0), (None, 0), (formatdate(1060, usegmt=True), 60), (formatdate(900, usegmt=True), 0)])
def test_retry_after_seconds_and_http_date(value, expected):
    assert nc.retry_after_seconds(value, now=1000) == expected


def test_http_throttling_captures_retry_after(monkeypatch):
    from urllib.error import HTTPError

    def limited(*args, **kwargs):
        raise HTTPError('https://api.typesafe.ai/v1/systemone', 429, 'limited', {'Retry-After': '75'}, None)

    monkeypatch.setattr(nc, 'urlopen', limited)
    with pytest.raises(nc.ClassificationError) as caught:
        nc.classify('title', 'test-key', 'test-model')
    assert caught.value.rate_limited and caught.value.retry_after == 75


def test_shared_backoff_one_reduction_per_wave_and_old_success_does_not_recover():
    now = [100.0]
    gate = nc.AdaptiveConcurrency(clock=lambda: now[0])
    stopped = threading.Event()
    tokens = [gate.acquire(stopped) for _ in range(8)]
    assert gate.snapshot()['active_requests'] == 8
    for token in tokens[:7]:
        gate.result(token, nc.ClassificationError('http_429', retryable=True, retry_after=60))
        gate.release()
    assert gate.snapshot() == {'configured_concurrency': 8, 'effective_concurrency': 4,
        'active_requests': 1, 'rate_limited': True, 'cooldown_seconds': 60}
    now[0] += 100
    gate.result(tokens[-1], None)  # Success from before the throttle cannot speed it up.
    gate.release()
    assert gate.successes == 0
    token = gate.acquire(stopped)
    gate.result(token, nc.ClassificationError('http_529', retryable=True))
    gate.release()
    assert gate.snapshot()['effective_concurrency'] == 2
    assert gate.snapshot()['cooldown_seconds'] == 10
    gate.configure(16)
    assert gate.snapshot()['effective_concurrency'] == 2  # Settings cannot bypass a cooldown.
    now[0] += 10
    for _ in range(30):
        gate.result(gate.generation, None)
    assert gate.snapshot()['effective_concurrency'] == 2  # Minimum stable period, not just request count.
    now[0] += 30
    gate.result(gate.generation, None)
    assert gate.snapshot()['effective_concurrency'] == 3
    for _ in range(20):
        now[0] += 30
        for _ in range(32):
            gate.result(gate.generation, None)
    assert gate.snapshot()['effective_concurrency'] == 16
    assert gate.snapshot()['rate_limited'] is False


def test_gate_blocks_new_work_during_cooldown_and_live_lower_limit():
    gate = nc.AdaptiveConcurrency(limit=2)
    stop, acquired = threading.Event(), threading.Event()
    tokens = [gate.acquire(stop), gate.acquire(stop)]
    gate.configure(1)

    def waiting():
        if gate.acquire(stop) is not None:
            acquired.set()
            gate.release()

    thread = threading.Thread(target=waiting)
    thread.start()
    try:
        gate.release()
        assert not acquired.wait(.05)  # One old request still in flight at the new limit of one.
        gate.result(tokens[-1], nc.ClassificationError('http_429', retryable=True))
        gate.release()
        assert not acquired.wait(.05)  # Zero in flight, but the shared cooldown still blocks.
        with gate.condition:
            gate.cooldown_until = 0
            gate.condition.notify_all()
        assert acquired.wait(1)
    finally:
        stop.set()
        with gate.condition:
            gate.condition.notify_all()
        thread.join(2)
    assert gate.snapshot()['active_requests'] == 0


def test_rate_limit_does_not_exhaust_titles_or_bypass_cooldown_on_retry(conn):
    insert(conn)
    with conn:
        nc.enqueue(conn)
    for _ in range(8):
        job = nc.claim(conn)
        error = nc.ClassificationError('http_429', retryable=True, retry_after=75)
        before = time.time()
        nc.finish(conn, job, None, error)
        row = conn.execute('SELECT * FROM item_classifications').fetchone()
        assert row['status'] == 'pending' and row['attempts'] == 0
        assert row['next_attempt_at'] >= before + 75
        assert nc.retry_failed(conn) == 0
        with conn:
            conn.execute('UPDATE item_classifications SET next_attempt_at=0')
    job = nc.claim(conn)
    nc.finish(conn, job, nc.parse_response(response()))
    assert nc.status(conn)['done'] == 1


@pytest.mark.parametrize('initial_limit', [8, 2])
def test_eight_workers_run_requests_concurrently_without_duplicate_titles(conn, initial_limit):
    for i in range(16):
        insert(conn, str(i), f'title-{i}')
    with conn:
        nc.enqueue(conn)
    path = conn.execute('PRAGMA database_list').fetchone()[2]

    def connect():
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    lock, release, reached = threading.Lock(), threading.Event(), threading.Event()
    initial_reached = threading.Event()
    seen = []
    active = peak = 0

    def runner(title, key, model):
        nonlocal active, peak
        with lock:
            seen.append(title)
            active += 1
            peak = max(peak, active)
            if active == initial_limit:
                initial_reached.set()
            if active == 8:
                reached.set()
        release.wait(5)
        with lock:
            active -= 1
        return nc.parse_response(response())

    service = nc.Service(connect, lambda: ('test-key', 'test-model'), runner=runner, concurrency=initial_limit)
    try:
        service.start()
        assert initial_reached.wait(4)
        if initial_limit < 8:
            service.configure(8)  # Raising the live limit must add workers without a restart.
        assert reached.wait(4), 'Eight requests must be in flight before any response is released'
        assert peak == 8
        service.configure(4)
        release.set()
        end = time.monotonic() + 5
        while time.monotonic() < end and nc.status(conn)['done'] < 16:
            time.sleep(.02)
        assert nc.status(conn)['done'] == 16
        assert len(seen) == len(set(seen)) == 16
        assert service.gate.snapshot()['effective_concurrency'] == 4
        service.gate.result(service.gate.generation, nc.ClassificationError('http_429'))
        service.resume()
        assert service.gate.snapshot()['cooldown_seconds'] > 0
    finally:
        service.stop.set()
        release.set()
        with service.gate.condition:
            service.gate.condition.notify_all()
        for thread in service.threads:
            thread.join(2)
