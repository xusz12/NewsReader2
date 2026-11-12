"""SQLite schema and migrations, independent of news input files."""
import sqlite3
from pathlib import Path
import topic_tracking
import news_versions
import x_accounts

def apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.executescript(topic_tracking.SCHEMA)
    if "archived_at" not in {r[1] for r in conn.execute("PRAGMA table_info(event_topics)")}:
        conn.execute("ALTER TABLE event_topics ADD COLUMN archived_at TEXT")
    for table, additions in {
        "event_topics": (("horizontal_rule", "TEXT NOT NULL DEFAULT ''"), ("rules_revision", "INTEGER NOT NULL DEFAULT 1")),
        "topic_checks": (("rules_json", "TEXT NOT NULL DEFAULT '{}'"),),
        "topic_jobs": (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_attempt_at", "REAL NOT NULL DEFAULT 0")),
    }.items():
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in additions:
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.execute("UPDATE event_topics SET horizontal_rule=? WHERE horizontal_rule=''", (topic_tracking.DEFAULT_HORIZONTAL_RULE,))
    conn.execute("""UPDATE topic_jobs SET kind='milestone',status='pending',config_json='{}',lease_token=NULL,
        lease_until=0,error='',attempts=0,next_attempt_at=0 WHERE kind='update' AND status IN ('pending','running','failed')
        AND topic_id IN (SELECT id FROM event_topics WHERE timeline_mode='events' AND archived_at IS NULL)""")
    topic_tracking.seed_ingestions(conn)
    detail_job_cols = {r[1] for r in conn.execute("PRAGMA table_info(detail_jobs)")}
    for name, kind in (("next_attempt_at", "TEXT"), ("worker_pid", "INTEGER"), ("auto_translate", "INTEGER NOT NULL DEFAULT 0")):
        if name not in detail_job_cols:
            conn.execute(f"ALTER TABLE detail_jobs ADD COLUMN {name} {kind}")
    source_file_cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(source_files)").fetchall()
    }
    if "ingest_mode" not in source_file_cols:
        conn.execute("ALTER TABLE source_files ADD COLUMN ingest_mode TEXT")
    if "ingest_warning" not in source_file_cols:
        conn.execute("ALTER TABLE source_files ADD COLUMN ingest_warning TEXT")
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(item_state)").fetchall()
    }
    if "important_at" not in cols:
        conn.execute("ALTER TABLE item_state ADD COLUMN important_at TEXT")
    if "read_later_at" not in cols:
        conn.execute("ALTER TABLE item_state ADD COLUMN read_later_at TEXT")
    if "read_later_done_at" not in cols:
        conn.execute("ALTER TABLE item_state ADD COLUMN read_later_done_at TEXT")
    if "favorite_at" not in cols:
        conn.execute("ALTER TABLE item_state ADD COLUMN favorite_at TEXT")
    # Install after legacy columns have been migrated, including on old databases.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_state_favorites ON item_state(item_id) WHERE favorite_at IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_state_important ON item_state(item_id) WHERE important_at IS NOT NULL")
    if "bookmarked" in cols:
        conn.execute(
            """
            UPDATE item_state
            SET favorite_at = COALESCE(
              favorite_at,
              updated_at,
              read_at,
              important_at,
              read_later_at,
              datetime('now', 'localtime')
            )
            WHERE bookmarked = 1 AND favorite_at IS NULL
            """
        )
    tracked_topic_cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(tracked_topics)").fetchall()
    }
    if "rules_json" not in tracked_topic_cols:
        conn.execute("ALTER TABLE tracked_topics ADD COLUMN rules_json TEXT NOT NULL DEFAULT ''")
    news_versions.install_schema(conn)
    x_accounts.initialize(conn)
    # Preserve the queue users saw before cache preparation became independent.
    # Run once, before automatic prefetch can create any new cached articles.
    if not conn.execute("SELECT 1 FROM news_version_migrations WHERE name='explicit_read_later'").fetchone():
        conn.execute("""INSERT INTO item_state(item_id,read_later_at,updated_at)
            SELECT ad.item_id,COALESCE(ad.fetched_at,ad.updated_at,datetime('now')),
                COALESCE(ad.updated_at,ad.fetched_at,datetime('now')) FROM news_item_details ad
            LEFT JOIN item_state st ON st.item_id=ad.item_id
            WHERE st.read_later_at IS NULL AND st.read_later_done_at IS NULL
            ON CONFLICT(item_id) DO UPDATE SET read_later_at=excluded.read_later_at""")
        conn.execute("INSERT INTO news_version_migrations VALUES ('explicit_read_later')")
    conn.commit()
