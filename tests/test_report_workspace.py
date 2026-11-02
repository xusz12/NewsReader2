"""Evidence, recovery and delegation boundaries; all sources/providers isolated."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import report_workspace as rw
import report_generation as gen
from report_tools import Bridge, request
from test_report_generation import source, Checkpoints


class Journal:
    def __init__(self): self.data = {}
    def __call__(self, key, value=None):
        if value is not None: self.data[key] = copy.deepcopy(value)
        return copy.deepcopy(self.data.get(key))


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(rw.report_model, "estimate_tokens", len)
    # Separate batches whose dates cross a storyline. Batching never removes
    # the first development from a later chapter's access to original evidence.
    snapshot = {"kind": "monthly", "sources": [source(i, str(i)+"事实"*4000) for i in range(1, 6)]}
    return rw.Workspace(snapshot, {"runtime": "pi", "model": "fixture", "limits": {"input_tokens": 40000}}, state_store=Journal())


def review_all(w):
    for batch, refs in w.batches.items():
        assert "error" not in w.invoke("report_review", {"batch_id": batch, "events": [{"text": "关键进展", "refs": [refs[0]]}], "omitted": refs})


def plan_all(w):
    return w.invoke("report_plan", {"parts": [{"id": "main", "title": "跨日变化", "event_ids": [e["id"] for e in w.events()]}], "omitted_events": []})


def final_actions(w, audit="已回读当前草稿并复核"):
    return {"actions": [{"tool": "report_draft", "arguments": {"part_id": p["id"]}} for p in w.state["plan"]["parts"]] + [
        {"tool": "report_finish", "arguments": {"revision": w.state["plan"]["revision"], "audit": audit}}]}


def test_paging_exposes_every_source_and_large_fields_are_explicitly_continued(workspace):
    workspace.page_tokens = 1000
    page = workspace.invoke("report_sources", {"limit": 2})
    assert page["total"] == 5 and page["next_offset"] == 2
    assert all(x["requires_source_read"] for x in page["items"])
    text, offset = "", 0
    while offset is not None:
        page = workspace.invoke("report_source", {"ref": "N00001", "offset": offset})
        text += page["json_text"]
        offset = page["next_offset"]
    assert json.loads(text) == workspace.sources["N00001"]
    assert "error" in workspace.invoke("report_sources", {"offset": -1})


def test_review_does_not_imply_selection_and_omitted_sources_remain_accessible(workspace):
    review_all(workspace)
    assert workspace.status()["reviewed_count"] == 5
    assert workspace.invoke("report_sources", {"query": "2事实"})["total"] == 1
    plan = plan_all(workspace)
    # Any source, including a preliminary omission, may support the final story.
    refs = list(workspace.sources)
    result = workspace.invoke("report_draft", {"part_id": "main", "revision": plan["revision"], "sections": [
        {"title": "月内变化", "paragraphs": [{"text": "起点、转折和结果。", "refs": refs}]}]})
    assert result["saved"] == "main"
    workspace.invoke("report_draft", {"part_id": "main"})
    assert workspace.invoke("report_finish", {"revision": plan["revision"], "audit": "复核时间、引用与反证"})["finished"]
    assert len(workspace.state["final"]["sources"]) == 5


def test_missing_sources_candidates_and_chapters_cannot_be_silently_omitted(workspace):
    batch = next(iter(workspace.batches))
    result = workspace.invoke("report_review", {"batch_id": batch, "events": [], "omitted": []})
    assert result["missing_refs"] == workspace.batches[batch]
    assert not workspace.state["reviews"]
    assert "error" in plan_all(workspace)
    review_all(workspace)
    assert "error" in workspace.invoke("report_plan", {"parts": [], "omitted_events": []})
    plan = plan_all(workspace)
    assert "error" in workspace.invoke("report_draft", {"part_id": "main", "revision": plan["revision"], "sections": [], "briefs": []})
    assert "error" in workspace.invoke("report_finish", {"revision": plan["revision"], "audit": "尚未完成"})


def test_finish_requires_reading_each_latest_draft_and_preserves_old_work(workspace):
    review_all(workspace)
    plan = plan_all(workspace)
    args = {"part_id": "main", "revision": plan["revision"], "briefs": [{"text": "完整正文", "refs": list(workspace.sources)}]}
    workspace.invoke("report_draft", args)
    finish = {"revision": plan["revision"], "audit": "已复核"}
    assert "main" in workspace.invoke("report_finish", finish)["error"]
    workspace.invoke("report_draft", {"part_id": "main"})
    workspace.invoke("report_draft", {**args, "briefs": [{"text": "更新后的正文", "refs": list(workspace.sources)}]})
    assert workspace.status()["drafts_pending_review"] == ["main"]
    assert "error" in workspace.invoke("report_finish", finish)
    workspace.invoke("report_draft", {"part_id": "main"})
    assert workspace.invoke("report_finish", finish)["finished"]
    assert workspace.state["final"]["generation"]["reviewed_chapters"] == ["main"]
    # An older interrupted run can resume just its final review, not rewrite
    # already-completed source selection or chapter prose.
    workspace.state["accepted"] = True
    workspace.state.pop("draft_reads")
    workspace.persist()
    restored = rw.Workspace(workspace.snapshot, workspace.config, state_store=workspace.store)
    assert restored.state["final"] is None and not restored.state["accepted"]
    assert restored.state["reviews"] and restored.state["drafts"]["main"]["briefs"][0]["text"] == "更新后的正文"


def test_candidate_ranges_preserve_coverage_and_reject_duplicate_chapter_assignment(workspace):
    for batch, refs in workspace.batches.items():
        workspace.invoke("report_review", {"batch_id": batch, "events": [
            {"text": "事件", "refs": [ref]} for ref in refs], "omitted": []})
    events = workspace.events()
    batch = next(iter(workspace.batches))
    first = [e["id"] for e in events if e["id"].startswith(batch+":")]
    rest = [e["id"] for e in events if e["id"] not in first]
    args = {"parts": [{"id": "main", "title": "主题", "event_ids": [first[0]+"-"+first[-1]]}], "omitted_events": rest}
    plan = workspace.invoke("report_plan", args)
    assert plan["parts"][0]["event_ids"] == first
    assert workspace.invoke("report_plan", {**args, "omitted_events": rest+[first[0]]}) == plan
    duplicate = {"id": "other", "title": "重复分配", "event_ids": [first[0]]}
    assert "duplicate_count" in workspace.invoke("report_plan", {**args, "parts": args["parts"]+[duplicate]})["error"]
    assert workspace.state["plan"] == plan
    allowed = {e["id"] for e in events}
    for invalid in [first[-1]+"-"+first[0], first[0]+"-B9999:E0001", first[0]+f"-{batch}:E9999"]:
        with pytest.raises(ValueError):
            rw.expand_event_ids([invalid], allowed)


def test_retry_restores_reviews_and_model_change_does_not_reuse_them(workspace):
    review_all(workspace)
    restored = rw.Workspace(workspace.snapshot, workspace.config, state_store=workspace.store)
    assert restored.state["reviews"] == workspace.state["reviews"]
    other = rw.Workspace(workspace.snapshot, {**workspace.config, "model": "changed"}, state_store=workspace.store)
    assert not other.state["reviews"]
    changed = copy.deepcopy(workspace.snapshot)
    changed["sources"][0]["summary"] = "修订"
    assert not rw.Workspace(changed, workspace.config, state_store=workspace.store).state["reviews"]


def test_large_context_pi_batches_reduce_repeated_reasoning_but_keep_all_sources(workspace):
    sources = workspace.snapshot["sources"] * 1
    small = rw.working_batches(sources, workspace.config)
    large = rw.working_batches(sources, {"runtime": "pi", "limits": {"input_tokens": 834464, "output_tokens": 65536}})
    assert len(large) < len(small)
    assert [ref for batch in large.values() for ref in batch] == list(workspace.sources)


def test_planned_chapters_are_written_from_original_sources_in_parallel(workspace):
    review_all(workspace)
    events = workspace.events()
    parts = [{"id": "p"+str(i), "title": "独立主题", "event_ids": [event["id"]]} for i, event in enumerate(events)]
    workspace.invoke("report_plan", {"parts": parts, "omitted_events": []})
    calls = []
    def model(config, prompt):
        if "本次独立编写计划中的一个章节" in prompt:
            material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
            assert "事实" in material[0]["title"]  # Original, not only the event summary.
            calls.append("draft")
            return gen.encoded({"sections": [], "briefs": [{"text": "依据原始资料编写", "refs": [r for s in material for r in s["refs"]]}]})
        assert "saved_chapters" in prompt
        assert len(workspace.state["drafts"]) == len(parts)
        return gen.encoded(final_actions(workspace))
    result = rw.program_editor(workspace, workspace.config, model, Checkpoints())
    assert len(calls) == len(parts) and len(result["briefs"]) == len(parts)


def test_changed_review_invalidates_plan_and_rejects_late_delegate_draft(workspace):
    review_all(workspace)
    old = plan_all(workspace)
    batch = next(iter(workspace.batches))
    refs = workspace.batches[batch]
    workspace.invoke("report_review", {"batch_id": batch, "events": [{"text": "新增反证", "refs": refs}], "omitted": []})
    new = plan_all(workspace)
    assert old["revision"] != new["revision"]
    assert "error" in workspace.invoke("report_draft", {"part_id": "main", "revision": old["revision"], "briefs": [{"text": "迟到的旧稿", "refs": list(workspace.sources)}]})


def test_candidate_directory_survives_preliminary_stateless_tool_reads(workspace):
    review_all(workspace)
    calls = []
    def model(config, prompt):
        calls.append(prompt)
        assert "完整候选目录（已读取全部页）" in prompt
        for event in workspace.events():
            assert gen.encoded(event) in prompt
        if len(calls) == 1:
            return gen.encoded({"actions": [{"tool": "report_status", "arguments": {}}]})
        assert '"tool":"report_status"' in prompt
        raise RuntimeError("fixture_stop_after_read")
    with pytest.raises(RuntimeError, match="fixture_stop_after_read"):
        rw.program_editor(workspace, workspace.config, model, Checkpoints())
    assert len(calls) == 2


def test_malformed_editor_actions_do_not_poison_the_next_retry(workspace):
    review_all(workspace)
    cache = Checkpoints()
    with pytest.raises(RuntimeError, match="report_invalid_result"):
        rw.program_editor(workspace, workspace.config, lambda *_: '{"actions":[]}', cache)
    assert workspace.state["turn"] == 1
    def call(*_):
        raise RuntimeError("fixture_fresh_call")
    with pytest.raises(RuntimeError, match="fixture_fresh_call"):
        rw.program_editor(workspace, workspace.config, call, cache)


def test_cached_chapter_repairs_only_missing_planned_events(workspace):
    for batch, refs in workspace.batches.items():
        workspace.invoke("report_review", {"batch_id": batch, "events": [
            {"text": "独立事件", "refs": [r]} for r in refs], "omitted": []})
    events = workspace.events()
    plan = workspace.invoke("report_plan", {"parts": [{"id": "main", "title": "重点", "event_ids": [e["id"] for e in events[:2]]}],
                                           "omitted_events": [e["id"] for e in events[2:]]})
    good = {"title": "已完成的段落", "paragraphs": [{"text": "已有事实", "refs": events[0]["refs"]}]}
    raw = {"sections": [good], "briefs": []}
    cache = Checkpoints()
    key = workspace.key+":draft:"+plan["revision"]+":main"
    cache.saved[key+":raw"] = raw
    cache.saved[key+":checked"] = gen.validate_report(raw, list(workspace.sources.values()), "monthly")
    repairs = []
    def model(config, prompt):
        if "本次独立编写计划中的一个章节" in prompt:
            assert "本次仅修复" in prompt and '"path":"/briefs"' in prompt
            material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
            assert [s["refs"] for s in material] == [events[1]["refs"]]
            repairs.append(1)
            return gen.encoded({"repairs": [{"path": "/briefs", "value": [{"text": "补充的独立事件", "refs": events[1]["refs"]}]}]})
        return gen.encoded(final_actions(workspace))
    result = rw.program_editor(workspace, workspace.config, model, cache)
    assert result["sections"][0]["paragraphs"][0]["text"] == good["paragraphs"][0]["text"]
    assert len(repairs) == 1 and result["briefs"][0]["refs"] == events[1]["refs"]


def test_mcp_bridge_is_report_scoped_and_concurrent_reviews_are_durable(workspace):
    with Bridge(workspace) as bridge:
        def save(batch):
            return request(bridge.path, "report_review", {"batch_id": batch, "events": [{"text": "进展", "refs": workspace.batches[batch]}], "omitted": []})
        with ThreadPoolExecutor(max_workers=3) as pool:
            saved = list(pool.map(save, workspace.batches))
        assert all("saved" in item for item in saved)
        assert request(bridge.path, "report_source", {"ref": "outside"}).get("error")
        assert request(bridge.path, "shell", {"command": "no"}).get("error")
    restored = rw.Workspace(workspace.snapshot, workspace.config, state_store=workspace.store)
    assert set(restored.state["reviews"]) == set(workspace.batches)


def test_bridge_closes_only_after_an_accepted_journal_write_finishes(workspace):
    entered, release = threading.Event(), threading.Event()
    def progress(*args, **kwargs):
        entered.set()
        assert release.wait(3)
    workspace.progress = progress
    batch = next(iter(workspace.batches))
    with Bridge(workspace) as bridge:
        client = threading.Thread(target=lambda: request(bridge.path, "report_review", {
            "batch_id": batch, "events": [], "omitted": workspace.batches[batch]}))
        client.start()
        assert entered.wait(3)
        threading.Timer(.1, release.set).start()
    client.join(timeout=3)
    assert not client.is_alive()
    restored = rw.Workspace(workspace.snapshot, workspace.config, state_store=workspace.store)
    assert batch in restored.state["reviews"]


def test_program_editor_uses_same_model_and_preserves_full_chapters_on_retry(workspace):
    calls, fail = [], [True]
    def model(config, prompt):
        assert config["model"] == "fixture"
        calls.append(prompt)
        if "本批仅建立" in prompt:
            material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
            return gen.encoded({"events": [{"text": "独立事件", "refs": [item["ref"]]} for item in material], "omitted": []})
        if fail[0]:
            fail[0] = False
            raise RuntimeError("report_model_timeout")
        if workspace.state["plan"] is None:
            return gen.encoded({"actions": [{"tool": "report_plan", "arguments": {"parts": [{"id": "main", "title": "主线", "event_ids": [e["id"] for e in workspace.events()]}], "omitted_events": []}}]})
        revision = workspace.state["plan"]["revision"]
        if not workspace.state["drafts"]:
            return gen.encoded({"actions": [{"tool": "report_draft", "arguments": {"revision": revision, "part_id": "main", "briefs": [{"text": "完整重要事实", "refs": list(workspace.sources)}]}}]})
        return gen.encoded(final_actions(workspace))
    checkpoint = Checkpoints()
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        rw.program_editor(workspace, workspace.config, model, checkpoint)
    first_calls = sum("本批仅建立" in p for p in calls)
    result = rw.program_editor(workspace, workspace.config, model, checkpoint)
    assert first_calls == len(workspace.batches)
    assert sum("本批仅建立" in p for p in calls) == first_calls
    assert result["briefs"][0]["text"] == "完整重要事实"


def test_native_completion_without_persisted_final_report_is_not_success(workspace):
    with pytest.raises(RuntimeError, match="report_work_incomplete"):
        rw.generate_workspace(workspace.snapshot, {**workspace.config, "runtime": "codex", "codex": {}},
                              lambda *_: "子任务已完成", Checkpoints(), lambda *a, **kw: None)


def test_short_ultra_report_keeps_native_workspace_capability(monkeypatch):
    seen = []
    monkeypatch.setattr(rw, "generate_workspace", lambda *args: seen.append(args) or {"sections": [], "briefs": []})
    config = {"runtime": "codex", "codex": {"reasoning_effort": "ultra"}, "limits": {"input_tokens": 100000}}
    gen.generate({"kind": "evening", "sources": [source(1)]}, config,
                 lambda *_: pytest.fail("Ultra should keep its tools"), Checkpoints())
    assert len(seen) == 1


def test_native_editor_continues_partial_turn_without_losing_valid_work(workspace):
    calls = []
    def call(config, prompt):
        w = config["_workspace"]
        calls.append(1)
        if len(calls) == 1:
            review_all(w)
            return "已整理全部新闻，下一步将编写"
        assert "完整报告尚未保存" in prompt and w.status()["reviewed_count"] == 5
        plan = plan_all(w)
        w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"], "briefs": [{"text": "完成跨日比较", "refs": list(w.sources)}]})
        w.invoke("report_draft", {"part_id": "main"})
        w.invoke("report_finish", {"revision": plan["revision"], "audit": "已复核"})
        return "已完成"
    result = rw.generate_workspace(workspace.snapshot, {**workspace.config, "runtime": "codex", "codex": {}}, call, Checkpoints(), lambda *a, **kw: None)
    assert len(calls) == 2 and result["briefs"][0]["text"] == "完成跨日比较"


def test_pi_failed_batch_stops_scheduling_remaining_month(workspace):
    calls = []
    def model(config, prompt):
        calls.append(1)
        raise RuntimeError("report_output_truncated")
    with pytest.raises(RuntimeError, match="report_output_truncated"):
        rw.program_editor(workspace, workspace.config, model, Checkpoints())
    assert 1 <= len(calls) <= 3


def test_repeated_invalid_editor_actions_stop_without_losing_reviews(workspace):
    review_all(workspace)
    calls = []
    def model(config, prompt):
        if calls:
            assert '"arguments":{"attempt":"retain for correction"}' in prompt
        calls.append(1)
        return gen.encoded({"actions": [{"tool": "unknown", "arguments": {"attempt": "retain for correction"}}]})
    with pytest.raises(RuntimeError, match="report_work_incomplete"):
        rw.program_editor(workspace, workspace.config, model, Checkpoints())
    assert len(calls) == 3 and workspace.status()["reviewed_count"] == 5
    restored = rw.Workspace(workspace.snapshot, workspace.config, state_store=workspace.store)
    assert restored.state["agent_results"][0]["arguments"] == {"attempt": "retain for correction"}


def test_pi_truncation_expands_budget_once_without_changing_model_or_effort(workspace, monkeypatch):
    config = {**workspace.config, "reasoning_effort": "max", "limits": {"context_window": 1000000, "input_tokens": 834464, "output_tokens": 65536, "max_output_tokens": 384000}}
    seen = []
    def model(selected, prompt):
        seen.append(selected)
        if len(seen) == 1: raise RuntimeError("report_output_truncated")
        return "complete"
    def editor(w, selected, call, checkpoint):
        assert call(selected, "bounded evidence") == "complete"
        w.state["final"] = {"sections": [], "briefs": []}
        return w.state["final"]
    monkeypatch.setattr(rw, "program_editor", editor)
    rw.generate_workspace(workspace.snapshot, config, model, Checkpoints(), lambda *a, **kw: None)
    assert [c["limits"]["output_tokens"] for c in seen] == [65536, 131072]
    assert seen[1]["limits"]["input_tokens"] == 768928
    assert all(c["model"] == "fixture" and c["reasoning_effort"] == "max" for c in seen)


def test_failed_root_turn_requires_coordinator_acknowledgement_on_retry(workspace):
    config = {**workspace.config, "runtime": "codex", "codex": {"model": "fixture"}}
    journal, calls = Journal(), []
    def call(config, prompt):
        w = config["_workspace"]
        calls.append(1)
        if len(calls) == 1:
            review_all(w)
            plan = plan_all(w)
            w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"], "briefs": [{"text": "已经保存的完整章节", "refs": list(w.sources)}]})
            w.invoke("report_draft", {"part_id": "main"})
            w.invoke("report_finish", {"revision": plan["revision"], "audit": "已复核"})
            raise RuntimeError("report_model_timeout")
        assert w.state["final"] and not w.state["accepted"]
        return "主编辑确认完成"
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        rw.generate_workspace(workspace.snapshot, config, call, Checkpoints(), lambda *a, **kw: None, journal)
    result = rw.generate_workspace(workspace.snapshot, config, call, Checkpoints(), lambda *a, **kw: None, journal)
    assert result["briefs"][0]["text"] == "已经保存的完整章节" and len(calls) == 2
    rw.generate_workspace(workspace.snapshot, config, lambda *_: pytest.fail("Accepted result is reusable"), Checkpoints(), lambda *a, **kw: None, journal)
