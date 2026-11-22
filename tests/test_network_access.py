import json
from contextlib import contextmanager
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


def test_serve_origin_requires_private_https_on_the_exact_development_target():
    target = "mac.example.ts.net:443"
    config = {"TCP":{"443":{"HTTPS":True}},
              "Web":{target:{"Handlers":{"/":{"Proxy":"http://127.0.0.1:18080"}}}}}
    assert network_access.private_serve_origin(config, 18080) == "https://mac.example.ts.net"
    assert not network_access.private_serve_origin(config, 8080)
    assert not network_access.private_serve_origin(config | {"AllowFunnel":{target:True}}, 18080)
    assert not network_access.private_serve_origin(config | {"TCP":{"443":{"HTTP":True}}}, 18080)


def test_serve_separates_formal_and_development_ports_and_rejects_funnel():
    host = "mac.example.ts.net"
    development = f"{host}:443"
    formal = f"{host}:8443"
    config = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {development: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18080"}}}},
        "Foreground": {"session": {
            "TCP": {"8443": {"HTTPS": True}},
            "Web": {formal: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080/"}}}},
        }},
    }
    assert network_access.private_serve_origins(config, 8080, host) == [f"https://{formal}"]
    assert network_access.private_serve_origins(config, 18080, host) == [f"https://{host}"]
    assert not network_access.private_serve_origins(config, 8080, "old.example.ts.net")
    assert not network_access.private_serve_origins(config | {"AllowFunnel": {formal: True}}, 8080)
    assert not network_access.private_serve_origins(config | {"Foreground": "unrecognized"}, 8080)


def test_disconnected_tailscale_does_not_reopen_cached_serve_mapping(tmp_path):
    cli = tmp_path / "tailscale"
    cli.touch()
    calls = []

    def run(command, **kwargs):
        calls.append(command[1:])
        return SimpleNamespace(returncode=0, stdout='{"BackendState":"Stopped"}')

    assert not network_access.discover_private_serve(18080, cli=str(cli), run=run)
    assert calls == [["status", "--json"]]


@pytest.mark.parametrize("certs,config,existing,port", [
    ([], {}, [], None),
    (["mac.example.ts.net"], {}, [], "443"),
    (["mac.example.ts.net"], {"TCP":{"443":{"HTTPS":True}},
        "Foreground":{"session":{"AllowFunnel":{"mac.example.ts.net:8443":True}}}}, [], "9443"),
    (["mac.example.ts.net"], {"TCP":{p:{"HTTPS":True} for p in ("443", "8443", "9443")}}, [], None),
    (["mac.example.ts.net"], {"TCP":{"8443":{"HTTPS":True}}, "Web":{"mac.example.ts.net:8443":{
        "Handlers":{"/":{"Proxy":"http://localhost:8080/"}}}}}, ["https://mac.example.ts.net:8443"], None),
])
def test_serve_plan_preserves_existing_services(tmp_path, certs, config, existing, port):
    cli = tmp_path / "tailscale"
    cli.touch()
    status = {"BackendState":"Running", "Self":{"DNSName":"mac.example.ts.net."}, "CertDomains":certs}

    def run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(status if command[1] == "status" else config))

    origins, plan = network_access.inspect_private_serve(8080, prepare=True, cli=str(cli), run=run)
    assert origins == existing
    if port:
        assert plan == (str(cli), port, "https://mac.example.ts.net" + ("" if port == "443" else ":" + port))
    else:
        assert plan is None


@pytest.mark.parametrize("verified", [False, True])
def test_temporary_serve_requires_readback_and_closes_with_the_app(monkeypatch, verified):
    origin = "https://mac.example.ts.net:8443"
    monkeypatch.setattr(network_access, "inspect_private_serve", lambda *_, **kw: ([], ("tailscale", "8443", origin)))
    commands = []
    stopped = []
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: stopped.append(True), wait=lambda timeout: 0)
    def start(args, **kwargs):
        commands.append(args)
        return process
    monkeypatch.setattr(network_access.subprocess, "Popen", start)
    monkeypatch.setattr(network_access, "discover_private_serve_origins", lambda *_, **kw: [origin] if verified else [])
    monkeypatch.setattr(network_access, "DISCOVERY_TIMEOUT", 1 if verified else 0)
    with network_access.private_serve_session(8080) as actual:
        assert actual == (origin if verified else "")
        assert bool(stopped) is not verified
    assert commands == [["tailscale", "serve", "--https=8443", "http://127.0.0.1:8080"]]
    assert stopped == [True]


def test_managed_https_uses_bound_loopback_even_without_a_tailnet_ip(monkeypatch):
    import app

    monkeypatch.setenv("NEWS_READER_MANAGED", "1")
    monkeypatch.setenv("NEWS_READER_AUTO_SERVE", "1")
    monkeypatch.setenv("NEWS_READER_HOST", "127.0.0.1")
    monkeypatch.setenv("NEWS_READER_PORT", "18080")
    monkeypatch.setenv("NEWS_READER_INSTANCE_ID", "fixture")
    monkeypatch.delenv("NEWS_READER_TAILSCALE_BIND", raising=False)
    monkeypatch.delenv("NEWS_READER_PUSH_ORIGIN", raising=False)
    steps = []
    @contextmanager
    def listener(host, port, application, *, threaded):
        steps.append(("listen", host, port))
        yield SimpleNamespace(serve_forever=lambda: steps.append("serve"))
    @contextmanager
    def private_https(port):
        steps.append(("https", port))
        yield "https://mac.example.ts.net"
    monkeypatch.setattr(app, "make_server", listener)
    monkeypatch.setattr(app, "private_serve_session", private_https)
    monkeypatch.setattr(app.runtime.push, "configure", lambda *args: steps.append(args))
    monkeypatch.setattr(app.runtime, "start", lambda: steps.append("ready"))
    monkeypatch.setitem(app.app.config, "NEWS_READER_PORT", 18080)
    app.main()
    assert steps == [("listen", "127.0.0.1", 18080), ("https", 18080),
                     ("https://mac.example.ts.net", "18080"), "ready", "serve"]


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
