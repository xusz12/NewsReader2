"""Editorial pipeline regressions; no real model, database, or credentials."""
import copy
import json

import pytest

import report_generation as gen


def source(number, title="关键进展", **extra):
    return {"ref": f"N{number:05}", "id": str(number), "title": title, "summary": "",
            "published_at": "2026-10-30T07:00:00+08:00", "source_name": "测试媒体",
            "body": "", "body_excerpt": False, "url": "", **extra}


def edition(refs):
    return {"sections": [{"title": "主要变化", "paragraphs": [
        {"text": "本期关键进展。", "emphasis": "", "refs": refs}]}], "briefs": [], "omitted": []}


class Checkpoints:
    def __init__(self): self.saved = {}
    def __call__(self, key, produce):
        if key not in self.saved: self.saved[key] = produce()
        return copy.deepcopy(self.saved[key])


@pytest.fixture
def budget(monkeypatch):
    # Predictable capacity boundaries; tokenizer accuracy is tested separately.
    monkeypatch.setattr(gen.report_model, "estimate_tokens", len)
    return {"limits": {"input_tokens": 100000}}


def test_title_only_edition_needs_one_call_and_duplicates_keep_references(budget):
    sources = [source(1, "消息" * 20000), source(2, "消息" * 20000), source(3)]
    calls = []
    def model(config, prompt):
        material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
        calls.append(material)
        return gen.encoded(edition([ref for item in material for ref in item["refs"]]))
    result = gen.generate({"kind": "evening", "sources": sources}, budget, model, Checkpoints())
    assert len(calls) == 1 and len(calls[0]) == 2
    assert {row["ref"] for row in result["sources"]} == {row["ref"] for row in sources}


def test_over_capacity_splits_but_65000_characters_do_not_force_another_merge(budget):
    # Raw input >100k; extracted candidates >65k but <100k. Exactly two
    # extraction calls and one final edit, without the former compression pass.
    sources = [source(i, str(i) + "消息" * 35000) for i in (1, 2)]
    calls = []
    def model(config, prompt):
        assert len(prompt) <= 100000
        material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
        if "本批仅做选材" in prompt:
            assert "候选仍超出" not in prompt
            calls.append("extract")
            return gen.encoded({"events": [{"text": "证据" * 9000, "refs": item["refs"]} for item in material for _ in range(2)], "omitted": []})
        calls.append("edition")
        return gen.encoded(edition([ref for item in material for ref in item["refs"]]))
    progress = []
    result = gen.generate({"kind": "monthly", "sources": sources}, budget, model, Checkpoints(),
                          progress=lambda stage, **details: progress.append((stage, details)))
    assert calls == ["extract", "extract", "edition"]
    assert len(result["sources"]) == 2
    assert [details["completed"] for stage, details in progress if stage == "selecting"] == [0, 1, 1, 2]
    assert all(details["total"] == 2 for stage, details in progress if stage == "selecting")
    assert [stage for stage, _ in progress][-2:] == ["writing", "validating"]


def test_oversized_single_item_is_never_silently_truncated(budget):
    with pytest.raises(RuntimeError, match="report_input_too_large"):
        gen.generate({"kind": "evening", "sources": [source(1, "x" * 110000)]}, budget,
                     lambda *_: pytest.fail("Cannot call model with oversized input"), Checkpoints())


def test_old_topic_and_total_character_caps_removed_and_briefs_are_cited():
    sources = [source(i) for i in range(1, 16)]
    value = edition(["N00001"])
    value["sections"] = [{"title": f"独立主题{i}", "paragraphs": [{"text": "有依据的事实。" * 60, "refs": [f"N{i:05}"]}]} for i in range(1, 15)]
    value["briefs"] = [{"text": "次重点新闻。", "refs": ["N00015"]}]
    result = gen.validate_edition(value, sources, "evening")["report"]
    assert len(result["sections"]) == 14 and result["reading_minutes"] > 10
    assert len(result["sources"]) == 15
    value["sections"] = []
    value["omitted"] = ["N00001-N00014"]
    result = gen.validate_edition(value, sources, "evening")["report"]
    assert result["reading_minutes"] == 1 and len(result["sources"]) == 1


