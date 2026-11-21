from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

from scripts import check_opencli_release as release
from scripts import verify_collection_runtime as verify


def test_live_article_sample_skips_dedicated_bloomberg_video_without_skipping_supported_articles():
    video = "https://www.bloomberg.com/news/videos/2026-11-12/technology-news-video"
    article = "https://www.bloomberg.com/news/articles/2026-11-12/technology-news"
    reuters = "https://www.reuters.com/world/article-with-embedded-video-2026-11-12/"
    assert verify.article_sample([{"url": video}, {"url": article}]) == article
    assert verify.article_sample([{"url": reuters}]) == reuters
    assert verify.article_sample([{"url": video}]) is None
    assert verify.article_sample([]) is None


@pytest.fixture
def report():
    root = release.ROOT
    manifest = json.loads((root / "collection/opencli-manifest.json").read_text())
    sources = json.loads((root / "collection/sources.json").read_text())
    cases = json.loads((root / verify.SAMPLE_FILE).read_text())
    return {"ok": True, "fingerprint_version": verify.FINGERPRINT_VERSION, "fingerprint": verify.content_fingerprint(),
            "runtime": {"isolated": True, "version": manifest["version"],
                        "adapter_commit": manifest["adapters"]["commit"], "adapter_version": manifest["adapters"]["version"]},
            "sources": [{"section": s["section"], "ok": True} for s in sources],
            "details": [{"name": name, "ok": True} for name in
                        ["Reuters", "Bloomberg", "TechCrunch", "Ars Technica", *(r["name"] for r in cases)]],
            "isolation": {"external_modules_blocked": True, "global_cli_used": False, "project_daemon_started": True}}


@pytest.fixture
def input_changes(monkeypatch):
    changes = {}
    read_bytes = Path.read_bytes

    def read_changed_inputs(path):
        name = path.relative_to(verify.ROOT).as_posix() if path.is_relative_to(verify.ROOT) else None
        return changes[name] if name in changes else read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_changed_inputs)
    return changes


def test_complete_matching_run_is_accepted(report):
    release.validate_report(report)


@pytest.mark.parametrize("change", ["changed_code", "missing_source", "duplicate_source", "failed_detail", "global_cli", "old_daemon", "unproven_isolation"])
def test_incomplete_or_stale_evidence_blocks_release(report, change):
    altered = deepcopy(report)
    if change == "changed_code": altered["fingerprint"] = "new-code"
    if change == "missing_source": altered["sources"].pop()
    if change == "duplicate_source": altered["sources"].append(altered["sources"][0])
    if change == "failed_detail": altered["details"][-1]["ok"] = False
    if change == "global_cli": altered["isolation"]["global_cli_used"] = True
    if change == "old_daemon": altered["isolation"]["project_daemon_started"] = False
    if change == "unproven_isolation": altered["isolation"]["external_modules_blocked"] = False
    with pytest.raises(RuntimeError):
        release.validate_report(altered)


def test_unverified_legacy_evidence_requires_new_live_run(report):
    report.pop("fingerprint_version")
    with pytest.raises(RuntimeError, match="npm run test:collection:live"):
        release.validate_report(report)


