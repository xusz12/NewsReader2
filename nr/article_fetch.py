from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import jsonify, send_from_directory

from feed_sources import is_bloomberg_video_url
from llm_client import LLMClientError, generate_twitter_comments_summary
from nr import database
from opencli_runtime import resolve_command as resolve_opencli_command


DETAIL_COMMAND_ROUTES = {
    "reuters.com": {"source": "Reuters", "command": ["opencli", "reuters", "article-detail"], "timeout": 45},
    "bloomberg.com": {"source": "Bloomberg", "command": ["opencli", "bloomberg", "news"], "timeout": 90},
    "techcrunch.com": {"source": "TechCrunch", "command": ["opencli", "TechcrunchPublic", "article"], "timeout": 45},
    "arstechnica.com": {"source": "Ars Technica", "command": ["opencli", "ArsPublic", "article"], "timeout": 45},
}
TWITTER_THREAD_COMMAND = ["opencli", "twitter", "thread"]
TWITTER_ARTICLE_COMMAND = ["opencli", "twitter", "article"]
TWITTER_COMMENT_FETCH_LIMIT = 50
TWITTER_THREAD_TIMEOUT = 90
TWITTER_ARTICLE_TIMEOUT = 60


def resolve_detail_route(url: str) -> dict | None:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("x.com", "twitter.com"):
        return {"source": "Twitter/X", "command": TWITTER_THREAD_COMMAND, "timeout": TWITTER_THREAD_TIMEOUT}
    return DETAIL_COMMAND_ROUTES.get(host)


def _run_opencli_json_command(cmd: list[str], timeout: int) -> tuple[bool, object, str]:
    try:
        proc = subprocess.run(resolve_opencli_command(cmd), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, None, "TIMEOUT"
    except Exception as exc:
        return False, None, f"SUBPROCESS_ERROR: {exc}"

    try:
        parsed = json.loads(proc.stdout or "")
    except (ValueError, TypeError) as exc:
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return False, None, err or f"EXIT_{proc.returncode}"
        return False, None, f"INVALID_JSON: {exc}"

    # opencli 2.x wraps results in {ok, data/error}; older adapters returned
    # the list or article object directly. Unwrap once at this shared boundary.
    if isinstance(parsed, dict) and isinstance(parsed.get("ok"), bool):
        if not parsed["ok"]:
            error = parsed.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "OPENCLI_ERROR")
                parts = [str(error[key]) for key in ("message", "cause", "hint", "help") if error.get(key)]
                return False, None, f"{code}: {' | '.join(dict.fromkeys(parts))}".rstrip(": ")
            return False, None, f"OPENCLI_ERROR: {error or 'Command failed'}"
        if "data" not in parsed:
            return False, None, "INVALID_JSON: opencli success response has no data"
        parsed = parsed["data"]

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, None, err or f"EXIT_{proc.returncode}"
    return True, parsed, ""


def _normalize_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for part in (_normalize_text(item) for item in value) if part)
    if isinstance(value, dict):
        return _normalize_text(value.get("text") or value.get("content") or value.get("body"))
    return ""


def _normalize_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _normalize_text(item)
        if text:
            result.append(text)
    return result


def _strip_twitter_comment_summary_prefix(text: str, comment_count: int) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    prefixes = [
        f"基于已抓取的 {comment_count} 条评论总结：",
        f"基于已抓取的{comment_count}条评论总结：",
        f"基于已抓取的 {comment_count} 条评论总结",
        f"基于已抓取的{comment_count}条评论总结",
    ]
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    return normalized.lstrip("：: ").strip()


def _looks_like_same_content(left: str, right: str) -> bool:
    a = re.sub(r"\s+", " ", (left or "").strip())
    b = re.sub(r"\s+", " ", (right or "").strip())
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 80 and shorter in longer


def _extract_twitter_thread_payload(parsed: object) -> tuple[dict, list[dict]]:
    if isinstance(parsed, list):
        tweets = [item for item in parsed if isinstance(item, dict)]
        if not tweets:
            return {}, []
        return tweets[0], tweets[1:]

    record = parsed if isinstance(parsed, dict) else {}
    if not record:
        return {}, []
    main = record.get("tweet") if isinstance(record.get("tweet"), dict) else record
    comments: list[dict] = []
    for key in ("comments", "replies", "reply_tweets", "tweets"):
        value = record.get(key)
        if isinstance(value, list):
            entries = [item for item in value if isinstance(item, dict)]
            if entries:
                comments = entries[1:] if key == "tweets" and entries and main is entries[0] else entries
                break
    return main if isinstance(main, dict) else {}, comments


