"""Title-only evidence boundaries for native and programmatic report editors."""
import json
import pytest

import report_generation as gen
import report_workspace as rw
from test_report_generation import source, Checkpoints
from test_report_workspace import Journal, final_actions


def test_workspace_tools_hide_details_until_selection_and_never_reveal_omitted_bodies():
    sources = [source(1, "标题入选"), source(2, "正文入选", summary="可用摘要", body="可用正文" * 500),
               source(3, "标题舍弃", summary="隐蔽摘要", body="隐蔽正文" * 1000)]
    snapshot = {"kind": "monthly", "sources": sources}
    config = {"runtime": "codex", "limits": {"input_tokens": 40000}}
    journal = Journal()
    w = rw.Workspace(snapshot, config, state_store=journal)
    assert not w.status()["selection_locked"]
    items = w.invoke("report_sources", {})["items"]
    assert items == [gen.headline_material(s) for s in sources]
    assert w.invoke("report_sources", {"query": "正文"})["total"] == 1  # Title only.
    assert w.invoke("report_sources", {"query": "隐蔽"})["total"] == 0
    assert w.invoke("report_sources", {"query": "可用"})["total"] == 0
    assert json.loads(w.invoke("report_source", {"ref": "N00002"})["json_text"]) == gen.headline_material(sources[1])
    batch = next(iter(w.batches))
    assert "error" not in w.invoke("report_review", {"batch_id": batch, "events": [
        {"text": "标题支持的重要进展", "refs": ["N00001", "N00002"]},
        {"text": "不重要的事件", "refs": ["N00003"]}], "omitted": []})
    # Candidate inclusion is not enough to expose either summary or body.
    assert w.invoke("report_sources", {})["items"] == items
    plan = w.invoke("report_plan", {"parts": [{"id": "main", "title": "重点", "event_ids": [w.events()[0]["id"]]}],
                                    "omitted_events": [w.events()[1]["id"]]})
    assert w.status()["selection_locked"]
    w = rw.Workspace(snapshot, config, state_store=journal)
    assert w.selected_refs() == {"N00001", "N00002"}
    page = w.invoke("report_source", {"ref": "N00002"})
    assert json.loads(page["json_text"])["body"] == sources[1]["body"]
    assert json.loads(w.invoke("report_source", {"ref": "N00003"})["json_text"]) == gen.headline_material(sources[2])
    assert w.invoke("report_sources", {"query": "隐蔽"})["total"] == 0
    assert "error" in w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"],
        "briefs": [{"text": "正文影响后试图改选", "refs": ["N00002", "N00003"]}]})
    assert "error" in w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"],
        "briefs": [{"text": "漏掉仅标题新闻", "refs": ["N00002"]}]})
    plain = [{**s, "body": "", "summary": "", "body_excerpt": False} for s in sources]
    assert rw.working_batches(sources, config) == rw.working_batches(plain, config)


@pytest.mark.parametrize("runtime", ["pi", "codex"])
def test_oar_workspace_selects_with_titles_then_writes_only_selected_details(runtime):
    snapshot = {"kind": "monthly", "sources": [source(1, "只有标题"), source(2, "已选新闻", body="被选正文"),
                                               source(3, "未选新闻", body="不该被读到" * 50000)]}
    config = {"runtime": runtime, "model": "fixture", "codex": {}, "limits": {"input_tokens": 100000}}
    def model(selected, prompt):
        w = selected["_workspace"]
        assert gen.EDITOR_RULES in prompt and gen.EDITION_RULES in prompt
        assert w.status()["context"]["period_focus"] == gen.PERIOD_FOCUS["monthly"]
        assert "不该被读到" not in prompt and "被选正文" not in prompt
        material = w.invoke("report_sources", {})["items"]
        assert all(set(s) == {"ref", "title", "published_at", "source_name"} for s in material)
        batch = next(iter(w.batches))
        w.invoke("report_review", {"batch_id": batch, "events": [{"text": "标题重点", "refs": ["N00001", "N00002"]}], "omitted": ["N00003"]})
        plan = w.invoke("report_plan", {"parts": [{"id": "main", "title": "重点", "event_ids": [w.events()[0]["id"]]}], "omitted_events": []})
        assert "被选正文" in w.invoke("report_source", {"ref": "N00002"})["json_text"]
        assert "不该被读到" not in w.invoke("report_source", {"ref": "N00003"})["json_text"]
        w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"],
            "briefs": [{"text": "标题事实及被选正文", "refs": ["N00001", "N00002"]}]})
        for action in final_actions(w)["actions"]:
            assert "error" not in w.invoke(action["tool"], action["arguments"])
        return "已完成"
    result = rw.generate_workspace(snapshot, config, model, Checkpoints(), lambda *a, **kw: None)
    assert {s["ref"] for s in result["sources"]} == {"N00001", "N00002"}


