from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from parser import parse_daily_file, parse_daily_json_file, sidecar_path_for_daily

INGEST_MODE_SIDECAR_JSON = "sidecar_json"
INGEST_MODE_MARKDOWN_FALLBACK = "markdown_fallback"
INGEST_MODE_MARKDOWN_ONLY = "markdown_only"


@dataclass
class ReindexStats:
    scanned_files: int = 0
    changed_files: int = 0
    upserted: int = 0
    deleted_stale: int = 0
    ingest_counts: dict[str, int] = field(
        default_factory=lambda: {
            INGEST_MODE_SIDECAR_JSON: 0,
            INGEST_MODE_MARKDOWN_FALLBACK: 0,
            INGEST_MODE_MARKDOWN_ONLY: 0,
        }
    )

    def record_ingest_mode(self, ingest_mode: str | None) -> None:
        mode = ingest_mode if ingest_mode in self.ingest_counts else INGEST_MODE_MARKDOWN_ONLY
        self.ingest_counts[mode] += 1


@dataclass
class ParsedSource:
    items: list
    ingest_mode: str
    ingest_warning: str | None = None


@dataclass
class SourceRecord:
    mtime: float
    size: int
    ingest_mode: str | None
    ingest_warning: str | None


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_item_id(date: str, source: str, title: str, url: str) -> str:
    basis = f"{date}|{url}" if url else f"{date}|{source}|{title}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))
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
    conn.commit()


def list_daily_files(root: Path) -> list[Path]:
    return sorted(root.rglob("dailyFreshNews_*.md"))


def file_signature(path: Path, sidecar_path: Path | None = None) -> tuple[float, int]:
    md_stat = path.stat()
    sidecar_exists = bool(sidecar_path and sidecar_path.exists())
    sidecar_stat = sidecar_path.stat() if sidecar_exists and sidecar_path else None
    latest_mtime = max(float(md_stat.st_mtime), float(sidecar_stat.st_mtime) if sidecar_stat else 0.0)
    basis = "|".join(
        [
            f"md:{md_stat.st_mtime_ns}:{md_stat.st_size}",
            f"sidecar:{sidecar_stat.st_mtime_ns}:{sidecar_stat.st_size}" if sidecar_stat else "sidecar:missing",
        ]
    )
    signature = int(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:15], 16)
    return latest_mtime, signature


def parse_daily_source(path: Path) -> ParsedSource:
    sidecar_path = sidecar_path_for_daily(path)
    if sidecar_path.exists():
        try:
            return ParsedSource(
                items=parse_daily_json_file(sidecar_path),
                ingest_mode=INGEST_MODE_SIDECAR_JSON,
            )
        except Exception as exc:
            return ParsedSource(
                items=parse_daily_file(path),
                ingest_mode=INGEST_MODE_MARKDOWN_FALLBACK,
                ingest_warning=str(exc) or exc.__class__.__name__,
            )
    return ParsedSource(
        items=parse_daily_file(path),
        ingest_mode=INGEST_MODE_MARKDOWN_ONLY,
    )


def load_source_record(conn: sqlite3.Connection, rel_path: str) -> SourceRecord | None:
    row = conn.execute(
        "SELECT mtime, size, ingest_mode, ingest_warning FROM source_files WHERE path = ?",
        (rel_path,),
    ).fetchone()
    if not row:
        return None
    return SourceRecord(
        mtime=float(row[0]),
        size=int(row[1]),
        ingest_mode=row[2],
        ingest_warning=row[3],
    )


def reindex(
    conn: sqlite3.Connection,
    daily_root: Path,
    *,
    full: bool = False,
) -> ReindexStats:
    stats = ReindexStats()
    files = list_daily_files(daily_root)
    stats.scanned_files = len(files)

    if full:
        conn.execute("DELETE FROM items")
        conn.execute("DELETE FROM source_files")
        conn.commit()

    for path in files:
        rel_path = str(path.relative_to(daily_root))
        sidecar_path = sidecar_path_for_daily(path)
        current = file_signature(path, sidecar_path)
        old = load_source_record(conn, rel_path)
        if old and (old.mtime, old.size) == current and old.ingest_mode:
            stats.record_ingest_mode(old.ingest_mode)
            continue

        stats.changed_files += 1
        parsed_source = parse_daily_source(path)
        parsed = parsed_source.items
        ts = now_iso()
        new_ids: list[str] = []
        stats.record_ingest_mode(parsed_source.ingest_mode)

        with conn:
            for it in parsed:
                item_id = make_item_id(it.date, it.source, it.title, it.url)
                new_ids.append(item_id)
                conn.execute(
                    """
                    INSERT INTO items (
                      id, source_file, item_order, published_at, date, time, source,
                      source_type, source_name, title, summary, url, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      source_file=excluded.source_file,
                      item_order=excluded.item_order,
                      published_at=excluded.published_at,
                      date=excluded.date,
                      time=excluded.time,
                      source=excluded.source,
                      source_type=excluded.source_type,
                      source_name=excluded.source_name,
                      title=excluded.title,
                      summary=excluded.summary,
                      url=excluded.url,
                      updated_at=excluded.updated_at
                    """,
                    (
                        item_id,
                        rel_path,
                        it.item_order,
                        it.published_at,
                        it.date,
                        it.time,
                        it.source,
                        it.source_type,
                        it.source_name,
                        it.title,
                        it.summary,
                        it.url,
                        ts,
                        ts,
                    ),
                )
                stats.upserted += 1

            if new_ids:
                placeholders = ",".join("?" for _ in new_ids)
                cur = conn.execute(
                    f"DELETE FROM items WHERE source_file=? AND id NOT IN ({placeholders})",
                    [rel_path, *new_ids],
                )
            else:
                cur = conn.execute("DELETE FROM items WHERE source_file=?", (rel_path,))
            stats.deleted_stale += cur.rowcount

            conn.execute(
                """
                INSERT INTO source_files (path, mtime, size, last_scanned_at, item_count, ingest_mode, ingest_warning)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  mtime=excluded.mtime,
                  size=excluded.size,
                  last_scanned_at=excluded.last_scanned_at,
                  item_count=excluded.item_count,
                  ingest_mode=excluded.ingest_mode,
                  ingest_warning=excluded.ingest_warning
                """,
                (
                    rel_path,
                    current[0],
                    current[1],
                    ts,
                    len(parsed),
                    parsed_source.ingest_mode,
                    parsed_source.ingest_warning,
                ),
            )

    conn.commit()
    return stats
