"""Library-owned X subscriptions and immutable per-run collection policies."""
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from feed_sources import derive_source_subkey


def normalize_handle(value):
    if not isinstance(value, str):
        raise ValueError("invalid_x_handle")
    value = value.strip()
    if re.match(r"^(https?://)?(www\.)?(x\.com|twitter\.com)/", value, re.I):
        parsed = urlsplit(value if "://" in value else "https://" + value)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 1 or parsed.username or parsed.password or parsed.port:
            raise ValueError("invalid_x_handle")
        value = parts[0]
    else:
        value = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value) or value.lower() in {
        "home", "explore", "search", "notifications", "messages", "settings", "intent", "share", "i",
    }:
        raise ValueError("invalid_x_handle")
    return value.lower()


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS x_accounts (
        handle TEXT PRIMARY KEY, name TEXT NOT NULL, section TEXT NOT NULL UNIQUE,
        auto_translate INTEGER NOT NULL DEFAULT 0, auto_fulltext INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE TABLE IF NOT EXISTS x_accounts_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision TEXT NOT NULL)")
    # The marker survives removing the last account: an empty list stays empty.
    if conn.execute("SELECT 1 FROM x_accounts_meta WHERE singleton=1").fetchone():
        return
    defaults = json.loads((Path(__file__).parent / "collection/sources.json").read_text())
    for source in defaults:
        if source["source_type"] == "twitter":
            conn.execute("INSERT OR IGNORE INTO x_accounts(handle,name,section) VALUES (?,?,?)",
                         (normalize_handle(source["source_handle"]), source["display_name"], source["section"]))
    conn.execute("INSERT INTO x_accounts_meta VALUES (1,?)", (uuid.uuid4().hex,))


def snapshot(conn):
    return {"x_accounts": [{"handle": r["handle"], "name": r["name"],
                             "auto_translate": bool(r["auto_translate"]), "auto_fulltext": bool(r["auto_fulltext"])}
                            for r in conn.execute("SELECT * FROM x_accounts ORDER BY rowid")],
            "x_accounts_revision": conn.execute("SELECT revision FROM x_accounts_meta WHERE singleton=1").fetchone()[0]}


def normalize(accounts):
    if not isinstance(accounts, list) or len(accounts) > 200:
        raise ValueError("invalid_x_accounts")
    result, seen = [], set()
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("invalid_x_accounts")
        handle = normalize_handle(account.get("handle"))
        if handle in seen:
            raise ValueError("duplicate_x_account")
        seen.add(handle)
        options = {key: account.get(key, False) for key in ("auto_translate", "auto_fulltext")}
        if any(type(v) is not bool for v in options.values()):
            raise ValueError("invalid_x_account_options")
        result.append({"handle": handle, **options})
    return result


def check_revision(conn, revision):
    if revision != snapshot(conn)["x_accounts_revision"]:
        raise ValueError("x_accounts_changed")


def changed(conn):
    conn.execute("UPDATE x_accounts_meta SET revision=? WHERE singleton=1", (uuid.uuid4().hex,))


def save(conn, accounts, revision):
    accounts = normalize(accounts)
    check_revision(conn, revision)
    existing = {row["handle"]: dict(row) for row in conn.execute("SELECT * FROM x_accounts")}
    # Saved removals must go through the explicit keep/delete-history action.
    if set(existing) - {a["handle"] for a in accounts}:
        raise ValueError("x_account_removal_required")
    dirty = False
    for account in accounts:
        handle = account["handle"]
        old = existing.get(handle)
        if old and all(bool(old[k]) == account[k] for k in ("auto_translate", "auto_fulltext")):
            continue
        conn.execute("""INSERT INTO x_accounts(handle,name,section,auto_translate,auto_fulltext) VALUES (?,?,?,?,?)
            ON CONFLICT(handle) DO UPDATE SET auto_translate=excluded.auto_translate,auto_fulltext=excluded.auto_fulltext""",
            (handle, old["name"] if old else handle, old["section"] if old else "x/" + handle,
             account["auto_translate"], account["auto_fulltext"]))
        dirty = True
    if dirty:
        changed(conn)


def source_config(handle, *, name=None, section=None, auto_translate=False, auto_fulltext=False, limit=10):
    return {"section": section or "x/" + handle, "display_name": name or handle, "source_name": name or handle,
            "source_type": "twitter", "source_handle": handle,
            "translation_policy": "always" if auto_translate else "never",
            "auto_fulltext": bool(auto_fulltext), "auto_translate": bool(auto_translate),
            "command": ["opencli", "twitter", "tweets", handle, "--limit", str(limit), "-f", "json",
                        "--window", "background", "--site-session", "persistent"]}


def sources(conn):
    return [source_config(**dict(r)) for r in conn.execute("SELECT * FROM x_accounts ORDER BY rowid")]


def test_account(handle, acquire):
    # Use the real collection command/normalizer without staging, translating,
    # publishing or saving any account. A reachable profile alone is not enough.
    source = {**source_config(handle, limit=3), "command_timeout_seconds": 45,
              "treat_empty_as_failure": True, "min_valid_items": 1}
    try:
        result = acquire(source)
    except Exception:
        raise ValueError("x_account_test_failed") from None
    if not result.get("ok"):
        code = result.get("error_code")
        if code == "EMPTY_RESULT":
            raise ValueError("x_account_empty")
        if code == "NOT_FOUND":
            raise ValueError("x_account_unavailable")
        if code in {"AUTH_REQUIRED", "TIMEOUT", "BROWSER_CONNECT", "NETWORK_ERROR", "RETRY_AFTER",
                    "COMMAND_UNAVAILABLE", "OPENCLI_SETUP", "INVALID_JSON"}:
            raise ValueError(code)
        raise ValueError("x_account_test_failed")
    urls = {item["url"] for item in result.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"].strip()
            and isinstance(item.get("url"), str)
            and re.fullmatch(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]{1,15}/status/[0-9]+(?:[?#].*)?", item["url"], re.I)}
    if not urls:
        raise ValueError("x_account_empty")
    return {"handle": handle, "count": len(urls)}


def item_auto_translate(conn, item_id):
    row = conn.execute("SELECT url,source_type,source,source_name FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return False
    source = derive_source_subkey(row["url"], row["source_type"], row["source"], row["source_name"])
    collected = (conn.execute("SELECT json_extract(raw_json,'$.source_handle') FROM collection_items WHERE item_id=?", (item_id,)).fetchone()
                 if conn.execute("SELECT 1 FROM sqlite_master WHERE name='collection_items'").fetchone() else None)
    handle = (collected[0] if collected else None) or (source["key"][2:] if source and source["key"].startswith("x/") else "")
    account = conn.execute("SELECT auto_translate FROM x_accounts WHERE handle=?", (handle.lower(),)).fetchone()
    return bool(account and account[0])


def remove(conn, handle, *, delete_history):
    row = conn.execute("SELECT section FROM x_accounts WHERE handle=?", (handle,)).fetchone()
    if not row:
        raise ValueError("x_account_not_found")
    if delete_history:
        # Revoke both unstarted and in-flight source work. Workers check this
        # again before storing results, even after the account is re-added.
        conn.execute("""UPDATE collection_run_sources SET status='canceled',error=''
            WHERE (source_id=? OR lower(json_extract(config_json,'$.source_handle'))=?)
            AND status IN ('pending','running')""", (row[0], handle))
    conn.execute("DELETE FROM x_accounts WHERE handle=?", (handle,))
    changed(conn)
