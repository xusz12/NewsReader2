"""Adapt saved reports to the shared reader without inserting synthetic news."""
import json

ID_PREFIX = "report:"
URL_PREFIX = "newsreader-report:"


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
