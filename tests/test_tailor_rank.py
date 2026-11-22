"""tailor_mode rank: no model call, no rewording; bullets, groups and skills ordered by overlap with the job's words."""
from jobfinder import tailor
from .conftest import FakeBackend, make_job

B = ["Answered customer emails every day.", "Built n8n workflows that sync invoices into Postgres.",
     "Wrote Python agents that call the Claude API."]
RES = {"name": "Alex Example", "contact": ["alex@example.com"], "education": [], "certifications": [], "languages": [],
       "summary": "Automation developer.", "skills": ["Excel", "Python", "n8n", "Postgres"],
       "text": " ".join(B + ["Weekly report.", "Shipped Claude API agents."]),
       "roles": [{"title": "Automation Developer", "company": "Example Co.", "dates": "2023 - Present", "location": "Cebu",
                  "bullets": B,
                  "groups": [{"heading": "Support desk", "bullets": ["Weekly report."]},
                             {"heading": "Agent project", "bullets": ["Shipped Claude API agents."]}]}]}
JOB = make_job(title="AI Automation Engineer", description="Build Python agents with the Claude API and n8n workflows.")


def test_rank_orders_by_overlap_and_never_calls_the_model():
    b = FakeBackend([])
    logs = []
    tailored, info = tailor.tailor_resume(b, RES, JOB, lambda *a, **k: logs.append(a), mode="rank")
    assert b.calls == []
    bullets = tailored["roles"][0]["bullets"]
    assert bullets[0] == B[2] and bullets[1] == B[1] and bullets[2] == B[0]           # most shared words first
    assert tailored["skills"][:2] == ["Python", "n8n"] and sorted(tailored["skills"]) == sorted(RES["skills"])
    assert [g["heading"] for g in tailored["roles"][0]["groups"]] == ["Agent project", "Support desk"]
    assert info["gaps"] == [] and info["reverts"] == [] and "rank mode" in info["changes"][0]


def test_rank_never_rewords_adds_or_drops_a_line():
    tailored, _ = tailor.rank_resume(RES, JOB)
    role = tailored["roles"][0]
    assert sorted(role["bullets"]) == sorted(B)
    assert sorted(b for g in role["groups"] for b in g["bullets"]) == ["Shipped Claude API agents.", "Weekly report."]
    assert tailored["summary"] == RES["summary"] and role["title"] == "Automation Developer"


def test_rank_is_stable_when_nothing_overlaps():
    job = make_job(title="Pastry Chef", description="Bake bread.")
    tailored, _ = tailor.rank_resume(RES, job)
    assert tailored["roles"][0]["bullets"] == B and tailored["skills"] == RES["skills"]


def test_rewrite_mode_still_calls_the_model():
    out = {"summary": "", "skills": [], "gaps": [], "changes": [],
           "roles": [{"role_id": "r0", "bullets": [{"source_id": "r0b2", "text": B[2]}], "groups": []}]}
    b = FakeBackend([out])
    assert tailor.tailor_resume(b, RES, JOB, lambda *a, **k: None, mode="rewrite") is not None
    assert len(b.calls) == 1
