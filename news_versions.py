"""Article title versions, with stable legacy IDs and frozen reader caches."""
import hashlib
import html
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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


def canonical_url(url):
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise ValueError("collection_invalid_url")
    if host.removeprefix("www.") in {"twitter.com", "x.com", "mobile.twitter.com"}:
        match = re.search(r"/(?:status|statuses)/(\d+)", parts.path)
        if match:
            return "https://x.com/i/status/" + match[1]
    host = host.removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host += f":{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", host, parts.path or "/", urlencode(query), ""))


def identity_key(url):
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def is_tweet(url, source_type=""):
    return source_type == "twitter" or (urlsplit(url).hostname or "").lower().removeprefix("www.") in {
        "x.com", "twitter.com", "mobile.twitter.com"}


def normalized_title(title):
    text = unicodedata.normalize("NFC", html.unescape(str(title or "")))
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "\ufeff": "", "\u200b": ""}))
    return " ".join(text.split())


def version_key(url, title):
    payload = json.dumps([canonical_url(url), normalized_title(title)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


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


def register(conn, url, title, item_id, original_id, observed_at, *, update=False, previous_id=None, baseline=False):
    conn.execute("""INSERT OR IGNORE INTO news_title_versions
        (version_key,identity_key,item_id,original_title,is_update,original_item_id,previous_item_id,observed_at,baseline_only)
        VALUES (?,?,?,?,?,?,?,?,?)""", (version_key(url, title), identity_key(url), item_id, title,
                                       int(update), original_id, previous_id, observed_at, int(baseline)))


def backfill(conn):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM news_version_migrations WHERE name='original-title-v1'").fetchone():
        return
    for row in conn.execute("SELECT item_id,raw_json,collected_at FROM collection_items ORDER BY collected_at,item_id").fetchall():
        try:
            raw = json.loads(row["raw_json"])
            url, title = raw["url"], raw["title"]
            if is_tweet(url, raw.get("source_type")) or not normalized_title(title):
                continue
            key = identity_key(url)
        except (ValueError, TypeError, KeyError):
            continue
        original = conn.execute("SELECT item_id,deleted FROM news_identities WHERE identity_key=?", (key,)).fetchone()
        if original and not original["deleted"]:
            register(conn, url, title, row["item_id"], original["item_id"], row["collected_at"])
    conn.execute("""INSERT OR IGNORE INTO news_article_heads SELECT n.identity_key,n.item_id
        FROM news_identities n JOIN items i ON i.id=n.item_id WHERE n.deleted=0""")
    conn.execute("INSERT INTO news_version_migrations VALUES ('original-title-v1')")


def cache_token(conn, url):
    try:
        key = identity_key(url)
    except (ValueError, TypeError):
        return ""
    row = conn.execute("SELECT item_id FROM news_article_heads WHERE identity_key=?", (key,)).fetchone()
    return row[0] if row else ""


def archived(conn, item_id):
    return conn.execute("SELECT 1 FROM news_version_snapshots WHERE item_id=?", (item_id,)).fetchone() is not None


def publish(conn, item_id):
    version = conn.execute("SELECT rowid AS seq,* FROM news_title_versions WHERE item_id=?", (item_id,)).fetchone()
    if not version:
        return
    head = conn.execute("""SELECT v.rowid FROM news_article_heads h
        JOIN news_title_versions v ON v.item_id=h.item_id WHERE h.identity_key=?""",
        (version["identity_key"],)).fetchone()
    if head and head[0] > version["seq"]:
        # Translation retries may publish an older observation after a newer one.
        # Its historical body was never captured; keep the current cache intact.
        conn.execute("INSERT OR IGNORE INTO news_version_snapshots(item_id) VALUES (?)", (item_id,))
        return
    if version["is_update"]:
        # Freeze all previously visible rows for this article, including legacy
        # aliases; do not change their titles, IDs, notes or reading state.
        urls = {r[0] for r in conn.execute("""SELECT url FROM items WHERE id IN
            (SELECT item_id FROM news_title_versions WHERE identity_key=?) OR id=?""",
            (version["identity_key"], version["original_item_id"])) if r[0]}
        for url in urls:
            detail = conn.execute("SELECT * FROM article_details WHERE url=?", (url,)).fetchone()
            ai = conn.execute("SELECT * FROM article_ai WHERE url=?", (url,)).fetchone()
            data = tuple(json.dumps(dict(row), ensure_ascii=False) if row else None for row in (detail, ai))
            conn.execute("""INSERT OR IGNORE INTO news_version_snapshots(item_id,detail_json,ai_json)
                SELECT id,?,? FROM items WHERE url=? AND id<>?""", (*data, url, item_id))
            for table in ("article_details", "article_ai", "detail_jobs", "ai_jobs"):
                conn.execute(f"DELETE FROM {table} WHERE url=?", (url,))
    conn.execute("INSERT OR REPLACE INTO news_article_heads VALUES (?,?)", (version["identity_key"], item_id))


def attach(conn, items):
    for offset in range(0, len(items), 500):
        batch = items[offset:offset + 500]
        ids = [item["id"] for item in batch]
        marks = ",".join("?" for _ in ids)
        versions = {r["item_id"]: dict(r) for r in conn.execute(f"""SELECT v.item_id,v.is_update,
            v.original_item_id,v.previous_item_id,v.observed_at AS update_detected_at,v.baseline_only,
            h.item_id AS latest_item_id FROM news_title_versions v
            LEFT JOIN news_article_heads h ON h.identity_key=v.identity_key WHERE v.item_id IN ({marks})""", ids)}
        frozen = {r[0]: r[1] for r in conn.execute(f"""SELECT s.item_id,i.url FROM news_version_snapshots s
            JOIN items i ON i.id=s.item_id WHERE s.item_id IN ({marks})""", ids)}
        for item in batch:
            item.update(versions.get(item["id"], {"is_update": 0}))
            item["archived_version"] = item["id"] in frozen
            if item["archived_version"] and item["id"] not in versions:
                # Older file imports could retain several IDs for one URL.
                item["latest_item_id"] = cache_token(conn, frozen[item["id"]]) or None
