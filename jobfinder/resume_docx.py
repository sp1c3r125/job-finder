"""Write a tailored resume as a plain one-column .docx (easy for applicant tracking systems to read)
and a .changes.md beside it with the gaps, the changes and the guardrail reverts."""
from pathlib import Path

from .drafts import slug


def write_docx(t, path):
    import docx
    from docx.shared import Pt

    d = docx.Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading(t["name"] or "Resume", level=0)
    if t.get("headline"):
        d.add_paragraph(t["headline"])
    if t["contact"]:
        d.add_paragraph(" | ".join(t["contact"]))
    if t["summary"]:
        d.add_heading("Summary", level=1)
        d.add_paragraph(t["summary"])
    if t["skills"]:
        d.add_heading("Skills", level=1)
        d.add_paragraph(", ".join(t["skills"]))
    if t["roles"]:
        d.add_heading("Experience", level=1)
        for r in t["roles"]:
            head = d.add_paragraph()
            head.add_run(", ".join(x for x in (r["title"], r["company"]) if x)).bold = True
            head.paragraph_format.space_after = Pt(0)
            meta = " | ".join(x for x in (r["dates"], r["location"]) if x)
            if meta:
                d.add_paragraph(meta)
            for b in r["bullets"]:
                d.add_paragraph(b, style="List Bullet")
            for g in r.get("groups") or []:
                h = d.add_paragraph()
                run = h.add_run(g["heading"])
                run.bold = run.italic = True
                h.paragraph_format.space_after = Pt(0)
                h.paragraph_format.keep_with_next = True
                for b in g["bullets"]:
                    d.add_paragraph(b, style="List Bullet")
    if t.get("additional"):
        d.add_heading("Additional", level=1)
        for x in t["additional"]:
            d.add_paragraph(x)
    for head, key in (("Education", "education"), ("Certifications", "certifications")):
        if t[key]:
            d.add_heading(head, level=1)
            for x in t[key]:
                d.add_paragraph(x)
    if t["languages"]:
        d.add_heading("Languages", level=1)
        d.add_paragraph(", ".join(t["languages"]))
    d.save(path if hasattr(path, "write") else str(path))      # a path, or a file-like object (hosted mode streams bytes)


def docx_bytes(t):
    """The .docx as bytes, built in memory. Hosted mode never writes a resume to disk."""
    import io
    buf = io.BytesIO()
    write_docx(t, buf)
    return buf.getvalue()


def write_tailored(t, info, job, out_dir="resumes"):
    """Returns the .docx path. Nothing is sent anywhere."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    base = f"{slug(job.company)}__{slug(job.title)}__{job.source}"
    docx_path = d / f"{base}.docx"
    write_docx(t, docx_path)
    md = [f"# Tailored resume: {job.title} at {job.company}", f"Job link: {job.url}", f"Resume: {docx_path.name}",
          "STATUS: draft. Read every line before you send it. Nothing was sent.", "",
          "## Gaps (the job asks for these; your resume does not show them)"]
    md += [f"- {g}" for g in info["gaps"]] or ["- none named"]
    md += ["", "## Changes made"] + ([f"- {c}" for c in info["changes"]] or ["- none"])
    md += ["", "## Guardrail reverts (rewritten text rejected by code; original kept)"]
    md += [f"- {r}" for r in info["reverts"]] or ["- none"]
    (d / f"{base}.changes.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return str(docx_path)
