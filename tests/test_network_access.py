import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

import network_access


PROFILE = """[Proxy]
家庭网络 = tailscale, section-name = home
Other = http, private.example, 123, password=not-for-logs
[Tailscale home]
auth-key = secret-not-for-logs
allow-peer-access = true
"""


def test_policy_discovery_handles_custom_names_without_returning_secrets():
    assert network_access.tailscale_policies(PROFILE) == [("家庭网络", {"allow-peer-access": "true"})]


@pytest.mark.parametrize("state,address,expected", [
    ("ready", "100.79.126.62", [{"address":"100.79.126.62","incoming_enabled":True}]),
    ("ready", "100.90.1.2", [{"address":"100.90.1.2","incoming_enabled":True}]),
    ("idle", "100.79.126.62", []),
    ("ready", "192.168.1.1", []),
    ("ready", "invalid", []),
])
def test_surge_uses_current_ready_runtime_address(tmp_path, capsys, state, address, expected):
    cli = tmp_path/"surge-cli"
    cli.touch()
    calls = []
    def run(args, **kwargs):
        calls.append(args[2:])
        assert 0 < kwargs["timeout"] <= 4
        data = {"profile":PROFILE} if args[2] == "dump" else {"tailscale":{"state":state,"selfIPv4":address}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    assert network_access.discover_surge(cli=str(cli), run=run) == expected
    assert calls == [["dump","profile","effective"], ["proxy-runtime-status","家庭网络"]]
    assert "secret" not in str(capsys.readouterr())


def test_surge_timeout_does_not_prevent_normal_launcher_fallback(tmp_path):
    cli = tmp_path/"surge-cli"
    cli.touch()
    def timeout(*_, **__):
        raise subprocess.TimeoutExpired("surge-cli", 4)
    assert network_access.discover_surge(cli=str(cli), run=timeout) == []


def test_surge_hint_uses_loopback_and_reports_missing_inbound_access(monkeypatch, capsys):
    monkeypatch.setattr(network_access, "discover_tailscale", lambda: None)
    monkeypatch.setattr(network_access, "discover_surge", lambda: [{"address":"100.79.126.62","incoming_enabled":False}])
    monkeypatch.setattr(network_access.sys, "argv", ["network_access.py","8081"])
    network_access.main()
    output = capsys.readouterr()
    assert output.out.strip() == "127.0.0.1"
    assert "100.79.126.62" in output.err
    assert "http://127.0.0.1:8081" in output.err
    assert "allow-peer-access" in output.err


@pytest.mark.parametrize("explicit_host", [None, "127.0.0.2"])
@pytest.mark.parametrize("detected_host", ["127.0.0.1", "100.79.126.62"])
def test_startup_script_uses_detected_network_and_preserves_explicit_host(tmp_path, explicit_host, detected_host):
    import os
    from pathlib import Path
    import shutil
    import sys
    project = tmp_path/"project"
    project.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[1]/"启动NewsReader.command", project/"启动NewsReader.command")
    (project/"network_access.py").write_text(f"print({detected_host!r})\n")
    (project/"launcher.py").write_text("import os\nprint('selected='+os.environ['NEWS_READER_HOST'])\n")
    tailscale = tmp_path/"tailscale"
    marker = tmp_path/"tailscale-called"
    tailscale.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '100.88.185.111\\n'\n")
    tailscale.chmod(0o755)
    env = {**os.environ, "NEWS_READER_PYTHON":sys.executable, "NEWS_READER_NO_ALERT":"1", "PATH":str(tmp_path)+os.pathsep+os.environ["PATH"]}
    env.pop("NEWS_READER_HOST", None)
    if explicit_host: env["NEWS_READER_HOST"] = explicit_host
    result = subprocess.run(["zsh",str(project/"启动NewsReader.command")], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "selected="+(explicit_host or detected_host) in result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("client,surge,provider,host", [
    ("100.64.1.2", [], "tailscale", "100.64.1.2"),
    (None, [{"address":"100.79.126.62","incoming_enabled":True}], "surge", "127.0.0.1"),
    ("100.64.1.2", [{"address":"100.79.126.62","incoming_enabled":True}], "tailscale", "100.64.1.2"),
    ("100.64.1.2", [{"address":"100.79.126.62","incoming_enabled":False}], "tailscale", "100.64.1.2"),
    (None, [{"address":"100.79.126.62","incoming_enabled":False}], "local", "127.0.0.1"),
    (None, [], "local", "127.0.0.1"),
    (None, [{"address":"100.79.126.62","incoming_enabled":False},
            {"address":"100.79.126.63","incoming_enabled":True}], "surge", "127.0.0.1"),
])
def test_both_implementations_are_checked_before_choosing(client, surge, provider, host, monkeypatch):
    calls = []
    def probe_client():
        calls.append("client")
        return client
    def probe_surge():
        calls.append("surge")
        return surge
    monkeypatch.setattr(network_access, "discover_tailscale", probe_client)
    monkeypatch.setattr(network_access, "discover_surge", probe_surge)
    result = network_access.discover_access()
    assert sorted(calls) == ["client","surge"]
    assert result["provider"] == provider
    assert result["host"] == host
    if provider == "surge":
        assert result["addresses"] == [node["address"] for node in surge if node["incoming_enabled"]]


@pytest.mark.parametrize("payload,bindable,expected", [
    ({"BackendState":"Running","Self":{"Online":True,"TailscaleIPs":["100.64.1.2"]}}, True, "100.64.1.2"),
    ({"BackendState":"Running","TailscaleIPs":["100.64.1.3"]}, True, "100.64.1.3"),
    ({"BackendState":"Stopped","TailscaleIPs":["100.64.1.2"]}, True, None),
    ({"BackendState":"NeedsLogin","TailscaleIPs":["100.64.1.2"]}, True, None),
    ({"BackendState":"Running","Self":{"Online":False,"TailscaleIPs":["100.64.1.2"]}}, True, None),
    ({"BackendState":"Running","TailscaleIPs":["100.64.1.2"]}, False, None),
    ({"BackendState":"Running","TailscaleIPs":["192.168.1.2","100.1.2.3","invalid","fd7a:115c:a1e0::1"]}, True, None),
    ({"BackendState":"Running","TailscaleIPs":"100.64.1.2"}, True, None),
    ({"BackendState":"Running","Peer":{"peer":{"TailscaleIPs":["100.64.1.2"]}}}, True, None),
    ([], True, None),
])
def test_client_uses_current_self_address_and_checks_bindability(tmp_path, payload, bindable, expected):
    cli = tmp_path/"tailscale"
    cli.touch()
    probed = []
    def run(args, **kwargs):
        assert args == [str(cli),"status","--json"]
        assert kwargs["env"]["TAILSCALE_BE_CLI"] == "1"
        assert 0 < kwargs["timeout"] <= 4
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    def bind(address):
        probed.append(address)
        return bindable
    assert network_access.discover_tailscale(cli=str(cli), run=run, bind=bind) == expected
    if expected: assert probed == [expected]
    if isinstance(payload, dict) and payload.get("BackendState") != "Running": assert not probed


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "malformed"])
def test_client_failure_does_not_raise_or_reuse_a_saved_address(tmp_path, failure):
    cli = tmp_path/"tailscale"
    cli.touch()
    def run(*_, **__):
        if failure == "timeout": raise subprocess.TimeoutExpired("tailscale", 4)
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0, stdout="private error response")
    assert network_access.discover_tailscale(cli=str(cli), run=run) is None


