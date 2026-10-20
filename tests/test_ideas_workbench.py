"""Lightweight standalone editing and linked idea list contracts."""
import pytest
from test_api import _setup_agent_api_fixture


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return _setup_agent_api_fixture(tmp_path, monkeypatch)


def test_title_round_trip_and_older_client_preserves_title(fixture):
    _, client, *_ = fixture
    created = client.post("/api/standalone-ideas", json={
        "title": "  研究方向  ", "note": "  **证据**\n\n待核实  ",
    }).json["idea"]
    assert created["title"] == "研究方向"
    assert created["note"] == "**证据**\n\n待核实"
    path = f"/api/standalone-ideas/{created['standalone_id']}"
    updated = client.patch(path, json={"note": "旧客户端补充"}).json["idea"]
    assert updated["title"] == "研究方向"
    assert updated["created_at"] == created["created_at"]
    listed = client.get("/api/ideas?type=standalone").json["items"][0]
    assert listed == updated
    assert client.patch(path, json={"title": "", "note": "无标题"}).json["idea"]["title"] == ""


@pytest.mark.parametrize("title", [None, 123, [], "x" * 201])
def test_invalid_title_rejected_without_losing_existing_content(fixture, title):
    _, client, *_ = fixture
    idea = client.post("/api/standalone-ideas", json={"title": "保留标题", "note": "保留正文"}).json["idea"]
    assert client.post("/api/standalone-ideas", json={"title": title, "note": "正文"}).status_code == 400
    assert client.patch(f"/api/standalone-ideas/{idea['standalone_id']}",
                        json={"title": title, "note": "新正文"}).status_code == 400
    listed = client.get("/api/ideas?type=standalone").json["items"]
    assert listed == [idea]


def test_legacy_standalone_schema_migrates_idempotently(fixture):
    app, client, *_ = fixture
    with app.db_conn() as conn:
        conn.execute("DROP TABLE standalone_ideas")
        conn.execute("CREATE TABLE standalone_ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO standalone_ideas VALUES (42, '历史正文', '2026-09-01 10:00:00', '2026-09-02 12:00:00')")
    app.ensure_db()
    app.ensure_db()
    idea = client.get("/api/ideas?type=standalone").json["items"][0]
    assert (idea["standalone_id"], idea["title"], idea["note"], idea["created_at"], idea["updated_at"]) == (
        42, "", "历史正文", "2026-09-01 10:00:00", "2026-09-02 12:00:00")
    new = client.post("/api/standalone-ideas", json={"title": "迁移后", "note": "新正文"}).json["idea"]
    assert new["standalone_id"] > 42


def test_linked_preview_uses_last_entry_without_splitting_legacy_text(fixture):
    _, client, item, *_ = fixture
    path = f"/api/news/{item['id']}"
    client.post(path + "/notes", json={"note": "早期想法"})
    client.post(path + "/notes", json={"note": "最新想法", "quote": {"text": "依据", "source": "original"}})
    idea = client.get("/api/ideas?type=article").json["items"][0]
    assert idea["note_count"] == 2
    assert idea["latest_note_preview"] == "最新想法"
    assert client.get(path + "/detail").json["notes"][-1]["quote_text"] == "依据"
    client.put(path + "/note", json={"note": "历史笔记\n\n---\n分隔线之后"})
    idea = client.get("/api/ideas?type=article").json["items"][0]
    assert idea["note_count"] == 1
    assert idea["latest_note_preview"].startswith("历史笔记")
