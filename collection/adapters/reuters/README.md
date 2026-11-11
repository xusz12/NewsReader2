# 本机 Reuters 正文适配器

`article-detail.js` 是本机覆盖适配器。它显式设置 `browser: true`、
`navigateBefore: false`，直接打开传入的文章链接，不依赖框架预先打开首页。
正文恢复逻辑位于本目录；列表恢复策略见 [ReutersBrowser news](../reuters-section/README.md)。

## 等待与恢复

- 导航、提取和等待共用 **30 秒**总期限，低于 NewsReader 的 45 秒子进程超时。
- 临时导航错误最多调用 `page.goto` **2 次**，间隔 1 秒。
- 每秒检查页面状态；自动验证和正文加载期间不刷新页面、不请求列表接口。
- 临时浏览器或页面执行上下文错误最多容忍 **2 次重试**，第 3 次错误终止。
- 正文必须包含至少 200 字符的文章段落，标题或普通容器文本不算正文。
- 自动验证或加载超时返回现有的 `TIMEOUT` 错误，便于 NewsReader 有限重试。
- 交互验证码、登录订阅要求、明确封锁返回 `AUTH_REQUIRED`，缺失页面返回 `EMPTY_RESULT`。
- 不自动操作验证码或修改网站验证逻辑；所有读取使用现有浏览器会话。

浏览器检查在独立、短小的 `evaluate` 中完成，等待循环运行在 Node 中。
单个浏览器调用卡住时也会到期退出，不会再发起重叠重试；底层已经发送的调用无法由适配器取消。

可见的 DataDome iframe、顶层验证页、明确提示文字用于判断验证状态。
Reuters 的正常 `MainContent` 容器也有 `paywall-*` 类名，只有实际可见的访问提示才作为订阅墙。

## 正文采集（v0.9.2）

- 除普通 `paragraph-*` 和 `p` 节点外，采集明确的 `ArticleList` 正文块，不笼统采集文章内所有 `li`。
- 按文档顺序处理有序/无序列表、Body 包装、嵌套列表前后文字、内部段落和链接；子列表及内部 `p` 不重复进入正文，合法重复条目不会被全局去重误删。
- 排除列表内脚本、样式、嵌入导航和控件，继续使用现有推广过滤、署名保留和末尾边界。
- 列表块之外的章节小标题目前未保留；200 字符就绪门槛仅保证存在可用正文，不保证所有未知排版完整。

## 正文清洗（2026-10-30）

`utils.js` 的 `cleanReutersParagraph` 随正文提取脚本在浏览器中执行：

- 识别完整的 newsletter 推广句式及 `Sign up here`／`Sign up for … newsletter here`，覆盖 Daily Briefing、Trading Day、Daily Docket、Misinformation Monitor、Morning Bid、Breakingviews 等已观察到的文案。
- 逐段处理全文；删除正文开头、中间或末尾的推广后继续提取。同一段中的推广句只删除对应句子，保留前后报道。
- 订阅链接单独成为下一段时，也能与前面的推广句配对；同时处理不间断空格、零宽字符、换行、链接辅助文字和尾随反斜线。
- 不凭 `newsletter`、`Advertisement`、`Recommended` 等单个关键词删除报道；保留正常事实描述及带引号／归因的推广文案引用。没有订阅提示的模糊文字保留。
- 保持既有末尾编辑信息和访问限制判断；纯推广内容不能满足正文就绪条件。

这是本机 `~/.opencli/clis/reuters/` 下的覆盖适配器，新的 opencli 调用直接读取修改后的代码。
清洗发生在返回 JSON 之前；NewsReader 已缓存的正文和中文翻译不会自动重写，需先重新抓取正文，再重新翻译，才会反映修复。

## 测试

在本机安装了 Vitest 的环境中执行：

```sh
npx --yes vitest@5.0.4 run --root "$HOME/.opencli/clis" reuters/reuters.test.js reuters/article-recovery.test.js reuters/article-cleaning.test.js --cache=false
```

测试覆盖状态识别、真实正文容器误判、有限重试、跳转导致上下文失效、卡住的浏览器调用、共享总期限及返回格式；清洗回归使用实际观察到的推广文案和合成报道，检查全文不同位置、同段混排、分段链接、重复插入、正常报道／引用保留及纯推广页面。

v0.9.2 另覆盖真实 ArticleList 结构、有序/无序和嵌套列表、内部段落重复防护、普通段落正向对照，以及 Daily Briefing 精确清洗边界。全仓本轮启用 NewsReader2 源码只读 AST 联动后 363 项全部通过；未设置 `NEWSREADER_SOURCE` 时为 361 通过、2 跳过。
