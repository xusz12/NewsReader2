from __future__ import annotations

from nr import reviews

from legacy_news_seed import seed_news, confirm_all_read
import importlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from api_fixtures import _create_standalone_idea, _isolate_app_settings


def _setup_review_env(tmp_path: Path, monkeypatch):
    """Set up a clean env with DB and reindexed news for review tests."""
    db_path = tmp_path / "news_index.sqlite3"
    daily_dir = tmp_path / "DailyNews"
    daily_dir.mkdir(parents=True)
    (daily_dir / "dailyFreshNews_2026-07-01.md").write_text(
        """## Reuters · World（1条）
### [复盘测试新闻](https://example.com/review-test)
- 发布时间：2026-07-01 12:00:00
- 摘要：新能源政策即将出台
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_READER_DAILY_NEWS_DIR", str(daily_dir))
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    app_module.ensure_db()
    client = app_module.app.test_client()
    seed_news(client, json={})
    return client, app_module


def _create_article_note(client, url: str, note: str = "新能源板块即将大涨") -> None:
    resp = client.put(f"/api/news/url/{url}/note", json={"note": note})
    if resp.status_code != 200:
        # fallback: use item-based note endpoint
        # find item_id for this url
        news = client.get("/api/news?per=100").get_json()
        for item in news["items"]:
            if item["url"] == url:
                resp = client.put(f"/api/news/{item['id']}/note", json={"note": note})
                assert resp.status_code == 200
                return
        raise RuntimeError(f"cannot find item with url {url}")


def test_review_schema_migration_idempotent(tmp_path: Path, monkeypatch):
    """Schema migration should be idempotent — ensure_db twice without error."""
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    app_module.ensure_db()
    app_module.ensure_db()  # second call should not error

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Verify review tables exist
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "review_chains" in tables
    assert "review_versions" in tables
    assert "review_events" in tables
    assert "review_evidence" in tables
    # Verify news_reminders has review_chain_id
    cols = {r[1] for r in conn.execute("PRAGMA table_info(news_reminders)").fetchall()}
    assert "review_chain_id" in cols
    conn.close()


def test_review_create_from_standalone_idea(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)

    resp = client.post("/api/reviews", json={
        "source_type": "standalone_idea",
        "source_key": str(idea_id),
        "judgment": "政策实施后三个月内新能源融资成本下降",
        "criteria": "新能源企业平均融资成本数据下降5%以上",
        "plan_review_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    review = data["review"]
    assert review["status"] == "active"
    assert review["effective_status"] == "in_progress"
    assert review["current_version"] == 1
    assert review["source_note"] == "这项政策长期可能利好新能源"
    assert len(review["versions"]) == 1
    assert review["versions"][0]["judgment"] == "政策实施后三个月内新能源融资成本下降"
    assert len(review["events"]) == 1


def test_review_create_from_article_note(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    url = "https://example.com/review-test"
    # Create article note
    news = client.get("/api/news?per=100").get_json()
    item_id = None
    for item in news["items"]:
        if item["url"] == url:
            item_id = item["id"]
            break
    assert item_id is not None
    resp = client.put(f"/api/news/{item_id}/note", json={"note": "新能源板块要涨"})
    assert resp.status_code == 200

    resp = client.post("/api/reviews", json={
        "source_type": "article_note",
        "source_key": url,
        "judgment": "新能源板块一周内上涨",
        "criteria": "板块指数涨幅超过3%",
        "plan_review_date": "2026-07-08",
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert review["source_note"] == "新能源板块要涨"
    assert review["source_snapshot"]["news_list"][0]["title"] == "复盘测试新闻"


def test_review_create_source_not_found(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    resp = client.post("/api/reviews", json={
        "source_type": "standalone_idea",
        "source_key": "99999",
        "judgment": "test",
        "criteria": "test",
        "plan_review_date": "2026-10-01",
    })
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "source_not_found"


def test_review_create_missing_fields(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    base = {"source_type": "standalone_idea", "source_key": str(idea_id)}
    # missing judgment
    assert client.post("/api/reviews", json={**base, "criteria": "c", "plan_review_date": "2026-10-01"}).status_code == 400
    # missing criteria is now OK (optional)
    resp = client.post("/api/reviews", json={**base, "judgment": "j", "plan_review_date": "2026-10-01"})
    assert resp.status_code == 200
    assert resp.get_json()["review"]["source_note"]
    # missing date
    assert client.post("/api/reviews", json={**base, "judgment": "j", "criteria": "c"}).status_code == 400
    # invalid date
    assert client.post("/api/reviews", json={**base, "judgment": "j", "criteria": "c", "plan_review_date": "bad"}).status_code == 400


def test_review_revise_without_criteria(tmp_path: Path, monkeypatch):
    """Criteria is optional in revise."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "V1判断", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/revise", json={
        "judgment": "V2判断", "revision_reason": "新证据", "event_date": "2026-07-06",
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert review["current_version"] == 2
    assert review["versions"][1]["criteria"] == ""


def test_review_retrack_without_criteria(tmp_path: Path, monkeypatch):
    """Criteria is optional in retrack."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "V1判断", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Complete the chain first
    client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "confirmed", "actual_text": "结果", "experience": "经验",
    })
    # Retrack without criteria
    resp = client.post(f"/api/reviews/{chain_id}/retrack", json={
        "judgment": "新判断", "plan_review_date": "2099-12-01",
    })
    assert resp.status_code == 200
    assert resp.get_json()["review"]["source_note"]


def test_review_list_date_key_label(tmp_path: Path, monkeypatch):
    """Reviews list should return date_key/date_label derived from plan_review_date."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "未来判断", "plan_review_date": "2099-03-15",
    })
    r = client.get("/api/reviews?per=100")
    assert r.status_code == 200
    item = r.get_json()["items"][0]
    assert item["date_key"] == "2099-03-15"
    assert item["date_label"] == "2099年3月15日"


