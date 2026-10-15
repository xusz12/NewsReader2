# News Reader Workbench

本地新闻阅读工作台。当前稳定版本：`v0.0.1`。版本记录见 [CHANGELOG.md](CHANGELOG.md)。

本项目独立维护，使用新的 Git 历史，不绑定原项目远程仓库或更新源。内部包名为 `newsreader-workbench`，界面名称保留 News Reader。

## 界面与功能

前端严格依据 `news-reader-workbench.html` 设计原型，使用原生 HTML/CSS/JavaScript，由 Flask 直接提供，无需构建。

- 桌面采用 248px 导航、430px 新闻列表和自适应阅读区；920px 以下使用列表/阅读器切换、导航抽屉及底部导航。
- 保留新闻流、稍后再看、重要新闻、收藏、想法，以及单篇新闻中文精读和研究 Agent。
- 新闻总结、日报、提醒、复盘、跟踪、板块已从前端移除；原有后端 API 与数据库结构保持兼容。
- 支持搜索、信源筛选、日期分组、分页、批量标读、划词引用和想法编辑。
- 支持浅色、深色、跟随系统、墨水屏主题；设置采用原型中的右侧抽屉。
- 快捷键：`j/k` 浏览、`m` 标读、`i` 重要、`s` 稍后、`f` 收藏、`a` Agent、`/` 搜索、`,` 设置、`?` 帮助。

实现说明见 [docs/ui-refactor.md](docs/ui-refactor.md)。生产页面使用真实 API；设计示例新闻仅用于测试预览。

## 安装与启动

需要 Python 3.10+。新闻研究 Agent 另需 Node.js 24+，及已安装、配置的 Pi 或 Codex CLI。

在项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
./启动NewsReader.command
```

macOS 也可在 Finder 中双击 `启动NewsReader.command`。脚本优先使用本项目 `.venv`，检查依赖和服务健康后打开浏览器；同一项目重复启动会复用自身服务。终端等价入口为 `python3 updater.py --serve`，也可用 `python3 app.py` 直接启动。

默认端口为 `8080`。Finder 启动器在未指定 host 时会尝试可用的 Tailscale IPv4，否则使用 `127.0.0.1`。与其他本机服务并行运行时可指定另一端口：

```bash
NEWS_READER_HOST=127.0.0.1 NEWS_READER_PORT=8081 ./启动NewsReader.command
```

可选安装并配置 `opencli`，用于新闻正文抓取。Node 依赖使用锁文件中的固定版本，不会在启动时自动安装或升级。

## 数据与配置

- 新闻输入为 `dailyFreshNews_*.md`；同目录存在有效的同名 `.newsreader.json` 时优先读取结构化数据，否则回退到 Markdown。应用不写回原始新闻文件。
- 默认输入目录仍为本机 `DailyNews`，可通过 `NEWS_READER_DAILY_NEWS_DIR` 指定。新闻输入可以共享，阅读状态与想法默认独立保存。
- 默认数据库为**本项目目录下的 `news_index.sqlite3`**，不再自动读取父目录的旧数据库。需要使用其他库时必须在设置或环境变量中明确指定。
- 本地配置位于 `app_settings.json`，不纳入 Git。模型与信源可在设置抽屉配置。
- Keychain 使用独立账号 `newsreader-workbench`。原项目保存的 DeepSeek 密钥不会自动迁移；请在新项目设置中重新保存，或显式设置 `DEEPSEEK_API_KEY`。
- Pi/Codex 沿用本机 CLI 的登录与配置；应用会话数据库和临时目录独立保存，不迁移旧项目会话。
- 数据库、缓存、密钥、虚拟环境、`node_modules` 和测试产物不纳入 Git。

可用环境变量：

| 变量 | 用途 |
| --- | --- |
| `NEWS_READER_HOST` / `NEWS_READER_PORT` | 服务监听地址和端口 |
| `NEWS_READER_PYTHON` / `NEWS_READER_NODE` | Python / Node 可执行文件 |
| `NEWS_READER_DAILY_NEWS_DIR` | 新闻输入目录 |
| `NEWS_READER_DB_PATH` | 显式数据库路径，优先于设置 |
| `NEWS_READER_APP_SETTINGS_PATH` | 应用设置文件 |
| `NEWS_READER_MEDIA_CACHE_DIR` | 媒体缓存目录 |
| `NEWS_READER_AGENT_DB_PATH` | 临时研究会话数据库 |
| `NEWS_READER_AGENT_RUNTIME_DIR` | Agent 临时运行目录，默认按项目隔离 |
| `NEWS_READER_PI_AGENT_DIR` | Pi 配置来源；默认继承本机 Pi 配置 |
| `NEWS_READER_CODEX` | Codex CLI 可执行文件 |

环境变量名称保留兼容。只有显式指定相同路径，才会让不同项目使用同一数据文件。

## 新闻研究 Agent

设置中可选择 Pi 或 Codex，分别保存模型、provider、推理强度及连接选项。切换模型只影响新会话；正在进行的会话保留启动时的配置。支持流式状态、停止、重试、新会话及归档到想法。连接测试使用当前草稿配置。

Pi 使用 OAR/Pi SDK 或兼容 CLI 路径。Codex 使用本机 CLI；应用不复制账号凭据，显式设置 `read-only` 文件沙盒和 `approval_policy=never`。本机扩展、MCP 与联网能力仍取决于实际配置。未配置 CLI 或凭据不影响基础阅读。

Agent 会话默认保留 72 小时，可在设置中改为 24 小时；运行中的任务不被 TTL 清理。应用仅保存自身会话映射，不删除本机 Codex 全局历史。连接失败会显示实际错误，不使用演示成功结果。

## 版本与更新

版本统一为 `v0.0.1`；npm 包版本按规范为 `0.0.1`。`version.json` 的 `repository` 留空，因此检查更新不会访问原仓库，确认更新接口也不会启动更新进程。

后续建立本项目的新仓库后，可显式配置 `version.json.repository` 为新 GitHub 仓库的 HTTPS 地址，并重启服务。Git 远程地址需单独配置。受控更新只读取该仓库的稳定版本 tag，验证目标提交和 manifest 后快进更新，拒绝脏工作树、分叉和 detached HEAD。

## 测试与设计预览

```bash
PYTHONPATH=. python3 -m pytest -q
node --test tests/error_ui.test.cjs
npm run test:oar
npx playwright install chromium
npm run test:ui
```

浏览器测试使用临时 SQLite 与测试信源，新闻、阅读状态、想法和配置走真实 API；模型执行与文件选择使用隔离响应。覆盖 360 至 1920 像素宽度，截图写入 `.ui-artifacts/`。

```bash
python3 tests/ui/server.py
# 浏览器访问 http://127.0.0.1:8765
```

预览使用设计示例新闻；日常使用请通过正常启动入口加载自己的新闻。
