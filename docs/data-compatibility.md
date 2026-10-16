# 原数据库与数据输入兼容

项目和版本管理独立，数据接口沿用原 News Reader。`parser.py`、`scanner.py` 沿用原项目基线。`v0.0.2` 增加 `article_note_entries` 表以保存多条想法，保留原 `article_notes` 聚合内容供旧版、搜索和想法列表读取。旧单条想法首次追加时自动转为评论，原内容与时间保留；旧客户端整体编辑会作为一条新的完整想法显示。

研读评论另以 `article_note_metadata` 表保存作者、引用正文及原文／译文段落来源、Agent 归档摘要和会话 ID。原五列评论表及聚合文本保持兼容，删除评论的触发器同步清理元数据；重复启动不会改写已有内容和时间。旧版单独保存的 `【Chat 归档｜时间】` 评论可识别为 Agent，混合历史笔记不自动拆分或推定作者。

正文高亮继续使用 `article_highlights`，新增 `original` 类型供非推文原文使用。启动时按需扩展旧表约束，保留已有高亮 ID、颜色、批注及时间；重复启动不重建已升级的表。原文、译文、结论和要点分别保存定位信息，正文更新后沿用既有文本重新定位规则。

## 继续使用原数据库

原 `news_index.sqlite3` 可以直接打开，无需转换、导出或重建。数据库路径按以下顺序确定：

1. `NEWS_READER_DB_PATH` 环境变量。
2. `app_settings.json` 中的 `database.path`（也可在“设置 → 数据”选择）。
3. 已存在的项目父目录 `news_index.sqlite3`，兼容原安装布局。
4. 项目根目录下的 `news_index.sqlite3`，用于新安装。

本机原项目配置中的数据库是 `/Users/x/news-reader/news_index.sqlite3`。可以在项目目录这样启动：

```bash
NEWS_READER_DB_PATH='/Users/x/news-reader/news_index.sqlite3' ./启动NewsReader.command
```

也可以在新项目设置中选择这个文件，保存后重启。使用原库副本时，只需选择副本路径。路径由环境变量指定时，设置页会显示该状态。

新闻 ID、已读、重要、稍后再看、收藏、新闻想法、独立想法、原文和中文精读缓存沿用原数据。较早数据库中的 `bookmarked` 收藏标记继续通过原有兼容迁移映射为 `favorite_at`；重复启动不会重复迁移已完成的记录。已从界面移除功能的数据表保留，不因 UI 重构删除历史数据。

## 沿用原数据输入

默认目录仍为：

```text
/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents/DailyNews
```

继续递归扫描 `dailyFreshNews_*.md`，包括 `2026年5月/` 等子目录。原采集脚本、文件名称和 Markdown 段落格式无需调整。

同目录存在同名 `dailyFreshNews_YYYY-MM-DD.newsreader.json` 时，使用 `newsreader.daily.v1` 结构化数据；缺少 sidecar 时解析 Markdown，sidecar 无效时回退到 Markdown。JSON 仍以对应 Markdown 文件作为扫描入口。

`NEWS_READER_DAILY_NEWS_DIR` 继续覆盖输入目录。Markdown 或 sidecar 更新都会触发增量导入，日期和 URL 相同的新闻沿用相同 ID，从而保留原有状态和想法。读取和扫描不会修改输入文件。

## 验证

`tests/test_data_compatibility.py` 覆盖旧表结构启动、旧收藏标记、历史状态／想法／缓存保留，以及 Markdown、JSON 和 JSON 异常回退后的重复增量导入。`tests/test_settings.py` 覆盖原路径优先级和输入目录参数。

2026-10-15 使用本机原数据库的只读备份进行了验证：原库包含 28,527 条新闻；扫描原 DailyNews 的 162 个文件（110 个有效 sidecar、52 个纯 Markdown）后，临时副本增量至 28,785 条新闻。28,443 条阅读状态、102 条新闻想法、2 条独立／板块想法、1,270 条原文缓存、1,155 条中文精读、19 条划线及其他历史记录的内容校验均未变化，数据库完整性检查通过。验证未修改真实原库，也未将新项目的运行配置切换到原库。
