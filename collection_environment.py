"""On-demand, read-only collection diagnostics; never return CLI credentials."""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import subprocess
import threading

from collection_sources import run_command


_lock = threading.Lock()
CHECK_IDS = {"runtime", "adapters", "chrome", "bridge", "x"}


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS collection_environment_checks (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT NOT NULL,
        checked_at TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0)""")


def latest(conn):
    return [{**dict(row), "stale": bool(row["stale"])} for row in conn.execute(
        "SELECT id,status,message,checked_at,stale FROM collection_environment_checks ORDER BY id")]


def remember(conn, target, result):
    """Keep the latest safe diagnostic per check, independent of browser storage."""
    at = result["checked_at"]
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if target == "environment":
            conn.execute("UPDATE collection_environment_checks SET stale=1 WHERE id='x' AND checked_at<=?", (at,))
        for row in result["checks"]:
            if row["id"] not in CHECK_IDS or row["status"] not in {"success", "failed", "unknown"}:
                continue
            stale = row["id"] == "x" and bool(conn.execute("""SELECT 1 FROM collection_environment_checks
                WHERE id IN ('chrome','bridge') AND checked_at>? LIMIT 1""", (at,)).fetchone())
            conn.execute("""INSERT INTO collection_environment_checks(id,status,message,checked_at,stale)
                VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                message=excluded.message,checked_at=excluded.checked_at,stale=excluded.stale
                WHERE excluded.checked_at>=collection_environment_checks.checked_at""",
                (row["id"], row["status"], row["message"], at, int(stale)))
        return latest(conn)


class Busy(Exception):
    pass


class ProbeError(Exception):
    pass


def _run(command, timeout):
    try:
        return run_command(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ProbeError("TIMEOUT") from None
    except OSError:
        raise ProbeError("EXECUTION_FAILED") from None


def _json(command, timeout):
    result = _run(command, timeout)
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ProbeError("INVALID_RESPONSE") from None
    if isinstance(data, dict) and isinstance(data.get("ok"), bool):
        if not data["ok"]:
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            # No raw messages, account identities, cookies, or local paths leave
            # this boundary. Unknown upstream failures stay unknown.
            raise ProbeError(code if code in {"AUTH_REQUIRED", "TIMEOUT", "BROWSER_CONNECT"} else "CHECK_FAILED")
        data = data.get("data")
    if result.returncode:
        raise ProbeError("CHECK_FAILED")
    return data


def _item(key, status, message):
    return {"id": key, "status": status, "message": message}


def _runtime(binary):
    result = _run([binary, "--version"], 5)
    version = (result.stdout or "").strip()
    if result.returncode or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ProbeError("INVALID_RESPONSE")
    return _item("runtime", "success", f"opencli {version}；使用 NewsReader 锁定的项目内版本。")


def _commands(binary):
    report = _json([binary, "--nr-check"], 10)
    manifest = json.loads((Path(__file__).parent / "collection/opencli-manifest.json").read_text())
    required = {f"{row['site']} {row['name']}" for row in manifest["commands"]}
    if (not isinstance(report, dict) or report.get("isolated") is not True
            or report.get("version") != manifest["version"]
            or report.get("adapter_commit") != manifest["adapters"]["commit"]
            or not isinstance(report.get("commands"), list)
            or not all(isinstance(name, str) for name in report["commands"])
            or not required.issubset(report["commands"])):
        raise ProbeError("REQUIRED_COMMAND_MISSING")
    return _item("adapters", "success", f"{len(required)} 项必需命令与内置 adapter 校验通过；不加载个人 adapter 或插件。")


def _browser(binary):
    report = _json([binary, "doctor"], 20)
    if (not isinstance(report, dict) or not isinstance(report.get("extensionConnected"), bool)
            or not isinstance(report.get("connectivity"), dict)
            or not isinstance(report["connectivity"].get("ok"), bool)):
        raise ProbeError("INVALID_RESPONSE")
    connected = report["connectivity"]["ok"] and report["extensionConnected"]
    chrome = _item("chrome", "success" if connected else "unknown",
                   "已通过浏览器连接检查；采集期间请保持 Chrome 运行。" if connected else
                   "尚未确认连接。请在运行 NewsReader 的电脑打开 Chrome，并使用安装扩展的浏览器配置。")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    incompatible = any(isinstance(issue, str) and "CLI version incompatible with extension" in issue for issue in issues)
    if incompatible:
        bridge = _item("bridge", "failed", "OpenCLI 命令与浏览器扩展版本不兼容，请安装相互兼容的版本。")
    elif report.get("daemonStale") or report.get("daemonFlaky") or report.get("extensionFlaky"):
        bridge = _item("bridge", "unknown", "桥接服务版本不一致或连接不稳定，请重启 OpenCLI 桥接服务后重新检查。")
    elif connected:
        version = report.get("extensionVersion")
        version = version if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version) else None
        bridge = _item("bridge", "success" if version else "unknown",
                       f"OpenCLI 浏览器扩展 {version} 已连接。" if version else
                       "扩展已连接，但未报告版本；请确认使用兼容版本。")
    else:
        bridge = _item("bridge", "failed", "OpenCLI 浏览器扩展未连接；可能未安装、未启用，或 Chrome 尚未打开。")
    return [chrome, bridge]


def _x_account(binary):
    identity = _json([binary, "twitter", "whoami", "-f", "json", "--window", "background"], 45)
    if isinstance(identity, list) and len(identity) == 1:
        identity = identity[0]
    if isinstance(identity, dict) and identity.get("logged_in") is False:
        raise ProbeError("AUTH_REQUIRED")
    if (not isinstance(identity, dict) or not isinstance(identity.get("username"), str)
            or identity.get("logged_in", True) is not True or identity.get("site", "twitter") != "twitter"
            or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", identity["username"])):
        raise ProbeError("INVALID_RESPONSE")
    return _item("x", "success", "已验证当前浏览器中的 X 登录会话；此检查不代表全部账号的采集测试已通过。")


def check(target="environment"):
    if target not in {"environment", "x"}:
        raise ValueError("invalid_environment_check")
    if not _lock.acquire(blocking=False):
        raise Busy()
    try:
        binary = "opencli"  # run_command resolves the project entry, never PATH.
        checks = []
        try:
            checks.append(_runtime(binary))
        except ProbeError:
            checks.append(_item("runtime", "failed", "项目内 opencli 未就绪；请确认 Node.js 24+，并在项目目录执行 npm ci --ignore-scripts。"))
        if checks[0]["status"] != "success":
            for key in (["adapters", "chrome", "bridge"] if target == "environment" else ["adapters", "x"]):
                checks.append(_item(key, "unknown", "opencli 尚未就绪，无法执行检查。"))
        else:
            try:
                checks.append(_commands(binary))
            except (ProbeError, OSError, ValueError, KeyError):
                checks.append(_item("adapters", "failed", "必需命令或内置 adapter 不完整；请恢复当前版本的项目文件并执行 npm ci --ignore-scripts。"))
            try:
                checks.extend(_browser(binary) if target == "environment" else [_x_account(binary)])
            except ProbeError as exc:
                if target == "x":
                    message = "X 尚未登录或会话已失效，请在同一 Chrome 配置中登录 X 后重新检查。" if str(exc) == "AUTH_REQUIRED" else (
                        "X 登录检查超时；当前登录状态未确认，请稍后重试。" if str(exc) == "TIMEOUT" else
                        "无法确认 X 登录状态，请检查浏览器连接、网络及 opencli 的 twitter whoami 命令。")
                    checks.append(_item("x", "failed" if str(exc) == "AUTH_REQUIRED" else "unknown", message))
                else:
                    checks.extend(_item(key, "unknown", "浏览器检查未完成；请确认 Chrome 与 OpenCLI 扩展可用后重试。")
                                  for key in ("chrome", "bridge"))
        return {"checks": checks, "checked_at": datetime.now(timezone.utc).isoformat()}
    finally:
        _lock.release()
