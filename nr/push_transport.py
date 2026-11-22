import base64
from functools import cache
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


@cache
def has_pywebpush():
    try:
        import_module("pywebpush")
    except (ImportError, OSError):
        return False
    return True


def node_request(operation, **data):
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node or not (ROOT / "node_modules/web-push/package.json").is_file():
        raise OSError("web_push_unavailable")
    try:
        result = subprocess.run(
            [node, str(ROOT / "scripts/browser-push.mjs"), operation],
            input=json.dumps(data), text=True, encoding="utf-8", stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={"PATH": os.defpath}, timeout=8, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise OSError("web_push_unavailable") from exc


def available():
    if has_pywebpush():
        return True
    try:
        return node_request("check") is True
    except OSError:
        return False


def deliver_with_node(subscription, payload, private_key, subject, ttl):
    import httpx

    try:
        request = node_request("encrypt", subscription=subscription, payload=payload,
                               privateKey=private_key, subject=subject, ttl=ttl)
        response = httpx.post(
            subscription["endpoint"], content=base64.b64decode(request["body"]),
            headers={name: str(value) for name, value in request["headers"].items()},
            follow_redirects=False, trust_env=False, timeout=8,
        )
        return response.status_code
    except (OSError, httpx.RequestError):
        return 503
