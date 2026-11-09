"""URL-only deduplication and read compatibility with v0.5.2 title variants."""
import hashlib
import json
from contextlib import closing

import pytest

import collection_translation
import news_collection as collection
import news_versions as versions
from legacy_title_seed import seed_title, title_id, STAMP
from test_news_collection import SOURCE, api, article, query, service

URL = 'https://www.reuters.com/world/version-test-2026-11-09/'


def collect(svc, title, url=URL, publish=True):
    svc.collector = lambda _: {'ok': True, 'items': [article(url,title)]}
    svc.request(); svc.collect_once()
    if publish:
        while svc.translate_once():
            pass
        while svc.publish_once():
            pass
    return versions.identity_key(url)


def seed_cache(conn, body='Old original body.', translated='旧版本正文。'):
    conn.execute('INSERT INTO article_details(url,title,source,content,fetched_at,updated_at) VALUES (?,?,?,?,?,?)',
                 (URL,'Old headline','Reuters',body,'captured','captured'))
    conn.execute('INSERT INTO article_ai(url,body_zh,conclusion_zh,key_points_zh,generated_at,updated_at) VALUES (?,?,?,?,?,?)',
                 (URL,translated,'旧结论','["旧要点"]','translated','translated'))


@pytest.mark.parametrize('title', ['Markets rise 2%', 'Investors welcome the deal', 'The “deal” & markets', 'Other section description'])
def test_changed_title_and_publication_time_keep_url_id_state_and_cache(service, title):
    ident = collect(service,'Markets rise 1%')
    assert ident == hashlib.sha256(collection.canonical_url(URL).encode()).hexdigest()
    with closing(service.connect()) as conn, conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,important_at,read_later_at,read_later_done_at,favorite_at,updated_at) VALUES (?,'read','important','later','done','favorite','old')", (ident,))
        seed_cache(conn)
    tables = ('items','item_state','article_details','article_ai','topic_ingestions')
    before = {t:[tuple(r) for r in query(service,'SELECT * FROM '+t)] for t in tables}
    service.collector = lambda _: {'ok':True,'items':[article(URL+'?utm_source=business#story',title,'2026-11-10T13:00:00Z')]}
    service.translator = lambda *_: pytest.fail('duplicate must be rejected before translation')
    service.request(); service.collect_once()
    assert not service.translate_once()
    assert service.publish_once()
    assert before == {t:[tuple(r) for r in query(service,'SELECT * FROM '+t)] for t in tables}
    assert not query(service,'SELECT * FROM news_title_versions')
    assert not query(service,'SELECT * FROM news_version_snapshots')
    assert query(service,'SELECT SUM(duplicate_count) FROM collection_run_sources')[0][0] == 1


def test_different_sections_and_titles_share_one_pending_article(service):
    service.sources = [SOURCE,{**SOURCE,'section':'business','display_name':'Reuters · Business'}]
    service.collector = lambda source: {'ok':True,'items':[article(URL,source['display_name'])]}
    translations = []
    service.translator = lambda raw, _: translations.append(raw['title']) or collection_translation.original_fields(raw)
    service.request(); service.collect_once()
    assert len(query(service,'SELECT * FROM collection_items')) == 1
    assert service.translate_once(); assert service.publish_once()
    assert translations == ['Reuters · World']
    assert len(query(service,'SELECT * FROM items')) == 1
    assert query(service,'SELECT duplicate_count FROM collection_run_sources WHERE source_id=?',('business',))[0][0] == 1


def test_changed_title_does_not_bypass_failed_original_job(service):
    service.translator = lambda *_: (_ for _ in ()).throw(RuntimeError('HTTP_429'))
    ident = collect(service,'First title',publish=False)
    service.translate_once()
    collect(service,'Different section title',publish=False)
    assert len(query(service,'SELECT * FROM collection_items')) == 1
    assert query(service,'SELECT item_id,status FROM collection_items')[0][:] == (ident,'failed')
    service.translator = lambda raw, _: collection_translation.original_fields(raw)
    assert service.retry() == 1
    assert service.translate_once()
    while service.publish_once():
        pass
    assert [r['id'] for r in query(service,'SELECT * FROM items')] == [ident]


