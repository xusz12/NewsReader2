from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi
from flask import jsonify, request

from nr import preferences
from updater import (
    REPOSITORY_URL as UPDATE_REPOSITORY_URL,
    STATE_PATH as UPDATE_STATE_PATH,
    git_state as update_git_state,
    github_tags_url,
    local_health_url,
    request_supervisor,
)


UPDATE_CHECK_TTL = 300
UPDATE_TOKEN_TTL = 600
RELEASE_NOTE_HEADING_RE = re.compile(r"^###\s+(?P<date>\d{4}-\d{2}-\d{2})\s+—\s+(?P<title>.+?)\s*$")
VERSION_RE = re.compile(r"\bv\d+(?:\.\d+){1,3}\b", re.IGNORECASE)


def categorize_release_note(title: str) -> str:
    lowered = title.lower()
    if "fix:" in lowered or lowered.startswith("fix"):
        return "FIX"
    if "feat:" in lowered or lowered.startswith("feat"):
        return "NEW"
    return "IMPROVE"


def _stable_version(tag: object) -> tuple[int, ...] | None:
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+(?:\.\d+){2,3}", tag):
        return None
    return tuple(int(part) for part in tag[1:].split("."))


class Updates:
    def __init__(self, db):
        self.db = db
        self.version_path = db.root / "version.json"
        self.changelog_path = db.root / "CHANGELOG.md"
        self.check_url = github_tags_url(UPDATE_REPOSITORY_URL)
        self.check_lock = threading.Lock()
        self.check_cache = None
        self.tokens = {}
        self.launch_lock = threading.Lock()
        self.process = None

    def parse_release_notes(self) -> list[dict]:
        """Parse versioned entries from the whole CHANGELOG in file order.

    CHANGELOG.md is the single source for the settings-page release notes.  Do
    not depend on a particular section heading: version entries may be placed
    before or after ``## What's Changed`` as the file evolves.
    """
        if not self.changelog_path.exists():
            return []

        lines = self.changelog_path.read_text(encoding="utf-8").splitlines()
        notes: list[dict] = []
        seen_versions: set[str] = set()
        current: dict | None = None

        def finalize_current() -> None:
            nonlocal current
            if current is None:
                return
            version = str(current.get("version") or "").lower()
            # Only publish standard version entries with actual release content.
            # This excludes headings such as "待发布" and empty placeholders.
            if version and current["lines"] and version not in seen_versions:
                notes.append(current)
                seen_versions.add(version)
            current = None

        for raw_line in lines:
            line = raw_line.rstrip()
            # A level-2/3 heading always terminates the previous entry.  A
            # non-version heading is intentionally ignored rather than becoming a
            # release note itself.
            if re.match(r"^#{2,3}\s+", line):
                match = RELEASE_NOTE_HEADING_RE.match(line)
                finalize_current()
                if not match:
                    continue
                title = match.group("title").strip()
                version_match = VERSION_RE.search(title)
                current = {
                    "date": match.group("date"),
                    "title": title,
                    "version": version_match.group(0) if version_match else "",
                    "category": categorize_release_note(title),
                    "lines": [],
                }
                continue
            if current is None or not line.strip():
                continue
            cleaned = line.strip()
            # Release groups use level-4 Markdown headings; the settings view is
            # plain text, so expose readable labels rather than Markdown markers.
            group_labels = {"#### feat": "新增功能", "#### improve": "体验优化", "#### fix": "问题修复"}
            cleaned = group_labels.get(cleaned.lower(), cleaned)
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            cleaned = cleaned.replace("**", "")
            current["lines"].append(cleaned)

        finalize_current()
        return notes

    def _version_manifest(self) -> dict:
        try:
            payload = json.loads(self.version_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        version = payload.get("version") if isinstance(payload, dict) else None
        return {"version": version if isinstance(version, str) else "v0.0.0", "repository": UPDATE_REPOSITORY_URL}

    def _current_commit(self) -> str:
        try:
            result = subprocess.run(["git", "-C", str(self.db.root), "rev-parse", "HEAD"], text=True,
                                    capture_output=True, check=False, timeout=3)
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _update_preflight(self) -> dict:
        """Re-read local conditions on every check; never cache worktree readiness."""
        blockers = []
        state = None
        try:
            state = update_git_state(self.db.root)
            if not state["clean"]:
                blockers.append("dirty_worktree")
            if state["detached"]:
                blockers.append("detached_head")
        except (OSError, RuntimeError):
            blockers.append("git_state_unavailable")
        managed = os.getenv("NEWS_READER_MANAGED") == "1"
        if not managed:
            blockers.append("managed_launcher_required")
        return {"managed_launcher": managed, "can_update": not blockers,
                "blockers": blockers, "worktree": state}

    def _fetch_update_check(self, *, force: bool = False) -> dict:
        now = time.time()
        current = self._version_manifest()["version"]
        current_commit = self._current_commit()
        if not self.check_url:
            return {
                "ok": True, "enabled": False, "status": "not_configured",
                "current_version": current, "current_commit": current_commit,
                "latest": None, "available": False, "repository": UPDATE_REPOSITORY_URL,
                "message": "独立项目尚未配置更新仓库，在线更新未启用。",
            }
        with self.check_lock:
            cached = (not force and self.check_cache
                      and self.check_cache.get("repository") == UPDATE_REPOSITORY_URL
                      and now - self.check_cache.get("checked_at", 0) < UPDATE_CHECK_TTL)
            if not cached:
                stable = []
                # Some macOS Python installs have no default CA bundle. Add trusted
                # public roots while retaining system / SSL_CERT_FILE certificates.
                context = ssl.create_default_context()
                context.load_verify_locations(cafile=certifi.where())
                # Do not assume the API order matches numeric versions. Read all pages.
                for page in range(1, 11):
                    req = Request(self.check_url + f"&page={page}", headers={
                        "Accept": "application/vnd.github+json", "User-Agent": "news-reader-updater"})
                    with urlopen(req, timeout=8, context=context) as response:
                        tags = json.loads(response.read().decode("utf-8"))
                    if not isinstance(tags, list):
                        raise ValueError("invalid_update_manifest")
                    for item in tags:
                        if not isinstance(item, dict):
                            continue
                        tag = item.get("name")
                        parsed = _stable_version(tag)
                        commit = ((item.get("commit") or {}).get("sha") if isinstance(item.get("commit"), dict) else "")
                        if parsed and isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                            stable.append({"tag": tag, "version": tag, "commit": commit.lower(),
                                           "numeric": parsed + (0,) * (4 - len(parsed))})
                    if len(tags) < 100:
                        break
                else:
                    raise ValueError("too_many_update_tags")
                latest = max(stable, key=lambda item: item["numeric"], default=None)
                self.check_cache = {"checked_at": time.time(), "repository": UPDATE_REPOSITORY_URL,
                                      "latest": latest}
            latest = self.check_cache["latest"]
            current_numeric = _stable_version(current) or (0,)
            current_numeric += (0,) * (4 - len(current_numeric))
            available = bool(latest and latest["numeric"] > current_numeric)
            status = ("no_releases" if latest is None else "update_available" if available
                      else "ahead" if latest["numeric"] < current_numeric else "up_to_date")
            payload = {
                "ok": True, "enabled": True, "status": status,
                "current_version": current, "current_commit": current_commit,
                "latest": {k: v for k, v in latest.items() if k != "numeric"} if latest else None,
                "available": available, "checked_at": int(self.check_cache["checked_at"]),
                "repository": UPDATE_REPOSITORY_URL,
            }
        return {**payload, **self._update_preflight()}


def register(app, db, updates):
    @app.get("/api/version")
    def api_version():
        manifest = updates._version_manifest()
        payload = {"ok": True, **manifest, "commit": updates._current_commit()}
        instance_id = os.getenv("NEWS_READER_INSTANCE_ID")
        if instance_id:
            payload["instance_id"] = instance_id
        return jsonify(payload)

    @app.get("/api/update/check")
    def api_update_check():
        try:
            payload = updates._fetch_update_check(force=request.args.get("force") == "1")
        except (OSError, ValueError, HTTPError, URLError, TimeoutError) as exc:
            return jsonify({"ok": False, "error": "update_check_failed", "detail": preferences.trim_settings_error(exc)}), 502
        if not payload.get("enabled") or not payload.get("available") or not payload.get("can_update"):
            return jsonify(payload)
        token = uuid.uuid4().hex
        with updates.check_lock:
            now = time.time()
            for expired in [key for key, value in updates.tokens.items() if value["expires_at"] <= now]:
                updates.tokens.pop(expired, None)
            # Bound memory even if checks are repeated without applying an update.
            while len(updates.tokens) >= 128:
                updates.tokens.pop(next(iter(updates.tokens)))
            updates.tokens[token] = {"expires_at": now + UPDATE_TOKEN_TTL, "payload": payload}
        return jsonify({**payload, "check_token": token, "token_expires_in": UPDATE_TOKEN_TTL})

    @app.get("/api/update/status")
    def api_update_status():
        try:
            payload = json.loads(UPDATE_STATE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = {"status": "idle"}
        except (OSError, ValueError):
            return jsonify({"ok": False, "error": "update_status_unavailable"}), 503
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "update_status_unavailable"}), 503
        return jsonify({"ok": True, **payload})

    @app.post("/api/update/apply")
    def api_update_apply():
        if not UPDATE_REPOSITORY_URL:
            return jsonify({"ok": False, "error": "update_repository_not_configured"}), 409
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        token = body.get("check_token")
        if not isinstance(token, str):
            return jsonify({"ok": False, "error": "missing_check_token"}), 400
        with updates.check_lock:
            record = updates.tokens.pop(token, None)
        if not record or record["expires_at"] < time.time():
            return jsonify({"ok": False, "error": "expired_check_token"}), 409
        payload = record["payload"]
        latest = payload.get("latest") or {}
        if not payload.get("available"):
            return jsonify({"ok": False, "error": "no_update_available"}), 409
        if os.getenv("NEWS_READER_MANAGED") != "1":
            return jsonify({"ok": False, "error": "managed_launcher_required"}), 409
        if payload.get("current_commit") != updates._current_commit():
            return jsonify({"ok": False, "error": "current_commit_changed"}), 409
        if any(body.get(key) != latest.get(key) for key in ("tag", "version", "commit")):
            return jsonify({"ok": False, "error": "update_target_mismatch"}), 409
        preflight = updates._update_preflight()
        if preflight["blockers"]:
            return jsonify({"ok": False, "error": preflight["blockers"][0]}), 409
        run_id = uuid.uuid4().hex
        try:
            port = int((os.getenv("NEWS_READER_PORT") or "8080").strip())
        except ValueError:
            port = 8080
        health_url = local_health_url(os.getenv("NEWS_READER_HOST") or "127.0.0.1", port)
        if os.getenv("NEWS_READER_SUPERVISOR_SOCKET"):
            try:
                response = request_supervisor(
                    "start_update", pid=os.getpid(), run_id=run_id,
                    tag=latest["tag"], version=latest["version"],
                    commit=latest["commit"], health_url=health_url,
                    instance_id=os.getenv("NEWS_READER_INSTANCE_ID", ""),
                )
            except RuntimeError:
                return jsonify({"ok": False, "error": "updater_launch_failed"}), 503
            if response.get("ok") is not True:
                error = response.get("error") or "updater_launch_failed"
                status = 409 if error == "update_in_progress" else 503
                return jsonify({"ok": False, "error": error}), status
        else:
            command = [sys.executable, str(db.root / "updater.py"), "--repo", str(db.root), "--run-id", run_id,
                       "--tag", latest["tag"], "--version", latest["version"], "--commit", latest["commit"],
                       "--pid", str(os.getpid()), "--health-url", health_url]
            with updates.launch_lock:
                if updates.process is not None and updates.process.poll() is None:
                    return jsonify({"ok": False, "error": "update_in_progress"}), 409
                try:
                    updates.process = subprocess.Popen(command, cwd=db.root, start_new_session=True,
                                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    return jsonify({"ok": False, "error": "updater_launch_failed"}), 503
        return jsonify({"ok": True, "status": "updating", "target": latest, "run_id": run_id}), 202

    @app.get("/api/release-notes")
    def api_release_notes():
        return jsonify({"ok": True, "items": updates.parse_release_notes()})