@pytest.mark.parametrize("name,bundle", [
    ("tailscale","Tailscale.app/Contents/MacOS/Tailscale"),
    ("surge-cli","Surge.app/Contents/Applications/surge-cli"),
])
@pytest.mark.parametrize("folder", ["/Applications", "user"])
def test_finder_finds_bundled_cli_without_path(name, bundle, folder, monkeypatch):
    expected = str((Path.home()/"Applications" if folder == "user" else Path(folder))/bundle)
    monkeypatch.setattr(network_access.shutil, "which", lambda _: None)
    monkeypatch.setattr(network_access.Path, "is_file", lambda path: str(path) == expected)
    assert network_access.cli_candidates(name, bundle) == [expected]


def test_address_probe_uses_ephemeral_port_and_rejects_absent_interfaces():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1",0))
        occupied.listen()
        assert network_access.can_bind("127.0.0.1")
    assert not network_access.can_bind("203.0.113.1")


def test_surge_skips_a_broken_policy_and_discovers_other_ready_nodes(tmp_path):
    cli=tmp_path/"surge-cli"
    cli.touch()
    profile="[Proxy]\nBroken=tailscale\nGood=tailscale\n[Tailscale Good]\nallow-peer-access=true\n"
    def run(args, **_):
        if args[-1] == "Broken": return SimpleNamespace(returncode=1, stdout="")
        data = {"profile":profile} if args[2] == "dump" else {"tailscale":{"state":"ready","selfIPv4":"100.79.1.2"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    assert network_access.discover_surge(cli=str(cli), run=run) == [{"address":"100.79.1.2","incoming_enabled":True}]
