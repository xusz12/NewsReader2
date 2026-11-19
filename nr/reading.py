from __future__ import annotations

import math
import news_classification
import news_collection
import news_query
import sqlite3
import topic_tracking
from contextlib import closing
from feed_sources import derive_source_key, source_label_for_key, visible_news_sql
from flask import jsonify, request

from nr import configuration, ideas, reader_items, reviews


FEED_NEWS_ORDER_BY_SQL = """
COALESCE(NULLIF(items.date, ''), substr(items.published_at, 1, 10)) ASC,
items.published_at ASC,
items.id ASC
"""


NON_FEED_NEWS_ORDER_BY_SQL = """
COALESCE(NULLIF(items.date, ''), substr(items.published_at, 1, 10)) DESC,
items.published_at DESC,
items.id DESC
"""


IDEA_TYPE_FILTERS = {"all", "article", "trend", "standalone"}


def news_sort_ascending(collection: str, read_filter: str, sort_order: str = "default") -> bool:
    ascending = collection in ("feed", "read_later") and read_filter == "unread"
    return not ascending if sort_order == "reverse" else ascending


def news_order_by_sql(collection: str, sort_order: str = "default", read_filter: str = "all") -> str:
    ascending = news_sort_ascending(collection, read_filter, sort_order)
    direction = "ASC" if ascending else "DESC"
    return f"""
    {reader_items.ITEM_DATE_SQL} {direction},
    items.published_at {direction},
    items.id {direction}
    """


def use_feed_unread_cursor_paging(collection: str, read_filter: str) -> bool:
    # Completing/re-adding/canceling read-later entries also removes rows from
    # the current filter. Offsets would skip unseen articles on the next page.
    return (collection == "feed" and read_filter == "unread") or collection == "read_later"


def parse_idea_type_filter(args) -> str:
    idea_type = (args.get("type") or "all").strip().lower()
    if idea_type not in IDEA_TYPE_FILTERS:
        raise ValueError("invalid_idea_type")
    return idea_type


def parse_feed_unread_cursor(args) -> dict | None:
    cursor_date = (args.get("cursor_date") or "").strip()
    cursor_published_at = (args.get("cursor_published_at") or "").strip()
    cursor_id = (args.get("cursor_id") or "").strip()
    if not (cursor_date or cursor_published_at or cursor_id):
        return None
    # Unknown source publication time is stored as empty, never replaced by
    # collection time. Such rows still need a complete, explicit paging cursor.
    if (not cursor_id or "cursor_date" not in args or "cursor_published_at" not in args
            or bool(cursor_date) != bool(cursor_published_at)):
        raise ValueError("invalid_cursor")
    return {
        "date_key": cursor_date,
        "published_at": cursor_published_at,
        "id": cursor_id,
    }


def build_feed_source_hidden_clause(collection: str) -> tuple[str, list[str]]:
    if collection != "feed":
        return "", []
    hidden = configuration.current_runtime_settings()["feed"].get("hidden_source_subkeys") or []
    if not hidden:
        return "", []
    placeholders = ",".join(["?"] * len(hidden))
    return f"feed_source_subkey(items.url, items.source_type, items.source, items.source_name) NOT IN ({placeholders})", list(hidden)


def build_source_filter_clause(source_filter: str) -> tuple[str, list[str]]:
    sf = (source_filter or "all").strip().lower()
    if sf in ("", "all"):
        return "", []
    if sf == "x":
        return "(items.source_type='twitter' OR lower(items.url) LIKE 'https://x.com/%' OR lower(items.url) LIKE 'https://www.x.com/%' OR lower(items.url) LIKE 'https://twitter.com/%' OR lower(items.url) LIKE 'https://www.twitter.com/%')", []
    if sf == "reuters":
        return "(lower(items.url) LIKE 'https://reuters.com/%' OR lower(items.url) LIKE 'https://www.reuters.com/%')", []
    if sf == "bloomberg":
        return "(lower(items.url) LIKE 'https://bloomberg.com/%' OR lower(items.url) LIKE 'https://www.bloomberg.com/%')", []
    if sf == "techcrunch":
        return "(lower(items.url) LIKE 'https://techcrunch.com/%' OR lower(items.url) LIKE 'https://www.techcrunch.com/%')", []
    if sf == "ars":
        return "(lower(items.url) LIKE 'https://arstechnica.com/%' OR lower(items.url) LIKE 'https://www.arstechnica.com/%')", []
    if sf.startswith("host:"):
        host = sf[5:]
        if host:
            return "(lower(items.url) LIKE ? OR lower(items.url) LIKE ?)", [f"https://{host}/%", f"https://www.{host}/%"]
    return "", []