def test_tweet_edits_and_url_aliases_keep_post_identity(service):
    service.sources = [{**SOURCE,'source_type':'twitter','source_name':'Twitter/X'}]
    collect(service,'First tweet','https://twitter.com/author/status/123456')
    collect(service,'Edited tweet','https://x.com/other/status/123456?utm_source=test')
    assert len(query(service,'SELECT * FROM items')) == 1
    assert not query(service,'SELECT * FROM news_title_versions')
    assert query(service,'SELECT id FROM items')[0][0] == versions.identity_key('https://x.com/i/status/123456')


@pytest.mark.parametrize('id_kind',['url_hash','date_url_hash','title_hash'])
def test_existing_ids_need_no_original_title_baseline_and_are_not_rewritten(service,id_kind):
    legacy_id = (versions.identity_key(URL) if id_kind=='url_hash' else title_id(URL,'Lost title') if id_kind=='title_hash'
                 else hashlib.sha256(('2026-11-09|'+URL).encode()).hexdigest())
    with closing(service.connect()) as conn, conn:
        conn.execute('DELETE FROM collection_meta')
        conn.execute("INSERT INTO items VALUES (?,'old.md',1,'2026-11-09 01:00:00','2026-11-09','01:00','Reuters','reuters','Reuters','旧中文标题','旧摘要',?,'old','old')",(legacy_id,URL))
        conn.execute("INSERT INTO item_state(item_id,read_at,favorite_at,updated_at) VALUES (?,'read','star','old')",(legacy_id,))
        before = [tuple(conn.execute('SELECT * FROM '+t).fetchone()) for t in ('items','item_state')]
        collection.initialize(conn); collection.initialize(conn)
    collect(service,'Never seen original title')
    collect(service,'Another unseen title')
    assert before == [tuple(query(service,'SELECT * FROM '+t)[0]) for t in ('items','item_state')]
    assert len(query(service,'SELECT * FROM items')) == 1
    assert not query(service,'SELECT * FROM news_title_versions')


@pytest.mark.parametrize('status',['pending','running','ready','failed'])
def test_startup_retires_unpublished_title_duplicates_once_without_stalling_batches(service,status):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First')
        b = seed_title(conn,URL,'Different section',root=a,previous=a,status=status)
        seed_cache(conn)
        conn.execute("UPDATE collection_items SET lease='old-worker',lease_until=9999999999 WHERE item_id=?",(b,))
        before = [tuple(r) for r in conn.execute('SELECT * FROM items')]
        collection.initialize(conn); collection.initialize(conn)
        assert before == [tuple(r) for r in conn.execute('SELECT * FROM items')]
    row = query(service,'SELECT * FROM collection_items WHERE item_id=?',(b,))[0]
    assert row['status']=='duplicate' and row['ingested_at'] is None and row['lease'] is None
    stats = query(service,'SELECT * FROM collection_run_sources WHERE run_id=?',(row['run_id'],))[0]
    assert stats['duplicate_count']==1 and stats['added']==0 and stats['ingested_count']==0
    assert service.status()['awaiting_batches']==0
    assert service.retry()==0
    assert not service.translate_once()
    assert service.publish_once()
    assert len(query(service,'SELECT * FROM items'))==1
    assert query(service,'SELECT content FROM article_details')[0][0]=='Old original body.'


@pytest.mark.parametrize('raw_json', ['{invalid json', '{}', 'null', '{"url":"not a URL"}'])
def test_unverifiable_legacy_job_does_not_block_startup_or_invent_duplicates(service, raw_json):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First')
        b = seed_title(conn,URL,'Other title',root=a,status='failed')
        conn.execute('UPDATE collection_items SET raw_json=? WHERE item_id=?',(raw_json,b))
        before = tuple(conn.execute('SELECT * FROM collection_items WHERE item_id=?',(b,)).fetchone())
        collection.initialize(conn)
        assert before == tuple(conn.execute('SELECT * FROM collection_items WHERE item_id=?',(b,)).fetchone())
    assert query(service,'SELECT SUM(duplicate_count) FROM collection_run_sources')[0][0] == 0


def test_only_first_pending_legacy_variant_survives_and_new_urls_use_url_hash(service):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First',status='ready')
        b = seed_title(conn,URL,'Second',root=a,previous=a,status='ready')
        collection.initialize(conn)
    while service.publish_once():
        pass
    assert [r['id'] for r in query(service,'SELECT * FROM items')]==[a]
    assert query(service,'SELECT status FROM collection_items WHERE item_id=?',(b,))[0][0]=='duplicate'
    new_id = collect(service,'New URL','https://www.reuters.com/world/new-article/')
    assert query(service,'SELECT 1 FROM items WHERE id=?',(new_id,))
    assert not query(service,'SELECT 1 FROM news_title_versions WHERE item_id=?',(new_id,))


