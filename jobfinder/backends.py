"""Two model backends. Both expose complete(system, user) -> parsed JSON (dict). No repair here."""
import json
import os
import subprocess

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "matching_evidence": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "deal_breakers": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["fit_score", "matching_evidence", "gaps", "deal_breakers", "summary"],
    "additionalProperties": False,
}
DEFAULT_MODEL = "claude-sonnet-5-5"


from .settings import load_env  # noqa: E402  (one .env loader; kept importable from here)


class ApiBackend:
    def __init__(self, model=None, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("MODEL", DEFAULT_MODEL)

    def complete(self, system, user, schema=SCORE_SCHEMA, tool_name="submit_result", max_tokens=1500, tools=None, timeout=None):
        if tools:
            raise NotImplementedError("web research needs BACKEND=claude-code (the api backend has no web tools here)")
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": tool_name, "description": "Return the result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )
        usage = getattr(resp, "usage", None)
        counts = [getattr(usage, k, None) for k in ("input_tokens", "output_tokens")]
        self.last_usage = (self.model, *counts) if all(isinstance(c, int) for c in counts) else None   # for the hosted cost record
        for block in resp.content:
            if block.type == "tool_use":
                return block.input
        raise ValueError("no tool_use block in response")


class ClaudeCodeBackend:
    def __init__(self, model=None, run=subprocess.run):
        self.model = model or os.environ.get("MODEL")
        self.run = run

    def complete(self, system, user, schema=SCORE_SCHEMA, tool_name=None, max_tokens=None, tools=None, timeout=None):
        # max_tokens is ignored: claude -p sets its own output limit.
        # tools: None = no tools at all. A string such as "WebSearch,WebFetch" turns on exactly those tools and
        # pre-approves them; dontAsk denies everything else.
        cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--tools", tools or "", "--no-session-persistence"]
        if tools:
            cmd += ["--allowedTools", tools, "--permission-mode", "dontAsk"]
        if self.model:
            cmd += ["--model", self.model]
        # Everything goes through stdin so job text never touches the command line.
        proc = self.run(cmd, input=f"{system}\n\n{user}", capture_output=True, text=True,
                        encoding="utf-8", timeout=timeout or 300)
        if proc.returncode != 0:
            # claude -p prints its JSON error to stdout, so keep both streams (never the prompt).
            raise RuntimeError(f"claude exited {proc.returncode}: stdout={(proc.stdout or '')[:300]!r} "
                               f"stderr={(proc.stderr or '')[:300]!r}")
        outer = json.loads(proc.stdout)
        if outer.get("is_error"):
            raise RuntimeError(f"claude error: {str(outer.get('result'))[:300]}")
        if isinstance(outer.get("structured_output"), dict):
            return outer["structured_output"]
        return json.loads(outer["result"])


def get_backend():
    load_env()
    name = os.environ.get("BACKEND", "api").lower()
    if name == "api":
        return ApiBackend()
    if name == "claude-code":
        return ClaudeCodeBackend()
    raise SystemExit(f"BACKEND must be 'api' or 'claude-code', got {name!r}")
