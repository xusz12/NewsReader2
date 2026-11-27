# NR 后端开发地图

NR 使用一个 Flask 服务进程。启动入口是 `python3 app.py` 或现有启动器。应用工厂 `app.create_app()` 装配路由与服务，`nr/bootstrap.py` 负责数据库初始化、迁移和启动后台线程。正文抓取和模型执行按需调用本机 CLI。新增路由和保存到 SQLite 的业务规则直接放进对应功能模块。

## 从功能定位代码

下表的模块位于 `nr/`，测试文件位于 `tests/`。从 URL 入手时，可以先在 [HTTP 路由清单](../tests/fixtures/backend-routes.txt) 找到处理函数，再用 `rg` 搜索函数名。

| 维护任务 | 实现及直接调用方 | 首选回归测试 |
| --- | --- | --- |
| 新闻列表、搜索、收藏、稍后读、阅读进度 | `reading.py` 负责查询，`read_state.py` 负责状态写入，`reader_items.py` 组装新闻和报告共用的阅读数据 | `test_feed_api.py`、`test_read_later_states.py` |
| 高亮、重定位、批注 | `highlights.py` 负责各类正文的偏移、哈希与写入，新闻详情 `details.py` 和报告详情 `reports.py` 共用 | `test_highlights.py`、`test_report_reading.py` |
| 想法、提醒、复盘、市场标签 | `ideas.py`、`reminders.py`、`reviews.py`、`market.py` 分别负责各自页面与数据，详情页读取对应结果 | `test_ideas_workbench.py`、`test_reminders_api.py`、`test_review_api.py`、`test_market_api.py` |
| 手动追踪与自动主题研究 | `tracked_topics.py` 和 `tracked_rules.py` 负责手动追踪及匹配规则，`topics.py` 接入根目录的 `topic_tracking.py` 后台服务 | `test_tracked_topics_api.py`、`test_topic_tracking.py` |
| 正文、图片、翻译、重试 | `article_fetch.py` 对接抓取命令、错误分类与媒体缓存，`details.py` 负责任务领取、持久化和详情响应 | `test_opencli_details.py`、`test_detail_api.py`、`test_article_fetch.py`、`test_translation_routing.py` |
| 新闻对话、归档、停止任务 | `news_chat.py` 处理对话和归档，`agent_sessions.py` 管理会话及任务状态，`agent_jobs.py` 执行与终止任务 | `test_news_chat_api.py`、`test_agent_lifecycle.py`、`test_agent_conversation.py` |
| 浏览器通知、设备偏好、事件与投递 | `browser_push.py` 管理设备和投递，`push_transport.py` 检测 Python 推送库并在旧环境调用项目内的 Node 推送库。根目录 `push_events.py` 为完成回复保存事件，主库事务记录日报和采集批次 | `test_browser_push.py`、`test_agent_notifications.py`、`test_personal_assistant.py` |
| 分类、采集、任务进度、积压总结、定期报告 | `tasks.py`、`summaries.py`、`reports.py` 提供接口。调度与生成在根目录同领域服务，实例由 `bootstrap.py` 装配 | `test_news_classification.py`、`test_news_collection.py`、`test_task_progress.py`、`test_news_summaries.py`、`test_periodic_reports.py` |
| 应用设置、路径、更新、启动 | `preferences.py` 与 `configuration.py` 处理配置，`database.py` 打开主库和会话库，`updates.py` 对接现有启动器、更新器 | `test_preferences_api.py`、`test_data_compatibility.py`、`test_updater.py`、`test_launcher.py` |
| 其他设备访问地址 | `updates.py` 的设置接口复用 `network_access.py` 的网络发现和实例核验，与启动终端共用地址排序 | `test_network_access.py`、`test_preferences_api.py`、`test_launcher_replacement.py` |

个人助手由 `personal_assistant.py` 管理独立锁与 SQLite 事务。`assistant_main_thread` 选定唯一主线，`message_quotes` 按用户消息 ID 保存引文快照。`thread_models` 保存最近一轮的执行选择，供原轮重试使用。首次有效发送在同一写事务内建立主线、任务与消息。`assistant_reply_notice` 在回复成功提交时记录待看的任务 ID，`/api/personal-assistant/reply-notice` 查询提醒或按精确任务 ID 确认已读。主库按永久条目 ID 读取。`personal_assistant_tools.py` 检索正文、引文和标题，核验记忆所引用户消息。三种 runtime 的压缩能力及用量检查位于 `scripts/oar_assistant_context.mjs`，停止、超时与缓存重建由 `personal_assistant_runtime.py` 管理。回归入口为 `test_personal_assistant.py`、`test_oar_assistant.py`、`test_personal_assistant_runtime.py` 和 `test_agent_conversation.py`。迁移、备份与回退见[个人助手说明](personal-assistant.md)。修改共享阅读行为时同时检查新闻和报告。修改任务停止行为时同时检查 HTTP 状态、SQLite 记录和执行线程。

文章问答的 worker 仅在阶段变化时更新 `agent_jobs.progress`。取得有效终态且进程正常退出后，才在同一事务中写入任务答案、消息正文和通知。现有任务 SSE 及单任务 GET 的 `?updates=1` 返回同一组轻量字段，仅成功终态附带 `answer_text`。新闻与报告共用这套机制。

## 新增与拆分约定

1. 先定位一个用户动作、它读写的数据和已有测试。通常在同一功能文件中保留路由、校验、查询与序列化。只有出现独立生命周期、多个真实调用方，或一组能独立验证的业务行为时，再提取模块。文件行数只作为定位线索。
2. 路由通过 `register(app, ...)` 接收依赖，由工厂注册。数据库路径、锁、线程、队列、会话和缓存归所属服务实例。功能模块只导入所需的业务依赖，依赖方向可以通过架构测试检查。
3. 一次操作在同一个连接中读取与修改关联数据，由发起操作的一层确定提交或回滚。数据库迁移须支持旧库和重复启动。任务须能处理重复请求、失败重试、取消与进程重启。
4. 使用可搜索的领域名称。新代码通过命名、类型和测试表达意图，协作说明放在此文档。新功能优先扩充已有领域测试。新增边界时，在本表补上入口、直接调用方和回归文件。
5. 结构移动与行为、性能调整分开提交。先跑领域测试及直接调用方，例如 `python3 -m pytest -q tests/test_highlights.py tests/test_report_reading.py`。交付前运行 `python3 -m pytest -q tests/test_backend_architecture.py`、相关集成测试和全量回归。路由有意变更时同步审阅清单、调用方及契约测试。
6. 评估性能时，使用同一份数据、同一种查询和相同预热次数，记录耗时与 SQL 数量。评估上下文时记录实际要读的模块、函数与测试，区分整文件读取和按符号读取。编辑器前端构建仅在其源代码或依赖变更时执行。
