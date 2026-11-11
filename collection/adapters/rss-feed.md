# NewsFlow RSS collectors

`BloombergUser tech`、`TechcrunchPublic news`、`ArsPublic news` 共用本目录的 `rss-feed.js`。没有改动 BloombergUser 的 main/politics/economics 或原来的共享 utils.js。

- 每次调用只发一次 HTTP 请求；没有 adapter 内部重试或浏览器导航。
- 请求和响应读取共用 15 秒超时，超时通过 AbortController 取消。
- 验证 RSS 2.0 外层结构、条目完整性、有效标题和绝对 HTTP(S) 链接。HTML/人工验证/格式错误不会伪装成空列表成功。
- 保留字段：rank/title/time/url；Bloomberg 另保留 summary。时间为 Asia/Shanghai，limit 默认 10、上限 30。
- 解码十进制/十六进制 Unicode 字符实体，保留标题中转义的尖括号。
- 失败使用 opencli 原有 CliError 和标准错误封装，code/hint 可读；cause 是版本化 JSON 元数据（newsflow.rss-error/v1），携带 HTTP 状态、是否暂时性失败及 Retry-After 的 UTC 截止时间。不修改 opencli 核心序列化逻辑。
- NewsFlow 对三个 RSS 来源分别设置 25 秒命令超时，最多补试一次，等待 5–7 秒或服务器要求的更长时间。超过 30 秒等待预算时记录冷却时间并继续其他来源，不提前重试。独立的新运行不会继承冷却状态。

测试：

```sh
npx --yes vitest@5.0.4 run --root "$HOME/.opencli/clis" rss-feed.test.js --cache=false
```

配套实现见 [NewsFlow v1.2.4](https://github.com/xusz12/NewsFlowSkills/tree/v1.2.4)。这三个 adapter 由本仓库提供，不包含在 NewsFlow 的安装 payload 中。