def test_review_list_groups_same_plan_date_together(tmp_path: Path, monkeypatch):
    """Reviews with the same plan_review_date must be contiguous regardless of updated_at order."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    # A1 and A2 share plan date; B has a different plan date
    idea_a1 = _create_standalone_idea(client, "A1 想法")
    idea_b = _create_standalone_idea(client, "B 想法")
    idea_a2 = _create_standalone_idea(client, "A2 想法")

    r_a1 = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_a1),
        "judgment": "A1", "plan_review_date": "2026-08-12",
    })
    r_b = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_b),
        "judgment": "B", "plan_review_date": "2026-09-01",
    })
    r_a2 = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_a2),
        "judgment": "A2", "plan_review_date": "2026-08-12",
    })
    id_a1 = r_a1.get_json()["review"]["id"]
    id_b = r_b.get_json()["review"]["id"]
    id_a2 = r_a2.get_json()["review"]["id"]

    # Force interleaved updated_at: A2 newest, B middle, A1 oldest
    db_path = tmp_path / "news_index.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE review_chains SET updated_at = ? WHERE id = ?",
        ("2026-07-13T10:00:00", id_a1),
    )
    conn.execute(
        "UPDATE review_chains SET updated_at = ? WHERE id = ?",
        ("2026-07-13T11:00:00", id_b),
    )
    conn.execute(
        "UPDATE review_chains SET updated_at = ? WHERE id = ?",
        ("2026-07-13T12:00:00", id_a2),
    )
    conn.commit()
    conn.close()

    r = client.get("/api/reviews?per=100")
    assert r.status_code == 200
    items = r.get_json()["items"]
    date_keys = [item["date_key"] for item in items]
    # Same plan date should be contiguous, and latest-updated within group comes first
    assert date_keys == ["2026-08-12", "2026-08-12", "2026-09-01"], date_keys
    assert items[0]["id"] == id_a2
    assert items[1]["id"] == id_a1
    assert items[2]["id"] == id_b


def test_review_initial_event_date_is_today_not_plan_date(tmp_path: Path, monkeypatch):
    """Initial revision event must record the actual creation date, not plan_review_date."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "V1", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    assert len(detail["events"]) == 1
    ev = detail["events"][0]
    assert ev["event_type"] == "revision"
    assert ev["version_id"] is None
    today = reviews._today_str()
    assert ev["event_date"] == today, f"event_date {ev['event_date']} != today {today}"
    assert ev["event_date"] != "2099-01-01"


def test_review_old_record_event_date_fallback_to_created_at(tmp_path: Path, monkeypatch):
    """Old records whose initial revision stored plan_review_date should display created_at."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "V1", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Simulate legacy record: force initial event_date to plan_review_date
    conn = sqlite3.connect(str(tmp_path / "news_index.sqlite3"))
    conn.execute(
        "UPDATE review_events SET event_date = '2099-01-01' WHERE chain_id = ? AND event_type = 'revision' AND version_id IS NULL",
        (chain_id,),
    )
    conn.commit()
    conn.close()
    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    ev = detail["events"][0]
    assert ev["event_date"] == detail["created_at"][:10]
    assert ev["event_date"] != "2099-01-01"


def test_review_criteria_empty_in_versions(tmp_path: Path, monkeypatch):
    """When criteria is omitted, version criteria should be empty string, not missing."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "无标准判断", "plan_review_date": "2099-01-01",
    })
    review = r.get_json()["review"]
    assert review["versions"][0]["criteria"] == ""


