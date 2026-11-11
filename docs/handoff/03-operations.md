# 安装运行与维护

2026-11-08 更新：已启用[内置新闻采集](../native-collection.md)，旧版 DailyNews 扫描、刷新索引及文件日报流程退役。原数据库与阅读状态继续使用；当前采集配置、故障恢复和切换步骤以新文档为准。

首次接手以 macOS、本机回环地址和全新数据目录为基准。Python 需要 3.10+，Node.js 需要 24+；Node 用于内置采集、正文抓取、OAR/Pi、编辑器构建和浏览器测试，基础阅读主要依赖 Python。Linux 需另验密钥存储、文件选择和启动器；Windows 原生运行会受到 `fcntl` 等 POSIX 依赖限制。当前 v0.5.4 集成的首次升级、新机安装与依赖准备见[集成与升级说明](../opencli-integration.md)。

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
mkdir -p "$NEWSREADER_DATA_ROOT"
cp -n docs/handoff/examples/app-settings.example.json "$NEWS_READER_APP_SETTINGS_PATH"
python3 app.py
```

打开 `http://127.0.0.1:8080`。全新数据库开始为空，不再导入目录中的合成 Markdown 样例。在另一个终端验证服务与采集状态：

```bash
curl --fail http://127.0.0.1:8080/api/version
curl --fail http://127.0.0.1:8080/api/collection
curl --fail 'http://127.0.0.1:8080/api/news?collection=feed&read_filter=all&per=10'
```

版本以当前源码的 `version.json` 为准。按[内置采集说明](../native-collection.md)安装项目 npm 锁定依赖、准备浏览器会话与翻译配置后，点击「立即采集」验证整批入库、重复采集去重及失败重试。只做历史数据迁移演练时，可先在设置暂停定时采集，再验证收藏、想法及重新启动后的保存效果。

## 启动方式的区别

| 入口 | 用途与行为 |
| --- | --- |
| `python3 app.py` | 直接启动 Flask、初始化数据库和工作线程；适合首次演练，不支持受控升级 |
| `python3 updater.py --serve` | 通过 supervisor 管理实例、重启、健康检查和升级；默认本机监听 |
| `./启动NewsReader.command` | macOS 入口，优先使用项目 `.venv`，检查依赖后停止并替换同项目旧受管实例，健康检查通过后打开浏览器；未指定 host 时分别检测独立 Tailscale 与 Surge，依次选择可绑定的客户端地址、可入站的 Surge 节点、本机 127.0.0.1 |
| `python3 launcher.py --replace-existing` | 同样执行旧受管实例替换；直接从终端使用当前 Python，监听地址按环境配置 |
| `python3 tests/ui/server.py` | 临时测试库和模型替身；端口 8765，仅用于演示及测试 |

日常受控启动前，加载接手方环境文件，再执行 `python3 updater.py --serve` 或 `.command`。新开终端不会继承上一终端的导出变量；Finder 双击也不会自动加载 shell 环境文件。**本项目不自动读取 `.env`**，示例文件必须显式 `source`，或由服务管理器注入环境。

需要固定本机访问时显式设置 `NEWS_READER_HOST=127.0.0.1`。Tailscale 仅改变网络可达性，不提供应用内用户权限。停止测试服务时在启动终端按 Ctrl+C；不要用按进程名批量结束 Python 的方式停服务。

`network_access.py` 在每次启动时并行检测两种实现，每路 CLI 探测总超时4秒。独立客户端使用 `status --json` 的本机地址，要求运行状态有效、未明确离线，并在随机端口验证本机可绑定；停止后的保留 IP 不作为候选，8080 被旧实例占用也不会误判接口失效。CLI 从 PATH、`/Applications`、用户 Applications 和常见 Homebrew 目录发现，调用 Tailscale 应用内 CLI 时设置 `TAILSCALE_BE_CLI=1`，不弹出 GUI 或登录流程。

Surge 内置 Tailscale 的 `100.x` 地址属于用户态节点，通常不在本机网卡上，不能直接设成 `NEWS_READER_HOST`。启动器通过 `surge-cli` 的有效配置和 `proxy-runtime-status` 动态读取就绪节点；只有可接收入站访问的节点参与自动选择。若独立客户端不可用而 Surge 可用，服务监听 `127.0.0.1`，手机访问提示中的 Surge 节点地址与端口。Surge 未启用 `allow-peer-access` 时仍继续检查独立客户端，不能因此提前选中本机地址。两者均不可用、未安装或探测失败时回退到本机，显式 host 始终优先。探测不修改网络配置、不输出配置正文或凭据。

