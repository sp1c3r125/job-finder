import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from jobfinder import db, main, resume
from jobfinder.checks import grounded, terms, unsupported
from jobfinder.resume_docx import write_tailored
from jobfinder.scoring import load_profile
from jobfinder.sources import ashby
from jobfinder.tailor import assemble, tailor_resume
from .conftest import GOOD, FakeBackend, FakeHttp, make_job, recent

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "samples" / "resume.sample.md"
TEXT = SAMPLE.read_text(encoding="utf-8")
B = [l[2:] for l in TEXT.splitlines() if l.startswith("- ")]      # the five real bullets, in order

EXTRACTED = {
    "name": "Alex Example",
    "contact": ["alex@example.com", "Cebu, Philippines", "linkedin.com/in/made-up"],     # last one is invented
    "summary": "Automation developer who builds workflow tools and small LLM apps for small businesses.",
    "skills": ["Python", "SQL", "n8n", "Zapier", "REST APIs", "Kubernetes"],               # Kubernetes is invented
    "roles": [
        {"title": "Automation Developer", "company": "Example Agency Co.", "dates": "Mar 2023 - Present",
         "location": "Cebu", "bullets": B[:3] + ["Led a team of 12 engineers."]},     # last bullet is invented
        {"title": "Virtual Assistant", "company": "Freelance", "dates": "Jan 2021 - Feb 2023", "location": "",
         "bullets": B[3:]},
        {"title": "Head of AI", "company": "Big Corp", "dates": "2019", "location": "", "bullets": ["Ran AI."]},
    ],
    "education": ["BS Information Technology, University of Example, 2020"],
    "certifications": [], "languages": ["English", "Filipino"],
    "search_keywords": ["automation developer", "n8n", "LLM apps"],
    "target": {"titles": ["Automation Developer"], "title_words": ["developer", "automation"],
               "exclude_words": ["intern"], "level": "mid"},
}
JOB_TEXT = "We need Kubernetes, LangGraph and AWS. Build LLM agents with n8n."
TAILORED = {
    "summary": "Automation developer who builds n8n workflows and small LLM apps.",
    "skills": ["n8n", "Python", "Kubernetes"],
    "roles": [{"role_id": "r0", "bullets": [
        {"source_id": "r0b1", "text": "Built an LLM email-sorting tool that routes 300 emails a day."},
        {"source_id": "r0b0", "text": "Built an invoice workflow in Python and n8n on Kubernetes, saving 10 hours a week."},
        {"source_id": "r9b9", "text": "Invented bullet."}]}],
    "gaps": ["Kubernetes", "LangGraph"],
    "changes": ["Moved the LLM email tool to the top."],
}


@pytest.fixture
def facts(tmp_path, logs):
    return resume.import_resume(FakeBackend([EXTRACTED]), SAMPLE, logs[1], out=tmp_path / "resume.json")[0]


# --- import ---------------------------------------------------------------------------------------------

def test_import_keeps_only_facts_found_in_resume(tmp_path, logs):
    events, log = logs
    out = tmp_path / "resume.json"
    facts, dropped = resume.import_resume(FakeBackend([EXTRACTED]), SAMPLE, log, out=out)
    assert "Kubernetes" not in facts["skills"] and "n8n" in facts["skills"]
    assert [r["title"] for r in facts["roles"]] == ["Automation Developer", "Virtual Assistant"]
    assert facts["roles"][0]["bullets"] == B[:3]
    assert facts["contact"] == ["alex@example.com", "Cebu, Philippines"]
    assert len(dropped) == 4 and any("Big Corp" in d for d in dropped)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["name"] == "Alex Example" and saved["text"] == TEXT
    assert events[-1][0] == "resume_imported"


def test_import_retries_once_and_never_repairs(tmp_path, logs):
    events, log = logs
    bad = dict(EXTRACTED, skills="Python")                     # wrong type: not coerced
    assert resume.import_resume(FakeBackend([bad, bad]), SAMPLE, log, out=tmp_path / "r.json") is None
    assert [e for e, _ in events] == ["resume_extract_failed"] * 2 and not (tmp_path / "r.json").exists()


