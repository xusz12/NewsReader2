"""Stage locked npm dependencies before stopping a running NewsReader.

Own only a temporary sibling directory. Keep the old node_modules until the
new service is healthy, so code rollback also restores its exact dependencies.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def _file_at(repo, commit, name):
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{name}"],
                            capture_output=True, timeout=15, check=False)
    return result.stdout if result.returncode == 0 else None


def _node():
    node = shutil.which(os.environ.get("NEWS_READER_NODE") or "node")
    if not node:
        raise RuntimeError("dependency_node_unavailable")
    return str(Path(node).resolve())


def validate_runtime(repo):
    manifest = repo / "collection/opencli-manifest.json"
    if not manifest.exists():
        return  # Releases before the managed runtime had no such check.
    try:
        result = subprocess.run([_node(), str(repo / "scripts/opencli.mjs"), "--nr-check"],
                                cwd=repo, capture_output=True, text=True, timeout=20, check=False)
        payload = json.loads(result.stdout)
        if result.returncode or payload.get("ok") is not True or payload.get("data", {}).get("isolated") is not True:
            raise ValueError("invalid runtime")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("dependency_runtime_check_failed") from exc


class StagedDependencies:
    def __init__(self, repo):
        self.repo = repo
        self.stage = Path(tempfile.mkdtemp(prefix=".newsreader-deps-", dir=repo.parent))
        self.previous = self.stage / "previous_node_modules"
        self.activated = False

    def activate(self):
        destination = self.repo / "node_modules"
        if destination.exists() or destination.is_symlink():
            destination.rename(self.previous)
        try:
            (self.stage / "node_modules").rename(destination)
            self.activated = True
        except OSError:
            if self.previous.exists() or self.previous.is_symlink():
                self.previous.rename(destination)
            raise

    def rollback(self):
        if not self.activated:
            return
        (self.repo / "node_modules").rename(self.stage / "rejected_node_modules")
        if self.previous.exists() or self.previous.is_symlink():
            self.previous.rename(self.repo / "node_modules")
        self.activated = False

    def close(self):
        shutil.rmtree(self.stage, ignore_errors=True)


def prepare(repo, commit):
    package = _file_at(repo, commit, "package.json")
    lock = _file_at(repo, commit, "package-lock.json")
    if package is None and lock is None:
        return None
    if not package or not lock:
        raise RuntimeError("dependency_lock_missing")
    unchanged = ((repo / "node_modules").is_dir() and (repo / "package-lock.json").exists()
            and (repo / "package.json").exists() and (repo / "package-lock.json").read_bytes() == lock
            and (repo / "package.json").read_bytes() == package)
    manifest_bytes = _file_at(repo, commit, "collection/opencli-manifest.json")
    runtime_files = {}
    if manifest_bytes:
        manifest = json.loads(manifest_bytes)
        names = ["collection/opencli-manifest.json", "scripts/opencli.mjs"]
        names += ["collection/adapters/" + name for name in manifest["adapters"]["files"]]
        runtime_files = {name: _file_at(repo, commit, name) for name in names}
    elif "@jackwener/opencli" in json.loads(package).get("dependencies", {}):
        raise RuntimeError("dependency_manifest_missing")
    if unchanged and all((repo / name).is_file() and (repo / name).read_bytes() == data for name, data in runtime_files.items()):
        validate_runtime(repo)
        return None
    staged = StagedDependencies(repo)
    try:
        for name, data in (("package.json", package), ("package-lock.json", lock)):
            (staged.stage / name).write_bytes(data)
        for name, data in runtime_files.items():
            target = staged.stage / name
            if not target.resolve().is_relative_to(staged.stage) or not data:
                raise RuntimeError("dependency_source_missing")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if unchanged:
            # An adapter-only update also needs preflight before service stop.
            # Copy instead of linking: candidate imports cannot modify the
            # running version's dependency files or resolve outside the stage.
            shutil.copytree(repo / "node_modules", staged.stage / "node_modules", symlinks=True)
            validate_runtime(staged.stage)
            staged.close()
            return None
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("dependency_npm_unavailable")
        node = _node()
        # npm's executable is a JS entrypoint. Explicit Node also supports
        # minimal GUI PATHs; ignore-scripts prevents upstream personal installs.
        env = {**os.environ, "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", os.defpath)}
        result = subprocess.run([node, str(Path(npm).resolve()), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                                cwd=staged.stage, env=env, capture_output=True, timeout=300, check=False)
        if result.returncode:
            raise RuntimeError("dependency_install_failed")
        validate_runtime(staged.stage)
        return staged
    except Exception:
        staged.close()
        raise
