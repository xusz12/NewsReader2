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
    return {"overview": [], "sections": [{"title": "主要变化", "paragraphs": [
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


def test_large_whole_edition_needs_one_call_and_duplicates_keep_references(budget):
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


@pytest.mark.parametrize("omitted", [[], ["N00001-N00003"], ["N00002-N00004"], ["N00003-N00002"]])
def test_coverage_rejects_missing_overlapping_unknown_and_reversed_ranges(omitted):
    value = edition(["N00001"])
    value["omitted"] = omitted
    with pytest.raises(gen.InvalidReport):
        gen.validate_edition(value, [source(i) for i in range(1, 4)], "evening")


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
    assert stages == ["writing", "validating", "repairing", "validating"]
    gen.generate(snapshot, budget, model, cache)
    assert len(calls) == 3


def test_repair_cannot_replace_unrequested_correct_content():
    with pytest.raises(RuntimeError, match="report_invalid_result"):
        gen.apply_repairs(edition(["N00001"]), {"repairs": [{"path": "/sections", "value": []}]}, {"/briefs"})


def test_empty_period_does_not_need_model_or_capacity():
    result = gen.generate({"kind": "evening", "sources": []}, {}, lambda *_: pytest.fail("No inference"), Checkpoints())
    assert result["reading_minutes"] == 0 and result["briefs"] == []
