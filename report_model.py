"""Model transport for periodic reports; no legacy digest/topic workflow."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import nullcontext
from functools import lru_cache
import math
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

import codex_client
import model_catalogs
import pi_models
from settings import feature_model_config
from oar_client import parse_event, terminate_group
from llm_client import _pi_subprocess_env

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


def limits(context_window, max_output=32768, *, effective_percent=100, source, output_budget=32768):
    if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window < 8192:
        raise RuntimeError("report_model_capacity_unknown")
    if not isinstance(max_output, int) or max_output < 1:
        max_output = 32768
    output = min(max_output, output_budget, context_window // 4)
    effective = min(100, max(1, effective_percent if isinstance(effective_percent, int) else 100))
    available = math.floor(context_window * effective / 100) - output - max(4096, context_window // 10)
    if available < 1024:
        raise RuntimeError("report_model_capacity_unknown")
    return {"context_window": context_window, "input_tokens": available,
            "output_tokens": output, "source": source}


def capacity(config):
    """Read model metadata only. This performs no inference or credential export."""
    if config["runtime"] == "pi":
        try:
            metadata = pi_metadata({"action": "metadata", "provider": config["provider"], "model": config["model"]})
            # Most reasoning providers count thinking against maxTokens too.
            # A 32k total cap left the observed Flash/max turn only ~6k tokens
            # for JSON (25,773 thinking tokens).
            reasoning = bool(metadata.get("reasoning_efforts")) and config.get("reasoning_effort") != "off"
            return {**limits(metadata["contextWindow"], metadata["maxTokens"], source="pi-model-registry", output_budget=65536 if reasoning else 32768),
                    "max_output_tokens": metadata["maxTokens"], "output_budget_version": 2}
        except RuntimeError as exc:
            if str(exc) == "report_model_dependencies_missing":
                raise
            raise RuntimeError("report_model_capacity_unknown") from None
        except (ValueError, KeyError, TypeError):
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
    model = selected.get("model") or native.get("model")
    def resolved(result, default_effort=None):
        if model and not selected.get("model"):
            result["resolved_model"] = model
        effort = native.get("model_reasoning_effort") or default_effort
        if effort and not selected.get("reasoning_effort"):
            result["resolved_reasoning_effort"] = effort
        return result
    override = native.get("model_context_window")
    if override:
        return resolved(limits(override, source="codex-resolved-config"))
    provider = selected.get("provider") or native.get("model_provider") or "openai"
    try:
        # The CLI's resolved catalog also applies to custom providers/profiles.
        # An explicit catalog replaces the default cache, so a missing entry
        # must not inherit a capacity from an unrelated cached model.
        catalog_path = native.get("model_catalog_json")
        if catalog_path:
            path = Path(catalog_path).expanduser()
        else:
            if provider != "openai" or (selected.get("profile") and not native):
                raise ValueError()
            root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
            path = root / "models_cache.json"
        if path.stat().st_size > MAX_BYTES:
            raise ValueError()
        entries = json.loads(path.read_text(encoding="utf-8"))["models"]
        entry = next(row for row in entries if isinstance(row, dict) and row.get("slug") == model)
        return resolved(limits(entry["context_window"], entry.get("max_output_tokens", 32768),
                      effective_percent=entry.get("effective_context_window_percent", 100), source="codex-model-catalog"), entry.get("default_reasoning_level"))
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        raise RuntimeError("report_model_capacity_unknown") from None


def fits(config, prompt):
    return len(prompt.encode()) <= MAX_BYTES and estimate_tokens(prompt) <= config["limits"]["input_tokens"]


def toml_value(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def codex_restrictions(config: dict, bridge=None) -> list[str]:
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
    entries = {server["name"]: {"enabled": False, "command": "false"} for server in servers}
    if bridge:
        entries["newsreader_report"] = bridge.config()
        restrictions += ["--enable", "multi_agent", "-c", "agents.enabled=true",
                         "-c", "agents.max_concurrent_threads_per_session=3",
                         "-c", "skills.include_instructions=false", "-c", "skills.bundled.enabled=false"]
        for source, target in (("model", "default_subagent_model"), ("reasoning_effort", "default_subagent_reasoning_effort")):
            if config.get(source):
                restrictions += ["-c", "agents." + target + "=" + toml_value(config[source])]
        if not bridge.workspace.delegation:
            restrictions += ["--disable", "multi_agent", "-c", "agents.enabled=false"]
    else:
        restrictions += ["--disable", "multi_agent"]
    restrictions += ["-c", "mcp_servers=" + toml_value(entries)]
    for feature in ("shell_tool", "apps", "plugins", "browser_use", "computer_use", "image_generation", "hooks", "memories", "goals"):
        restrictions += ["--disable", feature]
    return restrictions


def selection(settings: dict, feature="reports") -> dict:
    """Only public choices needed by the creation form, never credentials."""
    agent = feature_model_config(settings, feature)
    return {"runtime": agent.get("runtime", "pi"),
            "codex": codex_client.normalize_config(agent.get("codex", {})),
            "pi": pi_models.normalize_config(agent.get("pi", {}))}


def pi_command():
    return pi_models.command(root=ROOT)


def pi_metadata(request):
    return pi_models.metadata(request, argv=pi_command())


def catalog(settings: dict, runtime: str, feature="reports") -> dict:
    if runtime == "pi":
        return pi_models.filter_catalog(pi_metadata({"action": "catalog"}))
    if runtime != "codex":
        raise ValueError("report_model_selection_invalid")
    config = selection(settings, feature)["codex"]
    saved = model_catalogs.cached_catalog(config)
    return saved if saved.get("options") else model_catalogs.safe_catalog(config)


def configuration(settings: dict, *, resolve=True, override=None, feature="reports") -> dict:
    agent = feature_model_config(settings, feature)
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
                available = catalog(settings, runtime) if feature == "reports" else catalog(settings, runtime, feature)
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
                agent["pi"]["reasoning_effort"] = effort
    if agent.get("runtime") == "codex":
        config = {"runtime": "codex", "codex": codex_client.normalize_config(agent.get("codex", {}))}
    else:
        pi = agent["pi"]
        if not pi.get("provider") or not pi.get("model"):
            raise RuntimeError("report_model_unconfigured")
        config = {"runtime": "pi", "provider": pi["provider"], "model": pi["model"]}
        if pi.get("reasoning_effort"):
            config["reasoning_effort"] = pi["reasoning_effort"]
    if resolve:
        config["limits"] = capacity(config)
    return config


def run(config: dict, prompt: str, *, timeout: int = 900) -> str:
    """One ephemeral turn, optionally with a private report evidence workspace.

    Reuse only the app's CLI transport/authentication, not its summary pipelines.
    Prompts travel over stdin; never log the prompt, credentials or raw errors.
    """
    proc = None
    workspace = config.get("_workspace")
    from report_tools import Bridge
    began = time.monotonic()
    usage, delegates, collaboration_calls = {}, set(), Counter()
    with tempfile.TemporaryDirectory(prefix="newsreader-report-") as directory, (Bridge(workspace) if workspace else nullcontext()) as bridge:
        if config["runtime"] == "codex":
            codex_client.require_ready()
            selected = dict(config["codex"])
            for field in ("model", "reasoning_effort"):
                if not selected.get(field) and config.get("limits", {}).get("resolved_"+field):
                    selected[field] = config["limits"]["resolved_"+field]
            command = codex_client.command(selected, ephemeral=True, cwd=Path(directory))
            command[-1:-1] = codex_restrictions(selected, bridge) if bridge else codex_restrictions(selected)
            pending = prompt.encode()
            parser = codex_client.EventParser(allow_delegation=bool(workspace))
        else:
            command = pi_command()
            request = {"prompt": prompt, "provider": config["provider"], "model": config["model"],
                       "maxTokens": config.get("limits", {}).get("output_tokens", 32768)}
            if config.get("reasoning_effort"):
                request["reasoning_effort"] = config["reasoning_effort"]
            if config.get("session_id"):
                request["session_id"] = config["session_id"]
            pending = json.dumps(request, ensure_ascii=False).encode()
            parser = None
        if len(pending) > MAX_BYTES:
            raise RuntimeError("report_input_too_large")
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=ROOT, start_new_session=True, bufsize=0,
                                    **({"env": _pi_subprocess_env()} if config["runtime"] == "pi" else {}))
            terminal = None
            buffer = b""
            total = 0
            deadline = time.monotonic() + (max(timeout, 3600) if workspace else timeout)
            if config.get("_deadline"):
                deadline = min(deadline, config["_deadline"])
            last_activity = time.monotonic()
            with selectors.DefaultSelector() as selector:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, "input")
                selector.register(proc.stdout, selectors.EVENT_READ, "output")
                selector.register(proc.stderr, selectors.EVENT_READ, "error")
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("report_model_timeout")
                    activity = max(last_activity, workspace.last_activity if workspace else 0)
                    if time.monotonic() - activity >= timeout:
                        raise RuntimeError("report_model_timeout")
                    if workspace and workspace.failed:
                        raise RuntimeError(workspace.failed)
                    if config.get("_cancelled") and config["_cancelled"]():
                        raise RuntimeError("report_interrupted")
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
                            if total > (MAX_OUTPUT_BYTES * 16 if workspace else MAX_OUTPUT_BYTES):
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
                            last_activity = time.monotonic()
                            owned_tool = (workspace and item.get("type") == "mcp_tool_call"
                                          and item.get("server") == "newsreader_report"
                                          and item.get("tool") in {t["name"] for t in __import__("report_workspace").TOOLS})
                            if parser and item.get("type") in {"command_execution", "file_change", "mcp_tool_call", "web_search", "tool_call", "image_generation"} and not owned_tool:
                                raise RuntimeError("report_tools_not_allowed")
                            if workspace and item.get("type") == "collab_tool_call":
                                if raw.get("type") == "item.completed":
                                    collaboration_calls[str(item.get("tool", "unknown"))] += 1
                                if item.get("tool") in {"spawn_agent", "spawn"}:
                                    delegates.update(item.get("receiver_thread_ids") or [])
                                if config.get("_collaboration_trace"):
                                    config["_collaboration_trace"]({k: v for k, v in item.items() if k in {"id", "type", "tool", "sender_thread_id", "receiver_thread_ids", "status"}})
                                workspace.progress("delegating", agents=len(delegates), completed=len(workspace.state["reviews"]), total=len(workspace.batches))
                            if raw.get("type") in {"turn.completed", "oar_result"} and (not parser or not raw.get("thread_id") or raw["thread_id"] == parser.thread_id):
                                observed = raw.get("usage", {})
                                if isinstance(observed, dict):
                                    usage = {k: v for k, v in observed.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 10**15}
                            event = parser.parse(line.decode()) if parser else parse_event(line.decode())
                            if not parser and event["type"] == "oar_tool":
                                raise RuntimeError("report_tools_not_allowed")
                            if event["type"] == "oar_result":
                                terminal = event
                    # A completed protocol result is authoritative; do not wait for CLI teardown.
                    if terminal:
                        break
            if terminal and not parser and terminal.get("error") in {"report_model_dependencies_missing", "report_output_truncated"}:
                raise RuntimeError(terminal["error"])
            if not terminal or terminal.get("outcome") != "completed" or not terminal.get("answer", "").strip():
                raise RuntimeError("report_model_failed")
            return terminal["answer"]
        finally:
            if proc is not None:
                terminate_group(proc)
            metric = {"runtime": config["runtime"], "seconds": round(time.monotonic()-began, 3),
                      "estimated_prompt_tokens": estimate_tokens(prompt), "usage": usage,
                      "delegate_threads_observed": len(delegates), "collaboration_calls": dict(collaboration_calls),
                      "usage_scope": "cli_turn_as_reported" if parser else "provider_completion",
                      "requested_reasoning": config.get("codex", {}).get("reasoning_effort") if config["runtime"] == "codex" else config.get("reasoning_effort", ""),
                      "outcome": terminal.get("outcome") if 'terminal' in locals() and terminal else "interrupted"}
            if workspace:
                workspace.metric(metric)
            elif config.get("_metric"):
                config["_metric"](metric)
