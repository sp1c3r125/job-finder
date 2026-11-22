from jobfinder import eval as ev
from .conftest import GOOD

RES = {"name": "Alex Example", "contact": ["alex@example.com"], "summary": "Automation developer.",
       "skills": ["Python", "n8n"], "education": [], "certifications": [], "languages": [],
       "roles": [{"title": "Developer", "company": "Example Co", "dates": "2023", "location": "Cebu",
                  "bullets": ["Built an n8n workflow in Python that cut manual entry from 6 hours to 1 hour."]}],
       "text": "Automation developer. Built an n8n workflow in Python that cut manual entry from 6 hours to 1 hour."}


class Model:
    """Answers each prompt kind. `bullet` is what the tailor step returns for the one role bullet."""
    def __init__(self, bullet=None, note="Short note.", score=GOOD):
        self.bullet, self.note, self.score = bullet or RES["roles"][0]["bullets"][0], note, score

    def complete(self, system, user, schema=None, tool_name=None, max_tokens=None):
        if tool_name == "submit_note":
            return {"note": self.note}
        if tool_name == "submit_resume":
            return {"summary": "", "skills": ["Python", "n8n"], "gaps": ["No Kubernetes"], "changes": [],
                    "roles": [{"role_id": "r0", "bullets": [{"source_id": "r0b0", "text": self.bullet}]}]}
        return self.score


def test_fake_jobs_are_20_and_invented():
    jobs = ev.load_jobs()
    assert len(jobs) == 20
    assert all(j.company.startswith("Fake Company") and j.url.startswith("https://example.invalid") for j in jobs)


def test_clean_model_passes_every_rule():
    events = []
    m = ev.evaluate(Model(), RES, ev.load_jobs(), 70, events)
    assert m["scored"] == 20 and m["eligible"] == 20 and m["failed_calls"] == 0
    assert all(passed for _, passed in ev.verdict([m, m], [60, 60]))


def test_invented_number_in_a_tailored_line_is_counted():
    # the guardrail in tailor.assemble reverts this line, so the final resume stays clean
    events = []
    m = ev.evaluate(Model(bullet="Built an n8n workflow in Python that cut manual entry by 90%."), RES, ev.load_jobs()[:2], 70, events)
    assert m["unsupported_lines"] == 0
    assert any("kept original wording" in " ".join(r) for e, f in events if e == "resume_tailored" for r in [f["reverts"]])


def test_evidence_and_failure_rules_fail_when_broken():
    no_ev = dict(GOOD, matching_evidence=[])
    m = ev.evaluate(Model(score=no_ev), RES, ev.load_jobs()[:3], 70, [])
    assert m["no_evidence"] == 3
    bad = {"jobs": 20, "failed_calls": 7, "unsupported_lines": 0, "long_notes": 0, "no_evidence": 0}
    assert ("invalid-output rate under 10% in each run", False) in ev.verdict([bad], [60])


def test_verdict_is_added_as_one_line_to_the_eval_log(tmp_path, monkeypatch):
    import json, shutil
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    shutil.copy(Path(__file__).resolve().parent.parent / "config.yaml", ".")
    ev.main(["--runs", "1"], backend=Model())
    lines = [json.loads(l) for l in Path("logs/eval.jsonl").read_text().splitlines()]
    verdicts = [l for l in lines if l.get("event") == "eval_verdict"]
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v["verdict"] in ("PASS", "FAIL") and v["score"].count("/") == 1 and v["threshold"] == 70
    assert v["date"] == __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
