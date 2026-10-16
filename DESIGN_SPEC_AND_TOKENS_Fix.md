# News Reader 2.0 设计规范与 Design Tokens 映射交付文档
# (Design System Specification & Tokens Reference)

> **版本**：v2.3.7 生产基线对齐版\
> **基准原型**：`news-reader-workbench.html`（100% 单一真实源 Single Source of Truth）\
> **设计语言**：Financial & Research Editorial (报刊级沉浸式深度研读与金融研究系统)\
> **适用平台**：Web / Desktop App (Electron/Tauri) / Tablet / Mobile Web\
> **标准规范组织参考**：W3C Design Tokens Community Group (DTCG), WCAG 2.1 AAA/AA, Apple HIG

---

## 目录 (Table of Contents)

1. [体系架构与单一真实源契约 (Architecture & SSOT Contract)](#1-体系架构与单一真实源契约-architecture--ssot-contract)
2. [色彩系统与感知对比度 (Color System & OKLch Palette)](#2-色彩系统与感知对比度-color-system--oklch-palette)
   - 2.1 [浅色主题 (Light / Paper Edition)](#21-浅色主题-light--paper-edition)
   - 2.2 [深色主题 (Dark / Obsidian Slate)](#22-深色主题-dark--obsidian-slate)
   - 2.3 [墨水屏模式 (E-Ink / Pure High Contrast)](#23-墨水屏模式-e-ink--pure-high-contrast)
   - 2.4 [派生工具色与状态语义色](#24-派生工具色与状态语义色)
3. [字体排印与阅读尺度系统 (Typography & Type Hierarchy)](#3-字体排印与阅读尺度系统-typography--type-hierarchy)
   - 3.1 [三大字体栈 (Font Stacks)](#31-三大字体栈-font-stacks)
   - 3.2 [字号比例系统 (`--fs-*`)](#32-字号比例系统---fs-)
   - 3.3 [行高与行长正文黄金规范](#33-行高与行长正文黄金规范)
4. [三栏工作台布局与网格尺度 (3-Column Layout & Spacing Metrics)](#4-三栏工作台布局与网格尺度-3-column-layout--spacing-metrics)
   - 4.1 [物理三栏核心尺寸 (Core Layout Dimensions)](#41-物理三栏核心尺寸-core-layout-dimensions)
   - 4.2 [响应式断点与分层降级](#42-响应式断点与分层降级)
5. [圆角、边框发丝与动态阴影 (Radii, Hairlines & Dynamic Shadows)](#5-圆角边框发丝与动态阴影-radii-hairlines--dynamic-shadows)
6. [交互工学与对偶变色契约 (Interaction States & Color Pairing)](#6-交互工学与对偶变色契约-interaction-states--color-pairing)
7. [核心业务组件级设计规范 (Component-Level Design Specs)](#7-核心业务组件级设计规范-component-level-design-specs)
   - 7.1 [左侧双组导航栏 (Two-Tier Left Navigation)](#71-左侧双组导航栏-two-tier-left-navigation)
   - 7.2 [中栏卡片流与底部三联动作条 (Feed Card & Tri-Action Bar)](#72-中栏卡片流与底部三联动作条-feed-card--tri-action-bar)
   - 7.3 [阅读器顶栏与离散标读工具组 (Reader Topbar & Discrete Actions)](#73-阅读器顶栏与离散标读工具组-reader-topbar--discrete-actions)
   - 7.4 [常驻收折式 Agent (Docked Collapsed Agent)](#74-常驻收折式-agent-docked-collapsed-agent)
   - 7.5 [系统与模型设置三栏工作台 (In-Page Settings Workbench)](#75-系统与模型设置三栏工作台-in-page-settings-workbench)
   - 7.6 [研读想法展示区与社交化评论流 (Research Notes Stream & Agent Archive)](#76-研读想法展示区与社交化评论流-research-notes-stream--agent-archive)
8. [标准生产级代码产物 (Production-Ready Code Assets)](#8-标准生产级代码产物-production-ready-code-assets)
   - 8.1 [完整 CSS 变量表 (`tokens.css`)](#81-完整-css-变量表-tokenscss)
   - 8.2 [W3C DTCG 标准 JSON 规范 (`design-tokens.json`)](#82-w3c-dtcg-标准-json-规范-design-tokensjson)

---

## 1. 体系架构与单一真实源契约 (Architecture & SSOT Contract)

本规范与 `news-reader-workbench.html` 中的 CSS 自定义属性（Custom Properties）**100% 严格一致**。原型即为代码落地的单一真实源（Single Source of Truth），所有在规范中出现的 Token 均在原型代码中存在直接对应的变量或类名规则。

```
┌────────────────────────────────────────────────────────┐
│  Tier 1: Global Primitive Tokens (基础基元)            │
│  - 定义纯粹物理值：OKLch 色相基底、标准字族、布局基底尺寸      │
│  - 例：--header-height, --sidebar-width, --feed-width  │
└───────────────────────────┬────────────────────────────┘
                            │ 映射 (Referenced by)
┌───────────────────────────▼────────────────────────────┐
│  Tier 2: Semantic System Tokens (系统语义)             │
│  - 驱动多主题切换 (Light/Dark/E-Ink)，承载明暗感知对比度 │
│  - 例：--bg, --surface, --fg, --muted, --accent, --border│
└───────────────────────────┬────────────────────────────┘
                            │ 消费 (Consumed by)
┌───────────────────────────▼────────────────────────────┐
│  Tier 3: Component Scoped Tokens (组件级映射)          │
│  - 对应具体组件的类名状态与样式规则                     │
│  - 例：.thought-stream-card, .agent-dock.minimized     │
└────────────────────────────────────────────────────────┘
```

---

## 2. 色彩系统与感知对比度 (Color System & OKLch Palette)

全量采用 **OKLch (Oklab Cylindrical)** 感知均匀色彩空间，在不同色相与明度变化下彻底杜绝 HSL/RGB 的明度漂移问题。

### 2.1 浅色主题 (Light / Paper Edition)
呈现温暖低反光的金融时报/彭博专业报刊纸张质感：

| CSS 变量 | OKLch 定义值 | 感知角色与使用场景 | 相对底色对比度 |
| :--- | :--- | :--- | :---: |
| `--bg` | `oklch(98.5% 0.003 95)` | 整个视口与工作台背板纸张底色 | 1.0 : 1 (基准) |
| `--surface` | `oklch(100% 0 0)` | 卡片、面板、正文阅读器纯白底色 | 1.05 : 1 |
| `--surface-subtle` | `oklch(96.8% 0.005 95)` | 悬浮态、标签胶囊底、次级条带底色 | 1.08 : 1 |
| `--surface-raised` | `oklch(100% 0 0)` | 模态弹窗、常驻 Agent 浮层底色 | 1.05 : 1 (带浮动投影) |
| `--fg` | `oklch(20% 0.015 65)` | 严谨沉稳的深石墨黑正文字体 | **13.5 : 1** (远超 AAA 级 7:1) |
| `--muted` | `oklch(48% 0.012 65)` | 次要说明、作者出处、时间戳灰调 | **4.9 : 1** (满足 AA 级 4.5:1) |
| `--border` | `oklch(91% 0.006 90)` | 极简 1px 发丝分割线与卡片边框 | 1.25 : 1 |
| `--border-strong` | `oklch(80% 0.012 90)` | 聚焦、激活态、虚线容器边框高光 | 2.1 : 1 |
| `--accent` | `oklch(50% 0.14 32)` | 报刊级陶土红 (Financial Red)，核心品牌与重要新闻高光 | **5.2 : 1** (满足 AA 级) |

### 2.2 深色主题 (Dark / Obsidian Slate)
以 **色相角 250° (Obsidian Blue-Gray 冷石墨蓝灰)** 为基底，避免发黄发乌，营造专业金融终端夜间沉浸感：

| CSS 变量 | OKLch 定义值 | 感知角色与使用场景 | 相对底色对比度 |
| :--- | :--- | :--- | :---: |
| `--bg` | `oklch(16% 0.01 250)` | 暗色视口底板 | 1.0 : 1 (基准) |
| `--surface` | `oklch(20% 0.012 250)` | 卡片、面板、阅读器深色底板 | 1.25 : 1 |
| `--surface-subtle` | `oklch(23% 0.012 250)` | 悬浮项高光底色、条带底板 | 1.5 : 1 |
| `--surface-raised` | `oklch(25% 0.015 250)` | 浮层、Agent 窗口深色底板 | 1.7 : 1 |
| `--fg` | `oklch(93% 0.005 250)` | 柔和高反差文字白 | **12.8 : 1** (远超 AAA 级) |
| `--muted` | `oklch(66% 0.01 250)` | 暗色次级说明文字与时间戳 | **5.4 : 1** (满足 AA 级) |
| `--border` | `oklch(27% 0.012 250)` | 极细发丝暗部分割线 | 1.8 : 1 |
| `--border-strong` | `oklch(38% 0.015 250)` | 激活项边框与焦点高光 | 3.2 : 1 |
| `--accent` | `oklch(66% 0.16 35)` | 提升明度后的陶土橙红，保障夜间可见性 | **6.1 : 1** (满足 AA 级) |

### 2.3 墨水屏模式 (E-Ink / Pure High Contrast)
面向电子纸与高反光户外设备，完全停用抗锯齿过渡与半透明混合：

```css
[data-theme="eink"] {
  --bg:             #ffffff;
  --surface:        #ffffff;
  --surface-subtle: #f4f4f4;
  --surface-raised: #ffffff;
  --fg:             #000000;
  --muted:          #555555;
  --border:         #000000;
  --border-strong:  #000000;
  --accent:         #000000;
  --accent-soft:    #e5e5e5;
  --accent-hover:   #222222;
  --tag-bg:         #ebebeb;
  --shadow-subtle:  none;
  --shadow-float:   0 4px 0 #000000;
}
```

### 2.4 派生工具色与状态语义色

#### 1. 动态派生色 (CSS `color-mix` Derived)
```css
--accent-soft:     color-mix(in oklch, var(--accent) 12%, transparent);
--accent-hover:    oklch(45% 0.16 32); /* 暗色下为 oklch(72% 0.17 35) */
--fg-soft:         color-mix(in oklch, var(--fg) 6%, transparent);
--tag-bg:          color-mix(in oklch, var(--fg) 5%, transparent);
```

#### 2. 四大状态语义色 (Functional Status Colors)
- **成功 / 已读 (`--status-success`)**：`oklch(54% 0.14 145)`，用于标记已读墨绿色、在线心跳；
- **警告 / 稍后看 (`--status-warning`)**：`oklch(66% 0.15 65)`，用于稍后再看时钟黄色徽标；
- **危险 / 重要 (`--status-danger`)**：`oklch(52% 0.18 28)`，用于重要新闻陶土红旗标；
- **信息 / 链接 (`--status-info`)**：`oklch(56% 0.14 250)`，用于 opencli 抓取就绪与工具调用指示；
- **收藏高光（组件内专属）**：`oklch(68% 0.16 75)`，用于高光收藏星标的点亮琥珀金色。

---

## 3. 字体排印与阅读尺度系统 (Typography & Type Hierarchy)

### 3.1 三大字体栈 (Font Stacks)
```css
/* 1. 标题与核心论断展示字族：报刊级典雅衬线 */
--font-display: 'Iowan Old Style', 'Charter', 'Source Serif Pro', Georgia, serif;

/* 2. 界面与阅读正文字族：现代系统无衬线 */
--font-body:    -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;

/* 3. 键位、时间戳与数据表字族：严谨等宽字族 */
--font-mono:    ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace;
```

### 3.2 字号比例系统 (`--fs-*`)
与 HTML 原型 `:root` 变量 100% 对齐：

| 变量名称 | 定义值 / 响应式公式 | 典型应用场景 | 字重与行高 |
| :--- | :--- | :--- | :--- |
| `--fs-h1` | `clamp(24px, 2.6vw, 34px)` | 新闻主标题、全天日报大标题 | `font-weight: 700; line-height: 1.25;` |
| `--fs-h2` | `clamp(18px, 2.0vw, 24px)` | 正文二级小节标题、看板卡片标题 | `font-weight: 600; line-height: 1.35;` |
| `--fs-h3` | `16px` | 三级标题、设置分类卡片标题 | `font-weight: 600; line-height: 1.4;` |
| `--fs-body` | `14px` | 正文阅读文本、卡片摘要、想法正文 | `font-weight: 400; line-height: 1.7;` |
| `--fs-small` | `13px` | 列表项副标题、工具条按钮文本、元数据 | `font-weight: 500; line-height: 1.5;` |
| `--fs-meta` | `12px` | 信源来源、发布时间戳、键位提示徽标 | `font-weight: 500; line-height: 1.4;` |

### 3.3 行高与行长正文黄金规范
- **黄金阅读行高**：正文段落（`.article-prose p`）强制锁定 `line-height: 1.7`，为长文扫读提供充分的呼吸感；
- **黄金阅读列宽**：正文阅读区域最大宽度锁定 `max-width: 760px; margin: 0 auto;`，单行中文字符控制在 **35~45 字**，英文控制在 **65~75 字符**，杜绝横向视觉摆头疲倦。

---

## 4. 三栏工作台布局与网格尺度 (3-Column Layout & Spacing Metrics)

### 4.1 物理三栏核心尺寸 (Core Layout Dimensions)
与 HTML 原型 `:root` 变量 100% 对齐：

| 布局变量 | 物理尺寸 | 弹性与约束 | 承载业务模块 |
| :--- | :---: | :--- | :--- |
| `--header-height` | **`52px`** | 固定高度，`flex-shrink: 0` | 顶部系统状态栏，品牌刊头、抓取状态、快捷键帮助、主题切换器 |
| `--sidebar-width` | **`248px`** | 固定宽度，`flex-shrink: 0` | 左侧两级导航栏（阅读组、研究组、信源过滤、底栏设置入口） |
| `--feed-width` | **`430px`** | 固定宽度，`min-width: 360px` | 中栏高信息密度卡片流（新闻流、日报、提醒、想法、设置分类流） |
| **右栏工作台** | **`flex: 1`** | 自适应弹性延伸 | 深度阅读器、日报工作台、设置表单面板、底部常驻收折 Agent |

### 4.2 响应式断点与分层降级
- **桌面全屏 (`≥ 1280px`)**：全展标准三栏模式，左栏常驻（248px）、中栏卡片流（430px）、右栏弹性工作台（自适应）；
- **平板 / 窄屏 (`921px ~ 1279px`)**：左栏收起为紧凑图标抽屉或可通过抽屉滑出，中栏与右栏并排；
- **移动端 (`≤ 920px`)**：
  - 自动切换为原生多层级流转体验；
  - 默认全屏展示中栏卡片流（`width: 100%`）；
  - 点击卡片后，平滑滑入全屏详情页，顶栏提供一键返回按钮；
  - 常驻收折 Agent 升格为原生 Bottom Sheet 抽屉浮窗。

---

## 5. 圆角、边框发丝与动态阴影 (Radii, Hairlines & Dynamic Shadows)

### 5.1 圆角体系 (Radii Tokens)
与 HTML 原型 `:root` 变量 100% 对齐：

```css
--radius-sm: 6px;   /* 按钮、输入框、微型徽标圆角 */
--radius:    8px;   /* 新闻卡片、设置卡片、研读想法卡片圆角 */
--radius-lg: 12px;  /* 浮动面板、常驻 Agent Dock、全景模态框圆角 */
```

### 5.2 边框发丝与虚线隐喻 (Hairlines & Borders)
- **实体发丝边框**：全站通用 `border: 1px solid var(--border)`（91% 浅灰 / 27% 暗灰）；
- **主观研读虚线边框**：正文研读想法展示区专门采用 `border: 1px dashed var(--border)`，建立纸质边注（Marginalia）的主观隐喻；鼠标悬浮时提亮为 `border-color: var(--border-strong)`。

### 5.3 动态阴影系统 (Dynamic Shadows)
通过 `color-mix` 动态混合当前 `--fg`，确保阴影在任意主题和对比度下均保持自然漫反射：

```css
/* 轻度微浮阴影（用于卡片悬浮、小菜单） */
--shadow-subtle: 0 1px 3px color-mix(in oklch, var(--fg) 6%, transparent),
                 0 4px 12px color-mix(in oklch, var(--fg) 4%, transparent);

/* 高度浮空阴影（用于常驻 Agent Dock、模态弹窗） */
--shadow-float:  0 12px 36px color-mix(in oklch, var(--fg) 12%, transparent);
```

---

## 6. 交互工学与对偶变色契约 (Interaction States & Color Pairing)

### 6.1 成对对偶变色原则 (Pairwise Inversion Principle)
严禁单向调暗文字或只改背景导致对比度缩水。交互状态变化必须保证前景色与背景色成对调优：

```css
/* 规范示例：离散按钮默认态与悬停激活态 */
.action-btn {
  background: transparent;
  color: var(--fg);
  border: 1px solid transparent;
}
.action-btn:hover {
  background: var(--surface-subtle);
  border-color: var(--border);
  color: var(--fg);
}
.action-btn.is-active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: color-mix(in oklch, var(--accent) 30%, transparent);
}
```

### 6.2 键盘焦点与可访问性契约
- **全局焦点环**：凡获得键盘焦点的元素，统一呈现外圈微高光：
  `outline: 2px solid var(--accent); outline-offset: 2px;`
- **中栏键盘导引**：通过 `j`/`k` 切换焦点时，卡片获得 `.is-active` 类，呈现微光背景与陶土红左边框：
  `border-left: 3px solid var(--accent); background: var(--surface-subtle);`

---

## 7. 核心业务组件级设计规范 (Component-Level Design Specs)

### 7.1 左侧双组导航栏 (Two-Tier Left Navigation)
- **容器规格**：宽 `var(--sidebar-width)` (248px)，发丝右边框；
- **分级分组架构**：
  1. **【阅读组】**：`新闻流 (42)`、`稍后再看 (15)`、`重要新闻`、`收藏 (8)`、`想法 (24)`；
  2. **【研究组】**：`日报 (今日最新)`、`提醒 (2到期)`、`复盘 (2到期)`、`跟踪 (5)`、`板块 (市场)`；
  3. **【系统底栏】**：常驻信源过滤抽屉按钮与系统级**「设置」**入口（`#leftNavSettingsBtn`，内嵌等宽 `,` 键位徽标）；
- **交互规范**：点击任意项，在同页中栏与右栏展开，导航常驻高亮，不发生页面跳转。

### 7.2 中栏卡片流与底部三联动作条 (Feed Card & Tri-Action Bar)
- **容器规格**：宽 `var(--feed-width)` (430px)，独立纵向滚动；
- **卡片解构**：
  - 顶栏：信源 Favicon + 媒体名称 + 发布时间戳 + 预计阅读时长；
  - 标题：`--font-display` 衬线大字（15px，600 字重）；
  - 摘要：两行截断文本（13px，`--muted`）；
  - **底部三联微动作条**：
    - `⚡ 重要 (I)`：点击切换陶土红激活态；
    - `🕒 稍后再看 (S)`：点击切换时钟黄色激活态；
    - `⭐ 收藏 (F)`：点击切换琥珀金色激活态；
    - 与阅读器顶栏及左栏计数实时双向单源同步。

### 7.3 阅读器顶栏与离散标读工具组 (Reader Topbar & Discrete Actions)
- **空间解耦**：彻底移除灰色连体 Tab 框，采用国际顶级生产力软件（Linear / Readwise）的离散微按钮组；
- **左侧元数据**：`opencli 全文就绪 · 14:04 抓取`（带微呼吸绿点与 Mono 时间戳）；
- **右侧工具群**：
  - `查看原文 ↗`（直达原始链接）；
  - 发丝垂线分割；
  - `标记已读 (M)`、`重要 (I)`、`稍后看 (S)`、`收藏 (F)`（高 28px，圆角 6px，内嵌 Mono 键位提示徽标）。

### 7.4 常驻收折式 Agent (Docked Collapsed Agent)
- **默认收折态 (`.agent-dock.minimized`)**：
  - 高度：**`42px`**，停靠于右下角，微晶立体阴影；
  - 排印：左侧微晶星芒矢量 + `新闻研究 Agent` + `Codex · read-only` 运行沙盒；中间等宽按键标签 `点击展开 · A`；右侧折叠箭头指示；
- **展开态 (`.agent-dock`)**：
  - 高度：**`520px`**，支持最大化切换；
  - 顶栏配备 **`✦ 归档至想法`** 操作胶囊，支持将研读对话提炼为一条由 Agent 发布的想法沉淀至正文想法流中。

### 7.5 系统与模型设置三栏工作台 (In-Page Settings Workbench)
- **空间一致性**：彻底废除右侧滑出抽屉与遮罩，与新闻流等入口一致在主页面三栏呈现；
- **中栏 (`#feedColSettings`)**：5 大设置分类卡片（模型与 Agent、DeepSeek 管道、opencli 采集、SQLite 数据库、系统升级）；
- **右栏 (`#detailPanelSettings`)**：深度表单卡片工作台，配备 `恢复默认`、`测试连接` 与 `保存配置`，中栏点击卡片平滑滚动锚定对应表单。

### 7.6 研读想法展示区与社交化评论流 (Research Notes Stream & Agent Archive)
- **容器设计**：外框保留典雅的 **`1px dashed var(--border)` 发丝虚线边框**，建立主观研读边注（Marginalia）隐喻；
- **顶栏架构**：
  - 左侧：标题 `研读想法 (Research Notes)` + 数量徽标（如 `4条`，带弹性缩放动效）；
  - 右侧：单一显式操作按钮 **`+ 写想法`**；
- **折叠式原位输入器 (`#thoughtComposer`)**：
  - 默认状态：**完全隐藏（`display: none`）**，不占垂直阅读空间；
  - 唤出方式：点击 `+ 写想法` 按钮平滑展开，获得输入光标；
  - 底部操作：右侧 `发布想法`（支持 `⌘ + Enter` 瞬时发布），左侧配备高质感 **`取消`** 按钮（点击或按 `Esc` 即刻收起输入器）；
- **社交化多时期想法流 (`.thought-stream-item`)**：
  - **研究员个人想法**：头像（我）+ 署名与时间戳（如 `10-14 14:15`）+ 核心研读思考正文（及关键原文引用卡片）；
  - **Agent 提炼归档想法**：
    - 专属微晶头像（陶土红底色 `var(--accent-soft)` + 星芒矢量 `✦`）；
    - 署名：`新闻研究 Agent` + 胶囊徽标 `<span class="thought-agent-badge">✦ AI 研读归档</span>`；
    - 卡片左侧内嵌 `3px solid var(--accent)` 陶土红微晶发丝边；
    - 底部显示 `📌 归档出处：新闻研究 Agent 会话 (含 1 次 SRF 历史回购指标工具调用与推演测算)`；
- **悬浮显现删除按钮 (`.thought-item-delete-btn`)**：
  - **默认态**：完全透明且不响应交互（`opacity: 0; pointer-events: none;`）；
  - **卡片悬浮态**：鼠标移入想法卡片时平滑淡入（`opacity: 0.65; pointer-events: auto;`），位于时间戳右侧；
  - **按钮悬停态**：呈现高亮陶土红、发丝边框与立体微悬浮动效；
  - **点击删除**：卡片向上轻微位移淡出并平滑移除，顶栏计数徽标即时递减，右下角弹出轻量 Toast 提示。

---

## 8. 标准生产级代码产物 (Production-Ready Code Assets)

### 8.1 完整 CSS 变量表 (`tokens.css`)

```css
/**
 * News Reader 2.0 Production Design Tokens
 * Aligned with news-reader-workbench.html v2.3.7
 */

:root {
  /* ==========================================================================
     1. 物理布局尺寸 (Core Layout Dimensions)
     ========================================================================== */
  --header-height:   52px;
  --sidebar-width:   248px;
  --feed-width:      430px;

  /* ==========================================================================
     2. 字体栈 (Font Stacks)
     ========================================================================== */
  --font-display:    'Iowan Old Style', 'Charter', 'Source Serif Pro', Georgia, serif;
  --font-body:       -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --font-mono:       ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace;

  /* ==========================================================================
     3. 字号比例 (Type Scale)
     ========================================================================== */
  --fs-h1:           clamp(24px, 2.6vw, 34px);
  --fs-h2:           clamp(18px, 2.0vw, 24px);
  --fs-h3:           16px;
  --fs-body:         14px;
  --fs-small:        13px;
  --fs-meta:         12px;

  /* ==========================================================================
     4. 基础画板与语义色彩 (Light / Paper Edition)
     ========================================================================== */
  --bg:              oklch(98.5% 0.003 95);   /* 温暖低反光的专业纸张底色 */
  --surface:         oklch(100% 0 0);         /* 卡片与面板纯白底色 */
  --surface-subtle:  oklch(96.8% 0.005 95);   /* 次级悬浮/条带底色 */
  --surface-raised:  oklch(100% 0 0);         /* 浮层/模态框底色 */
  --fg:              oklch(20% 0.015 65);     /* 严谨沉稳的深石墨黑字体 */
  --muted:           oklch(48% 0.012 65);     /* 次级灰调，清晰且有足够对比度 */
  --border:          oklch(91% 0.006 90);     /* 极简发丝分割线 */
  --border-strong:   oklch(80% 0.012 90);     /* 聚焦、激活态边框 */
  --accent:          oklch(50% 0.14 32);      /* 报刊级陶土红 / 财经红点缀 */

  /* 派生工具色值 (color-mix) */
  --accent-soft:     color-mix(in oklch, var(--accent) 12%, transparent);
  --accent-hover:    oklch(45% 0.16 32);
  --fg-soft:         color-mix(in oklch, var(--fg) 6%, transparent);
  --tag-bg:          color-mix(in oklch, var(--fg) 5%, transparent);

  /* 功能状态语义色 */
  --status-success:  oklch(54% 0.14 145);     /* 墨绿色：已读、在线状态 */
  --status-warning:  oklch(66% 0.15 65);      /* 琥珀黄：稍后再看 */
  --status-danger:   oklch(52% 0.18 28);      /* 陶土红：重要、高危预警 */
  --status-info:     oklch(56% 0.14 250);     /* 科技蓝：抓取就绪、opencli */

  /* ==========================================================================
     5. 圆角体系 (Radii)
     ========================================================================== */
  --radius-sm:       6px;
  --radius:          8px;
  --radius-lg:       12px;

  /* ==========================================================================
     6. 动态阴影系统 (Dynamic Shadows)
     ========================================================================== */
  --shadow-subtle:   0 1px 3px color-mix(in oklch, var(--fg) 6%, transparent),
                     0 4px 12px color-mix(in oklch, var(--fg) 4%, transparent);
  --shadow-float:    0 12px 36px color-mix(in oklch, var(--fg) 12%, transparent);
}

/* ============================================================================
   深色模式适配 (Dark / Obsidian Slate Edition) - 基准色相角 250°
   ============================================================================ */
[data-theme="dark"] {
  --bg:              oklch(16% 0.01 250);
  --surface:         oklch(20% 0.012 250);
  --surface-subtle:  oklch(23% 0.012 250);
  --surface-raised:  oklch(25% 0.015 250);
  --fg:              oklch(93% 0.005 250);
  --muted:           oklch(66% 0.01 250);
  --border:          oklch(27% 0.012 250);
  --border-strong:   oklch(38% 0.015 250);
  --accent:          oklch(66% 0.16 35);
  --accent-soft:     color-mix(in oklch, var(--accent) 18%, transparent);
  --accent-hover:    oklch(72% 0.17 35);
  --fg-soft:         color-mix(in oklch, var(--fg) 10%, transparent);
  --tag-bg:          color-mix(in oklch, var(--fg) 8%, transparent);
  --shadow-subtle:   0 1px 3px rgba(0, 0, 0, 0.4), 0 4px 12px rgba(0, 0, 0, 0.3);
  --shadow-float:    0 12px 36px rgba(0, 0, 0, 0.6);
}

/* ============================================================================
   电子纸墨水屏模式 (E-Ink / Pure High Contrast)
   ============================================================================ */
[data-theme="eink"] {
  --bg:              #ffffff;
  --surface:         #ffffff;
  --surface-subtle:  #f4f4f4;
  --surface-raised:  #ffffff;
  --fg:              #000000;
  --muted:           #555555;
  --border:          #000000;
  --border-strong:   #000000;
  --accent:          #000000;
  --accent-soft:     #e5e5e5;
  --accent-hover:    #222222;
  --tag-bg:          #ebebeb;
  --shadow-subtle:   none;
  --shadow-float:    0 4px 0 #000000;
}
```

### 8.2 W3C DTCG 标准 JSON 规范 (`design-tokens.json`)

```json
{
  "layout": {
    "headerHeight": { "value": "52px", "type": "dimension" },
    "sidebarWidth": { "value": "248px", "type": "dimension" },
    "feedWidth": { "value": "430px", "type": "dimension" }
  },
  "typography": {
    "fontFamilies": {
      "display": {
        "value": "Iowan Old Style, Charter, Source Serif Pro, Georgia, serif",
        "type": "fontFamilies"
      },
      "body": {
        "value": "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif",
        "type": "fontFamilies"
      },
      "mono": {
        "value": "ui-monospace, JetBrains Mono, SF Mono, Menlo, monospace",
        "type": "fontFamilies"
      }
    },
    "fontSizes": {
      "h1": { "value": "clamp(24px, 2.6vw, 34px)", "type": "fontSizes" },
      "h2": { "value": "clamp(18px, 2.0vw, 24px)", "type": "fontSizes" },
      "h3": { "value": "16px", "type": "fontSizes" },
      "body": { "value": "14px", "type": "fontSizes" },
      "small": { "value": "13px", "type": "fontSizes" },
      "meta": { "value": "12px", "type": "fontSizes" }
    }
  },
  "color": {
    "light": {
      "bg": { "value": "oklch(98.5% 0.003 95)", "type": "color" },
      "surface": { "value": "oklch(100% 0 0)", "type": "color" },
      "surfaceSubtle": { "value": "oklch(96.8% 0.005 95)", "type": "color" },
      "surfaceRaised": { "value": "oklch(100% 0 0)", "type": "color" },
      "fg": { "value": "oklch(20% 0.015 65)", "type": "color" },
      "muted": { "value": "oklch(48% 0.012 65)", "type": "color" },
      "border": { "value": "oklch(91% 0.006 90)", "type": "color" },
      "borderStrong": { "value": "oklch(80% 0.012 90)", "type": "color" },
      "accent": { "value": "oklch(50% 0.14 32)", "type": "color" }
    },
    "dark": {
      "bg": { "value": "oklch(16% 0.01 250)", "type": "color" },
      "surface": { "value": "oklch(20% 0.012 250)", "type": "color" },
      "surfaceSubtle": { "value": "oklch(23% 0.012 250)", "type": "color" },
      "surfaceRaised": { "value": "oklch(25% 0.015 250)", "type": "color" },
      "fg": { "value": "oklch(93% 0.005 250)", "type": "color" },
      "muted": { "value": "oklch(66% 0.01 250)", "type": "color" },
      "border": { "value": "oklch(27% 0.012 250)", "type": "color" },
      "borderStrong": { "value": "oklch(38% 0.015 250)", "type": "color" },
      "accent": { "value": "oklch(66% 0.16 35)", "type": "color" }
    }
  },
  "radii": {
    "sm": { "value": "6px", "type": "borderRadius" },
    "md": { "value": "8px", "type": "borderRadius" },
    "lg": { "value": "12px", "type": "borderRadius" }
  }
}
```
