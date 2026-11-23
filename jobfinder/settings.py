"""Run mode and startup checks.

JOBFINDER_MODE=local (default) is the single-user behaviour. JOBFINDER_MODE=hosted is the multi-user service:
it refuses to start unless every required setting is present. Settings come from the environment, filled from .env."""
import os

MODES = ("local", "hosted")

# Hosted mode: setting name -> what it is for. A missing or empty value stops the start.
HOSTED_REQUIRED = {
    "DATABASE_URL": "Postgres connection string for the app role",
    "SUPABASE_URL": "Supabase project URL (sign-in)",
    "SUPABASE_ANON_KEY": "Supabase public key (sign-in)",
    "ANTHROPIC_API_KEY": "the project's own model API key",
    "SESSION_SECRET": "secret for signing session and CSRF values",
    "ALLOWED_HOSTS_EXTRA": "public hostname(s) of the service",
}


def load_env(path=".env"):
    """Read KEY=VALUE lines into os.environ without overriding values that are already set."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def mode(environ=None):
    """'local' unless JOBFINDER_MODE says otherwise. An unknown value is an error, never a silent fallback."""
    environ = os.environ if environ is None else environ
    value = (environ.get("JOBFINDER_MODE") or "local").strip().lower()
    if value not in MODES:
        raise SystemExit(f"JOBFINDER_MODE must be one of {', '.join(MODES)}, got {value!r}")
    return value


def hosted_problems(environ=None, cfg=None):
    """Every reason hosted mode cannot start (empty list = ready). Names only, never values."""
    environ = os.environ if environ is None else environ
    cfg = cfg or {}
    problems = [f"{name} is missing ({why})" for name, why in HOSTED_REQUIRED.items()
                if not (environ.get(name) or "").strip()]
    backend = (environ.get("BACKEND") or "api").strip().lower()
    if backend != "api":
        problems.append(f"BACKEND must be 'api' in hosted mode, got {backend!r}")
    if (environ.get("DEMO") or "") == "1":
        problems.append("DEMO=1 cannot be combined with hosted mode")
    if not cfg.get("hosted_sources"):
        problems.append("hosted_sources is missing in config.yaml (the sources hosted mode may fetch)")
    return problems


def check_startup(environ=None, cfg=None):
    """Return the mode. In hosted mode raise SystemExit listing every problem."""
    m = mode(environ)
    if m == "hosted":
        problems = hosted_problems(environ, cfg)
        if problems:
            raise SystemExit("hosted mode cannot start:\n- " + "\n- ".join(problems))
    return m