def test_mixed_upgrade_retires_old_duplicates_and_preserves_ordinary_jobs(service):
    current_ids = [collect(service,'New article',f'https://example.com/new/{status}',publish=False)
                   for status in ('pending','running','ready','failed')]
    with closing(service.connect()) as conn, conn:
        for ident,status in zip(current_ids,('pending','running','ready','failed')):
            conn.execute('UPDATE collection_items SET status=? WHERE item_id=?',(status,ident))
        original = seed_title(conn,URL,'Legacy title')
        duplicate = seed_title(conn,URL,'Section headline',root=original,status='pending')
        before = {r['item_id']:tuple(r) for r in conn.execute('SELECT * FROM collection_items')
                  if r['item_id'] in current_ids}
        collection.initialize(conn); collection.initialize(conn)
        after = {r['item_id']:tuple(r) for r in conn.execute('SELECT * FROM collection_items')
                 if r['item_id'] in current_ids}
    assert before == after
    assert query(service,'SELECT status FROM collection_items WHERE item_id=?',(duplicate,))[0][0]=='duplicate'
    assert query(service,'SELECT SUM(duplicate_count) FROM collection_run_sources')[0][0]==1


def test_late_original_retry_is_skipped_when_a_variant_already_exists(service):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First',status='failed')
        b = seed_title(conn,URL,'Second',root=a)
        seed_cache(conn,'Current saved body.','已存译文')
        collection.initialize(conn)
    assert query(service,'SELECT status FROM collection_items WHERE item_id=?',(a,))[0][0]=='duplicate'
    assert service.retry()==0
    assert [r['id'] for r in query(service,'SELECT * FROM items')]==[b]
    assert query(service,'SELECT content FROM article_details')[0][0]=='Current saved body.'


def test_retirement_revokes_inflight_title_translation(service):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First')
        b = seed_title(conn,URL,'Other title',root=a,status='pending')
    def translated(raw,_):
        with closing(service.connect()) as conn, conn:
            collection.initialize(conn)
        return collection_translation.original_fields(raw)
    service.translator=translated
    assert service.translate_once()
    assert service.publish_once()
    assert query(service,'SELECT status FROM collection_items WHERE item_id=?',(b,))[0][0]=='duplicate'
    assert len(query(service,'SELECT * FROM items'))==1


def test_publication_rechecks_duplicates_and_rolls_back_their_counts_with_batch(service,monkeypatch):
    with closing(service.connect()) as conn, conn:
        a = seed_title(conn,URL,'First')
        b = seed_title(conn,URL,'Second',root=a,status='ready',run_id='batch')
        c = seed_title(conn,'https://example.com/brand-new','Unrelated news',status='ready',run_id='batch')
    enqueue=collection.topic_tracking.enqueue_checks
    monkeypatch.setattr(collection.topic_tracking,'enqueue_checks',lambda *_: (_ for _ in ()).throw(RuntimeError('rollback')))
    assert service.publish_once()
    assert query(service,'SELECT status FROM collection_items WHERE item_id=?',(b,))[0][0]=='ready'
    assert query(service,"SELECT duplicate_count FROM collection_run_sources WHERE run_id='batch'")[0][0]==0
    monkeypatch.setattr(collection.topic_tracking,'enqueue_checks',enqueue)
    assert service.retry()==1
    assert service.publish_once()
    assert {r['id'] for r in query(service,'SELECT * FROM items')}=={a,c}
    assert query(service,"SELECT duplicate_count,ingested_count FROM collection_run_sources WHERE run_id='batch'")[0][:]==(1,1)


def test_unknown_legacy_duplicate_count_stays_unknown_and_empty_failed_batch_can_finish(service):
    with closing(service.connect()) as conn, conn:
        a=seed_title(conn,URL,'First')
        seed_title(conn,URL,'Second',root=a,status='ready',run_id='failed-batch')
        conn.execute("UPDATE collection_run_sources SET duplicate_count=NULL WHERE run_id='failed-batch'")
        conn.execute("UPDATE collection_runs SET publication_status='failed' WHERE id='failed-batch'")
        collection.initialize(conn)
    assert query(service,"SELECT duplicate_count FROM collection_run_sources WHERE run_id='failed-batch'")[0][0] is None
    assert service.status()['publication_failed']==0
    assert service.publish_once()


