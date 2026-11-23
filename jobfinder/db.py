"""All SQL lives here. Nothing else in the project may import sqlite3 or write SQL."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Job

LOCAL_USER = "local"      # the user id in local mode: one person, no login

# `jobs` is the shared catalogue. Everything that belongs to one user carries a user_id:
# `user_jobs` (did the job pass this user's pre-filter, its rule score), `job_scores` (what the model said, the draft,
# the tailored resume), `usage` (model calls, for the cap) and `settings`. A connection has one user_id
# (`connect(path, user_id)`), and every function below reads and writes only that user's rows.
# Schema changes are numbered migrations in MIGRATIONS, applied once and recorded in `schema_version`.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
  source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
  location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
  first_seen TEXT, passed INTEGER, rule TEXT, norm_key TEXT, expires TEXT DEFAULT '', undated INTEGER DEFAULT 0,
  rule_score INTEGER, rule_json TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS job_scores (
  source TEXT, source_id TEXT, score_json TEXT, draft_path TEXT, resume_path TEXT, scored_at TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS usage (day TEXT, ts TEXT, kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class Connection(sqlite3.Connection):
    """A SQLite connection that knows whose rows it may touch."""
    user_id = LOCAL_USER
    dialect = "sqlite"


def _uid(con):
    return getattr(con, "user_id", LOCAL_USER)


def _migrate_1(con):
    """The schema as it was before users, plus the old in-place fixes for jobs.db files made earlier."""
    for stmt in SCHEMA_V1.split(";"):                           # not executescript: it would commit the migration's transaction
        if stmt.strip():
            con.execute(stmt)
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    if "expires" not in cols:                                   # jobs.db made before expiry was stored
        con.execute("ALTER TABLE jobs ADD COLUMN expires TEXT DEFAULT ''")
    if "undated" not in cols:                                   # jobs.db made before undated research results
        con.execute("ALTER TABLE jobs ADD COLUMN undated INTEGER DEFAULT 0")
    if "rule_score" not in cols:                                # jobs.db made before the rule score
        con.execute("ALTER TABLE jobs ADD COLUMN rule_score INTEGER")
        con.execute("ALTER TABLE jobs ADD COLUMN rule_json TEXT")
    if "scored_at" not in {r[1] for r in con.execute("PRAGMA table_info(job_scores)")}:
        con.execute("ALTER TABLE job_scores ADD COLUMN scored_at TEXT")
    if {"score_json", "draft_path", "resume_path"} <= cols:     # jobs.db made before the split: copy once
        con.execute("INSERT OR IGNORE INTO job_scores (source,source_id,score_json,draft_path,resume_path) SELECT source, source_id, score_json, draft_path, resume_path "
                    "FROM jobs WHERE score_json IS NOT NULL OR resume_path IS NOT NULL")


def _migrate_2(con):
    """user_id. Existing rows become user 'local'. The old jobs.passed/rule/rule_score/rule_json columns are copied to
    user_jobs and then left unused in place (nothing is dropped, so nothing can be lost)."""
    con.execute("CREATE TABLE IF NOT EXISTS user_jobs (user_id TEXT NOT NULL, source TEXT, source_id TEXT, passed INTEGER, "
                "rule TEXT, rule_score INTEGER, rule_json TEXT, PRIMARY KEY (user_id, source, source_id))")
    have = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}      # a very old jobs.db may lack some of them
    pick = ", ".join(c if c in have else "NULL" for c in ("passed", "rule", "rule_score", "rule_json"))
    con.execute("INSERT OR IGNORE INTO user_jobs (user_id,source,source_id,passed,rule,rule_score,rule_json) "
                f"SELECT ?, source, source_id, {pick} FROM jobs", (LOCAL_USER,))
    con.execute("CREATE TABLE job_scores_v2 (user_id TEXT NOT NULL, source TEXT, source_id TEXT, score_json TEXT, draft_path TEXT, "
                "resume_path TEXT, scored_at TEXT, PRIMARY KEY (user_id, source, source_id))")
    con.execute("INSERT INTO job_scores_v2 SELECT ?, source, source_id, score_json, draft_path, resume_path, scored_at FROM job_scores",
                (LOCAL_USER,))
    con.execute("DROP TABLE job_scores")
    con.execute("ALTER TABLE job_scores_v2 RENAME TO job_scores")
    con.execute("ALTER TABLE usage ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
    con.execute("CREATE TABLE settings_v2 (user_id TEXT NOT NULL, key TEXT, value TEXT, PRIMARY KEY (user_id, key))")
    con.execute("INSERT INTO settings_v2 SELECT ?, key, value FROM settings", (LOCAL_USER,))
    con.execute("DROP TABLE settings")
    con.execute("ALTER TABLE settings_v2 RENAME TO settings")


MIGRATIONS = [(1, _migrate_1), (2, _migrate_2)]


def schema_version(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
        return 0
    return con.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]


def _backup(con, path, version):
    """Before a migration rewrites tables in a database that already has data, keep a copy next to it (once)."""
    if path == ":memory:" or not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        return
    dest = f"{path}.bak-v{version}"
    if not os.path.exists(dest):
        out = sqlite3.connect(dest)
        try:
            con.backup(out)
        finally:
            out.close()


def migrate(con, path=":memory:"):
    have = schema_version(con)
    pending = [(v, fn) for v, fn in MIGRATIONS if v > have]
    if not pending:
        return
    if have < 2:
        _backup(con, path, 2)
    for version, fn in pending:
        con.execute("BEGIN")                                    # one transaction per migration, schema change and version together
        try:
            fn(con)
            con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
            con.execute("INSERT INTO schema_version (version, applied_at) VALUES (?,?)",
                        (version, datetime.now(timezone.utc).isoformat()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def connect(path="jobs.db", user_id=LOCAL_USER):
    con = sqlite3.connect(path, factory=Connection)
    con.user_id = user_id
    migrate(con, str(path))
    return con


def is_seen(con, job):
    return con.execute("SELECT 1 FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)).fetchone() is not None


def save(con, job, now_iso, passed, rule, key=""):
    con.execute(
        "INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,also_seen_on,"
        "first_seen,norm_key,expires,undated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job.source, job.source_id, job.company, job.title, job.url, job.posted, job.location,
         None if job.remote is None else int(job.remote), job.description, json.dumps(job.also_seen_on),
         now_iso, key, job.expires, int(job.undated)),
    )
    _set_user_job(con, job, passed, rule)
    con.commit()


def _set_user_job(con, job, passed, rule):
    con.execute("INSERT INTO user_jobs (user_id,source,source_id,passed,rule) VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id,source,source_id) DO UPDATE SET passed=excluded.passed, rule=excluded.rule",
                (_uid(con), job.source, job.source_id, int(passed), rule))


def requalify(con, job, posted, undated, passed, rule):
    """Re-decide one stored job: new posted date, undated flag and pre-filter result."""
    con.execute("UPDATE jobs SET posted=?, undated=? WHERE source=? AND source_id=?",
                (posted, int(undated), job.source, job.source_id))
    _set_user_job(con, job, passed, rule)
    con.commit()


def set_score(con, job, score, draft_path=None, resume_path=None):
    con.execute("INSERT INTO job_scores (user_id,source,source_id,score_json,draft_path,resume_path,scored_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id,source,source_id) DO UPDATE SET score_json=excluded.score_json, "
                "draft_path=excluded.draft_path, resume_path=excluded.resume_path, scored_at=excluded.scored_at",
                (_uid(con), job.source, job.source_id, json.dumps(score), draft_path, resume_path,
                 datetime.now(timezone.utc).isoformat()))
    con.commit()


def set_draft(con, job, path):
    con.execute("INSERT INTO job_scores (user_id,source,source_id,draft_path) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,source,source_id) DO UPDATE SET draft_path=excluded.draft_path",
                (_uid(con), job.source, job.source_id, path))
    con.commit()


def model_scored_today(con):
    """How many jobs the model scored today (UTC day)."""
    day = datetime.now(timezone.utc).date().isoformat()
    return con.execute("SELECT COUNT(*) FROM job_scores WHERE user_id=? AND score_json IS NOT NULL AND substr(scored_at,1,10)=?",
                       (_uid(con), day)).fetchone()[0]


def passed_jobs(con):
    """Every stored job that passed the pre-filter, as Jobs (rule_score filled in when stored)."""
    out = []
    for r in con.execute("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.location,j.remote,j.description,"
                         "j.expires,u.rule_score FROM jobs j JOIN user_jobs u ON u.source=j.source AND u.source_id=j.source_id "
                         "WHERE u.user_id=? AND u.passed=1", (_uid(con),)).fetchall():
        out.append(Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or "", rule_score=r[10]))
    return out


def set_rule_scores(con, items):
    """items: [(job, rule_score_dict)]. One transaction."""
    con.executemany("UPDATE user_jobs SET rule_score=?, rule_json=? WHERE user_id=? AND source=? AND source_id=?",
                    [(r["score"], json.dumps(r), _uid(con), j.source, j.source_id) for j, r in items])
    con.commit()


def set_resume(con, job, path):
    con.execute("INSERT INTO job_scores (user_id,source,source_id,resume_path) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,source,source_id) DO UPDATE SET resume_path=excluded.resume_path",
                (_uid(con), job.source, job.source_id, path))
    con.commit()


def find_job(con, url):
    """The stored job with this link, or None."""
    row = con.execute("SELECT source,source_id,company,title,url,posted,location,remote,description "
                      "FROM jobs WHERE url=? LIMIT 1", (url,)).fetchone()
    if not row:
        return None
    return Job(*row[:7], None if row[7] is None else bool(row[7]), row[8])


def unscored_passed(con, now, max_age_days, source_max_age=None):
    """Every stored job that passed the pre-filter, has no job_scores row, is not expired and is not older than
    max_age_days. Oldest first_seen first. `now` is a timezone-aware datetime."""
    cur = con.execute(
        "SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.location,j.remote,j.description,j.expires,"
        "u.rule_score FROM jobs j JOIN user_jobs u ON u.source=j.source AND u.source_id=j.source_id AND u.user_id=? "
        "LEFT JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id AND s.user_id=? "
        "WHERE u.passed=1 AND s.source_id IS NULL AND (j.expires IS NULL OR j.expires='' OR j.expires>?) "
        "ORDER BY j.first_seen", (_uid(con), _uid(con), now.isoformat()))
    out = []
    for r in cur.fetchall():
        cutoff = now - timedelta(days=(source_max_age or {}).get(r[0], max_age_days))
        try:
            if not r[5] or datetime.fromisoformat(r[5]) < cutoff:
                continue
        except ValueError:
            continue
        out.append(Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or "", rule_score=r[10]))
    return out


def merge_duplicate(con, job, key):
    """If another source already stored this company+title, record this source on that row. True if merged."""
    row = con.execute("SELECT source, source_id, also_seen_on FROM jobs WHERE norm_key=? ORDER BY first_seen, posted LIMIT 1", (key,)).fetchone()
    if not row:
        return False
    source, source_id, also = row[0], row[1], json.loads(row[2] or "[]")
    if job.source == source:
        return False                       # same source: a separate posting, not a cross-source duplicate
    if job.source not in also:
        also.append(job.source)
        con.execute("UPDATE jobs SET also_seen_on=? WHERE source=? AND source_id=?", (json.dumps(also), source, source_id))
        con.commit()
    return True


WAITING_DAYS = 7      # a job with only a rule score is listed this long after it was posted

_SCORED = ("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,u.rule AS rule,s.score_json,s.draft_path,"
           "j.also_seen_on,s.resume_path,j.location,j.expires,j.undated,u.rule_score AS rule_score,u.rule_json AS rule_json "
           "FROM jobs j LEFT JOIN user_jobs u ON u.source=j.source AND u.source_id=j.source_id AND u.user_id=? "
           "LEFT JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id AND s.user_id=? "
           "WHERE (s.score_json IS NOT NULL OR (u.passed=1 AND u.rule_score IS NOT NULL AND j.posted>?)) "
           "AND (j.expires IS NULL OR j.expires='' OR j.expires>?)")


def _since(now_iso, days):
    return (datetime.fromisoformat(now_iso) - timedelta(days=days)).isoformat()


def scored_jobs(con, now_iso=None, waiting_days=WAITING_DAYS):
    """Raw rows, as dicts, for every job the dashboard lists: model-scored jobs, and jobs that passed the pre-filter and
    have only a rule score (posted within waiting_days). Expired jobs (Himalayas expiryDate) are left out."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED, (_uid(con), _uid(con), _since(now_iso, waiting_days), now_iso))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def get_scored_job(con, source, source_id, now_iso=None, waiting_days=WAITING_DAYS):
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED + " AND j.source=? AND j.source_id=?", (_uid(con), _uid(con), _since(now_iso, waiting_days), now_iso, source, source_id))
    names = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        return None
    out = dict(zip(names, row))
    desc = con.execute("SELECT description FROM jobs WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    out["description"] = desc[0] if desc else ""
    return out


def get_job(con, source, source_id):
    """The stored job with this key, or None."""
    row = con.execute("SELECT source,source_id,company,title,url,posted,location,remote,description "
                      "FROM jobs WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    if not row:
        return None
    return Job(*row[:7], None if row[7] is None else bool(row[7]), row[8])


def add_usage(con, kind, note=""):
    """One row per model call ('call') or refused call ('refused')."""
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO usage (user_id, day, ts, kind, note) VALUES (?,?,?,?,?)",
                (_uid(con), now.date().isoformat(), now.isoformat(), kind, note))
    con.commit()


def calls_today(con):
    day = datetime.now(timezone.utc).date().isoformat()
    return con.execute("SELECT COUNT(*) FROM usage WHERE user_id=? AND day=? AND kind='call'", (_uid(con), day)).fetchone()[0]


def get_setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE user_id=? AND key=?", (_uid(con), key)).fetchone()
    return row[0] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (user_id,key,value) VALUES (?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                (_uid(con), key, value))
    con.commit()


def model_scored(con):
    """[(Job, fit_score)] for every job the model scored (any age). Used to calibrate the rule score."""
    out = []
    fit = "(s.score_json::json->>'fit_score')::float" if _dialect(con) == "postgres" else "json_extract(s.score_json,'$.fit_score')"
    for r in con.execute("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.location,j.remote,j.description,"
                         f"j.expires,{fit} FROM jobs j JOIN job_scores s "
                         "ON s.source=j.source AND s.source_id=j.source_id AND s.user_id=? WHERE s.score_json IS NOT NULL",
                         (_uid(con),)).fetchall():
        out.append((Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or ""), r[10]))
    return out


