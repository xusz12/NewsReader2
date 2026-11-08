from datetime import date, timedelta
import json
from pathlib import Path
import threading

import pytest

import periodic_reports as pr
import trading_calendar as tc


SOURCE = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
# Official 2026 dates; substituting a year below creates a synthetic fixture,
# never a prediction of that year's holidays.
HOLIDAYS = [
    ("元旦", "1月1日（星期四）至1月3日（星期六）"),
    ("春节", "2月15日（星期日）至2月23日（星期一）"),
    ("清明节", "4月4日至4月6日"), ("劳动节", "5月1日至5月5日"),
    ("端午节", "6月19日至6月21日"), ("中秋节", "9月25日至9月27日"),
    ("国庆节", "10月1日至10月7日"),
]


def notice(year=2026, holidays=None):
    body = "".join(f"<p>（一）{name}：{dates}休市。另外，2月14日（星期六）为周末休市。</p>"
                   for name, dates in (HOLIDAYS if holidays is None else holidays))
    return (f'<h2>站点导航</h2><div class="article-infor"><h2>关于上海证券交易所{year}年部分节假日休市安排的通知</h2>'
            f'<div class="allZoom">{body}</div></div><h2>相关通知</h2>')


def listing(*years):
    return "".join(f'<a href="/disclosure/announcement/general/c/{year}.shtml">'
                   f'关于上海证券交易所{year}年部分节假日休市安排的通知</a>' for year in years)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWS_READER_TRADING_CALENDAR_PATH", raising=False)
    monkeypatch.setenv("NEWS_READER_DB_PATH", str(tmp_path / "news.sqlite3"))
    return tc.cache_path()


def updater(*years):
    calls = []
    def fetch(url):
        calls.append(url)
        if url == tc.LIST_URL:
            return listing(*years)
        return notice(int(Path(url).stem))
    return tc.Service(fetch=fetch), calls


def test_only_complete_annual_official_links_are_selected():
    html = listing(2026) + '<a title="关于2026年国庆节休市安排的公告" href="/disclosure/holiday.shtml">国庆</a>'
    html += '<a href="https://example.com/disclosure/a" title="关于上海证券交易所2027年部分节假日休市安排的通知">链接</a>'
    html += listing(2025)
    assert list(tc.annual_links(html)) == [2026, 2025]


def test_official_2026_ranges_match_bundled_calendar():
    expected = json.loads(tc.BUNDLED_PATH.read_text())["2026"]["closed_ranges"]
    assert tc.parse_annual(notice(), 2026, SOURCE)["closed_ranges"] == expected


def test_single_day_cross_month_and_combined_holidays_2025():
    holidays = [("元旦", "1月1日"), ("春节", "1月28日至2月4日"), ("清明节", "4月4日至4月6日"),
                ("劳动节", "5月1日至5月5日"), ("端午节", "5月31日至6月2日"),
                ("国庆节、中秋节", "10月1日至10月8日")]
    ranges = tc.parse_annual(notice(2025, holidays), 2025, SOURCE)["closed_ranges"]
    assert ranges == [["2025-01-01", "2025-01-01"], ["2025-01-28", "2025-02-04"],
                      ["2025-04-04", "2025-04-06"], ["2025-05-01", "2025-05-05"],
                      ["2025-05-31", "2025-06-02"], ["2025-10-01", "2025-10-08"]]


@pytest.mark.parametrize("new_year", ["2025年12月31日至2026年1月2日", "12月31日至1月2日"])
def test_new_year_range_also_closes_previous_december(new_year):
    value = tc.parse_annual(notice(2026, [("元旦", new_year), *HOLIDAYS[1:]]), 2026, SOURCE)
    assert value["closed_ranges"][0] == ["2025-12-31", "2026-01-02"]
    calendar = {"2025": {"closed_ranges": []}, "2026": value}
    assert pr.trading_day(date(2025, 12, 31), calendar) is False
    assert pr.trading_day(date(2026, 1, 5), calendar) is True


