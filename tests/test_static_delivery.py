import gzip

import app


def test_static_gzip_keeps_content_validation_and_range_requests():
    client = app.app.test_client()
    path = "/static/app.js"
    plain = client.get(path)
    compressed = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert compressed.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(compressed.data) == plain.data
    assert len(compressed.data) < len(plain.data) / 2
    assert "Accept-Encoding" in compressed.headers["Vary"]
    assert "no-cache" in compressed.headers["Cache-Control"]
    conditional = client.get(path, headers={"Accept-Encoding": "gzip", "If-None-Match": compressed.headers["ETag"]})
    assert conditional.status_code == 304 and not conditional.data
    partial = client.get(path, headers={"Accept-Encoding": "gzip", "Range": "bytes=0-19"})
    assert partial.status_code == 206 and partial.data == plain.data[:20]
    assert "Content-Encoding" not in partial.headers
    refused = client.get(path, headers={"Accept-Encoding": "gzip;q=0"})
    assert "Content-Encoding" not in refused.headers
    head = client.head(path, headers={"Accept-Encoding": "gzip"})
    assert head.content_length == compressed.content_length and not head.data