def _extract_twitter_article_payload(parsed: object) -> dict:
    record = parsed[0] if isinstance(parsed, list) and parsed else parsed
    return record if isinstance(record, dict) else {}


_TWITTER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_TWITTER_VIDEO_HOSTS = {"video.twimg.com"}
_TWITTER_VIDEO_PATTERNS = (".mp4", ".m3u8", ".mov", "amplify_video", "ext_tw_video", "/video/")


def _is_cacheable_twitter_media_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host != "pbs.twimg.com" or "/media/" not in path:
        return False
    url_lower = url.lower()
    if any(pattern in url_lower for pattern in _TWITTER_VIDEO_PATTERNS):
        return False
    return True


def _has_substantial_twitter_content(
    main_text: str,
    quoted_text: str,
    article_text: str,
    media_images: list[dict],
) -> bool:
    if (main_text or "").strip():
        return True
    if (quoted_text or "").strip():
        return True
    if (article_text or "").strip():
        return True
    if media_images:
        return True
    return False


def _is_twitter_image_url(url: str) -> bool:
    return _is_cacheable_twitter_media_url(url)


def _extract_tweet_media_urls(tweet: dict) -> list[str]:
    urls: list[str] = []
    media_urls = tweet.get("media_urls")
    if isinstance(media_urls, str):
        media_urls = [media_urls]
    if isinstance(media_urls, list):
        for value in media_urls:
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
    media_list = tweet.get("media")
    if isinstance(media_list, list):
        for entry in media_list:
            if not isinstance(entry, dict):
                continue
            for key in ("media_url_https", "url", "media_url"):
                value = entry.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    urls.append(value)
                    break
    return urls


def _build_twitter_media_images(main_tweet: dict, quoted_tweet: dict | None) -> list[dict]:
    seen: set[str] = set()
    images: list[dict] = []
    for source, tweet in (("tweet", main_tweet), ("quoted_tweet", quoted_tweet or {})):
        if not isinstance(tweet, dict):
            continue
        for url in _extract_tweet_media_urls(tweet):
            if url in seen:
                continue
            if _is_twitter_image_url(url):
                seen.add(url)
                images.append({"url": url, "source": source})
    return images


def _is_twitter_url(url: str) -> bool:
    route = resolve_detail_route(url)
    return route is not None and route.get("source") == "Twitter/X"


_MEDIA_CACHE_MAX_SIZE = 10 * 1024 * 1024
_MEDIA_CACHE_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MEDIA_CACHE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MEDIA_CACHE_MAX_AGE_DAYS = 30
_MEDIA_CACHE_KEY_RE = re.compile(r"^[a-f0-9]{64}$")


def _media_cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _extension_for_mime_type(mime_type: str | None) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get((mime_type or "").lower(), "")


def _media_cache_relative_path(cache_key: str, ext: str = "") -> str:
    return f"{cache_key[:2]}/{cache_key[2:4]}/{cache_key}{ext}"


def _download_media_file(url: str, dest_path: Path) -> tuple[bool, str | None, int]:
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as resp:
            final_url = resp.geturl()
            if not _is_cacheable_twitter_media_url(final_url):
                return False, f"redirect_to_non_whitelist: {final_url}", 0
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type:
                if content_type not in _MEDIA_CACHE_ALLOWED_MIME_TYPES:
                    return False, f"unsupported_content_type: {content_type}", 0
            else:
                parsed_path = urlparse(final_url).path.lower()
                ext = os.path.splitext(parsed_path)[1]
                if ext not in _MEDIA_CACHE_ALLOWED_EXTENSIONS:
                    return False, f"unsupported_extension: {ext}", 0
            data = resp.read(_MEDIA_CACHE_MAX_SIZE + 1)
            if len(data) > _MEDIA_CACHE_MAX_SIZE:
                return False, "content_too_large", 0
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(data)
            return True, content_type or None, len(data)
    except HTTPError as exc:
        return False, f"http_{exc.code}", 0
    except Exception as exc:
        return False, str(exc), 0


