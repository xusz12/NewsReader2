"""Live acceptance, with isolated app data and no global/personal CLI code.

Requires the operator's running Chrome/Bridge and authorized site sessions.
No translation, production database writes, or automatic adapter upgrades.
"""
from pathlib import Path, PurePosixPath
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
FINGERPRINT_VERSION = 2
SAMPLE_FILE = "collection/validation-samples.json"
sys.path.insert(0, str(ROOT))

from feed_sources import is_bloomberg_video_url


def article_sample(items):
    """Pick supported article evidence; a dedicated video has no reader body."""
    return next((item["url"] for item in items
                 if item.get("url") and not is_bloomberg_video_url(item["url"])), None)


def locked_dependency_paths(packages, owner, dependency):
    location = PurePosixPath(owner)
    for directory in (location, *location.parents):
        if directory.name == "node_modules":
            continue
        candidate = (directory / "node_modules" / dependency).as_posix()
        if candidate in packages:
            yield candidate
            if not any(packages[candidate].get(key) for key in ("optional", "dev", "devOptional", "os", "cpu", "libc")):
                return


def opencli_dependency_lock(lock, package, version):
    if lock.get("lockfileVersion") not in (2, 3) or not isinstance(lock.get("packages"), dict):
        raise RuntimeError("OpenCLI 发布检查需要包含安装路径的 npm 锁文件。")
    packages = lock["packages"]
    entry_path = f"node_modules/{package}"
    if (packages.get("", {}).get("dependencies", {}).get(package) != version
            or packages.get(entry_path, {}).get("version") != version):
        raise RuntimeError("OpenCLI 的声明版本、锁定版本和采集清单不一致。")

    pending = [entry_path]
    resolved = {}
    while pending:
        path = pending.pop()
        if path in resolved:
            continue
        entry = packages[path]
        if entry.get("link") or (entry.get("resolved", "").startswith("file:") and not entry.get("integrity")):
            raise RuntimeError(f"OpenCLI 锁文件无法固定本地依赖 {path} 的内容。")
        resolved[path] = entry
        dependencies = set(entry.get("dependencies", {}))
        optional = set(entry.get("optionalDependencies", {}))
        peers = set(entry.get("peerDependencies", {}))
        peer_meta = entry.get("peerDependenciesMeta", {})
        required = (dependencies - optional) | {peer for peer in peers if not peer_meta.get(peer, {}).get("optional")}
        for dependency in dependencies | optional | peers:
            paths = list(locked_dependency_paths(packages, path, dependency))
            if not paths and dependency in required:
                raise RuntimeError(f"OpenCLI 锁文件缺少 {path} 的依赖 {dependency}。")
            pending.extend(paths)
    return resolved