def test_read_txt_docx_pdf_and_bad_files(tmp_path):
    import docx
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "alex@example.com"
    for line in TEXT.splitlines():
        d.add_paragraph(line)
    d.save(tmp_path / "r.docx")
    assert "alex@example.com" in resume.read_resume(tmp_path / "r.docx")
    assert B[0] in resume.read_resume(SAMPLE)

    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    c = canvas.Canvas(str(tmp_path / "r.pdf"))
    for i, line in enumerate(TEXT.splitlines()):
        c.drawString(40, 800 - 14 * i, line)
    c.save()
    assert grounded(B[0], resume.read_resume(tmp_path / "r.pdf"))

    (tmp_path / "short.txt").write_text("Alex Example, Python")
    with pytest.raises(ValueError, match="almost no text"):
        resume.read_resume(tmp_path / "short.txt")
    with pytest.raises(ValueError, match="unsupported"):
        resume.read_resume(tmp_path / "r.rtf")


def test_grounded_allows_pdf_noise_but_not_stitched_fragments():
    noisy = TEXT.replace("workflow in Python", "work flow in Python")
    assert grounded(B[0], noisy)
    assert not grounded("Built an invoice-processing workflow that routes 300 emails a day.", TEXT)


# --- search -----------------------------------------------------------------------------------------------

def test_resume_keywords_added_capped_and_deduped():
    cfg = {"keywords": ["AI", "automation"], "max_resume_keywords": 2}
    res = {"search_keywords": ["Automation", "n8n", "LLM apps"]}
    assert resume.with_resume_keywords(cfg, res)["keywords"] == ["AI", "automation", "n8n"]
    assert cfg["keywords"] == ["AI", "automation"]                                # input not changed
    assert resume.with_resume_keywords(dict(cfg, use_resume_keywords=False), res)["keywords"] == ["AI", "automation"]
    assert resume.with_resume_keywords(cfg, None) is cfg


def test_profile_uses_verified_resume_facts(tmp_path, monkeypatch, facts):
    monkeypatch.chdir(tmp_path)
    Path("resume.json").unlink()
    with pytest.raises(SystemExit):
        load_profile()
    Path("resume.json").write_text(json.dumps(facts), encoding="utf-8")
    p = load_profile()
    assert "Example Agency Co." in p and "search_keywords" not in p and "alex@example.com" in p
    assert "Kubernetes" not in p and B[3] in p and "STATUS" not in p
    Path("profile.yaml").write_text("timezone: UTC+8\nconstraints: [No night shift]\n")
    p = load_profile()
    assert "No night shift" in p and "Example Agency Co." in p


# --- tailoring guardrails -----------------------------------------------------------------------------------

def test_tailor_keeps_facts_and_reverts_invented_claims(facts):
    t, info = assemble(facts, copy.deepcopy(TAILORED), JOB_TEXT)
    r0, r1 = t["roles"]
    assert r0["bullets"] == [TAILORED["roles"][0]["bullets"][0]["text"], B[0]]    # reworded line kept, bad one reverted
    assert (r0["title"], r0["company"], r0["dates"]) == ("Automation Developer", "Example Agency Co.", "Mar 2023 - Present")
    assert r1["bullets"] == B[3:]                                                  # role left out by the model: kept whole
    assert t["skills"] == ["n8n", "Python", "SQL", "Zapier", "REST APIs"]      # chosen first, none dropped
    assert t["summary"] == TAILORED["summary"]
    rv = " | ".join(info["reverts"])
    assert "r0b0: kept original wording" in rv and "10" in rv and "Kubernetes" in rv
    assert "r9b9" in rv and "skill not in resume, removed: Kubernetes" in rv and "r1: no usable bullets" in rv
    assert info["gaps"] == ["Kubernetes", "LangGraph"]


def test_tailor_reverts_invented_summary_and_lowercase_job_terms(facts):
    out = copy.deepcopy(TAILORED)
    out["summary"] = "Automation developer with 10 years of AWS experience."
    out["roles"] = [{"role_id": "r0", "bullets": [{"source_id": "r0b0", "text": B[0].replace("n8n", "n8n and langgraph")}]}]
    t, info = assemble(facts, out, JOB_TEXT)
    assert t["summary"] == facts["summary"] and t["roles"][0]["bullets"] == [B[0]]
    assert any("summary: kept original" in r for r in info["reverts"])
    assert any("LangGraph" in r for r in info["reverts"])


