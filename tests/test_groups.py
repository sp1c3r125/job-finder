"""Import gaps: sub-headings kept as groups (not bullets) and lines that fit no field kept as `additional`."""
import copy
from pathlib import Path

import docx

from jobfinder import resume
from jobfinder.resume_docx import write_docx
from jobfinder.tailor import assemble, resume_block
from .conftest import FakeBackend

TEXT = """Alex Example
Workflow Developer  |  Certified Process Analyst
alex@example.com | Porto, Portugal
SUMMARY
Developer who builds scheduling and reporting tools for retail and logistics teams in Portugal and Brazil.
EXPERIENCE
Acme Co  |  Automation Engineer  |  2024 - Present
Rollout lead - regional bakery chain
- Built a shift-scheduling tool used by 9 bakery branches.
- Built a read-only stock report that lets managers check ovens and supplies.
Inventory project - garden supply wholesaler
- Delivered 4 reports for 6 buyers.
- Replaced a paper stock count with a live spreadsheet.
Shop-floor tooling
- Built a label-printing service with 58 passing tests.
Independent  |  Freelancer  |  2022 - 2024
- Built Pantrylog, a stock-tracking app for corner shops.
CERTIFICATIONS & EDUCATION
Certified Process Analyst
Earlier:
Shift Supervisor, Blue Harbor Bakery (2019 - 2021)  |  Bookkeeper, Northfield Insurance Group (2015 - 2017)
Leadership:
Chair, Riverside Rotary Club (2023)  |  Led the chapter; raised $12,000 for a community garden program
"""
H = ["Rollout lead - regional bakery chain", "Inventory project - garden supply wholesaler", "Shop-floor tooling"]
G0 = ["Built a shift-scheduling tool used by 9 bakery branches.", "Built a read-only stock report that lets managers check ovens and supplies."]
G1 = ["Delivered 4 reports for 6 buyers.", "Replaced a paper stock count with a live spreadsheet."]
G2 = ["Built a label-printing service with 58 passing tests."]
LEAD = "Leadership: Chair, Riverside Rotary Club (2023) | Led the chapter; raised $12,000 for a community garden program"
EARLIER = ("Earlier: Shift Supervisor, Blue Harbor Bakery (2019 - 2021) | "
           "Bookkeeper, Northfield Insurance Group (2015 - 2017)")
RAW = {
    "name": "Alex Example", "headline": "Workflow Developer | Certified Process Analyst", "contact": ["alex@example.com", "Porto, Portugal"],
    "summary": "Developer who builds scheduling and reporting tools for retail and logistics teams in Portugal and Brazil.",
    "skills": ["scheduling"],
    "roles": [
        {"title": "Automation Engineer", "company": "Acme Co", "dates": "2024 - Present", "location": "", "bullets": [],
         "groups": [{"heading": H[0], "bullets": G0}, {"heading": H[1], "bullets": G1}, {"heading": H[2], "bullets": G2}]},
        {"title": "Freelancer", "company": "Independent", "dates": "2022 - 2024", "location": "",
         "bullets": ["Built Pantrylog, a stock-tracking app for corner shops."], "groups": []}],
    "education": [], "certifications": ["Certified Process Analyst"], "languages": [], "additional": [EARLIER, LEAD],
    "search_keywords": [], "target": {"titles": ["A"], "title_words": ["a"], "exclude_words": [], "level": "mid"},
}


def imported(tmp_path, raw):
    src = tmp_path / "cv.md"
    src.write_text(TEXT, encoding="utf-8")
    return resume.import_resume(FakeBackend([raw]), src, lambda *a, **k: None, tmp_path / "resume.json")


# --- (a) sub-headings are groups, not bullets ------------------------------------------------------------------------

def test_headings_are_saved_as_groups_with_their_own_bullets(tmp_path):
    facts, dropped = imported(tmp_path, RAW)
    r0 = facts["roles"][0]
    assert r0["bullets"] == [] and [g["heading"] for g in r0["groups"]] == H
    assert [g["bullets"] for g in r0["groups"]] == [G0, G1, G2]
    assert not any(h in b for h in H for b in r0["bullets"])             # no heading was kept as a bullet
    assert dropped == []


def test_a_heading_not_found_in_the_resume_drops_the_group_and_says_so(tmp_path):
    raw = copy.deepcopy(RAW)
    raw["roles"][0]["groups"][1]["heading"] = "Invented engagement"
    facts, dropped = imported(tmp_path, raw)
    assert [g["heading"] for g in facts["roles"][0]["groups"]] == [H[0], H[2]]
    assert any("Invented engagement" in d for d in dropped)


# --- (b) lines that fit no field -------------------------------------------------------------------------------------

def test_additional_lines_are_checked_word_for_word_like_other_facts(tmp_path):
    raw = copy.deepcopy(RAW)
    raw["additional"] = [EARLIER, LEAD, "Leadership: Chair, Riverside Rotary Club (2031) | Led the chapter", "Won a Nobel prize"]
    facts, dropped = imported(tmp_path, raw)
    assert facts["additional"] == [EARLIER, LEAD]
    assert sorted(d.split(": ", 1)[0] for d in dropped) == ["additional line", "additional line"]
    assert "additional" in resume.profile_facts(facts)    # the scoring prompt sees them too


# --- tailoring: reorder inside groups only, headings and additional lines untouched -----------------------------------

def tailor_out(roles):
    return {"summary": "", "skills": [], "gaps": [], "changes": [], "roles": roles}


def bullets(ids_texts):
    return [{"source_id": i, "text": t} for i, t in ids_texts]


def facts_for(tmp_path):
    return imported(tmp_path, RAW)[0]


