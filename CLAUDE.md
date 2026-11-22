# job-finder

## Web UI
- Before you write or change HTML, CSS or browser JavaScript, use the modern-web-guidance skill: search the guides
  for the pattern, retrieve the match and follow its MANDATORY items.
- Run MWG commands with DISABLE_TELEMETRY=1.
- The modern-web-guidance skill is in .agents/skills/modern-web-guidance/ (committed). Claude Code does not list it because it is not in .claude/skills/. Read .agents/skills/modern-web-guidance/SKILL.md and run its commands with plain npx and DISABLE_TELEMETRY=1. Do not use npx.cmd: in Git Bash it fails on the space in "C:\Program Files".
- Browser baseline: Baseline Widely available (current Chrome, Edge, Firefox and Safari, including iOS Safari).
  An API newer than this needs a fallback.

## Data in the repo
- Tests, samples and docs use invented data only. Never copy text from resume.json, profile.yaml, target.yaml or my real resume.
