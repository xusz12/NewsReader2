# v0.0.4 设计 Token 与组件映射

视觉基线为 [DESIGN_SPEC_AND_TOKENS_Fix.md](../DESIGN_SPEC_AND_TOKENS_Fix.md) v2.3.7 生产基线对齐版。仓库中没有该文档提及的 `news-reader-workbench.html`，本轮依据修订文档中的明确数值与代码表实施，未宣称与缺失原型逐像素一致。旧版 [设计文档](DESIGN_SPEC_AND_TOKENS.md) 仅供历史对照。

## 交付文件

- [tokens.css](../static/tokens.css)：完整导入修订规范 8.1 的布局、字体、浅／深／墨水屏变量；跟随系统的深色分支与显式深色使用相同变量。
- [design-tokens.json](../static/design-tokens.json)：逐项保留修订规范 8.2 的名称、数值和格式。该示例覆盖布局、字号、字体、浅／深基础色及圆角；派生色、阴影和墨水屏完整定义以 CSS 为准。
- [style.css](../static/style.css)：实际组件消费规范变量；应用额外变量包括间距、动效、760px 阅读宽度、Agent 尺寸和收藏派生状态色。字号统一使用 `--fs-*`，额外的 `--fs-card-title: 15px` 对应规范 7.2，`--fs-caption: 11px` 用于紧凑辅助控件。

## 映射表

| 规范 | 生产组件 | 实际值与行为 |
| --- | --- | --- |
| `--header-height` | `.topbar` | 52px，各屏宽一致 |
| `--sidebar-width` | `.left-nav` | 桌面固定 248px；1280px 以下通过抽屉打开 |
| `--feed-width` | `.feed-column`、`#feedColSettings` | 双栏／三栏固定 430px，最小 360px；单栏宽 100% |
| 阅读列上限 | `.detail-content` | 最大 760px 居中；外层 `.detail-scroll-area` 占满右栏，隐藏滚动条外观，保留独立滚动 |
| 主标题 | `.article-title` | `--font-display`、`--fs-h1`、700／1.25 |
| 卡片标题与摘要 | `.news-card-title`、`.news-card-snippet` | 衬线 15px／600；摘要 13px、muted、两行截断 |
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
| 同页设置 | `#feedColSettings`、`#detailPanelSettings` | 五类配置沿用主布局；窄屏分类／详情流转 |

## 产品范围与刻意保留的差异

2026-10-16 用户截图反馈覆盖原规范中的选中卡片、稍后看与归档入口样式：选中改为整圈陶土红边框、浅色白底，稍后看恢复深底浅字，归档恢复仅图标；深色和墨水屏沿用对应主题的 surface／fg 以保持对比。主题选择改为设置页同款自绘菜单，统一顶栏控件高度，全应用隐藏滚动条显示。基线 Token 文件保留交付文档原值，覆盖在组件样式中明确实现。

本次修复针对视觉规范。延续 v0.0.2／v0.0.3 已确定的功能范围：阅读导航保留新闻流、稍后再看、重要新闻、收藏、想法；不重新引入日报、提醒、复盘、跟踪和板块。阅读器保留现有离散操作，标读快捷键与卡片稍后看流程保持原有语义；空笔记入口和 Agent 归档出处行的既有精简保持不变。设置继续按分类切换右栏配置，而非一次展示全部表单。

抓取、预计阅读时间、Agent runtime 与模型等信息来自真实数据；不将规范中的示例时间、计数、模型或沙盒标签作为生产状态。正文和卡片的现有 ID 不因原型命名而改动，以保留控制器与测试契约。

墨水屏在规范基础上将功能状态色、收藏派生色和高亮处理为黑白实色，并禁用 CSS 与评论 JavaScript 动画；键盘聚焦和触屏删除仍保持可用。

## 校验

`tests/test_design_tokens.py` 检查交付文档、CSS 基线与 JSON 的一致性，以及未定义 Token 和组件重复定义。`tests/ui/v004.spec.js` 检查浏览器计算样式、显式／系统主题、920／921／1279／1280px 边界、窗口缩放、导航焦点和设置流转。`tests/ui/design-feedback.spec.js` 覆盖截图反馈、主题菜单持久化与高度、仅图标归档及隐藏滚动条后的实际滚动。已有回归继续覆盖状态同步、笔记、Agent 与宽屏滚动。

2026-10-16 验证：640 项 Python、39 项 Node 通过；87 项 Playwright 用例完成回归，旧版断言更新后失败项复测通过，随后对受最终视觉微调影响的 12 项用例再次验证通过。截图位于 `.ui-artifacts/`，仅使用隔离测试数据；未修改真实新闻库。

2026-10-16 截图反馈复核：完整 91 项界面回归中 90 项直接通过；新增外观断言改为等待主题颜色过渡完成后，4 项反馈用例复测全部通过。另有 4 项 Token／版本检查通过。桌面和窄屏截图已复核，主题／模型菜单切换、键盘与触屏操作及实际滚动均已验证。