@pytest.mark.parametrize("omitted", [[], ["N00002-N00004"], ["N00003-N00002"]])
def test_coverage_rejects_missing_unknown_and_reversed_ranges(omitted):
    value = edition(["N00001"])
    value["omitted"] = omitted
    with pytest.raises(gen.InvalidReport):
        gen.validate_edition(value, [source(i) for i in range(1, 4)], "evening")


def test_overlap_normalizes_without_inference_or_losing_retained_facts(budget):
    value = edition(["N00001"])
    value["omitted"] = ["N00001-N00003"]
    cache = Checkpoints()
    cache.saved["title-first-v1:v2:direct:raw"] = value
    result = gen.generate({"kind": "evening", "sources": [source(i) for i in range(1, 4)]}, budget,
                          lambda *_: pytest.fail("Overlap requires no inference"), cache)
    assert result["sources"][0]["ref"] == "N00001"
    assert cache.saved["title-first-v1:v2:direct:checked"]["omitted"] == ["N00002", "N00003"]
    event = {"text": "完整事实", "refs": ["N03373"]}
    assert gen.validate_events({"events": [event], "omitted": ["N03373", "N03374"]}, {"N03373", "N03374"}) == {"events": [event], "omitted": ["N03374"]}


def test_reference_spelling_is_normalized_but_unknown_ids_are_not_guessed():
    result = gen.validate_events({"events": [{"text": "事实", "refs": ["01554", "n01554"]}], "omitted": []}, {"N01554"})
    assert result["events"][0]["refs"] == ["N01554"]
    with pytest.raises(gen.InvalidReport) as error:
        gen.validate_events({"events": [{"text": "事实", "refs": ["N01554"]}, {"text": "未知来源", "refs": ["01555"]}], "omitted": []}, {"N01554"})
    assert error.value.issues[0]["path"] == "/events/1"


def test_explicit_omission_with_reason_is_not_mistaken_for_missing_coverage():
    result = gen.validate_events({"events": [], "omitted": [{"ref": "N00079", "reason": "无新增事实"}]}, {"N00079"})
    assert result == {"events": [], "omitted": ["N00079"]}
    with pytest.raises(gen.InvalidReport):
        gen.validate_events({"events": [], "omitted": [{"ref": "N00080", "reason": "不存在"}]}, {"N00079"})


def test_coverage_repair_reads_only_missing_material_and_can_retain_it(budget):
    cache = Checkpoints()
    cache.saved["title-first-v1:v2:direct:raw"] = edition(["N00001"])
    sources = [source(1, "已有充分资料" * 10000), source(2, "遗漏的重要反证")]
    seen = []
    def call(config, prompt):
        seen.append(prompt)
        assert "已有充分资料" not in prompt and "遗漏的重要反证" in prompt
        return gen.encoded({"events": [{"text": "遗漏的重要反证", "refs": ["N00002"]}], "omitted": []})
    result = gen.generate({"kind": "evening", "sources": sources}, budget, call, cache)
    assert len(seen) == 1 and result["briefs"][0]["refs"] == ["N00002"]


def test_bad_block_repaired_without_rewriting_good_block_or_repeating_draft(budget):
    value = edition(["N00001"])
    good = copy.deepcopy(value["sections"][0])
    value["sections"].append({"title": "需要修复的段落", "paragraphs": [{"text": "第二条新闻", "refs": ["invented"]}]})
    calls, cache = [], Checkpoints()
    def model(config, prompt):
        calls.append(prompt)
        if len(calls) == 1: return gen.encoded(value)
        assert "本次仅修复" in prompt and '"path":"/sections/1"' in prompt
        if len(calls) == 2: raise RuntimeError("report_model_timeout")
        return gen.encoded({"repairs": [{"path": "/sections/1", "value": {
            "title": "需要修复的段落", "paragraphs": [{"text": "第二条新闻", "refs": ["N00002"]}]}}]})
    snapshot = {"kind": "evening", "sources": [source(1), source(2, "第二条新闻")]}
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        gen.generate(snapshot, budget, model, cache)
    stages = []
    result = gen.generate(snapshot, budget, model, cache, progress=lambda stage, **_: stages.append(stage))
    assert len(calls) == 3 and result["sections"][0] == good
    assert stages == ["selecting", "validating", "repairing", "validating"]
    gen.generate(snapshot, budget, model, cache)
    assert len(calls) == 3