重复双击 `.command` 会请求旧启动器关闭它拥有的服务，等待服务锁释放后，由新终端接管新实例；连续启动请求按顺序处理。旧实例不健康也可替换，旧实例正在更新时先等待更新及回退流程结束。交接等待上限90秒，超时不启动第二个服务并报告失败，不强杀更新；已收到关闭请求的旧启动器仍会在更新完成后退出。旧版启动器通过同用户、同项目的私有锁文件和进程命令核验后接收 SIGTERM。

拿到服务锁后，若目标端口仍有本项目的 `app.py`（包括启动器异常退出后的遗留服务或手工直接启动的实例），核验用户、Python 命令、项目路径与启动时间后先发送 SIGTERM；5秒未退出则再次核验身份后发送 SIGKILL。不会仅凭端口或健康接口结束进程，其他项目和程序保持运行。`updater.py --serve` 和不带参数的 `launcher.py` 继续复用已有健康服务。启动器与更新器的本地健康检查主动绕过系统及环境代理，远端更新请求沿用原有代理配置。

## 配置清单

| 变量 | 默认或作用 | 接手要求 |
| --- | --- | --- |
| `NEWS_READER_HOST` / `NEWS_READER_PORT` | 直接启动默认 127.0.0.1 / 8080 | 明确监听范围，端口冲突时更换 |
| `NEWS_READER_DB_PATH` | 环境变量 → 设置中的 path → 已存在的父目录库 → 项目内新库 | 明确设置，避免误开父目录历史库 |
| `NEWS_READER_APP_SETTINGS_PATH` | 项目根目录 `app_settings.json` | 建议放到专用数据目录 |
| `NEWS_READER_MEDIA_CACHE_DIR` | 主库同级 `media-cache` | 保留与数据库媒体索引的配套关系 |
| `NEWS_READER_AGENT_DB_PATH` | 主库同级 `agent_sessions.sqlite3` | 不同部署应使用独立路径 |
| `NEWS_READER_TRADING_CALENDAR_PATH` | 留空时后台自动更新上交所年度日历，缓存位于主库同级 `trading-calendar-cache.json` | 仅手工维护日历时设置；指定后关闭自动更新，以该文件为准 |
| `NEWS_READER_AGENT_RUNTIME_DIR` | 系统临时目录下按项目路径哈希隔离 | 临时会话状态，不作为长期业务备份 |
| `NEWS_READER_UPDATER_DIR` | 系统临时目录下项目隔离路径 | 更新锁及 `news-reader-updater-state.json` 所在位置 |
| `NEWS_READER_PYTHON` | macOS 启动脚本使用的 Python | 可指定依赖已安装的虚拟环境解释器 |
| `NEWS_READER_NODE` | PATH 中的 node | OAR/Pi 需要 Node 24+ |
| `NEWS_READER_CODEX` | PATH 中的 codex | 使用接手方安装及登录的 CLI |
| `NEWS_READER_PI_AGENT_DIR` | Pi 配置来源覆盖 | 不配置时继承本机 Pi；迁移扩展和信任配置需单独核验 |
| `DEEPSEEK_API_KEY` | 环境变量优先，否则 macOS Keychain | 由接手方设置；不要放进源码或交付说明 |
| `TYPESAFE_API_KEY` | 环境变量优先，否则 macOS Keychain | Jev 新闻分类、主题归属和重要进展筛选 |
| `OPENCODE_API_KEY` | 环境变量优先，否则 macOS Keychain | OpenCode Go 订阅的正文翻译／精读、目录与测试；沿用原 OpenCode 密钥槽，Keychain 密钥不再注入 Pi 子进程 |

| `NEWS_READER_LLM_MODEL` | 空时由代码使用翻译默认模型 | 显式保存的模型设置需与服务可用目录核对 |

`NEWS_READER_MANAGED`、`NEWS_READER_INSTANCE_ID`、`NEWS_READER_SUPERVISOR_SOCKET`、`NEWS_READER_SUPERVISOR_TOKEN` 由启动器生成，不应作为手工配置或对外分发。Keychain 账号为 `newsreader-workbench`，服务名使用 `DEEPSEEK_API_KEY` 等；非 macOS 环境不能依赖设置页的 Keychain 写入。

