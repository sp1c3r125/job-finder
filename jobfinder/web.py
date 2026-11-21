"""Private dashboard. Bind to 127.0.0.1 only; reach it through a private tunnel.
No SQL here: everything goes through db.py. The app never sends or applies anywhere:
every action only reads public job feeds, calls the model (behind the cap and kill switch) and writes local files."""
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, report
from . import demo as demo_mod
from . import resume as resume_mod
from .guard import CappedBackend, default_cap, killed
from .log import make_logger
from .resume_docx import write_tailored
from .runner import Runner
from .tailor import tailor_resume

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOWNLOAD_DIRS = ("drafts", "resumes")      # only these folders can be downloaded
MAX_UPLOAD = 5 * 1024 * 1024
MAGIC = {".pdf": b"%PDF", ".docx": b"PK"}  # cheap wrong-type check for binary formats
NOTICES = {"started": "Started. This page refreshes while it runs.",
           "busy": "Something is already running. Wait for it to finish.",
           "kill_on": "Kill switch is ON: no model calls will be made.", "kill_off": "Kill switch is off.",
           "no_resume": "Import a resume first.", "no_job": "That job is not in the database."}


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
    if demo:
        db_path = db_path or str(ROOT / demo_mod.DEMO_DB)
        demo_mod.seed(db_path)
    db_path = db_path or os.environ.get("JOBFINDER_DB", str(ROOT / "jobs.db"))
    base = Path(base_dir or ROOT)
    log = make_logger(log_path)
    runner = Runner()
    token = secrets.token_urlsafe(32)
    hosts = cfg.get("allowed_hosts") or ["127.0.0.1", "localhost"]
    cap = int(cfg.get("daily_call_cap", default_cap()))
    app = FastAPI(title="Job matches", docs_url=None, redoc_url=None, openapi_url=None)
    # Host allow-list blocks DNS rebinding. Add your tunnel hostname under allowed_hosts in config.yaml.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fileurl"] = lambda p: str(p).replace(chr(92), "/")

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

    def page(request, name, **ctx):
        con = db.connect(db_path)
        try:
            ctx.update(csrf=token, status=runner.status(), calls=db.calls_today(con), cap=cap,
                       kill=killed(con, kill_file), demo=demo,
                       has_resume=not demo and resume_mod.load_resume() is not None,
                       notice=NOTICES.get(request.query_params.get("notice", "")))
        finally:
            con.close()
        return templates.TemplateResponse(request, name, ctx)

    def back(to="/", notice=""):
        return RedirectResponse(to + (f"?notice={notice}" if notice else ""), status_code=303)

    def default_run():
        from . import main
        rc = main.run(["--db", db_path], backend=capped())
        return "run finished" if rc == 0 else f"run exited with {rc}"

    @app.get("/")
    def index(request: Request):
        con = db.connect(db_path)
        try:
            rs = report.rows(con)
        finally:
            con.close()
        return page(request, "index.html", rows=rs, threshold=cfg.get("score_threshold", 70))

    @app.get("/job/{source}/{source_id:path}")
    def job(request: Request, source: str, source_id: str):
        con = db.connect(db_path)
        try:
            raw = db.get_scored_job(con, source, source_id)
        finally:
            con.close()
        if raw is None:
            raise HTTPException(404, "No such job")
        return page(request, "job.html", r=report.row_from(raw), description=raw["description"] or "",
                    location=raw["location"] or "")

    @app.get("/about")
    def about(request: Request):
        return page(request, "about.html")

    if demo:
        # Every write route exists only to answer 405, so nothing can change data on the public demo.
        def not_allowed():
            raise HTTPException(405, "Read-only demo")

        for route in ("/run", "/upload", "/kill", "/job/{source}/{source_id:path}/tailor"):
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
                got = resume_mod.import_resume(capped(), dest, log)
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
        res = resume_mod.load_resume()
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
            got = tailor_resume(capped(), res, found, log)
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

    @app.get("/files/{path:path}")
    def files(path: str):
        target = (base / path).resolve()
        allowed = [(base / d).resolve() for d in DOWNLOAD_DIRS]
        if not any(a in target.parents for a in allowed) or not target.is_file():
            raise HTTPException(404, "No such file")
        return FileResponse(target)

    return app


app = create_app(demo=os.environ.get("DEMO") == "1")
