from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scanner import apply_schema, reindex


def make_daily(path: Path, title: str, url: str, t: str = "08:00:00"):
    path.write_text(
        f"""## Reuters · World（1条）
### [{title}]({url})
- 发布时间：2026-05-25 {t}
""",
        encoding="utf-8",
    )


def make_sidecar(path: Path, *, title: str, url: str, summary: str | None = None, source_type: str | None = None, source_name: str | None = None):
    path.write_text(
        json.dumps(
            {
                "schema_version": "newsreader.daily.v1",
                "items": [
                    {
                        "item_order": 1,
                        "section": f"Twitter · {source_name}" if source_type == "twitter" and source_name else "Reuters · World",
                        "source_type": source_type,
                        "source_name": source_name,
                        "title": title,
                        "summary": summary,
                        "published_at": "2026-05-25 08:00:00",
                        "url": url,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_reindex_is_idempotent_and_updates(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    f = daily_dir / "dailyFreshNews_2026-05-25.md"
    make_daily(f, "A", "https://example.com/a")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")

    s1 = reindex(conn, tmp_path / "DailyNews")
    assert s1.changed_files == 1
    c1 = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert c1 == 1

    s2 = reindex(conn, tmp_path / "DailyNews")
    assert s2.changed_files == 0
    c2 = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert c2 == 1

    make_daily(f, "A-UPDATED", "https://example.com/a", "09:00:00")
    s3 = reindex(conn, tmp_path / "DailyNews")
    assert s3.changed_files == 1
    row = conn.execute("SELECT title, published_at FROM items").fetchone()
    assert row[0] == "A-UPDATED"
    assert row[1].endswith("09:00")


def test_stale_delete_and_item_state_preserved(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    f = daily_dir / "dailyFreshNews_2026-05-25.md"
    f.write_text(
        """## Reuters · World（2条）
### [A](https://example.com/a)
- 发布时间：2026-05-25 08:00:00
### [B](https://example.com/b)
- 发布时间：2026-05-25 08:05:00
""",
        encoding="utf-8",
    )

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    reindex(conn, tmp_path / "DailyNews")
    ids = [r[0] for r in conn.execute("SELECT id FROM items ORDER BY id").fetchall()]
    assert len(ids) == 2

    conn.execute(
        "INSERT INTO item_state(item_id, bookmarked, skipped, read_at, updated_at) VALUES (?,1,0,NULL,'2026-05-25 10:00:00')",
        (ids[0],),
    )
    conn.commit()

    f.write_text(
        """## Reuters · World（1条）
### [A](https://example.com/a)
- 发布时间：2026-05-25 08:00:00
""",
        encoding="utf-8",
    )
    stats = reindex(conn, tmp_path / "DailyNews")
    assert stats.deleted_stale >= 1
    cnt_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert cnt_items == 1
    cnt_state = conn.execute("SELECT COUNT(*) FROM item_state").fetchone()[0]
    assert cnt_state == 1


def test_reindex_prefers_valid_sidecar_json(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-05-25.md"
    sidecar_path = daily_dir / "dailyFreshNews_2026-05-25.newsreader.json"
    make_daily(md_path, "Markdown 标题", "https://example.com/a")
    make_sidecar(sidecar_path, title="JSON 标题", url="https://example.com/a", summary="JSON 摘要")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    reindex(conn, tmp_path / "DailyNews")

    row = conn.execute("SELECT source_file, title, summary FROM items").fetchone()
    assert row == ("2026年5月/dailyFreshNews_2026-05-25.md", "JSON 标题", "JSON 摘要")
    source_row = conn.execute("SELECT ingest_mode, ingest_warning FROM source_files").fetchone()
    assert source_row == ("sidecar_json", None)


def test_reindex_falls_back_to_markdown_when_sidecar_invalid(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-05-25.md"
    sidecar_path = daily_dir / "dailyFreshNews_2026-05-25.newsreader.json"
    make_daily(md_path, "Markdown 标题", "https://example.com/a")
    sidecar_path.write_text(json.dumps({"schema_version": "newsreader.daily.v0", "items": []}), encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    reindex(conn, tmp_path / "DailyNews")

    row = conn.execute("SELECT title FROM items").fetchone()
    assert row == ("Markdown 标题",)
    source_row = conn.execute("SELECT ingest_mode, ingest_warning FROM source_files").fetchone()
    assert source_row[0] == "markdown_fallback"
    assert source_row[1] == "unsupported_newsreader_daily_schema"


def test_reindex_tracks_markdown_only_when_sidecar_missing(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-05-25.md"
    make_daily(md_path, "Markdown 标题", "https://example.com/a")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    stats = reindex(conn, tmp_path / "DailyNews")

    source_row = conn.execute("SELECT ingest_mode, ingest_warning FROM source_files").fetchone()
    assert source_row == ("markdown_only", None)
    assert stats.ingest_counts == {
        "sidecar_json": 0,
        "markdown_fallback": 0,
        "markdown_only": 1,
    }


def test_reindex_detects_sidecar_update_when_markdown_unchanged(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-05-25.md"
    sidecar_path = daily_dir / "dailyFreshNews_2026-05-25.newsreader.json"
    make_daily(md_path, "Markdown 标题", "https://example.com/a")
    make_sidecar(sidecar_path, title="JSON 标题", url="https://example.com/a", summary="摘要 A")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    first = reindex(conn, tmp_path / "DailyNews")
    assert first.changed_files == 1

    make_sidecar(sidecar_path, title="JSON 标题", url="https://example.com/a", summary="摘要 B")
    second = reindex(conn, tmp_path / "DailyNews")
    assert second.changed_files == 1
    row = conn.execute("SELECT summary FROM items").fetchone()
    assert row == ("摘要 B",)


def test_reindex_sidecar_can_set_twitter_source_meta(tmp_path: Path):
    daily_dir = tmp_path / "DailyNews" / "2026年5月"
    daily_dir.mkdir(parents=True)
    md_path = daily_dir / "dailyFreshNews_2026-05-25.md"
    sidecar_path = daily_dir / "dailyFreshNews_2026-05-25.newsreader.json"
    make_daily(md_path, "推文标题", "https://x.com/evan/status/1")
    make_sidecar(
        sidecar_path,
        title="推文标题",
        url="https://x.com/evan/status/1",
        summary="推文摘要",
        source_type="twitter",
        source_name="Evan",
    )

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    reindex(conn, tmp_path / "DailyNews")

    row = conn.execute("SELECT source, source_type, source_name FROM items").fetchone()
    assert row == ("Twitter · Evan", "twitter", "Evan")
