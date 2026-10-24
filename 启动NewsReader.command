#!/bin/zsh

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

show_startup_error() {
  local message="$1"
  print -u2 -- "$message"
  if [[ "${NEWS_READER_NO_ALERT:-0}" != "1" ]]; then
    /usr/bin/osascript -e 'display alert "NewsReader 启动失败" message "请查看启动终端中的错误信息。"' >/dev/null 2>&1 || true
  fi
}

PYTHON_BIN=""
PYTHON_VALIDATION_OUTPUT=""

validate_python_candidate() {
  local candidate="$1"
  local resolved_candidate=""
  local validation_output=""
  local validation_status=0

  if [[ "$candidate" == */* ]]; then
    resolved_candidate="$candidate"
  else
    resolved_candidate="$(command -v "$candidate" 2>/dev/null || true)"
  fi
  if [[ -z "$resolved_candidate" || ! -x "$resolved_candidate" ]]; then
    return 1
  fi

  validation_output=$("$resolved_candidate" - <<'PY' 2>&1
import sys

missing = []
try:
    import flask
except Exception:
    missing.append("Flask")
try:
    import openai
except Exception:
    missing.append("openai")
if missing:
    print("缺少或无法导入 Python 依赖：" + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY
)
  validation_status=$?
  if (( validation_status == 0 )); then
    PYTHON_BIN="$resolved_candidate"
    return 0
  fi

  PYTHON_VALIDATION_OUTPUT="$validation_output"
  return 1
}

if [[ -n "${NEWS_READER_PYTHON:-}" ]]; then
  if ! validate_python_candidate "$NEWS_READER_PYTHON"; then
    [[ -n "$PYTHON_VALIDATION_OUTPUT" ]] && print -u2 -- "$PYTHON_VALIDATION_OUTPUT"
    show_startup_error "NewsReader 启动前检查失败：NEWS_READER_PYTHON 指向的 Python 缺少 Flask 或 openai。请运行：$NEWS_READER_PYTHON -m pip install -r \"$SCRIPT_DIR/requirements.txt\"，然后重新双击此文件。"
    exit 1
  fi
else
  typeset -a python_candidates
  python_candidates=(
    "$SCRIPT_DIR/.venv/bin/python"
    "$HOME/.venvs/newsreader-workbench/bin/python"
    "$(command -v python3 2>/dev/null || true)"
  )

  for python_candidate in "${python_candidates[@]}"; do
    [[ -n "$python_candidate" ]] || continue
    if validate_python_candidate "$python_candidate"; then
      break
    fi
  done

  if [[ -z "$PYTHON_BIN" ]]; then
    show_startup_error "NewsReader 启动失败：未找到可用的 Python 环境（需要可执行且已安装 Flask、openai）。请运行：python3 -m pip install -r \"$SCRIPT_DIR/requirements.txt\"，或设置 NEWS_READER_PYTHON 指向依赖完整的 Python。"
    exit 1
  fi
fi

# OAR is optional for reading news: diagnose, but never install/upgrade implicitly.
OAR_NODE="${NEWS_READER_NODE:-$(command -v node 2>/dev/null || true)}"
if [[ -z "$OAR_NODE" ]] || ! command -v "$OAR_NODE" >/dev/null 2>&1; then
  print -u2 -- "新闻 Chat / Agent 不可用：请安装 Node.js 24+；新闻阅读仍可启动。"
elif ! "$OAR_NODE" -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)' >/dev/null 2>&1; then
  print -u2 -- "新闻 Chat / Agent 不可用：OAR 需要 Node.js 24+；新闻阅读仍可启动。"
elif [[ ! -f "$SCRIPT_DIR/node_modules/@botiverse/oar/package.json" ]]; then
  print -u2 -- "新闻 Chat / Agent 依赖缺失；请在项目目录执行 npm ci --ignore-scripts。新闻阅读仍可启动。"
fi

# Preserve the historical remote-access behavior without making every direct
# launcher invocation expose the service. An explicit host always wins; when
# it is absent, use a valid Tailscale IPv4 and otherwise keep loopback.
if [[ -z "${NEWS_READER_HOST:-}" ]] && command -v tailscale >/dev/null 2>&1; then
  tailscale_host="$(tailscale ip -4 2>/dev/null)"
  tailscale_status=$?
  tailscale_host="$(print -r -- "$tailscale_host" | awk 'NF { print $1; exit }')"
  if (( tailscale_status == 0 )) && [[ -n "$tailscale_host" ]] && awk -F. '
    NF == 4 {
      for (i = 1; i <= NF; i++) {
        if ($i !~ /^[0-9]+$/ || $i > 255) exit 1
      }
      exit 0
    }
    { exit 1 }
  ' <<< "$tailscale_host"; then
    export NEWS_READER_HOST="$tailscale_host"
  fi
fi

"$PYTHON_BIN" "$SCRIPT_DIR/launcher.py" --replace-existing
exit_code=$?
if (( exit_code != 0 )); then
  show_startup_error "NewsReader 启动失败（退出码 $exit_code）。请查看此终端中的错误信息。"
fi
exit "$exit_code"
