"""Private dashboard. Bind to 127.0.0.1 only; reach it through a private tunnel.
No SQL here: everything goes through db.py. The app never sends or applies anywhere:
every action only reads public job feeds, calls the model (behind the cap and kill switch) and writes local files."""
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from markupsafe import Markup

from . import db, report
from . import demo as demo_mod
from . import resume as resume_mod
from . import target as target_mod
from .drafts import draft_note, write_draft
from .guard import CappedBackend, default_cap, killed
from .localtime import load_zone, time_html, to_local
from .log import make_logger
from .resume_docx import write_tailored
from .runner import Runner
from .scoring import load_profile
from .tailor import tailor_resume

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'",
}
DOWNLOAD_DIRS = ("drafts", "resumes")      # only these folders can be downloaded
MAX_UPLOAD = 5 * 1024 * 1024
MAGIC = {".pdf": b"%PDF", ".docx": b"PK"}  # cheap wrong-type check for binary formats
NOTICES = {"started": "Started. Select Check progress to see where it is.",
           "busy": "Something is already running. Wait for it to finish.",
           "kill_on": "Kill switch is ON: no model calls will be made.", "kill_off": "Kill switch is off.",
           "no_resume": "Import a resume first.", "no_profile": "Add profile.yaml or import a resume first.", "no_job": "That job is not in the database."}


FILTERS = (("all", "All"), ("strong", "Strong fit"), ("good", "Good fit"), ("week", "Posted this week"))
SORTS = (("best", "Best match"), ("newest", "Newest"))
STRONG_MIN, GOOD_MIN = 80, 65               # fit score bands: Strong fit 80+, Good fit 65-79, Possible fit below 65
BUSY_PANEL = ("Search run", "Resume import")  # runs that replace the results with the progress panel


THEMES = ("light", "dark")


def extra_hosts(environ=None):
    """Hostnames from ALLOWED_HOSTS_EXTRA (.env, comma-separated): your tailnet name. Lowercased; empty items and wildcards are dropped."""
    if environ is None:
        from .settings import load_env
        load_env(str(ROOT / ".env"))
        environ = os.environ
    items = (h.strip().lower() for h in environ.get("ALLOWED_HOSTS_EXTRA", "").split(","))
    return [h for h in items if h and "*" not in h]


def safe_next(url):
    """A same-site path only: starts with one slash, not "//" or a slash and backslash, and has no control characters
    (a tab would turn "/<tab>/" into "//")."""
    ok = url.startswith("/") and not url.startswith(("//", "/\\")) and all(ord(c) >= 32 and c != chr(127) for c in url)
    return url if ok else "/"


def fit_band(r):
    """(key, label) for the score badge. A rule score is a different scale, so it never gets a fit label."""
    if r["score_kind"] != "model":
        return "rule", "Rule score"
    if r["score"] >= STRONG_MIN:
        return "strong", "Strong fit"
    return ("good", "Good fit") if r["score"] >= GOOD_MIN else ("possible", "Possible fit")


def card_rows(rs):
    for r in rs:
        r["fit"], r["fit_label"] = fit_band(r)
    return rs