def test_review_list_and_filter(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)

    # Create in_progress review (future date)
    client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "未来判断", "criteria": "标准", "plan_review_date": "2099-01-01",
    })
    # Create pending_review review (past date)
    idea_id2 = _create_standalone_idea(client, "第二条想法")
    client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id2),
        "judgment": "到期判断", "criteria": "标准2", "plan_review_date": "2020-01-01",
    })

    # All
    r = client.get("/api/reviews?per=100")
    assert r.status_code == 200
    assert r.get_json()["total"] == 2

    # In progress
    r = client.get("/api/reviews?status=in_progress")
    assert r.get_json()["total"] == 1
    assert r.get_json()["items"][0]["current_judgment"] == "未来判断"

    # Pending review
    r = client.get("/api/reviews?status=pending_review")
    assert r.get_json()["total"] == 1
    assert r.get_json()["items"][0]["effective_status"] == "pending_review"

    # Done (none yet)
    r = client.get("/api/reviews?status=done")
    assert r.get_json()["total"] == 0


def test_review_done_list_result_filter_is_paginated_server_side(tmp_path: Path, monkeypatch):
    """Result filters must constrain the API query instead of only loaded rows."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    confirmed_ids = []
    for index in range(11):
        idea_id = _create_standalone_idea(client, f"成立想法 {index}")
        created = client.post("/api/reviews", json={
            "source_type": "standalone_idea", "source_key": str(idea_id),
            "judgment": f"成立判断 {index}", "plan_review_date": "2020-01-01",
        })
        chain_id = created.get_json()["review"]["id"]
        completed = client.post(f"/api/reviews/{chain_id}/complete", json={
            "result": "confirmed", "actual_text": "事实结果", "experience": "复盘经验",
        })
        assert completed.status_code == 200
        confirmed_ids.append(chain_id)

    refuted_idea_id = _create_standalone_idea(client, "未成立想法")
    created = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(refuted_idea_id),
        "judgment": "未成立判断", "plan_review_date": "2020-01-01",
    })
    refuted_id = created.get_json()["review"]["id"]
    client.post(f"/api/reviews/{refuted_id}/complete", json={
        "result": "refuted", "actual_text": "事实结果", "experience": "复盘经验",
    })

    first_page = client.get("/api/reviews?status=done&result=confirmed&per=10")
    assert first_page.status_code == 200
    assert first_page.get_json()["total"] == 11
    assert first_page.get_json()["has_more"] is True
    assert {item["result"] for item in first_page.get_json()["items"]} == {"confirmed"}

    second_page = client.get("/api/reviews?status=done&result=confirmed&per=10&page=2")
    assert second_page.status_code == 200
    assert second_page.get_json()["total"] == 11
    assert second_page.get_json()["has_more"] is False
    assert len(second_page.get_json()["items"]) == 1
    assert second_page.get_json()["items"][0]["id"] in confirmed_ids

    refuted = client.get("/api/reviews?status=done&result=refuted")
    assert refuted.status_code == 200
    assert refuted.get_json()["total"] == 1
    assert refuted.get_json()["items"][0]["id"] == refuted_id

    invalid = client.get("/api/reviews?status=done&result=partial")
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_result_filter"


def test_review_progress(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]

    resp = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "新政策已发布",
        "event_date": "2026-07-05",
        "evidence": [{"news_title": "新政策发布", "news_url": "https://example.com/policy", "news_summary": "政策细则"}],
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert len(review["events"]) == 2  # initial revision + progress
    assert review["events"][-1]["event_type"] == "progress"
    assert len(review["evidence"]) == 1
    assert review["evidence"][0]["news_title"] == "新政策发布"


def test_review_progress_overdue_renews_plan_and_only_matching_reminder(tmp_path: Path, monkeypatch):
    """An overdue progress save renews the plan and only moves its bound due reminder."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    monkeypatch.setattr(reviews, '_today_str', lambda: "2026-09-04")
    idea_id = _create_standalone_idea(client)
    created = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "到期判断", "criteria": "标准", "plan_review_date": "2020-01-01",
    })
    chain_id = created.get_json()["review"]["id"]

    matching = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2020-01-01", "remind_at": "2020-01-01T14:30",
    }).get_json()["reminder_id"]
    other_date = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2020-01-02", "remind_at": "2020-01-02T16:45",
    }).get_json()["reminder_id"]
    completed = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2020-01-01", "remind_at": "2020-01-01T17:00",
    }).get_json()["reminder_id"]
    dismissed = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2020-01-01", "remind_at": "2020-01-01T18:15",
    }).get_json()["reminder_id"]

    db_path = tmp_path / "news_index.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE news_reminders SET status='done' WHERE id=?", (completed,))
        conn.execute("UPDATE news_reminders SET status='dismissed' WHERE id=?", (dismissed,))
        cursor = conn.execute(
            """INSERT INTO news_reminders
               (item_id, item_title_snapshot, item_url_snapshot, event_title, event_date,
                remind_at, note, status, review_chain_id, created_at, updated_at)
               VALUES (NULL, ?, '', ?, ?, ?, '', 'active', NULL, ?, ?)""",
            ("无绑定提醒", "无绑定提醒", "2020-01-01", "2020-01-01 19:20:00", "2026-09-04 10:00:00", "2026-09-04 10:00:00"),
        )
        unrelated = cursor.lastrowid

    response = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "新增到期证据",
        "event_date": "2026-08-20",
    })
    assert response.status_code == 200
    data = response.get_json()
    expected_new_date = "2026-09-19"
    assert data["auto_postponed"] is True
    assert data["old_plan_review_date"] == "2020-01-01"
    assert data["new_plan_review_date"] == expected_new_date
    assert data["moved_reminder_count"] == 1
    assert data["moved_reminder_ids"] == [matching]

    review = data["review"]
    assert review["plan_review_date"] == expected_new_date
    event_types = [event["event_type"] for event in review["events"]]
    assert event_types.count("progress") == 1
    assert "postponed" not in event_types

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["id"]: dict(row)
            for row in conn.execute(
                "SELECT id, event_date, remind_at, status FROM news_reminders WHERE review_chain_id=? ORDER BY id",
                (chain_id,),
            )
        }
        unrelated_row = conn.execute(
            "SELECT event_date, remind_at, status, review_chain_id FROM news_reminders WHERE id=?",
            (unrelated,),
        ).fetchone()
    assert rows[matching]["event_date"] == expected_new_date
    assert rows[matching]["remind_at"] == f"{expected_new_date} 14:30:00"
    assert rows[matching]["status"] == "active"
    assert rows[other_date] == {
        "id": other_date, "event_date": "2020-01-02", "remind_at": "2020-01-02 16:45:00", "status": "active",
    }
    assert rows[completed] == {
        "id": completed, "event_date": "2020-01-01", "remind_at": "2020-01-01 17:00:00", "status": "done",
    }
    assert rows[dismissed] == {
        "id": dismissed, "event_date": "2020-01-01", "remind_at": "2020-01-01 18:15:00", "status": "dismissed",
    }
    assert tuple(unrelated_row) == ("2020-01-01", "2020-01-01 19:20:00", "active", None)