应用设置模板来自代码默认值，包含 llm、agent、feed、database、news_summary、tracked 配置。模型默认留空，接手方应在设置页选择和测试。Pi/Codex 沿用各自本机配置与认证；应用不会替团队完成账号登录。

## 正文抓取和模型接入

OpenCLI 2.0.3 是项目 npm 精确依赖，自定义 adapter 随源码分发。`npm ci --ignore-scripts` 安装后，采集与正文统一调用项目入口，不加载个人 adapter。浏览器扩展、网络和媒体订阅登录由接手方准备。见 [集成与验收](../opencli-integration.md)。以下逻辑命令均由项目入口执行：

| 来源 | 命令前缀 | 超时 |
| --- | --- | --- |
| Reuters | `opencli reuters article-detail` | 45 秒 |
| Bloomberg | `opencli bloomberg news` | 90 秒 |
| TechCrunch | `opencli TechcrunchPublic article` | 45 秒 |
| Ars Technica | `opencli ArsPublic article` | 45 秒 |
| X 线程 / 长文 | `opencli twitter thread` / `opencli twitter article` | 90 / 60 秒 |

代码兼容 opencli 2.x 的 `ok/data/error` 包装以及旧列表/对象。具体调用参数见 `app.py` 的抓取函数。先运行 `npm run check:collection` 确认必需命令可用，再通过页面用少量真实文章验证。

设置中的原 OpenCode 分区已替换为「OpenCode Go 订阅」，沿用现有密钥与模型。旧首选 `opencode` 归并为 `opencode-go`；首选 Go 时为 Go → DeepSeek → Pi，后备可分别关闭。已有明确 Go 模型优先，密钥须具有 Go 订阅权限；获取目录成功不代表额度可用。Pi 功能使用自身登录或显式环境变量，API 管理不再共享 Keychain 密钥。

设置中分别验收：翻译模型、Pi/Codex 研究模型、模型目录刷新、连接测试、单篇回答和新闻总结。单篇研究使用实时外部检索的能力取决于所选 CLI/provider；总结使用锁定材料。未配置模型和 opencli 不影响纯索引阅读。

Pi 登出后回到「模型与 Agent」会重新读取可用目录，也可点「刷新模型目录」。若服务商仍存在，检查同服务商的环境变量或自定义 models 配置；登出只删除 Pi 登录凭据。当前选项消失时保留原配置并提示重新选择，不自动切换其他服务商。推理强度按模型支持的档位显示；保存后用于新任务，已有会话和已创建报告保持原快照。连接测试只检查当前模型的一次简短请求，完整工具能力需通过实际功能验收。

## 开发与测试

```bash
PYTHONPATH=. python3 -m pytest -q
node --test tests/error_ui.test.cjs tests/item_state.test.mjs tests/read_later.test.mjs tests/oar_bridge.test.mjs
npx playwright install chromium
CI=1 npm run test:ui
```

先释放测试端口 8765；`CI=1` 禁止复用未知的现有测试服务。浏览器测试运行临时数据和模型替身，截图写入 `.ui-artifacts/`，失败 trace 写入 `test-results/`。本次运行结果及失败定位见 05，接手方应保留自己的验收结果。

普通前端无需构建。修改 `frontend/idea-markdown-editor.js` 或相关 ProseMirror 依赖后执行 `npm run build:idea-editor`，同时检查生成 JS、CSS 和 LICENSE 文件的差异。npm 依赖由锁文件固定；Python 的 `certifi` 为下限约束，`tiktoken` 为版本范围，其他列出的 Python 依赖固定版本。定期报告使用本地分词器估算输入容量，编码数据不可用时采用保守的字节估计，详见 [定期报告](../periodic-reports.md)。

## 排障入口

