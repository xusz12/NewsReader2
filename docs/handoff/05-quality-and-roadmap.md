# 测试结果与后续工作

交接材料与源码包已按 v0.0.15 基线整理。当前可以进行开发接手及本地演练，但本次默认 Python 全量回归没有全部通过；下列失败及诊断应作为接手事项保留，不能将条件复测结果写成默认全量通过。

## 2026 年 10 月 21 日验证记录

运行环境：macOS 27.0.1 arm64、Python 3.13.1、SQLite 3.45.3、Node.js 26.3.1、npm 11.17.0。已安装 Flask 3.1.0、pytest 8.3.3、openai 1.82.0、certifi 2025.11.12。本次复用已有依赖环境，没有执行全新机器安装。

| 验证项 | 实际结果 | 解释 |
| --- | --- | --- |
| `PYTHONPATH=. python3 -m pytest -q` | **727 通过，24 失败**，约 130 秒 | 23 项总结测试，1 项 localhost 启动健康检查 |
| 四个 Node 测试文件 | **56 通过，0 失败** | 错误显示、状态、稍后再看及 OAR 桥接 |
| `CI=1 npm run test:ui` | **208 通过，0 失败**，约 2.9 分钟 | Chromium、临时 SQLite 和模型替身 |
| 总结模块固定日期诊断 | **103 通过，0 失败** | 仅诊断进程中将选择模块的 today 固定到 2026-10-20 |
| localhost 绕过代理复测 | **1 通过，0 失败** | 设置 NO_PROXY / no_proxy 后复测原失败启动器用例 |
| 合成样例真实进程演练 | **通过** | app.py 启动、健康接口、sidecar 导入、重复索引、状态和想法保存、Markdown 回退、SQLite 备份及重启 |

简明记录见 [验证结果](verification/results.txt)，Python 默认全量输出见 [失败明细](verification/python-baseline.txt)，其余诊断及样例演练见 [诊断记录](verification/diagnostics.txt)。路径占位已替换为通用名称；真实新闻、凭据和模型输出未纳入验证资料。

浏览器测试的模型、文件选择、模型目录和升级采用替身或隔离响应；真实接手环境的 opencli、Pi/Codex、DeepSeek、上游采集、Tailscale、仓库权限及生产升级需要单独验收。本次没有迁移或修改真实业务数据库，也没有执行真实模型任务。

## 已发现的问题

### Q1 总结测试依赖实际日历日期

23 个失败集中在 `tests/test_news_summaries.py`。该模块 fixture 固定使用 2026-10-01 和 2026-10-02 的新闻，返回正文固定引用 `N0001` 和 `N0002`；默认选择窗口是含当日在内 20 天。2026-10-21 的窗口从 10 月 2 日开始，第一条材料被排除，但测试模型仍返回它的引用，触发结果验证失败。

诊断时仅固定 `news_summary_selection.date.today()` 到 2026-10-20，该模块全部 103 项通过；未修改应用或测试源码。这说明 fixture 的日期稳定性需要修复，不能由此保证其他日期均无问题，也不应放宽生产引用校验来让测试通过。

建议优先修复测试时钟：注入固定 today，或让通用 fixture 日期相对固定时钟构造；独立覆盖窗口边界、旧材料回退窗口及排除材料引用。随后在实际当日执行完整 Python 回归。

复现默认失败：

```bash
PYTHONPATH=. python3 -m pytest -q tests/test_news_summaries.py
```

复现本次条件诊断（仅影响此 Python 进程）：

```bash
PYTHONPATH=. python3 - <<'PY'
from datetime import date
import pytest
import news_summary_selection as selection
class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 20)
selection.date = FixedDate
raise SystemExit(pytest.main(['-q', 'tests/test_news_summaries.py']))
PY
```

### Q2 本机代理环境影响 localhost 健康检查

`tests/test_launcher.py::test_supervisor_update_handoff_and_exit_cleanup[update-success-custom-loopback]` 默认运行失败于启动健康检查。本机 Python 检测到 HTTP/HTTPS 代理配置；使用以下代理绕过配置复测后通过，证据指向 localhost 请求的代理路径。当前只做了这一项条件复测，未在所有网络配置下验证。

```bash
NO_PROXY=localhost,127.0.0.1,::1 \
no_proxy=localhost,127.0.0.1,::1 \
PYTHONPATH=. python3 -m pytest -q \
  'tests/test_launcher.py::test_supervisor_update_handoff_and_exit_cleanup[update-success-custom-loopback]'
```

接手团队应明确本地健康请求的代理策略，并补充对应环境测试。应用的公共模型和新闻网络请求仍可能需要代理，不能简单移除所有网络配置。

## 技术债与建议顺序

以下为基于当前实现的接手建议，不代表已承诺排期或已有未完成产品需求。

| 顺序 | 工作 | 原因及完成标准 |
| --- | --- | --- |
| 首次验收前 | 修复 Q1 测试时钟，明确 Q2 代理策略 | 在接手环境取得可复现的默认测试结果并记录 |
| 首次验收前 | 补齐上游采集与 opencli 版本、适配器及调度资产 | 仓库没有采集器，也未固定 opencli 版本；能从采集到全文完成真实闭环 |
| 首次验收前 | 完成数据副本恢复和账号权限移交 | 源码可运行不等于已有真实数据、订阅和发布权限 |
| 首次验收前 | 确认代码、品牌及第三方资源的交付授权 | 仓库根目录未提供项目 LICENSE；保留现有 vendor 许可证和品牌说明，由交付方确认授权范围 |
| 首个迭代 | 建立 CI 与干净环境安装验证 | 当前 Git 文件未包含 CI 工作流；Python 依赖也没有完整解析锁文件 |
| 首个迭代 | 给数据库迁移建立版本与备份流程 | DDL 分散于多个模块，自动回退源码无法降级数据库 |
| 首个迭代 | 按领域拆分 app.py，并统一 API 文档 | 约 1.2 万行的入口同时承担路由、SQL、任务与兼容逻辑；先以现有测试守住行为 |
| 按部署目标决定 | 多用户、公网和多进程支持 | 先补认证、权限、任务协调和外部服务边界，再选择部署拓扑；当前不能直接横向扩容 |
| 按平台目标决定 | Linux、Windows 和后台服务安装 | Keychain、osascript、fcntl、Unix socket、进程组等能力需要适配与验收 |
| 按规模决定 | 结构化日志、耗时指标、容量与压力测试 | 当前验证集中在功能，不包含长时间运行、并发容量和性能服务承诺 |

## 本次保持的范围

交接工作新增文档、样例、验证记录和根 README 入口。应用源码、版本、依赖锁文件和原有测试均未为本次交接改写；尤其没有为了取得绿色测试结果修改总结范围或引用校验。Q1、Q2 已作为具备复现证据的接手事项交付。
