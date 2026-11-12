"""Preview and delete a source's library data using the same transaction plan.

Sizes describe logical row contents plus owned files, not SQLite page allocation.
Published research documents are independent artifacts; their snapshots survive.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from feed_sources import derive_source_subkey


def item_fingerprint(item_id: str) -> bytes:
    """Compact identity only: no article content or URL is retained."""
    return hashlib.sha256(item_id.encode("utf-8")).digest()


MEDIA_REFERENCE = re.compile(r'https?://[^\s"<>\\\[\]()]+|/api/media-cache/[\w-]+|\b[0-9a-f]{64}\b')


def _media_keys(text, lookup):
    """Find URL/path references in prose and exact cache keys in JSON payloads."""
    found = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for token in [value, *MEDIA_REFERENCE.findall(value)]:
                if token in lookup:
                    found.add(lookup[token])

    try:
        visit(json.loads(text or ""))
    except (ValueError, TypeError):
        visit(text)
    return found


class SourceDeletionError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def attach_agent(conn, path, database_path):
    if Path(path).resolve() == Path(database_path).resolve():
        return "main"
    conn.execute("ATTACH DATABASE ? AS source_agent", (str(path),))
    return "source_agent"


def _within(root, relative):
    root = Path(root).resolve()
    path = root / relative
    # Neither an absolute path nor a symlink may reach outside the cache.
    if path.is_symlink() or root not in path.resolve().parents:
        return None
    return path


class Plan:
    def __init__(self, conn, key, *, database_path, agent_schema, scope, media_root, runtime_root, empty_source=None):
        self.conn, self.key = conn, key
        self.targets = {}
        self.files = []
        self.media_files = []
        self.runtime_dirs = []
        self.process_ids = []
        self.media_root, self.runtime_root = Path(media_root), Path(runtime_root)
        conn.execute("CREATE TEMP TABLE source_delete_items AS SELECT * FROM items WHERE feed_source_subkey(url,source_type,source,source_name)=?", (key,))
        conn.execute("CREATE UNIQUE INDEX temp.source_delete_item_id ON source_delete_items(id)")
        first = conn.execute("SELECT * FROM source_delete_items LIMIT 1").fetchone()
        if not first and not empty_source:
            raise SourceDeletionError("source_not_found", 404)
        source = derive_source_subkey(first["url"], first["source_type"], first["source"], first["source_name"]) if first else empty_source
        ids = "SELECT id FROM source_delete_items"
        # URL-owned content is shared across dates, and sometimes across sources.
        conn.execute(f"""CREATE TEMP TABLE source_delete_urls AS SELECT url FROM source_delete_items
            WHERE COALESCE(url,'')<>'' EXCEPT SELECT url FROM items WHERE id NOT IN ({ids})""")
        conn.execute("CREATE UNIQUE INDEX temp.source_delete_url ON source_delete_urls(url)")
        urls = "SELECT url FROM source_delete_urls"
        item_clause, url_clause = f"item_id IN ({ids})", f"url IN ({urls})"
        self.item_ids = [r[0] for r in conn.execute(ids)]
        dates = conn.execute("SELECT MIN(date),MAX(date) FROM source_delete_items").fetchone()

        for table in ("item_state", "item_classifications", "classification_progress_items", "topic_ingestions",
                      "topic_checks", "topic_node_items", "tracked_topic_items", "news_version_snapshots"):
            self.targets[table] = item_clause
        self.targets["news_reminders"] = f"{item_clause} OR item_url_snapshot IN ({urls})"
        self.targets["reading_checkpoints"] = f"{item_clause} OR {url_clause}"
        self.targets["article_note_metadata"] = f"note_id IN (SELECT id FROM article_note_entries WHERE {url_clause})"
        for table in ("article_note_entries", "article_notes", "article_highlights", "article_market_tags",
                      "article_ai", "ai_jobs", "article_details", "detail_jobs"):
            self.targets[table] = url_clause

        conn.execute(f"""CREATE TEMP TABLE source_delete_reviews AS WITH RECURSIVE chains(id) AS (
            SELECT id FROM review_chains WHERE source_type='article_note' AND source_key IN ({urls})
            UNION SELECT r.id FROM review_chains r JOIN chains c ON r.parent_chain_id=c.id)
            SELECT id FROM chains""")
        reviews = "SELECT id FROM source_delete_reviews"
        self.targets["review_evidence"] = f"chain_id IN ({reviews}) OR news_url IN ({urls})"
        self.targets["review_events"] = f"chain_id IN ({reviews})"
        self.targets["review_versions"] = f"chain_id IN ({reviews})"
        self.targets["review_chains"] = f"id IN ({reviews})"

        # Scan text once, rather than rescanning every article for every image.
        media = {r["url"]: dict(r) for r in conn.execute("SELECT * FROM media_cache")}
        lookup = {token: url for url, row in media.items()
                  for token in (url, row["cache_key"], "/api/media-cache/" + row["cache_key"])}
        owned, shared = set(), set()
        if media:
            for table, columns in (("article_details", ("content", "raw_json")), ("article_ai", ("body_zh", "raw_json"))):
                for row in conn.execute(f"SELECT url IN ({urls}) AS owned,{','.join(columns)} FROM {table}"):
                    references = owned if row[0] else shared
                    for text in row[1:]:
                        references.update(_media_keys(text, lookup))
            for row in conn.execute(f"SELECT item_id IN ({ids}),detail_json,ai_json FROM news_version_snapshots"):
                for text in row[1:]:
                    (owned if row[0] else shared).update(_media_keys(text, lookup))
        conn.execute("CREATE TEMP TABLE source_delete_media(url TEXT PRIMARY KEY,relative_path TEXT)")
        conn.executemany("INSERT INTO source_delete_media VALUES (?,?)", [(url, media[url]["relative_path"]) for url in owned - shared])
        self.targets["media_cache"] = "url IN (SELECT url FROM source_delete_media)"
        for row in conn.execute("SELECT relative_path FROM source_delete_media"):
            path = _within(self.media_root, row[0])
            if path and path.is_file():
                self.media_files.append(path)
                self.files.append(path)

        prefix = agent_schema + "."
        conn.execute("CREATE TEMP TABLE source_delete_scope (scope TEXT)")
        conn.execute("INSERT INTO source_delete_scope VALUES (?)", (scope,))
        conn.execute(f"""CREATE TEMP TABLE source_delete_sessions AS SELECT * FROM {prefix}agent_sessions
            WHERE db_scope=? AND ({item_clause} OR {url_clause})""", (scope,))
        sessions = "SELECT id FROM source_delete_sessions"
        self.targets[prefix + "agent_notifications"] = f"session_id IN ({sessions}) OR (db_scope=(SELECT scope FROM source_delete_scope) AND {item_clause})"
        self.targets[prefix + "agent_messages"] = f"session_id IN ({sessions})"
        self.targets[prefix + "agent_jobs"] = f"session_id IN ({sessions})"
        self.targets[prefix + "agent_sessions"] = f"id IN ({sessions})"
        self.process_ids = [r[0] for r in conn.execute(f"SELECT id FROM {prefix}agent_jobs WHERE session_id IN ({sessions})")]
        for row in conn.execute("SELECT id,executor_session_id FROM source_delete_sessions"):
            for name in {row[0], row[1], f"newsreader-codex-agent-{row[0]}", f"newsreader-oar-pi-{row[0]}"} - {""}:
                # Do not remove a runtime shared with another library/session.
                if conn.execute(f"SELECT 1 FROM {prefix}agent_sessions WHERE id NOT IN ({sessions}) AND (id=? OR executor_session_id=?)", (name, name)).fetchone():
                    continue
                path = _within(self.runtime_root, name)
                if path and path.is_dir():
                    self.runtime_dirs.append(path)
                    self.files.extend(p for p in path.rglob("*") if p.is_file() and not p.is_symlink())
        conn.execute("""CREATE TEMP TABLE source_delete_pending AS SELECT item_id FROM collection_items
            WHERE status<>'done' AND feed_source_subkey(json_extract(raw_json,'$.url'),
                json_extract(raw_json,'$.source_type'),json_extract(raw_json,'$.source'),json_extract(raw_json,'$.source_name'))=?""", (key,))
        self.targets["collection_items"] = f"{item_clause} OR item_id IN (SELECT item_id FROM source_delete_pending)"
        conn.execute(f"""CREATE TEMP TABLE source_delete_identities AS
            SELECT identity_key FROM news_title_versions
            WHERE {item_clause} OR item_id IN (SELECT item_id FROM source_delete_pending)
            UNION SELECT identity_key FROM news_identities
            WHERE {item_clause} OR item_id IN (SELECT item_id FROM source_delete_pending)""")
        self.targets["news_title_versions"] = f"{item_clause} OR item_id IN (SELECT item_id FROM source_delete_pending)"
        self.targets["news_article_heads"] = "identity_key IN (SELECT identity_key FROM source_delete_identities)"
        self.targets["items"] = f"id IN ({ids})"

        digest = hashlib.sha256()
        stat = Path(database_path).stat()
        digest.update(f"{scope}:{stat.st_dev}:{stat.st_ino}:{key}".encode())
        database_bytes = 0
        counts = {}
        for table, condition in self.targets.items():
            count = 0
            digest.update(table.encode())
            for row in conn.execute(f"SELECT * FROM {table} WHERE {condition} ORDER BY rowid"):
                count += 1
                for value in row:
                    data = value if isinstance(value, bytes) else str(value).encode("utf-8") if value is not None else b""
                    database_bytes += len(data)
                    digest.update(str(len(data)).encode() + b":" + data)
            counts[table.split(".")[-1]] = count
        file_bytes = 0
        for path in sorted(set(self.files)):
            stat = path.stat()
            file_bytes += stat.st_size
            digest.update(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        self.preview = {
            "key": key, "label": source["label"], "source_label": source["source_label"],
            "news_count": len(self.item_ids), "first_date": dates[0], "last_date": dates[1],
            "pending_news_count": conn.execute("SELECT COUNT(*) FROM source_delete_pending p JOIN collection_items c ON c.item_id=p.item_id WHERE c.status IN ('pending','running','ready','failed')").fetchone()[0],
            "data_bytes": database_bytes + file_bytes, "database_bytes": database_bytes,
            "file_bytes": file_bytes, "counts": counts,
            "note_count": counts["article_note_entries"] + conn.execute(f"SELECT COUNT(*) FROM article_notes n WHERE {url_clause} AND NOT EXISTS (SELECT 1 FROM article_note_entries e WHERE e.url=n.url)").fetchone()[0],
            "shared_article_count": conn.execute(f"SELECT COUNT(DISTINCT url) FROM source_delete_items WHERE COALESCE(url,'')<>'' AND url NOT IN ({urls})").fetchone()[0],
            "confirmation_token": digest.hexdigest(),
        }

    def delete_rows(self):
        self.conn.executemany("INSERT OR IGNORE INTO deleted_news_items(fingerprint) VALUES (?)",
                              ((item_fingerprint(ident),) for ident in self.item_ids))
        self.conn.execute("""UPDATE news_identities SET deleted=1
            WHERE identity_key IN (SELECT identity_key FROM source_delete_identities)""")
        # Explicit predicates also cover old tables that have no foreign keys.
        # Note metadata and review children are removed before their parents.
        self.conn.execute("UPDATE review_chains SET parent_chain_id=NULL WHERE id IN (SELECT id FROM source_delete_reviews)")
        for table, condition in self.targets.items():
            self.conn.execute(f"DELETE FROM {table} WHERE {condition}")
        # A surviving source can still fetch a shared URL after its original job's
        # item was removed. Rebind it instead of deleting that shared job.
        self.conn.execute("""UPDATE detail_jobs SET item_id=(SELECT id FROM items WHERE items.url=detail_jobs.url LIMIT 1)
            WHERE item_id IN (SELECT id FROM source_delete_items)""")
        self.conn.execute("""UPDATE source_files SET item_count=(SELECT COUNT(*) FROM items WHERE items.source_file=source_files.path)
            WHERE path IN (SELECT source_file FROM source_delete_items)""")


class StagedFiles:
    """Rename within each cache before committing; restore on a DB/I/O failure."""
    def __init__(self, plan):
        self.plan, self.moves, self.folders = plan, [], []

    def stage(self):
        grouped = [(self.plan.media_root, list(set(self.plan.media_files))),
                   (self.plan.runtime_root, list(set(self.plan.runtime_dirs)))]
        for root, paths in grouped:
            if not paths:
                continue
            folder = Path(tempfile.mkdtemp(prefix=".source-delete-", dir=root))
            self.folders.append(folder)
            for index, path in enumerate(paths):
                if path.exists():
                    target = folder / str(index)
                    path.rename(target)
                    self.moves.append((path, target))

    def restore(self):
        for path, target in reversed(self.moves):
            target.rename(path)
        self.cleanup()

    def cleanup(self):
        for folder in self.folders:
            shutil.rmtree(folder)
