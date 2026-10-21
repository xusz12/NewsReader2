# 设计 Token 与组件映射（含后续调整）

视觉基线为 [DESIGN_SPEC_AND_TOKENS_Fix.md](../DESIGN_SPEC_AND_TOKENS_Fix.md) v2.3.7 生产基线对齐版。原型 `news-reader-workbench.html` 未纳入仓库；生产实现以修订文档明确数值及后续约定为准，不宣称与外部原型逐像素一致。旧版 [设计文档](DESIGN_SPEC_AND_TOKENS.md) 仅供历史对照。

## 依据优先级

根 `AGENTS.md` 与后续已记录的用户调整优先，其次为当前组件映射及对应 CHANGELOG，再参考修订规范和原型历史。原型与修订规范并非处处同值：例如原型手机顶栏50px、正文15px/1.75，而生产已按修订规范采用52px、14px/1.7。不得用旧原型覆盖后来确认的产品交互。

## 交付文件

- [tokens.css](../static/tokens.css)：完整导入修订规范 8.1 的布局、字体、浅／深／墨水屏变量；跟随系统的深色分支与显式深色使用相同变量。
- [design-tokens.json](../static/design-tokens.json)：逐项保留修订规范 8.2 的名称、数值和格式。该示例覆盖布局、字号、字体、浅／深基础色及圆角；派生色、阴影和墨水屏完整定义以 CSS 为准。
- [style.css](../static/style.css)：实际组件消费规范变量；应用额外变量包括间距、动效、760px 阅读宽度、Agent 尺寸和收藏派生状态色。主阅读组件使用 `--fs-*`；部分辅助控件仍有固定字号，尚未统一为角色 Token。额外的 `--fs-card-title: 15px` 对应规范 7.2，`--fs-caption: 11px` 用于紧凑辅助控件。

## 映射表

| 规范 | 生产组件 | 实际值与行为 |
| --- | --- | --- |
| `--header-height` | `.topbar` | 52px，各屏宽一致 |
| `--sidebar-width` | `.left-nav` | 桌面固定 248px；1280px 以下通过抽屉打开 |
| `--feed-width` | `.feed-column`、`#feedColSettings` | 双栏／三栏固定 430px，最小 360px；单栏宽 100% |
| 阅读列上限 | `.detail-content` | 最大 760px 居中；外层 `.detail-scroll-area` 占满右栏，隐藏滚动条外观，保留独立滚动 |
| 主标题 | `.article-title` | `--font-display`、`--fs-h1`、700／1.25 |
| 卡片标题与摘要 | `.news-card-title`、`.news-card-snippet` | 衬线 15px／600；摘要 13px、muted、普通摘要两行截断；v0.0.14起已抓取新闻的精读结论完整显示 |
| 正文 | `.article-body`、`.digest-point-item`、`.saved-note-content` | 14px／1.7；正文段落显式锁定 1.7 |
| 卡片／浮层圆角 | `.news-card`、`.saved-note`、`.agent-dock` | 8px／8px／12px |
| 焦点与选中 | `:focus-visible`、`.news-card.is-active` | 2px accent 焦点环、2px 偏移；选中卡片整圈 1px accent 边框及 surface 底色（浅色为白底） |
| 三联动作条 | `.card-action-btn[data-state]` | 重要为 accent，稍后看为 fg 底色／surface 文字，收藏图标为 `oklch(68% 0.16 75)`；状态仍与 API 单源同步 |
| 离散阅读控件 | `.reader-tool-btn` | 桌面高 28px、圆角 6px；移动端扩大触控区域 |
| 收折 Agent | `.agent-dock.minimized` | 高 42px，使用 `--shadow-float`；展开高 520px，受可用高度约束；移动端展开为 Bottom Sheet |
| 想法展示区 | `.digest-thoughts` | `1px dashed var(--border)`，悬停切换 strong；空状态保留既有紧凑入口 |
| 想法输入器 | `#researchNoteComposer` | 对应规范 `#thoughtComposer`；默认 hidden，保留发布／取消、草稿与快捷键 |
| 想法流 | `.thought-timeline-entry`、`.saved-note` | 对应规范 `.thought-stream-item`；Agent 卡片保留 accent 左边框、身份和徽标 |
| 删除动作 | `.note-delete-btn` | 对应规范 `.thought-item-delete-btn`；默认透明且不可点击，卡片悬停 0.65，按钮悬停／键盘聚焦 1；触屏常显 |
| Agent 归档 | `#archiveAgentBtn` | 28px 仅图标按钮，保留提示与可访问名称 |
| 顶栏控件 | `.topbar-tool-btn`、`.theme-select` | 桌面高 32px，单栏高 36px，垂直居中且等高 |
| 全应用下拉框 | `SelectControls(document.body)` | 主题与所有设置共用自绘菜单，保留键盘、触屏、焦点恢复与主题持久化 |
| 滚动容器 | 新闻列表、阅读区、设置、Agent、菜单等 | 全局隐藏滚动条外观，保留原有 overflow 和滚轮／触屏／键盘滚动 |
| 同页设置 | `#feedColSettings`、`#detailPanelSettings` | 六类配置（模型与 Agent、DeepSeek 精读与翻译、信源与采集、数据库与存储、积压新闻总结范围、系统与版本）沿用主布局；窄屏分类／详情流转 |

## 2026-10-21 组件修复规则

