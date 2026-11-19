from __future__ import annotations

import json
import re
import sqlite3


TRACKED_TOPIC_SCOPES = {"important", "all"}


TRACKED_BACKFILL_MODES = {"recent_important", "all_important", "all_news"}


TRACKED_MATCH_METHODS = {"keyword", "manual"}


TRACKED_RULE_FIELD_SCORES = {
    "title": {"strong": 8, "core": 4, "context": 2},
    "note": {"strong": 6, "core": 3, "context": 2},
    "summary": {"strong": 5, "core": 2, "context": 1},
    "content": {"strong": 2, "core": 1, "context": 1},
}


TRACKED_RULE_FIELD_WEIGHT_DEFAULTS = {
    "title": 1.0,
    "note": 1.0,
    "summary": 1.0,
    "content": 1.0,
}


TRACKED_RULE_TERM_SCORE_DEFAULTS = {
    "strong": 1.0,
    "core": 1.0,
    "context": 1.0,
    "exclude": 1.0,
}


TRACKED_DEFAULT_RULE_PARAM_KEYS = (
    "title_weight",
    "note_weight",
    "summary_weight",
    "content_weight",
    "strong_score",
    "core_score",
    "context_score",
    "exclude_penalty",
    "threshold",
)


TRACKED_RULE_FIELD_LABELS = {
    "title": "标题",
    "note": "笔记",
    "summary": "摘要",
    "content": "正文",
}


TRACKED_RULE_THRESHOLD_DEFAULT = 6


TRACKED_RULE_THRESHOLD_MIN = 1


TRACKED_RULE_THRESHOLD_MAX = 50


TRACKED_RULE_CANDIDATE_THRESHOLD_DEFAULT = 4


TRACKED_RULE_WEIGHT_MIN = 0


TRACKED_RULE_WEIGHT_MAX = 20


TRACKED_RULE_EXCLUDE_PENALTY_BASE = 100


def normalize_keyword_list(value: object) -> list[str]:
    raw_values: list[str] = []
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str):
                raw_values.append(entry)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    for entry in parsed:
                        if isinstance(entry, str):
                            raw_values.append(entry)
                else:
                    raw_values.extend(re.split(r"[\n,，]+", value))
            except Exception:
                raw_values.extend(re.split(r"[\n,，]+", value))
        else:
            raw_values.extend(re.split(r"[\n,，]+", value))

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = " ".join((raw or "").split()).strip()
        lowered = text.lower()
        if not text or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


def tracked_scope_value(value: object) -> str:
    scope = str(value or "important").strip().lower()
    if scope not in TRACKED_TOPIC_SCOPES:
        raise ValueError("invalid_scope")
    return scope


def tracked_backfill_mode_value(value: object) -> str:
    mode = str(value or "recent_important").strip().lower()
    if mode not in TRACKED_BACKFILL_MODES:
        raise ValueError("invalid_backfill_mode")
    return mode


