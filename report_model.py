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
import uuid

import codex_client
import model_catalogs
import pi_models
import oar_client
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
    selected = codex_client.normalize_config(config["codex"])
    native = {}
    rpc = None
    try:
        command = [codex_client.executable(), "app-server", "--listen", "stdio://"]
        if selected.get("model"):
            command += ["-c", "model=" + json.dumps(selected["model"])]
        rpc = model_catalogs.Rpc(command, timeout=10)
        rpc.call("initialize", {"clientInfo": {"name": "newsreader-reports", "version": "2"}})
        rpc.send({"method": "initialized"})
        native = rpc.call("config/read", {"includeLayers": False}).get("config", {})
    except Exception:
        # An explicit default-provider model may still be resolved from the
        # CLI's public model cache; never guess an inherited model.
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
    provider = native.get("model_provider") or "openai"
    try:
        # The CLI's resolved catalog also applies to its own provider settings.
        # An explicit catalog replaces the default cache, so a missing entry
        # must not inherit a capacity from an unrelated cached model.
        catalog_path = native.get("model_catalog_json")
        if catalog_path:
            path = Path(catalog_path).expanduser()
        else:
            if provider != "openai":
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
    config = codex_client.normalize_config(config)
    command = [codex_client.executable(), "mcp", "list", "--json"]
    try:
        response = subprocess.run(command, capture_output=True, timeout=20, check=True)
        servers = json.loads(response.stdout)
        if not isinstance(servers, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in servers):
            raise ValueError()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise RuntimeError("report_model_unconfigured") from None
    restrictions = ["-c", 'web_search="disabled"', "-c", "skills.include_instructions=false",
                    "-c", "skills.bundled.enabled=false"]
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
    for feature in ("shell_tool", "apps", "plugins", "browser_use", "computer_use", "image_generation", "view_image", "hooks", "memories", "goals"):
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
            if agent["codex"]["model"] or effort:
                available = catalog(settings, runtime) if feature == "reports" else catalog(settings, runtime, feature)
                if available.get("error") and not available.get("options"):
                    raise RuntimeError("report_model_catalog_failed")
                entry = next((m for m in available["options"] if m["value"] == agent["codex"]["model"]), {})
                if not entry:
                    raise ValueError("report_model_selection_invalid")
                if effort and effort not in entry.get("reasoning_efforts", []):
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


def report_request(config, prompt, directory, bridge):
    request = {"runtime": config["runtime"], "task": "report", "prompt": prompt, "resume_prompt": "",
               "cwd": str(directory), "state_dir": str(Path(directory) / "state"),
               "output_tokens": config.get("limits", {}).get("output_tokens", 32768),
               "delegation": bool(bridge and bridge.workspace.delegation and config["runtime"] == "codex")}
    if bridge:
        transport = bridge.config()
        request["nr_tools"] = {key: transport[key] for key in ("command", "args")}
    if config["runtime"] == "codex":
        codex_client.require_ready()
        selected = codex_client.normalize_config(config["codex"])
        for field in ("model", "reasoning_effort"):
            if not selected.get(field) and config.get("limits", {}).get("resolved_"+field):
                selected[field] = config["limits"]["resolved_"+field]
        request.update(codex=selected, web_search="disabled", report_launch_args=codex_restrictions(selected, bridge))
    else:
        request.update(provider=config["provider"], model=config["model"],
                       reasoning_effort=config.get("reasoning_effort") or "off",
                       report_session_id=config.get("session_id") or str(uuid.uuid4()))
    return request


