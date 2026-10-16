# News Reader 2.0 设计规范与 Design Tokens 映射交付文档
# (Design System Specification & Tokens Reference)

> **版本**：v2.3.7\
> **设计语言**：Financial & Research Editorial (报刊级沉浸式深度研读与金融研究系统)\
> **适用平台**：Web / Desktop App (Electron/Tauri) / Tablet / Mobile Web\
> **参考原型**：`news-reader-workbench.html`\
> **标准规范组织参考**：W3C Design Tokens Community Group (DTCG), WCAG 2.1 AAA/AA, Apple HIG

---

## 目录 (Table of Contents)

1. [设计体系架构 (Design System Architecture)](#1-设计体系架构-design-system-architecture)
2. [色彩系统与感知对比度 (Color System & OKLch Palette)](#2-色彩系统与感知对比度-color-system--oklch-palette)
3. [字体排印与层次系统 (Typography & Type Hierarchy)](#3-字体排印与层次系统-typography--type-hierarchy)
4. [间距、网格与容器系统 (Spacing, Grid & Container System)](#4-间距网格与容器系统-spacing-grid--container-system)
5. [形状、边框与高程体系 (Shape, Border & Elevation)](#5-形状边框与高程体系-shape-border--elevation)
6. [交互工学与状态对偶变色契约 (Interaction States & Motion)](#6-交互工学与状态对偶变色契约-interaction-states--motion)
7. [组件级 Token 映射对照清单 (Component-Level Tokens Inventory)](#7-组件级-token-映射对照清单-component-level-tokens-inventory)
8. [标准生产级代码产物 (Production-Ready Code Assets)](#8-标准生产级代码产物-production-ready-code-assets)
   - 8.1 [完整 CSS 变量表 (`tokens.css`)](#81-完整-css-变量表-tokenscss)
   - 8.2 [W3C DTCG 标准 JSON 规范 (`design-tokens.json`)](#82-w3c-dtcg-标准-json-规范-design-tokensjson)

---

## 1. 设计体系架构 (Design System Architecture)

本系统采用现代设计系统黄金准则 **三层分层 Token 架构 (3-Tier Token Model)**，消除硬编码 hex/px 数值，确保设计与代码间建立单一事实来源 (Single Source of Truth)：

```
┌────────────────────────────────────────────────────────┐
│  Tier 1: Global Primitive Tokens (基础基元)            │
│  - 定义纯粹物理值：OKLch 色相基底、绝对像素阶梯、标准字族     │
│  - 例：--color-terracotta-500, --space-4, --font-serif │
└───────────────────────────┬────────────────────────────┘
                            │ 映射 (Referenced by)
┌───────────────────────────▼────────────────────────────┐
│  Tier 2: Semantic System Tokens (系统语义)             │
│  - 赋予业务与界面含义，驱动主题切换 (Light/Dark/E-Ink) │
│  - 例：--bg, --surface, --fg, --muted, --accent, --border│
└───────────────────────────┬────────────────────────────┘
                            │ 消费 (Consumed by)
┌───────────────────────────▼────────────────────────────┐
│  Tier 3: Component Scoped Tokens (组件级映射)          │
│  - 具体组件的特定属性绑定，保护全局 Token 隔离性       │
│  - 例：--card-bg, --nav-item-active-fg, --agent-dock-h │
└────────────────────────────────────────────────────────┘
```

### Token 命名规约 (Naming Convention)
遵循 Kebab-case，格式统一为：\
`--[scope]-[category]-[property]-[variant]-[state]`
- 范例：`--nav-item-bg-hover`、`--btn-primary-fg-active`、`--input-border-focus`

---

## 2. 色彩系统与感知对比度 (Color System & OKLch Palette)

本系统全量采用 **OKLch (Oklab Cylindrical)** 色彩空间：
- **感知均匀度 (Perceptual Uniformity)**：相同的明度数值 ($L$) 在所有色相下具备完全相同的视觉亮度，从数学上杜绝“浅黄色刺眼、深蓝色偏暗”的传统 HSL 弊端。
- **动态衍生计算**：主题微调通过 `color-mix(in oklch, ...)` 或改变 $L$ 通道数值实现，不依赖外部调色板生成器。

### 2.1 语义色彩对照矩阵 (Semantic Colors Matrix)

| 语义 Token | Light (浅色暖纸) | Dark (深色石墨) | E-Ink (墨水屏) | 用途说明 | WCAG 对比度 |
| :--- | :--- | :--- | :--- | :--- | :---: |
| `--bg` | `oklch(98.5% 0.003 95)` | `oklch(16% 0.01 60)` | `oklch(99% 0 0)` | 整个工作台视口底色，浅色呈现暖报纸感 | - |
| `--surface` | `oklch(100% 0 0)` | `oklch(20% 0.012 60)` | `oklch(100% 0 0)` | 中栏卡片、右栏工作台主背景 | 对 `--fg` ≥ 12:1 |
| `--surface-subtle` | `oklch(97% 0.005 95)` | `oklch(23% 0.012 60)` | `oklch(95% 0 0)` | 悬浮态、交替斑马纹、标签底色 | - |
| `--surface-raised` | `oklch(100% 0 0)` | `oklch(24% 0.015 60)` | `oklch(100% 0 0)` | 常驻浮窗 (Agent Dock)、弹窗底色 | - |
| `--fg` | `oklch(20% 0.015 65)` | `oklch(92% 0.005 60)` | `oklch(5% 0 0)` | 高可读性正文文字、主标题 | **13.5:1 (AAA)** |
| `--muted` | `oklch(48% 0.012 65)` | `oklch(65% 0.01 60)` | `oklch(40% 0 0)` | 次级元数据、时间戳、快捷键说明 | **4.9:1 (AA)** |
| `--border` | `oklch(91% 0.006 90)` | `oklch(28% 0.012 60)` | `oklch(80% 0 0)` | 1px 发丝分割线、卡片边框 | 结构线 |
| `--border-strong` | `oklch(80% 0.012 90)` | `oklch(38% 0.015 60)` | `oklch(20% 0 0)` | 输入框外框、高亮焦点外框 | ≥ 3:1 |
| `--accent` | `oklch(50% 0.14 32)` | `oklch(65% 0.16 35)` | `oklch(20% 0 0)` | 品牌陶土红 (Financial Red)，聚焦核心 | 5.2:1 (AA) |
| `--accent-soft` | `mix(accent, 12%)` | `mix(accent, 18%)` | `oklch(92% 0 0)` | 激活标签底色、徽标浅底 | - |
| `--accent-hover` | `oklch(45% 0.16 32)` | `oklch(70% 0.17 35)` | `oklch(0% 0 0)` | 交互悬浮加深/提亮态 | - |

### 2.2 状态与业务标记色彩 (Functional Colors)

| 功能语义 Token | OKLch 数值 | 业务落地场景 | 视觉表现与契约 |
| :--- | :--- | :--- | :--- |
| `--status-success` | `oklch(54% 0.14 145)` | 已读状态标记 (`M`)、服务健康自检 `0 错误` | 沉静墨绿，激活时仅作为点缀或微高光 |
| `--status-warning` | `oklch(66% 0.15 65)` | 稍后再看 (`S`)、2 到期复盘预警 | 典雅琥珀色，带有明确紧迫感提醒 |
| `--status-danger` | `oklch(52% 0.18 28)` | 重要新闻 (`I`)、关键突破催化剂 | 报刊陶土红，强力抓取第一视线 |
| `--status-favorite` | `oklch(68% 0.16 75)` | 长期高光收藏 (`F`) | 暖金黄，星标实心填充 |
| `--status-info` | `oklch(56% 0.14 250)` | opencli 抓取就绪、版本更新提示 | 沉稳钴蓝，信息性元数据 |

---

## 3. 字体排印与层次系统 (Typography & Type Hierarchy)

### 3.1 字体族分工规范 (Font Family Assignment)

| 字体分类 Token | 推荐字体栈 (Font Stack) | 视觉角色与排印规则 |
| :--- | :--- | :--- |
| `--font-display` | `'Iowan Old Style', 'Charter', 'Source Serif Pro', Georgia, serif` | **典雅衬线**。专用于新闻大标题、日报封面大字、核心论断，强化权威与智性阅读感。严禁用作密集正文。 |
| `--font-body` | `-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif` | **无衬线正文**。专用于阅读器文章段落、中栏卡片摘要、系统配置表单，保持极高清晰度与呼吸感。 |
| `--font-mono` | `ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace` | **等宽字体**。专用于快捷键徽标 (`M`, `I`, `S`, `F`, `A`, `,`)、抓取时间戳、代码与量化数据。 |

### 3.2 字号、行高与字距阶梯 (Modular Type Scale)

| Token 标号 | Font Size (rem / px) | Line Height | Letter Spacing | 适用典型场景 |
| :--- | :--- | :--- | :--- | :--- |
| `--text-2xs` | `0.6875rem (11px)` | `1.3` | `+0.04em` | 单键按键徽标 (`.kbd-badge`)、信源来源标签 |
| `--text-xs` | `0.75rem (12px)` | `1.4` | `+0.02em` | 次级时间戳、阅读用时、未读角标、表单帮助说明 |
| `--text-sm` | `0.8125rem (13px)` | `1.45` | `0` | 中栏卡片摘要、左栏导航标签、工具栏按钮文本 |
| `--text-base` | `0.875rem (14px)` | `1.5` | `-0.005em` | 默认界面基准文字、输入框输入文本、表格正文 |
| `--text-reading` | `0.9375rem (15px)` | **`1.7` (黄金行高)** | `+0.005em` | **右栏长文正文段落 (`.reader-body p`)** |
| `--text-lg` | `1.0625rem (17px)` | `1.4` | `-0.01em` | 卡片新闻标题、各业务中栏主标题、设置区分类标题 |
| `--text-xl` | `1.25rem (20px)` | `1.35` | `-0.015em` | 结构化精读导读主句、小节 H3、弹窗模块主标题 |
| `--text-2xl` | `1.5rem (24px)` | `1.25` | `-0.02em` | 新闻阅读器主标题 (H1)、全景研报主论断 |
| `--text-3xl` | `1.875rem (30px)` | `1.2` | `-0.025em` | 日报期次大封面刊头、重要数据看板数值 |

---

## 4. 间距、网格与容器系统 (Spacing, Grid & Container System)

系统建立在 **4px 原子基底** 与 **8px 节奏模数** 之上，杜绝奇数随机间距。

### 4.1 间距梯度阶梯 (Spacing Scale)

```css
--space-0-5: 2px;   /* 微型微调、按键徽标内边距 */
--space-1:   4px;   /* 紧凑内边距、图标与文字间隙 */
--space-2:   8px;   /* 默认小间距、按钮水平内边距、Chip 间距 */
--space-3:   12px;  /* 卡片内部元素上下间距、顶栏垂直对齐 */
--space-4:   16px;  /* 基础布局外边距、卡片标准 Padding */
--space-5:   20px;  /* 阅读器段落间距、设置分类卡片间隙 */
--space-6:   24px;  /* 模块分栏留白、工作台顶栏内边距 */
--space-8:   32px;  /* 大区块垂直分割 */
--space-12:  48px;  /* 顶栏固定高度、超大呼吸留白 */
```

### 4.2 三栏工作台空间物理规格与弹性比例 (3-Column Layout Spec)

```
┌─────────────────┬──────────────────────┬──────────────────────────────────────────┐
│ Left Navigation │ Feed Column (List)   │ Detail Panel / Workbench                 │
│ Width: 230px    │ Width: 420px         │ Width: flex: 1 (Min 560px, Reading Limit)│
│ (Fixed Column)  │ (Fixed Column)       │ (Elastic Viewport)                       │
└─────────────────┴──────────────────────┴──────────────────────────────────────────┘
```

1. **左侧导航栏 (`aside.left-nav`)**：
   - 宽度：`230px` 固定；
   - 包含：顶部品牌刊头、两大分类工作组（【阅读】5 项 + 【研究】5 项）、底栏常驻设置入口（高 `36px`，距底 `12px`）。
2. **中栏卡片流 (`section.feed-column`)**：
   - 宽度：`420px` 固定；
   - 包含：顶部检索过滤区（高 `48px` 固定置顶）、卡片独立纵向滚动容器。
3. **右侧工作台 (`section.detail-panel`)**：
   - 宽度：`flex: 1` 占据全部剩余视口；
   - 正文安全阅读行宽限制：`.article-reading-container` 设置 `max-width: 760px; margin: 0 auto;`，保证单行字符数维持在 65–75 字符（中文字符 35–45 字）的最高效阅读区间。

### 4.3 响应式断点与降级契约 (Responsive Breakpoints)

| 断点模式 | 视口范围 | 布局行为与降级交互标准 |
| :--- | :--- | :--- |
| **Desktop Ultra** | `≥ 1440px` | 三栏完全展开；阅读器正文两侧自动留白，居中黄金阅读区；Agent 浮窗舒适停靠。 |
| **Desktop Standard** | `1024px – 1439px` | 三栏基准形态；左栏 `230px` + 中栏 `380px~420px` + 右栏弹性拉伸。 |
| **Tablet Split** | `768px – 1023px` | 左栏收起为左滑浮动抽屉（保留汉堡按钮）；中栏与右栏呈两栏工作流并存。 |
| **Mobile Unified** | `< 768px` | **单栏原生流转 (Stack View)**：单视口仅展示一栏。中栏全屏展示列表，点击卡片后通过平滑动画前进至全屏详情，顶部带返回导航栏。 |

---

## 5. 形状、边框与高程体系 (Shape, Border & Elevation)

### 5.1 圆角系统 (Border Radius)

```css
--radius-xs: 3px;    /* 键位提示徽标、微型角标 */
--radius-sm: 6px;    /* 按钮 (Buttons)、输入框 (Inputs)、工具栏图标 */
--radius-md: 8px;    /* 文章列表卡片、分类 Chip、浮动工具条 */
--radius-lg: 12px;   /* 常驻 Agent Dock 浮窗、设置配置卡片大底板 */
--radius-xl: 16px;   /* 全景总结全屏模态框外框 */
--radius-full: 9999px; /* 状态胶囊 (Pills)、头像徽标 */
```

### 5.2 边框发丝规范 (Hairline Borders)
- **基准发丝线**：`border: 1px solid var(--border)`；在 Retina/高分屏下渲染为精致微晶发丝感，绝不使用 2px 粗边框。
- **活动高亮线**：`border-color: var(--accent)` 或 `var(--border-strong)`。
- **分割竖线**：工具组之间的垂线间隔使用 `width: 1px; height: 14px; background: var(--border);`。

### 5.3 高程与深度阴影模型 (Elevation & Z-Index)

| 高程层级 | 对应 CSS 变量 | 适用组件场景 |
| :--- | :--- | :--- |
| **Level 0 (Flat)** | `none` | 左栏底板、右栏正文背景 |
| **Level 1 (Card)** | `0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)` | 中栏卡片默认态、设置表单子卡片 |
| **Level 1.5 (Hover)**| `0 4px 12px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04)` | 卡片鼠标悬浮态、独立标读按钮悬浮态 |
| **Level 2 (Dock)** | `0 8px 24px -4px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.04)` | **常驻 Agent Dock 浮窗**、划词悬浮菜单 |
| **Level 3 (Modal)**| `0 20px 48px -12px rgba(0,0,0,0.22)` | 快捷键全屏帮助卡片、全景总结查看器 |

#### Z-Index 标定表
- `--z-base`: `1` (卡片交互元素)
- `--z-sticky`: `10` (顶栏、中栏置顶搜索条)
- `--z-dock`: `100` (常驻收折式 Agent Dock)
- `--z-drawer`: `500` (移动端侧滑抽屉)
- `--z-modal`: `1000` (全景总结查看器、快捷键帮助面板)
- `--z-toast`: `2000` (全局操作轻量吐司提示)

---

## 6. 交互工学与状态对偶变色契约 (Interaction States & Motion)

### 6.1 前景/背景对偶变色契约 (Contrast Guard Contract)
任何交互状态（Hover、Active、Focus、Selected）改变时，**前景色 (Text/Icon) 与背景色必须成对计算，杜绝单向变更导致的对比度下降**：
1. **幽灵态按钮悬浮 (`.btn-ghost:hover`)**：
   - 默认：`background: transparent; color: var(--fg);`
   - 悬浮：`background: var(--surface-subtle); color: var(--fg);`（严禁将文本变浅为 `--muted`）。
2. **标读激活状态 (`.btn-action.active`)**：
   - 重要 (`I`)：`background: var(--accent-soft); color: var(--accent); border-color: var(--accent);`
   - 稍后 (`S`)：`background: var(--fg); color: var(--surface); border-color: var(--fg);`
   - 收藏 (`F`)：`background: oklch(95% 0.05 75); color: oklch(50% 0.16 75); border-color: oklch(80% 0.1 75);`
3. **全局焦点环 (Focus-Visible)**：
   - 所有具备键盘焦点的控件在被 Tab/按键激活时，强制呈现：\
     `outline: 2px solid var(--accent); outline-offset: 2px;`

### 6.2 动效时长与贝塞尔曲线 (Motion Curves)

```css
/* 时长规范 */
--motion-fast:   120ms; /* 微型悬浮反馈、按键按下即时位移 */
--motion-normal: 200ms; /* 卡片边框呼吸、工具栏状态切换 */
--motion-slow:   320ms; /* Agent Dock 展开/收起、设置面板三栏无缝平移 */

/* 贝塞尔曲线 */
--ease-standard: cubic-bezier(0.2, 0.0, 0, 1.0);  /* 顶级桌面软件标配，出入平稳 */
--ease-out:      cubic-bezier(0.0, 0.0, 0.2, 1.0); /* 浮层展开、抽屉滑入 */
--ease-in:       cubic-bezier(0.4, 0.0, 1.0, 1.0); /* 快速收起 */
```

---

## 7. 组件级 Token 映射对照清单 (Component-Level Tokens Inventory)

### 7.1 左侧导航项 (`aside.left-nav .nav-item`)
| 属性 | 默认态 Token | 悬浮态 Token | 激活态 Token (`.active`) |
| :--- | :--- | :--- | :--- |
| **高度** | `32px` | `32px` | `32px` |
| **内边距** | `0 10px` | `0 10px` | `0 10px` |
| **背景** | `transparent` | `var(--surface-subtle)` | `var(--accent-soft)` |
| **文本色** | `var(--muted)` | `var(--fg)` | `var(--accent)` (加粗 Weight 600) |
| **图标色** | `var(--muted)` | `var(--fg)` | `var(--accent)` |
| **角标背景**| `var(--surface-subtle)` | `var(--border)` | `var(--accent)` (白字) |

### 7.2 新闻卡片 (`section.feed-column .feed-card`)
| 属性 | 默认态 Token | 悬浮态 Token | 键盘聚焦态 (`.is-active`) |
| :--- | :--- | :--- | :--- |
| **背景** | `var(--surface)` | `var(--surface)` | `var(--surface)` |
| **边框** | `1px solid var(--border)` | `1px solid var(--border-strong)` | `1px solid var(--accent)` |
| **阴影** | `var(--shadow-card)` | `var(--shadow-hover)` | `0 0 0 1px var(--accent)` |
| **底栏按钮**| `26px` 高，幽灵态 | 背景变 `--surface-subtle` | 独立激活态高亮 |

### 7.3 阅读器顶栏与标读工具组 (`.reader-action-toolbar button`)
- **高度**：`28px` 固定，圆角 `var(--radius-sm)` (6px)；
- **排版结构**：左侧图标 + 中间文本标签 + 右侧微型等宽按键提示 (`.kbd-badge`，背景 `--border`，字号 `10px`)；
- **解耦间隙**：按钮之间 `gap: 4px`；与左侧元数据及“查看原文”通过 `1px / 14px` 垂线隔开。

### 7.4 常驻收折式 Agent Dock (`section.agent-dock`)
- **停靠位置**：`position: absolute; right: 24px; bottom: 16px; width: 440px;`；
- **收起形态 (`.minimized`)**：高度 `42px`，单行排版，展示星芒图标、沙盒状态标签、`点击展开 · A` 提示与展开箭头；
- **展开形态**：高度 `520px`，阴影采用 `var(--shadow-dock)`，背景为 `var(--surface-raised)`，边框 `1px solid var(--border-strong)`。

### 7.5 系统设置三栏同页组件 (`#feedColSettings` + `#detailPanelSettings`)
- **中栏分类项**：采用标准卡片流规范，右侧带状态指示徽标（如 `已就绪`、`v2.3.7`）；
- **右栏表单卡片 (`.setting-card`)**：
  - 容器外框：`1px solid var(--border)`，圆角 `var(--radius-lg)` (12px)，背景 `var(--surface)`；
  - 表单输入框：高 `34px`，背景 `var(--surface)`，聚焦时 `border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft);`。

---

## 8. 标准生产级代码产物 (Production-Ready Code Assets)

### 8.1 完整 CSS 变量表 (`tokens.css`)

```css
/**
 * News Reader 2.0 Design Tokens
 * Generated: Production Release v2.3.7
 * Format: Standard CSS Custom Properties
 */

:root {
  /* ==========================================================================
     1. Primitive Font Stacks
     ========================================================================== */
  --font-display: 'Iowan Old Style', 'Charter', 'Source Serif Pro', Georgia, serif;
  --font-body:    -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --font-mono:    ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, monospace;

  /* ==========================================================================
     2. Modular Spacing (4px / 8px Rhythm)
     ========================================================================== */
  --space-0-5: 2px;
  --space-1:   4px;
  --space-2:   8px;
  --space-3:   12px;
  --space-4:   16px;
  --space-5:   20px;
  --space-6:   24px;
  --space-8:   32px;
  --space-12:  48px;

  /* ==========================================================================
     3. Radii & Hairline Borders
     ========================================================================== */
  --radius-xs:   3px;
  --radius-sm:   6px;
  --radius-md:   8px;
  --radius-lg:   12px;
  --radius-xl:   16px;
  --radius-full: 9999px;

  /* ==========================================================================
     4. Elevations & Shadows
     ========================================================================== */
  --shadow-card:  0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
  --shadow-hover: 0 4px 12px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.04);
  --shadow-dock:  0 8px 24px -4px rgba(0, 0, 0, 0.12), 0 2px 6px rgba(0, 0, 0, 0.04);
  --shadow-modal: 0 20px 48px -12px rgba(0, 0, 0, 0.22);

  /* ==========================================================================
     5. Motion & Transitions
     ========================================================================== */
  --motion-fast:   120ms;
  --motion-normal: 200ms;
  --motion-slow:   320ms;
  --ease-standard: cubic-bezier(0.2, 0.0, 0, 1.0);
  --ease-out:      cubic-bezier(0.0, 0.0, 0.2, 1.0);

  /* ==========================================================================
     6. Light Theme Colors (Default)
     ========================================================================== */
  --bg:              oklch(98.5% 0.003 95);  /* 温暖报纸米白底色 */
  --surface:         oklch(100% 0 0);        /* 纯白卡片主底色 */
  --surface-subtle:  oklch(97% 0.005 95);    /* 次级悬浮底色 */
  --surface-raised:  oklch(100% 0 0);        /* 常驻浮窗底色 */

  --fg:              oklch(20% 0.015 65);    /* 石墨黑高对比正文 */
  --muted:           oklch(48% 0.012 65);    /* 次级说明文本 */
  --border:          oklch(91% 0.006 90);    /* 1px 发丝边框 */
  --border-strong:   oklch(80% 0.012 90);    /* 聚焦高亮边框 */

  --accent:          oklch(50% 0.14 32);     /* 品牌报刊陶土红 */
  --accent-soft:     color-mix(in oklch, var(--accent) 12%, transparent);
  --accent-hover:    oklch(45% 0.16 32);

  --status-success:  oklch(54% 0.14 145);    /* 墨绿色 */
  --status-warning:  oklch(66% 0.15 65);     /* 琥珀色 */
  --status-danger:   oklch(52% 0.18 28);     /* 鲜明红 */
  --status-info:     oklch(56% 0.14 250);    /* 钴蓝色 */

  /* ==========================================================================
     7. Layout Metrics
     ========================================================================== */
  --topbar-height:      48px;
  --left-nav-width:     230px;
  --feed-column-width:  420px;
  --reading-max-width:  760px;
  --agent-dock-width:   440px;
  --agent-dock-height-collapsed: 42px;
  --agent-dock-height-expanded:  520px;
}

/* ==========================================================================
   Dark Theme Override
   ========================================================================== */
[data-theme="dark"] {
  --bg:              oklch(16% 0.01 60);
  --surface:         oklch(20% 0.012 60);
  --surface-subtle:  oklch(23% 0.012 60);
  --surface-raised:  oklch(24% 0.015 60);

  --fg:              oklch(92% 0.005 60);
  --muted:           oklch(65% 0.01 60);
  --border:          oklch(28% 0.012 60);
  --border-strong:   oklch(38% 0.015 60);

  --accent:          oklch(65% 0.16 35);
  --accent-soft:     color-mix(in oklch, var(--accent) 18%, transparent);
  --accent-hover:    oklch(70% 0.17 35);

  --shadow-card:  0 1px 3px rgba(0, 0, 0, 0.3);
  --shadow-hover: 0 4px 12px rgba(0, 0, 0, 0.4);
  --shadow-dock:  0 8px 24px -4px rgba(0, 0, 0, 0.6);
  --shadow-modal: 0 20px 48px -12px rgba(0, 0, 0, 0.8);
}

/* ==========================================================================
   E-Ink Theme Override (High-Contrast Monochrome)
   ========================================================================== */
[data-theme="eink"] {
  --bg:              oklch(99% 0 0);
  --surface:         oklch(100% 0 0);
  --surface-subtle:  oklch(94% 0 0);
  --surface-raised:  oklch(100% 0 0);

  --fg:              oklch(5% 0 0);
  --muted:           oklch(35% 0 0);
  --border:          oklch(75% 0 0);
  --border-strong:   oklch(20% 0 0);

  --accent:          oklch(10% 0 0);
  --accent-soft:     oklch(92% 0 0);
  --accent-hover:    oklch(0% 0 0);

  --shadow-card:  none;
  --shadow-hover: none;
  --shadow-dock:  0 0 0 1px oklch(20% 0 0);
  --shadow-modal: 0 0 0 2px oklch(10% 0 0);
}
```

---

### 8.2 W3C DTCG 标准 JSON 规范 (`design-tokens.json`)

本文件可直接导入 **Figma Tokens Studio**、**Style Dictionary** 或前端原子化 CSS 构建器：

```json
{
  "color": {
    "brand": {
      "terracotta": {
        "value": "oklch(50% 0.14 32)",
        "type": "color",
        "description": "品牌报刊陶土红，用于重要新闻标记与主高光"
      }
    },
    "semantic": {
      "bg": {
        "value": "{color.mode.bg}",
        "type": "color"
      },
      "surface": {
        "value": "{color.mode.surface}",
        "type": "color"
      },
      "fg": {
        "value": "{color.mode.fg}",
        "type": "color"
      },
      "muted": {
        "value": "{color.mode.muted}",
        "type": "color"
      },
      "border": {
        "value": "{color.mode.border}",
        "type": "color"
      },
      "accent": {
        "value": "{color.brand.terracotta}",
        "type": "color"
      }
    }
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
    "lineHeights": {
      "tight": { "value": "1.25", "type": "lineHeights" },
      "normal": { "value": "1.5", "type": "lineHeights" },
      "reading": { "value": "1.7", "type": "lineHeights" }
    }
  },
  "spacing": {
    "1": { "value": "4px", "type": "spacing" },
    "2": { "value": "8px", "type": "spacing" },
    "3": { "value": "12px", "type": "spacing" },
    "4": { "value": "16px", "type": "spacing" },
    "5": { "value": "20px", "type": "spacing" },
    "6": { "value": "24px", "type": "spacing" },
    "8": { "value": "32px", "type": "spacing" }
  },
  "borderRadius": {
    "sm": { "value": "6px", "type": "borderRadius" },
    "md": { "value": "8px", "type": "borderRadius" },
    "lg": { "value": "12px", "type": "borderRadius" },
    "full": { "value": "9999px", "type": "borderRadius" }
  }
}
```

---

## 9. 工程师与设计师实施校验核对清单 (QA Acceptance Checklist)

- [ ] **色彩对比度**：在 Light、Dark、E-Ink 三大主题下，正文与背景对比度通过 Chrome Lighthouse / Axe 审核，均达到 $\ge 12:1$（AAA 级）；辅助信息达到 $\ge 4.5:1$。
- [ ] **字体排印隔离**：新闻阅读器大标题呈现 Serif 衬线质感，正文行高锁定为 1.7；按键提示徽标与时间戳强制呈现 Monospace 等宽字体。
- [ ] **发丝线一致性**：所有卡片分割与列边界线严格采用 `1px solid var(--border)`，高分屏下无模糊虚边。
- [ ] **组件级映射闭环**：
  - 新闻卡片与阅读器顶栏的标读按钮组尺寸统一（高 28px/26px，圆角 6px）；
  - 常驻 Agent 浮窗默认收折高度为 `42px`，展开高度为 `520px`，动画时长平滑受控在 `320ms` 内；
  - 设置页面完全复用中栏（420px）与右栏工作台，无任何右侧遮罩抽屉弹出。
