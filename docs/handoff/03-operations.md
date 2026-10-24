# 安装运行与维护

首次接手以 macOS、本机回环地址和全新数据目录为基准。Python 需要 3.10+，Node.js 需要 24+；Node 用于 OAR/Pi、编辑器构建和浏览器测试，基础阅读主要依赖 Python。Linux 需另验密钥存储、文件选择和启动器；Windows 原生运行会受到 `fcntl` 等 POSIX 依赖限制。

## 获取源码

有仓库权限时，克隆仓库并确认交付基线：

```bash
git clone https://github.com/xusz12/NewsReader2.git newsreader-workbench
cd newsreader-workbench
git show --no-patch --oneline v0.0.15
git rev-parse v0.0.15^{commit}
```

应用代码基线为 `de1dec7fe15a536ef6fc802df02fbfafbf30566e`。交接文档由后续文档提交纳入主分支，原 v0.0.15 标签中没有本目录；要获取带交接文档的完整源码，请克隆主分支，或使用包内 `HANDOFF-MANIFEST.json` 的 `source_commit` 定位交付快照。ZIP 解压也可运行和开发，但没有 Git 历史，不能直接使用应用内自动升级。

## 首次启动

以下命令从项目根目录执行。示例数据写入已被 Git 忽略的 `output/handoff-demo/`，与真实新闻库隔离。重复操作不会覆盖已有示例配置；重新演练应另选一个空目录。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts

source docs/handoff/examples/runtime.env.example
mkdir -p "$NEWSREADER_DATA_ROOT/daily" "$NEWSREADER_DATA_ROOT/briefings"
cp -n docs/handoff/examples/app-settings.example.json "$NEWS_READER_APP_SETTINGS_PATH"
cp -n docs/handoff/examples/dailyFreshNews_2026-10-21.md "$NEWS_READER_DAILY_NEWS_DIR/"
cp -n docs/handoff/examples/dailyFreshNews_2026-10-21.newsreader.json "$NEWS_READER_DAILY_NEWS_DIR/"
python3 app.py
```

打开 `http://127.0.0.1:8080`。合成样例提供两条索引新闻，不含可抓取的真实媒体 URL、全文或模型结果。页面会请求增量导入；也可以在另一个终端验证：

```bash
curl --fail http://127.0.0.1:8080/api/version
curl --fail -X POST http://127.0.0.1:8080/api/reindex \
  -H 'Content-Type: application/json' -d '{"full":false}'
curl --fail 'http://127.0.0.1:8080/api/news?collection=feed&read_filter=all&per=10'
```

预期版本为 v0.0.15、新闻总数为 2、重复增量导入不重复增加记录。测试收藏、想法及重新启动后的保存效果后，再接入真实数据副本。

## 启动方式的区别

| 入口 | 用途与行为 |
| --- | --- |
| `python3 app.py` | 直接启动 Flask、初始化数据库和工作线程；适合首次演练，不支持受控升级 |
| `python3 updater.py --serve` | 通过 supervisor 管理实例、重启、健康检查和升级；默认本机监听 |
| `./启动NewsReader.command` | macOS 入口，优先使用项目 `.venv`，检查依赖并打开浏览器；未指定 host 时尝试 Tailscale IPv4 |
| `python3 tests/ui/server.py` | 临时测试库和模型替身；端口 8765，仅用于演示及测试 |

日常受控启动前，加载接手方环境文件，再执行 `python3 updater.py --serve` 或 `.command`。新开终端不会继承上一终端的导出变量；Finder 双击也不会自动加载 shell 环境文件。**本项目不自动读取 `.env`**，示例文件必须显式 `source`，或由服务管理器注入环境。

需要固定本机访问时显式设置 `NEWS_READER_HOST=127.0.0.1`。Tailscale 仅改变网络可达性，不提供应用内用户权限。停止测试服务时在启动终端按 Ctrl+C；不要用按进程名批量结束 Python 的方式停服务。

## 配置清单

