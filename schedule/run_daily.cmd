@echo off
REM Edit the path and BACKEND (api or claude-code). For api, put ANTHROPIC_API_KEY in .env.
cd /d C:\dev\job-finder
set BACKEND=claude-code
python -m jobfinder >> logs\run.txt 2>&1
