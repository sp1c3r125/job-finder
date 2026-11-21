"""Private dashboard. Read-only in M1. Bind to 127.0.0.1 only; reach it through a private tunnel.
No SQL here: everything goes through db.py. The app never sends or applies anywhere."""
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOWNLOAD_DIRS = ("drafts", "resumes")      # only these folders can be downloaded


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_app(db_path=None, cfg=None, base_dir=None):
    cfg = cfg if cfg is not None else load_config()
    db_path = db_path or os.environ.get("JOBFINDER_DB", str(ROOT / "jobs.db"))
    base = Path(base_dir or ROOT)
    app = FastAPI(title="Job matches", docs_url=None, redoc_url=None, openapi_url=None)
    # Host allow-list blocks DNS rebinding. Add your tunnel hostname under allowed_hosts in config.yaml.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.get("allowed_hosts") or ["127.0.0.1", "localhost"])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fileurl"] = lambda p: str(p).replace(chr(92), "/")

    def page(request, name, **ctx):
        return templates.TemplateResponse(request, name, ctx)

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

    @app.get("/files/{path:path}")
    def files(path: str):
        target = (base / path).resolve()
        allowed = [(base / d).resolve() for d in DOWNLOAD_DIRS]
        if not any(a in target.parents for a in allowed) or not target.is_file():
            raise HTTPException(404, "No such file")
        return FileResponse(target)

    return app


app = create_app()
