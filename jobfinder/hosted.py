"""Hosted app (JOBFINDER_MODE=hosted): invite-only, one account per person, Postgres with row-level security.
Everything a user sees comes from a connection made for that user (db.connect_pg), so the database refuses other users' rows
even if a route is wrong. No files are kept: resume facts and cover notes live in the database, the tailored resume is built in
memory and streamed, and an uploaded file is deleted as soon as it has been read. The app never sends or applies anywhere.
There is no /files route here."""
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import auth as auth_mod
from . import db, report
from . import resume as resume_mod
from .drafts import draft_note, slug
from .guard import Refused, UserCappedBackend
from .localtime import parse_zone, time_html
from .resume_docx import docx_bytes
from .tailor import tailor_resume
from .web import (HERE, MAGIC, MAX_UPLOAD, SECURITY_HEADERS, THEMES, card_rows, extra_hosts, safe_next)

SESSION_COOKIE = auth_mod.COOKIE
DEFAULT_USER_CAP = 20          # model calls per user per day (UTC); config.yaml user_daily_call_cap overrides
NOTICES = {"no_resume": "Upload a resume first.", "no_job": "That job is not in your matches.",
           "refused": "The daily limit for model calls is used up. Try again tomorrow.",
           "failed": "That did not work. Try again later.", "drafted": "Cover note drafted.",
           "imported": "Resume read.", "import_failed": "The resume could not be read. Check the file and try again."}
LOG_FIELDS = {"reason", "cap", "used_today", "mode", "attempt"}          # the only free fields a hosted log line may carry


def make_hosted_log(user_id, sink):
    """A logger for one user. It keeps event names, counts and short reasons, and a hash of the user id. It drops
    everything else (resume text, file names, errors that quote text), so a log never holds personal content."""
    who = auth_mod.user_hash(user_id)

    def log(event, job=None, **fields):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "user": who}
        if job is not None:
            rec.update(source=job.source, company=job.company, title=job.title)     # public job data, not the user's
        for k, v in fields.items():
            if k in LOG_FIELDS and isinstance(v, (int, str)) and (not isinstance(v, str) or len(v) <= 40):
                rec[k] = v
            elif isinstance(v, (bool, int)):
                rec[k] = v
        sink(json.dumps(rec, ensure_ascii=False))
    return log


def stdout_sink(line):
    print(line, file=sys.stdout, flush=True)


class Redirect(Exception):
    def __init__(self, to):
        self.to = to


class Session:
    def __init__(self, token, claims):
        self.token, self.user_id, self.email = token, claims["sub"], claims["email"]
        self.expires = int(claims["exp"])


