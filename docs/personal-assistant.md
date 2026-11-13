# 个人助手（第一阶段）

顶栏「个人助手」打开独立对话浮层；「设置 → 个人助手」管理名字、职责、运行模型和长期记忆。不预设名字，首次名称留空，由用户命名并保存后开始讨论；未命名时「个人助手」仅为功能标题。首次创建时复制当时 Chat 的模型选择，此后独立保存。每条讨论独立，关闭面板不会停止回复，讨论记录不参与新闻研究 Agent 的 24／72 小时清理。

这一阶段提供 NR 全库查询、讨论和个人记忆。不接管文章 Agent、主题追踪、报告或总结的执行入口；不自动导入旧 Agent 对话；没有向量索引或语义检索。库内没有的实时新闻会说明资料缺口，本功能不负责采集。

## 身份与存储

默认持久目录为应用设置文件旁的 `personal-assistant/`，可由 `NEWS_READER_PERSONAL_ASSISTANT_DIR` 指定。与主库位置独立，切换新闻库不会重建身份。

```text
personal-assistant/
  assistant.sqlite3        # 身份 ID、名字、职责、模型配置、讨论、消息、任务状态
  workspace/
    MEMORY.md              # 简短长期记忆索引
    notes/*.md             # 偏好、事件判断及变化的专题笔记
  runtime/<thread>/<epoch>/ # 可重建的运行时会话，不作为记忆权威来源
```

每条讨论绑定创建时的新闻库绝对路径。切库后仍可阅读旧讨论，但须在当前库开新讨论才能继续查询。历史检索返回原讨论的库路径；新闻引用保留 URL／记录 ID／时间，不把不同库中的相同 ID 合并。

Pi 与 Codex 使用相同的身份、记忆和规范化消息，但各自保留原生会话。更名、职责／模型变更、用户手动修正或删除记忆会提高 `epoch`，移除本模块的原生会话缓存。后续重建时只带同一讨论最近最多 24,000 字符的已完成消息，更早记录可通过工具翻阅；其他讨论不会整段塞入当前上下文。重启后发现未完成任务会标为中断，保留部分回复并弃用中断的原生上下文。第一阶段同一助手同时处理一轮请求，其他讨论仍可查看。

身份和内容保存在本机；模型推理使用所选服务，查询结果和记忆会随需要传给它。应用不新增模型密钥存储方式。

个人助手在业务状态和上下文上独立：单独的配置、数据库、任务锁、讨论、记忆及原生会话目录。它与现有功能共用 Flask 服务、机器资源、Pi／Codex 可执行程序及模型账号；不是进程与资源完全隔离的另一应用。同一账号的限流与机器负载可能同时影响这些功能。NR 主库通过只读工具共享，现有任务入口、排队及清理规则不交给个人助手。

## 记忆机制与 Raft 参考

参考公开 Raft 源码提交 `db5e399f017d584e49597e3a28757645d6807893` 的组织方式：