@pytest.mark.parametrize("html,year", [
    (notice(2026, HOLIDAYS[:-1]), 2026), (notice(2025), 2026),
    (notice().replace("2月23日", "2月30日"), 2026),
    (notice().replace("2月23日", "4月23日"), 2026),
    (notice().replace("4月4日至4月6日", "5月1日至5月5日"), 2026),
    (notice().replace("6月19日至6月21日", "另行通知"), 2026),
    (notice(2026, [*HOLIDAYS, HOLIDAYS[0]]), 2026),
    (notice().replace('class="allZoom"', 'class="changed-layout"'), 2026),
])
def test_reject_incomplete_or_unrecognized_notices(html, year):
    with pytest.raises(ValueError):
        tc.parse_annual(html, year, SOURCE)


def test_update_prefetch_persists_and_readers_never_fetch(cache, monkeypatch):
    service, calls = updater(2026, 2027)
    at = pr.local("2026-11-08T12:00:00+08:00")
    assert service.check_once(at)
    assert len(calls) == 3
    saved = json.loads(cache.read_text())
    assert set(saved["years"]) == {"2026", "2027"}
    assert not saved["last_error"]
    monkeypatch.setattr(tc, "fetch_page", lambda _: pytest.fail("Read must be local"))
    assert pr.calendar_data()["2027"] == saved["years"]["2027"]
    assert not service.check_once(at + timedelta(days=6, hours=23))
    restarted, restarted_calls = updater(2026, 2027)
    assert not restarted.check_once(at + timedelta(days=6, hours=23))
    assert not restarted_calls
    assert restarted.check_once(at + timedelta(days=7))
    assert len(restarted_calls) == 3


def test_unpublished_next_year_is_not_an_error_or_invented(cache):
    service, _ = updater(2026)
    at = pr.local("2026-11-08T12:00:00+08:00")
    service.check_once(at)
    assert "2027" not in tc.calendar_data()
    state = json.loads(cache.read_text())
    assert state["last_error"] == ""
    assert state["next_check_at"] == (at + timedelta(days=7)).isoformat()


def test_missing_current_year_retries_hourly_without_guessing(cache):
    service, calls = updater(2026)
    at = pr.local("2027-01-04T09:00:00+08:00")
    assert service.check_once(at)
    assert json.loads(cache.read_text())["last_error"] == "calendar_not_published"
    assert pr.trading_day(at.date(), tc.calendar_data()) is None
    assert not service.check_once(at + timedelta(minutes=59))
    assert len(calls) == 1
    assert service.check_once(at + timedelta(hours=1))


@pytest.mark.parametrize("restart", [False, True])
def test_december_shortens_weekly_wait_until_next_year_is_cached(cache, restart):
    service, _ = updater(2026)
    at = pr.local("2026-11-30T12:00:00+08:00")
    assert service.check_once(at)
    if restart:
        service, _ = updater(2026)
    assert not service.check_once(at + timedelta(hours=23))
    assert service.check_once(at + timedelta(days=1))
    state = json.loads(cache.read_text())
    assert state["next_check_at"] == (at + timedelta(days=2)).isoformat()
    service.fetch = updater(2026, 2027)[0].fetch
    assert service.check_once(at + timedelta(days=2))
    state = json.loads(cache.read_text())
    assert state["next_check_at"] == (at + timedelta(days=9)).isoformat()
    assert not service.check_once(at + timedelta(days=3))


def test_missing_current_year_network_failure_retries_hourly(cache):
    calls = []
    def fail(url):
        calls.append(url)
        raise OSError("offline")
    service = tc.Service(fail)
    at = pr.local("2027-01-04T09:00:00+08:00")
    assert service.check_once(at)
    assert json.loads(cache.read_text())["next_check_at"] == (at + timedelta(hours=1)).isoformat()
    assert not service.check_once(at + timedelta(minutes=59))
    assert service.check_once(at + timedelta(hours=1))
    assert len(calls) == 2


@pytest.mark.parametrize("restart", [False, True])
def test_missing_current_cache_year_is_checked_immediately(cache, restart):
    service, _ = updater(2027)
    at = pr.local("2027-02-04T09:00:00+08:00")
    assert service.check_once(at)
    state = json.loads(cache.read_text())
    state["years"].pop("2027")
    cache.write_text(json.dumps(state))
    if restart:
        service, _ = updater(2027)
    assert service.check_once(at + timedelta(minutes=1))
    assert "2027" in tc.calendar_data()


def test_malformed_check_metadata_does_not_prevent_update(cache):
    cache.write_text(json.dumps({"version": 1, "years": {}, "last_checked_at": 123, "next_check_at": []}))
    service, calls = updater(2026)
    assert service.check_once(pr.local("2026-11-08T12:00:00+08:00"))
    assert len(calls) == 2


