from contextlib import closing
from dataclasses import dataclass
from threading import Lock, RLock

import collection_environment
import news_classification
import news_collection
import news_summaries
import periodic_reports
import topic_tracking
import trading_calendar
from database_schema import apply_schema
from nr import (
    agent_jobs, agent_sessions, article_fetch, browser_push, configuration, details, highlights,
    market, news_chat, preferences, reminders, summaries, tasks, updates,
)
from settings import resolve_db_path


@dataclass
class BackgroundServices:
    classification: news_classification.Service
    collection: news_collection.Service
    reports: periodic_reports.Service
    summaries: news_summaries.Service
    topics: topic_tracking.Service
    report_lock: RLock

    def start(self, classification):
        self.classification.configure(**classification)
        self.classification.start()
        self.topics.start()
        self.reports.start()
        self.collection.start()


class Workbench:
    def __init__(self, db):
        self.db = db
        self.preferences = preferences.Preferences(db)
        self.updates = updates.Updates(db)
        self.push = browser_push.Service(db)
        self.fetcher = article_fetch.ArticleFetcher(db)
        self.sessions = agent_sessions.AgentSessions(db)
        start_lock = Lock()
        self.details = details.DetailWorker(db, self.fetcher, start_lock)
        self.agent = agent_jobs.AgentWorker(db, self.sessions, start_lock)
        self.chat = news_chat.NewsChat(self.sessions)
        report_lock = RLock()
        settings = configuration.current_runtime_settings
        self.jobs = BackgroundServices(
            classification=news_classification.Service(
                db.connect, tasks.classification_config, topic_queue=topic_tracking.MatchQueue(),
                backup_config=tasks.classification_config,
            ),
            collection=news_collection.Service(db.connect, settings, enqueue_detail=details.enqueue_detail_job),
            reports=periodic_reports.Service(db.connect, settings, index_lock=report_lock),
            summaries=news_summaries.Service(
                lambda: db.path, settings, lambda: str(resolve_db_path().expanduser().resolve()),
                scope_getter=summaries.news_summary_feed_scope,
            ),
            topics=topic_tracking.Service(db.connect, settings),
            report_lock=report_lock,
        )
        self.jobs.classification.configure(**settings()["classification"])

    def initialize(self):
        db = self.db
        db.media_dir.mkdir(parents=True, exist_ok=True)
        with closing(db.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(conn, db.schema_path)
            news_collection.initialize(conn)
            collection_environment.initialize(conn)
            if "title" not in {row[1] for row in conn.execute("PRAGMA table_info(standalone_ideas)")}:
                conn.execute("ALTER TABLE standalone_ideas ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            highlights.migrate_article_highlights_schema(conn)
            market.migrate_market_trend_notes(conn)
            reminders.migrate_news_reminders_review_chain_id(conn)
            market.seed_market_tag_definitions(conn)
            news_classification.enqueue(conn)
            topic_tracking.enqueue_checks(conn)
            self.fetcher._cleanup_media_cache(conn)
            conn.commit()
        with closing(db.connect()) as conn:
            periodic_reports.initialize(conn)
            self.push.initialize(conn)
        self.sessions.ensure_agent_db()

    def start(self):
        self.initialize()
        trading_calendar.SERVICE.start()
        self.details.start_detail_worker()
        self.agent.start_agent_worker()
        self.jobs.start(configuration.current_runtime_settings()["classification"])
        self.push.start()
