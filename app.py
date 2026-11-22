from __future__ import annotations

from contextlib import nullcontext
import os
import sys
from threading import Thread
from uuid import uuid4

from flask import Flask, send_from_directory
from werkzeug.serving import make_server

from network_access import discover_private_serve, private_serve_session, tailnet_ipv4
import personal_assistant
from nr import (
    agent_jobs, agent_sessions, article_fetch, bootstrap, browser_push, database, details, highlights, ideas,
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
    runtime.push.assistant_path = app.extensions["personal_assistant"].state_db
    browser_push.register(app, runtime.push)
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


def serve_on_tailnet(local, port, tailnet_host):
    try:
        peer = make_server(tailnet_host, port, app, threaded=True)
    except (OSError, SystemExit):
        print(f"Tailscale 地址 {tailnet_host}:{port} 无法监听，继续提供本机访问。", file=sys.stderr)
        local.serve_forever()
        return
    with peer:
        thread = Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            local.serve_forever()
        finally:
            peer.shutdown()
            thread.join()


def main() -> None:
    host = (os.getenv("NEWS_READER_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = (os.getenv("NEWS_READER_PORT") or "8080").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 8080
    app.config["NEWS_READER_PORT"] = port
    if not os.getenv("NEWS_READER_INSTANCE_ID"):
        os.environ["NEWS_READER_INSTANCE_ID"] = uuid4().hex
    detect_origin = "NEWS_READER_PUSH_ORIGIN" not in os.environ
    tailnet_host = tailnet_ipv4(os.getenv("NEWS_READER_TAILSCALE_BIND"))
    if (host == "127.0.0.1" and os.getenv("NEWS_READER_MANAGED") == "1"
            and (tailnet_host or os.getenv("NEWS_READER_AUTO_SERVE") == "1")):
        https = private_serve_session(port) if detect_origin else nullcontext("")
        with make_server(host, port, app, threaded=True) as local, https as origin:
            runtime.push.configure(origin, str(port))
            runtime.start()
            if tailnet_host:
                serve_on_tailnet(local, port, tailnet_host)
            else:
                local.serve_forever()
    else:
        accepts_loopback = host in {"127.0.0.1", "localhost", "0.0.0.0"}
        origin = discover_private_serve(port) if accepts_loopback and detect_origin else ""
        runtime.push.configure(origin, str(port) if accepts_loopback else "")
        runtime.start()
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
