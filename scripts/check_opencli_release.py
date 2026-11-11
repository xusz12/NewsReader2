"""Check upstream on every push; record only fully tested runtime candidates."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import sys
import ssl
import certifi
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_collection_runtime import ROOT, content_fingerprint

RECORD = ROOT / "collection/opencli-release.json"


def validate_report(report):
    manifest = json.loads((ROOT / "collection/opencli-manifest.json").read_text())
    sources = json.loads((ROOT / "collection/sources.json").read_text())
    samples = json.loads((ROOT / "collection/validation-samples.json").read_text())
    names = {"Reuters", "Bloomberg", "TechCrunch", "Ars Technica", *(row["name"] for row in samples)}
    if (report.get("ok") is not True or report.get("fingerprint") != content_fingerprint()
            or report.get("runtime", {}).get("version") != manifest["version"]
            or report.get("runtime", {}).get("adapter_commit") != manifest["adapters"]["commit"]
            or report.get("runtime", {}).get("adapter_version") != manifest["adapters"]["version"]
            or {r["section"] for r in report.get("sources", [])} != {r["section"] for r in sources}
            or {r["name"] for r in report.get("details", [])} != names
            or not all(r.get("ok") is True for group in ("sources", "details") for r in report[group])
            or report.get("isolation", {}).get("external_modules_blocked") is not True
            or report.get("isolation", {}).get("global_cli_used") is not False
            or report.get("isolation", {}).get("project_daemon_started") is not True):
        raise RuntimeError("运行时或来源已变化，或实测未全部通过。请重新运行 verify_collection_runtime.py --restart-daemon。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, metavar="LIVE_REPORT", help="record a passing full live run after offline checks")
    parser.add_argument("--retain-reason", default="", help="why a newer upstream version is not adopted yet")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "collection/opencli-manifest.json").read_text())
    request = Request("https://registry.npmjs.org/@jackwener%2fopencli/latest", headers={"User-Agent": "NewsReader-release-check"})
    with urlopen(request, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as response:
        latest = json.load(response)["version"]
    report = json.loads((args.record or RECORD).read_text())
    validate_report(report)
    if args.record:
        if latest != manifest["version"] and not args.retain_reason.strip():
            raise RuntimeError(f"上游已有 {latest}；验证候选版本，或明确记录保留 {manifest['version']} 的原因。")
        for command in (["npm", "run", "test:adapters"], ["npm", "run", "test:opencli"],
                        [sys.executable, "-m", "pytest", "-q", "tests/test_opencli_runtime.py", "tests/test_collection_environment.py",
                         "tests/test_opencli_details.py", "tests/test_news_collection.py", "tests/test_dependency_update.py", "tests/test_updater.py", "tests/test_opencli_release.py"]):
            subprocess.run(command, cwd=ROOT, check=True)
        report["upstream"] = {"latest": latest, "checked_at": datetime.now(timezone.utc).isoformat(),
                              "retained_reason": args.retain_reason.strip()}
        report["offline_checks_passed"] = True
        RECORD.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    elif latest != report.get("upstream", {}).get("latest") or report.get("offline_checks_passed") is not True:
        raise RuntimeError(f"上游版本或验证记录已变化（当前 {latest}）；重新评估并记录后再推送。")
    print(f"OpenCLI 发布检查通过：项目 {manifest['version']}，上游稳定版 {latest}，全部采集和正文测试记录匹配。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"OpenCLI 发布检查失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
