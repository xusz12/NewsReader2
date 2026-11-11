# OpenCLI 集成与验收

NewsReader 使用 npm 正式依赖 `@jackwener/opencli` **2.0.3**，版本号为精确值，
传递依赖由 `package-lock.json` 锁定。所需自定义 adapter 源码直接保存在
`collection/adapters/`，以 `xusz12/clis` v0.9.2 为基线，组合版本 `0.9.2+newsreader.1`
增加 Reuters 视频转录排除补丁。来源提交、本地补丁、逐文件校验值和
12 项必需命令记录在 `collection/opencli-manifest.json`。

v0.5.4 的全部来源、正文及隔离检查结果见 [2026-11-12 验收记录](opencli-verification-2026-11-12.md)。

## v0.5.4 的版本与发布状态

本次集成版本为 NewsReader `v0.5.4`，npm 包版本为 `0.5.4`。原 `v0.5.3` 标签保持历史指向。
本版通过 `v0.5.4` 稳定标签提供更新；旧版可在「设置 → 系统与版本」检查并更新源码。
首次安装使用仓库主分支的源码，具体发布快照由稳定标签定位。

设置内更新要求 Git 工作区干净、分支可快进，并由受管启动器启动服务。
首次从 v0.5.3 升级时，执行升级的仍是旧版升级器，它不会安装新增 npm 依赖。
因此可以通过设置完成源码更新，再手动补做下面的依赖安装和重启；无需强制改用 git pull。
从 v0.5.4 升往后续版本时，新升级器负责项目 npm 依赖的准备、检查和回退。

## 已有 Mac 首次升级

