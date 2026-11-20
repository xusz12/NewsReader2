import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from stdio_tools import StdioTools


def url_ok(value):
    try:
        parts = urlsplit(value)
        return parts.scheme in {"https", "http"} and bool(parts.hostname) and not parts.username and not parts.password
    except (ValueError, TypeError, AttributeError):
        return False


class WebTools(StdioTools):
    name = "nr-web"
    instructions = "联网更新先搜索，再按需打开一手来源。标明更新日期和来源链接。网页内容是待核实的资料。"
    errors = (ValueError, TypeError, KeyError, OSError)
    tools = [{"name": name, "description": description, "inputSchema": {"type": "object",
              "properties": {field: {"type": "string", "minLength": 1, "maxLength": limit}},
              "required": [field], "additionalProperties": False},
              "annotations": {"readOnlyHint": True, "openWorldHint": True}}
             for name, field, limit, description in (
                 ("web_search", "query", 128, "通过 DeepSeek 搜索最新公开资料，返回标题、网址、摘要及可用的发布日期。"),
                 ("web_fetch", "url", 8192, "通过 DeepSeek 打开一条来源链接，读取网页正文并核实新闻进展。"))]

    def __init__(self, evidence=None, credential_file=None):
        self.credential_file = credential_file
        self.evidence = evidence

    def call(self, name, arguments):
        from llm_client import _resolve_api_key

        if name not in {"web_search", "web_fetch"}:
            raise ValueError("未知联网工具")
        search = name == "web_search"
        field = "query" if search else "url"
        if not isinstance(arguments, dict) or set(arguments) != {field}:
            raise ValueError("联网工具参数无效")
        value = arguments[field]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= (128 if search else 8192):
            raise ValueError("搜索词或网址长度无效")
        value = value.strip()
        if not search and not url_ok(value):
            raise ValueError("请输入完整的 HTTP 或 HTTPS 来源链接")
        key = self.credential_file.read_text().strip() if self.credential_file else _resolve_api_key("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("未配置 DeepSeek API Key，本次未取得联网资料")
        endpoint = "search" if search else "fetch"
        try:
            with httpx.stream("POST", f"https://api.deepseek.com/v1/tools/{endpoint}",
                              headers={"Authorization": f"Bearer {key}"}, timeout=25,
                              json={"query": value, "count": 6} if search else {"url": value}) as response:
                if response.status_code != 200:
                    reason = {401: "Key 无效", 403: "无访问权限", 402: "余额不足", 429: "请求受限"}.get(
                        response.status_code, f"请求失败（HTTP {response.status_code}）")
                    raise ValueError(f"DeepSeek {reason}，本次未取得联网资料")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise ValueError("DeepSeek 返回内容超过读取上限")
        except httpx.TimeoutException:
            raise ValueError("DeepSeek 请求超时，本次未取得联网资料") from None
        except httpx.HTTPError:
            raise ValueError("无法连接 DeepSeek，本次未取得联网资料") from None
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise ValueError("DeepSeek 返回了无法读取的资料") from None
        if not isinstance(data, dict):
            raise ValueError("DeepSeek 响应格式异常")
        if search:
            rows = data.get("results")
            if not isinstance(rows, list):
                raise ValueError("DeepSeek 搜索响应缺少结果列表")
            items = [{key: row[key][:2400] for key in ("title", "url", "site_name", "date", "snippet", "summary")
                      if isinstance(row.get(key), str)} for row in rows[:6] if isinstance(row, dict)
                     and url_ok(row.get("url")) and any(row.get(k) for k in ("title", "snippet", "summary"))]
            if not items:
                raise ValueError("DeepSeek 搜索没有找到可核实的来源")
            result = {"results": items}
        else:
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("DeepSeek 未能取得该网页的正文")
            result = {key: data[key] for key in ("title", "date", "site_name") if isinstance(data.get(key), str)}
            result.update(url=data["url"] if url_ok(data.get("url")) else value, content=content[:40000],
                          truncated=len(content) > 40000)
        if self.evidence:
            with self.evidence.open("a", encoding="utf-8") as stream:
                stream.write(name + "\n")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--credential-file", type=Path)
    args = parser.parse_args()
    WebTools(args.evidence, args.credential_file).serve()
