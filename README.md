# News Reader Workbench

本地新闻阅读与研究工作台，汇集媒体报道和你关注的 X 账号发布的帖子。桌面和手机都能阅读，进度、收藏和想法保存在运行服务的电脑上。

当前稳定版本：`v0.6.5`。[版本记录](CHANGELOG.md)

## 界面与功能

| 想做什么 | 怎么用 |
| --- | --- |
| [采集新闻](docs/native-collection.md) | 按时或手动采集，抓取结束后就绪新闻先入库；失败项单独重试。在「采集记录」按日期查看各来源的收录数量和错误。 |
| [阅读与笔记](docs/reading-and-research.md#阅读与笔记) | 按来源或分类筛选，切换中英双语正文，划线写想法，或加入「稍后再看」。 |
| [研究单篇报道](docs/reading-and-research.md#新闻研究-agent) | 引用新闻段落向 Pi 或 Codex 提问，把讨论结论保存到这篇新闻的想法中。 |
| [追踪事件](docs/reading-and-research.md#主题追踪) | 在「主题追踪」定义事件，沿时间线查看新收录的报道和重要进展。 |
| [整理积压新闻](docs/reading-and-research.md#积压新闻总结) | 在「新闻总结」预览未读范围，生成可回溯来源的报告，成功归档后标读。 |
| [阅读定期报告](docs/periodic-reports.md) | 在「日报」查看早报、午报、晚报、周报和月报，展开来源即可回到原文。 |
| [使用个人助手](docs/personal-assistant.md) | 在顶栏开启独立讨论，查询本地新闻库，查看或修正助手长期记住的偏好。 |

## 安装与启动

在 macOS 上准备 Git、Python 3.10+、Node.js 24+ 和 npm。首次安装时，在项目根目录运行以下命令。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
npm run check:collection
./启动NewsReader.command
```

启动器会自动打开浏览器，默认端口为 `8080`。以后可直接在 Finder 中双击 `启动NewsReader.command`。其他启动方式和监听地址见[安装运行与维护](docs/handoff/03-operations.md#启动方式的区别)。

首次采集时，在服务所在电脑上连接 Chrome 的 OpenCLI 扩展，到「设置 → API 与运行环境 → 采集环境检查」确认连接。在「采集与处理」选择正文翻译服务，然后回到「采集排期」点击「立即采集」。浏览器来源需要 Chrome 保持运行，公开 RSS 可独立采集。[OpenCLI 安装与升级](docs/opencli-integration.md) · [分类与翻译设置](docs/reading-and-research.md#新闻分类与翻译)

## 数据与配置

- 在「设置 → 阅读偏好与维护 → 数据库位置」选择已有的 SQLite 新闻库，保存本页并重启即可沿用阅读状态、收藏、想法和已缓存正文。[数据库选择顺序](docs/data-compatibility.md#继续使用原数据库)
- 应用设置保存在 `app_settings.json`，API 密钥可在系统钥匙串中保存。个人助手的讨论和记忆另存于 `personal-assistant/`，备份时一并保存。[数据备份](docs/handoff/04-data-migration.md#制作一致性备份) · [助手备份](docs/personal-assistant.md#备份与验证)
- 用启动器运行时，可到「设置 → 阅读偏好与维护 → 版本与更新」检查更新。自动更新需要干净的 Git 分支。[升级与回退](docs/handoff/03-operations.md#发布与回退)

## 开发与维护

| 任务 | 入口 |
| --- | --- |
| 接手项目，查配置或排障 | [项目交接总览](docs/handoff/README.md) · [环境变量](docs/handoff/03-operations.md#配置清单) |
| 修改后端功能，找对应测试 | [后端开发地图](docs/backend-development.md) |
| 修改界面或共享组件 | [设计规范](docs/design-guidelines.md) · [组件与 Token 映射](docs/design-token-mapping.md) |
| 运行 Python、OAR 和浏览器测试 | [测试命令与环境](docs/handoff/03-operations.md#开发与测试) |

使用示例新闻预览界面，可运行 `python3 tests/ui/server.py`，然后打开 `http://127.0.0.1:8765`。
