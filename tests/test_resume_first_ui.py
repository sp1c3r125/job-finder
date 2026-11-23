"""The resume-first web UI: landing vs results, filter and sort links, cards, progress panel, brand, CSP, no inline styles."""
import json
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from jobfinder import db, main, web
from jobfinder.runner import Runner
from jobfinder.sources import ashby
from .conftest import CFG, FakeBackend, FakeHttp, GOOD, make_job, recent

ROOT = Path(__file__).resolve().parent.parent
CSP = "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
NOW = "2026-11-21T00:00:00+00:00"


def build(tmp_path, resume=True, **cfg):
    """Four invented jobs: Strong One (90, 1 day old), Good One (70, 10 days), Possible One (50, 2 days), Rule One (rule 55, 3 days)."""
    path = str(tmp_path / "t.db")
    con = db.connect(path)
    for sid, title, score, days, src, loc in (("1", "Strong One", 90, 1, "ashby", "Remote - Worldwide"),
                                              ("2", "Good One", 70, 10, "remoteok", "Philippines"),
                                              ("3", "Possible One", 50, 2, "lever", "APAC")):
        j = make_job(source=src, source_id=sid, title=title, company="Acme", posted=recent(days), location=loc, url=f"https://x.example/{sid}")
        db.save(con, j, NOW, True, "worldwide")
        db.set_score(con, j, dict(GOOD, fit_score=score, matching_evidence=[f"Evidence for {title}"], gaps=[f"Gap for {title}"]))
    r = make_job(source="greenhouse", source_id="4", title="Rule One", company="Beta", posted=recent(3), location="Asia", url="https://x.example/4")
    db.save(con, r, NOW, True, "asia")
    db.set_rule_scores(con, [(r, {"score": 55, "reasons": ["title matches"], "deal_breakers": []})])
    con.close()
    (tmp_path / "profile.yaml").write_text("timezone: UTC+8\n", encoding="utf-8")
    if resume:
        (tmp_path / "resume.json").write_text(json.dumps({"name": "Alex Example", "source_file": "abc.md", "imported_at": "2026-11-22T20:00:00+00:00",
            "roles": [{}], "skills": ["Python"], "search_keywords": [], "left_out": [],
            "target": {"titles": ["AI Automation Engineer", "Solutions Engineer"], "title_words": ["engineer"], "exclude_words": ["intern"], "level": "mid"}}), encoding="utf-8")
    app = web.create_app(path, dict(CFG, **cfg), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"), run_pipeline=lambda: "ok")
    return TestClient(app, base_url="http://127.0.0.1"), app


def sub(tmp_path, name):
    (tmp_path / name).mkdir()
    return tmp_path / name


def titles_in_order(html):
    return re.findall(r'<h2 id="job-\d+"><a href="[^"]+">([^<]+)</a>', html)


def card(html, title):
    """The HTML of one job card."""
    return next(part for part in html.split("<article")[1:] if f">{title}</a>" in part)


def test_landing_without_resume_and_results_with_one(tmp_path):
    c, _ = build(tmp_path, resume=False)
    t = c.get("/").text
    assert "Skip the search box" in t and "Choose your resume" in t and 'action="/upload"' in t
    assert "How it works" in t and "Our rules" in t and t.count('class="tile') >= 6
    assert "jobs fit your resume" not in t and "Strong One" not in t
    assert "drag" not in t.lower()
    (tmp_path / "resume.json").write_text(json.dumps({"name": "Alex"}), encoding="utf-8")
    t = c.get("/").text
    assert "4 jobs fit your resume" in t and "Skip the search box" not in t and "Strong One" in t


def test_filter_links_and_aria_current(tmp_path):
    c, _ = build(tmp_path)
    t = c.get("/").text
    nav = t[t.index('aria-label="Filter matches"'):t.index('aria-label="Sort matches"')]
    assert re.search(r'<a class="pill" href="/" aria-current="true">All 4</a>', nav)
    for q, label, n in (("filter=strong", "Strong fit", 1), ("filter=good", "Good fit", 1), ("filter=week", "Posted this week", 3)):
        assert f'<a class="pill" href="/?{q}">{label} {n}</a>' in nav
    t = c.get("/?filter=strong").text
    assert titles_in_order(t) == ["Strong One"]
    assert '<a class="pill" href="/?filter=strong" aria-current="true">Strong fit 1</a>' in t
    assert titles_in_order(c.get("/?filter=good").text) == ["Good One"]
    assert titles_in_order(c.get("/?filter=week").text) == ["Strong One", "Possible One", "Rule One"] or \
        set(titles_in_order(c.get("/?filter=week").text)) == {"Strong One", "Possible One", "Rule One"}
    assert len(titles_in_order(c.get("/?filter=nonsense&sort=nonsense").text)) == 4          # unknown values fall back


def test_sort_links_keep_the_filter_and_order_changes(tmp_path):
    c, _ = build(tmp_path)
    assert titles_in_order(c.get("/").text) == ["Strong One", "Good One", "Possible One", "Rule One"]       # model scores first
    assert titles_in_order(c.get("/?sort=newest").text) == ["Strong One", "Possible One", "Rule One", "Good One"]
    t = c.get("/?filter=week&sort=newest").text
    assert '<a class="pill" href="/?filter=week" aria-current="true">Best match</a>' not in t
    assert '<a class="pill" href="/?filter=week&amp;sort=newest" aria-current="true">Newest</a>' in t or \
        '<a class="pill" href="/?filter=week&sort=newest" aria-current="true">Newest</a>' in t
    assert '<a class="pill" href="/?filter=strong&amp;sort=newest"' in t or '<a class="pill" href="/?filter=strong&sort=newest"' in t
    assert '<a class="pill" href="/?filter=week">Best match</a>' in t


def test_card_fields_and_buttons(tmp_path):
    c, _ = build(tmp_path)
    t = c.get("/").text
    strong = card(t, "Strong One")
    for x in ("Acme", "Remote - Worldwide", "Posted", "<time ", "via Ashby", "Evidence for Strong One", "Gap for Strong One",
              "Why it fits", "Gaps", "Tailor my resume", "Draft a cover note", "Open job post", ">90<", "Strong fit"):
        assert x in strong, x
    assert 'href="https://x.example/1" target="_blank" rel="noopener noreferrer"' in strong
    assert 'action="/job/ashby/1/tailor"' in strong and 'action="/job/ashby/1/note"' in strong
    assert "Good fit" in card(t, "Good One") and "Possible fit" in card(t, "Possible One")
    assert 'via <a href="https://x.example/2">Remote OK</a>' in card(t, "Good One")             # _credit.html kept


def test_rule_score_card_has_no_why_or_gaps(tmp_path):
    c, _ = build(tmp_path)
    rule = card(c.get("/").text, "Rule One")
    assert "Rule score" in rule and ">55<" in rule and "via Greenhouse" in rule
    assert "Why it fits" not in rule and "Gaps" not in rule and "<ul>" not in rule
    assert "Strong fit" not in rule and "Good fit" not in rule and "Possible fit" not in rule
    assert "Tailor my resume" in rule and "Open job post" in rule


def test_last_run_line_and_posts_checked(tmp_path):
    c, _ = build(tmp_path)
    assert "No search run yet" in c.get("/").text
    con = db.connect(str(tmp_path / "t.db"))
    db.set_setting(con, "last_run", json.dumps({"at": "2026-11-22T20:00:00+00:00", "checked": 1240}))
    con.close()
    t = c.get("/").text
    assert '<time datetime="2026-11-23T04:00:00+08:00">2026-11-23 04:00 UTC+8</time>' in t and "1,240 posts checked" in t


def test_side_panel(tmp_path):
    c, _ = build(tmp_path)
    t = c.get("/").text
    side = t[t.index('<aside'):]
    for x in ("abc.md", "Upload a new one", "Roles we search for", "AI Automation Engineer", "Run search now",
              "Turn kill switch on", "Model calls today: 0 of", "Kill switch: <strong>off</strong>"):
        assert x in side, x
    assert "Delete my data" not in t


def test_site_name_in_title_header_and_footer(tmp_path):
    c, _ = build(tmp_path, site_name="Acme Jobs")
    for path in ("/", "/matches", "/about", "/job/ashby/1"):
        t = c.get(path).text
        assert "<title>Acme Jobs: " in t, path
        assert '<span class="brand-name">Acme Jobs</span>' in t and "<footer" in t and "Acme Jobs. Personal use only" in t
    assert "<title>jobfinder: " in build(sub(tmp_path, "default"))[0].get("/").text
    assert "site_name:" in (ROOT / "config.yaml").read_text(encoding="utf-8")


def test_no_inline_styles_scripts_or_handlers_in_templates_and_pages(tmp_path):
    for f in (ROOT / "jobfinder" / "templates").glob("*.html"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle\s*=", src, re.I) and "<style" not in src.lower() and "<script" not in src.lower(), f.name
        assert not re.search(r"\son[a-z]+\s*=", src, re.I), f.name
    c, _ = build(tmp_path)
    for path in ("/", "/?filter=strong", "/matches", "/about", "/job/ashby/1", "/job/greenhouse/4"):
        t = c.get(path).text
        assert not re.search(r"\sstyle\s*=", t) and "<script" not in t and "http-equiv" not in t, path
        assert "https://fonts.g" not in t and "http://" not in t.replace("http://127.0.0.1", ""), path


def test_csp_header_is_unchanged_on_every_new_page(tmp_path):
    c, _ = build(tmp_path)
    assert web.SECURITY_HEADERS["Content-Security-Policy"] == CSP
    for path in ("/", "/matches", "/?filter=week&sort=newest", "/job/ashby/1", "/about", "/static/fonts/inter-latin-wght-normal.woff2"):
        assert c.get(path).headers["content-security-policy"] == CSP, path
    c2, _ = build(sub(tmp_path, "landing"), resume=False)
    assert c2.get("/").headers["content-security-policy"] == CSP                 # landing too


def test_fonts_are_self_hosted_with_licences():
    fonts = ROOT / "jobfinder" / "static" / "fonts"
    css = (ROOT / "jobfinder" / "static" / "style.css").read_text(encoding="utf-8")
    for name in ("inter-latin-wght-normal.woff2", "space-grotesk-latin-wght-normal.woff2", "Inter-LICENSE.txt", "SpaceGrotesk-LICENSE.txt"):
        assert (fonts / name).stat().st_size > 1000
        if name.endswith(".woff2"):
            assert f"/static/fonts/{name}" in css and (fonts / name).read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (fonts / "Inter-LICENSE.txt").read_text(encoding="utf-8")
    assert "url(http" not in css and "@import" not in css


def test_every_colour_token_has_light_light_dark_and_dark_fallback_values():
    css = (ROOT / "jobfinder" / "static" / "style.css").read_text(encoding="utf-8")
    base = css[css.index(":root {"):css.index("@supports (color")]
    ld = css[css.index("@supports (color"):css.index("@media (prefers-color-scheme")]
    fb = css[css.index("@media (prefers-color-scheme"):css.index("*, *::before")]
    tok = lambda block: dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))
    light, pairs, dark = tok(base), tok(ld), tok(fb)
    colour = [k for k, v in light.items() if v.strip().startswith(("#", "0 24px"))]
    assert {"--bg", "--surface", "--fg", "--link", "--navy", "--accent", "--on-accent", "--strong-bg", "--possible-bg"} <= set(colour)
    for k in colour:
        m = re.fullmatch(r"light-dark\((.+),\s*([^,]+?)\)", pairs[k].strip()) if "rgb(" not in pairs[k] else None
        if m:
            assert m.group(1).strip() == light[k].strip(), k
            assert m.group(2).strip() == dark[k].strip(), k
        else:
            assert k in pairs and k in dark, k
    assert light["--navy"].strip() == "#0F1B2D" and light["--accent"].strip() == "#F4A62A" and light["--link"].strip() == "#1D5C8C"


