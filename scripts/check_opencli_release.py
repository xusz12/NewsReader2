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
from verify_collection_runtime import FINGERPRINT_VERSION, ROOT, SAMPLE_FILE, content_fingerprint

RECORD = ROOT / "collection/opencli-release.json"
RETEST = "请运行 npm run test:collection:live 重新验收。"


def complete_rows(rows, field, expected):
    return (isinstance(rows, list) and len(rows) == len(expected)
            and all(isinstance(row, dict) and isinstance(row.get(field), str) and row.get("ok") is True for row in rows)
            and {row[field] for row in rows} == expected)


def validate_report(report):
    if report.get("fingerprint_version") != FINGERPRINT_VERSION:
        raise RuntimeError(f"采集实测记录缺少当前格式的指纹。{RETEST}")
    if report.get("fingerprint") != content_fingerprint():
        raise RuntimeError(f"采集或正文代码、来源、样本、运行环境要求或 OpenCLI 锁定依赖已变化。{RETEST}")
    manifest = json.loads((ROOT / "collection/opencli-manifest.json").read_text())
    sources = json.loads((ROOT / "collection/sources.json").read_text())
    samples = json.loads((ROOT / SAMPLE_FILE).read_text())
    names = {"Reuters", "Bloomberg", "TechCrunch", "Ars Technica", *(row["name"] for row in samples)}
    runtime = report.get("runtime")
    isolation = report.get("isolation")
    if (report.get("ok") is not True
            or not isinstance(runtime, dict) or runtime.get("isolated") is not True
            or runtime.get("version") != manifest["version"]
            or runtime.get("adapter_commit") != manifest["adapters"]["commit"]
            or runtime.get("adapter_version") != manifest["adapters"]["version"]
            or not complete_rows(report.get("sources"), "section", {row["section"] for row in sources})
            or not complete_rows(report.get("details"), "name", names)
            or not isinstance(isolation, dict) or isolation.get("external_modules_blocked") is not True
            or isolation.get("global_cli_used") is not False or isolation.get("project_daemon_started") is not True):
        raise RuntimeError(f"采集实测未全部通过，或缺少完整的来源、正文与隔离记录。{RETEST}")


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
        validate_report(report)
        report["upstream"] = {"latest": latest, "checked_at": datetime.now(timezone.utc).isoformat(),
                              "retained_reason": args.retain_reason.strip()}
        report["offline_checks_passed"] = True
        RECORD.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        upstream = report.get("upstream", {})
        if (latest != upstream.get("latest") or report.get("offline_checks_passed") is not True
                or (latest != manifest["version"] and not upstream.get("retained_reason", "").strip())):
            raise RuntimeError(f"上游版本或验证记录已变化（当前 {latest}），请重新评估并记录后再推送。")
    print(f"OpenCLI 发布检查通过：项目 {manifest['version']}，上游稳定版 {latest}，全部采集和正文测试记录匹配。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"OpenCLI 发布检查失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
