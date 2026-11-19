import ast
import graphlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_registered_http_contract():
    from app import create_app

    rules = create_app().url_map.iter_rules()
    actual = sorted(
        f"{method} {rule.rule} {rule.endpoint}"
        for rule in rules if rule.endpoint != "static"
        for method in rule.methods - {"HEAD", "OPTIONS"}
    )
    expected = (ROOT / "tests/fixtures/backend-routes.txt").read_text().splitlines()
    assert actual == expected


def test_features_have_one_way_dependencies():
    graph = {}
    features = sorted((ROOT / "nr").glob("*.py"))
    leaf_modules = [p for p in ROOT.glob("*.py") if p.stem.isidentifier() and p.stem != "app"]
    for path in features + leaf_modules:
        deps = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level and path in features:
                    prefix = ("nr." + prefix).rstrip(".")
                names = [prefix + "." + alias.name for alias in node.names]
            else:
                continue
            for name in names:
                assert name.split(".", 1)[0] != "app", f"{path.name} imports {name}"
                if name.startswith("nr."):
                    deps.add(name.split(".")[1])
        if path in features:
            graph[path.stem] = deps
    graphlib.TopologicalSorter(graph).prepare()


def test_import_and_factory_leave_workers_and_databases_idle(tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for name in ("DB_PATH", "AGENT_DB_PATH", "AGENT_RUNTIME_DIR", "MEDIA_CACHE_DIR",
                 "APP_SETTINGS_PATH", "UPDATER_DIR", "PERSONAL_ASSISTANT_DIR"):
        env["NEWS_READER_" + name] = str(tmp_path / name.lower())
    script = """
import importlib
import pkgutil
import sqlite3
import subprocess
import threading

def unexpected(*args, **kwargs):
    raise AssertionError("database, subprocess or thread started during assembly")

sqlite3.connect = subprocess.Popen = threading.Thread.start = unexpected
import nr
for module in pkgutil.iter_modules(nr.__path__, nr.__name__ + "."):
    importlib.import_module(module.name)
from app import app, create_app
first = app.extensions["newsreader"]
second = create_app().extensions["newsreader"]
assert first.db is not second.db
assert first.details is not second.details and first.sessions is not second.sessions
assert first.agent is not second.agent and first.updates.tokens is not second.updates.tokens
assert first.jobs.collection is not second.jobs.collection
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, check=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("relative", "bound"), [
    ("nr/article_fetch.py", True), ("feed_sources.py", True), ("scripts/opencli.mjs", True),
    ("nr/reading.py", False), ("nr/highlights.py", False), ("app.py", False),
])
def test_opencli_fingerprint_tracks_extraction_dependencies(monkeypatch, relative, bound):
    from scripts.verify_collection_runtime import content_fingerprint

    baseline = content_fingerprint()
    read_bytes = Path.read_bytes
    target = ROOT / relative

    def changed_content(path):
        data = read_bytes(path)
        return data + b"\n" if path == target else data

    monkeypatch.setattr(Path, "read_bytes", changed_content)
    assert (content_fingerprint() != baseline) == bound
