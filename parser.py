from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SECTION_RE = re.compile(r"^##\s+(.+?)(?:（\d+条）)?\s*$")
ITEM_TITLE_RE = re.compile(r"^###\s+\[(.+?)\]\((https?://.+?)\)\s*$")
TIME_RE = re.compile(r"^- 发布时间：(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?\s*$")
SUMMARY_RE = re.compile(r"^- 摘要：\s*(.+?)\s*$")
ERROR_ITEM_RE = re.compile(r"^###\s+(?:\d+\.\s*)?(.+?)\s*$")
ERROR_TIME_RE = re.compile(r"^- 抓取时间：(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*$")
IGNORE_SECTIONS = {"本次无更新的分组", "errors"}
DAILY_FILE_RE = re.compile(r"dailyFreshNews_(\d{4}-\d{2}-\d{2})\.md$")
JSON_SCHEMA_VERSION = "newsreader.daily.v1"
TWITTER_SECTION_RE = re.compile(r"^(Twitter|X)\s*[·•\-]\s*(.+)$", re.IGNORECASE)
SOCIAL_SOURCE_NAMES = {
    "Ilya Sutskever",
    "郭明錤",
    "seekinganythingbutalpha",
    "外汇交易员",
    "Time Horizon",
    "卡比卡比",
}


@dataclass
class ParsedItem:
    item_order: int
    date: str
    time: str
    published_at: str
    source: str
    source_type: str | None
    source_name: str | None
    title: str
    summary: str | None
    url: str


def sidecar_path_for_daily(path: Path) -> Path:
    return path.with_suffix(".newsreader.json")


def daily_path_for_sidecar(path: Path) -> Path:
    if path.name.endswith(".newsreader.json"):
        return path.with_name(path.name[: -len(".newsreader.json")] + ".md")
    return path


def extract_date_from_filename(path: Path) -> str:
    m = DAILY_FILE_RE.search(path.name)
    if not m:
        raise ValueError(f"unsupported daily file name: {path.name}")
    return m.group(1)


def normalize_source_meta(section_name: str) -> tuple[str | None, str | None]:
    m = TWITTER_SECTION_RE.match(section_name)
    if m:
        return "twitter", m.group(2).strip()
    if section_name.strip() in SOCIAL_SOURCE_NAMES:
        return "twitter", section_name.strip()
    return None, None


def _normalize_published_parts(raw_value: object, default_date: str) -> tuple[str, str, str]:
    text = str(raw_value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return default_date, "00:00", f"{default_date} 00:00"


def _json_item_source_payload(item: dict) -> tuple[str, str | None, str | None]:
    source = str(item.get("section") or item.get("source") or "").strip()
    source_type = str(item.get("source_type") or "").strip() or None
    source_name = str(item.get("source_name") or "").strip() or None
    if not source:
        if source_type == "twitter" and source_name:
            source = f"Twitter · {source_name}"
        else:
            source = source_name or "未知来源"
    if source_type or source_name:
        return source, source_type, source_name
    normalized_type, normalized_name = normalize_source_meta(source)
    return source, normalized_type, normalized_name


def parse_daily_file(path: Path) -> list[ParsedItem]:
    date = extract_date_from_filename(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[ParsedItem] = []
    current_section: str | None = None
    current_ignore = False
    pending: dict | None = None
    order = 0

    def flush_pending() -> None:
        nonlocal pending, order
        if not pending:
            return
        title = pending.get("title")
        url = pending.get("url")
        if not title or not url:
            pending = None
            return
        raw_date = pending.get("date") or date
        raw_time = pending.get("time") or "00:00"
        published_at = f"{raw_date} {raw_time}"
        order += 1
        source_type, source_name = normalize_source_meta(pending["source"])
        items.append(
            ParsedItem(
                item_order=order,
                date=raw_date,
                time=raw_time,
                published_at=published_at,
                source=pending["source"],
                source_type=source_type,
                source_name=source_name,
                title=title,
                summary=pending.get("summary"),
                url=url,
            )
        )
        pending = None

    for line in lines:
        sec_m = SECTION_RE.match(line)
        if sec_m:
            flush_pending()
            section_name = sec_m.group(1).strip()
            current_section = section_name
            current_ignore = any(section_name.startswith(p) for p in IGNORE_SECTIONS)
            continue

        if current_ignore or not current_section:
            continue

        item_m = ITEM_TITLE_RE.match(line)
        if item_m:
            flush_pending()
            pending = {
                "source": current_section,
                "title": item_m.group(1).strip(),
                "url": item_m.group(2).strip(),
            }
            continue

        if pending is None:
            continue

        time_m = TIME_RE.match(line)
        if time_m:
            pending["date"] = time_m.group(1)
            pending["time"] = time_m.group(2)
            continue

        summary_m = SUMMARY_RE.match(line)
        if summary_m:
            pending["summary"] = summary_m.group(1).strip()
            continue

        if line.strip() == "---":
            flush_pending()

    flush_pending()

    # Normalize unexpected date/time values defensively.
    for idx, item in enumerate(items):
        try:
            dt = datetime.strptime(item.published_at, "%Y-%m-%d %H:%M")
            items[idx].published_at = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            items[idx].published_at = f"{date} 00:00"
            items[idx].time = "00:00"
            items[idx].date = date

    return items


def parse_daily_json_file(path: Path) -> list[ParsedItem]:
    default_date = extract_date_from_filename(daily_path_for_sidecar(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_newsreader_daily_json")
    if str(payload.get("schema_version") or "").strip() != JSON_SCHEMA_VERSION:
        raise ValueError("unsupported_newsreader_daily_schema")
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise ValueError("invalid_newsreader_daily_items")

    items: list[ParsedItem] = []
    order = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url:
            raise ValueError("missing_newsreader_daily_required_fields")
        item_order = row.get("item_order")
        if isinstance(item_order, int) and item_order > 0:
            order = item_order
        else:
            order += 1
        date, time, published_at = _normalize_published_parts(row.get("published_at"), default_date)
        source, source_type, source_name = _json_item_source_payload(row)
        summary = str(row.get("summary") or "").strip() or None
        items.append(
            ParsedItem(
                item_order=order,
                date=date,
                time=time,
                published_at=published_at,
                source=source,
                source_type=source_type,
                source_name=source_name,
                title=title,
                summary=summary,
                url=url,
            )
        )
    if rows and not items:
        raise ValueError("missing_newsreader_daily_required_fields")
    return items


def parse_daily_errors(path: Path) -> list[dict[str, str]]:
    date = extract_date_from_filename(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[dict[str, str]] = []
    inside_errors = False
    current_label: str | None = None

    def flush_pending(time_date: str | None, time_text: str | None) -> None:
        if current_label and time_text:
            entries.append({
                "date": time_date or date,
                "label": current_label,
                "time": time_text,
            })

    pending_date: str | None = None
    pending_time: str | None = None

    for line in lines:
        sec_m = SECTION_RE.match(line)
        if sec_m:
            if inside_errors:
                flush_pending(pending_date, pending_time)
                pending_date = None
                pending_time = None
            inside_errors = sec_m.group(1).strip().lower() == "errors"
            current_label = None
            continue

        if not inside_errors:
            continue

        item_m = ERROR_ITEM_RE.match(line)
        if item_m:
            flush_pending(pending_date, pending_time)
            current_label = item_m.group(1).strip()
            pending_date = None
            pending_time = None
            continue

        time_m = ERROR_TIME_RE.match(line)
        if time_m and current_label:
            pending_date = time_m.group(1)
            pending_time = time_m.group(2)

    flush_pending(pending_date, pending_time)
    return entries
