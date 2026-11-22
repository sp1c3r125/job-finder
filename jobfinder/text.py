import html
import re
from datetime import datetime, timezone


def html_to_text(s):
    s = html.unescape(s or "")          # Greenhouse content is escaped HTML
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_PLACEHOLDER = re.compile(r"%[A-Z][A-Z0-9_]*%")


def clean_text(s):
    """Tidy a job description: no %UPPER_SNAKE% template placeholders, no leading quote/angle fragments,
    no empty lines, no repeated lines (first one stays), no trailing separators such as ' - '."""
    out, seen = [], set()
    for line in re.split(r"[\r\n]+", s or ""):
        line = _PLACEHOLDER.sub(" ", line)
        line = re.sub(r"^[\s\"'>]+", "", line)                          # leftovers like '">' from a broken tag
        line = re.sub(r"[\s\-–—|:;,/]+$", "", line)           # trailing separators
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if not line or line.casefold() in seen:
            continue
        seen.add(line.casefold())
        out.append(line)
    return "\n".join(out)


def to_iso(value):
    """ISO string, unix seconds or millis -> ISO 8601 UTC; '' if unknown."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        if value > 1e11:
            value /= 1000
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
