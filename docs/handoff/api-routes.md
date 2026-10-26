# HTTP 路由索引

基线为 v0.0.15，提交 `de1dec7fe15a536ef6fc802df02fbfafbf30566e`。以下从 `app.py` 的显式 Flask 路由装饰器提取，共 118 条路径与方法声明；不包含 Flask 自动提供的 static、HEAD 和 OPTIONS。它是代码定位索引，不是完整 OpenAPI 契约。

未在当前界面使用的兼容接口仍在此列出；可达不代表已做公开网络的认证和权限控制。

| 方法 | 路径 | 处理函数 | 基线代码行 |
| --- | --- | --- | --- |
| GET | `/` | `index` | 6915 |
| DELETE | `/api/agent/sessions` | `api_agent_sessions_clear_all` | 3846 |
| GET | `/api/daily-briefings` | `api_daily_briefings` | 6920 |
| GET | `/api/daily-briefings/<date_key>` | `api_daily_briefing_detail` | 6932 |
| GET | `/api/error-stats` | `api_error_stats` | 7818 |
| GET | `/api/ideas` | `api_ideas` | 7110 |
| GET | `/api/market-tags` | `api_market_tags` | 10277 |
| POST | `/api/market-tags` | `api_market_tags_create` | 10288 |
| DELETE | `/api/market-tags/<tag_key>` | `api_market_tags_delete` | 10444 |
| PATCH | `/api/market-tags/<tag_key>` | `api_market_tags_update` | 10367 |
| GET | `/api/market-tags/<tag_key>/impact` | `api_market_tags_impact` | 10428 |
| POST | `/api/market-tags/<tag_key>/merge` | `api_market_tags_merge` | 10483 |
| GET | `/api/market-tags/<tag_key>/summary` | `api_market_tag_summary` | 8667 |
| POST | `/api/market-tags/<tag_key>/summary/generate` | `api_market_tag_summary_generate` | 8690 |
| POST | `/api/market-tags/reorder` | `api_market_tags_reorder` | 10329 |
| PUT | `/api/market-trend-note` | `api_market_trend_note_create` | 8763 |
| GET | `/api/market-trends` | `api_market_trends` | 8198 |
| GET | `/api/market-trends/detail` | `api_market_trends_detail` | 8352 |
| PUT | `/api/market-trends/note` | `api_market_trend_note_create` | 8763 |
| DELETE | `/api/market-trends/note/<int:note_id>` | `api_market_trend_note_delete` | 8909 |
| PATCH | `/api/market-trends/note/<int:note_id>` | `api_market_trend_note_update` | 8817 |
| GET | `/api/market-trends/tag-detail` | `api_market_trends_tag_detail` | 8428 |
| GET | `/api/market-workbench` | `api_market_workbench` | 8528 |
| PUT | `/api/market-workbench/pin` | `api_market_workbench_pin_save` | 8605 |
| GET | `/api/media-cache/<cache_key>` | `api_media_cache` | 9325 |
| GET | `/api/nav-summary` | `api_nav_summary` | 6946 |
| GET | `/api/news` | `api_news` | 6955 |
| GET | `/api/news-summaries` | `api_news_summaries` | 11971 |
| POST | `/api/news-summaries` | `api_news_summary_create` | 12017 |
| GET | `/api/news-summaries/<summary_id>` | `api_news_summary_detail` | 12052 |
| GET | `/api/news-summaries/jobs/<job_id>` | `api_news_summary_job` | 12031 |
| POST | `/api/news-summaries/jobs/<job_id>/cancel` | `api_news_summary_cancel` | 12040 |
| POST | `/api/news-summaries/prepare` | `api_news_summary_prepare` | 11994 |
| POST | `/api/news-summaries/previews/<preview_id>/discard` | `api_news_summary_discard_preview` | 12005 |
| GET, POST | `/api/news-summaries/scope` | `api_news_summary_scope` | 11980 |
| GET | `/api/news/<item_id>/agent` | `api_agent_session` | 3762 |
| POST | `/api/news/<item_id>/agent/jobs` | `api_agent_job_create` | 3923 |
| GET | `/api/news/<item_id>/agent/jobs/<job_id>` | `api_agent_job_get` | 3933 |
| POST | `/api/news/<item_id>/agent/jobs/<job_id>/retry` | `api_agent_job_retry` | 3928 |
| POST | `/api/news/<item_id>/agent/jobs/<job_id>/stop` | `api_agent_job_stop` | 3951 |
| GET | `/api/news/<item_id>/agent/jobs/<job_id>/stream` | `api_agent_job_stream` | 4019 |
| DELETE | `/api/news/<item_id>/agent/session` | `api_agent_session_clear` | 3828 |
| GET | `/api/news/<item_id>/agent/session` | `api_agent_session` | 3762 |
| POST | `/api/news/<item_id>/agent/session` | `api_agent_session_create` | 3783 |
| POST | `/api/news/<item_id>/chat` | `api_news_chat` | 10027 |
| POST | `/api/news/<item_id>/chat/archive` | `api_news_chat_archive` | 10140 |
| GET | `/api/news/<item_id>/detail` | `api_news_detail` | 9351 |
| POST | `/api/news/<item_id>/detail/retry` | `api_news_detail_retry` | 10863 |
| GET | `/api/news/<item_id>/highlights` | `api_news_highlights_list` | 9524 |
| POST | `/api/news/<item_id>/highlights` | `api_news_highlight_create` | 9543 |
| DELETE | `/api/news/<item_id>/highlights/<int:highlight_id>` | `api_news_highlight_delete` | 9817 |
| PATCH | `/api/news/<item_id>/highlights/<int:highlight_id>` | `api_news_highlight_update` | 9645 |
| DELETE | `/api/news/<item_id>/highlights/<int:highlight_id>/annotation` | `api_news_highlight_annotation_clear` | 9775 |
| GET | `/api/news/<item_id>/highlights/<int:highlight_id>/annotation` | `api_news_highlight_annotation_get` | 9690 |
| PATCH | `/api/news/<item_id>/highlights/<int:highlight_id>/annotation` | `api_news_highlight_annotation_save` | 9724 |
| PUT | `/api/news/<item_id>/highlights/<int:highlight_id>/annotation` | `api_news_highlight_annotation_save` | 9724 |
| DELETE | `/api/news/<item_id>/market-tag` | `api_news_market_tag_delete` | 10824 |
| PUT | `/api/news/<item_id>/market-tag` | `api_news_market_tag_upsert` | 10741 |
| PUT | `/api/news/<item_id>/note` | `api_news_note_upsert` | 10687 |
| POST | `/api/news/<item_id>/notes` | `api_article_note_entries` | 10634 |
| DELETE | `/api/news/<item_id>/notes/<note_id>` | `api_article_note_entries` | 10634 |
| POST | `/api/news/<item_id>/reminders` | `api_news_reminder_create` | 9848 |
| PATCH | `/api/news/<item_id>/state` | `api_update_item_state` | 9218 |
| POST | `/api/news/clear-favorites` | `api_clear_favorites` | 10962 |
| POST | `/api/news/clear-read-later` | `api_clear_read_later` | 11017 |
| POST | `/api/news/mark-all-read` | `api_mark_all_read` | 10898 |
| POST | `/api/news/mark-read-by-ids` | `api_mark_read_by_ids` | 10976 |
| GET | `/api/news/status` | `api_news_status` | 7150 |
| GET | `/api/reading-checkpoint` | `api_get_reading_checkpoint` | 9061 |
| PUT | `/api/reading-checkpoint` | `api_put_reading_checkpoint` | 9087 |
| GET | `/api/reading-checkpoint/locate` | `api_locate_reading_checkpoint` | 9123 |
| POST | `/api/reindex` | `api_reindex` | 9195 |
| GET | `/api/release-notes` | `api_release_notes` | 8056 |
| GET | `/api/reminders` | `api_reminders` | 7571 |
| DELETE | `/api/reminders/<int:reminder_id>` | `api_reminder_delete` | 10009 |
| PATCH | `/api/reminders/<int:reminder_id>` | `api_reminder_update` | 9909 |
| GET | `/api/reminders/summary` | `api_reminders_summary` | 7621 |
| GET | `/api/reviews` | `api_reviews_list` | 11296 |
| POST | `/api/reviews` | `api_review_create` | 11383 |
| GET | `/api/reviews/<int:chain_id>` | `api_review_detail` | 11370 |
| POST | `/api/reviews/<int:chain_id>/complete` | `api_review_complete` | 11663 |
| POST | `/api/reviews/<int:chain_id>/continue-observing` | `api_review_continue_observing` | 11726 |
| POST | `/api/reviews/<int:chain_id>/evidence` | `api_review_add_evidence` | 11832 |
| DELETE | `/api/reviews/<int:chain_id>/evidence/<int:evidence_id>` | `api_review_delete_evidence` | 11868 |
| POST | `/api/reviews/<int:chain_id>/progress` | `api_review_progress` | 11474 |
| POST | `/api/reviews/<int:chain_id>/reminders` | `api_review_create_reminder` | 11895 |
| POST | `/api/reviews/<int:chain_id>/retrack` | `api_review_retrack` | 11771 |
| POST | `/api/reviews/<int:chain_id>/revise` | `api_review_revise` | 11603 |
| GET | `/api/search` | `api_search` | 7631 |
| GET | `/api/settings` | `api_settings` | 8126 |
| PUT | `/api/settings` | `api_settings_update` | 8147 |
| POST | `/api/settings/connection-test` | `api_settings_connection_test` | 8114 |
| POST | `/api/settings/database/pick` | `api_settings_database_pick` | 8131 |
| POST | `/api/settings/models` | `api_settings_models` | 8098 |
| DELETE | `/api/settings/secrets/<provider>` | `api_settings_secret_delete` | 8186 |
| PUT | `/api/settings/secrets/<provider>` | `api_settings_secret_save` | 8173 |
| PUT | `/api/settings/tracked-default-rule-params` | `api_settings_tracked_default_rule_params_update` | 8161 |
| POST | `/api/settings/translation/models` | `api_translation_models` | 8061 |
| POST | `/api/settings/translation/test` | `api_translation_test` | 8071 |
| GET | `/api/sources` | `api_sources` | 7746 |
| POST | `/api/standalone-ideas` | `api_standalone_idea_create` | 8949 |
| DELETE | `/api/standalone-ideas/<int:idea_id>` | `api_standalone_idea_delete` | 9021 |
| PATCH | `/api/standalone-ideas/<int:idea_id>` | `api_standalone_idea_update` | 8983 |
| GET | `/api/tracked-topics` | `api_tracked_topics` | 7208 |
| POST | `/api/tracked-topics` | `api_tracked_topics_create` | 7254 |
| DELETE | `/api/tracked-topics/<int:topic_id>` | `api_tracked_topics_delete` | 7348 |
| PATCH | `/api/tracked-topics/<int:topic_id>` | `api_tracked_topics_update` | 7293 |
| POST | `/api/tracked-topics/<int:topic_id>/backfill` | `api_tracked_topic_backfill` | 7464 |
| GET | `/api/tracked-topics/<int:topic_id>/daily-summaries` | `api_tracked_topic_daily_summaries` | 7376 |
| POST | `/api/tracked-topics/<int:topic_id>/daily-summaries/<summary_date>/generate` | `api_tracked_topic_daily_summary_generate` | 7389 |
| GET | `/api/tracked-topics/<int:topic_id>/items` | `api_tracked_topic_items` | 7363 |
| POST | `/api/tracked-topics/<int:topic_id>/items` | `api_tracked_topic_item_create` | 7499 |
| PATCH | `/api/tracked-topics/<int:topic_id>/items/<item_id>` | `api_tracked_topic_item_update` | 7538 |
| POST | `/api/tracked-topics/rule-draft` | `api_tracked_topic_rule_draft` | 7218 |
| POST | `/api/update/apply` | `api_update_apply` | 7993 |
| GET | `/api/update/check` | `api_update_check` | 7960 |
| GET | `/api/update/status` | `api_update_status` | 7980 |
| GET | `/api/version` | `api_version` | 7950 |


