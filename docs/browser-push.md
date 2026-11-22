# 浏览器通知

在运行 NR 的 Mac 上，双击 Finder 启动器，使用自动打开的本机回环网址。其他设备连上 Tailscale 后，使用启动终端标为「推荐」的私有 HTTPS 网址。进入「设置 → 阅读偏好与维护 → 浏览器通知」，点击「启用本浏览器通知」并允许权限，再选择要接收的类型。开关即时保存，只作用于当前浏览器在该地址的订阅。可用「取消本浏览器订阅」关闭它。[浏览器安全上下文](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy)

手机使用私有 HTTPS。iPhone 和 iPad 需使用 iOS 或 iPadOS 16.4 及以上，先添加到主屏幕，再从应用图标打开并启用。[WebKit 主屏幕 Web Push 说明](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)

| 事件 | 投递与落点 |
| --- | --- |
| 日报完成 | 早、午、晚报每个北京时间期次一次。打开该期日报。 |
| 采集失败待处理 | 同一批次在入库阶段终结时合并一次，包括失败来源、耗尽重试的翻译或入库失败。打开该批次的错误与提示。 |
| 个人助手回复 | 完整回复保存后一次。打开对应讨论并定位回复。 |
| 文章问答回复 | 完整回复保存后一次。打开对应文章和问答轮次。 |

合并和去重以已保存的业务结果为准。浏览器页面实际显示完整回复时，会按设备报告该回复已可见。服务器预留短时间接收这个确认，再决定是否向该浏览器投递。别的订阅设备仍可收到。锁屏提示只含事件类型和打开提示，正文与问答内容在应用内读取。[WebKit 可见通知要求](https://webkit.org/blog/12945/meet-web-push/)

## 配置私有 HTTPS

旧安装升级后首次启用通知时，在项目目录激活运行 NR 的 Python 环境，执行以下命令以安装 `pywebpush`。

```sh
python -m pip install -r requirements.txt
```

在运行 NR 的 Mac 上先查看现有转发。已指向正式版回环端口 `8080` 的私有 HTTPS 根地址可以直接沿用。[Tailscale Serve 命令](https://tailscale.com/docs/reference/tailscale-cli/serve)

```sh
tailscale serve status
```

如果 `443` 已转发到开发版，且 `8443` 空闲，用下面的命令给正式版添加独立入口。手机使用查询结果里的完整 HTTPS 网址，包括 `:8443`。

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8080
tailscale serve status
```

如果 `443` 空闲，也可用它作为正式版的 HTTPS 端口。

```sh
tailscale serve --bg --https=443 http://127.0.0.1:8080
tailscale serve status
```

如终端仅显示尾网 HTTP 地址，可以用它阅读。启用通知前先核对 HTTPS 根地址的转发端口。

双击 `启动NewsReader.command` 后，启动器检测运行中的私有 Serve 根地址。匹配本实例的回环端口且 Python 环境已安装 `pywebpush` 时，启动器同时配置私有 HTTPS 和本机订阅入口。显式指定的通知环境变量优先。

自定义私有代理或使用其他启动方式时，通过环境变量指定源地址并重启服务。以下示例使用 macOS 安装步骤创建的 `.venv`。运行前把示例域名替换为自己的 HTTPS 地址。

```sh
NEWS_READER_PYTHON="$PWD/.venv/bin/python" \
NEWS_READER_HOST=127.0.0.1 \
NEWS_READER_PORT=8080 \
NEWS_READER_PUSH_ORIGIN=https://your-device.your-tailnet.ts.net \
NEWS_READER_PUSH_LOOPBACK_PORT=8080 \
./启动NewsReader.command
```

`NEWS_READER_PUSH_ORIGIN` 包含 HTTPS、主机名和所用的非默认端口。私有 HTTPS 代理通过回环地址转发请求，保留原始 `Host`，并传递 `X-Forwarded-Host` 和 `X-Forwarded-Proto: https`。

## 配置 Mac 本机入口

配置私有 HTTPS 后，在 Mac 上用本实例的回环网址启用一次本机订阅，默认是 `http://127.0.0.1:8080/`。手动配置时，`NEWS_READER_PUSH_LOOPBACK_PORT` 取同一实例的回环监听端口。回环入口要求直连 `127.0.0.1`，并精确匹配配置端口、Host 和订阅请求的 Origin。普通内网 HTTP 无法启用订阅。订阅接口还校验推送服务地址和加密公钥。

## 存储与投递

推送密钥、各浏览器的订阅、设备回执和投递状态保存在 NR 新闻库中，按库路径及完整私有 HTTPS 源隔离。回环入口沿用同一套密钥和投递队列，保留已有 HTTPS 订阅及类型偏好。浏览器在两个地址上分别订阅。签名的联系地址使用 HTTPS 主机名。复制到另一库路径或更换 HTTPS 源时，在新入口重新订阅。成功事件随业务事务保存，应用重启后继续处理有效队列。订阅及类型开关生效之前的事件不会补推。投递至多保留一天，浏览器推送服务判定订阅已过期时移除它。

关闭 NR 标签页后，应用后台和浏览器的推送服务仍可转交通知。点开内容时，设备需要能访问该浏览器订阅时使用的地址。服务端需要能访问 Chrome、Firefox、Safari 或 Edge 使用的对应 HTTPS 推送服务。
