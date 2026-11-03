"""Title-only evidence boundaries for native and programmatic report editors."""
import json

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


def test_pi_workspace_selects_with_titles_then_writes_only_selected_details():
    snapshot = {"kind": "monthly", "sources": [source(1, "只有标题"), source(2, "已选新闻", body="被选正文"),
                                               source(3, "未选新闻", body="不该被读到" * 50000)]}
    w = rw.Workspace(snapshot, {"runtime": "pi", "model": "fixture", "limits": {"input_tokens": 100000}})
    phases = []
    def model(config, prompt):
        assert "不该被读到" not in prompt
        if "本批仅建立" in prompt:
            phases.append("select")
            material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
            assert all(set(s) == {"ref", "title", "published_at", "source_name", "refs"} for s in material)
            assert "被选正文" not in prompt
            return gen.encoded({"events": [{"text": "标题重点", "refs": ["N00001", "N00002"]}], "omitted": ["N00003"]})
        if not w.state["plan"]:
            phases.append("plan")
            assert "被选正文" not in prompt
            return gen.encoded({"actions": [{"tool": "report_plan", "arguments": {
                "parts": [{"id": "main", "title": "重点", "event_ids": [w.events()[0]["id"]]}], "omitted_events": []}}]})
        if "本次独立编写计划中的一个章节" in prompt:
            phases.append("write")
            assert "被选正文" in prompt
            material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
            assert {s["ref"] for s in material} == {"N00001", "N00002"}
            return gen.encoded({"sections": [], "briefs": [{"text": "标题事实及被选正文", "refs": ["N00001", "N00002"]}]})
        phases.append("audit")
        return gen.encoded(final_actions(w))
    result = rw.program_editor(w, w.config, model, Checkpoints())
    assert phases == ["select", "plan", "write", "audit"]
    assert {s["ref"] for s in result["sources"]} == {"N00001", "N00002"}
