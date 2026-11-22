from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

DISCOVERY_TIMEOUT = 4
TAILNET_V4 = ipaddress.IPv4Network("100.64.0.0/10")
DISCOVERY_ERRORS = (OSError, ValueError, TypeError, AttributeError, TimeoutError, subprocess.TimeoutExpired)


def cli_candidates(name, bundle, override=None):
    """Finder may have a minimal PATH; both Mac app variants bundle their CLI."""
    paths = ([override] if override else [shutil.which(name),
             str(Path("/Applications") / bundle), str(Path.home() / "Applications" / bundle),
             f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"])
    result, seen = [], set()
    for candidate in paths:
        if candidate and Path(candidate).is_file():
            resolved = str(Path(candidate).resolve())
            if resolved not in seen:
                result.append(str(candidate))
                seen.add(resolved)
    return result


def json_command(command, *, deadline, run, env=None):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    result = run(command, capture_output=True, text=True, timeout=remaining, check=False, env=env)
    if result.returncode:
        raise ValueError("network_cli_unavailable")
    return json.loads(result.stdout)


def tailnet_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
        return str(address) if address in TAILNET_V4 else None
    except (ipaddress.AddressValueError, TypeError):
        return None


def can_bind(address):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Do not mistake a service port occupied by the old instance for an
            # unavailable interface; the launcher will replace its own instance.
            probe.bind((address, 0))
        return True
    except OSError:
        return False


def private_serve_origins(config, port, hostname=""):
    if not isinstance(config, dict):
        return []
    candidates = set()
    targets = {f"http://{host}:{port}{suffix}" for host in ("127.0.0.1", "localhost") for suffix in ("", "/")}
    try:
        sections = (config, *(config.get("Foreground") or {}).values())
        public = {
            endpoint for section in sections for endpoint, enabled in (section.get("AllowFunnel") or {}).items()
            if enabled
        }
        handled_ports = set()
        for section in sections:
            tcp = section.get("TCP") or {}
            for endpoint, site in (section.get("Web") or {}).items():
                host, _, tls_port = endpoint.rpartition(":")
                if (not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.ts\.net", host)
                        or (hostname and host != hostname) or not re.fullmatch(r"[1-9][0-9]{0,4}", tls_port)
                        or int(tls_port) > 65535 or tls_port in handled_ports or endpoint in public
                        or tcp.get(tls_port, {}).get("HTTPS") is not True):
                    continue
                handlers = site.get("Handlers") or {}
                if (handlers.get("/") or {}).get("Proxy") in targets:
                    candidates.add((tls_port != "443", f"https://{host if tls_port == '443' else endpoint}"))
            handled_ports.update(tcp)
    except (AttributeError, TypeError):
        return []
    return [origin for _, origin in sorted(candidates)]


def private_serve_origin(config, port):
    return next(iter(private_serve_origins(config, port)), "")


def tailscale_running(status):
    return status.get("BackendState") == "Running" and (status.get("Self") or {}).get("Online") is not False


def available_serve_port(config, app_port):
    occupied = {str(app_port)}
    for section in (config, *(config.get("Foreground") or {}).values()):
        occupied.update(section.get("TCP") or {})
        for key in ("Web", "AllowFunnel"):
            occupied.update(endpoint.rpartition(":")[2] for endpoint in section.get(key) or {})
    return next((port for port in ("443", "8443", "9443") if port not in occupied), None)


def inspect_private_serve(port, *, prepare=False, cli=None, run=subprocess.run):
    deadline = time.monotonic() + DISCOVERY_TIMEOUT
    for candidate in cli_candidates("tailscale", "Tailscale.app/Contents/MacOS/Tailscale", cli):
        try:
            env = {**os.environ, "TAILSCALE_BE_CLI": "1"}
            status = json_command([candidate, "status", "--json"], deadline=deadline, run=run, env=env)
            if not tailscale_running(status):
                continue
            data = json_command([candidate, "serve", "status", "--json"], deadline=deadline, run=run, env=env)
            hostname = ((status.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
            if not isinstance(data, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.ts\.net", hostname):
                continue
            origins = private_serve_origins(data, port, hostname)
            if origins:
                return origins, None
            if prepare and hostname in (status.get("CertDomains") or []):
                tls_port = available_serve_port(data, port)
                if tls_port:
                    origin = f"https://{hostname}" + ("" if tls_port == "443" else f":{tls_port}")
                    return [], (candidate, tls_port, origin)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError, TimeoutError):
            continue
    return [], None


def discover_private_serve_origins(port, *, cli=None, run=subprocess.run):
    return inspect_private_serve(port, cli=cli, run=run)[0]


def stop_serve(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def private_serve_session(port):
    origins, plan = inspect_private_serve(port, prepare=True)
    with ExitStack() as cleanup:
        if plan:
            cli, tls_port, origin = plan
            try:
                process = subprocess.Popen(
                    [cli, "serve", f"--https={tls_port}", f"http://127.0.0.1:{port}"],
                    env={**os.environ, "TAILSCALE_BE_CLI": "1"}, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                cleanup.callback(stop_serve, process)
                deadline = time.monotonic() + DISCOVERY_TIMEOUT
                while process.poll() is None and time.monotonic() < deadline:
                    if origin in discover_private_serve_origins(port, cli=cli):
                        origins = [origin]
                        break
                    time.sleep(0.1)
            except OSError:
                pass
            if not origins:
                cleanup.close()
                print("Tailscale Serve 未建立私有 HTTPS。可用 tailscale serve status 查看现有转发。", file=sys.stderr)
        yield origins[0] if origins else ""


def discover_private_serve(port, *, cli=None, run=subprocess.run):
    return next(iter(discover_private_serve_origins(port, cli=cli, run=run)), "")


def discover_tailscale(*, cli=None, run=subprocess.run, bind=can_bind):
    deadline = time.monotonic()+DISCOVERY_TIMEOUT
    for candidate in cli_candidates("tailscale", "Tailscale.app/Contents/MacOS/Tailscale", cli):
        try:
            data = json_command([candidate, "status", "--json"], deadline=deadline, run=run,
                                env={**os.environ, "TAILSCALE_BE_CLI": "1"})
            # `ip -4` may still return a saved address after the client stops.
            if not tailscale_running(data):
                continue
            own = data.get("Self") or {}
            addresses = own.get("TailscaleIPs", data.get("TailscaleIPs", []))
            if not isinstance(addresses, list):
                continue
            for value in addresses:
                address = tailnet_ipv4(value)
                if address and bind(address):
                    return address
        except DISCOVERY_ERRORS:
            continue
    return None


def tailscale_policies(profile):
    policies, sections = [], {}
    section = ""
    for line in profile.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), re.split(r"\s+(?:#|//|;)", value.strip(), maxsplit=1)[0]
        if section.casefold() == "proxy":
            parts = [part.strip() for part in value.split(",")]
            if parts[0].casefold() == "tailscale":
                options = {key.strip(): value.strip() for key, value in
                           (part.split("=", 1) for part in parts[1:] if "=" in part)}
                policies.append((key, options.get("section-name", key).strip()))
        elif section.casefold().startswith("tailscale ") and key in {
                "allow-peer-access", "accept-incoming", "advertise-exit-node", "advertise-routes"}:
            sections.setdefault(section[9:].strip(), {})[key] = value
    return [(name, sections.get(section, {})) for name, section in policies]


def discover_surge(*, cli=None, run=subprocess.run):
    deadline = time.monotonic()+DISCOVERY_TIMEOUT
    found = []
    for candidate in cli_candidates("surge-cli", "Surge.app/Contents/Applications/surge-cli", cli):
        def request(*args):
            return json_command([candidate, "--raw", *args], deadline=deadline, run=run)
        try:
            profile = request("dump", "profile", "effective").get("profile", "")
            if not isinstance(profile, str):
                continue
            for name, options in tailscale_policies(profile):
                try:
                    runtime = request("proxy-runtime-status", name).get("tailscale", {})
                    address = tailnet_ipv4(runtime.get("selfIPv4", ""))
                    if runtime.get("state") != "ready" or not address:
                        continue
                    enabled = any(options.get(key, "").lower() == "true" for key in
                                  ("allow-peer-access", "accept-incoming", "advertise-exit-node")) or bool(options.get("advertise-routes"))
                    node = {"address": address, "incoming_enabled": enabled}
                    if node not in found:
                        found.append(node)
                except DISCOVERY_ERRORS:
                    if time.monotonic() >= deadline:
                        break
        except DISCOVERY_ERRORS:
            continue
        if found:
            break
    return found


def discover_address_candidates(port=None):
    # Inspect both independently: an installed/stopped client or a Surge node
    # without inbound access must never hide another usable connection.
    with ThreadPoolExecutor(max_workers=3) as pool:
        client_probe = pool.submit(discover_tailscale)
        surge_probe = pool.submit(discover_surge)
        serve_probe = pool.submit(discover_private_serve_origins, port) if port is not None else None
        return client_probe.result(), surge_probe.result(), serve_probe.result() if serve_probe else []


def discover_connections():
    client_address, surge_nodes, _ = discover_address_candidates()
    return client_address, surge_nodes


def service_matches(url, commit, instance_id, *, open_url):
    if not instance_id:
        return False
    try:
        with open_url(url, timeout=0.5) as response:
            if response.geturl() != url:
                return False
            payload = json.loads(response.read(8192))
        return (isinstance(payload, dict) and payload.get("ok") is True
                and payload.get("commit") == commit and payload.get("instance_id") == instance_id)
    except (OSError, ValueError, HTTPException):
        return False


def verified_remote_addresses(port, client_address, surge_nodes, origins, *, matches, preferred_origin=""):
    addresses = []
    loopback_ready = (origins or surge_nodes) and matches("127.0.0.1")
    if loopback_ready:
        addresses.extend(("serve", origin + "/") for origin in sorted(origins, key=lambda value: value != preferred_origin))
    if client_address and matches(client_address):
        addresses.append(("tailscale", f"http://{client_address}:{port}/"))
    if loopback_ready:
        for node in surge_nodes:
            address = ("surge", f"http://{node['address']}:{port}/")
            if node["incoming_enabled"] and not any(url == address[1] for _, url in addresses):
                addresses.append(address)
    return addresses


def discover_access():
    client_address, surge_nodes = discover_connections()
    if client_address:
        return {"provider": "tailscale", "host": client_address, "addresses": [client_address]}
    reachable = [node["address"] for node in surge_nodes if node["incoming_enabled"]]
    if reachable:
        return {"provider": "surge", "host": "127.0.0.1", "addresses": reachable}
    return {"provider": "local", "host": "127.0.0.1", "addresses": [],
            "surge_without_incoming": [node["address"] for node in surge_nodes]}


def main():
    access = discover_access()
    port = sys.argv[1] if len(sys.argv)>1 and sys.argv[1].isdigit() else "8080"
    print(access["host"])
    if access["provider"] == "tailscale":
        print(f"检测到 Tailscale 客户端当前地址，使用 http://{access['host']}:{port} 启动。", file=sys.stderr)
    elif access["provider"] == "surge":
        print(f"检测到 Surge Tailscale 入站连接，NewsReader 监听 http://127.0.0.1:{port}。", file=sys.stderr)
        for address in access["addresses"]:
            print(f"其他 Tailscale 设备的访问地址：http://{address}:{port}", file=sys.stderr)
    else:
        for address in access.get("surge_without_incoming", []):
            print(f"检测到 Surge Tailscale 地址 {address}，但该节点未启用 allow-peer-access 入站访问。", file=sys.stderr)
        print(f"未找到可用的 Tailscale 客户端或 Surge 入站连接，使用本机地址 http://127.0.0.1:{port} 启动。", file=sys.stderr)


if __name__ == "__main__":
    main()
