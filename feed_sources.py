"""Shared source identity for settings, deletion and every ingestion entry point."""
from __future__ import annotations

import re
from urllib.parse import urlparse

SOURCE_LABELS = {
    "all": "全部来源",
    "reuters": "Reuters",
    "bloomberg": "Bloomberg",
    "techcrunch": "TechCrunch",
    "ars": "Ars Technica",
    "x": "X",
}

def _source_prefix(source: str | None) -> str:
    if not source:
        return ""
    return source.split("·", 1)[0].strip()


def derive_source_key(url: str | None, source_type: str | None, source: str | None) -> str:
    st = (source_type or "").strip().lower()
    if st == "twitter":
        return "x"

    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("x.com", "twitter.com"):
        return "x"
    if host.endswith("reuters.com"):
        return "reuters"
    if host.endswith("bloomberg.com"):
        return "bloomberg"
    if host.endswith("techcrunch.com"):
        return "techcrunch"
    if host.endswith("arstechnica.com"):
        return "ars"
    if host:
        return f"host:{host}"

    prefix = _source_prefix(source).lower()
    if prefix == "reuters":
        return "reuters"
    if prefix == "bloomberg":
        return "bloomberg"
    if prefix == "techcrunch":
        return "techcrunch"
    if prefix in ("ars technica", "ars"):
        return "ars"
    if prefix in ("x", "twitter"):
        return "x"
    if prefix:
        return f"name:{prefix}"
    return "unknown"


def is_bloomberg_video_url(url: str | None) -> bool:
    """Identify Bloomberg's dedicated video pages without title/slug guessing."""
    if not url:
        return False
    try:
        parsed = urlparse(str(url).strip())
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    path = (parsed.path or "").lower()
    return (host == "bloomberg.com" or host.endswith(".bloomberg.com")) and path.startswith("/news/videos/")


def visible_news_sql(alias: str = "items") -> str:
    return f"is_bloomberg_video_url({alias}.url) = 0"


def source_label_for_key(key: str, source: str | None = None) -> str:
    if key in SOURCE_LABELS:
        return SOURCE_LABELS[key]
    if key.startswith("host:"):
        return key[5:]
    if key.startswith("name:"):
        return source or key[5:]
    return source or key


def _slug_source_part(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[\s/]+", "-", text)
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text[:80]


def _x_account_from_url(url: str | None) -> str:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"x.com", "twitter.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    account = parts[0].strip()
    if account.lower() in {"i", "home", "search", "intent", "share", "explore", "notifications", "messages"}:
        return ""
    return account[:80]


X_COLLECTION_SOURCE_ALIASES = {
    "ilya sutskever": ("ilyasut", "Ilya Sutskever"),
    "郭明錤": ("mingchikuo", "郭明錤"),
    "外汇交易员": ("fxtrader", "外汇交易员"),
    "time horizon": ("Time_HorizonX", "Time Horizon"),
    "卡比卡比": ("jakevin7", "卡比卡比"),
    "seekinganythingbutalpha": ("ivanalog_com", "seekinganythingbutalpha"),
}
X_GENERIC_SOURCE_NAMES = {"", "x", "twitter", "twitter/x"}
SOURCE_SUBKEY_COMPAT_ALIASES = {
    "bloomberg/bloomberg_tech": "bloomberg/tech",
    "bloomberg/technology": "bloomberg/tech",
}


def _candidate_account_label(*values: str | None) -> str:
    for value in values:
        text = (value or "").strip().lstrip("@")
        if not text or text.lower() in X_GENERIC_SOURCE_NAMES:
            continue
        if " · " in text:
            text = text.split(" · ")[-1].strip().lstrip("@")
        return text[:80]
    return ""


def _looks_like_x_handle(value: str | None) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,15}", (value or "").strip().lstrip("@")))


def _x_collection_source_from_meta(source_type: str | None, source: str | None, source_name: str | None, url: str | None = None) -> tuple[str, str]:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    is_x_url = host in {"x.com", "twitter.com"}
    st = (source_type or "").strip().lower()

    for raw in (source, source_name):
        text = (raw or "").strip().lstrip("@")
        if not text:
            continue
        alias = X_COLLECTION_SOURCE_ALIASES.get(text.lower())
        if alias:
            handle, label = alias
            return handle, f"{label} (@{handle})"

    for raw in (source, source_name):
        text = (raw or "").strip().lstrip("@")
        if not text or text.lower() in X_GENERIC_SOURCE_NAMES:
            continue
        if _looks_like_x_handle(text) and (st == "twitter" or is_x_url):
            return text[:80], f"@{text[:80]}"

    return "", ""


def _media_subsource_label(source_key: str, source: str | None, source_name: str | None = None) -> str:
    generic = {
        "reuters": {"reuters"},
        "bloomberg": {"bloomberg"},
        "techcrunch": {"techcrunch", "tech crunch"},
        "ars": {"ars", "ars technica", "arstechnica"},
    }
    for raw in (source, source_name):
        text = (raw or "").strip()
        if not text:
            continue
        if "·" in text:
            prefix, suffix = [part.strip() for part in text.split("·", 1)]
            if derive_source_key(None, None, prefix) == source_key and suffix:
                if source_key == "bloomberg" and _slug_source_part(suffix) in {"bloomberg_tech", "technology"}:
                    return "Tech"
                return suffix[:80]
        lowered = text.lower()
        if lowered in generic.get(source_key, set()):
            continue
        if source_key == "bloomberg" and _slug_source_part(text) in {"bloomberg_tech", "technology"}:
            return "Tech"
        return text[:80]
    return ""


def normalize_source_subkey_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().lower()
    if "/" not in raw:
        return ""
    source_key, subkey = raw.split("/", 1)
    source_key = _slug_source_part(source_key)
    subkey = _slug_source_part(subkey)
    if not source_key or not subkey:
        return ""
    key = f"{source_key}/{subkey}"
    return SOURCE_SUBKEY_COMPAT_ALIASES.get(key, key)


def normalize_hidden_source_subkeys(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = normalize_source_subkey_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized[:500]


def derive_source_subkey(url: str | None, source_type: str | None, source: str | None, source_name: str | None = None) -> dict | None:
    source_key = derive_source_key(url, source_type, source)
    source_label = SOURCE_LABELS.get(source_key) or source_label_for_key(source_key, source)
    if source_key == "x":
        account, label = _x_collection_source_from_meta(source_type, source, source_name, url)
        if not account:
            account = _x_account_from_url(url) or _candidate_account_label(source, source_name)
            label = f"@{account.lstrip('@')}" if account else ""
        slug = _slug_source_part(account)
        if not slug:
            return None
        return {"key": f"x/{slug}", "source_key": "x", "source_label": SOURCE_LABELS["x"], "label": label or f"@{account.lstrip('@')}"}

    if source_key in {"reuters", "bloomberg", "techcrunch", "ars"}:
        sub_label = _media_subsource_label(source_key, source, source_name)
        sub_slug = _slug_source_part(sub_label)
        if not sub_slug:
            return None
        key = SOURCE_SUBKEY_COMPAT_ALIASES.get(f"{source_key}/{sub_slug}", f"{source_key}/{sub_slug}")
        if key == "bloomberg/tech":
            sub_label = "Tech"
        return {"key": key, "source_key": source_key, "source_label": source_label, "label": sub_label}

    return None


def feed_source_subkey_sql(url: str | None, source_type: str | None, source: str | None, source_name: str | None = None) -> str:
    item = derive_source_subkey(url, source_type, source, source_name)
    return item["key"] if item else ""