- 主按钮与发布想法按钮保留原 `--accent` 底色，使用组件文字色 `--accent-button-fg`：浅色及墨水屏白字，显式深色及跟随系统深色使用该主题 `--bg` 深色字。普通文字的默认与悬停对比目标均不低于4.5:1；不改变链接和正文强调色。
- 模型及翻译连接测试终端在墨水屏使用 `--surface-subtle` 灰底、`--fg` 黑字和1px `--border`；浅色、深色保留终端现有样式。
- 独立想法正文仅在 `:focus-visible` 呈现2px accent外圈、2px偏移；失焦恢复无边框轻量编辑。设置详情h2明确600字重，字号与行高不变。
- 独立想法/总结的新增排版尺度及编辑器节点选择态尚需单独确认，不在本轮修复中改变。

## Agent 回复通知

顶栏右侧通知铃铛采用无边框图标按钮，默认透明边框、muted文字，悬停／键盘聚焦显示边界，待看状态使用accent和独立小圆点；墨水屏同时显示黑色边框，保留可访问名称和待看条数的屏幕阅读提示。下拉沿用surface、fg、muted、border-strong、radius-lg及shadow-float，手机固定在顶栏下并限制在屏宽内，移动底栏三项不变。通知按会话合并；点击跳转并定位具体回复，实际可见后才确认，打开列表本身不清除。

无全文的 Agent 会话在聊天区上方显示标题资料引用与“尚未提供全文”，沿用fg、muted、border及fs-meta；标题换行并限制为两行，完整标题保留在title说明中。标题提示与用户划线引用分别显示，不占用问题输入框。独立想法的语法速查仅随标题／正文／速查区焦点显示，移出后隐藏，保留折叠偏好与示例复制。

## 产品范围与刻意保留的差异

2026-10-16 用户截图反馈覆盖原规范中的选中卡片、稍后看与归档入口样式：选中改为整圈陶土红边框、浅色白底，稍后看恢复深底浅字，归档恢复仅图标；深色和墨水屏沿用对应主题的 surface／fg 以保持对比。主题选择改为设置页同款自绘菜单，统一顶栏控件高度，全应用隐藏滚动条显示。基线 Token 文件保留交付文档原值，覆盖在组件样式中明确实现。

本次修复针对视觉规范。延续 v0.0.2／v0.0.3 已确定的功能范围：阅读导航保留新闻流、稍后再看、重要新闻、收藏、想法；不重新引入日报、提醒、复盘、跟踪和板块。阅读器保留现有离散操作，标读快捷键与卡片稍后看流程保持原有语义；空笔记入口和 Agent 归档出处行的既有精简保持不变。设置继续按分类切换右栏配置，而非一次展示全部表单。

抓取、预计阅读时间、Agent runtime 与模型等信息来自真实数据；不将规范中的示例时间、计数、模型或沙盒标签作为生产状态。正文和卡片的现有 ID 不因原型命名而改动，以保留控制器与测试契约。

墨水屏在规范基础上将功能状态色、收藏派生色和高亮处理为黑白实色，并禁用 CSS 与评论 JavaScript 动画；键盘聚焦和触屏删除仍保持可用。

## 校验

`tests/test_design_tokens.py` 检查交付文档、CSS 基线与 JSON 的一致性，以及未定义 Token 和组件重复定义。`tests/ui/v004.spec.js` 检查浏览器计算样式、显式／系统主题、920／921／1279／1280px 边界、窗口缩放、导航焦点和设置流转。`tests/ui/design-feedback.spec.js` 覆盖截图反馈、主题菜单持久化与高度、仅图标归档及隐藏滚动条后的实际滚动。已有回归继续覆盖状态同步、笔记、Agent 与宽屏滚动。

2026-10-16 验证：640 项 Python、39 项 Node 通过；87 项 Playwright 用例完成回归，旧版断言更新后失败项复测通过，随后对受最终视觉微调影响的 12 项用例再次验证通过。截图位于 `.ui-artifacts/`，仅使用隔离测试数据；未修改真实新闻库。

2026-10-16 截图反馈复核：完整 91 项界面回归中 90 项直接通过；新增外观断言改为等待主题颜色过渡完成后，4 项反馈用例复测全部通过。另有 4 项 Token／版本检查通过。桌面和窄屏截图已复核，主题／模型菜单切换、键盘与触屏操作及实际滚动均已验证。

## v0.1.1 界面修订

更新结果使用surface-subtle、border-strong和fg，主要结论为fs-body／600，说明与时间为muted／fs-meta，状态同时通过文本表达。刷新索引与铃铛共用topbar-ghost-btn，只影响这两个按钮。上游报错采用通知同类surface、border-strong、radius-lg、shadow-float非模态面板，视口两侧至少12px，内部记录滚动；与通知互斥。Agent textarea默认两行，桌面约八行／手机约五行和可见视口三分之一双重上限，手机16px，发送按钮固定可操作，软键盘视口变化重新布局。

共享应用确认框复用catchup-dialog的surface-raised、border-strong、radius-lg和shadow-float；动作文本采用纯文本，默认取消，关闭均取消，焦点留在框内并返回可用触发按钮。报错日历沿用surface／fg／muted、七列日期和焦点环，当前日用边框/下划线、选中日用fg/surface反色区分；日期文本输入校验错误就地显示，月份浏览不触发查询。
