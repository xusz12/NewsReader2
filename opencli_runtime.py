"""Resolve logical OpenCLI commands to this checkout's managed entry point.

Only Node is resolved from the host. Never discover an opencli binary on PATH,
use npx, or fall back to a global installation when dependencies are missing.
"""
from pathlib import Path
import os
import shutil


PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_command(command):
    command = list(command)
    if not command or command[0] != "opencli":
        return command
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise FileNotFoundError("Node.js 24+ 未就绪；请安装 Node.js 或设置 NEWS_READER_NODE。")
    return [str(Path(node).resolve()), str(PROJECT_ROOT / "scripts/opencli.mjs"), *command[1:]]
