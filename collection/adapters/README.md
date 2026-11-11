# NewsReader 内置 adapter

本目录以 `xusz12/clis` v0.9.2 的所需源码为基线，来源提交为
`c28502779314773797d92306c9bebbc9fe03b6f0`。来源：
https://github.com/xusz12/clis/tree/c28502779314773797d92306c9bebbc9fe03b6f0

包含 Reuters 栏目与正文、Bloomberg Tech RSS、TechCrunch 列表与正文、
Ars Technica 列表与正文，以及共享 RSS、恢复工具和全部相关测试。
Reuters search 保留以运行上游完整测试，但不开放为 NewsReader 运行命令。
不包含其他未使用的 Bloomberg 命令、个人配置、Cookie、node_modules 或 .git。

`../opencli-manifest.json` 记录每个文件的 SHA-256、来源版本和必需命令。
当前组合为 `0.9.2+newsreader.1`：NewsReader 补丁排除 Reuters 正文容器中的
视频转录提示、讲者标签及转录文本；按 DOM 组件过滤，保留报道中对转录内容的正常引用。
补丁文件、原始哈希和原因单独记在 manifest 的 `patches` 中，不冒充上游原样快照。
上游未附独立 LICENSE；本快照保留来源说明，不另行指定其许可。
各子目录 README 是上游原始文档，其中个人安装路径仅作历史参考。
NewsReader 的运行与升级以 [集成说明](../../docs/opencli-integration.md) 为准。

安装：在 NewsReader 根目录执行 `npm ci --ignore-scripts`。
测试：`npm run test:adapters`。无需复制到 `~/.opencli`，无需注册用户级 plugin。
修改源码必须同步更新 manifest 中对应文件的哈希，并重新通过全部验收。