READ_LATER_PENDING_SQL = "st.read_later_at IS NOT NULL"


def count_news_items(
    conn: sqlite3.Connection,
    *,
    collection: str = "feed",
    read_filter: str = "all",
    source_filter: str = "all",
    q: str = "",
    category: str = "all",
) -> int:
    join_sql = news_query.filter_joins(collection)
    where_sql, args = _build_news_where_clause(q, read_filter, collection, source_filter, category)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM items {join_sql} {where_sql}",
        args,
    ).fetchone()
    return int((row["c"] if row else 0) or 0)


def count_pending_review_chains(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM review_chains
        WHERE status = 'active' AND plan_review_date <= ?
        """,
        (reviews._today_str(),),
    ).fetchone()
    return int((row["c"] if row else 0) or 0)


def load_nav_summary(conn: sqlite3.Connection) -> dict:
    hidden, hidden_args = build_feed_source_hidden_clause("feed")
    feed_condition = "st.read_at IS NULL" + (f" AND {hidden}" if hidden else "")
    row = conn.execute(f"""SELECT
        COALESCE(SUM(CASE WHEN {feed_condition} THEN 1 ELSE 0 END),0) AS feed_unread,
        COALESCE(SUM(CASE WHEN {READ_LATER_PENDING_SQL} THEN 1 ELSE 0 END),0) AS read_later_unread,
        COALESCE(SUM(st.favorite_at IS NOT NULL),0) AS favorites
        FROM items LEFT JOIN item_state st ON st.item_id=items.id
        LEFT JOIN news_item_details ad ON ad.item_id=items.id
        WHERE {visible_news_sql()}""", hidden_args).fetchone()
    pending_review = count_pending_review_chains(conn)
    return {
        "feed_unread": row["feed_unread"],
        "read_later_unread": row["read_later_unread"],
        "favorites": row["favorites"],
        "pending_review": pending_review,
        "topics_unseen": topic_tracking.unseen_topic_count(conn),
    }


def _build_news_where_clause(
    q: str,
    read_filter: str,
    collection: str,
    source_filter: str,
    category: str = "all",
) -> tuple[str, list]:
    where = [visible_news_sql()]
    args: list = []
    category_clause, category_args = news_classification.filter_sql(category)
    if category_clause:
        where.append(category_clause)
        args.extend(category_args)
    if q:
        where.append("(items.title LIKE ? OR items.summary LIKE ? OR items.source LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like, like])
    if collection == "read_later":
        if read_filter == "unread":
            where.append(READ_LATER_PENDING_SQL)
        elif read_filter == "read":
            where.append("(st.read_later_done_at IS NOT NULL AND st.read_later_at IS NULL)")
        else:
            where.append("(st.read_later_at IS NOT NULL OR st.read_later_done_at IS NOT NULL)")
    else:
        if read_filter == "unread":
            where.append("st.read_at IS NULL")
        elif read_filter == "read":
            where.append("st.read_at IS NOT NULL")
    if collection == "important":
        where.append("st.important_at IS NOT NULL")
    elif collection == "favorites":
        where.append("st.favorite_at IS NOT NULL")
    elif collection == "notes":
        where.append("EXISTS (SELECT 1 FROM article_notes an WHERE an.url = items.url)")
    elif collection == "market_tags":
        where.append("EXISTS (SELECT 1 FROM article_market_tags mt WHERE mt.url = items.url)")
    source_clause, source_args = build_source_filter_clause(source_filter)
    if source_clause:
        where.append(source_clause)
        args.extend(source_args)
    hidden_clause, hidden_args = build_feed_source_hidden_clause(collection)
    if hidden_clause:
        where.append(hidden_clause)
        args.extend(hidden_args)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return where_sql, args


def _build_search_filter_clause(range_value: str, time_value: str) -> tuple[str, list]:
    where = []
    args: list = []

    if range_value == "important":
        where.append("st.important_at IS NOT NULL")
    elif range_value == "notes":
        where.append("EXISTS (SELECT 1 FROM article_notes an2 WHERE an2.url = items.url)")
    elif range_value == "market_tags":
        where.append("EXISTS (SELECT 1 FROM article_market_tags mt2 WHERE mt2.url = items.url)")
    elif range_value == "detail_ready":
        where.append("ad.url IS NOT NULL")

    if time_value == "today":
        where.append(f"{reader_items.ITEM_DATE_SQL} = date('now', 'localtime')")
    elif time_value == "7d":
        where.append(f"{reader_items.ITEM_DATE_SQL} >= date('now', 'localtime', '-6 day')")
    elif time_value == "30d":
        where.append(f"{reader_items.ITEM_DATE_SQL} >= date('now', 'localtime', '-29 day')")

    where_sql = (" AND " + " AND ".join(where)) if where else ""
    return where_sql, args


def news_batch_filter(conn, *, item_lookup=False):
    """A read transaction freezes the first page; subsequent pages reuse its horizon."""
    conn.execute("BEGIN")
    boundary = news_collection.batch_boundary(conn)
    if "after_batch" in request.args:
        clause, args = news_collection.batch_clause(request.args["after_batch"], newer=True, item_lookup=item_lookup)
    elif "batch" in request.args or request.args.get("count_only") != "1":
        value = request.args.get("batch", str(boundary))
        clause, args = news_collection.batch_clause(value, item_lookup=item_lookup)
        boundary = min(int(value), boundary)
    else:
        clause, args = "", []
    return boundary, clause, args


def source_counts(rows):
    counts = {}
    labels = {}
    for row in rows:
        key = derive_source_key(row["url"], row["source_type"], row["source"])
        counts[key] = counts.get(key, 0) + 1
        labels.setdefault(key, source_label_for_key(key, row["source"]))
    known_order = ["reuters", "bloomberg", "techcrunch", "ars", "x"]
    keys = [key for key in known_order if key in counts]
    keys += sorted((key for key in counts if key not in known_order), key=lambda key: labels.get(key, key))
    return [{"key": key, "label": labels[key], "count": counts[key]} for key in keys]


def register(app, db_conn):
    @app.get("/api/nav-summary")
    def api_nav_summary():
        conn = db_conn()
        try:
            return jsonify({"ok": True, "summary": load_nav_summary(conn)})
        finally:
            conn.close()

    @app.get("/api/news")
    def api_news():
        q = (request.args.get("q") or "").strip()
        read_filter = (request.args.get("read_filter") or "all").strip().lower()
        collection = (request.args.get("collection") or "feed").strip().lower()
        source_filter = (request.args.get("source_filter") or "all").strip().lower()
        page = max(1, int(request.args.get("page", "1")))
        per = min(100, max(10, int(request.args.get("per", "30"))))
        cursor_mode = use_feed_unread_cursor_paging(collection, read_filter)
        # Older clients can still request read-later pages by offset alone.
        if collection == "read_later" and page > 1 and not any(
            key in request.args for key in ("cursor_date", "cursor_published_at", "cursor_id")
        ):
            cursor_mode = False
        try:
            category = news_classification.parse_category(request.args.get("category"))
            sort_order = reader_items.parse_news_sort_order(request.args)
            cursor = parse_feed_unread_cursor(request.args) if cursor_mode else None
        except ValueError as exc:
            error = str(exc) if str(exc) in {"invalid_sort_order", "invalid_category"} else "invalid_cursor"
            return jsonify({"ok": False, "error": error}), 400
        combined = request.args.get("include_facets") == "1"
        where_sql, args = _build_news_where_clause(q, read_filter, collection, source_filter, "all" if combined else category)
        if request.args.get("has_note") == "1":
            where_sql += " AND EXISTS (SELECT 1 FROM article_notes n WHERE n.url = items.url)"
        return reading_page_response(
            news_query.filter_joins(collection), where_sql, args,
            collection=collection, read_filter=read_filter, category=category,
            sort_order=sort_order, page=page, per=per, cursor_mode=cursor_mode, cursor=cursor,
        )

    def reading_page_response(joins, where, args, *, collection, read_filter, category,
                              sort_order, page, per, cursor_mode=False, cursor=None):
        combined = request.args.get("include_facets") == "1"
        count_only = request.args.get("count_only") == "1"
        with closing(db_conn()) as conn:
            try:
                batch, clause, batch_args = news_batch_filter(conn, item_lookup=collection in {"favorites", "important"})
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            result = news_query.select_page(
                conn, joins, where + clause, [*args, *batch_args],
                category=category if combined else "all",
                categories=combined or request.args.get("category_counts") == "1",
                dates=not count_only or request.args.get("date_counts") == "1",
                count_only=count_only, ascending=news_sort_ascending(collection, read_filter, sort_order),
                cursor=cursor if cursor_mode else None, limit=per + int(cursor_mode),
                offset=0 if cursor_mode else (page - 1) * per,
            )
            ids = result.pop("ids")
            if not combined:
                result.pop("filter_total")
            if count_only:
                return jsonify({"ok": True, **result})
            has_more = len(ids) > per if cursor_mode else page * per < result["total"]
            ids = ids[:per]
            item_map = reader_items.load_news_item_map(conn, ids)
            items = [item_map[ident] for ident in ids if ident in item_map]
            revision = news_classification.completed_revision(conn)
        next_cursor = None
        if cursor_mode and has_more and items:
            last = items[-1]
            # Serialization labels an empty date as "unknown"; a cursor must retain
            # the actual SQL sort key, including an empty publication timestamp.
            next_cursor = {"date_key": last["date"] or last["published_at"][:10],
                           "published_at": last["published_at"], "id": str(last["id"])}
        pages = max(1, math.ceil(result["total"] / per))
        return jsonify({**result, "items": items, "batch": batch, "classification_revision": revision,
                        "page": page, "pages": max(page, pages) if cursor_mode else pages,
                        "has_more": has_more, "next_cursor": next_cursor, "sort_order": sort_order})

    @app.get("/api/ideas")
    def api_ideas():
        page = max(1, int(request.args.get("page", "1")))
        per = min(100, max(10, int(request.args.get("per", "30"))))
        cursor_mode = request.args.get("cursor_mode") == "1"
        cursor = None
        if cursor_mode and ("cursor_id" in request.args or "cursor_updated_at" in request.args):
            cursor = {"idea_id": request.args.get("cursor_id", "").strip(),
                      "updated_at": request.args.get("cursor_updated_at", "").strip()}
            if not all(cursor.values()):
                return jsonify({"ok": False, "error": "invalid_cursor"}), 400
        try:
            sort_order = reader_items.parse_news_sort_order(request.args)
            idea_type = parse_idea_type_filter(request.args)
        except ValueError as exc:
            error = "invalid_idea_type" if str(exc) == "invalid_idea_type" else "invalid_sort_order"
            return jsonify({"ok": False, "error": error}), 400

        with closing(db_conn()) as conn:
            conn.execute("BEGIN")
            selection = news_query.select_idea_page(
                conn, idea_type, reading_only=request.args.get("reading_only") == "1",
                query=(request.args.get("q") or "").strip(), ascending=sort_order == "reverse",
                cursor=cursor, limit=per + int(cursor_mode), offset=0 if cursor_mode else (page - 1) * per,
            )
            page_items = ideas.load_idea_rows(conn, idea_type, sort_order, selection["ids"][:per])
        total = selection["total"]
        date_counts = selection["date_counts"]
        pages = max(1, math.ceil(total / per)) if total else 1
        has_more = len(selection["ids"]) > per if cursor_mode else page < pages
        next_cursor = None
        if cursor_mode and has_more and page_items:
            last = page_items[-1]
            next_cursor = {"updated_at": last["updated_at"], "idea_id": last["idea_id"]}
        return jsonify(
            {
                "items": page_items,
                "total": total,
                "date_counts": date_counts,
                "page": page,
                "pages": max(page, pages) if cursor_mode else pages,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "sort_order": sort_order,
                "type": idea_type,
            }
        )

    @app.get("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip()
        range_value = (request.args.get("range") or "all").strip().lower()
        time_value = (request.args.get("time") or "all").strip().lower()
        page = max(1, int(request.args.get("page", "1")))
        per = min(100, max(10, int(request.args.get("per", "30"))))
        valid_ranges = {"all", "important", "notes", "market_tags", "detail_ready"}
        valid_times = {"all", "today", "7d", "30d"}
        try:
            category = news_classification.parse_category(request.args.get("category"))
            sort_order = reader_items.parse_news_sort_order(request.args)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if range_value not in valid_ranges:
            return jsonify({"ok": False, "error": "invalid_range"}), 400
        if time_value not in valid_times:
            return jsonify({"ok": False, "error": "invalid_time"}), 400
        if not q:
            return jsonify({"items": [], "total": 0, "date_counts": {}, "page": 1, "pages": 1})

        like = f"%{q}%"
        search_args = [like] * 9 + [like, like]
        feed_filters = request.args.get("feed_filters") == "1"
        collection = request.args.get("collection", "feed") if feed_filters else "feed"
        read_filter = request.args.get("read_filter", "all") if feed_filters else "all"
        join_sql = news_query.filter_joins(collection, search=True)
        where_sql = """
    WHERE (
      items.title LIKE ? OR
      COALESCE(items.summary, '') LIKE ? OR
      COALESCE(items.source, '') LIKE ? OR
      COALESCE(items.source_name, '') LIKE ? OR
      COALESCE(ad.content, '') LIKE ? OR
      COALESCE(aa.body_zh, '') LIKE ? OR
      COALESCE(aa.key_points_zh, '') LIKE ? OR
      COALESCE(aa.conclusion_zh, '') LIKE ? OR
      COALESCE(an.note, '') LIKE ? OR
      EXISTS (
        SELECT 1
        FROM article_market_tags mt
        LEFT JOIN market_tag_definitions mtd ON mtd.key = mt.tag
        WHERE mt.url = items.url
          AND (mt.tag LIKE ? OR COALESCE(mtd.display_name, '') LIKE ?)
      )
    )
    AND is_bloomberg_video_url(items.url) = 0
    """
        filter_sql, filter_args = _build_search_filter_clause(range_value, time_value)
        category_clause, category_args = news_classification.filter_sql("all" if request.args.get("include_facets") == "1" else category)
        if category_clause:
            filter_sql += " AND " + category_clause
            filter_args.extend(category_args)
        if request.args.get("feed_filters") == "1":
            collection_where, collection_args = _build_news_where_clause(
                "", request.args.get("read_filter", "all"), request.args.get("collection", "feed"),
                request.args.get("source_filter", "all"),
            )
            filter_sql += " AND " + collection_where.removeprefix("WHERE ")
            filter_args.extend(collection_args)
            if request.args.get("has_note") == "1":
                filter_sql += " AND EXISTS (SELECT 1 FROM article_notes n WHERE n.url = items.url)"
        all_args = [*search_args, *filter_args]

        cursor_mode = feed_filters and use_feed_unread_cursor_paging(collection, read_filter)
        if page > 1 and not any(key in request.args for key in ("cursor_date", "cursor_published_at", "cursor_id")):
            cursor_mode = False
        try:
            cursor = parse_feed_unread_cursor(request.args) if cursor_mode else None
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_cursor"}), 400
        return reading_page_response(
            join_sql, where_sql + filter_sql, all_args,
            collection=collection, read_filter=read_filter, category=category,
            sort_order=sort_order, page=page, per=per, cursor_mode=cursor_mode, cursor=cursor,
        )

    @app.get("/api/sources")
    def api_sources():
        q = (request.args.get("q") or "").strip()
        read_filter = (request.args.get("read_filter") or "all").strip().lower()
        collection = (request.args.get("collection") or "feed").strip().lower()

        include_catalog = request.args.get("include_catalog") == "1"
        include_groups = request.args.get("source_groups") == "1"
        where_sql, args = _build_news_where_clause(q, "all" if include_catalog else read_filter, collection, "all")
        joins = news_query.filter_joins(collection)
        detail = "ad.url IS NOT NULL" if collection == "read_later" else "0"
        with closing(db_conn()) as conn:
            rows = conn.execute(f"""SELECT items.url,items.source_type,items.source,
                st.read_at,st.read_later_at,st.read_later_done_at,{detail} AS detail_ready
                FROM items {joins} {where_sql}""", args).fetchall()
            group_rows = rows
            if include_groups and not (collection == "feed" and include_catalog and not q
                                       and not build_feed_source_hidden_clause("feed")[0]):
                # Navigation includes sources whose articles are all hidden/read or
                # outside this collection. It needs identities, not model settings.
                group_rows = conn.execute(f"SELECT url,source_type,source FROM items WHERE {visible_news_sql()}").fetchall()
        matched = rows
        if include_catalog and read_filter in {"unread", "read"}:
            if collection == "read_later":
                if read_filter == "unread":
                    matched = [r for r in rows if r["read_later_at"] is not None]
                else:
                    matched = [r for r in rows if r["read_later_done_at"] is not None and r["read_later_at"] is None]
            else:
                matched = [r for r in rows if (r["read_at"] is None) == (read_filter == "unread")]
        result = {"ok": True, "sources": source_counts(matched)}
        if include_catalog:
            result["catalog"] = result["sources"] if matched is rows else source_counts(rows)
        if include_groups:
            groups = result["sources"] if group_rows is matched else result.get("catalog") if group_rows is rows else None
            if groups is None:
                groups = source_counts(group_rows)
            result["groups"] = [{"source_key": row["key"], "label": row["label"]} for row in groups]
        return jsonify(result)