def run(config: dict, prompt: str, *, timeout: int = 900) -> str:
    proc, terminal, outcome = None, None, "interrupted"
    workspace = config.get("_workspace")
    from report_tools import Bridge
    from report_workspace import TOOLS
    began = time.monotonic()
    usage, delegates, collaboration_calls = {}, set(), Counter()
    allowed = {"mcp__newsreader_report__" + tool["name"] for tool in TOOLS} if workspace else set()
    with tempfile.TemporaryDirectory(prefix="newsreader-report-") as directory, (Bridge(workspace) if workspace else nullcontext()) as bridge:
        request = report_request(config, prompt, directory, bridge)
        if request["delegation"]:
            allowed.update({"collabAgentToolCall", "collabToolCall", "sleep"})
        pending = json.dumps(request, ensure_ascii=False).encode()
        if len(pending) > MAX_BYTES:
            raise RuntimeError("report_input_too_large")
        try:
            command = oar_client.bridge_command(uuid.uuid4().hex)
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=ROOT, start_new_session=True, bufsize=0,
                                    **({"env": _pi_subprocess_env()} if config["runtime"] == "pi" else {}))
            buffer, total = b"", 0
            deadline = time.monotonic() + (max(timeout, 3600) if workspace else timeout)
            if config.get("_deadline"):
                deadline = min(deadline, config["_deadline"])
            last_activity = time.monotonic()
            with selectors.DefaultSelector() as selector:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, "input")
                selector.register(proc.stdout, selectors.EVENT_READ, "output")
                selector.register(proc.stderr, selectors.EVENT_READ, "error")
                while selector.get_map() or proc.poll() is None:
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
                            try:
                                raw = json.loads(line)
                                if not isinstance(raw, dict):
                                    raise ValueError()
                            except (ValueError, UnicodeError):
                                raise RuntimeError("report_invalid_result") from None
                            if terminal:
                                raise RuntimeError("report_invalid_result")
                            last_activity = time.monotonic()
                            if raw.get("type") == "oar_report_agent":
                                if not request["delegation"]:
                                    raise RuntimeError("report_tools_not_allowed")
                                if not isinstance(raw.get("id"), str) or not raw["id"]:
                                    raise RuntimeError("report_invalid_result")
                                delegates.add(raw["id"])
                                workspace.progress("delegating", agents=len(delegates), completed=len(workspace.state["reviews"]), total=len(workspace.batches))
                                continue
                            if raw.get("type") == "oar_report_collaboration":
                                if not request["delegation"] or raw.get("phase") not in {"started", "completed"}:
                                    raise RuntimeError("report_tools_not_allowed")
                                receivers = raw.get("receiver_thread_ids", [])
                                if not isinstance(receivers, list) or any(not isinstance(value, str) for value in receivers):
                                    raise RuntimeError("report_invalid_result")
                                if raw["phase"] == "completed":
                                    collaboration_calls[str(raw.get("tool", "unknown"))] += 1
                                if raw.get("tool") in {"spawn_agent", "spawn"}:
                                    delegates.update(receivers)
                                if config.get("_collaboration_trace"):
                                    config["_collaboration_trace"]({k: v for k, v in raw.items() if k in {"id", "tool", "receiver_thread_ids", "status", "phase"}})
                                workspace.progress("delegating", agents=len(delegates), completed=len(workspace.state["reviews"]), total=len(workspace.batches))
                                continue
                            try:
                                event = parse_event(line.decode())
                            except (RuntimeError, UnicodeError):
                                raise RuntimeError("report_invalid_result") from None
                            if event["type"] == "oar_tool" and event["tool"] not in allowed:
                                raise RuntimeError("report_tools_not_allowed")
                            if event["type"] == "oar_result":
                                terminal = event
                                observed = raw.get("usage", {})
                                if isinstance(observed, dict):
                                    usage = {k: v for k, v in observed.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 10**15}
            if config.get("_cancelled") and config["_cancelled"]():
                raise RuntimeError("report_interrupted")
            if terminal and terminal.get("error") == "report_output_truncated":
                raise RuntimeError("report_output_truncated")
            if terminal and terminal.get("error") in {"oar_dependency_missing", "oar_node_version"}:
                raise RuntimeError("report_model_dependencies_missing")
            if proc.wait() != 0 or not terminal or terminal.get("outcome") != "completed" or not terminal.get("answer", "").strip():
                raise RuntimeError("report_model_failed")
            outcome = "completed"
            return terminal["answer"]
        except RuntimeError as exc:
            if str(exc) == "oar_node_missing":
                raise RuntimeError("report_model_dependencies_missing") from None
            outcome = "interrupted" if str(exc) == "report_interrupted" else "failed"
            raise
        finally:
            if proc is not None:
                terminate_group(proc)
            metric = {"runtime": config["runtime"], "transport": "oar", "seconds": round(time.monotonic()-began, 3),
                      "estimated_prompt_tokens": estimate_tokens(prompt), "usage": usage,
                      "delegate_threads_observed": len(delegates), "collaboration_calls": dict(collaboration_calls),
                      "usage_scope": "oar_root_session",
                      "requested_reasoning": config.get("codex", {}).get("reasoning_effort") if config["runtime"] == "codex" else config.get("reasoning_effort", ""),
                      "outcome": outcome}
            if workspace:
                workspace.metric(metric)
            elif config.get("_metric"):
                config["_metric"](metric)