def test_rollover_checks_new_year_without_waiting_yesterday_interval(cache):
    service, calls = updater(2026)
    service.check_once(pr.local("2026-12-31T23:59:00+08:00"))
    assert service.check_once(pr.local("2027-01-01T00:00:00+08:00"))
    assert len(calls) == 3


def test_network_failure_preserves_last_good_calendar_and_retries(cache):
    service, calls = updater(2026, 2027)
    at = pr.local("2026-11-08T12:00:00+08:00")
    service.check_once(at)
    previous = tc.calendar_data()
    def fail(_):
        raise OSError("offline")
    service.fetch = fail
    assert service.check_once(at + timedelta(days=7))
    assert tc.calendar_data() == previous
    state = json.loads(cache.read_text())
    assert state["last_error"] == "calendar_update_failed"
    assert state["last_success_at"] == at.isoformat()
    service.fetch = lambda _: pytest.fail("Must back off")
    assert not service.check_once(at + timedelta(days=13, hours=23))


def test_partial_notice_does_not_replace_valid_year(cache):
    service, _ = updater(2026, 2027)
    at = pr.local("2026-11-08T12:00:00+08:00")
    service.check_once(at)
    previous = tc.calendar_data()
    service.fetch = lambda url: listing(2026, 2027) if url == tc.LIST_URL else notice(int(Path(url).stem), HOLIDAYS[:1])
    assert service.check_once(at + timedelta(days=7))
    assert tc.calendar_data() == previous
    assert json.loads(cache.read_text())["last_error"] == "calendar_update_failed"


def test_atomic_write_failure_keeps_file_and_does_not_busy_retry(cache, monkeypatch):
    service, calls = updater(2026)
    at = pr.local("2026-11-08T12:00:00+08:00")
    service.check_once(at)
    original = cache.read_bytes()
    def fail(*_):
        raise OSError("disk full")
    monkeypatch.setattr(tc.os, "replace", fail)
    assert not service.check_once(at + timedelta(days=7))
    assert cache.read_bytes() == original
    assert not list(cache.parent.glob(".trading-calendar-*"))
    previous_calls = len(calls)
    assert not service.check_once(at + timedelta(days=13, hours=23))
    assert len(calls) == previous_calls


def test_first_cache_write_failure_also_backs_off(cache, monkeypatch):
    service, calls = updater(2026)
    monkeypatch.setattr(tc, "write_cache", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    at = pr.local("2026-11-08T12:00:00+08:00")
    assert not service.check_once(at)
    assert not service.check_once(at + timedelta(minutes=1))
    assert len(calls) == 2


@pytest.mark.parametrize("contents", ["broken", "[]", '{"version":1,"years":{"2027":{"closed_ranges":[["bad","bad"]]}}}'])
def test_corrupt_cache_falls_back_to_bundled(contents, cache):
    cache.write_text(contents)
    assert set(tc.calendar_data()) == {"2026"}


def test_manual_override_is_authoritative_and_never_overwritten(cache, monkeypatch):
    override = cache.parent / "custom.json"
    override.write_text('{"2028":{"closed_ranges":[]}}')
    monkeypatch.setenv("NEWS_READER_TRADING_CALENDAR_PATH", str(override))
    service = tc.Service(fetch=lambda _: pytest.fail("Manual override must not fetch"))
    service.start()
    assert service.thread is None
    assert not service.check_once()
    assert tc.calendar_data() == {"2028": {"closed_ranges": []}}
    assert not cache.exists()


def test_slow_network_does_not_block_start_or_local_read(cache):
    entered, release = threading.Event(), threading.Event()
    def slow(url):
        entered.set()
        assert release.wait(5)
        return listing(2026) if url == tc.LIST_URL else notice()
    service = tc.Service(slow)
    try:
        service.start()
        assert entered.wait(3)
        assert "2026" in tc.calendar_data()
        service.start()
        assert service.thread.is_alive()
    finally:
        release.set()
        service.stop()


def test_missing_year_keeps_nontrading_schedules_running(cache):
    at = date(2027, 2, 1)  # Monday and month boundary; fixture has no 2027 calendar.
    assert {kind for kind, *_ in pr.slots(at, tc.calendar_data())} == {"evening", "weekly", "monthly"}
