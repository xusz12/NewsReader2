"""Article revisions: persisted identities, legacy migration and cache ownership."""
import hashlib
import json
from contextlib import closing

import pytest

import collection_translation
import news_collection as collection
import news_versions as versions
from test_news_collection import SOURCE, api, article, query, service

URL = 'https://www.reuters.com/world/version-test-2026-11-09/'


def collect(svc, title, url=URL, publish=True):
    svc.collector = lambda _: {'ok': True, 'items': [article(url, title)]}
    svc.request(); svc.collect_once()
    if publish:
        while svc.translate_once():
            pass
        while svc.publish_once():
            pass
    return versions.version_key(url, title)


def seed_cache(conn, body='Old original body.', translated='旧版本正文。'):
    conn.execute('INSERT INTO article_details(url,title,source,content,fetched_at,updated_at) VALUES (?,?,?,?,?,?)',
                 (URL, 'Old headline', 'Reuters', body, 'captured', 'captured'))
    conn.execute('INSERT INTO article_ai(url,body_zh,conclusion_zh,key_points_zh,generated_at,updated_at) VALUES (?,?,?,?,?,?)',
                 (URL, translated, '旧结论', '["旧要点"]', 'translated', 'translated'))


@pytest.mark.parametrize('title', ['The “deal” & markets', 'The "deal" &amp; markets', '  The "deal"\u00a0 & markets\n', '\ufeffThe "deal" & markets\u200b'])
def test_cosmetic_title_changes_do_not_create_updates(service, title):
    original = collect(service, 'The "deal" & markets')
    collect(service, title, URL+'?utm_source=test#top')
    assert [r['id'] for r in query(service, 'SELECT id FROM items')] == [original]
    assert query(service, 'SELECT SUM(duplicate_count) FROM collection_run_sources')[0][0] == 1


def test_versions_are_unread_independent_of_translation_and_all_observations_dedup(service):
    service.translator = lambda raw, _: {**collection_translation.original_fields(raw), 'title': '相同中文译名'}
    a = collect(service, 'Markets rise 1%')
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,important_at,read_later_at,read_later_done_at,favorite_at,updated_at) VALUES (?,'read','important','later','done','favorite','saved')", (a,))
    before = tuple(query(service, 'SELECT * FROM item_state')[0])
    b = collect(service, 'Markets rise 2%')
    collect(service, 'Markets rise 1%')
    collect(service, 'Markets rise 2%')
    assert len(query(service, 'SELECT * FROM items')) == 2
    assert tuple(query(service, 'SELECT * FROM item_state')[0]) == before
    assert query(service, 'SELECT title FROM items WHERE id=?', (b,))[0][0] == '相同中文译名'
    assert not query(service, 'SELECT * FROM item_state WHERE item_id=?', (b,))
    row = query(service, 'SELECT * FROM news_title_versions WHERE item_id=?', (b,))[0]
    assert row['original_item_id'] == a and row['previous_item_id'] == a and row['is_update'] == 1
    assert query(service, 'SELECT COUNT(*) FROM topic_ingestions')[0][0] == 2


def test_tweet_edits_and_url_aliases_keep_post_identity(service):
    service.sources = [{**SOURCE, 'source_type': 'twitter', 'source_name': 'Twitter/X'}]
    collect(service, 'First tweet', 'https://twitter.com/author/status/123456')
    collect(service, 'Edited tweet', 'https://x.com/other/status/123456?utm_source=test')
    assert len(query(service, 'SELECT * FROM items')) == 1
    assert not query(service, 'SELECT * FROM news_title_versions')
    assert query(service, 'SELECT id FROM items')[0][0] == versions.identity_key('https://x.com/i/status/123456')