def test_review_progress_no_reminder_and_future_plan_do_not_move_reminders(tmp_path: Path, monkeypatch):
    """No reminder is created, and a future review records progress without renewal."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    monkeypatch.setattr(reviews, '_today_str', lambda: "2026-09-04")

    overdue_idea = _create_standalone_idea(client, "无提醒到期想法")
    overdue = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(overdue_idea),
        "judgment": "无提醒到期判断", "plan_review_date": "2020-01-01",
    }).get_json()["review"]["id"]
    overdue_response = client.post(f"/api/reviews/{overdue}/progress", json={
        "event_text": "记录到期进展", "event_date": "2026-09-04",
    })
    assert overdue_response.status_code == 200
    overdue_data = overdue_response.get_json()
    assert overdue_data["auto_postponed"] is True
    assert overdue_data["moved_reminder_count"] == 0
    assert overdue_data["review"]["plan_review_date"] == "2026-09-19"
    assert not client.get("/api/reminders?filter=all").get_json()["items"]

    future_idea = _create_standalone_idea(client, "未到期想法")
    future = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(future_idea),
        "judgment": "未到期判断", "plan_review_date": "2099-01-01",
    }).get_json()["review"]["id"]
    future_reminder = client.post(f"/api/reviews/{future}/reminders", json={
        "event_date": "2099-01-01", "remind_at": "2099-01-01T08:15",
    }).get_json()["reminder_id"]
    future_response = client.post(f"/api/reviews/{future}/progress", json={
        "event_text": "提前记录进展", "event_date": "2026-09-04",
    })
    assert future_response.status_code == 200
    future_data = future_response.get_json()
    assert future_data["auto_postponed"] is False
    assert future_data["old_plan_review_date"] == "2099-01-01"
    assert future_data["new_plan_review_date"] == "2099-01-01"
    assert future_data["moved_reminder_count"] == 0
    assert not any(event["event_type"] == "postponed" for event in future_data["review"]["events"])

    with sqlite3.connect(str(tmp_path / "news_index.sqlite3")) as conn:
        reminder = conn.execute(
            "SELECT event_date, remind_at, status FROM news_reminders WHERE id=?", (future_reminder,)
        ).fetchone()
    assert reminder == ("2099-01-01", "2099-01-01 08:15:00", "active")


def test_review_progress_repeated_same_day_does_not_renew_again(tmp_path: Path, monkeypatch):
    """Once the first save makes the plan future, another same-day save is not a second renewal."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    monkeypatch.setattr(reviews, '_today_str', lambda: "2026-09-04")
    idea_id = _create_standalone_idea(client)
    chain_id = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "重复记录判断", "plan_review_date": "2020-01-01",
    }).get_json()["review"]["id"]
    reminder_id = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2020-01-01", "remind_at": "2020-01-01T09:05",
    }).get_json()["reminder_id"]

    first = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "第一次进展", "event_date": "2026-09-04",
    })
    assert first.status_code == 200
    first_data = first.get_json()
    assert first_data["auto_postponed"] is True
    assert first_data["new_plan_review_date"] == "2026-09-19"

    second = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "同日补充进展", "event_date": "2026-09-04",
    })
    assert second.status_code == 200
    second_data = second.get_json()
    assert second_data["auto_postponed"] is False
    assert second_data["old_plan_review_date"] == "2026-09-19"
    assert second_data["new_plan_review_date"] == "2026-09-19"
    assert second_data["moved_reminder_count"] == 0
    assert sum(event["event_type"] == "progress" for event in second_data["review"]["events"]) == 2
    assert not any(event["event_type"] == "postponed" for event in second_data["review"]["events"])

    with sqlite3.connect(str(tmp_path / "news_index.sqlite3")) as conn:
        reminder = conn.execute(
            "SELECT event_date, remind_at FROM news_reminders WHERE id=?", (reminder_id,)
        ).fetchone()
    assert reminder == ("2026-09-19", "2026-09-19 09:05:00")