@pytest.mark.parametrize("with_section", [False, True])
def test_title_report_packs_briefs_in_order_by_selected_material_and_resumes(monkeypatch, with_section):
    monkeypatch.setattr(rw.report_model, "estimate_tokens", len)
    sources = [source(1, "本月决定"), source(2, "新规生效", monthly_role="important"),
               source(3, "法院作出裁决"), source(4, "公司公布财报", body="财务数据" * 3000),
               source(5, "项目获批"), source(6, "试产推迟"), source(7, "上月公布草案", monthly_role="background"),
               source(8, "未入选新闻", body="未选正文")]
    snapshot = {"kind": "monthly", "sources": sources}
    config = {"runtime": "dsh", "limits": {"input_tokens": 8000}}
    sections = [{"title": "本月决定", "paragraphs": [{"text": "本月决定", "refs": ["N00001"]}]}] if with_section else []
    briefs = [{"text": "上月公布的草案本月生效", "refs": ["N00002", "N00007"]}]
    briefs.extend({"text": s["title"], "refs": [s["ref"]]} for s in sources[2:6])
    title_report = gen.validate_report({"sections": sections, "briefs": briefs}, sources, "monthly")
    journal = Journal()
    w = rw.Workspace(snapshot, config, state_store=journal, title_report=title_report)
    plan = w.status()["plan"]
    parts = plan["parts"]
    first_brief = len(sections)
    events = {event["id"]: event for event in w.events()}
    assert [p["title"] for p in parts] == [s["title"] for s in sections] + ["简讯"] * 3
    assert [[events[i]["text"] for i in p["event_ids"]] for p in parts[first_brief:]] == [
        [b["text"] for b in briefs[:2]], [briefs[2]["text"]], [b["text"] for b in briefs[3:]]]
    assert "未选正文" not in w.invoke("report_source", {"ref": "N00008"})["json_text"]
    if with_section:
        assert "saved" in w.invoke("report_draft", {"part_id": parts[0]["id"], "revision": plan["revision"], "sections": sections})
    first = {"part_id": parts[first_brief]["id"], "revision": plan["revision"]}
    assert "error" in w.invoke("report_draft", {**first, "briefs": briefs[:1]})
    assert "saved" in w.invoke("report_draft", {**first, "sections": [], "briefs": briefs[:2]})

    resumed = rw.Workspace(snapshot, config, state_store=journal, title_report=title_report)
    assert resumed.status()["plan"] == plan
    for part, remaining in zip(parts[first_brief+1:], [briefs[2:3], briefs[3:]], strict=True):
        assert "saved" in resumed.invoke("report_draft", {"part_id": part["id"], "revision": plan["revision"], "briefs": remaining})
    for action in final_actions(resumed)["actions"]:
        assert "error" not in resumed.invoke(action["tool"], action["arguments"])
    result = resumed.state["final"]
    assert result["briefs"] == briefs and result["sections"] == title_report["sections"]
    assert result["sources"] == title_report["sources"]