def test_repair_cannot_replace_unrequested_correct_content():
    with pytest.raises(RuntimeError, match="report_invalid_result"):
        gen.apply_repairs(edition(["N00001"]), {"repairs": [{"path": "/sections", "value": []}]}, {"/briefs"})


def test_format_repair_then_missing_coverage_resumes_without_repeating_good_work(budget):
    cache, calls = Checkpoints(), []
    good = {"text": "已核实的事实", "refs": ["N00001"]}
    cache.saved["batch:raw"] = {"events": [good], "omitted": ["N00002?", "N00003?"]}
    material = [{"text": text, "refs": [f"N{i:05}"]} for i, text in enumerate(
        ["已有充分资料", "无新增信息", "值得保留的反证"], 1)]
    def call(config, prompt):
        calls.append(prompt)
        assert "已有充分资料" not in prompt
        if len(calls) == 1:
            assert "本次仅修复" in prompt
            return gen.encoded({"repairs": [{"path": "/omitted", "value": ["N00002"]}]})
        assert "值得保留的反证" in prompt and "无新增信息" not in prompt
        if len(calls) == 2:
            raise RuntimeError("report_model_timeout")
        return gen.encoded({"events": [{"text": "值得保留的反证", "refs": ["N00003"]}], "omitted": []})
    def run():
        return gen.edited_call("batch", "规则\n新闻资料："+gen.encoded(material), material, "{}", budget,
                               call, cache, lambda value: gen.validate_events(value, {"N00001", "N00002", "N00003"}))
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        run()
    assert cache.saved["batch:repair"]["omitted"] == ["N00002"]
    result = run()
    assert result["events"][0] == good
    assert result["events"][1]["refs"] == ["N00003"] and result["omitted"] == ["N00002"]
    assert run() == result and len(calls) == 3


def test_ineffective_field_repairs_are_bounded(budget):
    cache, calls = Checkpoints(), []
    cache.saved["batch:raw"] = {"events": [], "omitted": ["N00001?"]}
    def call(config, prompt):
        calls.append(prompt)
        return gen.encoded({"repairs": [{"path": "/omitted", "value": ["N00001?"]}]})
    for attempt in range(2):
        with pytest.raises(gen.InvalidReport):
            gen.edited_call("batch", "规则\n新闻资料：[]", [{"refs": ["N00001"]}], "{}", budget,
                            call, cache, lambda value: gen.validate_events(value, {"N00001"}))
        assert len(calls) == 3 * (attempt+1)
    assert list(cache.saved) == ["batch:raw"]


