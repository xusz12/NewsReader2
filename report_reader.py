"""Adapt saved reports to the shared reader without inserting synthetic news."""
import json
from datetime import datetime, timezone

import reader_sources

ID_PREFIX = "report:"
URL_PREFIX = "newsreader-report:"


def complete_reading(conn, report_id, token, visible_sql="1"):
    """Persist an explicit acknowledgement only after source browsing is saved."""
    conn.execute("BEGIN IMMEDIATE")
    if token != conn.execute("SELECT database_token FROM periodic_report_meta WHERE singleton=1").fetchone()[0]:
        raise ValueError("report_database_changed")
    row = conn.execute("SELECT status,result_json,read_completed_at FROM periodic_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise LookupError("report_not_found")
    if row["status"] != "done":
        raise ValueError("report_read_unavailable")
    if row["read_completed_at"]:
        return {"id": report_id, "read_completed_at": row["read_completed_at"]}
    content = json.loads(row["result_json"] or "{}")
    paragraphs = [p for section in content.get("sections", []) for p in section["paragraphs"]]
    refs = {ref for p in paragraphs + content.get("briefs", []) for ref in p["refs"]}
    sources = [source for source in content.get("sources", []) if source["ref"] in refs]
    items = reader_sources.source_items(conn, sources, visible_sql)
    if any(not item["read_at"] for item in items.values()):
        raise ValueError("report_sources_unread")
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("UPDATE periodic_reports SET read_completed_at=? WHERE id=?", (completed_at, report_id))
    return {"id": report_id, "read_completed_at": completed_at}


def subject(conn, item_id):
    if not item_id.startswith(ID_PREFIX):
        return None
    row = conn.execute("SELECT * FROM periodic_reports WHERE id=? AND status='done'",
                       (item_id[len(ID_PREFIX):],)).fetchone()
    if not row:
        return None
    content = json.loads(row["result_json"] or "{}")
    blocks = []
    for section in content.get("sections", []):
        blocks.append(section["title"])
        blocks.extend(p["text"] for p in section["paragraphs"])
    if content.get("briefs"):
        blocks.append("简讯")
        blocks.extend(p["text"] for p in content["briefs"])
    return {"id": item_id, "report_id": row["id"], "report_kind": row["kind"], "title": row["title"],
            "url": URL_PREFIX + row["id"], "source": "日报", "published_at": row["period_end"],
            "content": "\n\n".join(blocks), "sources": content.get("sources", [])}


def context_text(item):
    sources = "\n".join(f"[{s['ref']}] {s.get('title', '')} · {s.get('source_name', '')} · "
                        f"{s.get('published_at', '')}\n{s.get('url', '')}" for s in item["sources"])
    return item["content"] + ("\n\n引用来源：\n" + sources if sources else "")
