"""Title-only Jev classification, with an idempotent SQLite queue.

No network work runs inside an indexing transaction. Leases and compare-and-set
completion protect against restarts, concurrent processes and changed titles.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import ssl
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

RULES = json.loads((Path(__file__).parent / "classification/rules.json").read_text())
RULE_VERSION = RULES["version"]
CATEGORIES = {
    "china": "中国", "ai": "AI", "politics": "政治与国际",
    "economy": "经济与金融", "business": "商业", "science": "科技与科学", "other": "其他",
}
TOPICS = {"AI": "ai", "政治与国际": "politics", "经济与金融": "economy",
          "商业与产业": "business", "科技与科学": "science", "其他": "other"}
MAX_ATTEMPTS = 4
RETRY_DELAYS = (5, 30, 120)
LEASE_SECONDS = 180


def fingerprint(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def parse_category(value: object) -> str:
    if value in (None, "", "all"):
        return "all"
    if not isinstance(value, str) or value not in CATEGORIES:
        raise ValueError("invalid_category")
    return value


def filter_sql(category: str) -> tuple[str, list]:
    category = parse_category(category)
    if category == "all":
        return "", []
    return ("""EXISTS (SELECT 1 FROM item_classifications nc WHERE nc.item_id=items.id
              AND nc.status='done' AND nc.title=items.title AND nc.rule_version=? AND nc.category=?)""",
            [RULE_VERSION, category])


def enqueue(conn: sqlite3.Connection) -> int:
    """Backfill only missing, edited or outdated rows; retain successful results on full reindex."""
    rows = conn.execute("""SELECT i.id, i.title FROM items i
        LEFT JOIN item_classifications c ON c.item_id=i.id
        WHERE c.item_id IS NULL OR c.title<>i.title OR c.rule_version<>?""", (RULE_VERSION,)).fetchall()
    changed = conn.executemany("""INSERT INTO item_classifications(item_id,title,title_hash,rule_version,updated_at)
        VALUES (?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
        title=excluded.title,title_hash=excluded.title_hash,rule_version=excluded.rule_version,
        status='pending',category=NULL,topic=NULL,china_probability=NULL,probabilities_json=NULL,
        confidence=NULL,needs_review=0,model=NULL,usage_json=NULL,attempts=0,next_attempt_at=0,
        lease_token=NULL,lease_until=0,last_error=NULL,updated_at=excluded.updated_at
        WHERE item_classifications.title<>excluded.title OR item_classifications.rule_version<>excluded.rule_version""",
        [(r[0], r[1], fingerprint(r[1]), RULE_VERSION, time.time()) for r in rows]).rowcount
    conn.execute("DELETE FROM item_classifications WHERE item_id NOT IN (SELECT id FROM items)")
    return changed


class ClassificationError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, block_config: bool = False):
        super().__init__(code)
        self.code, self.retryable, self.block_config = code, retryable, block_config


def _probability(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def parse_response(payload: object) -> dict:
    try:
        topic, china = payload["answers"]["topic"], payload["answers"]["china_focus"]
        probabilities, choice, confidence = topic["probabilities"], topic["choice"], topic["confidence"]
        p_china, model = china["noul"], payload["model"]
        valid = (topic["type"] == "choice" and china["type"] == "noul" and choice in TOPICS
                 and set(probabilities) == set(TOPICS) and all(_probability(p) for p in probabilities.values())
                 and abs(sum(probabilities.values()) - 1) < .02 and _probability(p_china)
                 and _probability(confidence) and isinstance(model, str) and bool(model.strip())
                 and probabilities[choice] >= max(probabilities.values()))
        if not valid:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ClassificationError("invalid_response", retryable=True) from None
    category = "ai" if choice == "AI" else "china" if p_china > RULES["china_threshold"] else TOPICS[choice]
    ranked = sorted(probabilities.values(), reverse=True)
    review = RULES["model_review_heuristic"]
    lo, hi = review["china_uncertain_range"]
    return {"category": category, "topic": choice, "china_probability": p_china,
            "probabilities_json": json.dumps(probabilities, ensure_ascii=False), "confidence": confidence,
            "needs_review": int(ranked[0] < review["top_probability_below"]
                                or ranked[0] - ranked[1] < review["top_two_gap_below"] or lo <= p_china <= hi),
            "model": model, "usage_json": json.dumps(payload.get("usage", {}))}


def classify(title: str, key: str, model: str) -> dict:
    body = json.dumps({"model": model, "state": title, "questions": RULES["questions"]}, ensure_ascii=False).encode()
    req = Request("https://api.typesafe.ai/v1/systemone", data=body,
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=45, context=ssl.create_default_context(cafile=certifi.where())) as response:
            return parse_response(json.load(response))
    except HTTPError as exc:
        # Never persist response bodies, request headers or credentials.
        raise ClassificationError(f"http_{exc.code}", retryable=exc.code in (408, 429, 500, 502, 503, 504, 529),
                                  block_config=exc.code in (400, 401, 402, 403, 404, 422)) from None
    except (URLError, TimeoutError, OSError):
        raise ClassificationError("network_error", retryable=True) from None
    except (ValueError, UnicodeError):
        raise ClassificationError("invalid_response", retryable=True) from None


def claim(conn: sqlite3.Connection) -> dict | None:
    now = time.time()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE item_classifications SET status='failed',last_error='interrupted',
            lease_token=NULL,lease_until=0,updated_at=? WHERE status='running' AND lease_until<=? AND attempts>=?""",
                     (now, now, MAX_ATTEMPTS))
        row = conn.execute("""SELECT c.* FROM item_classifications c JOIN items i ON i.id=c.item_id
            WHERE c.title=i.title AND c.rule_version=? AND c.attempts<?
            AND ((c.status='pending' AND c.next_attempt_at<=?) OR (c.status='running' AND c.lease_until<=?))
            ORDER BY i.published_at DESC,i.id DESC LIMIT 1""", (RULE_VERSION, MAX_ATTEMPTS, now, now)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["lease_token"] = uuid.uuid4().hex
        job["attempts"] += 1
        conn.execute("""UPDATE item_classifications SET status='running',attempts=?,lease_token=?,lease_until=?,updated_at=?
            WHERE item_id=?""", (job["attempts"], job["lease_token"], now + LEASE_SECONDS, now, job["item_id"]))
    return job


def finish(conn: sqlite3.Connection, job: dict, result: dict | None, error: ClassificationError | None = None) -> bool:
    now = time.time()
    fields = dict(result or {})
    if error:
        retry = error.retryable and job["attempts"] < MAX_ATTEMPTS
        fields.update(status="pending" if retry or error.block_config else "failed", last_error=error.code,
                      next_attempt_at=now + RETRY_DELAYS[min(job["attempts"] - 1, 2)])
        if error.block_config:
            # Configuration errors pause the service, without exhausting the queue.
            fields["attempts"] = job["attempts"] - 1
    else:
        fields.update(status="done", last_error=None, next_attempt_at=0)
    fields.update(lease_token=None, lease_until=0, updated_at=now)
    with conn:
        changed = conn.execute("UPDATE item_classifications SET " + ",".join(f"{key}=?" for key in fields) +
            """ WHERE item_id=? AND lease_token=? AND title_hash=? AND rule_version=?
            AND EXISTS (SELECT 1 FROM items i WHERE i.id=item_classifications.item_id AND i.title=item_classifications.title)""",
            [*fields.values(), job["item_id"], job["lease_token"], job["title_hash"], job["rule_version"]]).rowcount
    return bool(changed)


def retry_failed(conn: sqlite3.Connection) -> int:
    with conn:
        return conn.execute("""UPDATE item_classifications SET status='pending',attempts=0,
            next_attempt_at=0,last_error=NULL,updated_at=? WHERE status='failed'""", (time.time(),)).rowcount


def completed_revision(conn: sqlite3.Connection) -> float:
    return conn.execute("SELECT COALESCE(MAX(updated_at),0) FROM item_classifications WHERE status='done'").fetchone()[0]


def status(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""SELECT c.status,COUNT(*) AS n FROM item_classifications c
        JOIN items i ON i.id=c.item_id AND i.title=c.title WHERE c.rule_version=? GROUP BY c.status""", (RULE_VERSION,)).fetchall()
    counts = {s: 0 for s in ("pending", "running", "done", "failed")}
    counts.update({r[0]: r[1] for r in rows})
    return {**counts, "total": sum(counts.values()), "rule_version": RULE_VERSION,
            "completed_revision": completed_revision(conn),
            "revision": conn.execute("SELECT COALESCE(MAX(updated_at),0) FROM item_classifications").fetchone()[0]}


def attach(conn: sqlite3.Connection, items: list[dict]) -> None:
    for offset in range(0, len(items), 400):
        batch = items[offset:offset + 400]
        rows = {r["item_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM item_classifications WHERE item_id IN (" + ",".join("?" for _ in batch) + ")",
            [item["id"] for item in batch])}
        for item in batch:
            row = rows.get(item["id"])
            current = bool(row and row["title"] == item.get("title") and row["rule_version"] == RULE_VERSION)
            category = row["category"] if current and row["status"] == "done" else None
            item.update(category=category, category_label=CATEGORIES.get(category, ""),
                        classification_status=row["status"] if current else "pending")


class Service:
    def __init__(self, connect, config, runner=classify):
        self.connect, self.config, self.runner = connect, config, runner
        self.stop = threading.Event()
        self.threads = []
        self.lock = threading.Lock()
        self.blocked_config = None
        self.error = ""

    def process_one(self, key: str, model: str) -> bool:
        with closing(self.connect()) as conn:
            job = claim(conn)
        if not job:
            return False
        try:
            result = self.runner(job["title"], key, model)
            error = None
        except ClassificationError as exc:
            result, error = None, exc
            if exc.block_config:
                self.blocked_config = (fingerprint(key), model)
                self.error = exc.code
        except Exception:
            result, error = None, ClassificationError("classification_failed", retryable=True)
        with closing(self.connect()) as conn:
            finish(conn, job, result, error)
        return True

    def loop(self):
        while not self.stop.is_set():
            try:
                key, model = self.config()
                if not key or self.blocked_config == (fingerprint(key), model):
                    self.stop.wait(5)
                    continue
                self.error = ""
                worked = self.process_one(key, model)
                self.stop.wait(.15 if worked else 5)
            except Exception:
                # DB contention/config errors must not kill the durable queue.
                self.error = "worker_unavailable"
                self.stop.wait(5)

    def start(self):
        with self.lock:
            if self.threads:
                return
            self.threads = [threading.Thread(target=self.loop, daemon=True, name=f"classification-{i}") for i in range(2)]
            for thread in self.threads:
                thread.start()

    def resume(self):
        self.blocked_config = None
        self.error = ""
