# Newsreader 品牌资源接入

接入日期：2026-10-16。采用设计交付的方案 04「留白书签」。

## 页面入口

| 入口 | 资源与处理 |
| --- | --- |
| 顶栏图形 | `mark-small.svg` 的原始路径，24px，保留小尺寸加宽负形 |
| 顶栏字标 | 从 `lockup.svg` 的 `brand-wordmark` 分组提取原始轮廓，124 × 19px，不依赖字体 |
| 浅色主题 | 图形 `#c96442`，字标 `#141413` |
| 深色／跟随系统深色 | 图形和字标均使用反白 `#faf9f5` |
| 墨水屏主题 | 图形和字标均为纯黑 |
| 浏览器标签页 | `favicon.svg`，并提供 16／32／48px 多尺寸 `favicon.ico` |
| 添加到主屏幕 | Web App Manifest 引用 192／512px 方形应用 PNG；Apple Touch Icon 为 180px PNG |

顶栏图形与字标之间保留 8px 布局间距，保持等比缩放和透明负空间。屏幕阅读器获得统一的 `newsreader` 名称，装饰性 SVG 不重复朗读。版本号、页面标题和现有功能名称继续使用原来的版本逻辑。

原始资源在 `static/brand/`；标准图形、单色、反白、完整组合字标、1024px 应用图标也一并保留。`docs/brand/geometry.svg` 与 `docs/brand/verification.md` 是设计师交付的原始参考文件，后者记录的是交付时的验证状态，其中相对路径指向原设计项目，不代表本仓库测试结果。

## 维护方式

顶栏通过 `header-symbols.svg` 复用路径，颜色由页面 CSS 控制。替换源文件后执行：

```bash
python3 scripts/build_brand_sprite.py
```

生成器只提取图形路径和字标分组中的轮廓，不重绘图形。字标的显示边界在生成后的消费端 `static/index.html` 中定义；如果未来字标几何发生变化，需要同步调整该边界。

Apple Touch Icon 从设计师的 1024px 不透明底板等比缩小，没有增加文件圆角：

```bash
sips -z 180 180 static/brand/app-icon-1024.png --out static/brand/apple-touch-icon.png
```

Manifest 提供应用名称与主屏幕图标元数据；本次没有加入离线缓存或原生应用打包。

## 本仓库验证

- 现有主题、响应式、版本和键盘交互浏览器检查：16 项通过，覆盖 360–1920px。
- 品牌专项真实 Chromium 检查：375、768、1024、1440px × 浅色／深色／墨水屏，共 12 组通过；另检查跟随系统深色。
- 检查 SVG 实际渲染边界、图形颜色、顶栏溢出及控件重叠；已目视复查手机浅色、桌面深色、墨水屏截图。
- SVG／ICO／Apple Touch Icon／Manifest 均返回 HTTP 200，Manifest 引用的 PNG 可访问；无页面 JavaScript 错误。
- 设计 Token 和版本元数据 Python 检查：4 项通过。

截图和专项结果在忽略提交的 `.ui-artifacts/brand-*` 中。浏览器验证使用隔离新闻库的真实应用页面；未实测 iOS／Android 添加到主屏幕或原生安装效果。

## 素材完整性

当前网页端无需设计师补充素材。用户最初列出的六个文件之外，同一设计目录已包含标准标志、小尺寸版、favicon 和应用 PNG，均已一并接入或归档。180px Apple Touch Icon 已由现有源图导出。

设计交付不包含平台专用原生安装图标包。未来若增加 macOS／Windows 原生客户端，再按目标平台制作 `.icns` 或 Windows 安装图标；当前 `.command` 启动器仍用于打开网页，不属于原生应用包。