def list_href(base, flt, srt):
    q = {}
    if flt != "all":
        q["filter"] = flt
    if srt != "best":
        q["sort"] = srt
    return base + ("?" + urlencode(q) if q else "")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_app(db_path=None, cfg=None, base_dir=None, backend=None, run_pipeline=None,
               log_path="logs/decisions.jsonl", kill_file="KILL", demo=False):
    """backend: a model backend or a function returning one (default: BACKEND from .env).
    run_pipeline: replaces the daily search run (tests).
    demo: public read-only build. Reads only demo.db (invented jobs); no write routes, no downloads, no resume,
    no model, no logging of visitors."""
    cfg = cfg if cfg is not None else load_config()
    if not demo:
        # Hosted mode has no sign-in yet (M4 P3). Until it exists the app must never serve in that mode,
        # even when every setting is present: the local app has no login.
        from .settings import check_startup, load_env
        load_env(str(ROOT / ".env"))
        if check_startup(cfg=cfg) == "hosted":
            raise SystemExit("hosted mode is not available yet: sign-in and per-user data are not built (M4 P3)")
    if demo:
        db_path = db_path or str(ROOT / demo_mod.DEMO_DB)
        demo_mod.seed(db_path)
    db_path = db_path or os.environ.get("JOBFINDER_DB", str(ROOT / "jobs.db"))
    base = Path(base_dir or ROOT)
    log = make_logger(log_path)
    resume_json = base / resume_mod.RESUME_JSON         # the project folder, not the current folder
    runner = Runner(log=None if demo else log)
    app_runner = runner
    token = secrets.token_urlsafe(32)
    hosts = list(cfg.get("allowed_hosts") or ["127.0.0.1", "localhost"]) + ([] if demo else extra_hosts())
    cap = int(cfg.get("daily_call_cap", default_cap()))
    app = FastAPI(title="Job matches", docs_url=None, redoc_url=None, openapi_url=None)
    # Host allow-list blocks DNS rebinding. Your tailnet hostname goes in ALLOWED_HOSTS_EXTRA in .env (not in the repo).
    app.state.runner = app_runner                         # tests read and set progress through this
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fileurl"] = lambda p: str(p).replace(chr(92), "/")
    zone = load_zone(base / "profile.yaml")                 # times are shown in this zone (UTC+8 in profile.yaml)
    templates.env.globals["when"] = lambda iso, with_time=True: Markup(time_html(iso, zone, with_time))

    def capped():
        from .backends import get_backend
        return CappedBackend(backend or get_backend, db_path, cap, log, kill_file)

    def guard_post(request, csrf_token):
        """Reject a POST from another site (Origin) or without our token (CSRF)."""
        origin = request.headers.get("origin")
        if origin and (urlparse(origin).hostname or "") not in hosts:
            raise HTTPException(403, "Bad origin")
        if not hmac.compare_digest(csrf_token or "", token):
            raise HTTPException(403, "Bad token")

    def resume_summary():
        """What the dashboard shows about resume.json (None when there is none)."""
        res = resume_mod.load_resume(resume_json)
        if not res:
            return None
        read = res.get("imported_at")
        if not read:
            read = datetime.fromtimestamp(os.path.getmtime(resume_json), timezone.utc).isoformat(timespec="seconds")
        return {"file": res.get("source_file") or "unknown", "read_iso": read,
                "name": res.get("name") or "not found", "roles": len(res.get("roles", [])),
                "skills": len(res.get("skills", [])), "keywords": ", ".join(res.get("search_keywords", [])),
                "left_out": res.get("left_out")}

    def target_summary():
        """The search profile in use (target.yaml overrides resume.json) or None."""
        t, source = target_mod.load_target(resume_mod.load_resume(resume_json), base)
        return None if not t else {**t, "source": source}

    def page(request, name, **ctx):
        con = db.connect(db_path)
        try:
            ctx.setdefault("progress_panel", False)
            cookie = request.cookies.get("theme")
            qs = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "notice"])
            ctx.update(theme=cookie if cookie in THEMES else "", next_url=request.url.path + (f"?{qs}" if qs else ""),
                       site_name=cfg.get("site_name") or "jobfinder", csrf=token, status=runner.take_status(), calls=db.calls_today(con), cap=cap,
                       kill=killed(con, kill_file), demo=demo,
                       has_resume=not demo and resume_mod.load_resume(resume_json) is not None,
                       notice=NOTICES.get(request.query_params.get("notice", "")))
            if ctx["status"]["state"] != "running" and request.query_params.get("notice") == "started":
                ctx["notice"] = None        # the task has ended; its result is shown below instead
        finally:
            con.close()
        return templates.TemplateResponse(request, name, ctx)

    def back(to="/", notice=""):
        return RedirectResponse(to + (f"?notice={notice}" if notice else ""), status_code=303)

    def default_run():
        from . import main
        rc = main.run(["--db", db_path], backend=capped(), root=base, progress=runner.set_progress)
        return "run finished" if rc == 0 else f"run exited with {rc}"

    def all_rows():
        con = db.connect(db_path)
        try:
            return card_rows(report.rows(con, max(cfg.get("max_age_days", 3), cfg.get("research_max_age_days", 7)))),                 db.get_setting(con, "last_run")
        finally:
            con.close()

    def side_info():
        """(resume, target) for the side panel. The demo shows an invented file name and roles."""
        if demo:
            return {"file": demo_mod.RESUME_FILE, "read_iso": ""}, {"titles": demo_mod.ROLES}
        return resume_summary(), target_summary()

    def results(request, base):
        rs, last = all_rows()
        flt = request.query_params.get("filter", "all")
        flt = flt if flt in dict(FILTERS) else "all"
        srt = request.query_params.get("sort", "best")
        srt = srt if srt in dict(SORTS) else "best"
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)

        def this_week(r):
            d = to_local(r["posted_iso"], zone) if r["posted_iso"] else None
            return d is not None and d >= week_ago

        tests = {"all": lambda r: True, "strong": lambda r: r["fit"] == "strong", "good": lambda r: r["fit"] == "good",
                 "week": this_week}
        counts = {k: sum(1 for r in rs if tests[k](r)) for k, _ in FILTERS}
        shown = [r for r in rs if tests[flt](r)]
        if srt == "newest":
            floor = datetime.min.replace(tzinfo=timezone.utc)
            shown.sort(key=lambda r: (to_local(r["posted_iso"], zone) if r["posted_iso"] else None) or floor, reverse=True)
        try:
            last_run = json.loads(last) if last else None
        except ValueError:
            last_run = None
        resume, tgt = side_info()
        return page(request, "index.html", rows=shown, total=len(rs), base=base, resume=resume, target=tgt, last_run=last_run, model_jobs=cfg.get("model_daily_jobs", 15),
                    flt=flt, srt=srt, threshold=cfg.get("score_threshold", 70),
                    filters=[{"label": lab, "count": counts[k], "current": k == flt, "href": list_href(base, k, srt)} for k, lab in FILTERS],
                    sorts=[{"label": lab, "current": k == srt, "href": list_href(base, flt, k)} for k, lab in SORTS])

    def matching(request, kind):
        """The progress panel, shown instead of the results while a search run or a resume import is busy."""
        prog = dict(runner.progress)
        stage = prog.get("stage", "")
        res, tgt = resume_summary(), target_summary()
        active = 0 if kind == "import" else 2 if stage in ("", "fetch") else 3
        states = ["done" if i < active else "active" if i == active else "next" for i in range(4)]
        return page(request, "matching.html", progress_panel=True, kind=kind, prog=prog, states=states, done_count=states.count("done"),
                    resume=res, target=tgt, model_jobs=cfg.get("model_daily_jobs", 15))

    @app.get("/")
    def index(request: Request):
        state = runner.status()
        if state["state"] == "running" and state["label"] in BUSY_PANEL:
            return matching(request, "import" if state["label"] == "Resume import" else "search")
        if demo or resume_mod.load_resume(resume_json) is None:
            con = db.connect(db_path)
            try:
                browse = bool(db.scored_jobs(con))
            finally:
                con.close()
            return page(request, "landing.html", browse=browse)
        return results(request, "/")

    @app.get("/matches")
    def matches(request: Request):
        return results(request, "/matches")

    @app.get("/job/{source}/{source_id:path}")
    def job(request: Request, source: str, source_id: str):
        con = db.connect(db_path)
        try:
            raw = db.get_scored_job(con, source, source_id,
                                    waiting_days=max(cfg.get("max_age_days", 3), cfg.get("research_max_age_days", 7)))
        finally:
            con.close()
        if raw is None:
            raise HTTPException(404, "No such job")
        return page(request, "job.html", r=card_rows([report.row_from(raw)])[0], description=raw["description"] or "")

    @app.get("/about")
    def about(request: Request):
        return page(request, "about.html")

    @app.post("/theme")
    def set_theme(request: Request, csrf_token: str = Form(""), theme: str = Form(""), next: str = Form("/")):
        """Remember light or dark in a cookie, or forget it (device). Stores nothing else and is not logged; also works in the demo."""
        guard_post(request, csrf_token)
        if theme not in THEMES + ("device",):
            raise HTTPException(400, "Unknown theme")
        response = RedirectResponse(safe_next(next), status_code=303)
        if theme == "device":
            response.delete_cookie("theme", path="/")
        else:
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie("theme", theme, max_age=31536000, path="/", samesite="lax", httponly=True, secure=secure)
        return response

    if demo:
        # Every write route exists only to answer 405, so nothing can change data on the public demo.
        def not_allowed():
            raise HTTPException(405, "Read-only demo")

        for route in ("/run", "/upload", "/kill", "/job/{source}/{source_id:path}/tailor", "/job/{source}/{source_id:path}/note"):
            app.add_api_route(route, not_allowed, methods=["POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)

        @app.get("/files/{path:path}")
        def no_files(path: str):
            raise HTTPException(404, "No downloads in the demo")

        return app

    @app.post("/run")
    def run_now(request: Request, csrf_token: str = Form("")):
        guard_post(request, csrf_token)
        if not runner.start("Search run", run_pipeline or default_run):
            return back(notice="busy")
        log("web_run", None)
        return back(notice="started")

    @app.post("/upload")
    async def upload(request: Request, csrf_token: str = Form(""), file: UploadFile = File(...)):
        guard_post(request, csrf_token)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in resume_mod.EXTS:
            raise HTTPException(400, "Use a PDF, DOCX, TXT or MD file")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File is over 5 MB")
        if not data or (ext in MAGIC and not data.startswith(MAGIC[ext])):
            raise HTTPException(400, "The file does not look like a " + ext[1:].upper())
        dest = base / resume_mod.DROP_DIR / f"{secrets.token_hex(8)}{ext}"   # random name; the original is never used

        def work():
            try:
                got = resume_mod.import_resume(capped(), dest, log, resume_json)
            except ValueError:                           # unreadable file: do not keep it
                dest.unlink(missing_ok=True)
                raise
            return "resume imported" if got else "resume import failed; see logs/decisions.jsonl"

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if not runner.start("Resume import", work):
            dest.unlink(missing_ok=True)
            return back(notice="busy")
        log("web_upload", None, file=dest.name, bytes=len(data))
        return back(notice="started")

    @app.post("/kill")
    def kill(request: Request, csrf_token: str = Form(""), on: str = Form("0")):
        guard_post(request, csrf_token)
        con = db.connect(db_path)
        try:
            db.set_setting(con, "kill", "1" if on == "1" else "0")
        finally:
            con.close()
        log("web_kill", None, on=on == "1")
        return back(notice="kill_on" if on == "1" else "kill_off")

    @app.post("/job/{source}/{source_id:path}/tailor")
    def tailor(request: Request, source: str, source_id: str, csrf_token: str = Form("")):
        guard_post(request, csrf_token)
        res = resume_mod.load_resume(resume_json)
        if not res:
            return back(notice="no_resume")
        con = db.connect(db_path)
        try:
            found = db.get_job(con, source, source_id)
        finally:
            con.close()
        if not found:
            return back(notice="no_job")

        def work():
            got = tailor_resume(capped(), res, found, log, mode=cfg.get("tailor_mode", "rank"))
            if not got:
                return "tailoring failed or was refused; see logs/decisions.jsonl"
            path = write_tailored(*got, found)
            con2 = db.connect(db_path)
            try:
                db.set_resume(con2, found, path)
            finally:
                con2.close()
            return "tailored resume written"

        if not runner.start("Tailor resume", work):
            return back(notice="busy")
        log("web_tailor", found)
        return back(f"/job/{source}/{source_id}", "started")

    @app.post("/job/{source}/{source_id:path}/note")
    def note(request: Request, source: str, source_id: str, csrf_token: str = Form("")):
        """Draft a cover note for one job, on request only (one model call)."""
        guard_post(request, csrf_token)
        con = db.connect(db_path)
        try:
            found = db.get_job(con, source, source_id)
            raw = db.get_scored_job(con, source, source_id, waiting_days=max(cfg.get("max_age_days", 3),
                                                                               cfg.get("research_max_age_days", 7)))
        finally:
            con.close()
        if not found or raw is None:
            return back(notice="no_job")
        s = json.loads(raw["score_json"]) if raw["score_json"] else {}
        score = {"gaps": s.get("gaps", []), "matching_evidence": s.get("matching_evidence", [])}
        try:
            profile_text = load_profile(base / "profile.yaml", resume_json)
        except SystemExit:                               # neither profile.yaml nor resume.json
            return back(f"/job/{source}/{source_id}", "no_profile")

        def work():
            text = draft_note(capped(), profile_text, found, score, log)
            if not text:
                return "cover note failed or was refused; see logs/decisions.jsonl"
            path = write_draft(found, text)
            con2 = db.connect(db_path)
            try:
                db.set_draft(con2, found, path)
            finally:
                con2.close()
            return "cover note drafted"

        if not runner.start("Cover note", work):
            return back(notice="busy")
        log("web_note", found)
        return back(f"/job/{source}/{source_id}", "started")

    @app.get("/files/{path:path}")
    def files(path: str):
        target = (base / path).resolve()
        allowed = [(base / d).resolve() for d in DOWNLOAD_DIRS]
        if not any(a in target.parents for a in allowed) or not target.is_file():
            raise HTTPException(404, "No such file")
        return FileResponse(target)

    return app


app = create_app(demo=os.environ.get("DEMO") == "1")