def test_review_progress_rolls_back_all_writes_when_reminder_move_fails(tmp_path: Path, monkeypatch):
    """A failed matching-reminder move rolls back progress, evidence, event, and plan changes."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    monkeypatch.setattr(reviews, '_today_str', lambda: "2026-09-04")
    idea_id = _create_standalone_idea(client)
    chain_id = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "回滚判断", "plan_review_date": "2020-01-01",
    }).get_json()["review"]["id"]

    db_path = tmp_path / "news_index.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            """INSERT INTO news_reminders
               (item_id, item_title_snapshot, item_url_snapshot, event_title, event_date,
                remind_at, note, status, review_chain_id, created_at, updated_at)
               VALUES (NULL, ?, '', ?, ?, ?, '', 'active', ?, ?, ?)""",
            ("回滚提醒", "回滚提醒", "2020-01-01", "not-a-time", chain_id, "2026-09-04 10:00:00", "2026-09-04 10:00:00"),
        )
        reminder_id = cursor.lastrowid

    response = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "不应持久化的进展",
        "event_date": "2026-09-04",
        "evidence": [{"news_title": "不应持久化的证据", "news_url": "https://example.com/rollback"}],
    })
    assert response.status_code == 500
    assert response.get_json() == {"error": "review_progress_failed", "ok": False}

    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    assert detail["plan_review_date"] == "2020-01-01"
    assert len(detail["events"]) == 1
    assert not detail["evidence"]
    with sqlite3.connect(str(db_path)) as conn:
        reminder = conn.execute(
            "SELECT event_date, remind_at, status FROM news_reminders WHERE id=?", (reminder_id,)
        ).fetchone()
    assert reminder == ("2020-01-01", "not-a-time", "active")


def test_review_revise(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "V1判断", "criteria": "V1标准", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]

    resp = client.post(f"/api/reviews/{chain_id}/revise", json={
        "judgment": "V2判断", "criteria": "V2标准",
        "revision_reason": "新证据出现", "event_date": "2026-07-06",
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert review["current_version"] == 2
    assert len(review["versions"]) == 2
    assert review["versions"][0]["judgment"] == "V1判断"
    assert review["versions"][1]["judgment"] == "V2判断"
    assert review["versions"][1]["revision_reason"] == "新证据出现"


def test_review_complete(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]

    resp = client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "confirmed",
        "actual_text": "新能源融资成本确实下降",
        "bias_text": "低估了政策力度",
        "experience": "关注政策实施力度而非仅看方向",
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert review["status"] == "done"
    assert review["effective_status"] == "done"
    assert review["result"] == "confirmed"
    assert review["experience"] == "关注政策实施力度而非仅看方向"
    assert review["completed_at"] != ""


def test_review_complete_missing_experience(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "confirmed", "actual_text": "a", "bias_text": "b",
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_experience"


def test_review_complete_invalid_result(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "wrong", "actual_text": "a", "bias_text": "b", "experience": "e",
    })
    assert resp.status_code == 400


def test_review_done_blocks_actions(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "confirmed", "actual_text": "a", "bias_text": "b", "experience": "e",
    })
    # Cannot revise
    assert client.post(f"/api/reviews/{chain_id}/revise", json={
        "judgment": "j2", "criteria": "c2", "revision_reason": "r", "event_date": "2026-07-06",
    }).status_code == 409
    # Cannot progress
    assert client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "t", "event_date": "2026-07-06",
    }).status_code == 409
    # Cannot continue observing
    assert client.post(f"/api/reviews/{chain_id}/continue-observing", json={
        "new_review_date": "2026-12-01",
    }).status_code == 409


def test_review_continue_observing(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/continue-observing", json={
        "event_text": "暂不可判断，继续观察",
        "new_review_date": "2026-12-01",
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert review["status"] == "active"  # still active, not done
    assert review["plan_review_date"] == "2026-12-01"
    assert review["effective_status"] == "in_progress"  # future date
    # Check event recorded
    assert any(e["event_type"] == "continue_observing" for e in review["events"])


def test_review_retrack(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "refuted", "actual_text": "a", "bias_text": "b", "experience": "e",
    })
    # Retrack
    resp = client.post(f"/api/reviews/{chain_id}/retrack", json={
        "judgment": "新判断", "criteria": "新标准", "plan_review_date": "2027-01-01",
    })
    assert resp.status_code == 200
    new_review = resp.get_json()["review"]
    assert new_review["parent_chain_id"] == chain_id
    assert new_review["status"] == "active"
    assert new_review["current_version"] == 1
    assert new_review["source_note"] == "这项政策长期可能利好新能源"  # snapshot preserved


def test_review_retrack_event_date_is_today(tmp_path: Path, monkeypatch):
    """Retracked chain's first 'retracked' event must use actual creation date, not plan_review_date."""
    client, app_module = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2020-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    client.post(f"/api/reviews/{chain_id}/complete", json={
        "result": "refuted", "actual_text": "a", "experience": "e",
    })
    future = "2027-12-01"
    resp = client.post(f"/api/reviews/{chain_id}/retrack", json={
        "judgment": "新判断", "criteria": "新标准", "plan_review_date": future,
    })
    assert resp.status_code == 200
    new_review = resp.get_json()["review"]
    assert new_review["plan_review_date"] == future
    retracked_events = [e for e in new_review["events"] if e["event_type"] == "retracked"]
    assert len(retracked_events) == 1
    today = reviews._today_str()
    assert retracked_events[0]["event_date"] == today
    assert retracked_events[0]["event_date"] != future


