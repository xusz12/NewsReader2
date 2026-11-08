"""Historical news fixtures for reader regressions; no production HTTP scanner."""
import os
from pathlib import Path
from contextlib import closing
from flask import jsonify
from legacy_news_scanner import reindex


def seed_news(client, json=None):
    import app
    with closing(app.db_conn()) as conn:
        stats = reindex(conn, Path(os.environ["NEWS_READER_DAILY_NEWS_DIR"]), full=bool((json or {}).get("full")))
        matched = app.run_tracked_topics_incremental(conn)
        conn.commit()
    with client.application.app_context():
        return jsonify(ok=True, **vars(stats), tracked_incremental_matches=matched)


def confirm_all_read(client, json=None):
    preview = client.post("/api/news/mark-all-read", json={**(json or {}), "preview": True})
    if preview.status_code != 200:
        return preview
    return client.post("/api/news/mark-all-read", json={"preview_id": preview.json["preview_id"]})
