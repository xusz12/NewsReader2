from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from flask import jsonify, request

from feed_sources import derive_source_key, visible_news_sql
from nr import database, ideas, reader_items


DEFAULT_MARKET_TAG_CHOICES = [
    "存储",
    "电力",
    "房地产",
    "APPLE",
    "AI",
    "电子消费",
    "医疗",
    "国际形势",
    "稀土",
    "矿产",
    "新能源",
    "中国资产",
]
MARKET_DIRECTIONS = {"bullish", "bearish"}
MARKET_WORKBENCH_CONTENT_FILTERS = {"all", "ideas", "bullish", "bearish"}
MARKET_PIN_NOTE_MAX_LEN = 5000


def load_market_tag_definitions(conn: sqlite3.Connection, active_only: bool = False) -> list[dict]:
    where_sql = "WHERE mtd.active=1" if active_only else ""
    rows = conn.execute(
        f"""
        SELECT mtd.key,
               mtd.display_name,
               mtd.active,
               mtd.sort_order,
               mtd.created_at,
               mtd.updated_at,
               COALESCE(amt.item_tag_count, 0) AS item_tag_count,
               COALESCE(mtn.trend_note_count, 0) AS trend_note_count
        FROM market_tag_definitions mtd
        LEFT JOIN (
            SELECT tag, COUNT(*) AS item_tag_count
            FROM article_market_tags
            GROUP BY tag
        ) amt ON amt.tag = mtd.key
        LEFT JOIN (
            SELECT tag, COUNT(*) AS trend_note_count
            FROM market_trend_notes
            GROUP BY tag
        ) mtn ON mtn.tag = mtd.key
        {where_sql}
        ORDER BY mtd.sort_order ASC, mtd.created_at ASC, mtd.key ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def load_market_tag_definition_map(conn: sqlite3.Connection) -> dict[str, dict]:
    return {row["key"]: row for row in load_market_tag_definitions(conn, active_only=False)}


def normalize_market_tag_sort_order(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT key
        FROM market_tag_definitions
        ORDER BY sort_order ASC, created_at ASC, key ASC
        """
    ).fetchall()
    ts = database.now_ts()
    for idx, row in enumerate(rows):
        conn.execute(
            "UPDATE market_tag_definitions SET sort_order=?, updated_at=? WHERE key=?",
            (idx, ts, row["key"]),
        )


def seed_market_tag_definitions(conn: sqlite3.Connection) -> None:
    deleted_keys = {
        row["key"]
        for row in conn.execute("SELECT key FROM market_tag_deleted_keys").fetchall()
    }
    existing = {
        row["key"]: row["display_name"]
        for row in conn.execute("SELECT key, display_name FROM market_tag_definitions").fetchall()
    }
    ts = database.now_ts()
    with conn:
        for idx, tag in enumerate(DEFAULT_MARKET_TAG_CHOICES):
            if tag in deleted_keys:
                continue
            if tag in existing:
                continue
            conn.execute(
                """
                INSERT INTO market_tag_definitions(key, display_name, active, sort_order, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (tag, tag, idx, ts, ts),
            )


def migrate_market_trend_notes(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_trend_notes'"
    ).fetchone()
    if not exists:
        return

    index_rows = conn.execute("PRAGMA index_list('market_trend_notes')").fetchall()
    legacy_unique_index = None
    for row in index_rows:
        if int(row["unique"] or 0) != 1:
            continue
        index_name = row["name"]
        cols = [
            info["name"]
            for info in conn.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        ]
        if cols == ["date_key", "tag", "direction"]:
            legacy_unique_index = index_name
            break
    if not legacy_unique_index:
        return

    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_trend_notes_v2 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              date_key TEXT NOT NULL,
              tag TEXT NOT NULL,
              direction TEXT NOT NULL,
              note TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO market_trend_notes_v2(id, date_key, tag, direction, note, created_at, updated_at)
            SELECT id, date_key, tag, direction, note, created_at, updated_at
            FROM market_trend_notes
            ORDER BY id ASC
            """
        )
        conn.execute("DROP TABLE market_trend_notes")
        conn.execute("ALTER TABLE market_trend_notes_v2 RENAME TO market_trend_notes")


def create_market_tag_key(conn: sqlite3.Connection, display_name: str) -> str:
    base = display_name.strip()
    candidate = base
    suffix = 2
    while (
        conn.execute("SELECT 1 FROM market_tag_definitions WHERE key=?", (candidate,)).fetchone()
        or conn.execute("SELECT 1 FROM market_tag_deleted_keys WHERE key=?", (candidate,)).fetchone()
    ):
        candidate = f"{base} ({suffix})"
        suffix += 1
    return candidate


def market_tag_impact_counts(conn: sqlite3.Connection, tag_key: str) -> dict[str, int]:
    return {
        "item_tag_count": int(
            conn.execute("SELECT COUNT(*) FROM article_market_tags WHERE tag=?", (tag_key,)).fetchone()[0] or 0
        ),
        "trend_note_count": int(
            conn.execute("SELECT COUNT(*) FROM market_trend_notes WHERE tag=?", (tag_key,)).fetchone()[0] or 0
        ),
    }


def parse_market_workbench_content_filter(value: str | None) -> str:
    normalized = (value or "all").strip().lower()
    if normalized not in MARKET_WORKBENCH_CONTENT_FILTERS:
        raise ValueError("invalid_market_workbench_content_filter")
    return normalized


def market_workbench_filter_sql_parts(content_filter: str) -> tuple[str, str]:
    if content_filter == "ideas":
        return (
            "AND an.url IS NOT NULL",
            "",
        )
    if content_filter == "bullish":
        return (
            "AND mt.direction = 'bullish'",
            "AND mtn.direction = 'bullish'",
        )
    if content_filter == "bearish":
        return (
            "AND mt.direction = 'bearish'",
            "AND mtn.direction = 'bearish'",
        )
    return ("", "")


