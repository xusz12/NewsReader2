from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path: Path, monkeypatch):
    # 默认隔离 app_settings：指向不存在的路径 → load_app_settings 返回默认设置。
    # 避免测试读到本机运行态 app_settings.json（可能被设成 provider=pi）而分叉。
    # 需要 provider=pi 的测试在自己的 body 里 setenv NEWS_READER_APP_SETTINGS_PATH 覆盖。
    monkeypatch.setenv("NEWS_READER_APP_SETTINGS_PATH", str(tmp_path / "absent-app_settings.json"))
