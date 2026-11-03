import { escapeHTML as e } from "./ui.js";

// Keep explanations alongside the UI; changes to ingestion/deletion semantics
// should update these answers as well as the architecture documentation.
const groups = [
  {
    id: "sources", title: "数据来源", items: [
      { id: "origin", question: "newsreader 的新闻从哪里来？", paragraphs: [
        "新闻先由上游采集程序写入本机的 DailyNews 目录，newsreader 再把这些文件中的新闻导入当前新闻库，供浏览、筛选和研究。上游采集程序及其定时任务独立于 newsreader 运行。",
        "「信源与采集」中的信源列表来自已经导入的新闻；具体有哪些媒体、栏目或账号，取决于上游文件提供了哪些内容。",
      ] },
      { id: "formats", question: "Markdown 和配套 JSON 文件如何使用？", paragraphs: [
        "扫描以 dailyFreshNews_YYYY-MM-DD.md 为入口。有同名的 dailyFreshNews_YYYY-MM-DD.newsreader.json 时，优先使用有效的 JSON 数据；JSON 无法解析时回退到 Markdown，没有 JSON 时直接解析 Markdown。",
        "两种格式用于描述同一天的新闻，不会各导入一份。只有 JSON、没有对应 Markdown 的文件不会被单独扫描。",
      ] },
      { id: "refresh", question: "刷新索引会重新上网采集新闻吗？", paragraphs: [
        "刷新索引读取本机 DailyNews 文件，不会要求上游采集程序重新抓取网站。上游需要先将新内容写入文件，newsreader 才能在后续扫描时导入。",
        "新闻全文是另一条处理流程：通过本机 opencli 抓取，成功后缓存到本地；翻译、精读等功能再使用配置的模型服务处理。已有标题或摘要不代表全文已经抓取完成。",
      ] },
    ],
  },
  {
    id: "index", title: "扫描与索引", items: [
      { id: "range", question: "刷新时只扫描今天，还是也检查历史文件？", paragraphs: [
        "每次刷新都会递归检查 DailyNews 目录及其子目录中的所有日期文件，不限今天，也没有固定的回溯天数。文件名需要严格符合 dailyFreshNews_YYYY-MM-DD.md；带「 2」等后缀的同步副本不会作为新闻输入。",
        "检查所有文件不等于重新解析所有内容。只有从未成功扫描、扫描记录不完整或文件特征发生变化的文件，才会重新解析。",
      ] },
      { id: "record", question: "如何判断一个文件是否已经扫描过？", paragraphs: [
        "当前新闻库会按文件在 DailyNews 中的相对路径保存扫描记录。没有记录，就视为尚未扫描；成功处理文件后，新闻和扫描记录一起保存。重启应用不会丢失这些记录。",
        "判断是否变化时，会比较 Markdown 与配套 JSON 的修改时间、文件大小，以及 JSON 是否存在。特征未变且记录完整就跳过内容解析；特征变化则重新读取整个文件。这是文件特征比较，不是每次逐字比较全文。",
      ] },
      { id: "updates", question: "历史文件补入新闻，或已有新闻被修改，会怎样？", paragraphs: [
        "历史文件发生变化后，下次刷新会重新解析，所以后来补入的旧日期新闻也能入库。新闻通常按「报道日期 + URL」识别；没有 URL 时，按「日期 + 信源 + 标题」识别。相同标识更新已有条目，新标识新增条目。",
        "重新解析时，会移除该文件对应的索引中已经不再出现在输入里的条目。被你明确删除过的新闻还会经过删除指纹检查，避免从原始文件中恢复。",
      ] },
      { id: "timing", question: "什么时候会刷新新闻索引？", paragraphs: [
        "打开页面时会刷新索引，也可以使用顶栏刷新按钮，或「信源与采集」中的「重新扫描 DailyNews」。报告取材前也会刷新新闻索引。",
        "这些入口通常执行增量扫描：检查所有日期文件，只重新解析发生变化或尚无有效扫描记录的文件。界面上的任务进度与计数刷新不等于重新扫描新闻文件。",
      ] },
    ],
  },
  {
    id: "deletion", title: "信源管理与删除", items: [
      { id: "visibility", question: "关闭信源开关和删除信源有什么区别？", paragraphs: [
        "关闭信源开关并保存，只调整新闻流的显示范围，保留已有新闻和阅读状态；重新开启后可以继续查看。这个开关不会停止上游采集，也不会阻止文件被扫描。",
        "删除会清除当前新闻库中该信源已有的新闻及其关联数据。删除前会显示信源、日期范围、新闻数目与数据大小，并要求确认。",
      ] },
      { id: "scope", question: "删除信源会清除哪些数据？", paragraphs: [
        "清除该信源在当前新闻库中的新闻、阅读标记及对应的正文、译文、想法、高亮、任务和临时会话等关联数据；只属于这些新闻的媒体缓存也会清理。其他信源仍在使用的共用正文或缓存会保留。",
        "外部 DailyNews 原始文件、上游采集任务、已生成的报告与主题研究成果不随之删除，其他新闻库的数据也不受影响。删除操作无法撤销，确认前请核对范围。",
      ] },
      { id: "future", question: "删除后，旧新闻会回来吗？新新闻还会收录吗？", paragraphs: [
        "已删除的新闻会留下一个小型指纹。以后扫描原始文件时，命中指纹的新闻会被跳过；增量扫描、全量重建和重启后都会遵守这一规则。",
        "同一信源后续的新新闻仍会正常收录，后来补入、从未被删除过的历史报道也可以导入。判断依据是新闻标识，不是删除时间，也不是永久屏蔽整个信源。",
        "例如，日期和 URL 相同、仅修改标题的新闻仍会被识别为原新闻；日期或 URL 改变后可能成为新的新闻标识。",
      ] },
      { id: "fingerprints", question: "保存删除记录会占用很多空间吗？", paragraphs: [
        "每条删除记录只保存 32 字节的指纹，不保存标题、网址、摘要或正文，重复记录不会重复保存。包含数据库结构开销的合成数据实测中，1 万条约增加 0.4 MiB，10 万条约增加 4 MiB；实际大小会随数据库页面使用情况变化。",
        "记录随累计删除数量增长，并持续保留，以防仍存在于上游文件中的旧新闻在重新扫描时恢复。",
      ] },
    ],
  },
  {
    id: "local", title: "阅读与本地数据", items: [
      { id: "storage", question: "新闻和设置保存在哪里？", paragraphs: [
        "新闻索引、浏览状态和大部分关联数据保存在本机 SQLite 新闻库中，当前主数据库路径可在「数据库与存储」查看。媒体缓存、Agent 会话等还使用独立的本地文件或数据库。",
        "主题、阅读字号和快捷键等偏好保存在当前浏览器。清除浏览器站点数据会重置这些偏好，并不等同于清空本机新闻库；完整备份也不能只备份浏览器数据。",
      ] },
      { id: "size", question: "删除提示的数据大小，为什么不等于磁盘立刻减少的大小？", paragraphs: [
        "确认框统计的是待删除记录的内容大小与专属文件的实际大小，不包含 SQLite 索引、页面和空闲空间等全部开销。",
        "SQLite 删除记录后通常保留空闲页面供后续写入复用，因此数据库文件可能暂时不变小。删除时不会自动执行整库压缩；数据已清除与磁盘文件立即缩小是两件不同的事。",
      ] },
      { id: "reading", question: "「已浏览」和「已看完」有什么区别？", paragraphs: [
        "新闻浏览状态分为「未浏览／已浏览」，表示新闻是否已浏览或处理，不代表全文读完。标记重要、收藏或加入稍后再看，也会把新闻设为已浏览；取消这些操作不会退回未浏览。",
        "全文阅读状态分为「待阅读／已看完」，用于稍后再看，独立于新闻浏览状态。新闻流筛选中的「未读／已读」对应未浏览／已浏览，稍后再看中的「已读」对应已看完。",
      ] },
    ],
  },
];

export function settingsHelpHTML(accordion) {
  return groups.map(group => `<section class="settings-section-card" aria-labelledby="help-${e(group.id)}-title">
    <h3 class="section-card-title" id="help-${e(group.id)}-title">${e(group.title)}</h3>
    ${accordion.html(`help-${group.id}`, group.items, { headingLevel: 4 })}
  </section>`).join("");
}
