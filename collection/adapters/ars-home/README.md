# ArsPublic article

本目录的 `article` 为 NewsReader 使用的正文 adapter；`top10.js` 提供 `ArsPublic news` 列表命令，其策略见 [RSS 列表](../rss-feed.md)。

- 直接打开文章，显式设置 `navigateBefore: false`。
- 导航、浏览器调用、正文加载共用 30 秒总预算。NewsReader 单次命令超时为 45 秒。
- 仅短暂导航错误允许再导航一次，间隔至少 5 秒；权限、参数和永久错误不重试。
- 普通正文加载每秒读取一次 DOM，不刷新页面、不调用文章 API。使用 `evaluateOnce`，由 adapter 自己控制恢复次数。
- 短暂 DOM 上下文/连接错误最多失败 3 次，之间等待 1 秒；错误次数不会被中间的正常加载状态清零。
- 发现可见验证码、人工验证或访问要求立即返回 `AUTH_REQUIRED`，即使页面已有较长预览正文。不等待验证自行通过。
- 超时返回 `TIMEOUT`，耗尽的短暂浏览器错误返回 `BROWSER_CONNECT`，两者交给 NewsReader 已有的 5 秒 / 15 秒任务重试。最多 3 次任务尝试，对应 adapter 层最多 6 次导航；页面子资源和浏览器底层恢复不计入此数。
- 无法取消已发出的浏览器桥接调用；总预算到期后不会发起后续调用。

测试使用 Vitest：

```sh
npx --yes vitest@5.0.4 run --root "$HOME/.opencli/clis" ars-home/article.test.js --cache=false
```

测试包含正常正文/元数据、无效 URL、人工验证与误判防护、导航次数及间隔、DOM 上下文恢复、慢调用和总预算。
