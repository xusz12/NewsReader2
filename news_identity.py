"""Stable URL-only news identity, shared by collection and legacy readers."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise ValueError("collection_invalid_url")
    if host.removeprefix("www.") in {"twitter.com", "x.com", "mobile.twitter.com"}:
        match = re.search(r"/(?:status|statuses)/(\d+)", parts.path)
        if match:
            return "https://x.com/i/status/" + match[1]
    host = host.removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host += f":{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", host, parts.path or "/", urlencode(query), ""))


def identity_key(url):
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()
