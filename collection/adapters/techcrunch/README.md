# TechcrunchPublic article

本目录的 `article` 为 NewsReader 使用的 HTTP 正文 adapter；`latest.js` 提供 `TechcrunchPublic news` 列表命令，其策略见 [RSS 列表](../rss-feed.md)。

- 保留正常文章解析和 success/failed 行格式，通过 opencli 2.0 的 `ok/data` 返回。
- 请求和响应正文读取共同受 8 秒超时控制，超时使用 AbortController 取消；一次命令总预算 30 秒，低于 NewsReader 的 45 秒。
- 网络/读取失败、超时及 HTTP 408、429、500、502、503、504 最多补发一次请求，至少等待 5 秒。
- `Retry-After` 支持秒数和 HTTP 日期，冷却从收到响应头时开始计算；正文读取失败或卡住也不会丢失已经收到的冷却要求。
- 401/403、404、验证 HTML、解析失败不会内部重试。直接 HTTP 无法完成人工验证，不套用 Reuters 的验证等待流程。
- 若服务器冷却无法放入总预算，或最后一次请求仍要求冷却，返回 `RETRY_AFTER` 并给出 UTC 截止时间。该错误在当前 NewsReader 中终止自动重试，需要到时手动重试；不会用默认 5 秒重试绕过服务器要求。
- 普通网络/超时/可恢复 HTTP 错误仍交给 NewsReader 的最多 3 次任务尝试（任务间隔 5 秒 / 15 秒）。每个任务最多 2 次 HTTP 尝试，因此最多 6 次 adapter 请求，不含 HTTP 重定向。

测试使用 Vitest；可设置 `NEWSREADER_SOURCE` 对接当前 NewsReader 的真实错误分类函数，测试只用 AST 读取函数，不启动应用或访问数据库：

```sh
NEWSREADER_SOURCE='/path/to/newsreader/app.py' npx --yes vitest@5.0.4 run --root "$HOME/.opencli/clis" techcrunch/article.test.js --cache=false
```

测试覆盖正常/失败记录、请求和正文读取超时及取消、重试间隔/上限、限流日期、长冷却、验证页面、解析错误及 NewsReader 错误分类兼容性。