def parse_tracked_threshold(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(str(value).strip())
    except Exception as exc:
        raise ValueError("invalid_tracked_threshold") from exc
    if parsed < TRACKED_RULE_THRESHOLD_MIN or parsed > TRACKED_RULE_THRESHOLD_MAX:
        raise ValueError("invalid_tracked_threshold")
    return parsed


def parse_tracked_weight(value: object, *, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(str(value).strip())
    except Exception as exc:
        raise ValueError("invalid_tracked_weight") from exc
    if parsed < TRACKED_RULE_WEIGHT_MIN or parsed > TRACKED_RULE_WEIGHT_MAX:
        raise ValueError("invalid_tracked_weight")
    return parsed


def parse_key_points_text(value: object) -> str:
    key_points = value or ""
    if isinstance(key_points, str):
        try:
            parsed = json.loads(key_points)
            if isinstance(parsed, list):
                return " ".join(str(part) for part in parsed if part)
        except Exception:
            return key_points
    if isinstance(key_points, list):
        return " ".join(str(part) for part in key_points if part)
    return str(key_points or "")


def tracked_default_rules(
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    default_rule_params: dict | None = None,
) -> dict:
    base_params = tracked_default_rule_params(default_rule_params)
    return {
        "strong_phrases": [],
        "core_terms": list(include_keywords or []),
        "context_terms": [],
        "exclude_terms": list(exclude_keywords or []),
        "required_terms": [],
        "threshold": base_params["threshold"],
        "candidate_threshold": TRACKED_RULE_CANDIDATE_THRESHOLD_DEFAULT,
        "title_weight": base_params["title_weight"],
        "note_weight": base_params["note_weight"],
        "summary_weight": base_params["summary_weight"],
        "content_weight": base_params["content_weight"],
        "strong_score": base_params["strong_score"],
        "core_score": base_params["core_score"],
        "context_score": base_params["context_score"],
        "exclude_penalty": base_params["exclude_penalty"],
    }


def tracked_default_rule_params(raw_params: object = None) -> dict:
    params = raw_params if isinstance(raw_params, dict) else {}
    return {
        "title_weight": parse_tracked_weight(
            params.get("title_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["title"],
        ),
        "note_weight": parse_tracked_weight(
            params.get("note_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["note"],
        ),
        "summary_weight": parse_tracked_weight(
            params.get("summary_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["summary"],
        ),
        "content_weight": parse_tracked_weight(
            params.get("content_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["content"],
        ),
        "strong_score": parse_tracked_weight(
            params.get("strong_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["strong"],
        ),
        "core_score": parse_tracked_weight(
            params.get("core_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["core"],
        ),
        "context_score": parse_tracked_weight(
            params.get("context_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["context"],
        ),
        "exclude_penalty": parse_tracked_weight(
            params.get("exclude_penalty"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["exclude"],
        ),
        "threshold": parse_tracked_threshold(
            params.get("threshold"),
            default=TRACKED_RULE_THRESHOLD_DEFAULT,
        ),
    }


def tracked_default_rule_params_from_rules(raw_rules: object) -> dict:
    rules = normalize_tracked_rules(raw_rules)
    return {key: rules[key] for key in TRACKED_DEFAULT_RULE_PARAM_KEYS}


def normalize_tracked_rules(raw_rules: object, *, fallback_keywords: list[str] | None = None, fallback_excludes: list[str] | None = None) -> dict:
    rules = raw_rules if isinstance(raw_rules, dict) else {}
    strong_phrases = normalize_keyword_list(rules.get("strong_phrases"))
    core_terms = normalize_keyword_list(rules.get("core_terms"))
    context_terms = normalize_keyword_list(rules.get("context_terms"))
    exclude_terms = normalize_keyword_list(rules.get("exclude_terms"))
    required_terms = normalize_keyword_list(rules.get("required_terms"))

    if not strong_phrases and not core_terms and fallback_keywords:
        core_terms = list(fallback_keywords)
    if not exclude_terms and fallback_excludes:
        exclude_terms = list(fallback_excludes)

    return {
        "strong_phrases": strong_phrases,
        "core_terms": core_terms,
        "context_terms": context_terms,
        "exclude_terms": exclude_terms,
        "required_terms": required_terms,
        "threshold": parse_tracked_threshold(rules.get("threshold"), default=TRACKED_RULE_THRESHOLD_DEFAULT),
        "candidate_threshold": parse_tracked_threshold(
            rules.get("candidate_threshold"),
            default=TRACKED_RULE_CANDIDATE_THRESHOLD_DEFAULT,
        ),
        "title_weight": parse_tracked_weight(
            rules.get("title_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["title"],
        ),
        "note_weight": parse_tracked_weight(
            rules.get("note_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["note"],
        ),
        "summary_weight": parse_tracked_weight(
            rules.get("summary_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["summary"],
        ),
        "content_weight": parse_tracked_weight(
            rules.get("content_weight"),
            default=TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["content"],
        ),
        "strong_score": parse_tracked_weight(
            rules.get("strong_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["strong"],
        ),
        "core_score": parse_tracked_weight(
            rules.get("core_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["core"],
        ),
        "context_score": parse_tracked_weight(
            rules.get("context_score"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["context"],
        ),
        "exclude_penalty": parse_tracked_weight(
            rules.get("exclude_penalty"),
            default=TRACKED_RULE_TERM_SCORE_DEFAULTS["exclude"],
        ),
    }


def tracked_rules_from_topic(topic: sqlite3.Row | dict) -> dict:
    payload = dict(topic)
    keywords = normalize_keyword_list(payload.get("keywords_json") or "[]")
    excludes = normalize_keyword_list(payload.get("exclude_keywords_json") or "[]")
    raw_rules = payload.get("rules_json") or ""
    parsed_rules: object = {}
    if isinstance(raw_rules, str) and raw_rules.strip():
        try:
            parsed_rules = json.loads(raw_rules)
        except Exception:
            parsed_rules = {}
    elif isinstance(raw_rules, dict):
        parsed_rules = raw_rules
    return normalize_tracked_rules(parsed_rules, fallback_keywords=keywords, fallback_excludes=excludes)


def tracked_text_fields(row: sqlite3.Row | dict) -> dict[str, str]:
    payload = dict(row)
    summary_parts = [
        payload.get("summary") or "",
        parse_key_points_text(payload.get("key_points_zh")),
        payload.get("conclusion_zh") or "",
    ]
    return {
        "title": str(payload.get("title") or ""),
        "note": str(payload.get("note") or ""),
        "summary": "\n".join(part for part in summary_parts if part),
        "content": str(payload.get("body_zh") or ""),
    }


def format_tracked_score(score: float) -> str:
    return f"{score:.2f}".rstrip("0").rstrip(".")


def compact_tracked_reason(
    evidence: dict[str, dict[str, list[str]]] | None,
    score: float,
    *,
    required_hits: list[str] | None = None,
) -> str:
    if not evidence:
        return ""
    segments: list[str] = []
    if required_hits:
        segments.append(f"必要词命中：{'/'.join(required_hits[:4])}")
    for field_key in ("title", "note", "summary", "content"):
        field_hits = evidence.get(field_key) or {}
        terms: list[str] = []
        if field_hits.get("strong"):
            terms.extend(field_hits["strong"])
        if field_hits.get("core"):
            terms.extend(field_hits["core"])
        if field_hits.get("context"):
            terms.extend(field_hits["context"])
        if not terms:
            continue
        deduped_terms: list[str] = []
        seen: set[str] = set()
        for term in terms:
            lowered = term.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped_terms.append(term)
        segments.append(f"{TRACKED_RULE_FIELD_LABELS[field_key]}命中：{'/'.join(deduped_terms[:4])}")
    reason = "；".join(segments[:3])
    if reason:
        reason = f"{reason}；score={format_tracked_score(score)}"
    return reason[:200]


def match_tracked_topic_row(row: sqlite3.Row | dict, rules: dict) -> tuple[bool, float, str]:
    strong_phrases = rules.get("strong_phrases") or []
    core_terms = rules.get("core_terms") or []
    context_terms = rules.get("context_terms") or []
    exclude_terms = rules.get("exclude_terms") or []
    required_terms = rules.get("required_terms") or []
    threshold = int(rules.get("threshold") or TRACKED_RULE_THRESHOLD_DEFAULT)
    if not strong_phrases and not core_terms:
        return False, 0.0, ""
    field_weights = {
        "title": float(rules.get("title_weight", TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["title"])),
        "note": float(rules.get("note_weight", TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["note"])),
        "summary": float(rules.get("summary_weight", TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["summary"])),
        "content": float(rules.get("content_weight", TRACKED_RULE_FIELD_WEIGHT_DEFAULTS["content"])),
    }
    term_scores = {
        "strong": float(rules.get("strong_score", TRACKED_RULE_TERM_SCORE_DEFAULTS["strong"])),
        "core": float(rules.get("core_score", TRACKED_RULE_TERM_SCORE_DEFAULTS["core"])),
        "context": float(rules.get("context_score", TRACKED_RULE_TERM_SCORE_DEFAULTS["context"])),
    }

    text_fields = tracked_text_fields(row)
    normalized_fields = {
        key: value.lower().strip()
        for key, value in text_fields.items()
        if str(value or "").strip()
    }
    if not normalized_fields:
        return False, 0.0, ""

    exclude_hits: list[str] = []
    for field_text in normalized_fields.values():
        for term in exclude_terms:
            lowered = term.lower()
            if lowered and lowered in field_text:
                exclude_hits.append(term)
    if exclude_hits:
        return False, 0.0, ""

    required_hits: list[str] = []
    if required_terms:
        seen_required: set[str] = set()
        for field_text in normalized_fields.values():
            for term in required_terms:
                lowered = term.lower()
                if lowered and lowered in field_text and lowered not in seen_required:
                    seen_required.add(lowered)
                    required_hits.append(term)
        if not required_hits:
            return False, 0.0, ""

    score = 0.0
    evidence: dict[str, dict[str, list[str]]] = {}
    strong_hit = False
    non_body_signal = False
    core_hit = False
    context_hit = False

    for field_key, field_text in normalized_fields.items():
        field_scores = TRACKED_RULE_FIELD_SCORES[field_key]
        field_weight = field_weights[field_key]
        strong_hits = [term for term in strong_phrases if term.lower() in field_text]
        core_hits = [term for term in core_terms if term.lower() in field_text]
        context_hits = [term for term in context_terms if term.lower() in field_text]
        if not strong_hits and not core_hits and not context_hits:
            continue
        field_evidence: dict[str, list[str]] = {}
        if strong_hits:
            score += field_scores["strong"] * term_scores["strong"] * field_weight * len(strong_hits)
            field_evidence["strong"] = strong_hits
            strong_hit = True
        if core_hits:
            score += field_scores["core"] * term_scores["core"] * field_weight * len(core_hits)
            field_evidence["core"] = core_hits
            core_hit = True
        if context_hits:
            score += field_scores["context"] * term_scores["context"] * field_weight * len(context_hits)
            field_evidence["context"] = context_hits
            context_hit = True
        evidence[field_key] = field_evidence
        if field_key != "content":
            non_body_signal = True

    if not evidence:
        return False, 0.0, ""
    if not non_body_signal:
        return False, 0, ""
    if score < threshold:
        return False, score, ""

    if strong_hit:
        return True, score, compact_tracked_reason(evidence, score, required_hits=required_hits)
    if core_hit and context_hit:
        return True, score, compact_tracked_reason(evidence, score, required_hits=required_hits)
    return False, score, ""