# ---- Postgres (hosted mode) ----------------------------------------------------------------------------------------------
# Same functions, same SQL (the `?` placeholders are translated). Users are separated by row-level security: the app
# connects as `jobfinder_app` (no BYPASSRLS, not the table owner) and every transaction starts with
# set_config('app.user_id', <id>, true). Never the Supabase service role for a user request.
APP_ROLE = "jobfinder_app"
WORKER_ROLE = "jobfinder_worker"
PER_USER_TABLES = ("user_jobs", "job_scores", "usage", "settings")     # every table that holds one user's rows
_OWN_ROW = "user_id = nullif(current_setting('app.user_id', true), '')::uuid"

_PG_V1 = [
    f"""DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS; END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN CREATE ROLE {WORKER_ROLE} NOLOGIN NOBYPASSRLS; END IF;
    END $$""",
    """CREATE TABLE jobs (
      source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
      location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
      first_seen TEXT, norm_key TEXT, expires TEXT DEFAULT '', undated INTEGER DEFAULT 0,
      PRIMARY KEY (source, source_id))""",
    """CREATE TABLE user_jobs (user_id UUID NOT NULL, source TEXT, source_id TEXT, passed INTEGER, rule TEXT,
      rule_score INTEGER, rule_json TEXT, PRIMARY KEY (user_id, source, source_id))""",
    """CREATE TABLE job_scores (user_id UUID NOT NULL, source TEXT, source_id TEXT, score_json TEXT, draft_path TEXT,
      resume_path TEXT, scored_at TEXT, PRIMARY KEY (user_id, source, source_id))""",
    "CREATE TABLE usage (user_id UUID NOT NULL, day TEXT, ts TEXT, kind TEXT, note TEXT)",
    "CREATE INDEX usage_user_day ON usage (user_id, day)",
    "CREATE TABLE settings (user_id UUID NOT NULL, key TEXT, value TEXT, PRIMARY KEY (user_id, key))",
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {WORKER_ROLE}",
    f"GRANT SELECT ON jobs TO {APP_ROLE}",                                   # a user request cannot change the catalogue
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO {WORKER_ROLE}",
] + [stmt for t in PER_USER_TABLES for stmt in (
    f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY own_rows ON {t} FOR ALL TO {APP_ROLE}, {WORKER_ROLE} USING ({_OWN_ROW}) WITH CHECK ({_OWN_ROW})",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}, {WORKER_ROLE}")]
