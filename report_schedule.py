"""Independent Beijing-time execution schedules and news windows, with history."""
from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TIMES = {"morning": "08:30", "noon": "14:30", "evening": "21:30", "weekly": "08:30", "monthly": "08:30"}
# Legacy clients use these defaults; current windows are independently editable.
WINDOW_TIMES = {"morning": "08:30", "noon": "14:30", "evening": "21:30"}
DEFAULT_WINDOWS = {
    "morning": {"start_day": -1, "start": "21:30", "end": "08:30"},
    "noon": {"start_day": 0, "start": "08:30", "end": "14:30"},
    "evening": {"start_day": -1, "start": "21:30", "end": "21:30"},
    "weekly": {"start": "00:00", "end": "00:00"},
    "monthly": {"start": "00:00", "end": "00:00"},
}
FREQUENCIES = {"morning": "交易日", "noon": "交易日", "evening": "每天", "weekly": "每周一", "monthly": "每月 1 日"}


def normalize(value, defaults=None):
    if not isinstance(value, dict) or any(key not in DEFAULT_TIMES for key in value):
        raise ValueError("invalid_report_schedule")
    times = {**(defaults or DEFAULT_TIMES), **value}
    if any(not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in times.values()):
        raise ValueError("invalid_report_schedule")
    if not times["morning"] < times["noon"] < times["evening"]:
        raise ValueError("invalid_report_schedule_order")
    return times


def normalize_windows(value, defaults=None):
    if not isinstance(value, dict) or any(key not in DEFAULT_WINDOWS for key in value):
        raise ValueError("invalid_report_windows")
    result = {kind: dict(window) for kind, window in (defaults or DEFAULT_WINDOWS).items()}
    for kind, window in value.items():
        if not isinstance(window, dict) or any(key not in DEFAULT_WINDOWS[kind] for key in window):
            raise ValueError("invalid_report_windows")
        result[kind].update(window)
    for kind, window in result.items():
        if any(not isinstance(window[key], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", window[key]) for key in ("start", "end")):
            raise ValueError("invalid_report_windows")
        if kind in WINDOW_TIMES:
            if type(window["start_day"]) is not int or window["start_day"] not in (-1, 0):
                raise ValueError("invalid_report_windows")
            if window["start_day"] == 0 and window["start"] >= window["end"]:
                raise ValueError("invalid_report_window_order")
    return result


def validate_windows(times, windows=None):
    if any(times[kind] < window["end"] for kind, window in (windows or DEFAULT_WINDOWS).items()):
        raise ValueError("invalid_report_schedule_window")


def news_window(day, kind, windows=None):
    """Day is the edition end date; week/month start on their calendar boundary."""
    window = (windows or DEFAULT_WINDOWS)[kind]
    end = at_time(day, window["end"])
    start_day = ((day-timedelta(days=1)).replace(day=1) if kind == "monthly" else
                 day-timedelta(days=7) if kind == "weekly" else day+timedelta(days=window["start_day"]))
    start = at_time(start_day, window["start"])
    return start, end


def window_label(kind, window):
    start, end = (("上周一", "本周一") if kind == "weekly" else ("上月 1 日", "本月 1 日") if kind == "monthly"
                  else ("前日" if window["start_day"] == -1 else "当日", "当日"))
    return f'{start} {window["start"]}—{end+" " if start != end else ""}{window["end"]}'


def _history(value, current, normalizer, field, error):
    if not value:
        return [{"effective_at": None, field: normalizer(current)}]
    if not isinstance(value, list):
        raise ValueError(error)
    result = []
    previous = None
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise ValueError(error)
        since = row.get("effective_at")
        if index == 0:
            if since is not None:
                raise ValueError(error)
        else:
            try:
                parsed = datetime.fromisoformat(since).astimezone(TZ)
            except (TypeError, ValueError):
                raise ValueError(error) from None
            since = parsed.isoformat(timespec="microseconds")
            if previous and since <= previous:
                raise ValueError(error)
            previous = since
        result.append({"effective_at": since, field: normalizer(row.get(field))})
    if result[-1][field] != current:
        raise ValueError(error)
    return result


def history(value, times):
    return _history(value, times, normalize, "times", "invalid_report_schedule_history")


def window_history(value, windows):
    return _history(value, windows, normalize_windows, "windows", "invalid_report_window_history")


def changed_history(settings, times, at):
    old = normalize(settings.get("report_schedule", {}))
    revisions = history(settings.get("report_schedule_history"), old)
    return _changed_history(revisions, "times", old, times, at)


def changed_window_history(settings, windows, at):
    old = normalize_windows(settings.get("report_windows", {}))
    revisions = window_history(settings.get("report_window_history"), old)
    return _changed_history(revisions, "windows", old, windows, at)


def _changed_history(revisions, field, old, value, at):
    if old == value:
        return revisions
    since = at.astimezone(TZ).isoformat(timespec="microseconds")
    # A backwards wall-clock adjustment must not invalidate saved settings.
    revisions = [row for row in revisions if row["effective_at"] is None or row["effective_at"] < since]
    return [*revisions, {"effective_at": since, field: value}]


def at_time(day, hhmm):
    return datetime.combine(day, time.fromisoformat(hhmm), TZ)


class Schedule:
    def __init__(self, settings=None):
        settings = settings or {}
        self.times = normalize(settings.get("report_schedule", {}))
        self.revisions = history(settings.get("report_schedule_history"), self.times)
        self.windows = normalize_windows(settings.get("report_windows", {}))
        self.window_revisions = window_history(settings.get("report_window_history"), self.windows)

    def window(self, day, kind, scheduled):
        for row in reversed(self.window_revisions):
            if row["effective_at"] is None or scheduled >= datetime.fromisoformat(row["effective_at"]):
                return news_window(day, kind, row["windows"])

    def occurrence(self, day, kind):
        # Only the first valid occurrence belongs to a dated edition. Moving
        # today's time later after it was due must not create a second edition.
        for index, row in enumerate(self.revisions):
            moment = at_time(day, row["times"][kind])
            since = row["effective_at"]
            until = self.revisions[index+1]["effective_at"] if index+1 < len(self.revisions) else None
            if (since is None or moment >= datetime.fromisoformat(since)) and (until is None or moment < datetime.fromisoformat(until)):
                return moment, row["times"]
        return None
