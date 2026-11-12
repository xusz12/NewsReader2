"""Reading filters share statistics without changing scopes or paging rules."""
from urllib.parse import urlencode

import pytest

from test_news_classification import api


@pytest.mark.parametrize("endpoint,query", [("news", ""), ("search", "分类共同测试词")])
def test_combined_facets_match_legacy_queries_and_cursor_pages(api, endpoint, query):
    module, client = api
    base = dict(q=query, read_filter="unread", feed_filters="1", per=10)
    get = lambda **args: client.get(f"/api/{endpoint}?" + urlencode({**base, **args})).json
    all_categories = get()
    scoped = get(category="economy")
    combined = get(category="economy", include_facets="1")
    assert combined["total"] == scoped["total"] == 24
    assert combined["filter_total"] == all_categories["total"] == 36
    assert combined["date_counts"] == scoped["date_counts"]
    assert combined["category_counts"]["economy"] == 24
    assert combined["category_counts"]["ai"] == 10
    assert combined["items"] == scoped["items"]
    ids = {item["id"] for item in combined["items"]}
    # Removing the preceding page must not skip rows in search or ordinary lists.
    client.post("/api/news/mark-read-by-ids", json={"item_ids": sorted(ids)})
    cursor = combined["next_cursor"]
    second = get(category="economy", include_facets="1", page=2, batch=combined["batch"],
                 cursor_id=cursor["id"], cursor_date=cursor["date_key"], cursor_published_at=cursor["published_at"])
    assert len(second["items"]) == 10
    assert not ids.intersection(item["id"] for item in second["items"])
    assert second["total"] == 14
    assert second["filter_total"] == 26


def test_list_evaluates_visibility_once_then_only_hydrates_page(api, monkeypatch):
    module, client = api
    original = module.is_bloomberg_video_url
    calls = []
    def visible(url):
        calls.append(url)
        return original(url)
    monkeypatch.setattr(module, "is_bloomberg_video_url", visible)
    data = client.get("/api/news?read_filter=unread&include_facets=1&per=10").json
    assert data["total"] == 36 and len(data["items"]) == 10
    assert len(calls) <= 46  # One filtered scope plus the 10 visible page rows.


def test_combined_sources_keep_read_sources_available_and_nav_counts_live(api):
    _, client = api
    first = client.get("/api/news?per=10").json["items"][0]
    client.patch(f"/api/news/{first['id']}/state", json={"favorite": True, "important": True})
    combined = client.get("/api/sources?collection=feed&read_filter=unread&include_catalog=1").json
    assert combined["sources"] == client.get("/api/sources?collection=feed&read_filter=unread").json["sources"]
    assert combined["catalog"] == client.get("/api/sources?collection=feed&read_filter=all").json["sources"]
    nav = client.get("/api/nav-summary").json["summary"]
    for key, collection, read_filter in [("feed_unread", "feed", "unread"), ("read_later_unread", "read_later", "unread"), ("favorites", "favorites", "all")]:
        assert nav[key] == client.get("/api/news?" + urlencode(dict(collection=collection, read_filter=read_filter, count_only="1"))).json["total"]


@pytest.mark.parametrize("collection,flag", [("favorites", "favorite"), ("important", "important")])
def test_sparse_saved_collections_preserve_both_batch_boundaries(api, collection, flag):
    module, client = api
    with module.db_conn() as conn:
        boundary = conn.execute("SELECT MAX(seq) FROM topic_ingestions").fetchone()[0]
        saved_id = conn.execute("SELECT item_id FROM topic_ingestions WHERE seq=?", (boundary,)).fetchone()[0]
    client.patch(f"/api/news/{saved_id}/state", json={flag: True})
    base = f"/api/news?collection={collection}&count_only=1"
    assert client.get(base + f"&batch={boundary}").json["total"] == 1
    assert client.get(base + f"&batch={boundary - 1}").json["total"] == 0
    assert client.get(base + f"&after_batch={boundary - 1}").json["total"] == 1
    assert client.get(base + f"&after_batch={boundary}").json["total"] == 0


