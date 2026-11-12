"""Compress text assets while retaining Flask's conditional cache validation."""
from functools import lru_cache
import gzip

from flask import request


@lru_cache(maxsize=64)
def compressed_asset(body):
    return gzip.compress(body, compresslevel=6, mtime=0)


def compress_static_response(response):
    if request.endpoint not in {"static", "index"}:
        return response
    response.vary.add("Accept-Encoding")
    if (response.status_code != 200 or "Range" in request.headers
            or response.headers.get("Content-Encoding")
            or request.accept_encodings["gzip"] <= 0
            or response.mimetype not in {"text/html", "text/css", "text/javascript", "application/javascript", "image/svg+xml"}
            or not 1024 <= (response.content_length or 0) <= 2 * 1024 * 1024):
        return response
    response.direct_passthrough = False
    body = response.get_data()
    compressed = compressed_asset(body)
    if len(compressed) < len(body):
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        # Both encodings describe the same content, but not identical bytes.
        etag, _ = response.get_etag()
        if etag:
            response.set_etag(etag, weak=True)
    return response