def test_version_and_unrelated_dependencies_reuse_record(report, input_changes, tmp_path, monkeypatch):
    package = json.loads((verify.ROOT / "package.json").read_bytes())
    lock = json.loads((verify.ROOT / "package-lock.json").read_bytes())
    version = package["version"]
    package["version"] = lock["version"] = lock["packages"][""]["version"] = "99.0.0"
    package["dependencies"]["unrelated-sdk"] = "2.0.0"
    lock["packages"][""]["dependencies"]["unrelated-sdk"] = "2.0.0"
    lock["packages"]["node_modules/unrelated-sdk"] = {"version": "2.0.0"}
    input_changes["package.json"] = json.dumps(package).encode()
    input_changes["package-lock.json"] = json.dumps(lock).encode()
    input_changes["static/index.html"] = b"<title>new UI</title>"
    input_changes["requirements.txt"] = (verify.ROOT / "requirements.txt").read_bytes() + b"\nFlask-Extra==1.0\n"

    latest = report["runtime"]["version"]
    report.update({"newsreader_version": version, "upstream": {"latest": latest}, "offline_checks_passed": True})
    path = tmp_path / "release.json"
    original_record = json.dumps(report).encode()
    path.write_bytes(original_record)
    monkeypatch.setattr(release, "RECORD", path)
    monkeypatch.setattr(release, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps({"version": latest}).encode()))
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: pytest.fail("cached verification started subprocesses"))
    monkeypatch.setattr(sys, "argv", ["check_opencli_release.py"])
    assert release.main() == 0
    assert path.read_bytes() == original_record


def test_model_preference_change_reuses_collection_record(report, input_changes):
    settings = (verify.ROOT / "settings.py").read_bytes()
    input_changes["settings.py"] = settings + b"\n\ndef normalize_feature_models(value, settings):\n    return {}\n"
    release.validate_report(report)


@pytest.mark.parametrize("name", [".npmrc", "collection/sources.json", "collection/opencli-manifest.json",
                                  "collection/adapters/rss-feed.js", "scripts/opencli-isolation-guard.mjs"])
def test_runtime_input_changes_require_new_live_run(report, input_changes, name):
    input_changes[name] = (verify.ROOT / name).read_bytes() + b"\n"
    with pytest.raises(RuntimeError, match="npm run test:collection:live"):
        release.validate_report(report)


def test_node_and_python_requirements_require_new_live_run(report, input_changes):
    package = json.loads((verify.ROOT / "package.json").read_bytes())
    package["engines"]["node"] = ">=99.0.0"
    input_changes["package.json"] = json.dumps(package).encode()
    with pytest.raises(RuntimeError, match="运行环境要求"):
        release.validate_report(report)
    input_changes.pop("package.json")
    requirements = (verify.ROOT / "requirements.txt").read_bytes()
    requirements = b"\n".join(line for line in requirements.splitlines() if not line.startswith(b"Flask=="))
    input_changes["requirements.txt"] = requirements + b"\nFlask==99.0.0\n"
    with pytest.raises(RuntimeError, match="运行环境要求"):
        release.validate_report(report)


def test_live_report_fingerprint_uses_supplied_samples(report, input_changes):
    sample_bytes = (verify.ROOT / verify.SAMPLE_FILE).read_bytes()
    assert verify.content_fingerprint(sample_bytes=sample_bytes) == report["fingerprint"]
    samples = json.loads(sample_bytes)
    samples[0]["required_text"] = ["another observation"]
    changed_samples = json.dumps(samples).encode()
    changed_report = {**report, "fingerprint": verify.content_fingerprint(sample_bytes=changed_samples)}
    with pytest.raises(RuntimeError, match="样本"):
        release.validate_report(changed_report)
    input_changes[verify.SAMPLE_FILE] = changed_samples
    with pytest.raises(RuntimeError, match="样本"):
        release.validate_report(report)
    release.validate_report(changed_report)


