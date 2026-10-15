PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_files (
  path TEXT PRIMARY KEY,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  last_scanned_at TEXT NOT NULL,
  item_count INTEGER DEFAULT 0,
  ingest_mode TEXT,
  ingest_warning TEXT
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  source_file TEXT NOT NULL,
  item_order INTEGER NOT NULL,
  published_at TEXT NOT NULL,
  date TEXT NOT NULL,
  time TEXT,
  source TEXT,
  source_type TEXT,
  source_name TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_source_type ON items(source_type);

CREATE TABLE IF NOT EXISTS item_state (
  item_id TEXT PRIMARY KEY,
  bookmarked INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0,
  read_at TEXT,
  important_at TEXT,
  read_later_at TEXT,
  read_later_done_at TEXT,
  favorite_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detail_jobs (
  url TEXT PRIMARY KEY,
  item_id TEXT,
  source TEXT,
  status TEXT NOT NULL,
  attempts INTEGER DEFAULT 0,
  last_error TEXT,
  queued_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_detail_jobs_status ON detail_jobs(status);

CREATE TABLE IF NOT EXISTS article_details (
  url TEXT PRIMARY KEY,
  source TEXT,
  title TEXT,
  author TEXT,
  published_at TEXT,
  content TEXT,
  content_length INTEGER,
  raw_json TEXT,
  fetched_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_jobs (
  url TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  attempts INTEGER DEFAULT 0,
  last_error TEXT,
  queued_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);

CREATE TABLE IF NOT EXISTS article_ai (
  url TEXT PRIMARY KEY,
  model TEXT,
  key_points_zh TEXT,
  conclusion_zh TEXT,
  body_zh TEXT,
  raw_json TEXT,
  generated_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_highlights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  body_kind TEXT NOT NULL DEFAULT 'ai_zh' CHECK (body_kind IN ('ai_zh', 'ai_points_zh', 'ai_conclusion_zh', 'twitter_detail')),
  content_hash TEXT NOT NULL,
  start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
  end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
  selected_text TEXT NOT NULL CHECK (length(selected_text) > 0),
  color TEXT NOT NULL DEFAULT 'yellow' CHECK (color IN ('yellow', 'green', 'blue', 'pink')),
  annotation_text TEXT NOT NULL DEFAULT '' CHECK (length(annotation_text) <= 2000),
  prefix TEXT NOT NULL DEFAULT '',
  suffix TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (url, body_kind, content_hash, start_offset, end_offset)
);

CREATE INDEX IF NOT EXISTS idx_article_highlights_url
ON article_highlights(url, body_kind, created_at, id);

CREATE INDEX IF NOT EXISTS idx_article_highlights_content_hash
ON article_highlights(url, body_kind, content_hash);

CREATE TABLE IF NOT EXISTS reading_checkpoints (
  scope TEXT PRIMARY KEY,
  item_id TEXT,
  url TEXT,
  title TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_notes (
  url TEXT PRIMARY KEY,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_market_tags (
  url TEXT NOT NULL,
  tag TEXT NOT NULL,
  direction TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (url, tag)
);

CREATE TABLE IF NOT EXISTS market_tag_definitions (
  key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_tag_deleted_keys (
  key TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_trend_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date_key TEXT NOT NULL,
  tag TEXT NOT NULL,
  direction TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_tag_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag_key TEXT NOT NULL,
  range_days INTEGER NOT NULL,
  source_hash TEXT NOT NULL DEFAULT '',
  summary_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'success',
  error TEXT,
  model TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(tag_key, range_days)
);

CREATE INDEX IF NOT EXISTS idx_market_tag_summaries_tag_range
ON market_tag_summaries(tag_key, range_days);

CREATE TABLE IF NOT EXISTS market_pinned_notes (
  scope TEXT NOT NULL,
  tag_key TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  collapsed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (scope, tag_key)
);

CREATE TABLE IF NOT EXISTS news_reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT,
  item_title_snapshot TEXT NOT NULL,
  item_url_snapshot TEXT NOT NULL,
  event_title TEXT NOT NULL,
  event_date TEXT NOT NULL,
  remind_at TEXT NOT NULL,
  note TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_reminders_status_remind_at
ON news_reminders(status, remind_at);

CREATE INDEX IF NOT EXISTS idx_news_reminders_item_id
ON news_reminders(item_id);

CREATE TABLE IF NOT EXISTS tracked_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  keywords_json TEXT NOT NULL DEFAULT '[]',
  exclude_keywords_json TEXT NOT NULL DEFAULT '[]',
  rules_json TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'important',
  active INTEGER NOT NULL DEFAULT 1,
  last_incremental_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracked_topics_active_updated_at
ON tracked_topics(active, updated_at DESC);

CREATE TABLE IF NOT EXISTS tracked_topic_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  item_url TEXT,
  match_method TEXT NOT NULL DEFAULT 'keyword',
  score INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  hidden_at TEXT,
  manual_added_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(topic_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_tracked_topic_items_topic_hidden
ON tracked_topic_items(topic_id, hidden_at, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tracked_topic_items_item_id
ON tracked_topic_items(item_id);

CREATE TABLE IF NOT EXISTS tracked_topic_daily_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  item_ids_hash TEXT NOT NULL DEFAULT '',
  summary_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'success',
  error TEXT,
  model TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(topic_id, date)
);

CREATE INDEX IF NOT EXISTS idx_tracked_topic_daily_summaries_topic_date
ON tracked_topic_daily_summaries(topic_id, date DESC);


CREATE TABLE IF NOT EXISTS media_cache (
  url TEXT PRIMARY KEY,
  cache_key TEXT NOT NULL UNIQUE,
  relative_path TEXT NOT NULL,
  mime_type TEXT,
  size_bytes INTEGER DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_media_cache_created_at ON media_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_media_cache_status ON media_cache(status);

CREATE TABLE IF NOT EXISTS standalone_ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- ── 复盘功能 (v2.1.0) ──

CREATE TABLE IF NOT EXISTS review_chains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_chain_id INTEGER DEFAULT NULL REFERENCES review_chains(id),
  source_type TEXT NOT NULL,             -- 'article_note' | 'market_trend_note' | 'standalone_idea'
  source_key TEXT NOT NULL,              -- article_note: url; others: str(id)
  source_note TEXT NOT NULL,             -- 原想法正文快照
  source_created_at TEXT NOT NULL,
  source_tag_key TEXT DEFAULT '',
  source_tag_label TEXT DEFAULT '',
  source_snapshot_json TEXT NOT NULL DEFAULT '{}',  -- 完整来源快照（关联新闻等）
  status TEXT NOT NULL DEFAULT 'active', -- 'active' | 'done'
  current_version INTEGER NOT NULL DEFAULT 1,
  plan_review_date TEXT NOT NULL,
  result TEXT DEFAULT '',                -- 'confirmed' | 'refuted' | 'inconclusive'
  actual_text TEXT DEFAULT '',
  bias_text TEXT DEFAULT '',
  experience TEXT DEFAULT '',
  completed_at TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_chains_status_date
ON review_chains(status, plan_review_date);

CREATE INDEX IF NOT EXISTS idx_review_chains_source
ON review_chains(source_type, source_key);

CREATE TABLE IF NOT EXISTS review_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id INTEGER NOT NULL REFERENCES review_chains(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  judgment TEXT NOT NULL,
  criteria TEXT NOT NULL,
  revision_reason TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(chain_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_review_versions_chain
ON review_versions(chain_id, version_no);

CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id INTEGER NOT NULL REFERENCES review_chains(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,              -- 'progress' | 'revision' | 'review_completed' | 'continue_observing' | 'retracked'
  event_text TEXT DEFAULT '',
  event_date TEXT NOT NULL,
  version_id INTEGER DEFAULT NULL REFERENCES review_versions(id),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_events_chain
ON review_events(chain_id, created_at);

CREATE TABLE IF NOT EXISTS review_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id INTEGER NOT NULL REFERENCES review_chains(id) ON DELETE CASCADE,
  event_id INTEGER DEFAULT NULL REFERENCES review_events(id) ON DELETE CASCADE,
  news_title TEXT NOT NULL,
  news_summary TEXT DEFAULT '',
  news_url TEXT NOT NULL,
  added_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_evidence_chain
ON review_evidence(chain_id);
