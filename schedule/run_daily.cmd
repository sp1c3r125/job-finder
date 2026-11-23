@echo off
REM BACKEND is api or claude-code. Owner step: also put BACKEND=claude-code in .env (git-ignored). For api, put ANTHROPIC_API_KEY in .env.
cd /d C:\dev\job-finder
set BACKEND=claude-code
python -m jobfinder >> logs\run.txt 2>&1
