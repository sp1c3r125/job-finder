import html
import json
from pathlib import Path

from openpyxl import Workbook

COLUMNS = ["Source", "Company", "Title", "Link", "Posted", "Location rule", "Score",
           "Evidence", "Gaps", "Deal-breakers", "Draft", "Resume", "Summary"]
SOURCE_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
                 "himalayas": "Himalayas", "remoteok": "Remote OK", "wwr": "We Work Remotely"}


def rows(con):
    cur = con.execute("SELECT source,company,title,url,posted,rule,score_json,draft_path,also_seen_on,resume_path "
                      "FROM jobs WHERE score_json IS NOT NULL")
    out = []
    for source, company, title, url, posted, rule, sj, draft, also, resume in cur:
        s = json.loads(sj)
        names = [SOURCE_LABELS.get(x, x) for x in [source] + json.loads(also or "[]")]
        out.append(dict(source=names[0], sources=", ".join(names), source_key=source, company=company,
                        title=title, url=url, posted=(posted or "")[:10], rule=rule or "",
                        score=s["fit_score"], evidence="; ".join(s["matching_evidence"]),
                        gaps="; ".join(s["gaps"]), deal_breakers="; ".join(s["deal_breakers"]),
                        draft=draft or "", resume=resume or "", summary=s["summary"]))
    return sorted(out, key=lambda r: -r["score"])


def write_xlsx(rs, path="report.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for r in rs:
        ws.append([r["sources"], r["company"], r["title"], r["url"], r["posted"], r["rule"], r["score"],
                   r["evidence"], r["gaps"], r["deal_breakers"], r["draft"], r["resume"], r["summary"]])
    wb.save(path)


def _file_link(p):
    """Relative link to a local file (dashboard.html sits in the project folder)."""
    if not p:
        return ""
    href = p.replace(chr(92), "/")
    return f'<a href="{html.escape(href)}">{html.escape(Path(href).name)}</a>'


def write_dashboard(rs, path="dashboard.html"):
    e = html.escape
    body = []
    for r in rs:
        via = ""
        if r["source_key"] == "remoteok":
            via = f' <small>via <a href="{e(r["url"])}">Remote OK</a></small>'
        elif r["source_key"] == "himalayas":
            via = ' <small>via <a href="https://himalayas.app">Himalayas</a></small>'
        elif r["source_key"] == "wwr":
            via = ' <small>via <a href="https://weworkremotely.com">We Work Remotely</a></small>'
        body.append(
            f"<tr><td>{e(r['sources'])}{via}</td><td>{e(r['company'])}</td>"
            f"<td><a href=\"{e(r['url'])}\">{e(r['title'])}</a></td><td>{e(r['posted'])}</td>"
            f"<td>{e(r['rule'])}</td><td><b>{r['score']}</b></td><td>{e(r['evidence'])}</td>"
            f"<td>{e(r['gaps'])}</td><td>{e(r['deal_breakers'])}</td><td>{_file_link(r['draft'])}</td>"
            f"<td>{_file_link(r['resume'])}</td></tr>")
    head = "".join(f"<th>{c}</th>" for c in COLUMNS if c != "Summary")
    page = f"""<!doctype html><meta charset="utf-8"><title>Job matches</title>
<style>body{{font:14px system-ui;margin:1.5rem}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;vertical-align:top;text-align:left}}th{{background:#eee}}</style>
<h1>Job matches</h1><p>Personal use only. Drafts and tailored resumes are not sent anywhere.</p>
<table><tr>{head}</tr>{''.join(body)}</table>
<p><small>Job data sources: Greenhouse, Lever and Ashby public job-board APIs; Himalayas (himalayas.app);
Remote OK (remoteok.com); We Work Remotely (weworkremotely.com).</small></p>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