@pytest.mark.parametrize('raw_available', [False, True])
@pytest.mark.parametrize('id_kind', ['url_hash', 'date_url_hash'])
def test_legacy_migration_keeps_id_content_state_and_learns_missing_original(service, raw_available, id_kind):
    old_id = hashlib.sha256((versions.canonical_url(URL) if id_kind == 'url_hash' else '2026-11-09|'+URL).encode()).hexdigest()
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO items VALUES (?,'old.md',1,'2026-11-09 01:00:00','2026-11-09','01:00','Reuters','reuters','Reuters','旧中文标题','旧摘要',?,'old','old')", (old_id,URL))
        conn.execute("INSERT INTO item_state(item_id,read_at,favorite_at,updated_at) VALUES (?,'old-read','old-favorite','old')", (old_id,))
        conn.execute('INSERT INTO news_identities VALUES (?,?,0)', (versions.identity_key(URL),old_id))
        if raw_available:
            conn.execute("INSERT INTO collection_runs(id,trigger,status,created_at) VALUES ('legacy','manual','done','old')")
            conn.execute("INSERT INTO collection_items(item_id,run_id,source_id,raw_json,status,collected_at,published_at) VALUES (?,'legacy','world',?,'done','old','2026-11-09 01:00:00')", (old_id, json.dumps({**article(URL,'Original headline'),'source_type':'reuters'})))
        conn.execute('DELETE FROM news_version_migrations')
        before = [tuple(conn.execute('SELECT * FROM '+t).fetchone()) for t in ('items','item_state')]
        collection.initialize(conn)
        collection.initialize(conn)
        assert before == [tuple(conn.execute('SELECT * FROM '+t).fetchone()) for t in ('items','item_state')]
    collect(service, 'Original headline')
    assert query(service, 'SELECT id FROM items')[0][0] == old_id
    assert len(query(service, 'SELECT * FROM items')) == 1
    row = query(service, 'SELECT * FROM news_title_versions')[0]
    assert row['item_id'] == old_id and row['baseline_only'] == int(not raw_available)
    changed = collect(service, 'Changed original headline')
    assert len(query(service, 'SELECT * FROM items')) == 2
    assert query(service, 'SELECT original_item_id FROM news_title_versions WHERE item_id=?', (changed,))[0][0] == old_id
    assert before == [tuple(query(service, 'SELECT * FROM '+t+' WHERE '+('id' if t=='items' else 'item_id')+'=?',(old_id,))[0]) for t in ('items','item_state')]


def test_cache_snapshots_preserve_old_body_and_never_lend_new_body_to_uncaptured_version(service):
    a = collect(service, 'Headline A')
    with closing(service.connect()) as conn, conn:
        seed_cache(conn)
    b = collect(service, 'Headline B')
    c = collect(service, 'Headline C')
    with closing(service.connect()) as conn, conn:
        assert not conn.execute('SELECT * FROM article_details').fetchone()
        assert not conn.execute('SELECT * FROM article_ai').fetchone()
        seed_cache(conn, 'New body.', '新正文。')
        assert conn.execute('SELECT content FROM news_item_details WHERE item_id=?',(a,)).fetchone()[0] == 'Old original body.'
        assert conn.execute('SELECT body_zh FROM news_item_ai WHERE item_id=?',(a,)).fetchone()[0] == '旧版本正文。'
        assert not conn.execute('SELECT * FROM news_item_details WHERE item_id=?',(b,)).fetchone()
        assert not conn.execute('SELECT * FROM news_item_ai WHERE item_id=?',(b,)).fetchone()
        assert conn.execute('SELECT content FROM news_item_details WHERE item_id=?',(c,)).fetchone()[0] == 'New body.'


def test_late_translation_retry_cannot_restore_older_head_or_invalidate_new_cache(service):
    a = collect(service, 'Headline A', publish=False)
    with closing(service.connect()) as conn, conn:
        conn.execute("UPDATE collection_items SET status='failed' WHERE item_id=?", (a,))
    b = collect(service, 'Headline B')
    with closing(service.connect()) as conn, conn:
        seed_cache(conn, 'B body.', 'B 正文。')
    assert service.retry() == 1
    service.translate_once(); service.publish_once()
    with closing(service.connect()) as conn:
        assert versions.cache_token(conn, URL) == b
        assert conn.execute('SELECT content FROM news_item_details WHERE item_id=?',(b,)).fetchone()[0] == 'B body.'
        assert not conn.execute('SELECT * FROM news_item_details WHERE item_id=?',(a,)).fetchone()
        assert versions.archived(conn, a)


def test_same_batch_versions_publish_in_observed_order(service, monkeypatch):
    monkeypatch.setattr(collection, 'stamp', lambda *args: '2026-11-09T10:00:00+08:00')
    service.collector = lambda _: {'ok': True, 'items': [article(URL,'Z'),article(URL,'A')]}
    service.request(); service.collect_once()
    while service.translate_once():
        pass
    service.publish_once()
    with closing(service.connect()) as conn:
        assert versions.cache_token(conn, URL) == versions.version_key(URL,'A')
        assert versions.archived(conn, versions.version_key(URL,'Z'))


