# 数据迁移与恢复

源码交付包不包含真实业务数据。迁移对象由双方确认，先用旧库的 SQLite 一致性备份完成演练，再在停写窗口执行正式切换。重建新闻索引不能重建阅读状态、想法、高亮和历史报告。

## 资产清单

| 资产 | 是否需要迁移 | 说明 |
| --- | --- | --- |
| 新闻主库 | 保留历史时必需 | 阅读、收藏、重要、正文、精读、想法、高亮、总结及兼容业务数据 |
| DailyNews 文件树 | 持续导入及完整重建时必需 | 保留 Markdown 与同名 JSON sidecar、原相对目录和文件命名 |
| 实际应用设置 | 选择性迁移 | 先审查旧绝对路径、模型和 provider；接手方可从模板重配 |
| `media-cache` | 需要保留已缓存媒体时迁移 | 必须与主库媒体索引配套；迁移时单独核对 |
| Agent 会话数据库及临时目录 | 通常不迁移 | 会话默认 72 小时过期，且关联本机运行时；重要结论先归档到新闻想法 |
| `.model-catalogs` 缓存 | 可重新生成 | 位于设置文件旁；迁移不是模型可用性的保证 |
| Keychain、CLI/浏览器凭据 | 由接手方重新配置 | 不复制到源码包；应用主库不代表完整认证环境 |
| 浏览器偏好及未保存草稿 | 不随 SQLite 迁移 | 主题等可能在 localStorage；未保存想法先保存 |
| 上游采集脚本与调度 | 另行交接 | 本仓库只有输入解析，无法重建采集系统 |

## 数据来源格式

扫描入口严格为 `dailyFreshNews_YYYY-MM-DD.md`，允许年份、月份子目录。类似 `dailyFreshNews_2026-10-21 2.md` 的同步副本不作为输入。同目录可有 `dailyFreshNews_YYYY-MM-DD.newsreader.json`，`schema_version` 为 `newsreader.daily.v1`，有效时优先使用 JSON；无效时回退 Markdown，并记录 ingest_warning。

仅放 JSON 而不放同名 Markdown 不会被扫描。模板位于 [examples](examples/dailyFreshNews_2026-10-21.md)。原文 URL 和解析日期参与稳定 ID 生成，迁移时不要顺手批量改写 URL、日期或源目录结构。

## 迁移前记录

在旧服务“设置 → 数据”确认当前生效的数据库路径，并记录是否由环境变量控制；确认输入、媒体、设置及会话路径。记录源码提交、Python/Node/opencli/CLI 版本、系统时区、数据大小和磁盘余量。当前实现大量使用无时区的本地时间字符串，迁移到不同时区前必须定义时间解释规则。

先保存浏览器中尚未提交的想法。停止提交新任务，等待或显式取消抓取、研究和总结；重要会话先归档。正式切换时停止旧应用和上游采集，避免新闻库与输入文件来自不同时间点。

## 制作一致性备份

以下命令只打开指定源库为只读，并拒绝覆盖已有备份。替换两个绝对路径后执行。SQLite backup API 能处理数据库日志中的有效数据，优于运行时仅复制 `.sqlite3` 主文件；正式整套迁移仍采用停写窗口。

```bash
python3 - '/absolute/source/news_index.sqlite3' '/absolute/backup/news_index.sqlite3' <<'PY'
from pathlib import Path
import sqlite3
import sys

source = Path(sys.argv[1]).expanduser().resolve(strict=True)
target = Path(sys.argv[2]).expanduser().resolve()
target.parent.mkdir(parents=True, exist_ok=True)
with target.open('xb'):
    pass
src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
dst = sqlite3.connect(target)
try:
    src.backup(dst)
    result = dst.execute('PRAGMA integrity_check').fetchall()
    if result != [('ok',)]:
        raise RuntimeError(result)
    print('backup integrity_check: ok')
finally:
    dst.close()
    src.close()
PY
```

如果备份失败，保留错误记录并换一个新的备份文件名重试；不要把部分文件当作成功备份。将 DailyNews、媒体目录、经审查的设置副本与这份数据库放入同一批次目录，记录时间和校验值。业务数据的传输路径及访问范围由双方数据负责人确认。

## 核验备份与迁移副本

以下只读检查适用于旧库和新库；表不存在时输出“absent”，不会创建或迁移表。应分别保存启动前后结果。表级计数用于快速比对，重要状态和想法还需抽样或按键比对内容。

```bash
python3 - '/absolute/backup/news_index.sqlite3' <<'PY'
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1]).expanduser().resolve(strict=True)
c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
try:
    print('integrity_check:', c.execute('PRAGMA integrity_check').fetchall())
    present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = ('items', 'item_state', 'article_details', 'article_ai',
              'article_notes', 'article_note_entries', 'article_note_metadata',
              'article_highlights', 'standalone_ideas', 'news_summaries')
    for table in tables:
        print(table, c.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] if table in present else 'absent')
    if 'item_state' in present:
        columns = {r[1] for r in c.execute('PRAGMA table_info(item_state)')}
        for col in ('read_at', 'important_at', 'favorite_at', 'read_later_at', 'read_later_done_at'):
            if col in columns:
                print(col, c.execute('SELECT COUNT(*) FROM item_state WHERE "' + col + '" IS NOT NULL').fetchone()[0])
finally:
    c.close()
PY
```

## 恢复演练与正式切换

1. 保留备份原件不动，另外复制出可写的迁移副本。先在本机 127.0.0.1 启动，数据与旧服务完全隔离。
2. 显式设置主库、输入、设置、Agent 库、媒体及运行时路径。不要让新实例误用旧实例的 Agent 库；保留主库之外的独立会话策略。
3. 启动前采集上面的完整性和计数结果。启动应用会执行兼容迁移；较旧库新增列、表或收藏映射后，某些结构或计数变化可能是预期结果，应记录原因。旧单条想法的逐条迁移还可能在首次追加评论时触发。
4. 先检查既有状态、正文、精读、新闻想法、独立想法、高亮和历史总结。再触发普通增量导入，确认重复执行不重复入库、不丢状态。页面进入时会自动请求索引，需提前准备完整输入副本。
5. 对至少一条历史记录验证“新闻流已读”和“全文已看完”相互独立；核对跨日期相同 URL 的正文及想法共享；抽查带引用和 Agent 作者元数据的评论。
6. 完成抓取与模型配置后，在迁移副本上按验收清单走完新建、保存、重启和失败恢复。记录预期的新增业务数据，避免把测试写入误认为迁移差异。
7. 正式停写后重新制作最终快照，重复已通过的步骤，双方确认计数、抽样结果、版本和有效路径后开放使用。接手前保留旧实例和快照供回退。

当前没有独立 schema 版本迁移工具和自动数据库降级。升级失败时，停止新实例，使用旧版本代码与升级前快照的恢复副本启动；新版本期间新增的业务数据需单独核对后再决定如何合并，不能直接覆盖旧备份。
