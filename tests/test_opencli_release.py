from copy import deepcopy
import io
import json
import sys

import pytest

from scripts import check_opencli_release as release


@pytest.fixture
def report(monkeypatch):
    monkeypatch.setattr(release, "content_fingerprint", lambda: "tested-code")
    root = release.ROOT
    manifest = json.loads((root / "collection/opencli-manifest.json").read_text())
    sources = json.loads((root / "collection/sources.json").read_text())
    cases = json.loads((root / "collection/validation-samples.json").read_text())
    return {"ok": True, "fingerprint": "tested-code", "runtime": {
                "version": manifest["version"], "adapter_commit": manifest["adapters"]["commit"], "adapter_version": manifest["adapters"]["version"]},
            "sources": [{"section": s["section"], "ok": True} for s in sources],
            "details": [{"name": name, "ok": True} for name in
                        ["Reuters", "Bloomberg", "TechCrunch", "Ars Technica", *(r["name"] for r in cases)]],
            "isolation": {"external_modules_blocked": True, "global_cli_used": False, "project_daemon_started": True}}


def test_complete_matching_run_is_accepted(report):
    release.validate_report(report)


@pytest.mark.parametrize("change", ["changed_code", "missing_source", "failed_detail", "global_cli", "old_daemon", "unproven_isolation"])
def test_incomplete_or_stale_evidence_blocks_release(report, change):
    altered = deepcopy(report)
    if change == "changed_code": altered["fingerprint"] = "new-code"
    if change == "missing_source": altered["sources"].pop()
    if change == "failed_detail": altered["details"][-1]["ok"] = False
    if change == "global_cli": altered["isolation"]["global_cli_used"] = True
    if change == "old_daemon": altered["isolation"]["project_daemon_started"] = False
    if change == "unproven_isolation": altered["isolation"]["external_modules_blocked"] = False
    with pytest.raises(RuntimeError):
        release.validate_report(altered)


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
