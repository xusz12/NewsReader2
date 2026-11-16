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
        assert "error" not in w.invoke("report_review", {"batch_id": batch, "events": [{"text": "关键进展", "refs": refs}], "omitted": refs})


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
    assert json.loads(text) == gen.headline_material(workspace.sources["N00001"])
    assert "error" in workspace.invoke("report_sources", {"offset": -1})


def test_preliminary_omissions_can_be_reconsidered_using_titles_before_plan(workspace):
    for batch, refs in workspace.batches.items():
        workspace.invoke("report_review", {"batch_id": batch, "events": [], "omitted": refs})
    assert workspace.status()["reviewed_count"] == 5
    assert workspace.invoke("report_sources", {"query": "2事实"})["total"] == 1
    review_all(workspace)
    plan = plan_all(workspace)
    # A preliminary omission can be selected again before any body is exposed.
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


def test_confirmed_title_selection_rejects_reselection_and_late_draft(workspace):
    review_all(workspace)
    old = plan_all(workspace)
    batch = next(iter(workspace.batches))
    refs = workspace.batches[batch]
    assert "error" in workspace.invoke("report_review", {"batch_id": batch, "events": [{"text": "新增反证", "refs": refs}], "omitted": []})
    new = plan_all(workspace)
    assert old == new
    assert "error" in workspace.invoke("report_plan", {"parts": [], "omitted_events": [e["id"] for e in workspace.events()]})
    assert "error" in workspace.invoke("report_draft", {"part_id": "main", "revision": "stale", "briefs": [{"text": "迟到的旧稿", "refs": list(workspace.sources)}]})


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


@pytest.mark.parametrize("runtime", ["pi", "codex"])
def test_native_completion_without_persisted_final_report_is_not_success(workspace, runtime):
    with pytest.raises(RuntimeError, match="report_work_incomplete"):
        rw.generate_workspace(workspace.snapshot, {**workspace.config, "runtime": runtime, "codex": {}},
                              lambda *_: "子任务已完成", Checkpoints(), lambda *a, **kw: None)


def test_short_ultra_report_keeps_native_workspace_capability(monkeypatch):
    seen = []
    monkeypatch.setattr(rw, "generate_workspace", lambda *args: seen.append(args) or {"sections": [], "briefs": []})
    config = {"runtime": "codex", "codex": {"reasoning_effort": "ultra"}, "limits": {"input_tokens": 100000}}
    gen.generate({"kind": "evening", "sources": [source(1)]}, config,
                 lambda *_: pytest.fail("Ultra should keep its tools"), Checkpoints())
    assert len(seen) == 1


@pytest.mark.parametrize("runtime", ["pi", "codex"])
def test_native_editor_continues_partial_turn_without_losing_valid_work(workspace, runtime):
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
    result = rw.generate_workspace(workspace.snapshot, {**workspace.config, "runtime": runtime, "codex": {}}, call, Checkpoints(), lambda *a, **kw: None)
    assert len(calls) == 2 and result["briefs"][0]["text"] == "完成跨日比较"


def test_pi_truncation_expands_budget_once_without_changing_model_or_effort(workspace):
    config = {**workspace.config, "reasoning_effort": "max", "limits": {"context_window": 1000000, "input_tokens": 834464, "output_tokens": 65536, "max_output_tokens": 384000}}
    seen = []
    def model(selected, prompt):
        seen.append(selected)
        w = selected["_workspace"]
        if len(seen) == 1:
            review_all(w)
            raise RuntimeError("report_output_truncated")
        assert w.status()["reviewed_count"] == 5
        plan = plan_all(w)
        w.invoke("report_draft", {"part_id": "main", "revision": plan["revision"], "briefs": [{"text": "完整报告", "refs": list(w.sources)}]})
        w.invoke("report_draft", {"part_id": "main"})
        w.invoke("report_finish", {"revision": plan["revision"], "audit": "已复核"})
        return "complete"
    rw.generate_workspace(workspace.snapshot, config, model, Checkpoints(), lambda *a, **kw: None)
    assert [c["limits"]["output_tokens"] for c in seen] == [65536, 131072]
    assert seen[1]["limits"]["input_tokens"] == 768928
    assert all(c["model"] == "fixture" and c["reasoning_effort"] == "max" for c in seen)


@pytest.mark.parametrize("runtime", ["pi", "codex"])
def test_failed_root_turn_requires_coordinator_acknowledgement_on_retry(workspace, runtime):
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