def test_review_retrack_not_done(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/retrack", json={
        "judgment": "j2", "criteria": "c2", "plan_review_date": "2027-01-01",
    })
    assert resp.status_code == 409


def test_review_evidence_add_and_delete(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Add
    resp = client.post(f"/api/reviews/{chain_id}/evidence", json={
        "news_title": "证据新闻", "news_url": "https://example.com/ev1", "news_summary": "摘要",
    })
    assert resp.status_code == 200
    ev_id = resp.get_json()["evidence_id"]
    # Delete
    resp = client.delete(f"/api/reviews/{chain_id}/evidence/{ev_id}")
    assert resp.status_code == 200
    # Verify gone
    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    assert len(detail["evidence"]) == 0


def test_review_evidence_wrong_chain(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r1 = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j1", "criteria": "c1", "plan_review_date": "2099-01-01",
    })
    chain1 = r1.get_json()["review"]["id"]
    idea_id2 = _create_standalone_idea(client, "想法2")
    r2 = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id2),
        "judgment": "j2", "criteria": "c2", "plan_review_date": "2099-01-01",
    })
    chain2 = r2.get_json()["review"]["id"]
    client.post(f"/api/reviews/{chain2}/evidence", json={
        "news_title": "证据", "news_url": "https://example.com/ev",
    })
    # Try to delete chain2's evidence from chain1
    ev_id = client.get(f"/api/reviews/{chain2}").get_json()["review"]["evidence"][0]["id"]
    resp = client.delete(f"/api/reviews/{chain1}/evidence/{ev_id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "evidence_not_belong"


def test_review_source_snapshot_survives_idea_deletion(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client, "原始想法文本")
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Delete the original idea
    client.delete(f"/api/standalone-ideas/{idea_id}")
    # Review should still be readable with snapshot
    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    assert detail["source_note"] == "原始想法文本"
    assert detail["source_snapshot"]["source_note"] == "原始想法文本"


def test_review_reminder_decoupled(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    # Create review with reminder
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
    })
    chain_id = r.get_json()["review"]["id"]
    # Find the reminder
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    reminder_id = review_reminders[0]["id"]
    # Delete the reminder
    client.delete(f"/api/reminders/{reminder_id}")
    # Review should be unaffected
    detail = client.get(f"/api/reviews/{chain_id}").get_json()["review"]
    assert detail["status"] == "active"
    assert detail["plan_review_date"] == "2099-01-01"


def test_review_reminder_create_endpoint(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Create reminder via dedicated endpoint
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2026-08-01",
        "note": "记得回来复盘",
    })
    assert resp.status_code == 200
    assert resp.get_json()["reminder_id"] is not None


def test_review_full_text_search(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client, "新能源政策观察")
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "新能源融资成本将下降", "criteria": "融资成本数据",
        "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Add evidence
    client.post(f"/api/reviews/{chain_id}/evidence", json={
        "news_title": "新能源融资报告", "news_url": "https://example.com/report",
    })
    # Search by judgment text
    r = client.get("/api/reviews?q=融资成本")
    assert r.status_code == 200
    assert r.get_json()["total"] == 1
    # Search by evidence title
    r = client.get("/api/reviews?q=融资报告")
    assert r.get_json()["total"] == 1
    # Search by source note
    r = client.get("/api/reviews?q=新能源政策")
    assert r.get_json()["total"] == 1
    # No match
    r = client.get("/api/reviews?q=完全不相关")
    assert r.get_json()["total"] == 0


