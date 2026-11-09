"""Resolve archived report and catch-up sources to live news state."""

import news_versions


def source_items(conn, sources, visible_sql="1"):
    """Resolve archived references by URL so stale IDs cannot target other news."""
    result, by_id = {}, {}
    urls = list({s.get("url") for s in sources if s.get("url")})
    for start in range(0, len(urls), 400):
        batch = urls[start:start + 400]
        rows = conn.execute(f"""
            SELECT items.id, items.url, items.title, items.source, items.source_name, items.published_at,
                   items.date, items.time, items.summary, st.read_at, st.important_at,
                   st.read_later_at, st.read_later_done_at, st.favorite_at,
                   dj.status AS detail_status, aj.status AS ai_status,
                   CASE WHEN length(trim(ad.content)) > 0 THEN 1 ELSE 0 END AS detail_ready
            FROM items LEFT JOIN item_state st ON st.item_id=items.id
            LEFT JOIN news_item_detail_jobs dj ON dj.item_id=items.id
            LEFT JOIN news_item_details ad ON ad.item_id=items.id
            LEFT JOIN news_item_ai_jobs aj ON aj.item_id=items.id
            WHERE items.url IN ({','.join('?' for _ in batch)}) AND {visible_sql}
            ORDER BY items.id
        """, batch).fetchall()
        for row in rows:
            result.setdefault(row["url"], dict(row))
            by_id[row["id"]] = dict(row)
    news_versions.attach(conn, list(by_id.values()))
    resolved = {}
    for source in sources:
        item = by_id.get(source.get("id"))
        if not item or item["url"] != source.get("url"):
            item = result.get(source.get("url"))
        if item:
            resolved[source["ref"]] = by_id[item["id"]]
    return resolved
