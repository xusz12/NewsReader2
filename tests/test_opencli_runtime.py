import json
from pathlib import Path
import subprocess

import pytest

import opencli_runtime
import collection_sources
import collection_environment


def test_resolver_never_looks_for_global_opencli(monkeypatch):
    calls = []
    def which(name):
        calls.append(name)
        return "/project-node" if name == "node" else None
    monkeypatch.delenv("NEWS_READER_NODE", raising=False)
    monkeypatch.setattr(opencli_runtime.shutil, "which", which)
    result = opencli_runtime.resolve_command(["opencli", "twitter", "tweets", "example"])
    assert result == ["/project-node", str(opencli_runtime.PROJECT_ROOT / "scripts/opencli.mjs"), "twitter", "tweets", "example"]
    assert calls == ["node"]


def test_node_override_and_missing_node_fail_without_fallback(monkeypatch):
    monkeypatch.setenv("NEWS_READER_NODE", "/missing/node")
    calls = []
    monkeypatch.setattr(opencli_runtime.shutil, "which", lambda name: calls.append(name))
    with pytest.raises(FileNotFoundError, match="Node.js"):
        opencli_runtime.resolve_command(["opencli", "--version"])
    assert calls == ["/missing/node"]


def test_acquisition_process_uses_managed_entry_even_with_poisoned_path(tmp_path, monkeypatch):
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for subprocess integration")
    poison = tmp_path / "opencli"
    poison.write_text("#!/bin/sh\nexit 99\n"); poison.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("NEWS_READER_NODE", node)
    p = collection_sources.run_command(["opencli", "--nr-check"], timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["data"]["isolated"] is True


@pytest.mark.parametrize("corrupt", ["commands", "isolated", "version", "adapter_commit"])
def test_required_command_check_rejects_partial_or_unmanaged_runtime(monkeypatch, corrupt):
    manifest = json.loads((opencli_runtime.PROJECT_ROOT / "collection/opencli-manifest.json").read_text())
    report = {"commands": [f"{r['site']} {r['name']}" for r in manifest["commands"]],
              "isolated": True, "version": manifest["version"], "adapter_commit": manifest["adapters"]["commit"]}
    report[corrupt] = report[corrupt][1:] if corrupt == "commands" else None
    monkeypatch.setattr(collection_environment, "_json", lambda *a: report)
    with pytest.raises(collection_environment.ProbeError):
        collection_environment._commands("opencli")