PG_MIGRATIONS = [(1, _PG_V1)]


def migrate_pg(admin_url):
    """Create or update the Postgres schema. Run with the owner role (deploy step), never with the app role."""
    import psycopg
    with psycopg.connect(admin_url) as con:
        con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
        have = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        for version, statements in PG_MIGRATIONS:
            if version > have:
                for stmt in statements:                              # one transaction: the `with` commits or rolls back
                    con.execute(stmt)
                con.execute("INSERT INTO schema_version VALUES (%s, %s)", (version, datetime.now(timezone.utc).isoformat()))
        return max([have] + [v for v, _ in PG_MIGRATIONS])


def set_pg_role_login(admin_url, role, password):
    """Let a role log in (deploy step). The password comes from the secret store."""
    import psycopg
    from psycopg import sql
    if role not in (APP_ROLE, WORKER_ROLE):
        raise ValueError(role)
    with psycopg.connect(admin_url) as con:
        con.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))


_PG_CLASS = None


def _pg_class():
    global _PG_CLASS
    if _PG_CLASS is None:
        import psycopg
        from psycopg import pq

        class PgConnection(psycopg.Connection):
            """Looks like the SQLite connection to db.py: `?` placeholders, executemany, a user_id."""
            dialect = "postgres"
            user_id = None

            def execute(self, query, params=None, **kw):
                if self.info.transaction_status == pq.TransactionStatus.IDLE:   # a new transaction: say whose rows it may see
                    super().execute("SELECT set_config('app.user_id', %s, true)", (str(self.user_id),))
                return super().execute(query.replace("?", "%s"), params, **kw)

            def executemany(self, query, seq):
                cur = self.cursor()
                if self.info.transaction_status == pq.TransactionStatus.IDLE:
                    super().execute("SELECT set_config('app.user_id', %s, true)", (str(self.user_id),))
                cur.executemany(query.replace("?", "%s"), list(seq))
                return cur
        _PG_CLASS = PgConnection
    return _PG_CLASS


def connect_pg(url, user_id):
    """A connection for one signed-in user. `url` must use the app (or worker) role, never the owner or a service role."""
    import uuid
    user_id = str(uuid.UUID(str(user_id)))                          # refuses anything that is not a uuid
    con = _pg_class().connect(url)
    con.user_id = user_id
    return con


def _dialect(con):
    return getattr(con, "dialect", "sqlite")