def test_resume_block_lists_group_headings_and_group_bullet_ids(tmp_path):
    block = resume_block(facts_for(tmp_path))
    assert f"[r0g1] GROUP HEADING: {H[1]}" in block and f"[r0g1b0] {G1[0]}" in block


def test_groups_and_bullets_inside_a_group_can_be_reordered(tmp_path):
    res = facts_for(tmp_path)
    out = tailor_out([{"role_id": "r0", "bullets": [], "groups": [
        {"group_id": "r0g1", "bullets": bullets([("r0g1b1", G1[1]), ("r0g1b0", G1[0])])},
        {"group_id": "r0g0", "bullets": bullets([("r0g0b0", G0[0]), ("r0g0b1", G0[1])])}]}])
    t, info = assemble(res, out, "job text")
    groups = t["roles"][0]["groups"]
    assert [g["heading"] for g in groups] == [H[1], H[0], H[2]]          # reordered; the omitted group stays, in its place
    assert groups[0]["bullets"] == [G1[1], G1[0]]
    assert groups[2]["bullets"] == G2                                     # the omitted group kept all its bullets
    assert any("r0g2: no usable bullets" in r for r in info["reverts"])


def test_a_bullet_never_moves_to_another_group(tmp_path):
    res = facts_for(tmp_path)
    out = tailor_out([{"role_id": "r0", "bullets": [], "groups": [
        {"group_id": "r0g0", "bullets": bullets([("r0g0b0", G0[0]), ("r0g1b0", G1[0])])},        # G1[0] belongs under H[1]
        {"group_id": "r0g1", "bullets": bullets([("r0g1b1", G1[1])])}]}])
    t, info = assemble(res, out, "job text")
    g0, g1, _ = t["roles"][0]["groups"]
    assert g0["bullets"] == [G0[0]] and G1[0] not in g0["bullets"]
    assert g1["bullets"] == [G1[1]]
    assert any("r0g1b0" in r and "another group" in r for r in info["reverts"])


def test_headings_stay_word_for_word_and_unknown_groups_are_ignored(tmp_path):
    res = facts_for(tmp_path)
    out = tailor_out([{"role_id": "r0", "bullets": [], "groups": [
        {"group_id": "r0g0", "bullets": bullets([("r0g0b0", "Built a ticket agent at 23 clients."), ("r0g0b1", G0[1])])},
        {"group_id": "r1g0", "bullets": []}]}])                            # r1 has no groups; r0 must not claim r1's id
    t, info = assemble(res, out, "job text")
    assert [g["heading"] for g in t["roles"][0]["groups"]] == H
    assert any("r1g0: unknown group id" in r for r in info["reverts"])


def test_a_reworded_group_bullet_cannot_add_a_number(tmp_path):
    res = facts_for(tmp_path)
    bad = G1[0].replace("4 reports", "40 reports")
    out = tailor_out([{"role_id": "r0", "bullets": [], "groups": [{"group_id": "r0g1", "bullets": bullets([("r0g1b0", bad)])}]}])
    t, info = assemble(res, out, "job text")
    assert t["roles"][0]["groups"][0]["bullets"][0] == G1[0] and any("new numbers" in r for r in info["reverts"])


def test_additional_lines_are_copied_never_rewritten(tmp_path):
    res = facts_for(tmp_path)
    out = tailor_out([])
    out["summary"] = "x"
    t, _ = assemble(res, out, "job text")
    assert t["additional"] == [EARLIER, LEAD]


# --- the .docx ---------------------------------------------------------------------------------------------------------

def test_docx_shows_headings_bold_italic_above_their_bullets_and_additional_after_experience(tmp_path):
    res = facts_for(tmp_path)
    t, _ = assemble(res, tailor_out([]), "job text")
    path = tmp_path / "r.docx"
    write_docx(t, path)
    paras = docx.Document(str(path)).paragraphs
    texts = [p.text for p in paras]
    i = texts.index(H[1])
    run = paras[i].runs[0]
    assert run.bold and run.italic and paras[i].style.name == "Normal"
    assert [p.text for p in paras[i + 1:i + 3]] == G1 and all(p.style.name == "List Bullet" for p in paras[i + 1:i + 3])
    assert paras[i - 1].style.name == "List Bullet" and texts.index(H[0]) < i                 # the group above ends before it starts
    assert texts.index("Experience") < texts.index("Additional") < texts.index(EARLIER) < texts.index(LEAD)
    assert texts.index("Additional") > texts.index("Built Pantrylog, a stock-tracking app for corner shops.")
    assert texts.index("Additional") < texts.index("Education") if "Education" in texts else True


# --- headline: the title line under the name ---------------------------------------------------------------------------

HEADLINE = "Workflow Developer  |  Certified Process Analyst"


def test_headline_is_checked_like_other_facts_and_is_not_an_additional_line(tmp_path):
    facts, dropped = imported(tmp_path, dict(RAW, headline=HEADLINE))
    assert facts["headline"] == HEADLINE and HEADLINE not in facts["additional"] and dropped == []
    assert resume.profile_facts(facts)["headline"] == HEADLINE
    facts, dropped = imported(tmp_path, dict(RAW, headline="Chief Wizard of Everything"))
    assert facts["headline"] == "" and any(d.startswith("headline: Chief Wizard") for d in dropped)


def test_docx_prints_the_headline_directly_under_the_name(tmp_path):
    res = facts_for(tmp_path)
    res["headline"] = HEADLINE
    t, _ = assemble(res, tailor_out([]), "job text")
    assert t["headline"] == HEADLINE
    write_docx(t, tmp_path / "h.docx")
    texts = [p.text for p in docx.Document(str(tmp_path / "h.docx")).paragraphs]
    assert texts[:2] == ["Alex Example", HEADLINE]