def load_market_workbench_overview(conn: sqlite3.Connection, content_filter: str) -> list[dict]:
    item_filter_sql, note_filter_sql = market_workbench_filter_sql_parts(content_filter)
    rows = conn.execute(
        f"""
        SELECT
          mtd.key,
          mtd.display_name,
          mtd.active,
          mtd.sort_order,
          COALESCE(total_items.total, 0) AS total_items,
          COALESCE(recent_items.recent_total, 0) AS recent_total,
          COALESCE(recent_items.bullish_total, 0) AS bullish_total,
          COALESCE(recent_items.bearish_total, 0) AS bearish_total,
          COALESCE(total_notes.total, 0) AS trend_note_total,
          COALESCE(recent_notes.recent_total, 0) AS recent_note_total,
          recent_items.latest_published_at,
          recent_notes.latest_note_updated_at
        FROM market_tag_definitions mtd
        LEFT JOIN (
          SELECT mt.tag, COUNT(*) AS total
          FROM article_market_tags mt
          JOIN items ON items.url = mt.url
          LEFT JOIN article_notes an ON an.url = mt.url
          WHERE {visible_news_sql()} {item_filter_sql}
          GROUP BY mt.tag
        ) total_items ON total_items.tag = mtd.key
        LEFT JOIN (
          SELECT mt.tag,
                 COUNT(*) AS recent_total,
                 SUM(CASE WHEN mt.direction = 'bullish' THEN 1 ELSE 0 END) AS bullish_total,
                 SUM(CASE WHEN mt.direction = 'bearish' THEN 1 ELSE 0 END) AS bearish_total,
                 MAX(items.published_at) AS latest_published_at
          FROM article_market_tags mt
          JOIN items ON items.url = mt.url
          LEFT JOIN article_notes an ON an.url = mt.url
          WHERE items.published_at >= datetime('now', '-30 days')
            AND {visible_news_sql()}
            {item_filter_sql}
          GROUP BY mt.tag
        ) recent_items ON recent_items.tag = mtd.key
        LEFT JOIN (
          SELECT mtn.tag, COUNT(*) AS total
          FROM market_trend_notes mtn
          WHERE 1=1 {note_filter_sql}
          GROUP BY mtn.tag
        ) total_notes ON total_notes.tag = mtd.key
        LEFT JOIN (
          SELECT mtn.tag,
                 COUNT(*) AS recent_total,
                 MAX(mtn.updated_at) AS latest_note_updated_at
          FROM market_trend_notes mtn
          WHERE mtn.updated_at >= datetime('now', '-30 days')
            {note_filter_sql}
          GROUP BY mtn.tag
        ) recent_notes ON recent_notes.tag = mtd.key
        WHERE mtd.active = 1
        ORDER BY COALESCE(recent_items.latest_published_at, recent_notes.latest_note_updated_at, '') DESC,
                 mtd.sort_order ASC,
                 mtd.display_name COLLATE NOCASE ASC
        """
    ).fetchall()
    payload = []
    for row in rows:
        total_items = int(row["total_items"] or 0)
        trend_note_total = int(row["trend_note_total"] or 0)
        if total_items <= 0 and trend_note_total <= 0:
            continue
        payload.append(
            {
                "tag_key": row["key"],
                "tag_label": row["display_name"],
                "active": int(row["active"] or 0),
                "total_items": total_items,
                "recent_total": int(row["recent_total"] or 0),
                "bullish_total": int(row["bullish_total"] or 0),
                "bearish_total": int(row["bearish_total"] or 0),
                "trend_note_total": trend_note_total,
                "recent_note_total": int(row["recent_note_total"] or 0),
                "latest_published_at": row["latest_published_at"] or "",
                "latest_note_updated_at": row["latest_note_updated_at"] or "",
            }
        )
    return payload


def load_market_tag_feed(
    conn: sqlite3.Connection,
    *,
    tag_key: str | None,
    content_filter: str,
    sort_order: str,
    page: int,
    per: int,
) -> dict:
    item_filter_sql, note_filter_sql = market_workbench_filter_sql_parts(content_filter)
    sort_direction = "ASC" if sort_order == "reverse" else "DESC"
    if tag_key:
        union_sql = f"""
        SELECT
          'news' AS row_kind,
          items.id AS ref_id,
          items.published_at AS sort_ts,
          items.id AS tie_breaker
        FROM items
        JOIN article_market_tags mt ON mt.url = items.url
        LEFT JOIN article_notes an ON an.url = items.url
        WHERE mt.tag = ?
          AND {visible_news_sql()}
          {item_filter_sql}
        UNION ALL
        SELECT
          'trend_note' AS row_kind,
          CAST(mtn.id AS TEXT) AS ref_id,
          COALESCE(mtn.updated_at, mtn.created_at, mtn.date_key) AS sort_ts,
          CAST(mtn.id AS TEXT) AS tie_breaker
        FROM market_trend_notes mtn
        WHERE mtn.tag = ?
          {note_filter_sql}
    """
        args = [tag_key, tag_key]
    else:
        union_sql = f"""
        SELECT
          'news' AS row_kind,
          items.id AS ref_id,
          items.published_at AS sort_ts,
          items.id AS tie_breaker
        FROM items
        JOIN article_market_tags mt ON mt.url = items.url
        LEFT JOIN article_notes an ON an.url = items.url
        WHERE {visible_news_sql()}
          {item_filter_sql}
        GROUP BY items.id
        UNION ALL
        SELECT
          'trend_note' AS row_kind,
          CAST(mtn.id AS TEXT) AS ref_id,
          COALESCE(mtn.updated_at, mtn.created_at, mtn.date_key) AS sort_ts,
          CAST(mtn.id AS TEXT) AS tie_breaker
        FROM market_trend_notes mtn
        WHERE 1=1
          {note_filter_sql}
        """
        args = []
    total = int(conn.execute(f"SELECT COUNT(*) FROM ({union_sql}) feed_rows", args).fetchone()[0] or 0)
    offset = (page - 1) * per
    page_rows = conn.execute(
        f"""
        SELECT row_kind, ref_id
        FROM ({union_sql}) feed_rows
        ORDER BY sort_ts {sort_direction}, tie_breaker {sort_direction}
        LIMIT ? OFFSET ?
        """,
        [*args, per, offset],
    ).fetchall()
    news_ids = [row["ref_id"] for row in page_rows if row["row_kind"] == "news"]
    trend_note_ids = [int(row["ref_id"]) for row in page_rows if row["row_kind"] == "trend_note"]

    news_map: dict[str, dict] = {}
    if news_ids:
        placeholders = ",".join(["?"] * len(news_ids))
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
                   ) AS next_remind_at,
                   {reader_items.ITEM_DATE_SQL} AS date_key
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
            news_ids,
        ).fetchall()
        urls = [row["url"] for row in rows if row["url"]]
        market_tags_map = reader_items.load_market_tags_map(conn, urls)
        serialized = reader_items.serialize_news_rows([dict(row) for row in rows], market_tags_map, conn)
        news_map = {str(item["id"]): item for item in serialized}

    trend_note_map: dict[int, dict] = {}
    if trend_note_ids:
        placeholders = ",".join(["?"] * len(trend_note_ids))
        rows = conn.execute(
            f"""
            SELECT mtn.id,
                   mtn.date_key,
                   mtn.tag,
                   mtn.direction,
                   mtn.note,
                   mtn.created_at,
                   mtn.updated_at,
                   mtd.display_name
            FROM market_trend_notes mtn
            LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
            WHERE mtn.id IN ({placeholders})
            """,
            trend_note_ids,
        ).fetchall()
        for row in rows:
            updated_at = row["updated_at"] or ""
            date_key, date_label = reader_items.derive_ts_date_meta(updated_at)
            trend_note_map[int(row["id"])] = {
                "id": None,
                "idea_id": f"trend:{row['id']}",
                "idea_type": "trend_note",
                "idea_context_label": "板块想法",
                "trend_note_id": row["id"],
                "trend_date_key": row["date_key"],
                "tag_key": row["tag"],
                "tag_label": row["display_name"] or row["tag"],
                "direction": row["direction"],
                "direction_label": "看多" if row["direction"] == "bullish" else "看空",
                "title": f"{row['display_name'] or row['tag']} · {'看多' if row['direction'] == 'bullish' else '看空'}",
                "summary": f"{row['date_key']} · {row['display_name'] or row['tag']}",
                "source": "趋势",
                "published_at": row["date_key"],
                "url": "",
                "note": row["note"],
                "note_preview": reader_items.build_note_preview(row["note"]),
                "created_at": row["created_at"] or "",
                "updated_at": updated_at,
                "date_key": date_key,
                "date_label": date_label,
            }

    items: list[dict] = []
    for row in page_rows:
        if row["row_kind"] == "news":
            item = news_map.get(str(row["ref_id"]))
            if item:
                item["entry_type"] = "news"
                items.append(item)
        else:
            note = trend_note_map.get(int(row["ref_id"]))
            if note:
                note["entry_type"] = "trend_note"
                items.append(note)
    pages = max(1, math.ceil(total / per)) if total else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
        "has_more": page < pages,
    }


