SOURCES = {
    "classification": ("item_classifications", "item_id", "last_error", False),
    "topic": ("topic_jobs", "id", "error", True),
    "matching": ("topic_checks", "seq", "last_error", False),
    "report": ("periodic_reports", "id", "error", True),
}


def install(conn, sources):
    conn.execute("""CREATE TABLE IF NOT EXISTS task_progress_notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, item_key TEXT NOT NULL,
        dismissed INTEGER NOT NULL DEFAULT 0, UNIQUE(source,item_key))""")
    for source in sources:
        table, key, error, retries = SOURCES[source]
        trigger = "task_notice_" + source
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (trigger + "_update",)).fetchone():
            continue
        condition = "{p}status='failed'"
        if retries:
            condition += f" OR ({{p}}status='pending' AND COALESCE({{p}}{error},'')<>'')"
        current = condition.format(p="NEW.")
        remove = f"DELETE FROM task_progress_notices WHERE source='{source}' AND item_key=OLD.{key};"
        insert = f"""INSERT INTO task_progress_notices(source,item_key)
            SELECT '{source}',NEW.{key} WHERE {current};"""
        conn.execute(f"""CREATE TRIGGER {trigger}_insert AFTER INSERT ON {table}
            WHEN ({current}) OR EXISTS(SELECT 1 FROM task_progress_notices WHERE source='{source}' AND item_key=NEW.{key})
            BEGIN DELETE FROM task_progress_notices WHERE source='{source}' AND item_key=NEW.{key}; {insert} END""")
        conn.execute(f"""CREATE TRIGGER {trigger}_delete AFTER DELETE ON {table} WHEN {condition.format(p="OLD.")}
            BEGIN {remove} END""")
        conn.execute(f"""INSERT INTO task_progress_notices(source,item_key)
            SELECT ?,{key} FROM {table} WHERE {condition.format(p="")}""", (source,))
        conn.execute(f"""CREATE TRIGGER {trigger}_update AFTER UPDATE OF status,{error} ON {table}
            WHEN (OLD.status IS NOT NEW.status OR OLD.{error} IS NOT NEW.{error})
            AND (({condition.format(p="OLD.")}) OR ({current}))
            BEGIN {remove} {insert} END""")
