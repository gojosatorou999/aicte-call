# apps/voice - ACP AI calling agent (Track V)

FastAPI service that answers and places Twilio calls for the AICTE Approval &
Compliance Portal. Spec: `app.md` section 12 and section 20.

## Run locally

```powershell
cd apps/voice
uv pip install -e ".[dev]"          # or: python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"
uvicorn mock_core.app:app --port 9000      # stand-in for core until P8
uvicorn app.main:app --port 8080           # the voice service
```

`GET http://localhost:8080/health` should return `{"status": "ok", ...}`.

To take real calls, expose port 8080 (`cloudflared tunnel --url http://localhost:8080`),
set `PUBLIC_VOICE_URL` to the tunnel URL, and point the Twilio number's voice
webhook (HTTP POST) at `<PUBLIC_VOICE_URL>/voice/inbound`. Calling the number
plays the bilingual greeting (V0 acceptance).

Webhook signatures are validated with `TWILIO_AUTH_TOKEN`. For local curl
testing only, set `TWILIO_SIGNATURE_VALIDATION_ENABLED=false`.

## Tests and lint

```powershell
pytest
ruff check .
```

## Layout

| Path | Purpose |
|---|---|
| `app/main.py` | app factory, lifespan (boot checks) |
| `app/api/` | HTTP routes: `/health`, `/voice/*` |
| `app/twiml.py` | TwiML builders |
| `app/session.py` | per-call session JWT, `Persona` |
| `app/core_client.py` | bearer + session-header client for core, circuit breaker |
| `app/agent/tools.py` | tool allowlist from `packages/contracts/voice-tools.json` and routes to core |
| `mock_core/` | hand-written mock of core `/internal/voice/*` |

## Languages

Calls are English only by default. To bring back the six-language menu set
`ENABLED_LANGUAGES=en,hi,te,ml,ta,kn` (Indian-language speech quality is unverified; see
`docs/DECISIONS.md`).

## Officer / faculty verification

Officers enter a six digit voice PIN, and faculty a one-time code, on the phone keypad; the code is
never spoken to or seen by the model. In the mock core the PIN is `246810` and the OTP `123456`.