@pytest.fixture
def reader_service(api):
    module, client = api
    svc = collection.Service(module.db_conn, module.current_runtime_settings, sources=[SOURCE])
    yield module, client, svc
    svc.stop()


def test_reader_apis_use_version_body_and_keep_original_state(reader_service):
    module, client, svc = reader_service
    a = collect(svc, 'Headline A')
    with closing(svc.connect()) as conn, conn:
        seed_cache(conn)
        conn.execute("INSERT INTO item_state(item_id,read_at,favorite_at,updated_at) VALUES (?,'read','star','old')", (a,))
    b = collect(svc, 'Headline B')
    old = client.get(f'/api/news/{a}/detail').json
    new = client.get(f'/api/news/{b}/detail').json
    assert old['detail']['content'] == 'Old original body.' and old['ai']['body_zh'] == '旧版本正文。'
    assert old['read_at'] == 'read' and old['favorite_at'] == 'star'
    assert old['latest_version']['id'] == b and old['archived_version']
    assert new['is_update'] == 1 and new['previous_version']['id'] == a
    assert new['read_at'] is None and new['detail'] is None and new['ai'] is None
    statuses = {r['id']:r for r in client.get('/api/news/status',query_string={'ids':a+','+b}).json['items']}
    assert statuses[a]['detail_ready'] and not statuses[b]['detail_ready']
    assert statuses[b]['is_update'] and statuses[a]['archived_version']
    assert client.post(f'/api/news/{a}/detail/retry').status_code == 409
    assert client.post(f'/api/news/{b}/detail/retry').status_code == 200
    with closing(svc.connect()) as conn:
        assert conn.execute('SELECT item_id FROM detail_jobs').fetchone()[0] == b
    mark = client.post(f'/api/news/{a}/highlights',json={'body_kind':'original','selected_text':'Old','start_offset':0,'end_offset':3})
    assert mark.status_code == 201, mark.json
    assert client.get(f'/api/news/{a}/highlights').json['highlight_surfaces']['original']['highlights'][0]['status'] == 'active'
    assert client.get(f'/api/news/{b}/highlights').json['highlight_surfaces']['original']['body_ready'] is False
    _, detail = module._load_visible_agent_article(a)
    assert detail['content'] == 'Old original body.'


@pytest.mark.parametrize('worker', ['detail_success','detail_failure','ai'])
def test_update_revokes_inflight_workers_even_with_identical_claim_timestamp(reader_service, monkeypatch, worker):
    module, client, svc = reader_service
    a = collect(svc, 'Headline A')
    monkeypatch.setattr(module, 'now_ts', lambda: '2026-11-09 10:00:00')
    with closing(svc.connect()) as conn, conn:
        if worker == 'ai':
            seed_cache(conn)
            conn.execute('DELETE FROM article_ai')
            module.enqueue_ai_job(conn, URL)
        else:
            module.enqueue_detail_job(conn, a, URL, 'Reuters')
    def intervening_update(*args, **kwargs):
        b = collect(svc, 'Headline B')
        with closing(svc.connect()) as conn, conn:
            if worker == 'ai':
                seed_cache(conn,'Fresh body','新译文')
                module.enqueue_ai_job(conn, URL)
                conn.execute("UPDATE ai_jobs SET status='running',started_at=?", (module.now_ts(),))
            else:
                module.enqueue_detail_job(conn, b, URL, 'Reuters')
                conn.execute("UPDATE detail_jobs SET status='running',attempts=1,worker_pid=?,started_at=?", (module.os.getpid(),module.now_ts()))
        if worker == 'ai':
            return {'body_zh':'stale translation'}
        return (worker=='detail_success', {'content':'stale body','title':'A'}, 'NETWORK_ERROR')
    monkeypatch.setattr(module, 'generate_news_translation' if worker == 'ai' else 'run_opencli_detail', intervening_update)
    assert (module.process_pending_ai_once() if worker=='ai' else module.process_pending_jobs_once())
    with closing(svc.connect()) as conn:
        table = 'ai_jobs' if worker=='ai' else 'detail_jobs'
        assert conn.execute(f'SELECT status FROM {table}').fetchone()[0] == 'running'
        if worker == 'ai':
            assert conn.execute('SELECT body_zh FROM article_ai').fetchone()[0] == '新译文'
        else:
            assert not conn.execute('SELECT * FROM article_details').fetchone()