- [创建 Agent 身份](https://github.com/botiverse/raft-source/blob/db5e399f017d584e49597e3a28757645d6807893/packages/server/src/services/agentService.ts)：身份与执行 Runtime 分开。
- [初始化 workspace](https://github.com/botiverse/raft-source/blob/db5e399f017d584e49597e3a28757645d6807893/packages/daemon/src/workspaces.ts)：持久 `MEMORY.md` 与 `notes/`，初始化不覆盖旧记忆。
- [系统指令](https://github.com/botiverse/raft-source/blob/db5e399f017d584e49597e3a28757645d6807893/packages/daemon/src/drivers/systemPrompt.ts)与[记忆使用指南](https://github.com/botiverse/raft-source/blob/db5e399f017d584e49597e3a28757645d6807893/packages/shared/src/raftCliGuide.ts)：先读简短索引，按需读取专题，学到值得保留的信息后主动整理。

这里只参考架构，没有复制 Raft 的实现。NR 每轮加载当前索引和文件名列表，避免续聊时使用旧记忆。助手指令要求主动整理用户明确表达的偏好、判断、猜想及修正，并保留「依据 → 推断 → 预判」、成立条件、反例、不确定性和来源。自动整理由当前模型使用记忆工具完成，不是独立后台抽取作业；普通查询没有新的个人表达时无需写入。

`memory_write` 要求真实用户消息 ID，服务端核验作者并追加出处。文章内容、助手分析、Agent 归档不等于用户观点；收藏也不等于赞同。索引限 16 KB，单篇笔记限 64 KB，最多 200 个文件。更新采用内容哈希检查与原子替换，冲突时须重新读取。工具进程绑定当前任务；停止或重启中断后，旧进程不能继续通过工具写记忆。

设置支持查看、修改、删除笔记。删除笔记会移除索引中引用该路径的行；清空索引只重置 `MEMORY.md`，其他笔记保留。界面明确提示原始讨论仍保留，不承诺删除原生 CLI 自己保存的历史。用户修正优先于旧对话，指令禁止从历史自动重建已删除记忆。

## 查询与运行

`personal_assistant_tools.py` 是只使用 Python 标准库的 stdio MCP 服务，不导入 Flask 应用，不迁移数据库。

| 工具 | 作用 |
| --- | --- |
| `nr_schema` | 查看当前主库表结构和关联说明 |
| `nr_query` | 参数化只读 SQL，可跨所有业务表筛选、关键词搜索、关联、聚合及分页 |
| `discussion_search` | 检索个人助手消息，支持讨论、关键词和游标 |
| `memory_list` / `memory_read` | 查看索引及按需读取笔记 |
| `memory_write` | 整理有用户表达出处的记忆 |

新闻、原文、翻译、重要、收藏、想法、事件、总结和报告均以 NR 主库为数据源，不复制业务表。查询连接使用 SQLite `mode=ro`、`query_only` 和 authorizer，拒绝写操作、ATTACH、PRAGMA 及扩展加载。单次执行限 2 秒、最多 100 行；长字段和大结果明确标为截断，可用 `LIMIT/OFFSET` 或 `substr` 继续读取。没有把全部新闻预载到提示词中。

Pi 通过现有 OAR bridge 的原生 CLI 路径运行，仅在每次私有配置副本中安装 `nr_assistant` MCP，使用本机模型／认证配置；限制工具选择为 NR 工具，不加载个人扩展和 skills。Codex 复用原有 read-only／never 执行策略，通过本次命令的 MCP 配置访问工具，禁止加载项目开发文档；允许独立工作区不包含 Git 仓库。两者都不修改用户原生配置。现有新闻 Agent 的调用路径不携带这份工具注册。

## 代码与接口

- `personal_assistant.py`：Flask Blueprint、持久化、每轮状态、停止及重启恢复。
- `personal_assistant_runtime.py`：隔离工作目录、原生续聊、流式事件、超时与子进程回收。
- `personal_assistant_tools.py`：全库只读查询、讨论检索、文件记忆与 MCP 协议。
- `static/personal-assistant.js`、`static/personal-assistant-settings.js`：顶栏浮层、历史讨论、独立设置。

接口前缀 `/api/personal-assistant`：`GET/PUT profile`、`POST model-options`、`GET/POST threads`、`GET threads/<id>`、`POST threads/<id>/messages`、`POST threads/<id>/stop`、`GET memory`、`GET/PUT/DELETE memory/<path>`。发送携带 UUID `request_id`，同一请求重试不会创建重复消息。配置保存携带 `epoch`，记忆保存／删除携带 `revision`；并发修改会提示重新读取。

## 备份与验证

备份前等待回复完成并停止应用，连同新闻主库一起备份 `assistant.sqlite3` 和整个 `workspace/`。`runtime/` 可不迁移；原生模型认证在目标机器重新配置。不要将包含个人数据的目录提交到 Git；默认目录已加入忽略规则。

验证入口：`tests/test_personal_assistant.py`、`tests/oar_bridge.test.mjs`、`tests/ui/personal-assistant.spec.js`。覆盖全库查询限制、来源与路径校验、记忆冲突、切换 runtime、重启恢复、停止、原生 MCP 连接、独立讨论及主题／手机布局。另用合成新闻库分别完成真实 Pi、Codex 的两轮验证：统计库内新闻并保存偏好，在新讨论中读取之前的偏好。