def test_terms_and_unsupported_rules():
    found = dict(terms("Built tools in Python with AWS, C#, .NET and n8n. Then wrote docs."))
    assert found == {"Python": False, "AWS": True, "C#": True, ".NET": True, "n8n": True}
    src = "Wrote an internal email-sorting tool that calls an LLM API and routes 300 emails a day."
    assert unsupported("Built an AI email tool that routes 300 emails a day.", src, TEXT + " AI") == []
    assert unsupported("Built an email tool on AWS.", src, TEXT)
    assert unsupported("", src, TEXT) == ["empty"]


def test_tailor_retry_and_files(tmp_path, logs, facts):
    events, log = logs
    job = make_job(description=JOB_TEXT)
    got = tailor_resume(FakeBackend([ValueError("junk"), copy.deepcopy(TAILORED)]), facts, job, log)
    assert got and [e for e, _ in events][-2:] == ["tailor_failed", "resume_tailored"]
    path = Path(write_tailored(*got, job, tmp_path / "resumes"))
    import docx
    body = "\n".join(p.text for p in docx.Document(path).paragraphs)
    assert "Alex Example" in body and B[0] in body and "Kubernetes" not in body and "STATUS" not in body
    notes = path.with_name(path.stem + ".changes.md").read_text(encoding="utf-8")
    assert "## Gaps" in notes and "- LangGraph" in notes and "kept original wording" in notes and "Nothing was sent" in notes


# --- daily run and commands -----------------------------------------------------------------------------------

def test_old_db_gets_score_table(tmp_path):
    # resume_path moved from jobs to job_scores in the M1 schema split (see tests/test_db.py for the data migration)
    con = sqlite3.connect(tmp_path / "old.db")
    con.execute("CREATE TABLE jobs (source TEXT, source_id TEXT, PRIMARY KEY (source, source_id))")
    con.close()
    got = db.connect(str(tmp_path / "old.db"))
    assert {r[1] for r in got.execute("PRAGMA table_info(job_scores)")} >= {"score_json", "draft_path", "resume_path"}


def test_daily_run_reads_dropped_resume_then_tailors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copy(ROOT / "config.yaml", ".")                     # no profile.yaml: the resume is the profile
    Path("companies.yaml").write_text("companies:\n  - {name: Acme, source: ashby, board: acme}\n")
    Path("resume").mkdir()
    shutil.copy(SAMPLE, "resume/my-resume.md")
    job = {"id": "1", "title": "AI Automation Engineer", "jobUrl": "https://a/1", "publishedAt": recent(),
           "location": "Remote - Worldwide", "workplaceType": "Remote", "descriptionPlain": JOB_TEXT}
    http = FakeHttp({ashby.URL.format(board="acme"): {"jobs": [job]}})
    b = FakeBackend([EXTRACTED, GOOD, {"note": "I build n8n workflows. Gap: no Kubernetes."}, copy.deepcopy(TAILORED)])
    assert main.run(["--db", "t.db"], backend=b, http=http) == 0
    assert Path("resume.json").exists() and "Example Agency Co." in b.calls[1][1]     # scoring saw resume facts
    assert len(list(Path("resumes").glob("*.docx"))) == 1 and len(list(Path("resumes").glob("*.changes.md"))) == 1
    assert 'href="resumes/' in Path("dashboard.html").read_text(encoding="utf-8")
    events = [json.loads(l) for l in Path("logs/decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    kw = next(e for e in events if e["event"] == "keywords")["keywords"]
    assert "n8n" in kw and "LLM apps" in kw
    assert {"resume_imported", "resume_tailored", "scored"} <= {e["event"] for e in events}

    b2 = FakeBackend([])                                       # resume.json is newer: no new import, no new jobs
    main.run(["--db", "t.db"], backend=b2, http=http)
    assert b2.calls == []

    assert main.run(["tailor", "https://a/1", "--db", "t.db"], backend=FakeBackend([copy.deepcopy(TAILORED)])) == 0
    assert main.run(["tailor", "https://nope", "--db", "t.db"], backend=FakeBackend([])) == 1
    assert main.run(["import-resume", "resume/my-resume.md"], backend=FakeBackend([EXTRACTED])) == 0
    assert main.run(["import-resume", "missing.pdf"], backend=FakeBackend([])) == 1
