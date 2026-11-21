"""Plain-code checks on model output. No AI.
check_shape: exact JSON shape.  grounded / has_word: is this text really in the resume?
unsupported: does a rewritten line say more than its source line?"""
import re
from difflib import SequenceMatcher

# Common rephrasing terms. A rewritten line may use them if they appear anywhere in the resume.
GENERIC = {"ai", "llm", "llms", "api", "apis"}
_SKIP = {"e.g", "i.e"}
_TOKEN = re.compile(r"\.?[A-Za-z0-9](?:[A-Za-z0-9.#+'-]*[A-Za-z0-9#+])?")
_STARTS = set(".!?:;([\"'*•-–—")


def check_shape(v, schema, path="$"):
    """Raise ValueError unless v matches schema exactly. Never coerces or repairs."""
    t = schema["type"]
    if t == "object":
        if not isinstance(v, dict):
            raise ValueError(f"{path}: not an object")
        if set(v) != set(schema["properties"]):
            raise ValueError(f"{path}: wrong keys {sorted(v)}")
        for k, s in schema["properties"].items():
            check_shape(v[k], s, f"{path}.{k}")
    elif t == "array":
        if not isinstance(v, list):
            raise ValueError(f"{path}: not a list")
        for i, x in enumerate(v):
            check_shape(x, schema["items"], f"{path}[{i}]")
    elif t == "string" and not isinstance(v, str):
        raise ValueError(f"{path}: not a string")
    return v


def norm(s):
    s = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", s or "")      # PDF hyphenation at a line end
    return " ".join(re.sub(r"[^a-z0-9#+]+", " ", s.lower()).split())


def has_word(term, text):
    t = norm(term)
    return bool(t) and f" {t} " in f" {norm(text)} "


def _lines(text):
    return [x for x in (norm(p) for p in re.split(r"[\n•▪●◦·|]+", text or "")) if x]


def grounded(snippet, text, ratio=0.9):
    """True if snippet is in text word for word, or nearly so inside 1-4 adjacent lines (PDF noise).
    Two real fragments from different places do not pass."""
    a = norm(snippet)
    if not a:
        return False
    if f" {a} " in f" {norm(text)} ":
        return True
    if len(a) < 25:
        return False
    lines = _lines(text)
    for i in range(len(lines)):
        for k in range(1, 5):
            if i + k > len(lines):
                break
            w = " ".join(lines[i:i + k])
            if not 0.75 * len(a) <= len(w) <= 1.35 * len(a):
                continue
            m = SequenceMatcher(None, a, w, autojunk=False)
            if m.real_quick_ratio() >= ratio and m.quick_ratio() >= ratio and m.ratio() >= ratio:
                return True
    return False


def numbers(s):
    return set(re.findall(r"\d+(?:[.,]\d+)*", s or ""))


def terms(text):
    """(token, strong) for tool- or skill-like tokens.
    strong: has digits, # + . inside, or capitals after the first letter (AWS, C#, .NET, n8n, GPT-4, LangGraph).
    weak: a capitalised word that does not start a sentence or a line."""
    text = text or ""
    out = []
    for m in _TOKEN.finditer(text):
        tok = m.group()
        if tok.lower() in _SKIP or not any(c.isalpha() for c in tok):
            continue
        j, newline = m.start() - 1, False
        while j >= 0 and text[j].isspace():
            newline = newline or text[j] == "\n"
            j -= 1
        starts = j < 0 or newline or text[j] in _STARTS
        strong = (any(c.isdigit() for c in tok) or any(c in "#+." for c in tok)
                  or any(c.isupper() for c in tok[1:]))
        if strong:
            out.append((tok, True))
        elif tok[0].isupper() and not starts:
            out.append((tok, False))
    return out


def unsupported(new, src, resume_text, job_terms=()):
    """Reasons a rewritten line says more than its source. [] means it passes.
    Numbers and strong terms must be in src (the source line). Weak and GENERIC terms must be in the resume.
    A strong term from the job text must be in src, even when written in lower case."""
    if not new.strip():
        return ["empty"]
    why = []
    if len(new) > 2 * len(src) + 80:
        why.append("much longer than the source")
    extra = sorted(numbers(new) - numbers(src))
    if extra:
        why.append("new numbers: " + ", ".join(extra))

    def ok(tok, strong=True):
        return has_word(tok, src if strong and norm(tok) not in GENERIC else resume_text)

    bad = {tok for tok, strong in terms(new) if not ok(tok, strong)}
    bad |= {jt for jt in job_terms if has_word(jt, new) and not ok(jt)}
    if bad:
        why.append("not in the source: " + ", ".join(sorted(bad)))
    return why