def market_pin_scope_and_tag(tag_key: str | None) -> tuple[str, str]:
    normalized = (tag_key or "").strip()
    return ("overview", "") if not normalized else ("tag", normalized)


def load_market_pinned_note(conn: sqlite3.Connection, tag_key: str | None) -> dict:
    scope, normalized_tag_key = market_pin_scope_and_tag(tag_key)
    row = conn.execute(
        """
        SELECT scope, tag_key, note, collapsed, created_at, updated_at
        FROM market_pinned_notes
        WHERE scope = ? AND tag_key = ?
        """,
        (scope, normalized_tag_key),
    ).fetchone()
    return {
        "scope": scope,
        "tag_key": normalized_tag_key,
        "note": (row["note"] if row else "") or "",
        "collapsed": int((row["collapsed"] if row else 0) or 0),
        "created_at": row["created_at"] if row else None,
        "updated_at": row["updated_at"] if row else None,
    }


def serialize_market_pinned_note(pin_row: dict, tag_def: dict | None = None) -> dict:
    scope = pin_row.get("scope") or "overview"
    tag_key = pin_row.get("tag_key") or ""
    tag_label = tag_def["display_name"] if tag_def else ""
    return {
        "scope": scope,
        "tag_key": tag_key,
        "tag_label": tag_label,
        "title": "板块集合置顶" if scope == "overview" else f"{tag_label or tag_key} · 置顶信息",
        "scope_label": "全部板块" if scope == "overview" else (tag_label or tag_key),
        "note": pin_row.get("note") or "",
        "collapsed": int(pin_row.get("collapsed") or 0),
        "updated_at": pin_row.get("updated_at"),
        "created_at": pin_row.get("created_at"),
    }