def _lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def test_text_colour_pairs_meet_4_5_to_1_in_light_and_dark():
    css = (ROOT / "jobfinder" / "static" / "style.css").read_text(encoding="utf-8")
    fb = css[css.index("@media (prefers-color-scheme"):css.index("*, *::before")]
    base = css[css.index(":root {"):css.index("@supports (color")]
    pair = lambda block: dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", block))
    for name, t in (("light", pair(base)), ("dark", {**pair(base), **pair(fb)})):
        for fg, bg in (("--fg", "--bg"), ("--fg", "--surface"), ("--muted", "--bg"), ("--muted", "--surface"), ("--link", "--bg"),
                       ("--link", "--surface"), ("--on-navy", "--navy"), ("--navy-text", "--navy"), ("--navy-link", "--navy"),
                       ("--on-accent", "--accent"), ("--chip-fg", "--chip-bg"), ("--warn", "--surface"), ("--strong-fg", "--strong-bg"),
                       ("--good-fg", "--good-bg"), ("--possible-fg", "--possible-bg"), ("--rule-fg", "--rule-bg"), ("--rule-fg", "--surface"),
                       ("--muted", "--drop-bg"), ("--accent", "--navy")):
            assert _ratio(t[fg], t[bg]) >= 4.5, (name, fg, bg, _ratio(t[fg], t[bg]))