def test_review_detail_not_found(tmp_path: Path, monkeypatch):
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    assert client.get("/api/reviews/99999").status_code == 404


def test_review_progress_with_evidence_complete(tmp_path: Path, monkeypatch):
    """Progress with evidence should create both event and evidence atomically."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/progress", json={
        "event_text": "新证据出现",
        "event_date": "2026-07-05",
        "evidence": [
            {"news_title": "证据1", "news_url": "https://example.com/e1"},
            {"news_title": "证据2", "news_url": "https://example.com/e2"},
        ],
    })
    assert resp.status_code == 200
    review = resp.get_json()["review"]
    assert len(review["evidence"]) == 2
    # Evidence should be linked to the progress event
    event_id = review["events"][-1]["id"]
    assert all(ev["event_id"] == event_id for ev in review["evidence"])


def test_review_news_reminders_migration_compatible(tmp_path: Path, monkeypatch):
    """Old reminders without review_chain_id should still work after migration."""
    db_path = tmp_path / "news_index.sqlite3"
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(db_path))
    import app as app_module

    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    app_module.ensure_db()
    # Insert a legacy reminder directly
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO news_reminders
           (item_id, item_title_snapshot, item_url_snapshot, event_title, event_date,
            remind_at, note, status, created_at, updated_at)
           VALUES (NULL, 'old', 'http://old', 'old event', '2026-07-01',
                   '2026-07-01', '', 'active', '2026-07-01 00:00:00', '2026-07-01 00:00:00')"""
    )
    conn.commit()
    conn.close()
    # Reload and ensure_db again
    importlib.reload(app_module)
    monkeypatch.setattr(app_module, "has_secret", lambda name: False)
    app_module.ensure_db()
    client = app_module.app.test_client()
    r = client.get("/api/reminders?filter=all")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert any(i["event_title"] == "old event" for i in items)


def test_review_create_remind_at_default(tmp_path: Path, monkeypatch):
    """add_reminder with no remind_at should default to plan_review_date 09:00."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
    })
    chain_id = r.get_json()["review"]["id"]
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert "09:00" in review_reminders[0]["remind_at"]


def test_review_create_remind_at_custom(tmp_path: Path, monkeypatch):
    """add_reminder with explicit remind_at should use it."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
        "remind_at": "2099-01-01T14:30",
    })
    chain_id = r.get_json()["review"]["id"]
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert "14:30" in review_reminders[0]["remind_at"]


def test_review_create_remind_at_invalid(tmp_path: Path, monkeypatch):
    """add_reminder with invalid remind_at should fail."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
        "remind_at": "not-a-time",
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_remind_at"


def test_review_create_no_reminder_when_unchecked(tmp_path: Path, monkeypatch):
    """add_reminder=False should not create any reminder."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": False,
    })
    chain_id = r.get_json()["review"]["id"]
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    assert not any(rm.get("review_chain_id") == chain_id for rm in reminders)


def test_review_reminder_endpoint_custom_remind_at(tmp_path: Path, monkeypatch):
    """Dedicated reminder endpoint should accept remind_at."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2026-08-01",
        "remind_at": "2026-08-01T10:00",
    })
    assert resp.status_code == 200
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert "10:00" in review_reminders[0]["remind_at"]


def test_review_reminder_endpoint_invalid_remind_at(tmp_path: Path, monkeypatch):
    """Dedicated reminder endpoint should reject invalid remind_at."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2026-08-01",
        "remind_at": "garbage",
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_remind_at"


def test_review_create_remind_at_trailing_garbage(tmp_path: Path, monkeypatch):
    """Trailing characters after YYYY-MM-DDTHH:MM must be rejected."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
        "remind_at": "2099-01-01T14:30abc",
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_remind_at"


def test_review_reminder_endpoint_trailing_garbage(tmp_path: Path, monkeypatch):
    """Dedicated reminder endpoint must reject trailing garbage."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2026-08-01",
        "remind_at": "2026-08-01T10:00abc",
    })
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_remind_at"


def test_review_create_remind_at_semantic_invalid(tmp_path: Path, monkeypatch):
    """Non-existent date/time (e.g. 24:00) must be rejected."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    for bad in ("2099-02-30T09:00", "2099-01-01T24:00", "2099-01-01T09:00+08:00"):
        r = client.post("/api/reviews", json={
            "source_type": "standalone_idea", "source_key": str(idea_id),
            "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
            "add_reminder": True,
            "remind_at": bad,
        })
        assert r.status_code == 400, f"expected 400 for {bad}"
        assert r.get_json()["error"] == "invalid_remind_at"


def test_review_reminder_endpoint_semantic_invalid(tmp_path: Path, monkeypatch):
    """Dedicated endpoint must reject non-existent date/time and seconds."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    for bad in ("2026-02-30T10:00", "2026-08-01T10:00:00", "2026-08-01T24:00"):
        resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
            "event_date": "2026-08-01",
            "remind_at": bad,
        })
        assert resp.status_code == 400, f"expected 400 for {bad}"
        assert resp.get_json()["error"] == "invalid_remind_at"


