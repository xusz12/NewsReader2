"""Choose a current Tailscale client or Surge endpoint, falling back to loopback.

Only local, read-only CLI calls are used. Profile contents stay in memory;
credentials and unrelated proxy settings are never logged or retained.
"""
from concurrent.futures import ThreadPoolExecutor
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


def discover_tailscale(*, cli=None, run=subprocess.run, bind=can_bind):
    deadline = time.monotonic()+DISCOVERY_TIMEOUT
    for candidate in cli_candidates("tailscale", "Tailscale.app/Contents/MacOS/Tailscale", cli):
        try:
            data = json_command([candidate, "status", "--json"], deadline=deadline, run=run,
                                env={**os.environ, "TAILSCALE_BE_CLI": "1"})
            # `ip -4` may still return a saved address after the client stops.
            if data.get("BackendState") != "Running":
                continue
            own = data.get("Self") or {}
            if own.get("Online") is False:
                continue
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


def discover_access():
    # Inspect both independently: an installed/stopped client or a Surge node
    # without inbound access must never hide another usable connection.
    with ThreadPoolExecutor(max_workers=2) as pool:
        client_probe = pool.submit(discover_tailscale)
        surge_probe = pool.submit(discover_surge)
        client_address, surge_nodes = client_probe.result(), surge_probe.result()
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
