"""Pure, deterministic unread selection. No DB/state writes or model calls."""

from datetime import date
import re

DEFAULT_LIMITS = {"max_days": 20, "max_items": 6000}


def limits(value=None):
    if value is None:
        return dict(DEFAULT_LIMITS)
    if not isinstance(value, dict):
        raise ValueError("invalid_summary_limits")
    result = {k: value.get(k, v) for k, v in DEFAULT_LIMITS.items()}
    if any(
        type(v) is not int or not 1 <= v <= 9007199254740991 for v in result.values()
    ):
        raise ValueError("invalid_summary_limits")
    return result


def news_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def window(items, options, today=None):
    today = today or date.today()
    anchor = today
    start = date.fromordinal(max(1, anchor.toordinal() - options["max_days"] + 1))
    dates = [news_date(i.get("date")) for i in items]
    dates = [d for d in dates if d]
    fallback = bool(dates) and not any(
        start.isoformat() <= d <= anchor.isoformat() for d in dates
    )
    if fallback:
        anchor = date.fromisoformat(max(dates))
        start = date.fromordinal(max(1, anchor.toordinal() - options["max_days"] + 1))
    return start.isoformat(), anchor.isoformat(), fallback


def clean_snapshot(snapshot, options=None, fits=lambda s: True, today=None):
    """Keep newest dated rows first, undated last, shrink oldest whole rows.

    fits receives the complete proposed model snapshot (including preferences).
    Returned all_items/all_ids are immutable locked scope; items/summary_ids are
    model scope. Every excluded row belongs to exactly one disclosed reason.
    """
    options = limits(options)
    rows = []
    for raw in snapshot["items"]:
        if raw.get("read_at") is not None:
            continue
        i = dict(raw)
        i["source_date"] = i.get("source_date", i.get("date"))
        i["date"] = news_date(i.get("date"))
        rows.append(i)
    if not rows:
        raise ValueError("summary_empty")
    if len({str(i["id"]) for i in rows}) != len(rows):
        raise ValueError("summary_validation_failed")
    rows.sort(key=lambda i: (not bool(i["date"]), i["date"], str(i["id"])))
    for n, i in enumerate(rows, 1):
        i.setdefault("ref", f"N{n:04d}")
    start, end, fallback = window(rows, options, today)
    dated = [i for i in rows if i["date"] and start <= i["date"] <= end]
    undated = [i for i in rows if not i["date"]]
    date_excluded = [i for i in rows if i["date"] and not start <= i["date"] <= end]
    ranked = sorted(dated, key=lambda i: (i["date"], str(i["id"])), reverse=True)
    ranked += sorted(undated, key=lambda i: str(i["id"]), reverse=True)
    candidates = ranked[: options["max_items"]]
    count_excluded = ranked[len(candidates) :]

    def proposed(n):
        items = sorted(
            candidates[:n], key=lambda i: (not bool(i["date"]), i["date"], str(i["id"]))
        )
        ds = [i["date"] for i in items if i["date"]]
        return {
            **snapshot,
            "items": items,
            "start": min(ds) if ds else "日期不明",
            "end": max(ds) if ds else "日期不明",
        }

    # Encoded prompts grow monotonically as whole records are added; no single
    # title/summary/body truncation, deduplication or semantic filtering.
    low, high = 0, len(candidates)
    while low < high:
        n = (low + high + 1) // 2
        if fits(proposed(n)):
            low = n
        else:
            high = n - 1
    if not low:
        raise ValueError("summary_input_limit")
    result = proposed(low)
    size_excluded = candidates[low:]
    reasons = {"date": date_excluded, "count": count_excluded, "size": size_excluded}
    result.update(
        all_items=rows,
        all_ids=[i["id"] for i in rows],
        summary_ids=[i["id"] for i in result["items"]],
    )
    result["scope"] = {
        "limits": options,
        "window_start": start,
        "window_end": end,
        "fallback_window": fallback,
        "locked_count": len(rows),
        "summarized_count": low,
        "excluded_count": len(rows) - low,
        "excluded_by": {k: len(v) for k, v in reasons.items()},
        "excluded_ids": {k: [i["id"] for i in v] for k, v in reasons.items()},
        "undated_count": len(undated),
        "undated_summarized": sum(not i["date"] for i in result["items"]),
    }
    return result
