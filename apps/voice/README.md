# apps/voice - ACP AI calling agent (Track V)

The full documentation - what it does, how it works, what accounts and keys you need, how to run
it with and without Twilio - is in the repository root: [`../../README.md`](../../README.md).

Quick start (from this folder):

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn mock_core.app:app --port 9000      # terminal 1
$env:TWILIO_SIGNATURE_VALIDATION_ENABLED = "false"
.\.venv\Scripts\python -m uvicorn app.main:app --port 8080           # terminal 2
.\.venv\Scripts\python scripts/simulate_call.py                       # terminal 3
```

Settings: copy `.env.example` to `.env`. Tests: `python -m pytest`. Lint: `ruff check .`