def _sanitize_twitter_media_images(raw_images: object) -> list[dict]:
    if not isinstance(raw_images, list):
        return []
    seen: set[str] = set()
    result: list[dict] = []
    for entry in raw_images:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if source not in ("tweet", "quoted_tweet"):
            continue
        url = entry.get("url")
        if not _is_cacheable_twitter_media_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "source": source})
    return result


def _build_twitter_comments_summary_text(comments: list[dict]) -> str:
    parts: list[str] = []
    for index, comment in enumerate(comments, start=1):
        author = _normalize_text(comment.get("author") or comment.get("username") or comment.get("screen_name"))
        text = _normalize_text(comment.get("text") or comment.get("content") or comment.get("full_text") or comment.get("body"))
        if not text:
            continue
        label = f"评论 {index}"
        if author:
            label += f"（{author}）"
        parts.append(f"{label}：{text}")
    return "\n".join(parts).strip()


class ArticleFetcher:
    def __init__(self, db, *, cache_images=True, summarize_comments=generate_twitter_comments_summary):
        self.db = db
        self.summarize_comments = summarize_comments
        self.cache_images = cache_images

    def _is_path_within_media_cache_dir(self, full_path: Path) -> bool:
        try:
            resolved = full_path.resolve()
            root = self.db.media_dir.resolve()
            return resolved == root or root in resolved.parents
        except Exception:
            return False

    def _cleanup_media_cache(self, conn: sqlite3.Connection) -> None:
        cutoff = (datetime.now() - timedelta(days=_MEDIA_CACHE_MAX_AGE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT url, relative_path FROM media_cache WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            try:
                full_path = (self.db.media_dir / row["relative_path"]).resolve()
                if self._is_path_within_media_cache_dir(full_path) and full_path.exists():
                    full_path.unlink()
            except Exception:
                pass
        conn.execute("DELETE FROM media_cache WHERE created_at < ?", (cutoff,))

    def _cache_twitter_image(self, conn: sqlite3.Connection, url: str, *, article_url: str = "") -> dict | None:
        if not _is_cacheable_twitter_media_url(url):
            return None
        if article_url and not conn.execute("SELECT 1 FROM items WHERE url=? LIMIT 1", (article_url,)).fetchone():
            return None
        cache_key = _media_cache_key_for_url(url)
        relative_path = _media_cache_relative_path(cache_key)
        dest_path = self.db.media_dir / relative_path
        existing = conn.execute(
            "SELECT status, relative_path FROM media_cache WHERE url=?",
            (url,),
        ).fetchone()
        if existing and existing["status"] == "success":
            existing_path = self.db.media_dir / existing["relative_path"]
            if existing_path.exists():
                return {"cache_key": cache_key, "cached_url": f"/api/media-cache/{cache_key}", "status": "success"}
        ok, mime_type, size = _download_media_file(url, dest_path)
        if article_url:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM items WHERE url=? LIMIT 1", (article_url,)).fetchone():
                if not conn.execute("SELECT 1 FROM media_cache WHERE url=?", (url,)).fetchone():
                    dest_path.unlink(missing_ok=True)
                return None
        ts = database.now_ts()
        if not ok:
            conn.execute(
                """
            INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              status=excluded.status,
              updated_at=excluded.updated_at,
              last_error=excluded.last_error
            """,
                (url, cache_key, relative_path, None, 0, "failed", ts, ts, mime_type or "download_failed"),
            )
            return None
        ext = _extension_for_mime_type(mime_type)
        if ext:
            relative_path = _media_cache_relative_path(cache_key, ext)
            final_path = self.db.media_dir / relative_path
            if final_path != dest_path:
                final_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest_path), str(final_path))
        else:
            final_path = dest_path
        conn.execute(
            """
        INSERT INTO media_cache(url, cache_key, relative_path, mime_type, size_bytes, status, created_at, updated_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          cache_key=excluded.cache_key,
          relative_path=excluded.relative_path,
          mime_type=excluded.mime_type,
          size_bytes=excluded.size_bytes,
          status=excluded.status,
          created_at=excluded.created_at,
          updated_at=excluded.updated_at,
          last_error=NULL
        """,
            (url, cache_key, relative_path, mime_type, size, "success", ts, ts, None),
        )
        return {"cache_key": cache_key, "cached_url": f"/api/media-cache/{cache_key}", "status": "success"}

    def run_opencli_twitter_detail(self, url: str) -> tuple[bool, dict, str]:
        thread_cmd = [
            *TWITTER_THREAD_COMMAND,
            url,
            "--limit",
            str(TWITTER_COMMENT_FETCH_LIMIT),
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
        ok, parsed_thread, error = _run_opencli_json_command(thread_cmd, TWITTER_THREAD_TIMEOUT)
        if not ok:
            return False, {}, error

        main_tweet, comments = _extract_twitter_thread_payload(parsed_thread)
        main_text = _normalize_text(main_tweet.get("text") or main_tweet.get("content") or main_tweet.get("full_text") or main_tweet.get("body"))

        quoted = main_tweet.get("quoted_tweet") if isinstance(main_tweet.get("quoted_tweet"), dict) else {}
        quoted_text = _normalize_text(quoted.get("text") or quoted.get("content") or quoted.get("full_text") or quoted.get("body"))

        article_record: dict = {}
        article_error = ""
        article_cmd = [
            *TWITTER_ARTICLE_COMMAND,
            url,
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
        article_ok, parsed_article, article_err = _run_opencli_json_command(article_cmd, TWITTER_ARTICLE_TIMEOUT)
        if article_ok:
            article_record = _extract_twitter_article_payload(parsed_article)
        else:
            article_error = article_err

        article_text = _normalize_text(article_record.get("content") or article_record.get("body") or article_record.get("text") or article_record.get("full_text"))
        if _looks_like_same_content(main_text, article_text):
            article_text = ""

        media_images = _build_twitter_media_images(main_tweet, quoted)
        if not _has_substantial_twitter_content(main_text, quoted_text, article_text, media_images):
            return False, {}, "EMPTY_TWITTER_THREAD"

        comment_count = len(comments)
        comment_summary_text = ""
        comment_summary_error = ""
        if comment_count > 5:
            comments_text = _build_twitter_comments_summary_text(comments)
            if comments_text:
                try:
                    summary_payload = self.summarize_comments(
                        title=_normalize_text(main_tweet.get("title")) or "Twitter/X 评论区",
                        source="Twitter/X",
                        comments_text=comments_text,
                        comment_count=comment_count,
                    )
                    comment_summary_text = _strip_twitter_comment_summary_prefix(
                        summary_payload.get("summary_text", ""),
                        comment_count,
                    )
                except LLMClientError as exc:
                    comment_summary_error = str(exc)

        lines = ["【主推文】", main_text]
        if quoted_text:
            lines.extend(["", "【引用推文】", quoted_text])
        if article_text:
            lines.extend(["", "【长文补充】", article_text])
        if comment_count <= 5:
            comment_lines = _normalize_text_list(
                [
                    f"{_normalize_text(comment.get('author') or comment.get('username') or comment.get('screen_name'))}：{_normalize_text(comment.get('text') or comment.get('content') or comment.get('full_text') or comment.get('body'))}"
                    for comment in comments
                ]
            )
            if comment_lines:
                lines.extend(["", "【评论区观点】", *comment_lines])
            else:
                lines.extend(
                    [
                        "",
                        "【评论区观点】",
                        "未获取到评论；opencli thread 本次返回 0 条评论，可能是该推文无可见评论、登录态/权限限制或 X 分页未返回。",
                    ]
                )
        elif comment_summary_text:
            lines.extend(["", "【评论区观点】", f"基于已抓取的 {comment_count} 条评论总结：{comment_summary_text}"])
        elif comment_count:
            fallback_lines = _normalize_text_list(
                [
                    f"{_normalize_text(comment.get('author') or comment.get('username') or comment.get('screen_name'))}：{_normalize_text(comment.get('text') or comment.get('content') or comment.get('full_text') or comment.get('body'))}"
                    for comment in comments[:5]
                ]
            )
            lines.extend(
                [
                    "",
                    "【评论区观点】",
                    f"评论区观点总结失败，以下仅展示已抓取评论样本（共 {comment_count} 条）。",
                    *fallback_lines,
                ]
            )

        content = "\n".join(part for part in lines if part is not None).strip()
        cached_images: list[dict] = []
        if media_images and self.cache_images:
            conn = self.db.connect()
            try:
                for image in media_images:
                    cached = self._cache_twitter_image(conn, image["url"], article_url=url)
                    if cached:
                        cached_images.append({**image, **cached})
                    else:
                        cached_images.append(image)
                self._cleanup_media_cache(conn)
                conn.commit()
            finally:
                conn.close()
        else:
            cached_images = media_images
        payload = {
            "source": "Twitter/X",
            "tweet": main_tweet,
            "quoted_tweet": quoted or None,
            "comments": comments,
            "comment_count": comment_count,
            "article": article_record or None,
            "article_error": article_error or None,
            "comment_summary_error": comment_summary_error or None,
            "media_images": cached_images,
        }
        return (
            True,
            {
                "source": "Twitter/X",
                "title": _normalize_text(main_tweet.get("title")) or _normalize_text(main_tweet.get("author")) or "Twitter/X",
                "author": _normalize_text(main_tweet.get("author") or main_tweet.get("username") or main_tweet.get("screen_name")),
                "published_at": _normalize_text(main_tweet.get("published_at") or main_tweet.get("created_at") or main_tweet.get("date") or main_tweet.get("time")),
                "content": content,
                "content_length": len(content),
                "raw_json": json.dumps(payload, ensure_ascii=False),
            },
            "",
        )

    def run_opencli_detail(self, url: str) -> tuple[bool, dict, str]:
        route = resolve_detail_route(url)
        if not route:
            return False, {}, "UNSUPPORTED_URL"
        source = route["source"]
        command = route["command"]
        timeout = int(route.get("timeout", 30))
        if source == "Twitter/X":
            return self.run_opencli_twitter_detail(url)

        if source == "Bloomberg" and is_bloomberg_video_url(url):
            return False, {}, "SKIPPED_SOURCE: BLOOMBERG_VIDEO"

        cmd = [*command, url, "-f", "json"]
        ok, parsed, error = _run_opencli_json_command(cmd, timeout)
        if not ok:
            return False, {}, error

        record = parsed[0] if isinstance(parsed, list) and parsed else parsed
        if not isinstance(record, dict):
            return False, {}, "EMPTY_RESULT"

        if record.get("status") == "failed":
            code = record.get("error_code") or "FETCH_FAILED"
            reason = record.get("reason") or ""
            return False, {}, f"{code}: {reason}".strip()

        content = (record.get("content") or record.get("body") or record.get("body_text") or "").strip()
        if len(content) < 200:
            return False, {}, f"CONTENT_TOO_SHORT: {len(content)}"

        normalized = {
            "source": source,
            "title": (record.get("title") or record.get("headline") or "").strip(),
            "author": (record.get("author") or record.get("authors") or record.get("byline") or "").strip(),
            "published_at": (record.get("published_at") or record.get("date") or record.get("time") or record.get("publish_time") or "").strip(),
            "content": content,
            "content_length": int(record.get("content_length") or len(content)),
            "raw_json": json.dumps(record, ensure_ascii=False),
        }
        return True, normalized, ""


def register(app, db, fetcher):
    @app.get("/api/media-cache/<cache_key>")
    def api_media_cache(cache_key: str):
        if not _MEDIA_CACHE_KEY_RE.match(cache_key):
            return jsonify({"ok": False, "error": "invalid_cache_key"}), 400
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT relative_path, mime_type FROM media_cache WHERE cache_key=? AND status='success'",
                (cache_key,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"ok": False, "error": "not_found"}), 404
        full_path = (db.media_dir / row["relative_path"]).resolve()
        if not fetcher._is_path_within_media_cache_dir(full_path):
            return jsonify({"ok": False, "error": "invalid_path"}), 400
        if not full_path.exists():
            return jsonify({"ok": False, "error": "file_missing"}), 404
        return send_from_directory(
            str(db.media_dir),
            row["relative_path"],
            mimetype=row["mime_type"] or None,
        )