def test_source_deletion_removes_versions_and_blocks_pending_updates(reader_service):
    module, client, svc = reader_service
    collect(svc, 'Headline A')
    with closing(svc.connect()) as conn, conn:
        seed_cache(conn)
    collect(svc, 'Headline B')
    collect(svc, 'Headline C', publish=False)
    module.ensure_agent_db()
    snapshot = client.get('/api/settings/sources/reuters/world').json['source']
    assert snapshot['counts']['news_title_versions'] == 3
    assert snapshot['counts']['news_version_snapshots'] == 1
    response = client.delete('/api/settings/sources/reuters/world',json={'confirmation_token':snapshot['confirmation_token']})
    assert response.status_code == 200, response.json
    collect(svc, 'Headline D')
    for table in ('items','news_title_versions','news_version_snapshots','news_article_heads','collection_items','article_details','article_ai'):
        assert not query(svc, 'SELECT * FROM '+table), table
    assert query(svc, 'SELECT deleted FROM news_identities')[0][0] == 1


def test_timestamp_or_body_changes_alone_remain_duplicates(service):
    a = collect(service, 'Unchanged title')
    service.collector = lambda _: {'ok':True,'items':[{**article(URL,'Unchanged title','2026-11-10T12:00:00Z'),'summary':'New body excerpt'}]}
    service.request(); service.collect_once()
    assert not service.translate_once()
    assert [r['id'] for r in query(service,'SELECT id FROM items')] == [a]


def test_failed_batch_rolls_back_revision_cache_switch(service, monkeypatch):
    a = collect(service, 'Original headline')
    with closing(service.connect()) as conn, conn:
        seed_cache(conn)
    b = collect(service, 'Revised headline', publish=False)
    service.translate_once()
    enqueue = collection.topic_tracking.enqueue_checks
    monkeypatch.setattr(collection.topic_tracking,'enqueue_checks',lambda *_: (_ for _ in ()).throw(RuntimeError('rollback')))
    service.publish_once()
    with closing(service.connect()) as conn:
        assert versions.cache_token(conn,URL) == a
        assert not versions.archived(conn,a)
        assert not conn.execute('SELECT 1 FROM items WHERE id=?',(b,)).fetchone()
        assert conn.execute('SELECT content FROM article_details').fetchone()[0] == 'Old original body.'
    monkeypatch.setattr(collection.topic_tracking,'enqueue_checks',enqueue)
    assert service.retry() == 1
    assert service.publish_once()
    assert query(service,'SELECT COUNT(*) FROM news_version_snapshots')[0][0] == 1


def test_historical_queue_can_be_removed_without_canceling_current_fetch(reader_service):
    module, client, svc = reader_service
    a = collect(svc,'A')
    assert client.patch(f'/api/news/{a}/state',json={'read_later':True}).status_code == 200
    b = collect(svc,'B')
    assert client.post(f'/api/news/{b}/detail/retry').status_code == 200
    assert client.patch(f'/api/news/{a}/state',json={'read_later':True}).status_code == 409
    assert client.patch(f'/api/news/{a}/state',json={'read_later':False}).status_code == 200
    with closing(svc.connect()) as conn:
        assert conn.execute('SELECT status,item_id FROM detail_jobs').fetchone()[:] == ('pending',b)
        assert conn.execute('SELECT read_later_at FROM item_state WHERE item_id=?',(a,)).fetchone()[0] is None


def test_report_source_reference_selects_saved_version(reader_service):
    import reader_sources
    module, client, svc = reader_service
    a = collect(svc,'A')
    with closing(svc.connect()) as conn, conn:
        seed_cache(conn)
    b = collect(svc,'B')
    with closing(svc.connect()) as conn:
        resolved = reader_sources.source_items(conn,[{'ref':'N1','id':a,'url':URL}, {'ref':'N2','id':b,'url':URL}])
    assert resolved['N1']['detail_ready'] and resolved['N1']['archived_version']
    assert not resolved['N2']['detail_ready'] and resolved['N2']['is_update']


def test_legacy_duplicate_rows_keep_their_ids_and_can_find_latest_version(service):
    collect(service,'Original')
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO items SELECT 'legacy-alias',source_file,item_order,published_at,date,time,source,source_type,source_name,'历史同 URL 新闻',summary,url,created_at,updated_at FROM items LIMIT 1")
    b = collect(service,'Updated')
    with closing(service.connect()) as conn:
        row = {'id':'legacy-alias'}
        versions.attach(conn,[row])
        assert row['latest_item_id'] == b and row['archived_version']
        assert conn.execute("SELECT title FROM items WHERE id='legacy-alias'").fetchone()[0] == '历史同 URL 新闻'
