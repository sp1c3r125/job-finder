"""Show stored UTC times in the timezone from profile.yaml (e.g. "UTC+8"), as <time datetime="..."> elements."""
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

import yaml

_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)


def parse_zone(text):
    """(tzinfo, label) from "UTC+8", "+08:00", "UTC" or an IANA name. Anything unreadable gives UTC."""
    s = str(text or "").strip()
    m = _OFFSET.match(s)
    if m:
        delta = timedelta(hours=int(m[2]), minutes=int(m[3] or 0))
        return timezone(delta if m[1] == "+" else -delta), s
    if s and s.upper() not in ("UTC", "GMT", "Z"):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(s), s
        except Exception:                       # unknown name, or no tz database on this machine
            pass
    return timezone.utc, "UTC"


def load_zone(profile_path):
    """The `timezone:` value of profile.yaml; UTC when the file or the key is missing."""
    try:
        data = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    return parse_zone(data.get("timezone") if isinstance(data, dict) else None)


def to_local(iso, zone):
    """A timezone-aware datetime in `zone`, or None when `iso` is empty or unreadable. Date-only text counts as UTC midnight."""
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone[0])


def time_html(iso, zone, with_time=True):
    """<time datetime="local ISO">2026-11-22 09:02 UTC+8</time>; the date alone when with_time is false. "" when unreadable."""
    dt = to_local(iso, zone)
    if dt is None:
        return ""
    shown = dt.strftime("%Y-%m-%d %H:%M") + f" {zone[1]}" if with_time else dt.strftime("%Y-%m-%d")
    return f'<time datetime="{escape(dt.isoformat(timespec="seconds"))}">{escape(shown)}</time>'