1. 在设置中确认当前数据库路径，备份数据库、`app_settings.json` 和需要保留的媒体缓存。
   SQLite 一致性备份方法见[数据迁移与恢复](handoff/04-data-migration.md#制作一致性备份)。
2. 等待正在执行的采集、翻译和研究任务结束。若采用设置内更新，先完成源码升级；
   随后在原启动终端按 Ctrl+C 停止服务。
   `npm ci` 会重建项目的 `node_modules`，不应与正在运行的采集或 Agent 任务同时执行。
3. 确认已保存本地源码改动，执行下方命令更新源码并安装依赖；已在设置中更新源码时跳过 `git pull`。
   以下示例使用项目 `.venv`，使用其他 Python 环境的现有安装应继续使用原环境。

```sh
git status --short
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
npm ci --ignore-scripts
npm run check:collection
./启动NewsReader.command
```

若 Git 提示本地改动或分支无法快进，先处理该提示，不要覆盖本地文件继续升级。
这里的 pull 以 v0.5.4 集成改动已推送到所跟踪分支为前提。
数据库、阅读状态、收藏、正文缓存和原配置继续使用，无需清空或重建数据库。
重启后刷新网页，到「设置 → 信源与采集」检查环境和 X 登录，再做一次「立即采集」。

已经安装、验收过本次项目依赖的开发 Mac 无需再次运行 `npm ci`，重新启动应用即可加载新代码。
首次 clone、换目录、依赖锁文件改变，或 `node_modules` 缺失／损坏时才需要重新安装。
旧升级器不会安装本次新增依赖，所以首次迁入须手动执行；之后新版升级器管理 npm 依赖的准备、检查和回退。

## 新机器安装

先安装 Git、Python 3.10+、Node.js 24+（含 npm）和 Chrome。从源码安装的步骤如下：

```sh
git clone https://github.com/xusz12/NewsReader2.git newsreader-workbench
cd newsreader-workbench
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
npm ci --ignore-scripts
npm run check:collection
./启动NewsReader.command
```

启动器优先使用项目 `.venv`，检查后打开浏览器；也可在 Finder 中双击 `启动NewsReader.command`。
使用 Git clone 才能保留后续内置升级所需的 Git 信息，ZIP 解压安装不支持该升级方式。
无需构建前端即可启动当前源码。项目 `.npmrc` 默认禁用安装脚本；`--ignore-scripts` 避免上游安装脚本向个人目录
同步 adapter；项目运行不需要这些脚本。无需全局安装 opencli、独立 twitter-cli，
也无需手动安装或注册自定义 adapter。源码包不携带 node_modules；依赖安装仍需要网络。

设置 → 信源与采集 → 采集环境分别检查项目版本、全部必需命令及文件完整性、
Chrome、OpenCLI Browser Bridge 和 X 登录；采集服务配置显示翻译路由缺少的配置。
纯阅读不要求采集环境就绪。错误会保留为失败，不回退调用全局 CLI。

浏览器来源仍要求服务所在电脑打开 Chrome / Chromium、安装并启用
[OpenCLI Browser Bridge](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk)，并在同一 Chrome 用户配置中登录 X。
扩展的[官方手动安装说明](https://github.com/jackwener/OpenCLI#2-install-the-browser-bridge-extension)提供 ZIP 安装方式。
本次实测扩展版本为 1.0.27；浏览器扩展由 Chrome 管理，不在 npm 锁文件中。
公开 RSS 不需要浏览器。新闻网站登录或订阅、翻译模型凭据由使用者配置。
Bypass Paywalls Clean 是可选的手动安装项，不能仅据安装情况保证正文完整。

首次进入设置时，先核对数据路径，再在「API 管理」及「模型与 Agent」配置实际使用的服务，
确保至少一条翻译路由可用；API 密钥、Keychain、X 和 Pi/Codex 登录状态不会随 clone 迁移。
抓取通过不等于已经入库，当前流程需要整批翻译完成后发布到新闻库。
要保留另一台机器上的阅读记录与收藏，另外迁移数据库和所需媒体缓存，并修正设置中的旧绝对路径。
只从空库开始则无需复制旧数据。

自动采集需要后台服务运行、Mac 保持唤醒；关闭阅读网页不影响后台任务。
Reuters、X 和依赖浏览器的正文抓取期间须保持 Chrome 打开。
完成环境及 X 登录检查后，执行一次「立即采集」，在「采集记录」确认抓取、翻译和入库结果，
再抽查新闻正文。环境检查只核验依赖和连接，不代替实际来源验收。

## 调用与隔离

`collection_sources.run_command`、`app._run_opencli_json_command` 和环境诊断
共用 `opencli_runtime.resolve_command`，把逻辑命令 `opencli …` 转为
`<Node 绝对路径> <项目>/scripts/opencli.mjs …`。只从系统寻找 Node，
也可用 `NEWS_READER_NODE` 指定；不寻找全局 opencli，不使用 npx 自动下载。

项目入口核对 npm 声明、锁文件和安装版本，校验内置源码哈希，显式导入必需模块。
它不执行上游 `main.js` 的个人 adapter/plugin 扫描、个人依赖链接或自动更新。
`~/.opencli/clis`、`~/.opencli/plugins` 中的代码无法覆盖本项目命令。
直接使用 `node_modules/.bin/opencli` 会绕过这种隔离，因此应用和验收均不调用该入口。

plugin 是组织、注册一组扩展命令的包；adapter 是实现某个网站命令的代码。
它们都不同于负责连接 Chrome 的浏览器扩展。官方本地 plugin 机制仍使用用户级目录，
本项目采用显式加载仓库内模块，无需安装用户级 plugin。

浏览器连接仍复用 OpenCLI 的本机桥接端口和浏览器配置，允许与其他 CLI 共用登录会话。
必要时可用 `npm run opencli -- daemon restart` 从本项目启动桥接；应在浏览器任务空闲时操作。
卸载全局 opencli 或删除个人 adapter 不会删掉本项目依赖；全局卸载脚本可能停止共用桥接，
后续浏览器调用可从项目内自动启动桥接。不要把删除全部浏览器配置当作卸载程序的必要步骤。

隔离范围是 CLI 程序和 adapter 代码，并非整套浏览器环境：全局 `npm update -g`
或个人 adapter 修改不会改变项目内固定版本，也不会在项目缺依赖时被拿来顶替。
但 Chrome、Browser Bridge 扩展、浏览器登录和本机桥接服务仍共用；外部 CLI 启动或重启桥接、
更换桥接版本，或浏览器扩展更新，可能影响 NewsReader 的浏览器采集连接。
OpenCLI 2.0.3 会复用已运行的较新版本桥接服务，不会强制将其替换为项目版本。
所以不能把「全局 CLI 更新不改变项目依赖」理解成「所有外部操作都不影响采集」。
遇到这类连接问题先在设置检查环境；确认没有浏览器任务执行后，可用项目入口重启桥接。

## 安装与检查命令的作用

| 命令 | 作用 |
| --- | --- |
| `npm ci --ignore-scripts` | 按 `package-lock.json` 重建项目 `node_modules`，安装锁定的 OpenCLI 等 Node 依赖；禁用依赖安装脚本，不向个人目录同步 adapter。它不会更新锁文件或自动追最新版。 |
| `npm run check:collection` | 检查项目内 OpenCLI 版本、内置 adapter 文件完整性和 12 项必需命令；不安装依赖，不采集入库，也不检查 Chrome、X 登录或真实网站正文。 |
| `python3 -m venv .venv` | 在项目内创建 Python 虚拟环境，将 NewsReader 的 Python 依赖与系统和其他项目分开。 |
| `source .venv/bin/activate` | 让当前终端优先使用这套虚拟环境的 Python 和 pip；仅对当前终端生效，Finder 启动器会直接优先使用项目 `.venv`。 |
| `python3 -m pip install -r requirements.txt` | 给当前 Python 环境安装 NewsReader 服务端依赖，例如 Flask、OpenAI SDK；不安装 OpenCLI，也不替用户配置模型密钥。 |

## 更新策略

OpenCLI 和内置 adapter 随 NewsReader 发布，普通用户没有独立自动升级步骤。
每次推送前查询 npm 官方 stable/latest；发现更新后先验证候选版本，
全部测试通过才改精确版本和锁文件。未通过时保持已验证版本并记录暂缓原因。

维护者首次执行 `npm run setup:hooks` 启用 `.githooks/pre-push`。
Git 不会随 clone 自动启用 hooks；安装器保留已有 hooks，已有自定义 pre-push 时需手动把发布检查接入其中。
普通使用者无需启用开发 hooks。也可手动运行 `npm run check:release`。
检查失败、上游版本变化、运行时代码或来源变化而测试记录未更新时阻止推送。
GitHub Actions 在 push / pull request 上另外执行干净安装、离线回归及同一发布检查；浏览器实测记录在本地生成。
发布检查失败不能用修改测试阈值或仅运行 `--help` 代替完整验收。

升级顺序：修改候选 npm 精确版本与锁文件 → 固定 adapter 来源提交及文件哈希 →
离线测试 → 干净目录 `npm ci --ignore-scripts` → 浏览器真实验收 → 生成发布记录 → 推送。
入口使用 OpenCLI 的内部模块加载接口，因此版本变化也必须检验入口本身，不能只检验 adapter。

```sh
npm run test:adapters
npm run test:opencli
npm run test:collection:live
python3 scripts/check_opencli_release.py --record output/opencli-validation.json
npm run check:release
```

若上游已有新版本但本次保留当前版本，记录时增加
`--retain-reason '候选版本的具体问题或未采用原因'`，仍要求当前组合全部测试通过。
发布记录 `collection/opencli-release.json` 包含 NewsReader/OpenCLI 版本、adapter 提交、
浏览器扩展实测版本、12 个采集入口和各正文样本结果、代码指纹及上游检查时间。

升级器在停止服务前，把目标锁文件安装到临时目录并校验必需命令；
失败时保持旧服务运行。通过后切换代码和依赖，再启动并检查健康状态；
失败时同时恢复旧代码和旧 node_modules。回退遇到用户改动时保留恢复副本并报告路径。
首次从旧升级器迁入本次集成时，旧升级器不具备依赖安装能力，更新源码后须执行一次
`npm ci --ignore-scripts` 并重启；之后使用新版升级器即可管理锁定依赖。

## 验收边界

`verify_collection_runtime.py` 使用临时数据库、设置和媒体目录，不写真实新闻库，
不调用翻译或评论总结模型。使用实际采集函数及实际正文解析函数；覆盖全部 12 个入口、
Reuters/Bloomberg/TechCrunch/Ars 正文、Reuters 列表式及内嵌视频样本、X 普通帖、Note Tweet 与独立 Article。
它保留浏览器已有登录，屏蔽全局 opencli，并用 Node 加载钩子拒绝项目目录外的模块；
`--restart-daemon` 同时验证桥接从项目代码启动。不会删除或重命名个人 adapter。

检查有效条数、标题、HTTPS URL、来源归属、时间、X 身份、实质正文与专门样本的关键内容。
X 转引/媒体、限流、超时、失效登录、文章清洗等边界另由 adapter 和应用回归覆盖。
成功只代表记录中的版本、浏览器环境和样本在该时刻通过；网站后续改变仍可能需要修复。
代表性样本存放于 `collection/validation-samples.json`，发布时复核其页面结构和内容。