def test_ideas_search_includes_unloaded_pages_and_paginates_retained_types(api):
    _, client = api
    for index in range(35):
        client.post("/api/standalone-ideas", json={"title": f"分页 {index}", "note": f"想法 {index}"})
    first = client.get("/api/ideas?reading_only=1&per=10").json
    second = client.get("/api/ideas?reading_only=1&per=10&page=2").json
    assert first["total"] == second["total"] == 35
    assert len(first["items"]) == len(second["items"]) == 10
    assert not {item["idea_id"] for item in first["items"]}.intersection(item["idea_id"] for item in second["items"])
    missing = client.get("/api/ideas?reading_only=1&q=分页%20不存在").json
    assert missing["total"] == 0 and missing["items"] == []
    result = client.get("/api/ideas?reading_only=1&q=想法%200").json
    assert result["total"] == 1 and result["items"][0]["title"] == "分页 0"


def test_idea_cursor_keeps_unseen_rows_after_saving_boundary_and_deleting_loaded_row(api):
    module, client = api
    for index in range(35):
        client.post("/api/standalone-ideas", json={"title": f"续页 {index}", "note": "同秒创建"})
    with module.db_conn() as conn:
        conn.execute("UPDATE standalone_ideas SET updated_at='2026-01-01 00:00:00'")
    base = dict(type="standalone", q="续页", cursor_mode=1, per=10)
    get = lambda **args: client.get("/api/ideas?" + urlencode({**base, **args})).json
    original = get(per=100)["items"]
    first = get()
    assert first["items"] == original[:10]
    boundary = first["items"][-1]
    client.patch(f"/api/standalone-ideas/{boundary['standalone_id']}", json={"note": "保存后移到最新位置"})
    client.delete(f"/api/standalone-ideas/{first['items'][0]['standalone_id']}")
    client.post("/api/standalone-ideas", json={"title": "续页 新增", "note": "新想法"})
    data, remaining, page = first, [], 1
    while data["has_more"]:
        cursor = data["next_cursor"]
        page += 1
        data = get(page=page, cursor_id=cursor["idea_id"], cursor_updated_at=cursor["updated_at"])
        remaining.extend(item["idea_id"] for item in data["items"])
        assert data["total"] == sum(data["date_counts"].values()) == 35
    assert remaining == [item["idea_id"] for item in original[10:]]
    assert data["next_cursor"] is None


@pytest.mark.parametrize("sort_order", ["default", "reverse"])
def test_idea_cursor_survives_deleted_boundary_in_both_directions(api, sort_order):
    module, client = api
    for index in range(25):
        client.post("/api/standalone-ideas", json={"note": f"边界 {index}"})
    with module.db_conn() as conn:
        conn.execute("UPDATE standalone_ideas SET updated_at='2026-01-01 00:00:00'")
    base = dict(reading_only=1, cursor_mode=1, per=10, sort_order=sort_order)
    get = lambda **args: client.get("/api/ideas?" + urlencode({**base, **args})).json
    original, first = get(per=100), get()
    cursor = first["next_cursor"]
    for idea in first["items"]:
        client.delete(f"/api/standalone-ideas/{idea['standalone_id']}")
    second = get(page=2, cursor_id=cursor["idea_id"], cursor_updated_at=cursor["updated_at"])
    assert second["items"] == original["items"][10:20]
    assert second["has_more"] and second["total"] == 15
    cursor = second["next_cursor"]
    last = get(page=3, cursor_id=cursor["idea_id"], cursor_updated_at=cursor["updated_at"])
    assert last["items"] == original["items"][20:] and not last["has_more"]


@pytest.mark.parametrize("cursor", ["cursor_id=standalone:1", "cursor_updated_at=2026-01-01", "cursor_id=&cursor_updated_at="])
def test_idea_cursor_rejects_incomplete_boundary(api, cursor):
    _, client = api
    response = client.get("/api/ideas?cursor_mode=1&" + cursor)
    assert response.status_code == 400 and response.json["error"] == "invalid_cursor"


def test_lightweight_source_groups_keep_hidden_sources_without_settings_catalogs(api, monkeypatch):
    module, client = api
    with module.db_conn() as conn:
        conn.execute("UPDATE items SET url='https://www.reuters.com/world/example',source='Reuters',source_name='World' WHERE id='n00'")
    hidden = module.derive_source_subkey("https://www.reuters.com/world/example", "news", "Reuters", "World")["key"]
    settings = module.current_runtime_settings()
    settings["feed"]["hidden_source_subkeys"] = [hidden]
    monkeypatch.setattr(module, "current_runtime_settings", lambda: settings)
    result = client.get("/api/sources?collection=feed&read_filter=unread&include_catalog=1&source_groups=1").json
    assert "reuters" in [group["source_key"] for group in result["groups"]]
    assert "reuters" not in [source["key"] for source in result["sources"]]