@pytest.fixture
def reader_service(api):
    module,client=api
    svc=collection.Service(module.db_conn,module.current_runtime_settings,sources=[SOURCE])
    yield module,client,svc
    svc.stop()


def test_published_legacy_rows_state_snapshots_and_reader_references_survive(reader_service):
    import reader_sources
    module,client,svc=reader_service
    with closing(svc.connect()) as conn, conn:
        a=seed_title(conn,URL,'First')
        seed_cache(conn)
        b=seed_title(conn,URL,'Middle',root=a,previous=a)
        c=seed_title(conn,URL,'Last',root=a,previous=b)
        seed_cache(conn,'Current body.','当前正文。')
        conn.execute("INSERT INTO item_state(item_id,read_at,favorite_at,updated_at) VALUES (?,'read','star','old')",(a,))
    tables=('items','item_state','news_title_versions','news_version_snapshots','article_details','article_ai','news_article_heads')
    before={t:[tuple(r) for r in query(svc,'SELECT * FROM '+t)] for t in tables}
    with closing(svc.connect()) as conn, conn:
        collection.initialize(conn); collection.initialize(conn)
    collect(svc,'Fourth headline must not enter')
    assert before=={t:[tuple(r) for r in query(svc,'SELECT * FROM '+t)] for t in tables}
    old=client.get(f'/api/news/{a}/detail').json
    middle=client.get(f'/api/news/{b}/detail').json
    latest=client.get(f'/api/news/{c}/detail').json
    retired_fields = {'is_update','update_detected_at','original_item_id','baseline_only'}
    for payload in (old,middle,latest):
        assert retired_fields.isdisjoint(payload)
    listing = client.get('/api/news?read_filter=all').json['items']
    assert {a,b,c}.issubset({item['id'] for item in listing})
    assert all(retired_fields.isdisjoint(item) for item in listing)
    assert old['read_at']=='read' and old['favorite_at']=='star'
    assert old['detail']['content']=='Old original body.' and old['ai']['body_zh']=='旧版本正文。'
    assert middle['detail'] is None and middle['ai'] is None
    assert latest['detail']['content']=='Current body.'
    assert old['latest_version']['id']==c and latest['previous_version']['id']==b
    assert client.post(f'/api/news/{a}/detail/retry').status_code==409
    assert client.patch(f'/api/news/{b}/state',json={'read_later':True}).status_code==409
    with closing(svc.connect()) as conn:
        resolved=reader_sources.source_items(conn,[{'ref':'N1','id':a,'url':URL},{'ref':'N2','id':b,'url':URL}])
    assert resolved['N1']['detail_ready'] and not resolved['N2']['detail_ready']
    _,detail=module._load_visible_agent_article(a)
    assert detail['content']=='Old original body.'
    mark=client.post(f'/api/news/{a}/highlights',json={'body_kind':'original','selected_text':'Old','start_offset':0,'end_offset':3})
    assert mark.status_code==201
    assert client.get(f'/api/news/{a}/highlights').json['highlight_surfaces']['original']['highlights'][0]['status']=='active'


def test_source_deletion_cleans_retired_jobs_and_never_resurrects_changed_titles(reader_service):
    module,client,svc=reader_service
    with closing(svc.connect()) as conn, conn:
        a=seed_title(conn,URL,'First')
        seed_cache(conn)
        b=seed_title(conn,URL,'Second',root=a,previous=a)
        seed_title(conn,URL,'Third',root=a,previous=b,status='pending')
        collection.initialize(conn)
    module.ensure_agent_db()
    snapshot=client.get('/api/settings/sources/reuters/world').json['source']
    assert snapshot['pending_news_count']==0
    response=client.delete('/api/settings/sources/reuters/world',json={'confirmation_token':snapshot['confirmation_token']})
    assert response.status_code==200,response.json
    collect(svc,'New description of deleted news')
    for table in ('items','news_title_versions','news_version_snapshots','news_article_heads','collection_items','article_details','article_ai'):
        assert not query(svc,'SELECT * FROM '+table),table
    assert query(svc,'SELECT deleted FROM news_identities')[0][0]==1