def content_fingerprint(root=ROOT, *, sample_bytes=None):
    names = [".npmrc", "scripts/opencli.mjs", "opencli_runtime.py", "collection/opencli-manifest.json",
             "collection/sources.json", "collection_sources.py", "collection_environment.py", "nr/article_fetch.py",
             "feed_sources.py", "news_collection.py", "scripts/opencli-isolation-guard.mjs",
             "scripts/verify_collection_runtime.py"]
    names += [path.relative_to(root).as_posix() for path in (root / "collection/adapters").rglob("*") if path.is_file()]
    inputs = {name: (root / name).read_bytes() for name in names}
    inputs[SAMPLE_FILE] = (root / SAMPLE_FILE).read_bytes() if sample_bytes is None else sample_bytes
    manifest = json.loads(inputs["collection/opencli-manifest.json"])
    package = json.loads((root / "package.json").read_bytes())
    lock = json.loads((root / "package-lock.json").read_bytes())
    name, version = manifest["package"], manifest["version"]
    if package.get("dependencies", {}).get(name) != version:
        raise RuntimeError("OpenCLI 的声明版本、锁定版本和采集清单不一致。")

    requirements = (root / "requirements.txt").read_bytes().decode("utf-8").splitlines()
    python_runtime = [line for line in map(str.strip, requirements)
                      if re.match(r"(?i)^(?:flask|certifi)(?:$|[\s\[<>=!~;@])", line) or line.startswith("-")]
    payload = {
        "version": FINGERPRINT_VERSION,
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in inputs.items()},
        "node": {key: package.get(key) for key in ("type", "engines", "packageManager", "os", "cpu", "libc")},
        "python_requirements": python_runtime,
        "lockfile_version": lock.get("lockfileVersion"),
        "opencli_lock": opencli_dependency_lock(lock, name, version),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def valid_time(value):
    if value == "页面未显示":
        return True
    try:
        datetime.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=ROOT / SAMPLE_FILE)
    parser.add_argument("--restart-daemon", action="store_true", help="restart idle bridge using the project runtime")
    args = parser.parse_args()
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        parser.error("Node.js 24+ is required")
    sample_bytes = args.samples.read_bytes()
    samples = json.loads(sample_bytes)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "fingerprint_version": FINGERPRINT_VERSION,
              "fingerprint": content_fingerprint(sample_bytes=sample_bytes),
              "newsreader_version": json.loads((ROOT / "package.json").read_text())["version"],
              "sources": [], "details": [], "isolation": {"external_modules_blocked": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="newsreader-acceptance-") as temporary:
        state = Path(temporary)
        poison = state / "opencli"
        poison.write_text('#!/bin/sh\nprintf used > "' + str(state / 'global-used') + '"\nexit 99\n')
        poison.chmod(0o755)
        os.environ.update({"NEWS_READER_NODE": str(Path(node).resolve()),
            "PATH": str(state) + os.pathsep + os.defpath,
            "NODE_OPTIONS": '--import=' + (ROOT / "scripts/opencli-isolation-guard.mjs").as_uri(),
            "NEWS_READER_DB_PATH": str(state / "news.sqlite3"),
            "NEWS_READER_AGENT_DB_PATH": str(state / "agents.sqlite3"),
            "NEWS_READER_MEDIA_CACHE_DIR": str(state / "media"),
            "NEWS_READER_APP_SETTINGS_PATH": str(state / "settings.json")})
        import collection_environment
        import collection_sources
        import news_collection
        from llm_client import LLMClientError
        from nr.article_fetch import ArticleFetcher

        def no_summary(**_):
            raise LLMClientError("Live verification does not call model services")

        fetcher = ArticleFetcher(db=None, cache_images=False, summarize_comments=no_summary)
        def cli(*command, timeout=30):
            p = collection_sources.run_command(["opencli", *command], timeout=timeout)
            data = json.loads(p.stdout)
            if p.returncode or data.get("ok") is not True:
                raise RuntimeError(data.get("error", "CLI failed"))
            return data["data"]
        report["runtime"] = cli("--nr-check")
        if args.restart_daemon:
            status = cli("daemon", "status")
            if status.get("pending") or status.get("siteSlots") or status.get("sessionLeases"):
                raise RuntimeError("Browser is busy; retry verification when collection/detail work is idle")
            restarted = cli("daemon", "restart")
            report["isolation"]["project_daemon_started"] = restarted.get("action") in ("started", "restarted")
        report["environment"] = collection_environment.check()
        report["x_login"] = collection_environment.check("x")
        save()
        source_urls = {}
        for source in collection_sources.load_config(ROOT / "collection/sources.json"):
            started = time.monotonic()
            result = news_collection.acquire_source(source)
            items = result.get("items", [])
            host = {"Reuters": "reuters.com", "Bloomberg": "bloomberg.com", "TechCrunch": "techcrunch.com", "Ars Technica": "arstechnica.com"}.get(source["source_name"], "x.com")
            valid = bool(items) and all(item.get("title", "").strip() and
                urlsplit(item.get("url", "")).scheme == "https" and item.get("source_name") == source["source_name"]
                and (urlsplit(item["url"]).hostname == host or (urlsplit(item["url"]).hostname or "").endswith("." + host))
                and valid_time(item.get("time")) for item in items)
            if source["source_type"] == "twitter":
                valid = valid and all(re.search(r"/status/\d+", item["url"]) and
                    item.get("author_screen_name") and item.get("source_handle") == source["source_handle"] for item in items)
            row = {"section": source["section"], "ok": result["ok"] and valid,
                   "raw_count": result["raw_count"], "valid_count": result["valid_count"],
                   "seconds": round(time.monotonic() - started, 2), "error": result.get("error", ""),
                   "samples": [{k: item.get(k) for k in ("title", "url", "time", "source_name")} for item in items[:2]]}
            report["sources"].append(row)
            sample = article_sample(items)
            if sample:
                source_urls.setdefault(source["source_name"], sample)
            print(json.dumps({"source": source["section"], "ok": row["ok"], "count": row["valid_count"], "error": row["error"]}, ensure_ascii=False), flush=True)
            save()
        cases = [{"name": name, "url": source_urls.get(name), "kind": "article"}
                 for name in ("Reuters", "Bloomberg", "TechCrunch", "Ars Technica")]
        cases += samples
        for case in cases:
            url = case.get("url")
            started = time.monotonic()
            if not url:
                ok, detail, error = False, {}, "No source sample available"
            else:
                ok, detail, error = fetcher.run_opencli_detail(url)
            content = detail.get("content", "")
            raw = json.loads(detail.get("raw_json") or "{}")
            valid = len(content) >= (1 if case["kind"] == "x_thread" else 200) and bool(detail.get("title"))
            if case["kind"] in ("x_article", "x_note"):
                valid = valid and len((raw.get("article") or {}).get("content", "")) >= 200
            if case["kind"] == "x_article":
                valid = valid and (raw.get("article") or {}).get("title") not in (None, "", "(Note Tweet)")
            if case["kind"].startswith("x_"):
                tweet_id = re.search(r"/status/(\d+)", url or "")
                valid = valid and bool(tweet_id) and str((raw.get("tweet") or {}).get("id")) == tweet_id[1]
            for snippet in case.get("required_text", []):
                normalized = lambda value: re.sub(r"\s+", " ", re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", value)).casefold()
                valid = valid and normalized(snippet) in normalized(content)
            for snippet in case.get("forbidden_text", []):
                valid = valid and snippet.casefold() not in content.casefold()
            blocked = re.search(r"^(?:please (?:sign in|enable javascript)|verify (?:that )?you are human|access denied|subscribe to continue|this transcript was produced with AI technology)", content.strip(), re.I)
            row = {"name": case["name"], "kind": case["kind"], "url": url, "ok": bool(ok and valid and not blocked),
                   "title": detail.get("title"), "length": len(content), "seconds": round(time.monotonic() - started, 2),
                   "error": error, "article_length": len((raw.get("article") or {}).get("content", "")),
                   "required_text_checks": len(case.get("required_text", [])),
                   "forbidden_text_checks": len(case.get("forbidden_text", [])),
                   "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                   "preview": content[:160]}
            report["details"].append(row)
            print(json.dumps({k: row[k] for k in ("name", "ok", "length", "error")}, ensure_ascii=False), flush=True)
            save()
        report["isolation"]["global_cli_used"] = (state / "global-used").exists()
        report["ok"] = (not report["isolation"]["global_cli_used"]
            and all(row["status"] == "success" for target in ("environment", "x_login") for row in report[target]["checks"])
            and all(row["ok"] for group in ("sources", "details") for row in report[group]))
        save()
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
