#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${NEWS_READER_PYTHON:-python3}"
if [[ -z "${NEWS_READER_PYTHON:-}" && -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  KEYCHAIN_ACCOUNT="$("${PYTHON_BIN}" -B -c 'from secret_store import KEYCHAIN_ACCOUNT; print(KEYCHAIN_ACCOUNT)')"
  if command -v security >/dev/null 2>&1; then
    KEYCHAIN_KEY="$(security find-generic-password -a "${KEYCHAIN_ACCOUNT}" -s DEEPSEEK_API_KEY -w 2>/dev/null || true)"
    if [[ -n "${KEYCHAIN_KEY}" ]]; then
      export DEEPSEEK_API_KEY="${KEYCHAIN_KEY}"
    fi
  fi
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：未检测到 DEEPSEEK_API_KEY。" >&2
  echo "请先执行以下一次性命令写入 macOS Keychain：" >&2
  printf '%s %q %s\n' 'security add-generic-password -a' "${KEYCHAIN_ACCOUNT}" "-s DEEPSEEK_API_KEY -w '你的key' -U" >&2
  exit 1
fi

exec "${PYTHON_BIN}" "${ROOT_DIR}/launcher.py" --replace-existing --detect-network
