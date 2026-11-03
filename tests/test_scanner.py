from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scanner import apply_schema, list_daily_files, reindex


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


@pytest.mark.parametrize("ingest_mode", ["markdown_only", "sidecar_json", "markdown_fallback"])
@pytest.mark.parametrize("full", [False, True])
def test_reindex_excludes_video_pages_before_inserting_or_queuing(tmp_path, ingest_mode, full):
    md_path = tmp_path / "dailyFreshNews_2026-05-25.md"
    sidecar_path = md_path.with_suffix(".newsreader.json")
    articles = [
        "https://www.bloomberg.com/news/articles/2026-05-25/a-video-company",
        "https://example.com/news/videos/ordinary-article",
        "https://notbloomberg.com/news/videos/ordinary-article",
        "https://bloomberg.com.example.com/news/videos/ordinary-article",
    ]
    videos = [
        "https://www.bloomberg.com/news/videos/2026-05-25/one",
        "https://bloomberg.com/news/videos/2026-05-25/two?source=test",
        "https://MARKETS.BLOOMBERG.COM./NEWS/VIDEOS/2026-05-25/three",
    ]

    def write_input(urls):
        # A misleading section/source label must not bypass URL-based exclusion.
        md_path.write_text("## Reuters · World\n" + "\n".join(
            f"### [合成视频行业新闻 {index}]({url})\n- 发布时间：2026-05-25 08:00:00"
            for index, url in enumerate(urls)), encoding="utf-8")
        if ingest_mode == "sidecar_json":
            sidecar_path.write_text(json.dumps({"schema_version": "newsreader.daily.v1", "items": [
                {"title": f"合成视频行业新闻 {index}", "url": url, "published_at": "2026-05-25 08:00:00", "section": "Reuters"}
                for index, url in enumerate(urls)]}), encoding="utf-8")
        elif ingest_mode == "markdown_fallback":
            sidecar_path.write_text("invalid JSON", encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    write_input([articles[0], *videos])
    first = reindex(conn, tmp_path)
    assert first.upserted == 1 and first.skipped_videos == len(videos)
    retained_id = conn.execute("SELECT id FROM items").fetchone()[0]
    with conn:
        conn.execute("INSERT INTO item_state(item_id,read_at,updated_at) VALUES (?,'read','before')", (retained_id,))

    write_input([*articles, *videos])
    original_input = md_path.read_bytes()
    stats = reindex(conn, tmp_path, full=full)
    assert stats.upserted == len(articles) and stats.skipped_videos == len(videos)
    assert {row[0] for row in conn.execute("SELECT url FROM items")} == set(articles)
    assert conn.execute("SELECT item_count,ingest_mode FROM source_files").fetchone() == (len(articles), ingest_mode)
    assert conn.execute("SELECT COUNT(*) FROM item_classifications").fetchone()[0] == len(articles)
    assert conn.execute("SELECT COUNT(*) FROM topic_ingestions").fetchone()[0] == len(articles)
    assert conn.execute("SELECT read_at,updated_at FROM item_state WHERE item_id=?", (retained_id,)).fetchone() == ("read", "before")
    assert md_path.read_bytes() == original_input
    unchanged = reindex(conn, tmp_path)
    assert unchanged.changed_files == unchanged.upserted == unchanged.skipped_videos == 0


def test_reindex_records_video_only_file_without_repeated_import(tmp_path):
    path = tmp_path / "dailyFreshNews_2026-05-25.md"
    make_daily(path, "合成视频", "https://www.bloomberg.com/news/videos/2026-05-25/only")
    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    stats = reindex(conn, tmp_path)
    assert stats.skipped_videos == 1 and stats.upserted == 0
    assert stats.classification_queued == stats.topics_queued == 0
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert conn.execute("SELECT item_count FROM source_files").fetchone()[0] == 0
    assert reindex(conn, tmp_path).changed_files == 0


def test_reindex_ignores_copy_filenames_and_continues_to_new_dates(tmp_path: Path):
    daily = tmp_path / "2026年10月"
    daily.mkdir()
    current = daily / "dailyFreshNews_2026-10-18.md"
    following = daily / "dailyFreshNews_2026-10-19.md"
    make_daily(current, "原有新闻", "https://example.com/original")
    conn = sqlite3.connect(":memory:")
    apply_schema(conn, Path(__file__).resolve().parents[1] / "schema.sql")
    reindex(conn, tmp_path)

    make_daily(current, "更新后的新闻", "https://example.com/original")
    make_daily(following, "次日新闻", "https://example.com/new")
    copies = [daily / name for name in (
        "dailyFreshNews_2026-10-18 2.md",
        "dailyFreshNews_2026-10-18 (conflicted copy).md",
        "dailyFreshNews_2026-10-18.backup.md",
    )]
    for copy in copies:
        copy.write_text("副本保留，不参与索引", encoding="utf-8")
    (daily / "dailyFreshNews_2026-10-20.md").mkdir()

    assert list_daily_files(tmp_path) == [current, following]
    stats = reindex(conn, tmp_path)
    assert stats.scanned_files == stats.changed_files == 2
    assert [row[0] for row in conn.execute("SELECT title FROM items ORDER BY source_file")] == ["更新后的新闻", "次日新闻"]
    assert reindex(conn, tmp_path).changed_files == 0
    assert all(copy.read_text(encoding="utf-8") == "副本保留，不参与索引" for copy in copies)


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
