"""Frozen monthly inclusion signals; evidence bodies never enter selection context."""
from __future__ import annotations

VERSION = "monthly-important-v1"

RULES = """
月报补充规则（其余选稿偏好和编写要求不变）：
monthly_role=important 是取材时已标为重要的当月新闻，必须全部引用到原有主题正文或简讯，不能归入 omitted，也不能在归并或章节计划中舍弃。
其他当月新闻继续按原标准选稿；围绕同一事件串联月内进展、转折、纠正和相反证据，合并进原有主题，不因仅属同一公司或行业就强行关联。
monthly_role=background 是月前发布的历史重要新闻，只在与入选的当月事件直接相关且能补充理解时使用，不必全部收录。无关背景明确舍弃；不得将历史新闻单独作为当月进展、独立主题或独立简讯。
归并后的候选事件以 important_refs 标出必收编号、background_refs 标出历史背景编号，含义与原始 monthly_role 相同；这些约束在跨批次归并和最终章节计划中仍然有效。
每个引用历史背景的段落或简讯必须同时引用其关联的当月新闻，并在文字中明确历史时间或先前背景，不能把历史日期、计划或预期改写为当月事实。
这些角色仅是用户标记和时间范围，不是新闻内容证据。选稿仍只看标题、来源、发布时间；确认入选后才读相应摘要和正文，不能以正文丰富程度改选。
"""


def combine(snapshot, supplement):
    important = set(supplement["important_ids"])
    return {**snapshot, "monthly": {"version": supplement["version"], "captured_at": supplement["captured_at"]},
            "sources": [{**source, **({"monthly_role": "important"} if source["id"] in important else {})}
                        for source in snapshot["sources"]] + supplement["sources"]}


def required_refs(sources, kind):
    return {s["ref"] for s in sources if kind == "monthly" and s.get("monthly_role") == "important"}


def background_refs(sources, kind):
    return {s["ref"] for s in sources if kind == "monthly" and s.get("monthly_role") == "background"}


def event_signals(event, required, background):
    refs = set(event["refs"])
    return {**event, **({"important_refs": sorted(refs & required)} if refs & required else {}),
            **({"background_refs": sorted(refs & background)} if refs & background else {})}
