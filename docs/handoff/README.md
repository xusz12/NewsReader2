# News Reader Workbench 项目交接总览

2026-11-12 新增[个人助手第一阶段](../personal-assistant.md)：顶栏独立对话、命名身份、持久 workspace、文件记忆与 NR 全库只读查询。独立于原有临时新闻 Agent，备份时另保留助手数据库与工作区。

2026-11-08 更新：已启用[内置新闻采集](../native-collection.md)，旧版 DailyNews 扫描、刷新索引及文件日报流程退役。原数据库与阅读状态继续使用；当前采集配置、故障恢复和切换步骤以新文档为准。

本交接包用于将 News Reader Workbench 的开发和维护移交给新团队。接手方可先使用独立空库或备份副本启动阅读工作台，再核验历史状态并接入采集、正文抓取、模型服务和发布流程。

## 交付基线

| 项目 | 内容 |
| --- | --- |
| 交接日期 | 2026-10-21，Asia/Shanghai |
| 应用版本 | v0.0.15；npm 包版本 0.0.15 |
| 应用代码基线 | `de1dec7fe15a536ef6fc802df02fbfafbf30566e` |
| 基线分支及标签 | `main` / `v0.0.15`，均为本次检查的本地状态 |
| 仓库与当前更新源 | https://github.com/xusz12/NewsReader2.git |
| 应用形态 | 本地单用户新闻阅读工作台；Python Flask、SQLite、原生前端 |
| 已有主要运行环境 | macOS；本次验证环境见[测试记录](05-quality-and-roadmap.md) |
| 本次改动 | 新增交接文档、配置模板、合成样例及验证记录，在根 README 增加入口；应用行为和版本不变 |

应用代码基线开始检查时工作区干净。交接文档通过后续文档提交纳入 Git，不改变 v0.0.15 的应用代码或移动原发布标签。交付 ZIP 对应的完整源码提交见包内 `HANDOFF-MANIFEST.json` 的 `source_commit`，原应用基线见 `application_baseline_commit`。此包不含 `.git` 历史；需要继续沿用原更新器或查看历史时，应取得仓库权限并克隆仓库。

## 文件导航

| 阅读顺序 | 文件 | 用途 |
| --- | --- | --- |
| 1 | [产品范围与交互约定](01-product.md) | 已交付功能、状态含义、必须保留的交互 |
| 2 | [安装运行与维护](03-operations.md) | 从零启动、环境变量、外部依赖、排障和发布 |
| 3 | [架构与接口说明](02-architecture.md) | 模块职责、数据流、关键接口、修改入口 |
| 4 | [数据迁移与恢复](04-data-migration.md) | 数据资产、备份命令、迁移核验及回退 |
| 5 | [测试结果与后续工作](05-quality-and-roadmap.md) | 实测结果、验证边界、技术债及建议顺序 |
| 6 | [移交验收清单](06-acceptance.md) | 双方责任、账号资产移交和场景验收 |
| 附录 | [全部显式路由](api-routes.md) | 从交付基线代码生成的 HTTP 方法、路径和处理函数索引 |
| 模板 | [环境配置](examples/runtime.env.example) / [应用设置](examples/app-settings.example.json) | 不含凭据的接手方配置起点 |
| 历史样例 | [Markdown 新闻](examples/dailyFreshNews_2026-10-21.md) / [JSON sidecar](examples/dailyFreshNews_2026-10-21.newsreader.json) | 两条旧文件格式合成新闻，供历史核对；当前版本不导入 |

按角色阅读：产品负责人先读 01 和 06；开发负责人先读 02、03 和 05；运维及数据负责人先读 03、04 和 06。

## 交付物与边界

可转交的 ZIP 包包含当前 Git 跟踪的源码、锁文件、测试、设计和品牌资源，以及本目录交接文件。包内 `HANDOFF-MANIFEST.json` 记录基线与文件列表，`SHA256SUMS.txt` 提供逐文件校验值。压缩包旁另有整个 ZIP 的校验值。

`docs/handoff/` 和根 README 的交接入口随源码提交；生成的 ZIP、校验文件及本机原始测试日志位于 Git 忽略的 `output/`，不提交到仓库。交付包与源码仓库是两种分发方式，源码仓库中的交接文档以文档提交为准。

以下资产需要单独办理，当前不包含在源码包内：

- 真实 DailyNews 目录、新闻 SQLite 库、媒体缓存和个人想法。
- 实际 `app_settings.json`、环境变量、Keychain 密钥、Pi/Codex 登录状态和浏览器登录状态。
- 仓库管理权限及浏览器登录、订阅等个人资产；OpenCLI 和所需 adapter 已随项目依赖与源码提供。
- 域名、服务器、Tailscale 网络或模型付费账号的管理权限。

本次只准备交付材料，没有转移账号、发送给外部团队或切换真实服务。账号和数据的移交责任见 06。

## 接手时首先确认

1. **应用已内置采集编排与排期。** 接手机器执行 `npm ci --ignore-scripts` 安装 [项目内 OpenCLI](../opencli-integration.md)，并配置浏览器会话与翻译服务；不再依赖外部 Agent 或新闻文件输入。
2. **先使用新库或备份副本。** 默认数据库可能命中项目父目录的旧库，首次启动必须显式指定数据路径。旧 Markdown／JSON 样例仅保留供历史测试与核对。
3. **数据库会在启动时升级。** 在副本上验证迁移，确认后再切换正式数据；源码回退不等于数据库回退。
4. **当前是单用户可信网络模式。** 代码没有用户登录或租户隔离，HTTP 接口可修改数据、配置及调用本机模型。公网或多用户部署需要另行设计访问控制和任务调度。
5. **模型与抓取属于独立验收项。** 自动化测试通过不代表接手机器的 opencli、订阅登录和模型凭据已经可用。

## 资料优先级

交付源码及本次测试结果用于判断当前实现；根 [README](../../README.md) 和 [CHANGELOG](../../CHANGELOG.md) 说明当前产品及版本演进。界面约束遵循 [项目设计与协作规范](../design-guidelines.md)，视觉基线遵循 [修订设计规范](../../DESIGN_SPEC_AND_TOKENS_Fix.md) 和 [Token 映射](../design-token-mapping.md)。

[UI 重构说明](../ui-refactor.md) 含多版本历史记录，例如早期曾移除新闻总结，v0.0.8 已恢复。阅读旧段落时应结合对应版本，不能把历史描述当成当前功能清单。设计文档提及的 `news-reader-workbench.html` 原型未纳入当前 Git 文件；接手时以实际页面和已纳入的规范为准。