def create_hosted_app(cfg, environ=None, auth=None, backend=None, log_sink=None):
    """cfg: config.yaml. environ: the settings (default os.environ). auth: an auth_mod.SupabaseAuth (tests pass one built on a
    fake Supabase). backend: a model backend, or a function returning one (default: BACKEND from the environment)."""
    env = environ if environ is not None else os.environ
    url = env["DATABASE_URL"]
    secret = env["SESSION_SECRET"]
    hosts = extra_hosts(env)
    sink = log_sink or stdout_sink
    auth = auth or auth_mod.SupabaseAuth(env["SUPABASE_URL"], env["SUPABASE_ANON_KEY"])
    user_cap = int(cfg.get("user_daily_call_cap", DEFAULT_USER_CAP))
    public = (env.get("PUBLIC_URL") or "").rstrip("/")
    app = FastAPI(title="Job matches", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    zone = parse_zone(None)                                      # UTC for now; a per-user zone comes with the profile
    templates.env.globals["when"] = lambda iso, with_time=True: Markup(time_html(iso, zone, with_time))
    templates.env.filters["fileurl"] = lambda p: str(p)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        response.headers["Cache-Control"] = "no-store"          # every page is personal
        return response

    @app.exception_handler(Redirect)
    async def redirect(request, exc):
        return RedirectResponse(exc.to, status_code=303)

    # ---- helpers -------------------------------------------------------------------------------------------------------
    def session(request):
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            return Session(token, auth.decode(token))
        except auth_mod.AuthError:
            return None

    def need(request, consent=True):
        """The signed-in user (and consent given), or a redirect to the page that fixes that."""
        s = session(request)
        if s is None:
            raise Redirect("/login")
        if consent:
            con = db.connect_pg(url, s.user_id)
            try:
                if not db.has_consent(con):
                    raise Redirect("/consent")
            finally:
                con.close()
        return s

    def guard_post(request, csrf, s=None):
        origin = request.headers.get("origin")
        if origin and (urlparse(origin).hostname or "") not in hosts:
            raise HTTPException(403, "Bad origin")
        if not auth_mod.csrf_ok(secret, s.token if s else "", csrf):
            raise HTTPException(403, "Bad token")

    def page(request, name, s=None, **ctx):
        cookie = request.cookies.get("theme")
        ctx.update(theme=cookie if cookie in THEMES else "", next_url=request.url.path, site_name=cfg.get("site_name") or "jobfinder",
                   csrf=auth_mod.csrf_token(secret, s.token if s else ""), status=None, calls=0, cap=user_cap, kill=False,
                   demo=False, hosted=True, account=s.email if s else None, progress_panel=False,
                   notice=NOTICES.get(request.query_params.get("notice", "")))
        ctx.setdefault("has_resume", False)
        return templates.TemplateResponse(request, name, ctx)

    def back(to="/", notice=""):
        return RedirectResponse(to + (f"?notice={notice}" if notice else ""), status_code=303)

    def user_con(s):
        return db.connect_pg(url, s.user_id)

    def backend_for(s, log):
        from .backends import get_backend
        return UserCappedBackend(backend or get_backend, lambda: user_con(s), user_cap, log)

    def redirect_to(request):
        return (public or str(request.base_url).rstrip("/")) + "/auth/callback"

    # ---- sign-in -------------------------------------------------------------------------------------------------------
    @app.get("/login")
    def login_form(request: Request):
        if session(request):
            return back("/")
        return page(request, "h_login.html", sent=False)

    @app.post("/login")
    def login(request: Request, csrf_token: str = Form(""), email: str = Form("")):
        guard_post(request, csrf_token)
        email = email.strip().lower()[:254]
        # The answer is the same for every address, so the page does not reveal who is invited.
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) and db.is_invited(url, email):
            try:
                auth.send_link(email, redirect_to(request))
            except Exception:  # noqa: BLE001 - the page must not change either way
                sink(json.dumps({"event": "login_link_failed"}))
        return page(request, "h_login.html", sent=True)

    @app.get("/auth/callback")
    def callback_page(request: Request, token_hash: str = ""):
        """Shows a button instead of using the link at once: mail scanners open links, and a used link would fail for the person."""
        return page(request, "h_callback.html", token_hash=token_hash[:512])

    @app.post("/auth/callback")
    def callback(request: Request, csrf_token: str = Form(""), token_hash: str = Form("")):
        guard_post(request, csrf_token)
        try:
            token = auth.verify(token_hash)
            claims = auth.decode(token)
        except auth_mod.AuthError:
            return page(request, "h_callback.html", token_hash="", failed=True)
        if not db.is_invited(url, claims["email"]):                # an account made some other way is still not an invite
            return page(request, "h_callback.html", token_hash="", failed=True)
        response = back("/")
        response.set_cookie(SESSION_COOKIE, token, max_age=max(60, int(claims["exp"] - datetime.now(timezone.utc).timestamp())),
                            path="/", httponly=True, secure=True, samesite="lax")
        make_hosted_log(claims["sub"], sink)("login")
        return response

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form("")):
        s = need(request, consent=False)
        guard_post(request, csrf_token, s)
        response = back("/login")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/theme")
    def set_theme(request: Request, csrf_token: str = Form(""), theme: str = Form(""), next: str = Form("/")):
        guard_post(request, csrf_token, session(request))
        if theme not in THEMES + ("device",):
            raise HTTPException(400, "Unknown theme")
        response = RedirectResponse(safe_next(next), status_code=303)
        if theme == "device":
            response.delete_cookie("theme", path="/")
        else:
            response.set_cookie("theme", theme, max_age=31536000, path="/", samesite="lax", httponly=True, secure=True)
        return response

    # ---- consent and account -------------------------------------------------------------------------------------------
    @app.get("/consent")
    def consent_form(request: Request):
        s = need(request, consent=False)
        return page(request, "h_consent.html", s)

    @app.post("/consent")
    def consent(request: Request, csrf_token: str = Form(""), agree: str = Form("")):
        s = need(request, consent=False)
        guard_post(request, csrf_token, s)
        if agree != "yes":
            return back("/consent")
        con = user_con(s)
        try:
            db.add_consent(con)
        finally:
            con.close()
        make_hosted_log(s.user_id, sink)("consent", version=1)
        return back("/")

    @app.get("/account")
    def account(request: Request):
        s = need(request, consent=False)
        con = user_con(s)
        try:
            return page(request, "h_account.html", s, has_resume=db.get_resume_facts(con) is not None, consent_version=db.CONSENT_VERSION)
        finally:
            con.close()

    @app.post("/account/delete")
    def delete_account(request: Request, csrf_token: str = Form(""), confirm: str = Form("")):
        s = need(request, consent=False)
        guard_post(request, csrf_token, s)
        if confirm.strip().lower() != "delete my account":
            return page(request, "h_account.html", s, wrong=True)
        con = user_con(s)
        try:
            db.delete_account_data(con)                        # every row of this user, in one transaction
        finally:
            con.close()
        make_hosted_log(s.user_id, sink)("account_deleted")
        response = page(request, "h_deleted.html")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ---- the product ---------------------------------------------------------------------------------------------------
    def listing(request, s):
        con = user_con(s)
        try:
            facts = db.get_resume_facts(con)
            rows = card_rows(report.rows(con, max(cfg.get("max_age_days", 3), cfg.get("research_max_age_days", 7))))
            return page(request, "h_home.html", s, rows=rows, has_resume=facts is not None,
                        resume_name=(facts or {}).get("name") or "", roles=len((facts or {}).get("roles", [])),
                        skills=len((facts or {}).get("skills", [])), threshold=cfg.get("score_threshold", 70))
        finally:
            con.close()

    @app.get("/")
    def home(request: Request):
        return listing(request, need(request))

    @app.get("/matches")
    def matches(request: Request):
        return listing(request, need(request))

    @app.get("/job/{source}/{source_id:path}")
    def job(request: Request, source: str, source_id: str):
        s = need(request)
        con = user_con(s)
        try:
            raw = db.get_scored_job(con, source, source_id,
                                    waiting_days=max(cfg.get("max_age_days", 3), cfg.get("research_max_age_days", 7)))
            note_text = db.get_draft_text(con, source, source_id) or ""
            has_resume = db.get_resume_facts(con) is not None
        finally:
            con.close()
        if raw is None:
            raise HTTPException(404, "No such job")
        return page(request, "job.html", s, r=card_rows([report.row_from(raw)])[0], description=raw["description"] or "",
                    draft_text=note_text, has_resume=has_resume)

    @app.post("/upload")
    async def upload(request: Request, csrf_token: str = Form(""), file: UploadFile = File(...)):
        s = need(request)
        guard_post(request, csrf_token, s)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in resume_mod.EXTS:
            raise HTTPException(400, "Use a PDF, DOCX, TXT or MD file")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File is over 5 MB")
        if not data or (ext in MAGIC and not data.startswith(MAGIC[ext])):
            raise HTTPException(400, "The file does not look like a " + ext[1:].upper())
        log = make_hosted_log(s.user_id, sink)
        notice = await run_in_threadpool(read_upload, s, data, ext, log)
        return back("/", notice)

    def read_upload(s, data, ext, log):
        """Write the upload to a temporary file, read it, delete it (also when reading fails), keep only the checked facts."""
        fd, name = tempfile.mkstemp(suffix=ext, prefix="upload-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                got = resume_mod.extract_facts(backend_for(s, log), name, log)
            except Refused:
                return "refused"
            except Exception:  # noqa: BLE001 - an unreadable or damaged file; the message could quote its text, so it is not logged
                return "import_failed"
        finally:
            Path(name).unlink(missing_ok=True)
        if got is None:
            return "import_failed"
        facts, dropped = got
        facts["source_file"] = ""                              # the file name can hold the person's name: not kept
        con = user_con(s)
        try:
            db.set_resume_facts(con, facts)
        finally:
            con.close()
        log("resume_imported", None, roles=len(facts["roles"]), skills=len(facts["skills"]), left_out=len(dropped))
        return "imported"

    @app.post("/job/{source}/{source_id:path}/tailor")
    def tailor(request: Request, source: str, source_id: str, csrf_token: str = Form("")):
        s = need(request)
        guard_post(request, csrf_token, s)
        con = user_con(s)
        try:
            facts = db.get_resume_facts(con)
            found = db.get_job(con, source, source_id)
        finally:
            con.close()
        if not facts:
            return back(notice="no_resume")
        if not found:
            return back(notice="no_job")
        log = make_hosted_log(s.user_id, sink)
        try:
            got = tailor_resume(backend_for(s, log), facts, found, log, mode=cfg.get("tailor_mode", "rank"))
        except Refused:
            return back(f"/job/{source}/{source_id}", "refused")
        if not got:
            return back(f"/job/{source}/{source_id}", "failed")
        log("web_tailor", found)
        name = f"{slug(found.company)}__{slug(found.title)}.docx"
        return Response(docx_bytes(got[0]), headers={"Content-Disposition": f'attachment; filename="{name}"'},
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.post("/job/{source}/{source_id:path}/note")
    def note(request: Request, source: str, source_id: str, csrf_token: str = Form("")):
        s = need(request)
        guard_post(request, csrf_token, s)
        con = user_con(s)
        try:
            facts = db.get_resume_facts(con)
            found = db.get_job(con, source, source_id)
            raw = db.get_scored_job(con, source, source_id, waiting_days=max(cfg.get("max_age_days", 3),
                                                                             cfg.get("research_max_age_days", 7)))
        finally:
            con.close()
        if not facts:
            return back(notice="no_resume")
        if not found or raw is None:
            return back(notice="no_job")
        sc = json.loads(raw["score_json"]) if raw["score_json"] else {}
        score = {"gaps": sc.get("gaps", []), "matching_evidence": sc.get("matching_evidence", [])}
        log = make_hosted_log(s.user_id, sink)
        try:
            text = draft_note(backend_for(s, log), yaml.safe_dump({"resume": resume_mod.profile_facts(facts)}, sort_keys=False,
                                                                  allow_unicode=True), found, score, log)
        except Refused:
            return back(f"/job/{source}/{source_id}", "refused")
        if not text:
            return back(f"/job/{source}/{source_id}", "failed")
        con = user_con(s)
        try:
            db.set_draft_text(con, found, text)
        finally:
            con.close()
        log("web_note", found)
        return back(f"/job/{source}/{source_id}", "drafted")

    return app
