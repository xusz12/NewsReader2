"""Shared A-share calendar: local reads, independently scheduled official updates."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import certifi
from settings import resolve_db_path

TZ = ZoneInfo("Asia/Shanghai")
BUNDLED_PATH = Path(__file__).resolve().parent / "reports/trading-calendar.json"
LIST_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/list/"
CHECK_INTERVAL = timedelta(days=7)
YEAR_END_INTERVAL = timedelta(days=1)
RETRY_INTERVAL = timedelta(hours=1)
MAX_PAGE_BYTES = 1_000_000
ANNUAL_TITLE = re.compile(r"关于上海证券交易所(\d{4})年(?:部分)?节假日休市安排的通知")
HOLIDAYS = {"元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"}
HOLIDAY = "(?:" + "|".join(sorted(HOLIDAYS)) + ")"
DATE = r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"


def now():
    return datetime.now(TZ)


def override_path():
    value = os.environ.get("NEWS_READER_TRADING_CALENDAR_PATH", "").strip()
    return Path(value).expanduser() if value else None


def cache_path():
    return resolve_db_path().parent / "trading-calendar-cache.json"


def validate_year(year, value):
    if not re.fullmatch(r"20\d{2}", str(year)) or not isinstance(value, dict):
        raise ValueError("calendar_invalid_year")
    ranges = value.get("closed_ranges")
    if not isinstance(ranges, list):
        raise ValueError("calendar_invalid_ranges")
    for pair in ranges:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("calendar_invalid_range")
        start, end = map(date.fromisoformat, pair)
        if start > end or end.year != int(year) or start.year not in (int(year)-1, int(year)):
            raise ValueError("calendar_invalid_range")
    return value


def read_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("years"), dict):
            return {}
        # One damaged record must not discard the other verified years.
        years = {}
        for year, value in data["years"].items():
            try:
                if not 6 <= len(value.get("closed_ranges", [])) <= 7:
                    continue
                years[year] = validate_year(year, value)
            except (ValueError, TypeError, AttributeError):
                continue
        return {**data, "years": years}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def calendar_data():
    """Never fetch or start a worker from a read/API/scheduler call."""
    override = override_path()
    if override:
        data = json.loads(override.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("calendar_invalid_file")
        return {year: validate_year(year, value) for year, value in data.items()}
    try:
        bundled = json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))
        bundled = {year: validate_year(year, value) for year, value in bundled.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        bundled = {}
    return {**bundled, **read_cache(cache_path()).get("years", {})}


def check_interval(at, calendar):
    if str(at.year) not in calendar:
        return RETRY_INTERVAL
    if at.month == 12 and str(at.year + 1) not in calendar:
        return YEAR_END_INTERVAL
    return CHECK_INTERVAL


def still_waiting(at, interval, checked, deadline):
    try:
        checked = datetime.fromisoformat(checked) if isinstance(checked, str) else checked
        deadline = datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline
        if not isinstance(checked, datetime) or not isinstance(deadline, datetime) or checked.tzinfo is None or deadline.tzinfo is None:
            return False
        # Re-evaluate the interval in December and when a cached year disappears.
        # A new year or a backwards clock jump triggers a fresh check.
        return checked.astimezone(TZ).year == at.year and checked <= at < min(deadline, checked + interval)
    except (ValueError, TypeError):
        return False


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links, self.body, self.heading = [], [], []
        self.anchor = None
        self.body_depth = 0
        self.article_depth = 0
        self.in_heading = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = [attrs.get("href", ""), attrs.get("title", ""), []]
        if tag == "h2" and self.article_depth:
            self.in_heading = True
        if tag == "div":
            if self.article_depth:
                self.article_depth += 1
            elif "article-infor" in attrs.get("class", "").split():
                self.article_depth = 1
            if self.body_depth:
                self.body_depth += 1
            elif "allZoom" in attrs.get("class", "").split():
                self.body_depth = 1

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            href, title, text = self.anchor
            self.links.append((href, title or "".join(text)))
            self.anchor = None
        if tag == "h2":
            self.in_heading = False
        if tag == "div" and self.body_depth:
            self.body_depth -= 1
        if tag == "div" and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, value):
        if self.anchor:
            self.anchor[2].append(value)
        if self.body_depth:
            self.body.append(value)
        if self.in_heading:
            self.heading.append(value)


def official_url(value):
    url = urlsplit(value)
    return (url.scheme == "https" and url.netloc == "www.sse.com.cn"
            and url.path.startswith("/disclosure/"))


def fetch_page(url):
    if not official_url(url):
        raise ValueError("calendar_invalid_source")
    request = Request(url, headers={"User-Agent": "NewsReader/1.0 (trading-calendar)",
                                   "Accept": "text/html", "Accept-Encoding": "identity"})
    with urlopen(request, timeout=12, context=ssl.create_default_context(cafile=certifi.where())) as response:
        if not official_url(response.url):
            raise ValueError("calendar_invalid_source")
        data = response.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES:
            raise ValueError("calendar_page_too_large")
        return data.decode("utf-8")


def annual_links(html):
    links = {}
    for href, title in Page(html).links:
        match = ANNUAL_TITLE.fullmatch(re.sub(r"\s+", "", title))
        url = urljoin(LIST_URL, href)
        if match and official_url(url):
            # The exchange lists the most recent publication first.
            links.setdefault(int(match[1]), url)
    return links


def parse_annual(html, year, source):
    page = Page(html)
    heading = ANNUAL_TITLE.fullmatch(re.sub(r"\s+", "", "".join(page.heading)))
    if not heading or int(heading[1]) != year or not official_url(source):
        raise ValueError("calendar_invalid_notice")
    text = re.sub(r"\s+", "", "".join(page.body))
    text = re.sub(r"[（(][^()（）]*[）)]", "", text)
    pattern = rf"({HOLIDAY}(?:[、及和]{HOLIDAY})*)[：:]([^。；]+?)休市"
    seen, ranges = set(), []
    for match in re.finditer(pattern, text):
        labels = set(re.findall(HOLIDAY, match[1]))
        dates = re.fullmatch(DATE + r"(?:至" + DATE + r")?", match[2])
        if not dates or seen & labels:
            raise ValueError("calendar_invalid_holiday")
        sy, sm, sd, ey, em, ed = dates.groups()
        if em is None:
            ey, em, ed = sy, sm, sd
        # New Year's closure can start in the preceding December.
        start_year = int(sy) if sy else year - (int(sm) == 12 and int(em) == 1)
        start = date(start_year, int(sm), int(sd))
        end = date(int(ey or year), int(em), int(ed))
        if not 0 <= (end-start).days <= 16 or end.year != year:
            raise ValueError("calendar_invalid_holiday")
        ranges.append([start.isoformat(), end.isoformat()])
        seen.update(labels)
    if seen != HOLIDAYS or not 6 <= len(ranges) <= 7:
        raise ValueError("calendar_incomplete_notice")
    ranges.sort()
    if any(left[1] >= right[0] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("calendar_overlapping_ranges")
    return validate_year(str(year), {"source": source, "closed_ranges": ranges})


def write_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".trading-calendar-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class Service:
    def __init__(self, fetch=fetch_page):
        self.fetch = fetch
        self.lock = threading.Lock()
        self.check_lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None
        self.next_attempt = None
        self.path = None
        self.last_attempt = None
        self.missing_current = None

    def start(self):
        with self.lock:
            if override_path() or (self.thread and self.thread.is_alive()):
                return
            self.stopped.clear()
            self.thread = threading.Thread(target=self.loop, name="trading-calendar-update", daemon=True)
            self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)

    def check_once(self, at=None):
        at = (at or now()).astimezone(TZ)
        if override_path():
            return False
        with self.check_lock:
            path = cache_path()
            cached = read_cache(path)
            if self.path != path:
                self.next_attempt = None
                self.last_attempt = None
                self.missing_current = None
                self.path = path
            available = calendar_data()
            interval = check_interval(at, available)
            missing = str(at.year) not in available
            lost_calendar = missing and (self.missing_current is False
                                         or (self.last_attempt is None and not cached.get("last_error")))
            self.missing_current = missing
            if not lost_calendar and (still_waiting(at, interval, self.last_attempt, self.next_attempt)
                    or still_waiting(at, interval, cached.get("last_checked_at"), cached.get("next_check_at"))):
                return False
            self.last_attempt = at
            years = dict(cached.get("years", {}))
            error = ""
            try:
                links = annual_links(self.fetch(LIST_URL))
                if not links:
                    raise ValueError("calendar_annual_links_missing")
                for year in (at.year, at.year+1):
                    if year not in links:
                        continue  # Next year's announcement may not be published yet.
                    try:
                        years[str(year)] = parse_annual(self.fetch(links[year]), year, links[year])
                    except Exception:
                        error = "calendar_update_failed"
                if str(at.year) not in years and str(at.year) not in available:
                    error = error or "calendar_not_published"
            except Exception:
                error = "calendar_update_failed"
            self.next_attempt = at + check_interval(at, {**available, **years})
            state = {"version": 1, "years": years, "last_checked_at": at.isoformat(),
                     "last_success_at": cached.get("last_success_at") if error else at.isoformat(),
                     "last_error": error, "next_check_at": self.next_attempt.isoformat()}
            try:
                write_cache(path, state)
            except OSError:
                self.next_attempt = at + interval
                logging.getLogger(__name__).warning("Trading calendar cache write failed; keeping the previous calendar")
                return False
            self.missing_current = str(at.year) not in {**available, **years}
            if error:
                logging.getLogger(__name__).warning("Trading calendar update deferred: %s", error)
            return True

    def loop(self):
        while not self.stopped.is_set():
            try:
                self.check_once()
            except Exception:
                logging.getLogger(__name__).exception("Trading calendar update failed")
            self.stopped.wait(60)


SERVICE = Service()
