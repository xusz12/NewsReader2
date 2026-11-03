import importlib
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, value in {
        "DB_PATH": "news.sqlite3", "AGENT_DB_PATH": "agent.sqlite3",
        "APP_SETTINGS_PATH": "settings.json", "DAILY_NEWS_DIR": "daily",
        "AGENT_RUNTIME_DIR": "agent-runtime", "MEDIA_CACHE_DIR": "media",
    }.items():
        monkeypatch.setenv("NEWS_READER_" + name, str(tmp_path / value))
    daily = tmp_path / "daily"
    daily.mkdir()
    source = daily / "dailyFreshNews_2026-11-01.md"
    source.write_text("""## Reuters · World（2条）
### [World one](https://www.reuters.com/world/one)
- 发布时间：2026-11-01 09:00:00
### [World two](https://www.reuters.com/world/two)
- 发布时间：2026-11-01 10:00:00
## Reuters · Business（1条）
### [Business](https://www.reuters.com/business/one)
- 发布时间：2026-11-01 11:00:00
""")
    import app
    importlib.reload(app)
    app.ensure_db()
    app.ensure_agent_db()
    client = app.app.test_client()
    assert client.post("/api/reindex").status_code == 200
    with app.db_conn() as conn:
        items = [dict(r) for r in conn.execute("SELECT * FROM items ORDER BY published_at")]
    return app, client, items, source


def preview(client, key="reuters/world"):
    response = client.get("/api/settings/sources/" + key)
    assert response.status_code == 200, response.json
    return response.json["source"]


def delete(client, snapshot=None, key="reuters/world"):
    return client.delete("/api/settings/sources/" + key, json={"confirmation_token": (snapshot or preview(client, key))["confirmation_token"]})


