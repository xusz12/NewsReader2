from __future__ import annotations

import sqlite3
from datetime import datetime

import news_classification
import news_versions
import report_reader
from feed_sources import derive_source_key, visible_news_sql


ITEM_DATE_SQL = "COALESCE(NULLIF(items.date, ''), substr(items.published_at, 1, 10))"
NEWS_SORT_ORDERS = {"default", "reverse"}


def parse_news_sort_order(args) -> str:
    sort_order = (args.get("sort_order") or "default").strip().lower()
    if sort_order not in NEWS_SORT_ORDERS:
        raise ValueError("invalid_sort_order")
    return sort_order


def build_note_preview(note_text: str | None, limit: int = 120) -> str:
    if not isinstance(note_text, str):
        return ""
    compact = " ".join(note_text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def reader_subject(conn, item_id):
    if item_id.startswith(report_reader.ID_PREFIX):
        return report_reader.subject(conn, item_id)
    return conn.execute("SELECT id, title, url, source, published_at FROM items WHERE id=?", (item_id,)).fetchone()


def serialize_news_rows(items: list[dict], market_tags_map: dict[str, list[dict]], conn) -> list[dict]:
    if not items:
        return items
    news_classification.attach(conn, items)
    news_versions.attach(conn, items)
    # Batch comment counts and respect aggregate edits from the original reader.
    urls = list({item.get("url") for item in items if item.get("has_note") and item.get("url")})
    entries: dict[str, list[str]] = {}
    if urls:
        for offset in range(0, len(urls), 500):
            batch = urls[offset:offset + 500]
            for row in conn.execute("SELECT url, note FROM article_note_entries WHERE url IN (" + ",".join("?" for _ in batch) + ") ORDER BY created_at, rowid", batch):
                entries.setdefault(row["url"], []).append(row["note"])
    for item in items:
        notes = entries.get(item.get("url"), [])
        structured = notes and "\n\n---\n".join(notes) == item.get("note_preview_source")
        item["note_count"] = (len(notes) if structured else 1) if item.get("has_note") else 0
        item["latest_note_preview"] = build_note_preview(notes[-1] if structured else item.get("note_preview_source"))
        tags = market_tags_map.get(item.get("url") or "", [])
        item["note_preview"] = build_note_preview(item.pop("note_preview_source", None))
        item["market_tags"] = tags
        item["has_market_tags"] = 1 if tags else 0
        item["source_key"] = derive_source_key(item.get("url"), item.get("source_type"), item.get("source"))
        date_key, date_label = derive_date_meta(item.get("published_at"), item.get("date"))
        item["date_key"] = date_key
        item["date_label"] = date_label
    return items


def load_news_item_map(conn: sqlite3.Connection, item_ids: list[str]) -> dict[str, dict]:
    ordered_ids = [item_id for item_id in item_ids if item_id]
    if not ordered_ids:
        return {}
    placeholders = ",".join(["?"] * len(ordered_ids))
    rows = conn.execute(
        f"""
        SELECT items.id, items.source_file, items.item_order, items.published_at,
               items.date, items.time, items.source, items.source_type,
               items.source_name, items.title, items.summary, items.url,
               st.read_at, st.important_at, st.read_later_at, st.read_later_done_at, st.favorite_at,
               dj.status AS detail_status,
               dj.last_error AS detail_error,
               CASE WHEN ad.url IS NULL THEN 0 ELSE 1 END AS detail_ready,
               an.note AS note_preview_source,
               CASE WHEN an.url IS NULL THEN 0 ELSE 1 END AS has_note,
               aj.status AS ai_status,
               aj.last_error AS ai_error,
               CASE WHEN aa.url IS NULL THEN 0 ELSE 1 END AS ai_ready,
               aa.conclusion_zh AS ai_conclusion_zh,
               {ITEM_DATE_SQL} AS date_key,
               (
                 SELECT COUNT(*)
                 FROM news_reminders nr
                 WHERE nr.item_id = items.id AND nr.status = 'active'
               ) AS active_reminder_count,
               (
                 SELECT COUNT(*)
                 FROM news_reminders nr
                 WHERE nr.item_id = items.id
                   AND nr.status = 'active'
                   AND nr.remind_at <= datetime('now', 'localtime')
               ) AS due_reminder_count,
               (
                 SELECT MIN(nr.remind_at)
                 FROM news_reminders nr
                 WHERE nr.item_id = items.id AND nr.status = 'active'
               ) AS next_remind_at
        FROM items
        LEFT JOIN item_state st ON st.item_id = items.id
        LEFT JOIN news_item_detail_jobs dj ON dj.item_id=items.id
        LEFT JOIN news_item_details ad ON ad.item_id=items.id
        LEFT JOIN article_notes an ON an.url = items.url
        LEFT JOIN news_item_ai_jobs aj ON aj.item_id=items.id
        LEFT JOIN news_item_ai aa ON aa.item_id=items.id
        WHERE items.id IN ({placeholders})
          AND {visible_news_sql()}
        """,
        ordered_ids,
    ).fetchall()
    urls = [row["url"] for row in rows if row["url"]]
    market_tags_map = load_market_tags_map(conn, urls)
    serialized = serialize_news_rows([dict(row) for row in rows], market_tags_map, conn)
    return {item["id"]: item for item in serialized}


def load_market_tags_map(conn: sqlite3.Connection, urls: list[str]) -> dict[str, list[dict]]:
    if not urls:
        return {}
    uniq_urls = [u for u in dict.fromkeys(urls) if u]
    if not uniq_urls:
        return {}
    placeholders = ",".join(["?"] * len(uniq_urls))
    rows = conn.execute(
        f"""
        SELECT amt.url,
               amt.tag,
               amt.direction,
               amt.created_at,
               amt.updated_at,
               mtd.display_name
        FROM article_market_tags amt
        LEFT JOIN market_tag_definitions mtd ON mtd.key = amt.tag
        WHERE amt.url IN ({placeholders})
        ORDER BY amt.updated_at DESC, amt.tag ASC
        """,
        uniq_urls,
    ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["url"], []).append(
            {
                "key": r["tag"],
                "tag": r["display_name"] or r["tag"],
                "direction": r["direction"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return grouped


def derive_date_meta(published_at: str | None, raw_date: str | None) -> tuple[str, str]:
    date_key = (raw_date or "").strip()
    if not date_key and published_at:
        date_key = str(published_at).strip()[:10]
    if not date_key:
        return ("unknown", "未知日期")

    try:
        day = datetime.strptime(date_key, "%Y-%m-%d").date()
    except ValueError:
        return (date_key, date_key)

    today = datetime.now().date()
    if day == today:
        return (date_key, "今天")
    if day == today.fromordinal(today.toordinal() - 1):
        return (date_key, "昨天")
    return (date_key, f"{day.year}年{day.month}月{day.day}日")


def derive_ts_date_meta(timestamp: str | None) -> tuple[str, str]:
    normalized = (timestamp or "").strip()
    if len(normalized) >= 10:
        return normalized[:10], normalized[:10]
    return "unknown", "未知日期"