def test_invalid_json_repairs_only_saved_response_and_can_resume(budget):
    cache, calls = Checkpoints(), []
    raw = '{"sections":[],"briefs":[{"text":"待保留事实","refs":["N00001"]}],"omitted":[],}'
    def model(config, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            assert "完整原始新闻" in prompt
            return raw
        assert "完整原始新闻" not in prompt and "待保留事实" in prompt
        if len(calls) == 2:
            raise RuntimeError("report_model_timeout")
        return raw.replace(',}', '}')
    snapshot = {"kind": "evening", "sources": [source(1, "完整原始新闻")]}
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        gen.generate(snapshot, budget, model, cache)
    assert cache.saved["title-first-v1:v2:direct:answer"] == raw
    result = gen.generate(snapshot, budget, model, cache)
    assert result["briefs"][0]["text"] == "待保留事实" and len(calls) == 3
    gen.generate(snapshot, budget, model, cache)
    assert len(calls) == 3


@pytest.mark.parametrize("fence", ["```", "```JSON", "```json"])
def test_unambiguous_json_fence_is_removed_locally(fence):
    assert gen.parse_json(fence+'\n{"events":[],"omitted":[]}\n```') == {"events": [], "omitted": []}


def test_empty_period_does_not_need_model_or_capacity():
    result = gen.generate({"kind": "evening", "sources": []}, {}, lambda *_: pytest.fail("No inference"), Checkpoints())
    assert result["reading_minutes"] == 0 and result["briefs"] == []


def test_reports_do_not_require_or_preserve_an_overview(budget):
    def model(config, prompt):
        assert '"overview"' not in prompt
        return gen.encoded(edition(["N00001"]))
    result = gen.generate({"kind": "evening", "sources": [source(1)]}, budget, model, Checkpoints())
    assert "overview" not in result
    old = edition(["N00001"])
    old["overview"] = [{"text": "旧导读" * 500, "refs": ["not-a-source"]}]
    assert gen.validate_edition(old, [source(1)], "evening")["report"] == result
    # An overview cannot satisfy the required coverage of news materials.
    old["overview"] = [{"text": "旧导读", "refs": ["N00002"]}]
    with pytest.raises(gen.InvalidReport):
        gen.validate_edition(old, [source(1), source(2)], "evening")


@pytest.mark.parametrize("kind", ["morning", "noon", "evening", "weekly", "monthly", "custom"])
def test_selection_never_sees_details_and_writing_only_sees_selected_news(budget, kind):
    sources = [source(1, "标题入选，只有标题"),
               source(2, "标题入选，已有正文", summary="入选摘要", body="入选正文细节", body_excerpt=True),
               source(3, "标题价值不足", summary="不应泄漏的摘要", body="不应泄漏的正文" * 20000)]
    prompts, cache = [], Checkpoints()
    def model(config, prompt):
        material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
        prompts.append(prompt)
        if len(prompts) == 1:
            assert all(set(s) == {"title", "source_name", "published_at", "refs"} for s in material)
            assert all(s["summary"] not in prompt and s["body"] not in prompt for s in sources if s["body"])
            return gen.encoded({**edition(["N00001", "N00002"]), "omitted": ["N00003"]})
        assert {s["ref"] for s in material} == {"N00001", "N00002"}
        assert "入选正文细节" in prompt and "入选摘要" in prompt
        assert "不应泄漏" not in prompt and "标题价值不足" not in prompt
        assert material[1]["body_excerpt"] is True
        value = edition(["N00001", "N00002"])
        value["sections"][0]["paragraphs"][0]["text"] = "标题事实及入选正文细节。"
        return gen.encoded(value)
    snapshot = {"kind": kind, "sources": sources}
    result = gen.generate(snapshot, budget, model, cache)
    assert len(prompts) == 2
    assert {s["ref"] for s in result["sources"]} == {"N00001", "N00002"}
    assert "入选正文细节" in result["sections"][0]["paragraphs"][0]["text"]
    assert all("body" not in s and "summary" not in s for s in result["sources"])
    assert gen.generate(snapshot, budget, model, cache) == result
    assert len(prompts) == 2


def test_body_changes_do_not_change_title_deduplication_or_selection_prompt(budget):
    prompts = []
    for variant in (0, 1):
        sources = [source(1, "相同标题", body="不同正文" * 50000 if variant else ""),
                   source(2, "相同标题", summary="不同摘要" if variant else "")]
        def model(config, prompt):
            prompts.append(prompt)
            return gen.encoded({"sections": [], "briefs": [], "omitted": ["N00001-N00002"]})
        result = gen.generate({"kind": "evening", "sources": sources}, budget, model, Checkpoints())
        assert result["sources"] == []
    assert prompts[0] == prompts[1]
    assert json.loads(prompts[0].rsplit("\n新闻资料：", 1)[1])[0]["refs"] == ["N00001", "N00002"]


def test_writing_cannot_drop_title_only_news_and_repairs_only_missing_citation(budget):
    sources = [source(1, "仅标题的重要进展"), source(2, "有正文的进展", body="完整正文")]
    cache, calls = Checkpoints(), []
    cache.saved["title-first-v1:v2:direct:raw"] = edition(["N00001", "N00002"])
    def model(config, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return gen.encoded(edition(["N00002"]))
        assert "本次仅修复" in prompt and '"path":"/briefs"' in prompt
        material = json.loads(prompt.rsplit("\n新闻资料：", 1)[1])
        assert [s["ref"] for s in material] == ["N00001"]
        return gen.encoded({"repairs": [{"path": "/briefs", "value": [{"text": "仅标题的重要进展", "refs": ["N00001"]}]}]})
    result = gen.generate({"kind": "evening", "sources": sources}, budget, model, cache)
    assert result["briefs"][0]["refs"] == ["N00001"] and len(calls) == 2
    with pytest.raises(gen.InvalidReport):
        gen.validate_selected_report(edition(["N00001", "N00002", "N00003"]), sources, "evening")


def test_writing_retry_reuses_title_choices_and_ignores_legacy_selection(budget):
    cache, calls = Checkpoints(), []
    cache.saved["v2:direct:checked"] = {"report": gen.validate_report(edition(["N00002"]), [source(2)], "evening"), "omitted": ["N00001"]}
    snapshot = {"kind": "evening", "sources": [source(1, "标题决定入选", body="入选细节"), source(2, "未入选", body="诱人的完整正文")]}
    def model(config, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            assert "诱人的完整正文" not in prompt
            return gen.encoded({**edition(["N00001"]), "omitted": ["N00002"]})
        assert "未入选\"" not in prompt and "诱人的完整正文" not in prompt
        if len(calls) == 2:
            raise RuntimeError("report_model_timeout")
        return gen.encoded(edition(["N00001"]))
    with pytest.raises(RuntimeError, match="report_model_timeout"):
        gen.generate(snapshot, budget, model, cache)
    result = gen.generate(snapshot, budget, model, cache)
    assert [s["ref"] for s in result["sources"]] == ["N00001"] and len(calls) == 3


def test_selected_bodies_over_capacity_keep_title_choices_in_paged_workspace(budget):
    snapshot = {"kind": "evening", "sources": [source(1, "仅标题新闻"), source(2, "入选正文新闻", body="完整细节" * 28000),
                                               source(3, "未选正文新闻", body="不可见细节")]}
    config = {**budget, "runtime": "codex", "codex": {"reasoning_effort": "high"}}
    calls = []
    def model(config, prompt):
        calls.append(prompt)
        workspace = config.get("_workspace")
        if workspace is None:
            assert "完整细节" not in prompt and "不可见细节" not in prompt
            return gen.encoded({**edition(["N00001", "N00002"]), "omitted": ["N00003"]})
        assert workspace.selected_refs() == {"N00001", "N00002"}
        assert workspace.status()["selection_locked"]
        hidden = workspace.invoke("report_source", {"ref": "N00003"})
        assert "不可见细节" not in hidden["json_text"]
        text, offset = "", 0
        while offset is not None:
            page = workspace.invoke("report_source", {"ref": "N00002", "offset": offset})
            text += page["json_text"]
            offset = page["next_offset"]
        assert json.loads(text)["body"] == snapshot["sources"][1]["body"]
        plan = workspace.state["plan"]
        assert "error" not in workspace.invoke("report_draft", {"part_id": "part1", "revision": plan["revision"],
                                                               "briefs": [{"text": "两条已入选新闻的事实", "refs": ["N00001", "N00002"]}]})
        workspace.invoke("report_draft", {"part_id": "part1"})
        assert workspace.invoke("report_finish", {"revision": plan["revision"], "audit": "已回读全部正文及草稿"})["finished"]
        return "完成"
    result = gen.generate(snapshot, config, model, Checkpoints())
    assert len(calls) == 2 and {s["ref"] for s in result["sources"]} == {"N00001", "N00002"}
