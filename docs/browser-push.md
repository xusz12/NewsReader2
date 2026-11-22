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

从 v0.6.10 在「版本与更新」升级时，更新器会随项目安装 `web-push`。原 Python 环境缺少 `pywebpush` 时，NR 自动使用这份依赖，并沿用已有通知密钥和订阅。手动拉取代码的安装可在项目目录执行 `npm ci --ignore-scripts` 后重启。仅使用 Python 的安装可在运行 NR 的解释器中执行 `python -m pip install -r requirements.txt` 后重启。

在运行 NR 的 Mac 上登录独立 Tailscale 客户端，连接手机使用的同一尾网。管理员需在尾网的 DNS 设置中开启 MagicDNS 和 HTTPS Certificates。首次签发证书会将设备的完整域名写入公共证书透明度日志。设备的访问范围仍由尾网控制。[Tailscale HTTPS 证书设置](https://tailscale.com/docs/how-to/set-up-https-certificates)

双击 `启动NewsReader.command`。默认启动先复用转发到本实例回环端口的私有 HTTPS 根地址。需要新转发时，NR 按顺序检查 `443`、`8443`、`9443`，在空闲端口启动随本次运行结束的 Tailscale Serve 会话。打开「设置 → 阅读偏好与维护 → 访问地址」，复制标为「私有 HTTPS」的完整网址。可在 Mac 上查看转发状态。[Tailscale Serve 命令](https://tailscale.com/docs/reference/tailscale-cli/serve)

```sh
tailscale serve status
```

如需自行管理长期转发，先退出 NR 并查看已有配置。如果 `443` 已转发到开发版，且 `8443` 空闲，用下面的命令给正式版添加独立入口。手机使用查询结果里的完整 HTTPS 网址，包括 `:8443`。

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

单独使用 Surge 尾网入站时，设置中会标出 HTTP 阅读地址。需要手机通知时，可使用独立 Tailscale 客户端的私有 Serve 入口。连接尾网或更改转发后，重新启动 NR 以加载通知地址。显式指定的通知环境变量优先。

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

在运行 NR 的 Mac 上，打开本实例的回环网址，默认是 `http://127.0.0.1:8080/`。安装了推送依赖即可在此启用本机通知，Tailscale 离线时也可订阅。手动配置时，`NEWS_READER_PUSH_LOOPBACK_PORT` 取本实例的回环监听端口。回环入口要求直连 `127.0.0.1`，并精确匹配配置端口、Host 和订阅请求的 Origin。订阅接口还校验推送服务地址和加密公钥。

## 存储与投递

推送密钥、各浏览器的订阅、设备回执和投递状态保存在 NR 新闻库中，按库路径及源地址隔离。已有私有 HTTPS 时，回环入口可沿用同一套密钥和投递队列。先在仅本机模式订阅的浏览器按回环端口保存密钥和偏好，之后开启私有 HTTPS 仍可接收通知。浏览器在两个地址上分别订阅。复制到另一库路径或更换 HTTPS 源时，在新入口重新订阅。成功事件随业务事务保存，应用重启后继续处理有效队列。订阅及类型开关生效之前的事件不会补推。投递至多保留一天，浏览器推送服务判定订阅已过期时移除它。

关闭 NR 标签页后，应用后台和浏览器的推送服务仍可转交通知。点开内容时，设备需要能访问该浏览器订阅时使用的地址。服务端需要能访问 Chrome、Firefox、Safari 或 Edge 使用的对应 HTTPS 推送服务。
