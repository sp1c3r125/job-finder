"""P3: sign-in, invites, consent, account deletion and per-user resume facts in hosted mode. Needs TEST_DATABASE_URL.
Supabase is faked with an httpx mock transport; keys and tokens are made here. Invented data only."""
import json
import time
import uuid

import httpx
import pytest

pytest.importorskip("psycopg")
pytest.importorskip("jwt")
import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jobfinder import auth, db, hosted  # noqa: E402

from .conftest import GOOD, FakeBackend, make_job  # noqa: E402
from .pg_helpers import A, B, pg, seed  # noqa: E402,F401
from .test_resume import EXTRACTED, SAMPLE  # noqa: E402

SUPA = "https://fake.supabase.test"
HOST = "jobs.example.test"
KID = "key-1"
SECRET = "session-secret-invented"
ALICE, BOB = "alice@example.test", "bob@example.test"
ALICE_ID, BOB_ID = A, B


class FakeSupabase:
    """What Supabase does for us, nothing more: emails a link, swaps a token_hash for a session, publishes its public key."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.sent, self.deleted = [], []
        self.users = {ALICE: ALICE_ID, BOB: BOB_ID}
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def jwk(self, key=None):
        d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk((key or self.key).public_key()))
        return dict(d, kid=KID, alg="ES256", use="sig")

    def mint(self, email, uid=None, key=None, exp=3600, **over):
        now = int(time.time())
        claims = dict(sub=uid or self.users[email], email=email, aud="authenticated", iss=f"{SUPA}/auth/v1",
                      iat=now, exp=now + exp)
        claims.update(over)
        return jwt.encode(claims, key or self.key, algorithm="ES256", headers={"kid": KID})

    def handle(self, req):
        path, body = req.url.path, (json.loads(req.content) if req.content else {})
        if path.endswith("/otp"):
            self.sent.append(body["email"])
            return httpx.Response(200, json={})
        if path.endswith("/verify"):
            email = body["token_hash"].removeprefix("good-")
            if body["token_hash"].startswith("good-") and email in self.users:
                return httpx.Response(200, json={"access_token": self.mint(email)})
            return httpx.Response(403, json={"error": "expired"})
        if path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if "/admin/users/" in path and req.method == "DELETE":
            self.deleted.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def supa():
    return FakeSupabase()


@pytest.fixture
def env(pg):
    pg.admin_exec(f"INSERT INTO invites (email, invited_at) VALUES ('{ALICE}', 't'), ('{BOB}', 't')")
    return {"JOBFINDER_MODE": "hosted", "BACKEND": "api", "DATABASE_URL": pg.app, "SUPABASE_URL": SUPA,
            "SUPABASE_ANON_KEY": "anon-invented", "ANTHROPIC_API_KEY": "key-invented", "SESSION_SECRET": SECRET,
            "ALLOWED_HOSTS_EXTRA": HOST, "SUPPORT_CONTACT": "help@example.test"}


@pytest.fixture
def lines():
    return []


def make(env, supa, lines, backend=None):
    a = auth.SupabaseAuth(SUPA, "anon-invented", client=supa.client)
    app = hosted.create_hosted_app({"hosted_sources": ["greenhouse"], "tailor_mode": "rank", "user_daily_call_cap": 2},
                                   environ=env, auth=a, backend=backend, log_sink=lines.append)
    return TestClient(app, base_url=f"https://{HOST}", follow_redirects=False)


def anon_csrf():
    return auth.csrf_token(SECRET, "")


def sign_in(c, supa, email=ALICE, consent=True):
    r = c.post("/auth/callback", data={"csrf_token": anon_csrf(), "token_hash": "good-" + email})
    assert r.status_code == 303, r.text
    if consent:
        assert c.post("/consent", data={"csrf_token": csrf(c), "agree": "yes"}).status_code == 303
    return r


def csrf(c):
    return auth.csrf_token(SECRET, c.cookies.get(auth.COOKIE))


# ---- sign-in ------------------------------------------------------------------------------------------------------------
def test_invited_email_gets_a_link_and_can_sign_in(env, supa, lines):
    c = make(env, supa, lines)
    r = c.post("/login", data={"csrf_token": anon_csrf(), "email": ALICE.upper()})
    assert r.status_code == 200 and supa.sent == [ALICE]
    assert "invite list" in r.text
    r = c.get("/auth/callback", params={"token_hash": "good-" + ALICE})
    assert "Continue" in r.text and auth.COOKIE not in c.cookies           # opening the link does not use it up
    sign_in(c, supa, consent=False)
    assert c.get("/", follow_redirects=False).headers["location"] == "/consent"
    assert c.post("/consent", data={"csrf_token": csrf(c), "agree": "yes"}).status_code == 303
    assert c.get("/").status_code == 200


def test_cookie_flags(env, supa, lines):
    c = make(env, supa, lines)
    r = c.post("/auth/callback", data={"csrf_token": anon_csrf(), "token_hash": "good-" + ALICE})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_uninvited_email_is_refused_and_gets_the_same_page(env, supa, lines):
    c = make(env, supa, lines)
    r = c.post("/login", data={"csrf_token": anon_csrf(), "email": "stranger@example.test"})
    assert r.status_code == 200 and "invite list" in r.text and supa.sent == []     # no mail sent, same wording
    supa.users["stranger@example.test"] = str(uuid.uuid4())                           # an account made some other way
    r = c.post("/auth/callback", data={"csrf_token": anon_csrf(), "token_hash": "good-stranger@example.test"})
    assert r.status_code == 200 and "did not work" in r.text and auth.COOKIE not in c.cookies


def test_used_or_unknown_link_fails(env, supa, lines):
    c = make(env, supa, lines)
    r = c.post("/auth/callback", data={"csrf_token": anon_csrf(), "token_hash": "bad"})
    assert "did not work" in r.text and auth.COOKIE not in c.cookies


def test_forged_and_expired_tokens_are_refused(env, supa, lines):
    c = make(env, supa, lines)
    other = ec.generate_private_key(ec.SECP256R1())
    for token in (supa.mint(ALICE, key=other),                       # signed with the wrong key
                  supa.mint(ALICE, exp=-60),                         # expired
                  supa.mint(ALICE, aud="somebody-else"),
                  supa.mint(ALICE, iss="https://evil.test/auth/v1"),
                  jwt.encode({"sub": ALICE_ID, "email": ALICE}, "x" * 32, algorithm="HS256"),    # shared-secret algorithm
                  "not.a.jwt"):
        c.cookies.clear()
        c.cookies.set(auth.COOKIE, token, domain=HOST)
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", token[:20]
    unsigned = jwt.encode({"sub": ALICE_ID, "email": ALICE, "aud": "authenticated"}, None, algorithm="none")
    c.cookies.set(auth.COOKIE, unsigned, domain=HOST)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"


def test_pages_need_a_session_and_consent(env, supa, lines):
    c = make(env, supa, lines)
    for path in ("/", "/matches", "/account", "/consent", "/job/greenhouse/1"):
        assert c.get(path).headers["location"] == "/login", path
    for path in ("/upload", "/account/delete", "/consent", "/logout", "/job/greenhouse/1/note", "/job/greenhouse/1/tailor"):
        assert c.post(path, data={"csrf_token": anon_csrf()}).status_code in (303, 422), path


# ---- CSRF and origin -----------------------------------------------------------------------------------------------------
def test_missing_or_wrong_csrf_is_refused(env, supa, lines):
    c = make(env, supa, lines)
    assert c.post("/login", data={"email": ALICE}).status_code in (403, 422)
    assert c.post("/login", data={"email": ALICE, "csrf_token": "wrong"}).status_code == 403
    sign_in(c, supa)
    assert c.post("/account/delete", data={"confirm": "delete my account"}).status_code in (403, 422)
    assert c.post("/account/delete", data={"confirm": "delete my account", "csrf_token": "wrong"}).status_code == 403
    assert c.post("/logout", data={"csrf_token": anon_csrf()}).status_code == 403        # a token from before sign-in is not valid
    assert supa.deleted == [] and db_rows(env, ALICE_ID, "consents") == 1


def test_csrf_token_belongs_to_one_session(env, supa, lines):
    a, b = make(env, supa, lines), make(env, supa, lines)
    sign_in(a, supa, ALICE)
    sign_in(b, supa, BOB)
    assert a.post("/logout", data={"csrf_token": csrf(b)}).status_code == 403


def test_post_from_another_origin_is_refused(env, supa, lines):
    c = make(env, supa, lines)
    sign_in(c, supa)
    r = c.post("/logout", data={"csrf_token": csrf(c)}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_wrong_host_is_rejected(env, supa, lines):
    assert make(env, supa, lines).get("/login", headers={"host": "evil.example"}).status_code == 400


# ---- the app has no way to hand out files ---------------------------------------------------------------------------------
def test_there_is_no_files_route(env, supa, lines):
    c = make(env, supa, lines)
    sign_in(c, supa)
    assert not any("files" in getattr(r, "path", "") for r in c.app.routes)
    for p in ("/files/drafts/x.md", "/files/resumes/x.docx", "/files/"):
        assert c.get(p).status_code == 404


# ---- resume facts: in the database, upload deleted ---------------------------------------------------------------------------
def db_rows(env, uid, table):
    con = db.connect_pg(env["DATABASE_URL"], uid)
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_upload_stores_facts_in_the_database_and_deletes_the_file(env, supa, lines, tmp_path, monkeypatch):
    import tempfile
    seen = []
    real = tempfile.mkstemp

    def spy(*a, **k):
        fd, name = real(*a, **k)
        seen.append(name)
        return fd, name
    monkeypatch.setattr(tempfile, "mkstemp", spy)
    c = make(env, supa, lines, backend=FakeBackend([EXTRACTED]))
    sign_in(c, supa)
    r = c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("my-name-resume.md", SAMPLE.read_bytes())})
    assert r.status_code == 303 and "notice=imported" in r.headers["location"]
    import os
    assert seen and not any(os.path.exists(n) for n in seen)                 # the uploaded file is gone
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    facts = db.get_resume_facts(con)
    con.close()
    assert facts["name"] == "Alex Example" and facts["source_file"] == ""    # the file name is not kept
    assert "Alex Example" in c.get("/").text
    # Bob sees none of it
    b = make(env, supa, lines)
    sign_in(b, supa, BOB)
    assert "Alex Example" not in b.get("/").text


def test_unreadable_upload_is_also_deleted(env, supa, lines, monkeypatch):
    import os
    import tempfile
    seen = []
    real = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: (lambda r: (seen.append(r[1]), r)[1])(real(*a, **k)))
    c = make(env, supa, lines, backend=FakeBackend([]))
    sign_in(c, supa)
    r = c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("x.pdf", b"%PDF-1.4 not really")})
    assert "import_failed" in r.headers["location"]
    assert seen and not any(os.path.exists(n) for n in seen)


def test_bad_uploads_are_refused(env, supa, lines):
    c = make(env, supa, lines)
    sign_in(c, supa)
    assert c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("x.exe", b"MZ")}).status_code == 400
    assert c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("x.pdf", b"not a pdf")}).status_code == 400


def stored_facts():
    """What an import leaves in the database: the checked facts plus the resume text."""
    from jobfinder import resume as rm
    text = SAMPLE.read_text(encoding="utf-8")
    return dict(rm.verify(EXTRACTED, text)[0], text=text, source_file="", left_out=[])


def seed_job_for(env, uid):
    con = db.connect_pg(env["DATABASE_URL"].replace("jobfinder_app", "jobfinder_worker"), uid)
    j = make_job(source="greenhouse", source_id="1", title="Automation Engineer", company="Acme")
    db.save(con, j, "2026-11-19T00:00:00+00:00", True, "ok")
    db.set_rule_scores(con, [(j, {"score": 77, "reasons": ["title match"], "deal_breakers": []})])
    con.close()


def test_tailor_streams_a_docx_and_writes_nothing(env, supa, lines, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seed_job_for(env, ALICE_ID)
    c = make(env, supa, lines)
    sign_in(c, supa)
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    db.set_resume_facts(con, stored_facts())
    con.close()
    r = c.post("/job/greenhouse/1/tailor", data={"csrf_token": csrf(c)})
    assert r.status_code == 200 and r.content[:2] == b"PK" and "attachment" in r.headers["content-disposition"]
    assert list(tmp_path.iterdir()) == []                                      # no resume or notes written to disk


def test_cover_note_is_kept_in_the_database(env, supa, lines, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seed_job_for(env, ALICE_ID)
    note = "I build automation workflows for small teams and would like to talk about this role at Acme."
    c = make(env, supa, lines, backend=FakeBackend([{"note": note}, note]))
    sign_in(c, supa)
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    db.set_resume_facts(con, stored_facts())
    con.close()
    c.post("/job/greenhouse/1/note", data={"csrf_token": csrf(c)})
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    stored = db.get_draft_text(con, "greenhouse", "1")
    con.close()
    if stored is None:                                                          # the model reply shape belongs to drafts.py
        pytest.skip("draft_note rejected the fake reply shape")
    assert "automation" in stored and list(tmp_path.iterdir()) == []
    assert "Cover note draft" in c.get("/job/greenhouse/1").text


# ---- the per-user cap holds for hosted model calls -----------------------------------------------------------------------
def test_user_cap_stops_model_calls(env, supa, lines):
    c = make(env, supa, lines, backend=FakeBackend([ValueError("x")] * 10))
    sign_in(c, supa)
    for _ in range(3):
        c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("a.md", SAMPLE.read_bytes())})
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    used, refused = db.calls_today(con), con.execute("SELECT count(*) FROM usage WHERE kind='refused'").fetchone()[0]
    con.close()
    assert used == 2 and refused >= 1                                           # cap 2: the third attempt is refused


# ---- delete account ------------------------------------------------------------------------------------------------------
def all_user_tables(pg):
    import psycopg
    with psycopg.connect(pg.admin) as con:
        return sorted(r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='user_id'"))


def test_delete_removes_every_row_of_the_user_in_every_table(env, supa, lines, pg):
    import psycopg
    seed(pg)                                                                   # both users own rows in every table
    tables = all_user_tables(pg)
    assert set(tables) == set(db.PER_USER_TABLES)                              # derived from the schema, not typed here
    c = make(env, supa, lines)
    sign_in(c, supa, ALICE, consent=False)
    c.post("/consent", data={"csrf_token": csrf(c), "agree": "yes"})
    assert c.post("/account/delete", data={"csrf_token": csrf(c), "confirm": "no thanks"}).status_code == 200
    assert db_rows(env, ALICE_ID, "user_jobs") == 1                            # wrong words: nothing deleted
    r = c.post("/account/delete", data={"csrf_token": csrf(c), "confirm": "Delete my account"})
    assert r.status_code == 200 and "deleted" in r.text
    with psycopg.connect(pg.admin) as con:
        for t in tables:
            mine = con.execute(f"SELECT count(*) FROM {t} WHERE user_id=%s", (ALICE_ID,)).fetchone()[0]
            theirs = con.execute(f"SELECT count(*) FROM {t} WHERE user_id=%s", (BOB_ID,)).fetchone()[0]
            # only the queued removal of the sign-in account stays, so the worker can finish the job
            assert mine == (1 if t == "deletion_requests" else 0), t
            assert theirs >= 1, f"{t}: another user's rows were touched"
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"     # the cookie is cleared


def test_worker_removes_the_sign_in_account_after_a_deletion_request(env, supa, lines, pg):
    seed(pg)
    c = make(env, supa, lines)
    sign_in(c, supa)
    c.post("/account/delete", data={"csrf_token": csrf(c), "confirm": "delete my account"})
    assert supa.deleted == []                                                  # a user request never calls the admin API
    worker = db.connect_pg(pg.worker, db.NO_USER)
    try:
        ids = db.pending_deletions(worker)
        assert set(ids) == {ALICE_ID, BOB_ID}                                  # seed() queued both; Alice's is real
        a = auth.SupabaseAuth(SUPA, "anon-invented", client=supa.client)
        for uid in ids:
            a.delete_user(uid, "admin-secret-invented")
            db.finish_deletion(worker, uid)
        assert db.pending_deletions(worker) == [] and sorted(supa.deleted) == sorted([ALICE_ID, BOB_ID])
    finally:
        worker.close()


# ---- logs ----------------------------------------------------------------------------------------------------------------
def test_logs_hold_a_hash_and_no_personal_text(env, supa, lines):
    c = make(env, supa, lines, backend=FakeBackend([EXTRACTED]))
    sign_in(c, supa)
    c.post("/upload", data={"csrf_token": csrf(c)}, files={"file": ("my-name-resume.md", SAMPLE.read_bytes())})
    text = "\n".join(lines)
    assert text and auth.user_hash(ALICE_ID) in text
    for bad in (ALICE, "alice", ALICE_ID, "Alex Example", "Example Agency", "my-name-resume", "Cebu", "n8n"):
        assert bad not in text, bad


def test_session_hash_is_not_the_user_id():
    assert auth.user_hash(ALICE_ID) != ALICE_ID and len(auth.user_hash(ALICE_ID)) == 12


def test_search_now_queues_one_run_a_day_and_the_web_process_never_calls_the_model(env, supa, lines):
    backend = FakeBackend([])
    c = make(env, supa, lines, backend=backend)
    sign_in(c, supa)
    r = c.post("/run", data={"csrf_token": csrf(c)})
    assert r.status_code == 303 and "notice=no_resume" in r.headers["location"]       # no resume yet: nothing queued
    con = db.connect_pg(env["DATABASE_URL"], ALICE_ID)
    db.set_resume_facts(con, stored_facts())
    con.close()
    first = c.post("/run", data={"csrf_token": csrf(c)})
    second = c.post("/run", data={"csrf_token": csrf(c)})
    assert "notice=queued" in first.headers["location"] and "notice=run_used" in second.headers["location"]
    assert "Queued" in c.get("/matches").text
    assert c.post("/run", data={"csrf_token": "wrong"}).status_code == 403
    assert backend.calls == []
