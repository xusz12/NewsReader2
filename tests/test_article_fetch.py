from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from api_fixtures import _isolate_app_settings
from nr import article_fetch, database


def test_run_opencli_twitter_detail_parses_list_thread_payload(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "quoted_tweet": {"text": "引用内容"},
            "created_at": "2026-06-11 09:00:00",
        },
        {"id": "reply-1", "author": "bob", "text": "评论一"},
        {"id": "reply-2", "author": "carol", "text": "评论二"},
    ]
    article_payload = {"content": "长文内容"}

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return True, article_payload, ""

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "【评论区观点】" in detail["content"]
    assert "评论一" in detail["content"]
    assert "评论二" in detail["content"]


def test_run_opencli_twitter_detail_includes_zero_comment_notice(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "created_at": "2026-06-11 09:00:00",
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "【评论区观点】" in detail["content"]
    assert "opencli thread 本次返回 0 条评论" in detail["content"]
    payload = json.loads(detail["raw_json"])
    assert payload["comment_count"] == 0


def test_run_opencli_twitter_detail_deduplicates_summary_prefix(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "id": "tweet-1",
            "author": "alice",
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "created_at": "2026-06-11 09:00:00",
        },
        *({"id": f"reply-{i}", "author": "bob", "text": f"评论 {i}"} for i in range(6)),
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    def fake_summary(**kwargs):
        count = kwargs["comment_count"]
        return {
            "model": "deepseek-chat",
            "summary_text": f"基于已抓取的 {count} 条评论总结：评论区主要围绕利好与估值分歧展开。",
            "raw_json": "{}",
        }

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    monkeypatch.setattr(app_module.runtime.fetcher, 'summarize_comments', fake_summary)
    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert detail["content"].count("基于已抓取的 6 条评论总结：") == 1


def test_twitter_image_url_filtering():
    import app as app_module

    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.jpg") is True
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.png") is True
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.webp?format=webp") is True
    assert article_fetch._is_twitter_image_url("https://example.com/image.jpeg") is False
    assert article_fetch._is_twitter_image_url("http://pbs.twimg.com/media/abc.jpg") is False
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/profile_images/abc.jpg") is False
    assert article_fetch._is_twitter_image_url("https://video.twimg.com/ext_tw_video/abc.mp4") is False
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.mp4") is False
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.m3u8") is False
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.jpg/amplify_video/123") is False
    assert article_fetch._is_twitter_image_url("https://pbs.twimg.com/media/abc.ext_tw_video.jpg") is False


def test_build_twitter_media_images_extracts_deduplicates_and_marks_source():
    import app as app_module

    main = {
        "media_urls": [
            "https://pbs.twimg.com/media/a.jpg",
            "https://video.twimg.com/v.mp4",
            "https://pbs.twimg.com/media/a.jpg",
        ]
    }
    quoted = {"media_urls": ["https://pbs.twimg.com/media/b.webp", "https://pbs.twimg.com/media/a.jpg"]}
    images = article_fetch._build_twitter_media_images(main, quoted)
    assert [img["url"] for img in images] == [
        "https://pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com/media/b.webp",
    ]
    assert images[0]["source"] == "tweet"
    assert images[1]["source"] == "quoted_tweet"


def test_build_twitter_media_images_ignores_media_posters():
    import app as app_module

    main = {
        "media_urls": ["https://pbs.twimg.com/media/poster.jpg"],
        "media_posters": ["https://pbs.twimg.com/media/poster.jpg"],
    }
    # media_posters itself is not used as an image source; only media_urls is read.
    images = article_fetch._build_twitter_media_images(main, None)
    assert [img["url"] for img in images] == ["https://pbs.twimg.com/media/poster.jpg"]


def test_run_opencli_twitter_detail_includes_media_images(monkeypatch):
    import app as app_module

    thread_payload = [
        {
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "media_urls": [
                "https://pbs.twimg.com/media/a.jpg",
                "https://video.twimg.com/v.mp4",
            ],
            "quoted_tweet": {
                "text": "引用推文内容",
                "media_urls": ["https://pbs.twimg.com/media/b.webp"],
            },
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    payload = json.loads(detail["raw_json"])
    assert payload["media_images"] == [
        {"url": "https://pbs.twimg.com/media/a.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/b.webp", "source": "quoted_tweet"},
    ]


def test_sanitize_twitter_media_images_filters_invalid_items():
    import app as app_module

    raw = [
        {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
        {"url": "https://example.com/image.jpg", "source": "tweet"},
        {"url": "https://video.twimg.com/ext_tw_video/x.mp4", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
        {"url": "https://pbs.twimg.com/media/duplicate.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/other.jpg", "source": "comments"},
        {"url": "https://pbs.twimg.com/media/amplify_video.jpg", "source": "tweet"},
        "not a dict",
    ]
    result = article_fetch._sanitize_twitter_media_images(raw)
    assert result == [
        {"url": "https://pbs.twimg.com/media/valid.jpg", "source": "tweet"},
        {"url": "https://pbs.twimg.com/media/valid2.png", "source": "quoted_tweet"},
        {"url": "https://pbs.twimg.com/media/duplicate.jpg", "source": "tweet"},
    ]


def test_media_cache_key_is_deterministic_sha256():
    import app as app_module

    key1 = article_fetch._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
    key2 = article_fetch._media_cache_key_for_url("https://pbs.twimg.com/media/a.jpg")
    assert isinstance(key1, str) and len(key1) == 64
    assert key1 == key2
    assert article_fetch._media_cache_key_for_url("https://pbs.twimg.com/media/b.jpg") != key1


def test_extension_for_mime_type():
    import app as app_module

    assert article_fetch._extension_for_mime_type("image/jpeg") == ".jpg"
    assert article_fetch._extension_for_mime_type("image/png") == ".png"
    assert article_fetch._extension_for_mime_type("image/webp") == ".webp"
    assert article_fetch._extension_for_mime_type("video/mp4") == ""
    assert article_fetch._extension_for_mime_type(None) == ""


def test_cache_twitter_image_downloads_and_records(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    def fake_download(url, dest_path):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake-image")
        return True, "image/jpeg", len(b"fake-image")

    monkeypatch.setattr(article_fetch, '_download_media_file', fake_download)

    url = "https://pbs.twimg.com/media/sample.jpg"
    conn = app_module.runtime.db.connect()
    try:
        result = app_module.runtime.fetcher._cache_twitter_image(conn, url)
        conn.commit()
    finally:
        conn.close()

    assert result is not None
    assert result["cached_url"].startswith("/api/media-cache/")
    cache_key = result["cache_key"]

    conn = app_module.runtime.db.connect()
    try:
        row = conn.execute("SELECT * FROM media_cache WHERE url=?", (url,)).fetchone()
        assert row["status"] == "success"
        assert row["mime_type"] == "image/jpeg"
        assert row["size_bytes"] == len(b"fake-image")
        assert (media_dir / row["relative_path"]).exists()
    finally:
        conn.close()

    # Second call should return existing cache without re-downloading.
    conn = app_module.runtime.db.connect()
    try:
        result2 = app_module.runtime.fetcher._cache_twitter_image(conn, url)
    finally:
        conn.close()
    assert result2["cache_key"] == cache_key


def test_cache_twitter_image_failure_records_failed_status(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    def fake_download(url, dest_path):
        return False, "http_404", 0

    monkeypatch.setattr(article_fetch, '_download_media_file', fake_download)

    url = "https://pbs.twimg.com/media/missing.jpg"
    conn = app_module.runtime.db.connect()
    try:
        result = app_module.runtime.fetcher._cache_twitter_image(conn, url)
        conn.commit()
    finally:
        conn.close()

    assert result is None
    conn = app_module.runtime.db.connect()
    try:
        row = conn.execute("SELECT status, last_error FROM media_cache WHERE url=?", (url,)).fetchone()
        assert row["status"] == "failed"
        assert "404" in row["last_error"]
    finally:
        conn.close()


def test_cleanup_media_cache_removes_old_files_and_records(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    old_file = media_dir / "ab" / "cd" / "old.jpg"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"old")
    new_file = media_dir / "ef" / "gh" / "new.jpg"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_bytes(b"new")

    conn = app_module.runtime.db.connect()
    try:
        ts = database.now_ts()
        old_ts = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/old.jpg", "oldkey", "ab/cd/old.jpg", "image/jpeg", 3, "success", old_ts, old_ts),
        )
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/new.jpg", "newkey", "ef/gh/new.jpg", "image/jpeg", 3, "success", ts, ts),
        )
        conn.commit()
        app_module.runtime.fetcher._cleanup_media_cache(conn)
        conn.commit()
    finally:
        conn.close()

    conn = app_module.runtime.db.connect()
    try:
        rows = conn.execute("SELECT url FROM media_cache").fetchall()
        assert [r["url"] for r in rows] == ["https://pbs.twimg.com/media/new.jpg"]
    finally:
        conn.close()
    assert not old_file.exists()
    assert new_file.exists()


def test_run_opencli_twitter_detail_caches_images(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    # Media belongs to an indexed article; a deleted/unowned article must not
    # create orphan cache files when an in-flight download completes.
    with app_module.runtime.db.connect() as conn:
        conn.execute("""INSERT INTO items(id,source_file,item_order,published_at,date,title,url,created_at,updated_at)
            VALUES ('media-item','fixture.md',1,'2026-06-11 09:00:00','2026-06-11','Media fixture',
                    'https://x.com/example/status/123','2026-06-11','2026-06-11')""")

    thread_payload = [
        {
            "text": "主推文内容比较完整，足够通过正文长度校验。",
            "media_urls": [
                "https://pbs.twimg.com/media/a.jpg",
                "https://video.twimg.com/v.mp4",
            ],
            "quoted_tweet": {"text": "引用内容", "media_urls": ["https://pbs.twimg.com/media/b.png"]},
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    downloaded: set[str] = set()

    def fake_download(url, dest_path):
        downloaded.add(url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    monkeypatch.setattr(article_fetch, '_download_media_file', fake_download)

    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    payload = json.loads(detail["raw_json"])
    assert len(payload["media_images"]) == 2
    for img in payload["media_images"]:
        assert "cache_key" in img
        assert img["cached_url"].startswith("/api/media-cache/")
    assert "https://video.twimg.com/v.mp4" not in downloaded


def test_media_cache_route_serves_cached_file(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    url = "https://pbs.twimg.com/media/route.jpg"
    cache_key = article_fetch._media_cache_key_for_url(url)
    relative_path = f"{cache_key[:2]}/{cache_key[2:4]}/{cache_key}.jpg"
    full_path = media_dir / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"cached-data")

    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, cache_key, relative_path, "image/jpeg", len(b"cached-data"), "success", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    client = app_module.app.test_client()
    res = client.get(f"/api/media-cache/{cache_key}")
    assert res.status_code == 200
    assert res.data == b"cached-data"
    assert res.mimetype == "image/jpeg"


def test_media_cache_route_rejects_invalid_and_missing_keys(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()
    client = app_module.app.test_client()

    assert client.get("/api/media-cache/not-hex").status_code == 400
    assert client.get("/api/media-cache/" + "0" * 64).status_code == 404


def test_media_cache_route_blocks_traversal_and_sibling_prefix(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    evil_sibling = tmp_path / "media-cache-evil"
    evil_sibling.mkdir(parents=True, exist_ok=True)
    evil_file = evil_sibling / "stolen.jpg"
    evil_file.write_bytes(b"evil")

    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    ts = database.now_ts()
    conn = app_module.runtime.db.connect()
    try:
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/evil.jpg", "e" * 64, "../media-cache-evil/stolen.jpg", "image/jpeg", 4, "success", ts, ts),
        )
        conn.commit()
    finally:
        conn.close()

    client = app_module.app.test_client()
    res = client.get(f"/api/media-cache/{'e' * 64}")
    assert res.status_code == 400
    assert evil_file.exists()


def test_cleanup_media_cache_does_not_delete_outside_files(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    outside_file = tmp_path / "outside.jpg"
    outside_file.write_bytes(b"outside")

    conn = app_module.runtime.db.connect()
    try:
        old_ts = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("https://pbs.twimg.com/media/outside.jpg", "0" * 64, "../outside.jpg", "image/jpeg", 7, "success", old_ts, old_ts),
        )
        conn.commit()
        app_module.runtime.fetcher._cleanup_media_cache(conn)
        conn.commit()
    finally:
        conn.close()

    assert outside_file.exists()
    conn = app_module.runtime.db.connect()
    try:
        row = conn.execute("SELECT url FROM media_cache WHERE cache_key=?", ("0" * 64,)).fetchone()
        assert row is None
    finally:
        conn.close()


def test_cache_twitter_image_rejects_non_whitelisted_url(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media-cache"
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(media_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    downloaded: set[str] = set()

    def fake_download(url, dest_path):
        downloaded.add(url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(article_fetch, '_download_media_file', fake_download)

    conn = app_module.runtime.db.connect()
    try:
        assert app_module.runtime.fetcher._cache_twitter_image(conn, "https://example.com/image.jpeg") is None
        assert app_module.runtime.fetcher._cache_twitter_image(conn, "http://pbs.twimg.com/media/a.jpg") is None
        assert app_module.runtime.fetcher._cache_twitter_image(conn, "https://pbs.twimg.com/profile_images/a.jpg") is None
        conn.commit()
    finally:
        conn.close()

    assert not downloaded


def test_run_opencli_twitter_detail_allows_short_tweet_with_media(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [
        {
            "text": "看图。",
            "media_urls": ["https://pbs.twimg.com/media/a.jpg"],
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)
    def fake_download(url, dest_path):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"img")
        return True, "image/jpeg", 3

    monkeypatch.setattr(article_fetch, '_download_media_file', fake_download)

    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    payload = json.loads(detail["raw_json"])
    assert len(payload["media_images"]) == 1
    assert payload["media_images"][0]["url"] == "https://pbs.twimg.com/media/a.jpg"


def test_run_opencli_twitter_detail_allows_short_tweet_with_quoted_text(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [
        {
            "text": "短评。",
            "quoted_tweet": {"text": "这是一条内容足够长的引用推文，用来验证短主推文不会因为正文长度不足而被判定为空线程。"},
        }
    ]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)

    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "短评" in detail["content"]


def test_run_opencli_twitter_detail_rejects_truly_empty_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [{"text": ""}]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)

    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is False
    assert error == "EMPTY_TWITTER_THREAD"


def test_run_opencli_twitter_detail_allows_single_character_tweet(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWS_READER_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news_index.sqlite3"))

    import app as app_module

    importlib.reload(app_module)
    app_module.ensure_db()

    thread_payload = [{"text": "早"}]

    def fake_run(cmd, timeout):
        if "thread" in cmd:
            return True, thread_payload, ""
        return False, None, "Article not found"

    monkeypatch.setattr(article_fetch, '_run_opencli_json_command', fake_run)

    ok, detail, error = app_module.runtime.fetcher.run_opencli_twitter_detail("https://x.com/example/status/123")
    assert ok is True
    assert error == ""
    assert "早" in detail["content"]
