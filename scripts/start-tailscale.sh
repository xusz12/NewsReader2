#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  if command -v security >/dev/null 2>&1; then
    KEYCHAIN_KEY="$(security find-generic-password -a newsreader-workbench -s DEEPSEEK_API_KEY -w 2>/dev/null || true)"
    if [[ -n "${KEYCHAIN_KEY}" ]]; then
      export DEEPSEEK_API_KEY="${KEYCHAIN_KEY}"
    fi
  fi
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：未检测到 DEEPSEEK_API_KEY。" >&2
  echo "请先执行以下一次性命令写入 macOS Keychain：" >&2
  echo "security add-generic-password -a newsreader-workbench -s DEEPSEEK_API_KEY -w '你的key' -U" >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "错误：未找到 tailscale 命令，请先安装并登录 Tailscale。" >&2
  exit 1
fi

TAILSCALE_IP="$(tailscale ip -4 | head -n 1 | tr -d '[:space:]')"
if [[ -z "${TAILSCALE_IP}" ]]; then
  echo "错误：未获取到 Tailscale IPv4，请确认 Tailscale 已连接。" >&2
  exit 1
fi

PORT="${NEWS_READER_PORT:-8080}"

echo "使用 Tailscale IP 启动：${TAILSCALE_IP}:${PORT}"
echo "手机访问：http://${TAILSCALE_IP}:${PORT}"

NEWS_READER_HOST="${TAILSCALE_IP}" NEWS_READER_PORT="${PORT}" python3 app.py
