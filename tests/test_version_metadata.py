from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "v0.0.8"
ASSET_VERSION = RELEASE_VERSION.removeprefix("v")


def test_release_version_is_consistent_across_metadata_and_ui():
    manifest = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    index_source = (ROOT / "static/index.html").read_text(encoding="utf-8")
    readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog_source = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert manifest["version"] == RELEASE_VERSION
    assert f"<title>News Reader {RELEASE_VERSION}</title>" in index_source
    assert f"/static/style.css?v={ASSET_VERSION}" in index_source
    assert f"<span class=\"brand-version\">{RELEASE_VERSION}</span>" in index_source
    assert f"/static/app.js?v={ASSET_VERSION}" in index_source
    assert f"当前稳定版本：`{RELEASE_VERSION}`" in readme_source
    assert f"### 2026-10-18 — {RELEASE_VERSION} " in changelog_source


def test_independent_package_and_update_metadata():
    manifest = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert manifest["repository"] == "https://github.com/xusz12/NewsReader2.git"
    assert package["name"] == lock["name"] == lock["packages"][""]["name"] == "newsreader-workbench"
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == ASSET_VERSION
