"""Evaluate a reading/search filter once, then hydrate only the requested page."""
import json

import news_classification


DATE_SQL = "COALESCE(NULLIF(items.date, ''), substr(items.published_at, 1, 10))"


def filter_joins(collection, *, search=False):
    # An inner join lets SQLite start with the small, indexed saved collection.
    state_join = "JOIN" if collection in {"favorites", "important"} else "LEFT JOIN"
    joins = f"{state_join} item_state st ON st.item_id=items.id"
    if collection == "read_later" or search:
        joins += " LEFT JOIN news_item_details ad ON ad.item_id=items.id"
    if search:
        joins += " LEFT JOIN news_item_ai aa ON aa.item_id=items.id LEFT JOIN article_notes an ON an.url=items.url"
    return joins


def select_page(conn, joins, where, args, *, category="all", categories=False,
                dates=True, count_only=False, ascending=False, cursor=None,
                limit=30, offset=0):
    """Totals, facets and page IDs share one materialized, lightweight scope.

    `where` already contains legacy category constraints. `category` is used
    by combined-facet requests, whose primary total spans all categories.
    Detail/AI job views and large body fields never enter the page sort.
    """
    if count_only and not dates and not categories and category == "all":
        total = conn.execute(f"SELECT COUNT(*) FROM items {joins} {where}", args).fetchone()[0]
        return {"total": total, "filter_total": total, "ids": []}

    args = list(args)
    category_sql = "NULL"
    if categories or category != "all":
        joins += """ LEFT JOIN item_classifications facet ON facet.item_id=items.id
            AND facet.status='done' AND facet.title=items.title AND facet.rule_version=?"""
        args.insert(0, news_classification.RULE_VERSION)
        category_sql = "facet.category"
    scope = "WHERE category=?" if category != "all" else ""
    if scope:
        args.append(category)
    page_where = ""
    if cursor:
        op = ">" if ascending else "<"
        page_where = f"WHERE (date_key,published_at,id) {op} (?,?,?)"
        args.extend([cursor["date_key"], cursor["published_at"], cursor["id"]])
    direction = "ASC" if ascending else "DESC"
    args.extend([0 if count_only else limit, offset])
    row = conn.execute(f"""
        WITH filtered AS MATERIALIZED (
            SELECT items.id, {DATE_SQL} AS date_key, items.published_at,
                   {category_sql} AS category
            FROM items {joins} {where}
        ), scoped AS MATERIALIZED (SELECT * FROM filtered {scope}),
        page AS (
            SELECT id FROM scoped {page_where}
            ORDER BY date_key {direction}, published_at {direction}, id {direction}
            LIMIT ? OFFSET ?
        )
        SELECT (SELECT COUNT(*) FROM scoped) AS total,
               (SELECT COUNT(*) FROM filtered) AS filter_total,
               (SELECT json_group_array(id) FROM page) AS ids,
               {"(SELECT json_group_object(date_key,total) FROM (SELECT COALESCE(NULLIF(date_key,''),'unknown') AS date_key, COUNT(*) AS total FROM scoped GROUP BY 1))" if dates else "'{}'"} AS date_counts,
               {"(SELECT json_group_object(category,total) FROM (SELECT category, COUNT(*) AS total FROM filtered WHERE category IS NOT NULL GROUP BY category))" if categories else "'{}'"} AS category_counts
        """, args).fetchone()
    result = {"total": row["total"], "filter_total": row["filter_total"], "ids": json.loads(row["ids"])}
    if dates:
        result["date_counts"] = json.loads(row["date_counts"])
    if categories:
        counts = dict.fromkeys(news_classification.CATEGORIES, 0)
        counts.update({key: value for key, value in json.loads(row["category_counts"]).items() if key in counts})
        result["category_counts"] = counts
    return result


def select_idea_page(conn, idea_type, *, reading_only=False, query="", ascending=False,
                     cursor=None, limit=30, offset=0):
    parts = []
    if idea_type in {"all", "article"}:
        parts.append("""SELECT 'article:' || items.id AS idea_id, an.updated_at,
            items.title, an.note FROM article_notes an JOIN items ON items.url=an.url
            WHERE is_bloomberg_video_url(items.url)=0""")
    if idea_type in {"all", "standalone"}:
        parts.append("SELECT 'standalone:' || id AS idea_id, updated_at, title, note FROM standalone_ideas")
    if idea_type in {"all", "trend"} and not reading_only:
        parts.append("""SELECT 'trend:' || mtn.id AS idea_id, mtn.updated_at,
            COALESCE(mtd.display_name,mtn.tag) || ' · ' || CASE WHEN mtn.direction='bullish' THEN '看多' ELSE '看空' END AS title,
            mtn.note FROM market_trend_notes mtn LEFT JOIN market_tag_definitions mtd ON mtd.key=mtn.tag""")
    if not parts:
        return {"total": 0, "ids": [], "date_counts": {}}
    direction = "ASC" if ascending else "DESC"
    page_where = ""
    args = [query]
    if cursor:
        page_where = f"WHERE (updated_at,idea_id) {'>' if ascending else '<'} (?,?)"
        args.extend([cursor["updated_at"], cursor["idea_id"]])
    args.extend([limit, offset])
    row = conn.execute(f"""WITH base AS ({' UNION ALL '.join(parts)}),
        matched AS MATERIALIZED (
            SELECT idea_id,updated_at FROM base WHERE instr(lower(COALESCE(title,'') || ' ' || note),lower(?))>0
        ), page AS (
            SELECT idea_id FROM matched {page_where}
            ORDER BY updated_at {direction},idea_id {direction} LIMIT ? OFFSET ?
        )
        SELECT (SELECT COUNT(*) FROM matched) AS total,
          (SELECT json_group_array(idea_id) FROM page) AS ids,
          (SELECT json_group_object(date_key,total) FROM (
            SELECT COALESCE(NULLIF(substr(updated_at,1,10),''),'unknown') AS date_key,COUNT(*) AS total
            FROM matched GROUP BY 1)) AS date_counts
        """, args).fetchone()
    return {"total": row["total"], "ids": json.loads(row["ids"]), "date_counts": json.loads(row["date_counts"])}
