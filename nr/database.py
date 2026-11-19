from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from feed_sources import feed_source_subkey_sql, is_bloomberg_video_url
from settings import resolve_db_path


class Database:
    def __init__(self):
        self.root = Path(__file__).resolve().parent.parent
        self.schema_path = self.root / "schema.sql"
        self.static_dir = self.root / "static"
        self.path = resolve_db_path()
        self.media_dir = self.resolve_media_cache_dir()
        self.agent_path = self.resolve_agent_session_db_path()
        self.agent_dir = self.resolve_agent_runtime_dir()

    def resolve_media_cache_dir(self) -> Path:
        env = os.environ.get("NEWS_READER_MEDIA_CACHE_DIR")
        if env:
            return Path(env).expanduser().resolve()
        return self.path.parent / "media-cache"

    def resolve_agent_session_db_path(self) -> Path:
        env = os.environ.get("NEWS_READER_AGENT_DB_PATH")
        if env:
            return Path(env).expanduser().resolve()
        return self.path.parent / "agent_sessions.sqlite3"

    def resolve_agent_runtime_dir(self) -> Path:
        env = os.environ.get("NEWS_READER_AGENT_RUNTIME_DIR")
        if env:
            return Path(env).expanduser().resolve()
        # CLI 会话文件放在系统临时目录，不让默认运行态目录落入仓库或常规备份。
        project_id = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / f"newsreader-workbench-{project_id}" / "agent"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.create_function("feed_source_subkey", 4, feed_source_subkey_sql, deterministic=True)
        except TypeError:  # pragma: no cover - older sqlite/python fallback
            conn.create_function("feed_source_subkey", 4, feed_source_subkey_sql)
        try:
            conn.create_function("is_bloomberg_video_url", 1, lambda url: int(is_bloomberg_video_url(url)), deterministic=True)
        except TypeError:  # pragma: no cover - older sqlite/python fallback
            conn.create_function("is_bloomberg_video_url", 1, lambda url: int(is_bloomberg_video_url(url)))
        return conn

    def agent_connect(self) -> sqlite3.Connection:
        self.agent_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.agent_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
