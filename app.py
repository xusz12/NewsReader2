from __future__ import annotations

import os

from flask import Flask, send_from_directory

import personal_assistant
from nr import (
    agent_jobs, agent_sessions, article_fetch, bootstrap, database, details, highlights, ideas,
    market, news_chat, preferences, read_state, reading, reminders, reports, reviews,
    summaries, tasks, topics, tracked_topics, updates,
)
from settings import load_app_settings
from static_delivery import compress_static_response


def create_app(db=None):
    db = database.Database() if db is None else db
    runtime = bootstrap.Workbench(db)
    app = Flask(__name__, static_folder=str(db.static_dir), static_url_path="/static")
    app.extensions["newsreader"] = runtime
    app.after_request(compress_static_response)

    @app.get("/")
    def index():
        return send_from_directory(db.static_dir, "index.html")

    personal_assistant.register(app, lambda: db.path, load_app_settings)
    reading.register(app, db)
    read_state.register(app, db, details.enqueue_detail_job)
    highlights.register(app, db)
    ideas.register(app, db)
    market.register(app, db)
    tracked_topics.register(app, db)
    reminders.register(app, db)
    reviews.register(app, db)
    details.register(app, db, runtime.sessions)
    article_fetch.register(app, db, runtime.fetcher)
    news_chat.register(app, runtime.chat, db, runtime.sessions)
    agent_sessions.register(app, runtime.sessions, runtime.agent)
    agent_jobs.register(app, runtime.sessions, runtime.agent)
    preferences.register(app, db, runtime.jobs, runtime.preferences, runtime.sessions, runtime.agent)
    reports.register(app, db, runtime.jobs, runtime.sessions, runtime.agent)
    summaries.register(app, runtime.jobs)
    topics.register(app, db, runtime.jobs)
    tasks.register(app, db, runtime.jobs)
    updates.register(app, db, runtime.updates)
    return app


app = create_app()
runtime = app.extensions["newsreader"]


def ensure_db():
    runtime.initialize()


def main() -> None:
    runtime.start()
    host = (os.getenv("NEWS_READER_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = (os.getenv("NEWS_READER_PORT") or "8080").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 8080
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
