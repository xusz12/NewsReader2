from __future__ import annotations

import json
from pathlib import Path

from parser import parse_daily_file, parse_daily_json_file


def write_daily(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_parse_extracts_url_and_fields(tmp_path: Path):
    p = tmp_path / "dailyFreshNews_2026-05-25.md"
    write_daily(
        p,
        """## Reuters · World（1条）

### [测试新闻](https://example.com/a)
- 发布时间：2026-05-25 11:22:33
- 摘要：这是一条摘要
""",
    )
    items = parse_daily_file(p)
    assert len(items) == 1
    it = items[0]
    assert it.title == "测试新闻"
    assert it.url == "https://example.com/a"
    assert it.time == "11:22"
    assert it.published_at == "2026-05-25 11:22"
    assert it.summary == "这是一条摘要"


def test_parse_ignores_errors_and_empty_sections(tmp_path: Path):
    p = tmp_path / "dailyFreshNews_2026-05-25.md"
    write_daily(
        p,
        """## Reuters · World（1条）
### [保留新闻](https://example.com/ok)
- 发布时间：2026-05-25 09:00:00

## 本次无更新的分组（1个）
### [不应保留](https://example.com/skip)
- 发布时间：2026-05-25 09:01:00

## errors
### [也不应保留](https://example.com/err)
- 发布时间：2026-05-25 09:02:00
""",
    )
    items = parse_daily_file(p)
    assert len(items) == 1
    assert items[0].title == "保留新闻"


def test_parse_json_file_extracts_structured_fields(tmp_path: Path):
    p = tmp_path / "dailyFreshNews_2026-05-25.newsreader.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": "newsreader.daily.v1",
                "items": [
                    {
                        "item_order": 7,
                        "section": "Twitter · Evan",
                        "source_type": "twitter",
                        "source_name": "Evan",
                        "title": "结构化新闻",
                        "summary": "JSON 摘要",
                        "published_at": "2026-05-25 12:34:56",
                        "url": "https://x.com/evan/status/1",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    items = parse_daily_json_file(p)
    assert len(items) == 1
    it = items[0]
    assert it.item_order == 7
    assert it.source == "Twitter · Evan"
    assert it.source_type == "twitter"
    assert it.source_name == "Evan"
    assert it.title == "结构化新闻"
    assert it.summary == "JSON 摘要"
    assert it.published_at == "2026-05-25 12:34"
    assert it.time == "12:34"


def test_parse_json_file_rejects_unsupported_schema(tmp_path: Path):
    p = tmp_path / "dailyFreshNews_2026-05-25.newsreader.json"
    p.write_text(json.dumps({"schema_version": "newsreader.daily.v0", "items": []}), encoding="utf-8")
    try:
        parse_daily_json_file(p)
    except ValueError as exc:
        assert str(exc) == "unsupported_newsreader_daily_schema"
    else:  # pragma: no cover
        raise AssertionError("expected unsupported schema to fail")