def test_review_create_remind_at_canonical_format(tmp_path: Path, monkeypatch):
    """Review create should store remind_at in canonical space format."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
        "add_reminder": True,
        "remind_at": "2099-01-01T14:30",
    })
    assert r.status_code == 200
    chain_id = r.get_json()["review"]["id"]
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert review_reminders[0]["remind_at"] == "2099-01-01 14:30:00"


def test_review_reminder_endpoint_canonical_format(tmp_path: Path, monkeypatch):
    """Dedicated reminder endpoint should store remind_at in canonical space format."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": "2026-08-01",
        "remind_at": "2026-08-01T10:00",
    })
    assert resp.status_code == 200
    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert review_reminders[0]["remind_at"] == "2026-08-01 10:00:00"


def test_review_reminder_is_due_with_canonical_format(tmp_path: Path, monkeypatch):
    """A reminder set to a past time today must be marked is_due in the list and SQL summary."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "j", "criteria": "c", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Pick a time one hour ago to guarantee it is past, while still using today's date.
    now = datetime.now()
    past = now - timedelta(hours=1)
    remind_at_str = past.strftime("%Y-%m-%dT%H:%M")
    resp = client.post(f"/api/reviews/{chain_id}/reminders", json={
        "event_date": past.strftime("%Y-%m-%d"),
        "remind_at": remind_at_str,
    })
    assert resp.status_code == 200

    reminders = client.get("/api/reminders?filter=all").get_json()["items"]
    review_reminders = [rm for rm in reminders if rm.get("review_chain_id") == chain_id]
    assert len(review_reminders) == 1
    assert review_reminders[0]["is_due"] is True
    # The SQL-level summary should also reflect at least one due reminder.
    summary = client.get("/api/reminders").get_json()["summary"]
    assert summary["due_total"] >= 1


def test_review_search_snapshot_news_title(tmp_path: Path, monkeypatch):
    """Full-text search should find reviews by snapshot-associated news title."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    _create_article_note(client, "https://example.com/review-test", "新闻想法备注")
    r = client.post("/api/reviews", json={
        "source_type": "article_note", "source_key": "https://example.com/review-test",
        "judgment": "判断A", "criteria": "标准A", "plan_review_date": "2099-01-01",
    })
    assert r.status_code == 200
    # The snapshot should contain the news title "复盘测试新闻"
    r = client.get("/api/reviews?q=复盘测试新闻")
    assert r.get_json()["total"] == 1


def test_review_search_snapshot_news_summary(tmp_path: Path, monkeypatch):
    """Full-text search should find reviews by snapshot-associated news summary."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    _create_article_note(client, "https://example.com/review-test", "新闻想法备注")
    r = client.post("/api/reviews", json={
        "source_type": "article_note", "source_key": "https://example.com/review-test",
        "judgment": "判断B", "criteria": "标准B", "plan_review_date": "2099-01-01",
    })
    assert r.status_code == 200
    # The snapshot should contain the news summary "新能源政策即将出台"
    r = client.get("/api/reviews?q=新能源政策即将出台")
    assert r.get_json()["total"] == 1


def test_review_search_evidence_url(tmp_path: Path, monkeypatch):
    """Full-text search should find reviews by evidence URL."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client)
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "判断C", "criteria": "标准C", "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    client.post(f"/api/reviews/{chain_id}/evidence", json={
        "news_title": "证据标题",
        "news_url": "https://evidence-test-url.example.com/unique",
    })
    r = client.get("/api/reviews?q=evidence-test-url")
    assert r.get_json()["total"] == 1


def test_review_search_no_duplicate(tmp_path: Path, monkeypatch):
    """Search results should not duplicate reviews even with multiple matches."""
    client, _ = _setup_review_env(tmp_path, monkeypatch)
    idea_id = _create_standalone_idea(client, "新能源政策观察")
    r = client.post("/api/reviews", json={
        "source_type": "standalone_idea", "source_key": str(idea_id),
        "judgment": "新能源融资成本将下降", "criteria": "融资成本数据",
        "plan_review_date": "2099-01-01",
    })
    chain_id = r.get_json()["review"]["id"]
    # Add evidence that also matches the search term
    client.post(f"/api/reviews/{chain_id}/evidence", json={
        "news_title": "新能源融资报告", "news_url": "https://example.com/report",
    })
    r = client.get("/api/reviews?q=新能源")
    assert r.get_json()["total"] == 1
    assert len(r.get_json()["items"]) == 1