def register(app, db):
    @app.get("/api/market-trends")
    def api_market_trends():
        try:
            days = int(request.args.get("days", "7"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_days"}), 400
        days = min(30, max(3, days))

        conn = db.connect()
        try:
            active_tags = load_market_tag_definitions(conn, active_only=True)
            date_rows = conn.execute(
                f"""
                SELECT date_key
                FROM (
                  SELECT DISTINCT {reader_items.ITEM_DATE_SQL} AS date_key
                  FROM items
                  WHERE EXISTS (SELECT 1 FROM article_market_tags mt WHERE mt.url = items.url)
                    AND {visible_news_sql()}
                    AND {reader_items.ITEM_DATE_SQL} IS NOT NULL
                    AND {reader_items.ITEM_DATE_SQL} != ''
                  UNION
                  SELECT DISTINCT date_key
                  FROM market_trend_notes
                )
                ORDER BY date_key DESC
                LIMIT ?
                """,
                (days,),
            ).fetchall()
            latest_dates_desc = [r["date_key"] for r in date_rows]
            if not latest_dates_desc:
                return jsonify({
                    "ok": True,
                    "days": days,
                    "dates": [],
                    "rows": [],
                    "tag_count": 0,
                    "tagged_item_count": 0,
                })

            latest_dates = list(reversed(latest_dates_desc))
            placeholders = ",".join(["?"] * len(latest_dates_desc))
            agg_rows = conn.execute(
                f"""
                SELECT {reader_items.ITEM_DATE_SQL} AS date_key,
                       mt.tag,
                       mt.direction,
                       COUNT(*) AS total,
                       MAX(CASE WHEN an.url IS NULL THEN 0 ELSE 1 END) AS has_item_note
                FROM items
                JOIN article_market_tags mt ON mt.url = items.url
                LEFT JOIN article_notes an ON an.url = items.url
                WHERE {reader_items.ITEM_DATE_SQL} IN ({placeholders})
                  AND {visible_news_sql()}
                GROUP BY date_key, mt.tag, mt.direction
                ORDER BY date_key ASC, mt.tag ASC, mt.direction ASC
                """,
                latest_dates_desc,
            ).fetchall()
            note_rows = conn.execute(
                f"""
                SELECT date_key, tag, direction, COUNT(*) AS total
                FROM market_trend_notes
                WHERE date_key IN ({placeholders})
                GROUP BY date_key, tag, direction
                ORDER BY date_key ASC, tag ASC, direction ASC
                """,
                latest_dates_desc,
            ).fetchall()
            tagged_item_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM items
                WHERE EXISTS (SELECT 1 FROM article_market_tags mt WHERE mt.url = items.url)
                  AND {visible_news_sql()}
                  AND {reader_items.ITEM_DATE_SQL} IN ({placeholders})
                """,
                latest_dates_desc,
            ).fetchone()[0]
        finally:
            conn.close()

        counts: dict[tuple[str, str], dict[str, int]] = {}
        item_note_flags: dict[tuple[str, str], dict[str, int]] = {}
        note_counts: dict[tuple[str, str], dict[str, int]] = {}
        non_zero_tags: set[str] = set()
        for row in agg_rows:
            date_key = row["date_key"]
            tag = row["tag"]
            direction = row["direction"]
            total = int(row["total"] or 0)
            non_zero_tags.add(tag)
            slot = counts.setdefault((tag, date_key), {"bullish": 0, "bearish": 0})
            slot[direction] = total
            note_flag_slot = item_note_flags.setdefault((tag, date_key), {"bullish": 0, "bearish": 0})
            note_flag_slot[direction] = int(row["has_item_note"] or 0)
        for row in note_rows:
            date_key = row["date_key"]
            tag = row["tag"]
            direction = row["direction"]
            total = int(row["total"] or 0)
            non_zero_tags.add(tag)
            slot = note_counts.setdefault((tag, date_key), {"bullish": 0, "bearish": 0})
            slot[direction] = total

        tags_in_use = [tag for tag in active_tags if tag["key"] in non_zero_tags]
        rows_payload = []
        for tag_def in tags_in_use:
            tag_key = tag_def["key"]
            values = []
            row_total = 0
            for date_key in latest_dates:
                slot = counts.get((tag_key, date_key), {"bullish": 0, "bearish": 0})
                note_slot = note_counts.get((tag_key, date_key), {"bullish": 0, "bearish": 0})
                item_note_slot = item_note_flags.get((tag_key, date_key), {"bullish": 0, "bearish": 0})
                bullish = int(slot.get("bullish") or 0)
                bearish = int(slot.get("bearish") or 0)
                bullish_notes = int(note_slot.get("bullish") or 0)
                bearish_notes = int(note_slot.get("bearish") or 0)
                row_total += bullish + bearish
                values.append(
                    {
                        "date": date_key,
                        "bullish": bullish,
                        "bearish": bearish,
                        "bullish_notes": bullish_notes,
                        "bearish_notes": bearish_notes,
                        "bullish_has_item_note": int(item_note_slot.get("bullish") or 0),
                        "bearish_has_item_note": int(item_note_slot.get("bearish") or 0),
                    }
                )
            rows_payload.append(
                {
                    "tag": tag_key,
                    "tag_key": tag_key,
                    "tag_label": tag_def["display_name"],
                    "values": values,
                    "total": row_total,
                }
            )

        return jsonify(
            {
                "ok": True,
                "days": days,
                "dates": latest_dates,
                "rows": rows_payload,
                "tag_count": len(rows_payload),
                "tagged_item_count": int(tagged_item_count or 0),
            }
        )

    @app.get("/api/market-trends/detail")
    def api_market_trends_detail():
        date_key = (request.args.get("date") or "").strip()
        tag = (request.args.get("tag") or "").strip()
        direction = (request.args.get("direction") or "").strip().lower()
        if not date_key:
            return jsonify({"ok": False, "error": "missing_date"}), 400
        conn = db.connect()
        try:
            tag_def = conn.execute(
                "SELECT key, display_name, active FROM market_tag_definitions WHERE key=?",
                (tag,),
            ).fetchone()
            if not tag_def:
                return jsonify({"ok": False, "error": "invalid_tag"}), 400
            if direction not in MARKET_DIRECTIONS:
                return jsonify({"ok": False, "error": "invalid_direction"}), 400
            rows = conn.execute(
                f"""
                SELECT items.id, items.source_file, items.item_order, items.published_at,
                       items.date, items.time, items.source, items.source_type,
                       items.source_name, items.title, items.summary, items.url,
                       st.read_at, st.important_at, st.read_later_at, st.read_later_done_at, st.favorite_at,
                       CASE WHEN an.url IS NULL THEN 0 ELSE 1 END AS has_note
                FROM items
                JOIN article_market_tags mt ON mt.url = items.url
                LEFT JOIN item_state st ON st.item_id = items.id
                LEFT JOIN article_notes an ON an.url = items.url
                WHERE {reader_items.ITEM_DATE_SQL} = ?
                  AND mt.tag = ?
                  AND mt.direction = ?
                  AND {visible_news_sql()}
                ORDER BY items.published_at ASC, items.id ASC
                """,
                (date_key, tag, direction),
            ).fetchall()
            urls = [r["url"] for r in rows if r["url"]]
            notes_map = ideas.load_notes_map(conn, urls)
            market_tags_map = reader_items.load_market_tags_map(conn, urls)
            trend_notes = ideas.load_trend_notes_map(conn, [(date_key, tag_def["key"], direction)]).get((date_key, tag_def["key"], direction), [])
        finally:
            conn.close()

        items = []
        for row in rows:
            item = dict(row)
            url = item.get("url") or ""
            note = notes_map.get(url)
            tags = market_tags_map.get(url, [])
            item["note"] = note
            item["has_note"] = 1 if note else 0
            item["note_preview"] = reader_items.build_note_preview(note["note"] if note else None)
            item["market_tags"] = tags
            item["has_market_tags"] = 1 if tags else 0
            item["source_key"] = derive_source_key(item.get("url"), item.get("source_type"), item.get("source"))
            key, label = reader_items.derive_date_meta(item.get("published_at"), item.get("date"))
            item["date_key"] = key
            item["date_label"] = label
            items.append(item)

        return jsonify(
            {
                "ok": True,
                "view": "cell",
                "date": date_key,
                "tag": tag_def["display_name"],
                "tag_key": tag_def["key"],
                "direction": direction,
                "trend_notes": trend_notes,
                "trend_note_total": len(trend_notes),
                "total": len(items),
                "items": items,
            }
        )

    @app.get("/api/market-trends/tag-detail")
    def api_market_trends_tag_detail():
        tag = (request.args.get("tag") or "").strip()
        conn = db.connect()
        try:
            tag_def = conn.execute(
                "SELECT key, display_name, active FROM market_tag_definitions WHERE key=?",
                (tag,),
            ).fetchone()
            if not tag_def:
                return jsonify({"ok": False, "error": "invalid_tag"}), 400

            item_rows = conn.execute(
                f"""
                SELECT items.id, items.source_file, items.item_order, items.published_at,
                       items.date, items.time, items.source, items.source_type,
                       items.source_name, items.title, items.summary, items.url,
                       st.read_at, st.important_at, st.read_later_at, st.read_later_done_at, st.favorite_at,
                       amt.direction,
                       CASE WHEN an.url IS NULL THEN 0 ELSE 1 END AS has_note
                FROM items
                JOIN article_market_tags amt ON amt.url = items.url
                LEFT JOIN item_state st ON st.item_id = items.id
                LEFT JOIN article_notes an ON an.url = items.url
                WHERE amt.tag = ?
                  AND {visible_news_sql()}
                ORDER BY {reader_items.ITEM_DATE_SQL} DESC, items.published_at DESC, items.id DESC
                """,
                (tag,),
            ).fetchall()
            urls = [r["url"] for r in item_rows if r["url"]]
            notes_map = ideas.load_notes_map(conn, urls)
            market_tags_map = reader_items.load_market_tags_map(conn, urls)

            trend_note_rows = conn.execute(
                """
            SELECT mtn.id,
                   mtn.date_key,
                   mtn.tag,
                   mtn.direction,
                   mtn.note,
                   mtn.created_at,
                   mtn.updated_at,
                   mtd.display_name
            FROM market_trend_notes mtn
            LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
            WHERE mtn.tag = ?
            ORDER BY mtn.date_key DESC, mtn.updated_at DESC, mtn.id DESC
            """,
                (tag,),
            ).fetchall()
        finally:
            conn.close()

        items = []
        for row in item_rows:
            item = dict(row)
            url = item.get("url") or ""
            note = notes_map.get(url)
            tags = market_tags_map.get(url, [])
            item["note"] = note
            item["has_note"] = 1 if note else 0
            item["note_preview"] = reader_items.build_note_preview(note["note"] if note else None)
            item["market_tags"] = tags
            item["has_market_tags"] = 1 if tags else 0
            item["source_key"] = derive_source_key(item.get("url"), item.get("source_type"), item.get("source"))
            key, label = reader_items.derive_date_meta(item.get("published_at"), item.get("date"))
            item["date_key"] = key
            item["date_label"] = label
            items.append(item)

        trend_notes = []
        for row in trend_note_rows:
            note = {
                "id": row["id"],
                "date_key": row["date_key"],
                "tag_key": row["tag"],
                "tag": row["display_name"] or row["tag"],
                "direction": row["direction"],
                "note": row["note"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            trend_notes.append(note)

        return jsonify(
            {
                "ok": True,
                "view": "tag",
                "tag": tag_def["display_name"],
                "tag_key": tag_def["key"],
                "total": len(items),
                "item_total": len(items),
                "trend_note_total": len(trend_notes),
                "items": items,
                "trend_notes": trend_notes,
            }
        )

    @app.get("/api/market-workbench")
    def api_market_workbench():
        tag_key = (request.args.get("tag") or "").strip()
        try:
            content_filter = parse_market_workbench_content_filter(request.args.get("content_filter"))
            sort_order = reader_items.parse_news_sort_order(request.args)
        except ValueError as exc:
            error = "invalid_market_workbench_content_filter" if str(exc) == "invalid_market_workbench_content_filter" else "invalid_sort_order"
            return jsonify({"ok": False, "error": error}), 400
        page = max(1, int(request.args.get("page", "1")))
        per = min(100, max(10, int(request.args.get("per", "30"))))

        conn = db.connect()
        try:
            tags = load_market_tag_definitions(conn, active_only=True)
            if not tag_key:
                feed = load_market_tag_feed(conn, tag_key=None, content_filter=content_filter, sort_order=sort_order, page=page, per=per)
                pin_payload = serialize_market_pinned_note(load_market_pinned_note(conn, None))
                return jsonify(
                    {
                        "ok": True,
                        "mode": "all",
                        "selected_tag": None,
                        "content_filter": content_filter,
                        "sort_order": sort_order,
                        "tags": tags,
                        "items": feed["items"],
                        "total": feed["total"],
                        "page": feed["page"],
                        "pages": feed["pages"],
                        "has_more": feed["has_more"],
                        "pin": pin_payload,
                    }
                )

            tag_def = next((tag for tag in tags if tag["key"] == tag_key), None)
            if not tag_def:
                return jsonify({"ok": False, "error": "invalid_tag"}), 400

            feed = load_market_tag_feed(conn, tag_key=tag_key, content_filter=content_filter, sort_order=sort_order, page=page, per=per)
            pin_payload = serialize_market_pinned_note(load_market_pinned_note(conn, tag_key), tag_def)
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "mode": "tag",
                "selected_tag": {"key": tag_def["key"], "display_name": tag_def["display_name"]},
                "content_filter": content_filter,
                "sort_order": sort_order,
                "tags": tags,
                "items": feed["items"],
                "total": feed["total"],
                "page": feed["page"],
                "pages": feed["pages"],
                "has_more": feed["has_more"],
                "pin": pin_payload,
            }
        )

    @app.put("/api/market-workbench/pin")
    def api_market_workbench_pin_save():
        body = request.get_json(silent=True) or {}
        tag_key_value = body.get("tag_key")
        if tag_key_value is None:
            normalized_tag_key = ""
        elif isinstance(tag_key_value, str):
            normalized_tag_key = tag_key_value.strip()
        else:
            return jsonify({"ok": False, "error": "invalid_tag"}), 400

        note_value = body.get("note")
        if note_value is None:
            note_text = ""
        elif isinstance(note_value, str):
            note_text = note_value.strip()
        else:
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        if len(note_text) > MARKET_PIN_NOTE_MAX_LEN:
            return jsonify({"ok": False, "error": "note_too_long"}), 400

        collapsed_value = body.get("collapsed", False)
        if isinstance(collapsed_value, bool):
            collapsed = 1 if collapsed_value else 0
        elif collapsed_value in (0, 1):
            collapsed = int(collapsed_value)
        else:
            return jsonify({"ok": False, "error": "invalid_collapsed"}), 400

        conn = db.connect()
        try:
            tag_def = None
            if normalized_tag_key:
                tag_def = conn.execute(
                    "SELECT key, display_name FROM market_tag_definitions WHERE key=? AND active=1",
                    (normalized_tag_key,),
                ).fetchone()
                if not tag_def:
                    return jsonify({"ok": False, "error": "invalid_tag"}), 400

            scope, scope_tag_key = market_pin_scope_and_tag(normalized_tag_key)
            existing = load_market_pinned_note(conn, scope_tag_key)
            ts = database.now_ts()
            created_at = existing.get("created_at") or ts
            with conn:
                conn.execute(
                    """
                INSERT INTO market_pinned_notes(scope, tag_key, note, collapsed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, tag_key) DO UPDATE SET
                  note=excluded.note,
                  collapsed=excluded.collapsed,
                  updated_at=excluded.updated_at
                """,
                    (scope, scope_tag_key, note_text, collapsed, created_at, ts),
                )
            payload = serialize_market_pinned_note(load_market_pinned_note(conn, scope_tag_key), dict(tag_def) if tag_def else None)
        finally:
            conn.close()
        return jsonify({"ok": True, "pin": payload})

    @app.put("/api/market-trend-note")
    @app.put("/api/market-trends/note")
    def api_market_trend_note_create():
        body = request.get_json(silent=True) or {}
        date_key = (body.get("date_key") or body.get("date") or "").strip()
        tag_key = (body.get("tag_key") or body.get("tag") or "").strip()
        direction = (body.get("direction") or "").strip().lower()
        raw_note = body.get("note")
        if not date_key:
            return jsonify({"ok": False, "error": "missing_date"}), 400
        if not isinstance(raw_note, str):
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        if direction not in MARKET_DIRECTIONS:
            return jsonify({"ok": False, "error": "invalid_direction"}), 400

        note_text = raw_note.strip()
        conn = db.connect()
        try:
            tag_def = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=? AND active=1",
                (tag_key,),
            ).fetchone()
            if not tag_def:
                return jsonify({"ok": False, "error": "unsupported_tag"}), 400
            if not note_text:
                return jsonify({"ok": False, "error": "empty_note"}), 400
            ts = database.now_ts()
            with conn:
                cur = conn.execute(
                    """
                INSERT INTO market_trend_notes(date_key, tag, direction, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (date_key, tag_key, direction, note_text, ts, ts),
                )
            note_id = cur.lastrowid
            trend_notes = ideas.load_trend_notes_map(conn, [(date_key, tag_key, direction)]).get((date_key, tag_key, direction), [])
            trend_note = next((note for note in trend_notes if note["id"] == note_id), None)
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "date": date_key,
                "tag": tag_def["display_name"],
                "tag_key": tag_key,
                "direction": direction,
                "trend_note": trend_note,
                "trend_notes": trend_notes,
                "has_trend_note": 1 if trend_notes else 0,
            }
        )

    @app.patch("/api/market-trends/note/<int:note_id>")
    def api_market_trend_note_update(note_id: int):
        body = request.get_json(silent=True) or {}
        raw_note = body.get("note")
        if not isinstance(raw_note, str):
            return jsonify({"ok": False, "error": "invalid_note"}), 400
        note_text = raw_note.strip()
        if not note_text:
            return jsonify({"ok": False, "error": "empty_note"}), 400

        raw_date_key = (body.get("date_key") or body.get("date") or "").strip()
        tag_key = (body.get("tag_key") or body.get("tag") or "").strip()
        direction = (body.get("direction") or "").strip().lower()

        if raw_date_key:
            try:
                date_key = datetime.strptime(raw_date_key, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                return jsonify({"ok": False, "error": "invalid_date"}), 400
        else:
            date_key = ""

        conn = db.connect()
        try:
            existing = conn.execute(
                """
            SELECT mtn.id, mtn.date_key, mtn.tag, mtn.direction, mtd.display_name
            FROM market_trend_notes mtn
            LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
            WHERE mtn.id=?
            """,
                (note_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "note_not_found"}), 404

            new_date_key = date_key or existing["date_key"]
            new_tag_key = tag_key or existing["tag"]
            new_direction = direction or existing["direction"]

            if new_direction not in MARKET_DIRECTIONS:
                return jsonify({"ok": False, "error": "invalid_direction"}), 400

            if not new_date_key:
                return jsonify({"ok": False, "error": "missing_date"}), 400

            tag_def = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=? AND active=1",
                (new_tag_key,),
            ).fetchone()
            if not tag_def:
                # Allow keeping an existing inactive tag; reject only when changing to an invalid tag
                if tag_key and tag_key != existing["tag"]:
                    return jsonify({"ok": False, "error": "unsupported_tag"}), 400
                tag_def = conn.execute(
                    "SELECT key, display_name FROM market_tag_definitions WHERE key=?",
                    (new_tag_key,),
                ).fetchone()
                if not tag_def:
                    tag_def = {"key": new_tag_key, "display_name": None}

            with conn:
                conn.execute(
                    """
                UPDATE market_trend_notes
                SET date_key=?, tag=?, direction=?, note=?, updated_at=?
                WHERE id=?
                """,
                    (new_date_key, new_tag_key, new_direction, note_text, database.now_ts(), note_id),
                )
            trend_notes = ideas.load_trend_notes_map(
                conn,
                [(new_date_key, new_tag_key, new_direction)],
            ).get((new_date_key, new_tag_key, new_direction), [])
            trend_note = next((note for note in trend_notes if note["id"] == note_id), None)
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "date": new_date_key,
                "tag": tag_def["display_name"] or new_tag_key,
                "tag_key": new_tag_key,
                "direction": new_direction,
                "trend_note": trend_note,
                "trend_notes": trend_notes,
                "has_trend_note": 1 if trend_notes else 0,
            }
        )

    @app.delete("/api/market-trends/note/<int:note_id>")
    def api_market_trend_note_delete(note_id: int):
        conn = db.connect()
        try:
            existing = conn.execute(
                """
            SELECT mtn.id, mtn.date_key, mtn.tag, mtn.direction, mtd.display_name
            FROM market_trend_notes mtn
            LEFT JOIN market_tag_definitions mtd ON mtd.key = mtn.tag
            WHERE mtn.id=?
            """,
                (note_id,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "note_not_found"}), 404
            with conn:
                conn.execute("DELETE FROM market_trend_notes WHERE id=?", (note_id,))
            trend_notes = ideas.load_trend_notes_map(
                conn,
                [(existing["date_key"], existing["tag"], existing["direction"])],
            ).get((existing["date_key"], existing["tag"], existing["direction"]), [])
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "date": existing["date_key"],
                "tag": existing["display_name"] or existing["tag"],
                "tag_key": existing["tag"],
                "direction": existing["direction"],
                "trend_notes": trend_notes,
                "has_trend_note": 1 if trend_notes else 0,
            }
        )

    @app.get("/api/market-tags")
    def api_market_tags():
        active_only = ((request.args.get("active_only") or "").strip().lower() in ("1", "true", "yes"))
        conn = db.connect()
        try:
            tags = load_market_tag_definitions(conn, active_only=active_only)
        finally:
            conn.close()
        return jsonify({"ok": True, "tags": tags})

    @app.post("/api/market-tags")
    def api_market_tags_create():
        body = request.get_json(silent=True) or {}
        raw_name = body.get("display_name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return jsonify({"ok": False, "error": "invalid_display_name"}), 400
        display_name = raw_name.strip()

        conn = db.connect()
        try:
            duplicate = conn.execute(
                "SELECT key FROM market_tag_definitions WHERE display_name=? COLLATE NOCASE",
                (display_name,),
            ).fetchone()
            if duplicate:
                return jsonify({"ok": False, "error": "display_name_exists"}), 400
            key = create_market_tag_key(conn, display_name)
            row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM market_tag_definitions").fetchone()
            sort_order = int(row[0] or 0)
            ts = database.now_ts()
            with conn:
                conn.execute(
                    """
                INSERT INTO market_tag_definitions(key, display_name, active, sort_order, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                    (key, display_name, sort_order, ts, ts),
                )
            created = conn.execute(
                """
            SELECT key, display_name, active, sort_order, created_at, updated_at
            FROM market_tag_definitions
            WHERE key=?
            """,
                (key,),
            ).fetchone()
        finally:
            conn.close()
        return jsonify({"ok": True, "tag": dict(created)})

    @app.post("/api/market-tags/reorder")
    def api_market_tags_reorder():
        body = request.get_json(silent=True) or {}
        ordered_keys = body.get("ordered_keys")
        if not isinstance(ordered_keys, list) or not all(isinstance(key, str) and key.strip() for key in ordered_keys):
            return jsonify({"ok": False, "error": "invalid_ordered_keys"}), 400
        normalized_keys = [key.strip() for key in ordered_keys]
        if len(set(normalized_keys)) != len(normalized_keys):
            return jsonify({"ok": False, "error": "duplicate_ordered_keys"}), 400

        conn = db.connect()
        try:
            existing_rows = conn.execute(
                "SELECT key FROM market_tag_definitions ORDER BY sort_order ASC, created_at ASC, key ASC"
            ).fetchall()
            existing_keys = [row["key"] for row in existing_rows]
            existing_set = set(existing_keys)
            requested_set = set(normalized_keys)
            unknown = sorted(requested_set - existing_set)
            missing = [key for key in existing_keys if key not in requested_set]
            if unknown:
                return jsonify({"ok": False, "error": "unknown_ordered_keys", "unknown_keys": unknown}), 400
            if missing:
                return jsonify({"ok": False, "error": "missing_ordered_keys", "missing_keys": missing}), 400

            ts = database.now_ts()
            with conn:
                for idx, key in enumerate(normalized_keys):
                    conn.execute(
                        "UPDATE market_tag_definitions SET sort_order=?, updated_at=? WHERE key=?",
                        (idx, ts, key),
                    )
            tags = load_market_tag_definitions(conn, active_only=False)
        finally:
            conn.close()
        return jsonify({"ok": True, "tags": tags})

    @app.patch("/api/market-tags/<tag_key>")
    def api_market_tags_update(tag_key: str):
        body = request.get_json(silent=True) or {}
        updates: list[str] = []
        args: list = []
        display_name = body.get("display_name")
        active = body.get("active")

        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT key FROM market_tag_definitions WHERE key=?",
                (tag_key,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "tag_not_found"}), 404

            if display_name is not None:
                if not isinstance(display_name, str) or not display_name.strip():
                    return jsonify({"ok": False, "error": "invalid_display_name"}), 400
                normalized_name = display_name.strip()
                duplicate = conn.execute(
                    "SELECT key FROM market_tag_definitions WHERE display_name=? COLLATE NOCASE AND key<>?",
                    (normalized_name, tag_key),
                ).fetchone()
                if duplicate:
                    return jsonify({"ok": False, "error": "display_name_exists"}), 400
                updates.append("display_name=?")
                args.append(normalized_name)

            if active is not None:
                if not isinstance(active, bool):
                    return jsonify({"ok": False, "error": "invalid_active"}), 400
                updates.append("active=?")
                args.append(1 if active else 0)

            if not updates:
                return jsonify({"ok": False, "error": "missing_updates"}), 400

            ts = database.now_ts()
            updates.append("updated_at=?")
            args.append(ts)
            args.append(tag_key)
            with conn:
                conn.execute(
                    f"UPDATE market_tag_definitions SET {', '.join(updates)} WHERE key=?",
                    args,
                )
            updated = conn.execute(
                """
            SELECT key, display_name, active, sort_order, created_at, updated_at
            FROM market_tag_definitions
            WHERE key=?
            """,
                (tag_key,),
            ).fetchone()
        finally:
            conn.close()
        return jsonify({"ok": True, "tag": dict(updated)})

    @app.get("/api/market-tags/<tag_key>/impact")
    def api_market_tags_impact(tag_key: str):
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=?",
                (tag_key,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "tag_not_found"}), 404
            affected = market_tag_impact_counts(conn, tag_key)
        finally:
            conn.close()
        return jsonify({"ok": True, "tag": dict(existing), "affected": affected})

    @app.delete("/api/market-tags/<tag_key>")
    def api_market_tags_delete(tag_key: str):
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=?",
                (tag_key,),
            ).fetchone()
            if not existing:
                return jsonify({"ok": False, "error": "tag_not_found"}), 404
            affected = market_tag_impact_counts(conn, tag_key)
            ts = database.now_ts()
            with conn:
                conn.execute("DELETE FROM article_market_tags WHERE tag=?", (tag_key,))
                conn.execute("DELETE FROM market_trend_notes WHERE tag=?", (tag_key,))
                conn.execute("DELETE FROM market_tag_definitions WHERE key=?", (tag_key,))
                normalize_market_tag_sort_order(conn)
                if tag_key in DEFAULT_MARKET_TAG_CHOICES:
                    conn.execute(
                        """
                    INSERT INTO market_tag_deleted_keys(key, deleted_at)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET deleted_at=excluded.deleted_at
                    """,
                        (tag_key, ts),
                    )
            remaining_tags = load_market_tag_definitions(conn, active_only=False)
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "deleted_tag": dict(existing),
                "affected": affected,
                "tags": remaining_tags,
            }
        )

    @app.post("/api/market-tags/<tag_key>/merge")
    def api_market_tags_merge(tag_key: str):
        body = request.get_json(silent=True) or {}
        target_key = (body.get("target_key") or "").strip()
        if not target_key:
            return jsonify({"ok": False, "error": "missing_target_key"}), 400
        if target_key == tag_key:
            return jsonify({"ok": False, "error": "same_source_target"}), 400

        conn = db.connect()
        try:
            source = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=?",
                (tag_key,),
            ).fetchone()
            if not source:
                return jsonify({"ok": False, "error": "tag_not_found"}), 404
            target = conn.execute(
                "SELECT key, display_name FROM market_tag_definitions WHERE key=?",
                (target_key,),
            ).fetchone()
            if not target:
                return jsonify({"ok": False, "error": "target_tag_not_found"}), 404

            moved_item_tag_count = int(
                conn.execute(
                    """
                SELECT COUNT(*)
                FROM article_market_tags src
                WHERE src.tag=?
                  AND NOT EXISTS (
                      SELECT 1 FROM article_market_tags dst
                      WHERE dst.url = src.url AND dst.tag = ?
                  )
                """,
                    (tag_key, target_key),
                ).fetchone()[0]
                or 0
            )
            skipped_duplicate_item_tag_count = int(
                conn.execute(
                    """
                SELECT COUNT(*)
                FROM article_market_tags src
                WHERE src.tag=?
                  AND EXISTS (
                      SELECT 1 FROM article_market_tags dst
                      WHERE dst.url = src.url AND dst.tag = ?
                  )
                """,
                    (tag_key, target_key),
                ).fetchone()[0]
                or 0
            )
            moved_trend_note_count = int(
                conn.execute("SELECT COUNT(*) FROM market_trend_notes WHERE tag=?", (tag_key,)).fetchone()[0] or 0
            )
            ts = database.now_ts()
            with conn:
                conn.execute(
                    """
                DELETE FROM article_market_tags
                WHERE tag=?
                  AND EXISTS (
                      SELECT 1 FROM article_market_tags dst
                      WHERE dst.url = article_market_tags.url AND dst.tag = ?
                  )
                """,
                    (tag_key, target_key),
                )
                conn.execute(
                    "UPDATE article_market_tags SET tag=?, updated_at=? WHERE tag=?",
                    (target_key, ts, tag_key),
                )
                conn.execute(
                    "UPDATE market_trend_notes SET tag=?, updated_at=? WHERE tag=?",
                    (target_key, ts, tag_key),
                )
                conn.execute("DELETE FROM market_tag_definitions WHERE key=?", (tag_key,))
                conn.execute("DELETE FROM market_tag_deleted_keys WHERE key=?", (tag_key,))
                normalize_market_tag_sort_order(conn)
            remaining_tags = load_market_tag_definitions(conn, active_only=False)
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "source_tag": dict(source),
                "target_tag": dict(target),
                "moved_item_tag_count": moved_item_tag_count,
                "skipped_duplicate_item_tag_count": skipped_duplicate_item_tag_count,
                "moved_trend_note_count": moved_trend_note_count,
                "tags": remaining_tags,
            }
        )

    @app.put("/api/news/<item_id>/market-tag")
    def api_news_market_tag_upsert(item_id: str):
        body = request.get_json(silent=True) or {}
        raw_tag = body.get("tag")
        raw_direction = body.get("direction")
        if not isinstance(raw_tag, str) or not raw_tag.strip():
            return jsonify({"ok": False, "error": "invalid_tag"}), 400
        if not isinstance(raw_direction, str):
            return jsonify({"ok": False, "error": "invalid_direction"}), 400
        tag = raw_tag.strip()
        direction = raw_direction.strip().lower()
        if direction not in MARKET_DIRECTIONS:
            return jsonify({"ok": False, "error": "invalid_direction"}), 400

        conn = db.connect()
        try:
            tag_def = conn.execute(
                "SELECT key FROM market_tag_definitions WHERE key=? AND active=1",
                (tag,),
            ).fetchone()
            if not tag_def:
                return jsonify({"ok": False, "error": "unsupported_tag"}), 400
            item = conn.execute("SELECT id, url FROM items WHERE id=?", (item_id,)).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400

            ts = database.now_ts()
            important_at = None
            with conn:
                conn.execute(
                    """
                INSERT INTO article_market_tags(url, tag, direction, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url, tag) DO UPDATE SET
                  direction=excluded.direction,
                  updated_at=excluded.updated_at
                """,
                    (url, tag, direction, ts, ts),
                )
                row = conn.execute(
                    "SELECT read_at, important_at, read_later_at, read_later_done_at, favorite_at FROM item_state WHERE item_id=?",
                    (item_id,),
                ).fetchone()
                read_at = row["read_at"] if row else None
                important_at = row["important_at"] if row else None
                read_later_at = row["read_later_at"] if row else None
                read_later_done_at = row["read_later_done_at"] if row else None
                favorite_at = row["favorite_at"] if row else None
                if not important_at:
                    important_at = ts
                    read_at = read_at or ts
                    conn.execute(
                        """
                    INSERT INTO item_state(item_id, read_at, important_at, read_later_at, read_later_done_at, favorite_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                      read_at=COALESCE(item_state.read_at,excluded.read_at),
                      important_at=excluded.important_at,
                      read_later_at=excluded.read_later_at,
                      read_later_done_at=excluded.read_later_done_at,
                      favorite_at=excluded.favorite_at,
                      updated_at=excluded.updated_at
                    """,
                        (item_id, read_at, important_at, read_later_at, read_later_done_at, favorite_at, ts),
                    )
            market_tags = reader_items.load_market_tags_map(conn, [url]).get(url, [])
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "market_tags": market_tags,
                "has_market_tags": 1 if market_tags else 0,
                "important_at": important_at,
                "read_at": read_at,
                "favorite_at": favorite_at,
            }
        )

    @app.delete("/api/news/<item_id>/market-tag")
    def api_news_market_tag_delete(item_id: str):
        raw_tag = (request.args.get("tag") or "").strip()
        if not raw_tag:
            return jsonify({"ok": False, "error": "missing_tag"}), 400

        conn = db.connect()
        try:
            tag_def = conn.execute(
                "SELECT key FROM market_tag_definitions WHERE key=?",
                (raw_tag,),
            ).fetchone()
            if not tag_def:
                return jsonify({"ok": False, "error": "unsupported_tag"}), 400
            item = conn.execute("SELECT id, url FROM items WHERE id=?", (item_id,)).fetchone()
            if not item:
                return jsonify({"ok": False, "error": "item_not_found"}), 404
            url = (item["url"] or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "missing_url"}), 400
            with conn:
                conn.execute(
                    "DELETE FROM article_market_tags WHERE url=? AND tag=?",
                    (url, raw_tag),
                )
            market_tags = reader_items.load_market_tags_map(conn, [url]).get(url, [])
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "item_id": item_id,
                "market_tags": market_tags,
                "has_market_tags": 1 if market_tags else 0,
            }
        )