| 变量 | 默认或作用 | 接手要求 |
| --- | --- | --- |
| `NEWS_READER_HOST` / `NEWS_READER_PORT` | 直接启动默认 127.0.0.1 / 8080 | 明确监听范围，端口冲突时更换 |
| `NEWS_READER_DAILY_NEWS_DIR` | `settings.py` 内原开发者的 DailyNews 绝对路径 | 必须指定接手方目录，递归扫描 |
| `NEWS_READER_DAILY_BRIEFING_DIR` | 原默认 DailyNews 下 `briefings/daily` | 兼容日报如需使用应单独设置；不会自动随新闻目录覆盖而变化 |
| `NEWS_READER_DB_PATH` | 环境变量 → 设置中的 path → 已存在的父目录库 → 项目内新库 | 明确设置，避免误开父目录历史库 |
| `NEWS_READER_APP_SETTINGS_PATH` | 项目根目录 `app_settings.json` | 建议放到专用数据目录 |
| `NEWS_READER_MEDIA_CACHE_DIR` | 主库同级 `media-cache` | 保留与数据库媒体索引的配套关系 |
| `NEWS_READER_AGENT_DB_PATH` | 主库同级 `agent_sessions.sqlite3` | 不同部署应使用独立路径 |
| `NEWS_READER_AGENT_RUNTIME_DIR` | 系统临时目录下按项目路径哈希隔离 | 临时会话状态，不作为长期业务备份 |
| `NEWS_READER_UPDATER_DIR` | 系统临时目录下项目隔离路径 | 更新锁及 `news-reader-updater-state.json` 所在位置 |
| `NEWS_READER_PYTHON` | macOS 启动脚本使用的 Python | 可指定依赖已安装的虚拟环境解释器 |
| `NEWS_READER_NODE` | PATH 中的 node | OAR/Pi 需要 Node 24+ |
| `NEWS_READER_CODEX` | PATH 中的 codex | 使用接手方安装及登录的 CLI |
| `NEWS_READER_PI_AGENT_DIR` | Pi 配置来源覆盖 | 不配置时继承本机 Pi；迁移扩展和信任配置需单独核验 |
| `DEEPSEEK_API_KEY` | 环境变量优先，否则 macOS Keychain | 由接手方设置；不要放进源码或交付说明 |
| `NEWS_READER_LLM_MODEL` | 空时由代码使用翻译默认模型 | 显式保存的模型设置需与服务可用目录核对 |

`NEWS_READER_MANAGED`、`NEWS_READER_INSTANCE_ID`、`NEWS_READER_SUPERVISOR_SOCKET`、`NEWS_READER_SUPERVISOR_TOKEN` 由启动器生成，不应作为手工配置或对外分发。Keychain 账号为 `newsreader-workbench`，服务名使用 `DEEPSEEK_API_KEY` 等；非 macOS 环境不能依赖设置页的 Keychain 写入。

应用设置模板来自代码默认值，包含 llm、agent、feed、database、news_summary、tracked 配置。模型默认留空，接手方应在设置页选择和测试。Pi/Codex 沿用各自本机配置与认证；应用不会替团队完成账号登录。

## 正文抓取和模型接入

opencli 不包含在 Python 或 npm 依赖清单中。交付方需另提供安装来源、可复现版本及适配器；浏览器扩展、网络和媒体订阅登录由接手方准备。当前命令映射为：

| 来源 | 命令前缀 | 超时 |
| --- | --- | --- |
| Reuters | `opencli reuters article-detail` | 45 秒 |
| Bloomberg | `opencli bloomberg news` | 90 秒 |
| TechCrunch | `opencli TechcrunchPublic article` | 45 秒 |
| Ars Technica | `opencli ArsPublic article` | 45 秒 |
| X 线程 / 长文 | `opencli twitter thread` / `opencli twitter article` | 90 / 60 秒 |

代码兼容 opencli 2.x 的 `ok/data/error` 包装以及旧列表/对象。具体调用参数见 `app.py` 的抓取函数。先在接手方终端确认这些适配器可用，再通过页面用少量真实文章验证。

