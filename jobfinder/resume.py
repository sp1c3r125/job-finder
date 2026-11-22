"""Resume import. Read the file, let the model copy out the facts, keep only facts found in the resume text.
The resume and everything made from it stay local (git-ignored)."""
import copy
import json
from datetime import datetime, timezone
import os
from pathlib import Path

from . import target as target_mod
from .checks import check_shape, grounded, has_word

RESUME_JSON = "resume.json"
DROP_DIR = "resume"
EXTS = (".pdf", ".docx", ".txt", ".md")
FACT_KEYS = ("name", "contact", "summary", "skills", "roles", "education", "certifications", "languages")

_S = {"type": "string"}
_SL = {"type": "array", "items": _S}
_ROLE = {"type": "object", "properties": {"title": _S, "company": _S, "dates": _S, "location": _S, "bullets": _SL},
         "required": ["title", "company", "dates", "location", "bullets"], "additionalProperties": False}
_FIELDS = {"name": _S, "contact": _SL, "summary": _S, "skills": _SL, "roles": {"type": "array", "items": _ROLE},
           "education": _SL, "certifications": _SL, "languages": _SL, "search_keywords": _SL,
           "target": target_mod.TARGET_SCHEMA}
RESUME_SCHEMA = {"type": "object", "properties": _FIELDS, "required": list(_FIELDS), "additionalProperties": False}

SYSTEM = """Copy the facts out of one resume. Copy text EXACTLY as written: do not fix, shorten, merge, translate or add.
name: the person's name. contact: email, phone, city, links, one item each. summary: the summary or profile
paragraph, or "". skills: each skill or tool name as written. roles: every job, newest first, with bullets copied
one by one. education, certifications, languages: one item each, as written. Use "" or [] when the resume has none.
search_keywords: 3 to 8 short job-search terms (1 to 3 words) for the roles this person fits, such as job titles
or core skills. Base them only on the resume.
target: the job-search profile, made only from this resume:
"""
SYSTEM += target_mod.TARGET_PROMPT.split("\n", 1)[1]


def read_resume(path):
    """Plain text from a PDF, DOCX, TXT or MD resume. ValueError if the type is wrong or there is no text."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages)
    elif ext == ".docx":
        import docx
        d = docx.Document(str(p))
        parts = [para.text for s in d.sections for para in s.header.paragraphs]
        parts += [para.text for para in d.paragraphs]
        parts += [cell.text for t in d.tables for row in t.rows for cell in row.cells]
        text = "\n".join(parts)
    elif ext in (".txt", ".md"):
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"unsupported file type {ext!r}: use PDF, DOCX, TXT or MD")
    if len(text.split()) < 50:
        raise ValueError("almost no text found. A scanned PDF has no text layer: export the resume as a text PDF or DOCX")
    return text


def verify(raw, text):
    """Keep only what is found in the resume text. Returns (facts, dropped)."""
    dropped = []

    def keep(kind, items, test=grounded):
        out, seen = [], set()
        for x in (i.strip() for i in items):
            if not x or x.lower() in seen:
                continue
            if test(x, text):
                out.append(x)
                seen.add(x.lower())
            else:
                dropped.append(f"{kind}: {x}")
        return out

    def one(kind, x):
        x = x.strip()
        if x and not grounded(x, text):
            dropped.append(f"{kind}: {x}")
            return ""
        return x

    roles = []
    for r in raw["roles"]:
        title, company = r["title"].strip(), r["company"].strip()
        if not title or not grounded(title, text) or (company and not grounded(company, text)):
            dropped.append(f"role: {title} at {company} (title or employer not found)")
            continue
        roles.append({"title": title, "company": company, "dates": one("dates", r["dates"]),
                      "location": one("location", r["location"]), "bullets": keep(f"bullet ({title})", r["bullets"])})
    facts = {"name": one("name", raw["name"]), "contact": keep("contact", raw["contact"]),
             "summary": one("summary", raw["summary"]), "skills": keep("skill", raw["skills"], has_word),
             "roles": roles, "education": keep("education", raw["education"]),
             "certifications": keep("certification", raw["certifications"]),
             "languages": keep("language", raw["languages"], has_word),
             # search terms are not claims, so they are not checked against the text
             "search_keywords": [k.strip() for k in raw["search_keywords"] if 0 < len(k.strip()) <= 40][:8]}
    try:                                           # a search profile is not a claim either: no grounding check
        facts["target"] = target_mod.clean(raw["target"])
    except ValueError as e:
        dropped.append(f"target: {e}")
    return facts, dropped


def import_resume(backend, path, log, out=RESUME_JSON):
    """Read, extract (one try plus one retry, never repaired), verify, save. Returns (facts, dropped) or None."""
    text = read_resume(path)
    user = f"RESUME:\n{text[:20000]}"
    for attempt in (1, 2):
        try:
            raw = check_shape(backend.complete(SYSTEM, user, schema=RESUME_SCHEMA, tool_name="submit_resume",
                                               max_tokens=8000), RESUME_SCHEMA)
            break
        except Exception as e:  # noqa: BLE001 - log every failure kind
            log("resume_extract_failed", None, attempt=attempt, error=f"{type(e).__name__}: {e}"[:300])
    else:
        return None
    facts, dropped = verify(raw, text)
    facts.update(source_file=Path(path).name, text=text, left_out=dropped,
                 imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    Path(out).write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    log("resume_imported", None, file=Path(path).name, roles=len(facts["roles"]), skills=len(facts["skills"]),
        search_keywords=facts["search_keywords"], target=facts.get("target"), dropped=dropped)
    return facts, dropped


def load_resume(path=RESUME_JSON):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def newest_drop(drop_dir=DROP_DIR):
    d = Path(drop_dir)
    if not d.is_dir():
        return None
    files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in EXTS and not f.name.startswith("~$")
             and f.name.lower() != "readme.md"]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def import_if_new(get_backend, log, drop_dir=DROP_DIR, out=RESUME_JSON):
    """Import the newest file in resume/ if it is newer than resume.json. Returns import_resume's result or None."""
    f = newest_drop(drop_dir)
    if f is None or (os.path.exists(out) and os.path.getmtime(out) >= f.stat().st_mtime):
        return None
    try:
        got = import_resume(get_backend(), f, log, out)
    except ValueError as e:
        log("resume_read_failed", None, file=f.name, error=str(e))
        print(f"could not read {f}: {e}")
        return None
    if got:
        print(f"imported {f.name}: {len(got[0]['roles'])} roles, {len(got[0]['skills'])} skills, "
              f"{len(got[1])} items left out (see logs/decisions.jsonl)")
    return got


def profile_facts(res):
    """The verified facts for the scoring prompt: no raw text, no search terms."""
    return {k: res[k] for k in FACT_KEYS if res.get(k)}


def with_resume_keywords(cfg, res):
    """Copy of cfg with the resume's search keywords added to `keywords` (case-insensitive, capped)."""
    if not res or not cfg.get("use_resume_keywords", True):
        return cfg
    cfg = copy.deepcopy(cfg)
    have = {k.lower() for k in cfg["keywords"]}
    for k in res.get("search_keywords", [])[: cfg.get("max_resume_keywords", 6)]:
        if k.lower() not in have:
            cfg["keywords"].append(k)
            have.add(k.lower())
    return cfg
