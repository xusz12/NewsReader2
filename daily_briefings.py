from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


DAILY_BRIEFING_FILENAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_daily\.md$")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
METADATA_RE = re.compile(r"^(?:[-*]\s*)?(?P<key>[^:：]+?)\s*[：:]\s*(?P<value>.+?)\s*$")
INLINE_TOKEN_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_daily_briefing_date(value: str) -> str:
    return datetime.strptime((value or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


def list_daily_briefing_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    rows: list[tuple[str, Path]] = []
    for path in root.rglob("*_daily.md"):
        if not path.is_file():
            continue
        match = DAILY_BRIEFING_FILENAME_RE.match(path.name)
        if not match:
            continue
        rows.append((match.group("date"), path))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in rows]


def format_month_label(month_key: str) -> str:
    try:
        day = datetime.strptime(f"{month_key}-01", "%Y-%m-%d")
    except ValueError:
        return month_key
    return f"{day.year}年{day.month}月"


def build_fallback_briefing(date_key: str, text: str, reason: str) -> dict:
    return {
        "date": date_key,
        "month": date_key[:7],
        "month_label": format_month_label(date_key[:7]),
        "date_label": format_daily_briefing_date_label(date_key),
        "weekday_label": format_weekday_label(date_key),
        "title": f"{format_daily_briefing_date_label(date_key)} 日报",
        "metadata": [],
        "metadata_summary": "",
        "page_title": "",
        "sections": [
            {
                "title": "原始内容",
                "items": [
                    {"type": "paragraph", "parts": [{"type": "text", "text": line}]}
                    for line in text.splitlines()
                    if line.strip()
                ],
            }
        ],
        "footer_note": "",
        "parse_mode": "fallback",
        "parse_warning": reason,
    }


def parse_daily_briefing_file(path: Path) -> dict:
    match = DAILY_BRIEFING_FILENAME_RE.match(path.name)
    if not match:
        raise ValueError("invalid_briefing_filename")
    date_key = match.group("date")
    text = path.read_text(encoding="utf-8")
    stat = path.stat()
    try:
        parsed = parse_daily_briefing_text(date_key, text)
    except Exception:
        parsed = build_fallback_briefing(date_key, text, "parse_failed")
    parsed["filename"] = path.name
    parsed["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return parsed


def parse_daily_briefing_text(date_key: str, text: str) -> dict:
    lines = text.splitlines()
    index = 0
    metadata: list[dict] = []
    while index < len(lines) and not lines[index].strip():
        index += 1

    if index < len(lines) and lines[index].strip() == "## 简报信息":
        index += 1
        metadata, index = parse_metadata_block(lines, index)

    while index < len(lines) and is_separator_or_blank(lines[index]):
        index += 1

    blocks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    while index < len(lines):
        stripped = lines[index].strip()
        heading = HEADING_RE.match(stripped)
        if heading and heading.group(1) == "##":
            if current_title:
                blocks.append((current_title, current_lines))
            current_title = heading.group(2).strip()
            current_lines = []
        else:
            current_lines.append(lines[index])
        index += 1
    if current_title:
        blocks.append((current_title, current_lines))

    page_title = ""
    sections: list[dict] = []
    footer_note = ""
    for title, body_lines in blocks:
        parsed_sections, block_footer, block_page_title = parse_section_block(title, body_lines)
        if block_page_title and not page_title:
            page_title = block_page_title
        if block_footer and not footer_note:
            footer_note = block_footer
        sections.extend(parsed_sections)

    final_title = page_title or f"{format_daily_briefing_date_label(date_key)} 日报"
    metadata_summary = " · ".join(
        f"{entry['key']}：{entry['value']}" for entry in metadata if entry.get("key") and entry.get("value")
    )

    if not sections:
        fallback = build_fallback_briefing(date_key, text, "empty_sections")
        fallback["metadata"] = metadata
        fallback["metadata_summary"] = metadata_summary
        return fallback

    return {
        "date": date_key,
        "month": date_key[:7],
        "month_label": format_month_label(date_key[:7]),
        "date_label": format_daily_briefing_date_label(date_key),
        "weekday_label": format_weekday_label(date_key),
        "title": final_title,
        "metadata": metadata,
        "metadata_summary": metadata_summary,
        "page_title": page_title,
        "sections": sections,
        "footer_note": footer_note,
        "parse_mode": "structured",
        "parse_warning": "",
    }


def parse_metadata_block(lines: list[str], start: int) -> tuple[list[dict], int]:
    metadata: list[dict] = []
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped == "---":
            index += 1
            break
        heading = HEADING_RE.match(stripped)
        if heading and heading.group(1) == "##":
            break
        match = METADATA_RE.match(stripped)
        if match:
            metadata.append(
                {
                    "key": match.group("key").strip(),
                    "value": strip_wrapping_backticks(match.group("value").strip()),
                }
            )
        index += 1
    return metadata, index


def parse_section_block(title: str, lines: list[str]) -> tuple[list[dict], str, str]:
    has_h3 = any((HEADING_RE.match(line.strip()) or [None, ""])[1] == "###" for line in lines)
    if not has_h3:
        items, footer_note = parse_content_lines(lines)
        return ([{"title": title, "items": items}] if items else []), footer_note, ""

    sections: list[dict] = []
    current_title = ""
    current_lines: list[str] = []
    footer_note = ""
    for raw in lines:
        stripped = raw.strip()
        heading = HEADING_RE.match(stripped)
        if heading and heading.group(1) == "###":
            if current_title:
                items, section_footer = parse_content_lines(current_lines)
                if items:
                    sections.append({"title": current_title, "items": items})
                if section_footer:
                    footer_note = section_footer
            current_title = heading.group(2).strip()
            current_lines = []
            continue
        current_lines.append(raw)

    if current_title:
        items, section_footer = parse_content_lines(current_lines)
        if items:
            sections.append({"title": current_title, "items": items})
        if section_footer:
            footer_note = section_footer

    return sections, footer_note, title


def parse_content_lines(lines: list[str]) -> tuple[list[dict], str]:
    items: list[dict] = []
    footer_note = ""
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped == "---":
            continue
        if is_footer_note(stripped):
            footer_note = stripped[1:-1].strip()
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:].strip()
            if text:
                items.append({"type": "bullet", "parts": parse_inline_parts(text)})
            continue
        items.append({"type": "paragraph", "parts": parse_inline_parts(stripped)})
    return items, footer_note


def parse_inline_parts(text: str) -> list[dict]:
    normalized = str(text or "")
    parts: list[dict] = []
    cursor = 0
    for match in INLINE_TOKEN_RE.finditer(normalized):
        start, end = match.span()
        if start > cursor:
            parts.append({"type": "text", "text": normalized[cursor:start]})
        token = match.group(0)
        if token.startswith("**") and token.endswith("**") and len(token) >= 4:
            parts.append({"type": "bold", "text": token[2:-2]})
        elif token.startswith("`") and token.endswith("`") and len(token) >= 2:
            parts.append({"type": "code", "text": token[1:-1]})
        else:
            parts.append({"type": "text", "text": token})
        cursor = end
    if cursor < len(normalized):
        parts.append({"type": "text", "text": normalized[cursor:]})
    if not parts:
        parts.append({"type": "text", "text": normalized})
    return [part for part in parts if part.get("text")]


def format_daily_briefing_date_label(date_key: str) -> str:
    try:
        day = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return date_key
    return f"{day.month}月{day.day}日"


def format_weekday_label(date_key: str) -> str:
    try:
        day = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return ""
    return WEEKDAY_LABELS[day.weekday()]


def is_separator_or_blank(value: str) -> bool:
    stripped = (value or "").strip()
    return not stripped or stripped == "---"


def is_footer_note(value: str) -> bool:
    return value.startswith("*") and value.endswith("*") and not value.startswith("**") and len(value) >= 2


def strip_wrapping_backticks(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        return text[1:-1].strip()
    return text
