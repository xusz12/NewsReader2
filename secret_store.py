from __future__ import annotations

import shutil
import subprocess
import sys


KEYCHAIN_ACCOUNT = "newsreader-workbench"


class SecretStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _ensure_keychain_available() -> None:
    if sys.platform != "darwin":
        raise SecretStoreError("keychain_unavailable")
    if not shutil.which("security"):
        raise SecretStoreError("keychain_unavailable")


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    _ensure_keychain_available()
    try:
        return subprocess.run(
            ["security", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - env-specific
        raise SecretStoreError("keychain_unavailable") from exc


def has_secret(service_name: str) -> bool:
    result = _run_security(["find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service_name])
    if result.returncode == 0:
        return True
    if result.returncode == 44:
        return False
    raise SecretStoreError("keychain_unavailable")


def read_secret(service_name: str) -> str | None:
    result = _run_security(["find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service_name, "-w"])
    if result.returncode == 0:
        value = result.stdout.strip()
        return value or None
    if result.returncode == 44:
        return None
    raise SecretStoreError("read_failed")


def write_secret(service_name: str, secret: str) -> None:
    if not isinstance(secret, str) or not secret.strip():
        raise SecretStoreError("empty_key")
    result = _run_security(
        ["add-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service_name, "-w", secret.strip(), "-U"]
    )
    if result.returncode != 0:
        raise SecretStoreError("write_failed")


def delete_secret(service_name: str) -> None:
    result = _run_security(["delete-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service_name])
    if result.returncode in {0, 44}:
        return
    raise SecretStoreError("delete_failed")