设置中分别验收：翻译模型、Pi/Codex 研究模型、模型目录刷新、连接测试、单篇回答和新闻总结。单篇研究使用实时外部检索的能力取决于所选 CLI/provider；总结使用锁定材料。未配置模型和 opencli 不影响纯索引阅读。

## 开发与测试

```bash
PYTHONPATH=. python3 -m pytest -q
node --test tests/error_ui.test.cjs tests/item_state.test.mjs tests/read_later.test.mjs tests/oar_bridge.test.mjs
npx playwright install chromium
CI=1 npm run test:ui
```

先释放测试端口 8765；`CI=1` 禁止复用未知的现有测试服务。浏览器测试运行临时数据和模型替身，截图写入 `.ui-artifacts/`，失败 trace 写入 `test-results/`。本次运行结果及失败定位见 05，接手方应保留自己的验收结果。

普通前端无需构建。修改 `frontend/idea-markdown-editor.js` 或相关 ProseMirror 依赖后执行 `npm run build:idea-editor`，同时检查生成 JS、CSS 和 LICENSE 文件的差异。npm 依赖由锁文件固定；Python 的 `certifi` 为下限约束，其他列出的 Python 依赖固定版本。

## 排障入口

| 现象 | 首先检查 |
| --- | --- |
| 空列表 | 实际输入目录、严格日期文件名、Markdown 入口是否存在、增量索引统计、来源及已读筛选 |
| 换库未生效 | 环境变量是否覆盖设置，是否已重启，设置页显示的当前有效路径 |
| 启动健康检查失败 | 端口占用、服务终端输出、localhost DNS 和系统代理；本地请求应绕过代理 |
| 抓取失败 | opencli 是否在服务 PATH，适配器大小写、浏览器连接、登录状态和错误详情 |
| Agent 不可用 | Node 版本、npm 依赖、CLI 登录、模型选择、Pi 项目信任及扩展 |
| 总结失败 | 预览是否过期、库或配置是否变化、引用校验错误、模型结果及 job.error |
| 更新按钮不可用 | ZIP/detached HEAD/脏工作区、本地分叉、启动入口、更新源和远端稳定 tag |
| 媒体缺失 | 主库中的媒体索引和缓存目录是否一起迁移，缓存路径是否可写 |

服务日志默认输出到启动终端；需要长期日志时由接手方服务管理器采集。顶栏报错弹窗读取的是上游日报错误，不能替代服务端日志。更新进度位于 updater 运行目录。收集故障材料时保留错误码、步骤、版本、时间及相关日志，不附认证令牌和真实用户正文。

## 发布与回退

1. 在开发分支工作，默认前缀 `codex/`；Git 提交信息使用简体中文，例如 `docs: 补充项目交接文档`。
2. 修改版本时同步 `version.json`、`package.json`、`package-lock.json`、`static/index.html` 的标题/标记/资源版本、README、CHANGELOG 及版本一致性测试。从 v0.1.2 之后的版本开始，CHANGELOG 根据实际发布差异按 `feat`、`improve`、`fix` 分组，具体规则见 [CHANGELOG 发布分类](../design-guidelines.md#10-changelog-发布分类)。
3. 完成相关测试，并在数据副本上验证迁移。更换仓库后必须更新 `version.json.repository`，只改 Git remote 不会改变应用更新源；同时更新硬编码该仓库的测试和文档。
4. 审查后提交并推送版本及稳定 tag。更新器接受 `vN.N.N` 或 `vN.N.N.N`；本地改版本号不产生远端更新。发布与账号转移由新负责人执行。
5. 已安装实例在干净分支、受控启动条件下通过界面确认升级。升级器快进代码并健康检查，失败尝试恢复旧提交。依赖变化仍需人工安装，升级器不安装依赖。

手工回退使用独立旧版本 checkout，加上升级前备份恢复出的数据副本；验证后再切换服务路径。不要在共享工作区强制 reset，不要认为退回旧源码就能撤销数据库 DDL。
