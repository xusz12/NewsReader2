# v0.5.4 OpenCLI 集成验收记录（2026-11-12）

实测开始时间：2026-11-12 00:10:53，北京时间。本页记录 NewsReader v0.5.4 的发布前验收结果。

## 固定组合与隔离

- OpenCLI：`2.0.3`；adapter：`0.9.2+newsreader.1`。
- 代码指纹：`c9b860f0c2fe423e4caa726892ed30275f68e6fc35b09ee407192d1fd5c06f90`。
- Chrome、Browser Bridge 1.0.27 及 X 登录检查通过。
- 从当前项目启动桥接；拒绝项目外 JavaScript 模块，并以失败哨兵替换 PATH 中的全局 opencli。未调用全局程序或个人 adapter。
- 使用临时数据库、设置及媒体目录，不向真实新闻库写入验收内容，不调用翻译或评论总结模型。
- 验收完成后，从项目入口重启空闲桥接，恢复常规运行环境。

CLI 与 adapter 代码隔离不代表浏览器连接完全隔离。Chrome、浏览器扩展、登录与桥接服务仍共用；2.0.3 会复用已经运行的较新桥接。外部更新不会改写项目依赖，但桥接重启或兼容性变化可能影响浏览器抓取。

## 全部来源

| 来源 | 有效条数 | 结果 |
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

共 12 个来源、123 条有效记录。

## 正文抓取

| 场景 | 正文字数（字符） | 结果 |
| --- | ---: | --- |
| Reuters | 2145 | 通过 |
| Bloomberg | 4613 | 通过 |
| TechCrunch | 4204 | 通过 |
| Ars Technica | 3888 | 通过 |
| Reuters list-style article | 1647 | 通过 |
| Reuters embedded video article | 4206 | 通过 |
| X thread | 680 | 通过 |
| X note tweet | 8779 | 通过 |
| X long article | 4755 | 通过 |

Reuters 列表条目和视频转录排除样本均通过关键文本校验；X 覆盖普通帖、Note Tweet 与独立 Article。正文长度包含应用组合的可用回复，原始长文长度及哈希保存在机器记录中。

## 自动化检查与边界

| 检查 | 最终通过数 |
| --- | ---: |
| 版本元数据及界面标记一致性 | 2 |
| 内置 adapter 回归 | 369 |
| Node 入口及隔离负例 | 5 |
| 应用、采集、正文、环境、依赖升级与发布回归 | 180 |

首次发布检查中，`test_application_start_resumes_collection_without_a_browser_request` 的线程退出断言未在测试给定的 2 秒等待内满足，结果为 179 通过、1 失败。该用例单独复测通过，完整 180 项回归复测通过，随后完整发布检查再次通过。未修改退出时限或测试断言；本轮未修复这项间歇性测试问题，保留为已知验收事项。

本次只调整版本元数据、前端资源版本与文档；采集运行时代码、OpenCLI 版本和 adapter 与 11 月 11 日预验收一致。干净目录安装、界面主题、依赖切换与回退等此前已完成的检查见 [11 月 11 日预验收记录](opencli-verification-2026-11-11.md)，本轮没有重复执行这些检查。

发布检查确认 npm 上游稳定版为 `2.0.3`，本次全部来源及正文结果与 v0.5.4 当前代码指纹匹配。GitHub Actions 按对应提交执行独立的干净安装与离线检查。

机器记录：[collection/opencli-release.json](../collection/opencli-release.json)。安装、设置内更新与命令解释见 [集成说明](opencli-integration.md)。
