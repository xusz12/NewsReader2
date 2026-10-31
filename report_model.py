"""Model transport for periodic reports; no legacy digest/topic workflow."""
from __future__ import annotations

import json
from functools import lru_cache
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

import codex_client
import model_catalogs
from settings import feature_model_config
from oar_client import parse_event, terminate_group

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


@lru_cache(maxsize=1)
def tokenizer():
    try:
        import tiktoken
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        # Cache an unavailable tokenizer too, rather than retrying a failed
        # asset download for every item in a large edition.
        return None


def estimate_tokens(text: str) -> int:
    # A local estimate, not a provider usage claim. The input budget separately
    # reserves room for tokenizer differences and the CLI's own instructions.
    try:
        encoder = tokenizer()
        if encoder is not None:
            return len(encoder.encode(text, disallowed_special=()))
    except Exception:
        # UTF-8 bytes are a conservative upper bound for byte-based tokenizers.
        # Missing tokenizer assets must never silently allow an oversized input.
        pass
    return len(text.encode("utf-8"))


def limits(context_window, max_output=32768, *, effective_percent=100, source):
    if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window < 8192:
        raise RuntimeError("report_model_capacity_unknown")
    if not isinstance(max_output, int) or max_output < 1:
        max_output = 32768
    output = min(max_output, 32768, context_window // 4)
    effective = min(100, max(1, effective_percent if isinstance(effective_percent, int) else 100))
    available = math.floor(context_window * effective / 100) - output - max(4096, context_window // 10)
    if available < 1024:
        raise RuntimeError("report_model_capacity_unknown")
    return {"context_window": context_window, "input_tokens": available,
            "output_tokens": output, "source": source}


def capacity(config):
    """Read model metadata only. This performs no inference or credential export."""
    if config["runtime"] == "pi":
        node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
        if not node:
            raise RuntimeError("report_model_unconfigured")
        try:
            result = subprocess.run([node, str(ROOT / "scripts/report_model.mjs")],
                input=json.dumps({"action": "metadata", "provider": config["provider"], "model": config["model"]}),
                text=True, capture_output=True, timeout=20, check=True)
            metadata = json.loads(result.stdout)
            return limits(metadata["contextWindow"], metadata["maxTokens"], source="pi-model-registry")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            raise RuntimeError("report_model_capacity_unknown") from None
    selected = config["codex"]
    native = {}
    rpc = None
    try:
        command = [codex_client.executable(), "app-server", "--stdio"]
        if selected.get("profile"):
            command += ["--profile", selected["profile"]]
        for name, key in (("model", "model"), ("provider", "model_provider")):
            if selected.get(name):
                command += ["-c", key + "=" + json.dumps(selected[name])]
        rpc = model_catalogs.Rpc(command, timeout=10)
        rpc.call("initialize", {"clientInfo": {"name": "newsreader-reports", "version": "2"}})
        rpc.send({"method": "initialized"})
        native = rpc.call("config/read", {"includeLayers": False}).get("config", {})
    except Exception:
        # An explicit default-provider model may still be resolved from the
        # CLI's public model cache; never guess a profile's inherited model.
        pass
    finally:
        if rpc:
            rpc.close()
    override = native.get("model_context_window")
    if override:
        return limits(override, source="codex-resolved-config")
    model = selected.get("model") or native.get("model")
    provider = selected.get("provider") or native.get("model_provider") or "openai"
    if provider != "openai" or (selected.get("profile") and not native):
        raise RuntimeError("report_model_capacity_unknown")
    try:
        root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        path = root / "models_cache.json"
        if path.stat().st_size > MAX_BYTES:
            raise ValueError()
        entry = next(row for row in json.loads(path.read_text())["models"] if row.get("slug") == model)
        return limits(entry["context_window"], entry.get("max_output_tokens", 32768),
                      effective_percent=entry.get("effective_context_window_percent", 100), source="codex-model-catalog")
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        raise RuntimeError("report_model_capacity_unknown") from None


def fits(config, prompt):
    return len(prompt.encode()) <= MAX_BYTES and estimate_tokens(prompt) <= config["limits"]["input_tokens"]


def codex_restrictions(config: dict) -> list[str]:
    # TOML table overrides merge, so mcp_servers={} does not disable saved servers.
    # Discover names without exposing server URLs, environment values or credentials.
    command = [codex_client.executable()]
    if config.get("profile"):
        command += ["--profile", config["profile"]]
    command += ["mcp", "list", "--json"]
    try:
        response = subprocess.run(command, capture_output=True, timeout=20, check=True)
        servers = json.loads(response.stdout)
        if not isinstance(servers, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in servers):
            raise ValueError()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise RuntimeError("report_model_unconfigured") from None
    restrictions = ["--skip-git-repo-check", "-c", 'web_search="disabled"']
    # Bootstrap validates a transport even for disabled entries. Replace it with
    # an inert command rather than copying native server secrets into argv.
    restrictions += ["-c", "mcp_servers={" + ",".join(json.dumps(server["name"]) + '={enabled=false,command="false"}' for server in servers) + "}"]
    for feature in ("shell_tool", "apps", "plugins", "browser_use", "computer_use", "image_generation", "multi_agent", "hooks"):
        restrictions += ["--disable", feature]
    return restrictions


def selection(settings: dict) -> dict:
    """Only public choices needed by the creation form, never credentials."""
    agent = feature_model_config(settings, "reports")
    return {"runtime": agent.get("runtime", "pi"),
            "codex": codex_client.normalize_config(agent.get("codex", {})),
            "pi": {key: agent.get("pi", {}).get(key, "") for key in ("provider", "model")}}


def pi_metadata(request):
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise RuntimeError("report_model_catalog_failed")
    try:
        result = subprocess.run([node, str(ROOT / "scripts/report_model.mjs")],
            input=json.dumps(request), text=True, capture_output=True, timeout=20, check=True)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("report_model_catalog_failed") from None


def catalog(settings: dict, runtime: str) -> dict:
    if runtime == "pi":
        return pi_metadata({"action": "catalog"})
    if runtime != "codex":
        raise ValueError("report_model_selection_invalid")
    config = selection(settings)["codex"]
    saved = model_catalogs.cached_catalog(config)
    return saved if saved.get("options") else model_catalogs.safe_catalog(config)


def configuration(settings: dict, *, resolve=True, override=None) -> dict:
    agent = feature_model_config(settings, "reports")
    if override is not None:
        if not isinstance(override, dict) or set(override) - {"runtime", "model", "provider", "reasoning_effort"}:
            raise ValueError("report_model_selection_invalid")
        runtime = override.get("runtime")
        if runtime not in ("codex", "pi"):
            raise ValueError("report_model_selection_invalid")
        for key, limit in (("model", 120), ("provider", 40), ("reasoning_effort", 20)):
            value = override.get(key, "")
            if not isinstance(value, str) or len(value) > limit or value != value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError("report_model_selection_invalid")
        effort = override.get("reasoning_effort", "")
        agent["runtime"] = runtime
        if runtime == "codex":
            if override.get("provider"):
                raise ValueError("report_model_selection_invalid")
            try:
                agent["codex"] = codex_client.normalize_config({**agent.get("codex", {}),
                    "model": override.get("model", ""), "reasoning_effort": effort})
            except ValueError:
                raise ValueError("report_model_selection_invalid") from None
            if effort:
                available = catalog(settings, runtime)
                if available.get("error") and not available.get("options"):
                    raise RuntimeError("report_model_catalog_failed")
                entry = next((m for m in available["options"] if m["value"] == agent["codex"]["model"]), {})
                if effort not in entry.get("reasoning_efforts", []):
                    raise ValueError("report_reasoning_unsupported")
        else:
            agent["pi"] = {key: override.get(key, "") for key in ("provider", "model")}
            if not all(agent["pi"].values()):
                raise ValueError("report_model_selection_invalid")
            if effort:
                metadata = pi_metadata({"action": "metadata", **agent["pi"]})
                if effort not in metadata.get("reasoning_efforts", []):
                    raise ValueError("report_reasoning_unsupported")
    if agent.get("runtime") == "codex":
        config = {"runtime": "codex", "codex": codex_client.normalize_config(agent.get("codex", {}))}
    else:
        pi = agent["pi"]
        if not pi.get("provider") or not pi.get("model"):
            raise RuntimeError("report_model_unconfigured")
        config = {"runtime": "pi", "provider": pi["provider"], "model": pi["model"]}
        if override and override.get("reasoning_effort"):
            config["reasoning_effort"] = override["reasoning_effort"]
    if resolve:
        config["limits"] = capacity(config)
    return config


def run(config: dict, prompt: str, *, timeout: int = 900) -> str:
    """One ephemeral turn. Reject external tool evidence and partial responses.

    Reuse only the app's CLI transport/authentication, not its summary pipelines.
    Prompts travel over stdin; never log the prompt, credentials or raw errors.
    """
    proc = None
    with tempfile.TemporaryDirectory(prefix="newsreader-report-") as directory:
        if config["runtime"] == "codex":
            codex_client.require_ready()
            command = codex_client.command(config["codex"], ephemeral=True, cwd=Path(directory))
            command[-1:-1] = codex_restrictions(config["codex"])
            pending = prompt.encode()
            parser = codex_client.EventParser()
        else:
            node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
            if not node:
                raise RuntimeError("report_model_unconfigured")
            command = [node, str(ROOT / "scripts/report_model.mjs")]
            request = {"prompt": prompt, "provider": config["provider"], "model": config["model"],
                       "maxTokens": config.get("limits", {}).get("output_tokens", 32768)}
            if config.get("reasoning_effort"):
                request["reasoning_effort"] = config["reasoning_effort"]
            pending = json.dumps(request, ensure_ascii=False).encode()
            parser = None
        if len(pending) > MAX_BYTES:
            raise RuntimeError("report_input_too_large")
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=ROOT, start_new_session=True, bufsize=0)
            terminal = None
            buffer = b""
            total = 0
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, "input")
                selector.register(proc.stdout, selectors.EVENT_READ, "output")
                selector.register(proc.stderr, selectors.EVENT_READ, "error")
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("report_model_timeout")
                    for key, _ in selector.select(.1):
                        if key.data == "input":
                            try:
                                pending = pending[os.write(key.fileobj.fileno(), pending[:4096]):]
                            except BrokenPipeError:
                                pending = b""
                            if not pending:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.data == "output":
                            total += len(chunk)
                            if total > MAX_OUTPUT_BYTES:
                                raise RuntimeError("report_invalid_result")
                            buffer += chunk
                        if key.data != "output":
                            continue
                        while b"\n" in buffer or (not chunk and buffer):
                            if b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                            else:
                                line, buffer = buffer, b""
                            if not line.strip():
                                continue
                            raw = json.loads(line)
                            item = raw.get("item", {})
                            if parser and item.get("type") in {"command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call", "image_generation"}:
                                raise RuntimeError("report_tools_not_allowed")
                            event = parser.parse(line.decode()) if parser else parse_event(line.decode())
                            if not parser and event["type"] == "oar_tool":
                                raise RuntimeError("report_tools_not_allowed")
                            if event["type"] == "oar_result":
                                terminal = event
                    # A completed protocol result is authoritative; do not wait for CLI teardown.
                    if terminal:
                        break
            if not terminal or terminal.get("outcome") != "completed" or not terminal.get("answer", "").strip():
                raise RuntimeError("report_model_failed")
            return terminal["answer"]
        finally:
            if proc is not None:
                terminate_group(proc)
