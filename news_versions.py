"""Read saved v0.5.2 title variants and retire their unpublished duplicates."""
import json

from news_identity import identity_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_title_versions (
 version_key TEXT PRIMARY KEY, identity_key TEXT NOT NULL, item_id TEXT NOT NULL UNIQUE,
 original_title TEXT NOT NULL, is_update INTEGER NOT NULL DEFAULT 0,
 original_item_id TEXT NOT NULL, previous_item_id TEXT, observed_at TEXT NOT NULL,
 baseline_only INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_news_versions_identity ON news_title_versions(identity_key);
CREATE TABLE IF NOT EXISTS news_version_migrations (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS news_article_heads (identity_key TEXT PRIMARY KEY, item_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_version_snapshots (
 item_id TEXT PRIMARY KEY, detail_json TEXT, ai_json TEXT);
"""


def install_schema(conn):
    conn.executescript(SCHEMA)
    # Scalar lookups keep these views flattenable inside LEFT JOINs. Joining
    # cache tables inside the view would materialize the entire library for
    # every 30-row page (and repeatedly decode all historical bodies).
    def cache_view(name, table, column, fields, saved_fields=None):
        saved = "EXISTS(SELECT 1 FROM news_version_snapshots WHERE item_id=i.id)"
        snapshot = f"(SELECT {column} FROM news_version_snapshots WHERE item_id=i.id)"
        expressions = []
        for field in fields.split():
            historical = (saved_fields[field] if saved_fields else
                          f"json_extract({snapshot},'$.{field}')")
            expressions.append(f"CASE WHEN {saved} THEN {historical} ELSE "
                               f"(SELECT {field} FROM {table} WHERE url=i.url) END AS {field}")
        conn.execute(f"""CREATE VIEW IF NOT EXISTS {name} AS
            SELECT i.id AS item_id,{','.join(expressions)} FROM items i
            WHERE CASE WHEN {saved} THEN {snapshot} IS NOT NULL
                ELSE EXISTS(SELECT 1 FROM {table} WHERE url=i.url) END""")

    cache_view("news_item_details", "article_details", "detail_json",
               "url source title author published_at content content_length raw_json fetched_at updated_at")
    cache_view("news_item_ai", "article_ai", "ai_json",
               "url model key_points_zh conclusion_zh body_zh raw_json generated_at updated_at")
    # Saved historical content is ready; old pending/failed tasks must never
    # point at the newer URL-owned queue. An empty snapshot has no ready job.
    for name, table, column, timestamp, extra in (
        ("news_item_detail_jobs", "detail_jobs", "detail_json", "fetched_at", "source next_attempt_at worker_pid"),
        ("news_item_ai_jobs", "ai_jobs", "ai_json", "generated_at", ""),
    ):
        snapshot = f"(SELECT {column} FROM news_version_snapshots WHERE item_id=i.id)"
        saved = {field: "NULL" for field in ("last_error", "started_at", "next_attempt_at", "worker_pid")}
        saved.update(url="i.url", status="'success'", attempts="0",
                     source=f"json_extract({snapshot},'$.source')")
        for field in ("queued_at", "finished_at", "updated_at"):
            saved[field] = f"json_extract({snapshot},'$.{timestamp if field != 'updated_at' else field}')"
        cache_view(name, table, column,
                   "url status attempts last_error queued_at started_at finished_at updated_at " + extra, saved)


def skip_duplicate_job(conn, job):
    """Retire an unpublished v0.5.2 title variant without deleting any news."""
    if not conn.execute("""SELECT 1 FROM news_title_versions v WHERE v.item_id=?
        AND NOT EXISTS (SELECT 1 FROM items i WHERE i.id=v.item_id)""", (job["item_id"],)).fetchone():
        return False
    try:
        key = identity_key(json.loads(job["raw_json"])["url"])
    except (ValueError, TypeError, KeyError):
        # An invalid legacy payload must not prevent the database from opening.
        # Leave its failure/retry handling to the existing job lifecycle.
        return False
    owner = conn.execute("SELECT item_id FROM news_identities WHERE identity_key=?", (key,)).fetchone()
    already_published = conn.execute("""SELECT 1 FROM news_title_versions v JOIN items i ON i.id=v.item_id
        WHERE v.identity_key=? LIMIT 1""", (key,)).fetchone()
    if not ((owner and owner[0] != job["item_id"]) or already_published):
        return False
    changed = conn.execute("""UPDATE collection_items SET status='duplicate',error='',lease=NULL,lease_until=0
        WHERE item_id=? AND status IN ('pending','running','ready','failed')""", (job["item_id"],)).rowcount
    if changed:
        # Preserve unknown historical counts; never invent a complete total.
        conn.execute("""UPDATE collection_run_sources SET added=MAX(0,added-1),duplicate_count=duplicate_count+1
            WHERE run_id=? AND source_id=?""", (job["run_id"],job["source_id"]))
    return True


def retire_duplicate_jobs(conn):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    runs = set()
    # Only v0.5.2 jobs can carry a second title for the same URL. Ordinary
    # URL-only jobs need no per-item compatibility work on every startup.
    for job in conn.execute("""SELECT c.* FROM collection_items c
        JOIN news_title_versions v ON v.item_id=c.item_id
        WHERE c.status IN ('pending','running','ready','failed')""").fetchall():
        if skip_duplicate_job(conn, job):
            runs.add(job["run_id"])
    for run_id in runs:
        # An empty formerly failed batch can finish through normal publication.
        conn.execute("""UPDATE collection_runs SET publication_status='pending',publication_error=''
            WHERE id=? AND publication_status='failed' AND NOT EXISTS (
                SELECT 1 FROM collection_items WHERE run_id=? AND status NOT IN ('done','deleted','duplicate'))""",
            (run_id,run_id))


def cache_token(conn, url):
    try:
        key = identity_key(url)
    except (ValueError, TypeError):
        return ""
    row = conn.execute("SELECT item_id FROM news_article_heads WHERE identity_key=?", (key,)).fetchone()
    return row[0] if row else ""


def archived(conn, item_id):
    return conn.execute("SELECT 1 FROM news_version_snapshots WHERE item_id=?", (item_id,)).fetchone() is not None


def attach(conn, items):
    for offset in range(0, len(items), 500):
        batch = items[offset:offset + 500]
        ids = [item["id"] for item in batch]
        marks = ",".join("?" for _ in ids)
        versions = {r["item_id"]: dict(r) for r in conn.execute(f"""SELECT v.item_id,v.previous_item_id,
            h.item_id AS latest_item_id FROM news_title_versions v
            LEFT JOIN news_article_heads h ON h.identity_key=v.identity_key WHERE v.item_id IN ({marks})""", ids)}
        frozen = {r[0]: r[1] for r in conn.execute(f"""SELECT s.item_id,i.url FROM news_version_snapshots s
            JOIN items i ON i.id=s.item_id WHERE s.item_id IN ({marks})""", ids)}
        for item in batch:
            item.update(versions.get(item["id"], {}))
            item["archived_version"] = item["id"] in frozen
            if item["archived_version"] and item["id"] not in versions:
                # Older file imports could retain several IDs for one URL.
                item["latest_item_id"] = cache_token(conn, frozen[item["id"]]) or None
