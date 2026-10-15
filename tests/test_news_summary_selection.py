"""Isolated deterministic selection boundaries; never uses real news or a model."""
from datetime import date
import pytest
import news_summary_selection as sel
import news_summaries as ns

TODAY = date(2026,10,13)
def snap(rows):
    return {"items":rows,"preferences":[],"start":"", "end":""}
def row(i, d="2026-10-13", **kw):
    return {"id":str(i),"date":d,"title":"合成新闻"+str(i),"summary":"","body":"","material":"仅标题","source":"test","source_name":"合成","source_type":"site","url":"",**kw}
def clean(rows, **kw):
    return sel.clean_snapshot(snap(rows), today=TODAY, **kw)

@pytest.mark.parametrize("bad",[0,-1,True,1.5,"20",None])
def test_positive_integer_limits(bad):
    with pytest.raises(ValueError,match="invalid_summary_limits"):
        sel.limits({"max_days":bad})
    with pytest.raises(ValueError,match="invalid_summary_limits"):
        sel.limits({"max_items":bad})

def test_defaults_and_partial():
    assert sel.limits()=={"max_days":20,"max_items":6000}
    assert sel.limits({"max_days":2})["max_items"]==6000

def test_under_limits_unread_only_inclusive_twenty():
    s=clean([row(1,"2026-09-23"),row(2,"2026-09-24"),row(3),row(4,read_at="already")])
    assert s["summary_ids"]==["2","3"]
    assert s["all_ids"]==["1","2","3"]
    assert s["scope"]["window_start"]=="2026-09-24"
    assert s["scope"]["excluded_by"]=={"date":1,"count":0,"size":0}

def test_under_limits_keeps_everything():
    s=clean([row(1),row(2),row(3)])
    assert len(s["items"])==3 and s["scope"]["excluded_count"]==0

def test_six_thousand_boundary_stable():
    rows=[row(f"{i:05d}") for i in range(6001)]
    a=clean(rows)
    b=clean(list(reversed(rows)))
    assert a==b
    assert len(a["summary_ids"])==6000 and a["scope"]["excluded_by"]["count"]==1
    assert a["scope"]["excluded_ids"]["count"]==["00000"]

def test_old_only_anchor_latest():
    s=clean([row(1,"2025-12-01"),row(2,"2026-01-01"),row(3,"2026-01-20")])
    assert s["scope"]["fallback_window"]
    assert s["scope"]["window_start"]=="2026-01-01"
    assert s["start"]=="2026-01-01" and s["end"]=="2026-01-20"

def test_missing_invalid_lower_priority():
    s=clean([row(1,""),row(2,"2026-02-30"),row(3,None),row(4)],options={"max_items":2})
    assert s["summary_ids"]==["4","3"]
    assert s["scope"]["undated_count"]==3 and s["scope"]["undated_summarized"]==1
    assert s["items"][1]["date"]=="" and s["items"][1]["source_date"] is None

def test_all_unknown_never_invents_dates():
    s=clean([row(1,None),row(2,"bad")])
    assert s["start"]==s["end"]=="日期不明" and not s["scope"]["fallback_window"]

def test_shrink_whole_oldest_records():
    s=clean([row(1,"2026-10-11",body="X"*50),row(2,"2026-10-12"),row(3)],fits=lambda s:len(s["items"])<=2)
    assert s["summary_ids"]==["2","3"] and s["all_items"][0]["body"]=="X"*50
    assert s["scope"]["excluded_ids"]["size"]==["1"]

def test_no_single_fits_or_empty():
    with pytest.raises(ValueError,match="summary_input_limit"):
        clean([row(1)],fits=lambda s:False)
    with pytest.raises(ValueError,match="summary_empty"):
        clean([row(1,read_at="read")])

@pytest.mark.parametrize("runtime",["codex","pi"])
def test_actual_encoding_without_review_reserve(runtime):
    config={"runtime":runtime,"limits":sel.limits(),"pi_provider":"synthetic","pi_model":"synthetic"}
    s=snap([row(1,summary="文"*100000),row(2,summary="文"*100000),row(3,summary="文"*100000)])
    r=ns.clean_for_runtime(s,config,TODAY)
    budget=r["scope"]["input_budget"]
    assert budget["generation_bytes"]<=ns.MAX_INPUT_BYTES-4096
    assert all(i["summary"]=="文"*100000 for i in r["items"])
    assert set(budget)=={"generation_bytes","transport_limit_bytes"}
    assert budget["generation_bytes"]==ns.encoded_input_size(ns.draft_prompt(r),config)

def test_huge_latest_not_silently_skip_or_truncate():
    config={"runtime":"codex","limits":sel.limits()}
    with pytest.raises(ValueError,match="summary_input_limit"):
        ns.clean_for_runtime(snap([row(1,"2026-10-12"),row(2,body="文"*800000)]),config,TODAY)

def test_unknown_dates_require_no_machine_report():
    s=clean([row(1,None)])
    ref=s["items"][0]["ref"]
    body="报道日期不明，合成公司公布结果。[["+ref+"]]"
    assert ns.checked_body(body,s)[0]==body
    assert not hasattr(ns,"audit_prompt") and not hasattr(ns,"audited")

@pytest.mark.parametrize("runtime",["codex","pi"])
def test_single_call_budget_uses_capacity_previously_reserved_for_review(runtime):
    config={"runtime":runtime,"limits":sel.limits(),"pi_provider":"synthetic","pi_model":"synthetic"}
    # ~1.6MiB input would not fit with the old review/draft reserve.
    rows=[row(1,summary="文"*(530000 if runtime=="codex" else 265000))]
    result=ns.clean_for_runtime(snap(rows),config,TODAY)
    assert len(result["items"])==1
    assert 1500000<result["scope"]["input_budget"]["generation_bytes"]<ns.MAX_INPUT_BYTES-4096