def test_lock_paths_follow_hoisted_nested_and_conditional_packages():
    name = "@jackwener/opencli"
    root = f"node_modules/{name}"
    nested = f"{root}/node_modules"
    lock = {"lockfileVersion": 3, "packages": {
        "": {"dependencies": {name: "1.0.0"}},
        root: {"version": "1.0.0", "dependencies": {"nested": "1"}, "optionalDependencies": {"conditional": "1"},
               "peerDependencies": {"required-peer": "1", "optional-peer": "1"},
               "peerDependenciesMeta": {"optional-peer": {"optional": True}}},
        f"{nested}/nested": {"version": "1.0.0", "dependencies": {"shared": "1"}},
        f"{nested}/shared": {"version": "1.0.0", "dependencies": {"hoisted": "1"}},
        f"{nested}/conditional": {"version": "1.0.0", "optional": True, "os": ["darwin"]},
        "node_modules/conditional": {"version": "1.0.1"},
        "node_modules/nested": {"version": "1.0.1"},
        "node_modules/shared": {"version": "1.0.1"},
        "node_modules/hoisted": {"version": "1.0.0"},
        "node_modules/required-peer": {"version": "1.0.0"},
    }}
    resolved = verify.opencli_dependency_lock(lock, name, "1.0.0")
    assert set(resolved) == {root, f"{nested}/nested", f"{nested}/shared", f"{nested}/conditional",
                             "node_modules/conditional", "node_modules/hoisted", "node_modules/required-peer"}
    lock["packages"]["node_modules/optional-peer"] = {"version": "1.0.0"}
    assert "node_modules/optional-peer" in verify.opencli_dependency_lock(lock, name, "1.0.0")
    del lock["packages"]["node_modules/required-peer"]
    with pytest.raises(RuntimeError, match="required-peer"):
        verify.opencli_dependency_lock(lock, name, "1.0.0")


def test_npm_lock_change_only_matters_for_resolved_dependencies(report, input_changes):
    lock = json.loads((verify.ROOT / "package-lock.json").read_bytes())
    entries = lock["packages"]
    opencli = "node_modules/@jackwener/opencli"
    dependency = "verification-test-package"
    installed = f"{opencli}/node_modules/{dependency}"
    unused = f"node_modules/{dependency}"
    entries[opencli].setdefault("dependencies", {})[dependency] = "1.0.0"
    entries[installed] = {"version": "1.0.0", "integrity": "original-integrity"}
    entries[unused] = {"version": "2.0.0", "integrity": "unused-integrity"}
    input_changes["package-lock.json"] = json.dumps(lock).encode()
    report["fingerprint"] = verify.content_fingerprint()

    entries[unused]["integrity"] = "unrelated-integrity"
    input_changes["package-lock.json"] = json.dumps(lock).encode()
    release.validate_report(report)
    entries[installed]["integrity"] = "new-integrity"
    input_changes["package-lock.json"] = json.dumps(lock).encode()
    with pytest.raises(RuntimeError, match="OpenCLI 锁定依赖"):
        release.validate_report(report)


def test_new_upstream_blocks_push_until_explicitly_reviewed(report, tmp_path, monkeypatch):
    path = tmp_path / "release.json"
    report.update({"upstream": {"latest": "2.0.3"}, "offline_checks_passed": True})
    path.write_text(json.dumps(report))
    monkeypatch.setattr(release, "RECORD", path)
    monkeypatch.setattr(release, "urlopen", lambda *a, **kw: io.BytesIO(b'{"version":"2.0.4"}'))
    monkeypatch.setattr(sys, "argv", ["check_opencli_release.py"])
    with pytest.raises(RuntimeError, match="上游版本"):
        release.main()


def test_retained_version_requires_reason_and_offline_tests(report, tmp_path, monkeypatch):
    incoming = tmp_path / "live.json"; incoming.write_text(json.dumps(report))
    record = tmp_path / "release.json"
    monkeypatch.setattr(release, "RECORD", record)
    monkeypatch.setattr(release, "urlopen", lambda *a, **kw: io.BytesIO(b'{"version":"2.0.4"}'))
    monkeypatch.setattr(sys, "argv", ["check_opencli_release.py", "--record", str(incoming)])
    calls = []
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(RuntimeError, match="保留"):
        release.main()
    assert not record.exists() and not calls
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--retain-reason", "候选版本待验证，保留完整验证通过的版本"])
    assert release.main() == 0
    assert len(calls) == 3
    saved = json.loads(record.read_text())
    assert saved["offline_checks_passed"] and saved["upstream"]["latest"] == "2.0.4"
