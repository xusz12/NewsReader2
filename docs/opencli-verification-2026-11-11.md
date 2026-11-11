# OpenCLI 集成验收记录（2026-11-11）

预验收开始时间：2026-11-11 01:04:18，北京时间。当时工作区版本标记为 v0.5.3；本次发布版本于 2026-11-12 调整为 v0.5.4。以下保留 11 月 11 日的实测数据，版本调整后的结果见 [v0.5.4 验收记录](opencli-verification-2026-11-12.md)。预验收时尚未提交或推送，原 v0.5.3 标签保持历史指向。安装与迁入说明见[集成说明](opencli-integration.md)。

## 固定组合

- OpenCLI：`2.0.3`；npm 声明、锁文件及实际安装版本一致。
- 自定义 adapter：`0.9.2+newsreader.1`；上游提交 `c28502779314773797d92306c9bebbc9fe03b6f0`，Reuters 视频转录排除补丁及原始哈希另行记录。
- Browser Bridge：1.0.27，Chrome 已连接、X 登录检查通过。
- 本机运行环境：Node v26.3.1，Python 3.13.1。
- 本次运行时代码指纹：`22babf12914a81b6a84082bf76e5ae28edb0a14f21914584fecf07879f00fe40`。

## 干净安装与隔离

把待交付源码放入全新目录，排除个人设置、数据库、node_modules 和其他忽略产物，执行 `npm ci --ignore-scripts`，成功安装锁定依赖。随后从这份干净源码副本执行全部采集和正文验收。

- PATH 中的 opencli 替换为失败哨兵；实际没有触发该哨兵。
- Node 加载钩子拒绝项目目录外的模块，包括全局 opencli 与个人 adapter/plugin；没有修改 HOME。
- 浏览器桥接由干净副本内的 OpenCLI 启动。浏览器扩展和登录会话仍使用本机现有配置。
- 数据库、设置和媒体路径均指向临时目录；不向真实新闻库写测试内容，不调用翻译或评论总结模型。
- 个人 adapter Git 工作区仍保持干净。没有靠移动、恢复或重新注册个人 adapter 取得成功。

## 全部采集入口

| 入口 | 有效条数 | 结果 |
| --- | ---: | --- |
| middle-east | 10 | 通过 |
| china | 10 | 通过 |
| world | 10 | 通过 |
| business | 10 | 通过 |
| technology | 10 | 通过 |
| bloomberg_tech | 15 | 通过 |
| techcrunch | 10 | 通过 |
| arstechnica | 10 | 通过 |
| Ilya Sutskever | 10 | 通过 |
| 郭明錤 | 10 | 通过 |
| 外汇交易员 | 8 | 通过 |
| WaylandZhang | 10 | 通过 |

合计 123 条。核对标题、HTTPS URL、目标域名、来源归属、时间格式和 X 帖子身份；缺失时间允许按既有契约保持未知。条数是实际获取数量，不要求所有账号恰好有 10 条。

## 正文场景

| 场景 | 正文字符数 | 长文命令返回字符数 | 结果 |
| --- | ---: | ---: | --- |
| [Reuters](https://www.reuters.com/world/israels-parliament-sworn-eisenkot-tries-forge-government-2026-11-10/) | 4206 | — | 通过 |
| [Bloomberg](https://www.bloomberg.com/news/articles/2026-11-10/alibaba-s-chip-design-unit-t-head-files-for-hong-kong-listing) | 3633 | — | 通过 |
| [TechCrunch](https://techcrunch.com/2026/11/10/nvidia-ceo-jensen-huangs-affair-with-alleged-chinese-spy-a-timeline/) | 8135 | — | 通过 |
| [Ars Technica](https://arstechnica.com/science/2026/11/carbon-emissions-are-down-this-year-but-that-probably-wont-last/) | 3540 | — | 通过 |
| [Reuters list-style article](https://www.reuters.com/business/media-telecom/openai-limit-weapons-information-teens-chatgpt-violence-prevention-push-2026-10-30/) | 1647 | — | 通过 |
| [Reuters embedded video article](https://www.reuters.com/world/israels-parliament-sworn-eisenkot-tries-forge-government-2026-11-10/) | 4206 | — | 通过 |
| [X thread](https://x.com/ilyasut/status/2094881278621253755?s=20) | 752 | — | 通过 |
| [X note tweet](https://x.com/mingchikuo/status/2078159616832368941) | 8779 | 1906 | 通过 |
| [X long article](https://x.com/stripe/status/2037197998074335292) | 4755 | 3851 | 通过 |

X 正文长度包含主帖、可用回复与长文组合；Note Tweet 与独立 Article 分别验证。X 图片缓存和评论总结未在本轮真实网站测试中执行，相关保存与错误处理由应用离线测试覆盖。

最终内容复核发现 Reuters 曾把 VideoTranscript 组件混入报道。内置补丁按 DOM 容器排除该组件，页面实际 19 个报道段落逐段核对为 19/19，正文从 7202 字符恢复为 4206 字符。列表式样本的 7 个条目均保留，固定验收同时检查这些内容与转录禁止项。

## 自动化与升级验证

| 检查 | 通过数 |
| --- | ---: |
| 内置 adapter 回归（7 个文件，包括新增 6 项视频转录边界） | 369 |
| 应用、必需命令、发布及依赖更新 pytest | 180 |
| 设计 Token 检查 | 2 |
| 启动器与受管升级／回退 | 32 |
| Node 项目入口、个人覆盖隔离与缺依赖负例 | 5 |
| OAR / Pi SDK 既有回归 | 34 |
| 设置与采集界面（桌面、手机、浅／深／系统／墨水屏） | 42 |

另在独立 Git 夹具中实际执行目标锁文件 npm 安装、必需命令校验、依赖切换和回退；确认准备阶段保留旧依赖，回退同时恢复旧提交与旧依赖。对应记录为本机忽略产物 `output/opencli-dependency-transaction.json`。

推送前检查查询 npm 上游，确认 stable/latest 仍为 2.0.3；完整记录与当前代码指纹匹配。本仓库已启用 pre-push，并保留原有中文提交信息 hook。GitHub Actions 配置已新增，本轮未推送，远端 CI 尚未执行。

机器可读记录：[collection/opencli-release.json](../collection/opencli-release.json)，该文件随最新版本验收更新，11 月 11 日的预验收指纹与数据以上文为准。安装、更新规则和复测命令：[OpenCLI 集成说明](opencli-integration.md)。

结论适用于记录中的固定版本、浏览器环境及样本，不把环境检查或非空正文视作所有未来文章均完整的保证。新机器仍须准备 Chrome、Browser Bridge、账号登录和所需模型配置。
