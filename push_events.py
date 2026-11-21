import json
import time


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS browser_push_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, event_key TEXT NOT NULL,
        library TEXT NOT NULL DEFAULT '', target TEXT NOT NULL, created_at REAL NOT NULL,
        UNIQUE(kind,event_key,library))""")
    conn.execute("CREATE INDEX IF NOT EXISTS browser_push_event_library ON browser_push_events(library,seq)")


def record(conn, kind, key, target, library):
    conn.execute("""INSERT OR IGNORE INTO browser_push_events(kind,event_key,library,target,created_at)
        VALUES(?,?,?,?,?)""", (kind, key, library, json.dumps(target), round(time.time(), 3)))