## v0.2.5：新闻分类统计

`GET /api/news/classification` 的 `total/done/pending/running/failed` 按已保存的分类范围统计队列；新增 `coverage: {total, done}` 始终统计整个新闻库，包含已读和尚未排队的新闻。只有标题、规则版本均匹配且分类成功的新闻计入 `coverage.done`。设置页环形图使用 `coverage`，不受「仅对未读新闻分类」影响；四项队列计数仍使用原范围。

## v0.2.5：主题追踪接口

下列接口独立于上表旧版 `/api/tracked-*`，处理函数以 `api_event_topic` 开头。

| 方法 | 路径 | 契约 |
| --- | --- | --- |
| GET / POST | `/api/topics` | 列表及Jev配置状态；创建传 `{query,request_id}`，返回202及持久化主题 |
| GET | `/api/topics/<topic_id>` | 定义、messages对话、节点、匹配报道、任务状态；offset／limit分页，node可选筛选；每个节点的news_count为其关联的有效收录报道总数（与node筛选口径相同，不受分页影响）；topic.news_count始终为主题收录总数，不随node或分页变化，total为当前筛选的报道总数；topic同时返回last_refined_at、refine_available_at与refine_cooldown，按成功精简记录计算 |
| POST | `/api/topics/<topic_id>/messages` | 仅draft；传message（最多4000字）、request_id、rules_revision及definition对象（完整可编辑草稿），持久化问题并返回202；discuss任务回答或修订定义，不启动历史。重复相同请求幂等，不同内容复用标识报400，活动任务或版本冲突报409 |
| POST | `/api/topics/<topic_id>/initialize` | 确认name／definition／include_scope／exclude_scope／horizontal_rule／start_date及最新rules_revision；活动或失败的对话、版本冲突报409。新主题timeline_mode只接受events（默认），进入历史研究 |
| PATCH | `/api/topics/<topic_id>/definition` | ready主题（含已归档）提交name／definition／include_scope／exclude_scope／horizontal_rule及rules_revision；成功递增规则版本，只影响之后新入库新闻；版本冲突返回409并保留旧资料 |
| POST | `/api/topics/<topic_id>/retry` | 重试失败研究、归属判断或milestone横向筛选，保留共享Jev限流；milestone不读取Agent配置 |
| POST | `/api/topics/<topic_id>/seen` | 传详情返回的observed_at，仅清除相应主题待查看提示 |
| POST | `/api/topics/<topic_id>/archive` | 传布尔值 `{archived:true/false}`，仅ready可归档；归档停止匹配／整理，恢复重置首次入库边界，同状态重复请求幂等 |
| POST | `/api/topics/<topic_id>/refine` | 仅ready且未归档、没有待处理／失败任务时可用；成功后一个自然月内返回409/topic_refine_cooldown；使用已有非月度节点合并阶段里程碑，保留来源与新闻关系，返回202，失败可retry |
| DELETE | `/api/topics/<topic_id>` | 删除主题、定义对话、节点、匹配关系和任务；不删除新闻及阅读状态 |