def seed_data(app, items):
    stamp = app.now_ts()
    for index, item in enumerate(items):
        url, ident = item["url"], item["id"]
        with app.db_conn() as conn:
            conn.execute("INSERT INTO article_details(url,content,raw_json,fetched_at,updated_at) VALUES (?,?,?,?,?)", (url, "正文" * 200, "{}", stamp, stamp))
            conn.execute("INSERT INTO article_ai(url,body_zh,updated_at) VALUES (?,?,?)", (url, "译文", stamp))
            conn.execute("INSERT INTO article_notes VALUES (?,?,?,?)", (url, "旧想法", stamp, stamp))
            conn.execute("INSERT INTO article_note_entries VALUES (?,?,?,?,?)", (str(index), url, "新想法", stamp, stamp))
            conn.execute("INSERT INTO article_note_metadata(note_id,quote_text) VALUES (?,?)", (str(index), "引用"))
            conn.execute("INSERT INTO article_highlights(url,content_hash,start_offset,end_offset,selected_text,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (url, "hash", 0, 2, "正文", stamp, stamp))
            conn.execute("INSERT INTO item_state(item_id,favorite_at,read_at,updated_at) VALUES (?,?,?,?)", (ident, stamp, stamp, stamp))
            conn.execute("INSERT INTO detail_jobs(url,item_id,status,updated_at) VALUES (?,?,?,?)", (url, ident, "pending", stamp))
            conn.execute("INSERT INTO ai_jobs(url,status,updated_at) VALUES (?,?,?)", (url, "pending", stamp))
        with app.agent_db_conn() as conn:
            for scope in [app.agent_library_scope(), "another-library"]:
                session = f"{index}-{scope}"
                conn.execute("""INSERT INTO agent_sessions(id,item_id,url,provider,context_hash,context_json,status,created_at,last_activity_at,expires_at,updated_at,db_scope)
                    VALUES (?,?,?,'pi','hash','{}','active',?,?,?, ?,?)""", (session, ident, url, stamp, stamp, "2099-01-01", stamp, scope))
                conn.execute("INSERT INTO agent_jobs(id,session_id,item_id,provider,question,status,created_at,updated_at,db_scope) VALUES (?,?,?,'pi','question','queued',?,?,?)", (session, session, ident, stamp, stamp, scope))
                conn.execute("INSERT INTO agent_messages(session_id,job_id,role,content,created_at,updated_at) VALUES (?,?,'user','hello',?,?)", (session, session, stamp, stamp))
                conn.execute("INSERT INTO agent_notifications(id,db_scope,item_id,session_id,job_id,message_id,title,preview,kind,created_at) VALUES (?,?,?,?,?,1,'title','preview','reply',?)", (session, scope, ident, session, session, stamp))
                # The first turn can create its directory before executor_session_id is saved.
                runtime = app.AGENT_RUNTIME_DIR / app._full_pi_executor_session_id(session)
                runtime.mkdir()
                (runtime / "context.json").write_text("context " * 20)


def test_preview_and_deletion_cover_related_data_and_preserve_other_sources(env):
    app, client, items, _ = env
    seed_data(app, items)
    data = preview(client)
    assert data["label"] == "World" and data["source_label"] == "Reuters"
    assert data["news_count"] == 2
    assert data["counts"]["article_details"] == data["counts"]["agent_sessions"] == 2
    assert data["data_bytes"] == data["database_bytes"] + data["file_bytes"] > 2400
    assert data["file_bytes"] == 320
    assert preview(client) == data  # Reading the preview has no destructive side effects.
    response = delete(client, data)
    assert response.status_code == 200, response.json
    assert response.json["deleted"]["data_bytes"] == data["data_bytes"]
    assert response.json["feed_source_subkeys"]["groups"][0]["items"][0]["key"] == "reuters/business"
    with app.db_conn() as conn:
        for table in ("items", "item_state", "article_details", "article_ai", "article_notes", "article_note_entries",
                      "article_note_metadata", "article_highlights", "detail_jobs", "ai_jobs", "item_classifications", "topic_ingestions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
        assert conn.execute("SELECT item_count FROM source_files").fetchone()[0] == 1
    with app.agent_db_conn() as conn:
        for table in ("agent_sessions", "agent_messages", "agent_jobs", "agent_notifications"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 4, table
    assert not (app.AGENT_RUNTIME_DIR / app._full_pi_executor_session_id(f"0-{app.agent_library_scope()}")).exists()
    assert (app.AGENT_RUNTIME_DIR / app._full_pi_executor_session_id("0-another-library")).exists()
    assert client.get("/api/settings/sources/reuters/world").status_code == 404


@pytest.mark.parametrize("body", [None, {}, {"confirmation_token": True}, {"confirmation_token": "invalid"}])
def test_confirmation_required_without_deleting_anything(env, body):
    app, client, items, _ = env
    response = client.delete("/api/settings/sources/reuters/world", json=body)
    assert response.status_code == 400
    with app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == len(items)


def test_stale_preview_and_wrong_source_token_are_rejected(env):
    app, client, items, _ = env
    data = preview(client)
    assert delete(client, data, "reuters/business").status_code == 409
    with app.db_conn() as conn:
        conn.execute("UPDATE items SET title='Changed' WHERE id=?", (items[0]["id"],))
    assert delete(client, data).json["error"] == "source_data_changed"
    assert delete(client).status_code == 200


def test_preview_is_bound_to_library_even_with_identical_rows(env, tmp_path, monkeypatch):
    app, client, _, _ = env
    data = preview(client)
    replacement = tmp_path / "other.sqlite3"
    with app.db_conn() as source, sqlite3.connect(replacement) as target:
        source.backup(target)
    monkeypatch.setattr(app, "DB_PATH", replacement)
    assert delete(client, data).status_code == 409


def test_deleted_news_stays_deleted_while_same_source_new_news_is_imported(env):
    app, client, items, source = env
    original = source.read_bytes()
    assert delete(client).status_code == 200
    assert source.read_bytes() == original
    source.write_text(source.read_text() + "\n")
    for full in [False, True]:
        assert client.post("/api/reindex", json={"full": full}).status_code == 200
        with app.db_conn() as conn:
            assert [row[0] for row in conn.execute("SELECT id FROM items")] == [items[-1]["id"]]
    sidecar = source.with_suffix(".newsreader.json")
    rows = [{"section": "Reuters · World", "title": "New World", "url": "https://reuters.com/world/new", "published_at": "2026-11-01 12:00:00", "item_order": 1},
            {"section": "Reuters · World", "title": "Old headline changed", "url": items[0]["url"], "published_at": "2026-11-01 09:00:00", "item_order": 2}]
    sidecar.write_text(json.dumps({"schema_version": "newsreader.daily.v1", "items": rows}))
    for full in [False, True]:
        assert client.post("/api/reindex", json={"full": full}).status_code == 200
        with app.db_conn() as conn:
            assert [r[0] for r in conn.execute("SELECT title FROM items")] == ["New World"]
    assert preview(client)["news_count"] == 1  # The source reappears for newly imported news.
    assert delete(client).status_code == 200
    assert client.post("/api/reindex", json={"full": True}).status_code == 200
    with app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM deleted_news_items").fetchone()[0] == 3


def test_new_items_in_old_and_new_markdown_files_are_not_blocked_by_date(env):
    app, client, items, source = env
    assert delete(client).status_code == 200
    # Late-arriving reporting can have an older date. Only actual deleted IDs
    # are excluded, not everything older than the deletion timestamp.
    source.write_text(source.read_text() + """\n## Reuters · World（1条）
### [Late arrival](https://reuters.com/world/late)
- 发布时间：2026-11-01 08:00:00
""")
    (source.parent / "dailyFreshNews_2026-11-02.md").write_text("""## Reuters · World（1条）
### [Next day](https://reuters.com/world/next)
- 发布时间：2026-11-02 09:00:00
""")
    for full in [False, True]:
        assert client.post("/api/reindex", json={"full": full}).status_code == 200
        with app.db_conn() as conn:
            assert {row[0] for row in conn.execute("SELECT title FROM items")} == {'Business', 'Late arrival', 'Next day'}
            assert conn.execute("SELECT COUNT(*) FROM deleted_news_items").fetchone()[0] == 2


def test_deletion_fingerprints_are_compact_deduplicated_and_persist_after_restart(env):
    app, client, items, source = env
    assert delete(client).status_code == 200
    with app.db_conn() as conn:
        assert [row[1] for row in conn.execute("PRAGMA table_info(deleted_news_items)")] == ['fingerprint']
        rows = conn.execute("SELECT typeof(fingerprint),length(fingerprint) FROM deleted_news_items").fetchall()
        assert [tuple(row) for row in rows] == [('blob', 32), ('blob', 32)]
        conn.execute("INSERT OR IGNORE INTO deleted_news_items VALUES (?)", (app.source_deletion.item_fingerprint(items[0]['id']),))
        assert conn.execute("SELECT COUNT(*) FROM deleted_news_items").fetchone()[0] == 2
        assert 'WITHOUT ROWID' in conn.execute("SELECT sql FROM sqlite_master WHERE name='deleted_news_items'").fetchone()[0]
    app.ensure_db()
    assert client.post("/api/reindex", json={"full": True}).status_code == 200
    with app.db_conn() as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM items")] == [items[-1]['id']]


def test_shared_url_content_is_preserved_and_job_rebound(env):
    app, client, items, _ = env
    seed_data(app, items)
    with app.db_conn() as conn:
        conn.execute("UPDATE items SET url=? WHERE id=?", (items[0]["url"], items[-1]["id"]))
    data = preview(client)
    assert data["shared_article_count"] == 1
    assert data["counts"]["article_details"] == 1
    assert delete(client, data).status_code == 200
    with app.db_conn() as conn:
        assert conn.execute("SELECT content FROM article_details WHERE url=?", (items[0]["url"],)).fetchone()
        assert conn.execute("SELECT item_id FROM detail_jobs WHERE url=?", (items[0]["url"],)).fetchone()[0] == items[-1]["id"]


def seed_media(app, items):
    app.MEDIA_CACHE_DIR.mkdir(exist_ok=True)
    with app.db_conn() as conn:
        for index, name in enumerate(["exclusive", "shared"]):
            (app.MEDIA_CACHE_DIR / name).write_bytes(b"image" * 40)
            conn.execute("INSERT INTO media_cache VALUES (?,?,?,'image/jpeg',200,'success',?, ?,NULL)",
                         ("https://pbs.twimg.com/media/" + name, name, name, app.now_ts(), app.now_ts()))
        conn.execute("UPDATE article_details SET raw_json=? WHERE url=?", ('{"images":["https://pbs.twimg.com/media/exclusive","https://pbs.twimg.com/media/shared"]}', items[0]["url"]))
        conn.execute("UPDATE article_details SET raw_json=? WHERE url=?", ('{"images":["https://pbs.twimg.com/media/shared"]}', items[-1]["url"]))


def test_media_size_counts_and_deletes_only_owned_files(env):
    app, client, items, _ = env
    seed_data(app, items)
    seed_media(app, items)
    data = preview(client)
    assert data["file_bytes"] == 520
    assert data["counts"]["media_cache"] == 1
    assert delete(client, data).status_code == 200
    assert not (app.MEDIA_CACHE_DIR / "exclusive").exists()
    assert (app.MEDIA_CACHE_DIR / "shared").stat().st_size == 200


def test_failed_transaction_restores_staged_files_and_rows(env, monkeypatch):
    app, client, items, _ = env
    seed_data(app, items)
    seed_media(app, items)
    data = preview(client)
    def fail(*args):
        raise sqlite3.OperationalError("test failure")
    monkeypatch.setattr(app.source_deletion.Plan, "delete_rows", fail)
    assert delete(client, data).status_code == 500
    assert preview(client) == data
    assert (app.MEDIA_CACHE_DIR / "exclusive").stat().st_size == 200


@pytest.mark.parametrize("worker", ["detail", "ai"])
def test_inflight_worker_cannot_recreate_deleted_data(env, monkeypatch, worker):
    app, client, items, _ = env
    seed_data(app, items)
    # Ensure the first selected job belongs to the deleted source.
    with app.db_conn() as conn:
        conn.execute("UPDATE detail_jobs SET queued_at=?", ("2026-11-01 00:00:00",))
        conn.execute("UPDATE ai_jobs SET queued_at=?", ("2026-11-01 00:00:00",))
    def finish(*args, **kwargs):
        assert delete(client).status_code == 200
        payload = {"content": "late", "content_length": 4, "source": "Reuters", "title": "late", "author": "", "published_at": "", "raw_json": "{}", "body_zh": "迟到的译文"}
        return (True, payload, "") if worker == "detail" else payload
    monkeypatch.setattr(app, "run_opencli_detail" if worker == "detail" else "generate_news_translation", finish)
    assert (app.process_pending_jobs_once if worker == "detail" else app.process_pending_ai_once)()
    with app.db_conn() as conn:
        for table in ("article_details", "article_ai", "ai_jobs", "detail_jobs"):
            assert not conn.execute(f"SELECT 1 FROM {table} WHERE url=?", (items[0]["url"],)).fetchone()


def test_missing_agent_job_means_cancelled_and_late_media_is_discarded(env, monkeypatch):
    app, client, items, _ = env
    assert app._agent_cancel_requested("deleted-job")
    def download(url, target):
        assert delete(client).status_code == 200
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"late-image")
        return True, "image/jpeg", 10
    monkeypatch.setattr(app, "_download_media_file", download)
    with app.db_conn() as conn:
        assert app._cache_twitter_image(conn, "https://pbs.twimg.com/media/late.jpg", article_url=items[0]["url"]) is None
        assert conn.execute("SELECT COUNT(*) FROM media_cache").fetchone()[0] == 0
    assert not list(app.MEDIA_CACHE_DIR.rglob("*.*"))


def test_deleted_article_cannot_start_new_media_downloads(env, monkeypatch):
    app, client, items, _ = env
    assert delete(client).status_code == 200
    def must_not_download(*args):
        pytest.fail('Deleted article attempted to download media')
    monkeypatch.setattr(app, '_download_media_file', must_not_download)
    with app.db_conn() as conn:
        assert app._cache_twitter_image(conn, 'https://pbs.twimg.com/media/gone.jpg', article_url=items[0]['url']) is None


def test_related_topic_reminder_checkpoint_and_review_rows_are_removed(env):
    app, client, items, _ = env
    item, stamp = items[0], app.now_ts()
    with app.db_conn() as conn:
        conn.execute("INSERT INTO classification_progress_items VALUES (?,'hash','rules')", (item["id"],))
        conn.execute("INSERT INTO topic_checks(topic_id,item_id,revision,fingerprint,item_json,updated_at) VALUES ('topic',?,1,'hash','{}',1)", (item["id"],))
        conn.execute("INSERT INTO topic_node_items VALUES ('node',?)", (item["id"],))
        conn.execute("INSERT INTO tracked_topic_items(topic_id,item_id,item_url,created_at,updated_at) VALUES (1,?,?,?,?)", (item["id"], item["url"], stamp, stamp))
        conn.execute("INSERT INTO reading_checkpoints VALUES ('feed',?,?,?,?)", (item["id"], item["url"], item["title"], stamp))
        conn.execute("INSERT INTO news_reminders(item_id,item_title_snapshot,item_url_snapshot,event_title,event_date,remind_at,created_at,updated_at) VALUES (?,?,?,'test',?,?,?,?)", (item["id"], item["title"], item["url"], stamp, stamp, stamp, stamp))
        for ident, parent in [(1,None),(2,1)]:
            conn.execute("INSERT INTO review_chains(id,parent_chain_id,source_type,source_key,source_note,source_created_at,plan_review_date,created_at,updated_at) VALUES (?,?,'article_note',?,'note',?,?,?,?)", (ident,parent,item["url"],stamp,stamp,stamp,stamp))
            conn.execute("INSERT INTO review_versions(chain_id,version_no,judgment,criteria,created_at) VALUES (?,1,'test','test',?)", (ident,stamp))
            conn.execute("INSERT INTO review_events(chain_id,event_type,event_date,created_at) VALUES (?,'progress',?,?)", (ident,stamp,stamp))
            conn.execute("INSERT INTO review_evidence(chain_id,news_title,news_url,added_at) VALUES (?,'test',?,?)", (ident,item["url"],stamp))
    assert delete(client).status_code == 200
    with app.db_conn() as conn:
        for table in ('classification_progress_items','topic_checks','topic_node_items','tracked_topic_items',
                      'reading_checkpoints','news_reminders','review_chains','review_versions','review_events','review_evidence'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0, table
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_large_source_uses_no_sql_parameter_limit_and_media_paths_cannot_escape(env, tmp_path):
    app, client, items, _ = env
    seed_data(app, items)
    seed_media(app, items)
    outside = tmp_path / 'outside.jpg'
    outside.write_bytes(b'keep')
    with app.db_conn() as conn:
        conn.execute("UPDATE media_cache SET relative_path='../outside.jpg' WHERE cache_key='exclusive'")
        for index in range(1200):
            conn.execute("""INSERT INTO items SELECT ?,source_file,item_order,published_at,date,time,source,source_type,
                source_name,title,summary,url,created_at,updated_at FROM items WHERE id=?""", (f'bulk-{index}',items[0]['id']))
    data = preview(client)
    assert data['news_count'] == 1202
    assert delete(client, data).status_code == 200
    assert outside.read_bytes() == b'keep'


def test_legacy_notes_and_entries_are_counted_without_double_counting(env):
    app, client, items, _ = env
    seed_data(app, items)
    with app.db_conn() as conn:
        conn.execute('DELETE FROM article_note_entries WHERE url=?', (items[0]['url'],))
    data = preview(client)
    assert data['note_count'] == 2
    assert data['counts']['article_note_entries'] == 1