| 现象 | 首先检查 |
| --- | --- |
| 空列表 | 实际输入目录、严格日期文件名、Markdown 入口是否存在、增量索引统计、来源及已读筛选 |
| 换库未生效 | 环境变量是否覆盖设置，是否已重启，设置页显示的当前有效路径 |
| 启动健康检查失败 | 端口占用、服务终端输出、localhost DNS 和系统代理；本地请求应绕过代理 |
| `Can't assign requested address` | 监听 IP 是否仍属于本机；Tailscale 停止后 `tailscale ip` 仍可能返回保留地址，`.command` 会检查并回退到本机地址。显式设置的 `NEWS_READER_HOST` 需自行修正，或恢复对应网络后重启 |
| 抓取失败 | 项目依赖与必需命令检查、Node.js、浏览器连接、登录状态和错误详情 |
| Agent 不可用 | Node 版本、npm 依赖、CLI 登录、模型选择、Pi 项目信任及扩展 |
| OpenCode 获取模型超时或解析失败 | v0.4.3 将手动目录的网络超时由5秒延长至25秒，包含 HTTPS 握手。`URLError.reason` 中的 `TimeoutError` 表示超时，不代表密钥鉴权失败；接口返回504及 `reason=connection_timeout`。401、403等 HTTP 错误保留上游状态与已知原因；无效 JSON／目录格式返回 `response_parse_failed`。检查网络或代理后手动重试，更新代码后重启后端；失败保留已有目录和模型草稿 |
| OpenCode 获取模型返回 403 | 区分密钥错误与 Cloudflare `1010 / browser_signature_banned`；目录及连接测试必须通过 `model_apis.request_headers()` 标识本应用，避免 urllib 默认客户端标识被拒绝。2026-11-01已复现并修复；修改后须重启后端，获取目录成功不等于所选模型鉴权／额度可用 |
| OpenCode 模型测试返回 429 | 免费聊天模型有外部调用限制，输入密钥不会解除；候选目录已统一隐藏 OpenCode 免费模型，已有配置不自动改写。使用支持外部调用的文本模型。`model_apis.http_error_details()` 仅按已知结构化错误码区分免费／Go额度、消费上限、余额与临时限流，未知429不推断具体原因；解析 `Retry-After` 秒数／HTTP日期与 `retry-after-ms`，提示等待但不自动发起重试，不回显原始错误正文或账户信息。正文翻译仍按已保存策略切换其他 API |
| 总结失败 | 预览是否过期、库或配置是否变化、引用校验错误、模型结果及 job.error |
| 更新按钮不可用 | ZIP/detached HEAD/脏工作区、本地分叉、启动入口、更新源和远端稳定 tag |
| 媒体缺失 | 主库中的媒体索引和缓存目录是否一起迁移，缓存路径是否可写 |

服务日志默认输出到启动终端；需要长期日志时由接手方服务管理器采集。顶栏报错弹窗读取的是上游日报错误，不能替代服务端日志。更新进度位于 updater 运行目录。收集故障材料时保留错误码、步骤、版本、时间及相关日志，不附认证令牌和真实用户正文。

## 发布与回退

1. 在开发分支工作，默认前缀 `codex/`；Git 提交信息使用简体中文，例如 `docs: 补充项目交接文档`。
2. 修改版本时同步 `version.json`、`package.json`、`package-lock.json`、`static/index.html` 的标题/标记/资源版本、README、CHANGELOG 及版本一致性测试。从 v0.1.2 之后的版本开始，CHANGELOG 根据实际发布差异按 `feat`、`improve`、`fix` 分组，具体规则见 [CHANGELOG 发布分类](../design-guidelines.md#10-changelog-发布分类)。
3. 完成相关测试，并在数据副本上验证迁移。更换仓库后必须更新 `version.json.repository`，只改 Git remote 不会改变应用更新源；同时更新硬编码该仓库的测试和文档。
4. 审查后提交并推送版本及稳定 tag。更新器接受 `vN.N.N` 或 `vN.N.N.N`；本地改版本号不产生远端更新。发布与账号转移由新负责人执行。
5. 已安装实例在干净分支、受控启动条件下通过界面确认升级。升级器快进代码并健康检查，失败尝试恢复旧提交。新版升级器在停服前准备目标锁定依赖，健康检查失败同时恢复代码与依赖；首次从旧升级器迁入时需手动执行一次 `npm ci --ignore-scripts`。每次推送前运行 `npm run check:release`，维护者用 `npm run setup:hooks` 启用自动检查。

手工回退使用独立旧版本 checkout，加上升级前备份恢复出的数据副本；验证后再切换服务路径。不要在共享工作区强制 reset，不要认为退回旧源码就能撤销数据库 DDL。