def test_touch_targets_are_44px_and_no_fixed_widths_cause_sideways_scroll():
    css = (ROOT / "jobfinder" / "static" / "style.css").read_text(encoding="utf-8")
    for sel in (".pill {", "button, .btn {", ".site-header nav a {", ".brand {", ".panel summary {", 'input[type="file"]::file-selector-button {'):
        start = css.index(sel)
        assert "min-block-size: 44px" in css[start:css.index("}", start)], sel
    assert not re.search(r"(?<!min-)(?<!max-)(?<!-)(?:inline-size|width):\s*\d{3,}px", css)


# --- progress panel ---------------------------------------------------------------------------------------------------

def test_runner_keeps_stage_and_counts_and_resets_on_start():
    r = Runner()
    assert r.progress == {}
    r.set_progress("fetch", checked=10)
    r.set_progress("filter", roles=4, open_ph=2)
    assert r.progress == {"stage": "filter", "checked": 10, "roles": 4, "open_ph": 2}
    done = []
    assert r.start("x", lambda: done.append(1))
    import time
    for _ in range(100):
        if done:
            break
        time.sleep(0.01)
    assert r.progress == {}


def test_progress_panel_replaces_results_while_a_run_is_busy(tmp_path):
    import threading
    gate = threading.Event()
    build(tmp_path)                                      # the database, profile and resume.json
    path = str(tmp_path / "t.db")
    app2 = web.create_app(path, dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"), run_pipeline=lambda: gate.wait(5) and "ok")
    c = TestClient(app2, base_url="http://127.0.0.1", follow_redirects=False)
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/").text).group(1)
    assert "Strong One" in c.get("/").text
    assert c.post("/run", data={"csrf_token": token}).status_code == 303
    t = c.get("/").text
    assert "Finding jobs that fit your resume" in t and "Strong One" not in t and "Check progress" in t
    assert "http-equiv" not in t and "<script" not in t
    assert 'data-state="active"' in t and "Posts checked" not in t                       # nothing counted yet
    app2.state.runner.set_progress("filter", checked=1240, roles=96, open_ph=31)
    t = c.get("/").text
    assert "Posts checked" in t and "1,240" in t and "Match your roles" in t and ">96<" in t and "Open to the Philippines" in t and ">31<" in t
    app2.state.runner.set_progress("score", to_score=15, scored=3)
    assert "read 3 of 15 jobs" in c.get("/").text
    assert "Strong One" in c.get("/matches").text                                        # results stay reachable
    gate.set()


def test_run_reports_each_stage_and_saves_last_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copy(ROOT / "config.yaml", ".")
    shutil.copy(ROOT / "profile.example.yaml", "profile.yaml")
    Path("companies.yaml").write_text("companies:\n  - {name: Acme, source: ashby, board: acme}\n")
    job = {"id": "1", "title": "AI Automation Engineer", "jobUrl": "https://a/1", "publishedAt": recent(), "location": "Remote - Worldwide",
           "workplaceType": "Remote", "descriptionPlain": "LLM agents"}
    off = {"id": "2", "title": "Barista", "jobUrl": "https://a/2", "publishedAt": recent(), "location": "Remote - Worldwide",
           "workplaceType": "Remote", "descriptionPlain": "coffee"}
    http = FakeHttp({ashby.URL.format(board="acme"): {"jobs": [job, off]}})
    seen = []
    assert main.run(["--db", "t.db"], backend=FakeBackend([GOOD]), http=http, progress=lambda stage, **kw: seen.append((stage, kw))) == 0
    stages = [s for s, _ in seen]
    assert stages[0] == "fetch" and stages[1] == "filter" and stages[-1] == "done" and "score" in stages
    assert seen[0][1] == {"checked": 2}
    assert seen[1][1]["checked"] == 2 and seen[1][1]["open_ph"] == 1
    assert ("score", {"scored": 1}) in seen
    con = db.connect("t.db")
    last = json.loads(db.get_setting(con, "last_run"))
    con.close()
    assert last["checked"] == 2 and last["at"].startswith("20")
