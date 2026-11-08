"""Native collection schedule settings, independent of worker imports."""
import re

DEFAULT_SCHEDULE = {"enabled": True, "daily": ["00:01", "04:00", "08:00", "12:00", "18:00", "21:00"],
                    "trading": ["09:00", "10:00", "11:00", "13:00", "14:30"], "translation_concurrency": 4}

def normalize_schedule(value, defaults=None):
    result = {**DEFAULT_SCHEDULE, **(defaults or {})}
    if not isinstance(value, dict) or set(value) - set(result):
        raise ValueError("collection_schedule_invalid")
    result.update(value)
    if type(result["enabled"]) is not bool:
        raise ValueError("collection_schedule_invalid")
    if type(result["translation_concurrency"]) is not int or not 1 <= result["translation_concurrency"] <= 32:
        raise ValueError("collection_translation_concurrency_invalid")
    for key in ("daily", "trading"):
        values = result[key]
        if not isinstance(values, list) or len(values) > 48 or any(
                not isinstance(v, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) for v in values):
            raise ValueError("collection_schedule_invalid")
        result[key] = sorted(set(values))
    if result["enabled"] and not (result["daily"] or result["trading"]):
        raise ValueError("collection_schedule_empty")
    return result
