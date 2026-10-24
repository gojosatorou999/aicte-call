# Changelog

## V0 - Voice setup

- `apps/voice`: FastAPI app factory, `/health`, `POST /voice/inbound` (bilingual greeting TwiML,
  Twilio signature validation, draining fallback).
- `core_client.py` (bearer + `X-Voice-Session`, circuit breaker), `session.py` (session JWT, personas),
  `agent/tools.py` (allowlist + routes from `packages/contracts/voice-tools.json`).
- `mock_core/` hand-written mock of `/internal/voice/*`; `Dockerfile`; README.
- Tests: 11 unit tests; ruff clean.
- Not done in V0: Twilio number and cloudflared tunnel (manual, see `apps/voice/README.md`).

## V1 - Inbound IVR + session

- `/voice/inbound` -> bilingual greeting + 6-language menu (DTMF or speech); default language from
  the caller's preferred language. `/voice/language`, `/voice/identify`, `/voice/identify-number`,
  `/voice/connect` route to a persona (caller ID lookup; "press 9" for unknown callers).
- Session JWT now carries `identity_source`. Dual-channel recording starts before
  `<Connect><ConversationRelay>`. `/voice/status` and `/voice/recording` relay to core.
- `speech/languages.py`, `speech/phrases.py` (6 languages), `ivr.py`.

## V2 - Relay WebSocket + brain

- `relay_ws.py`: ConversationRelay handler (setup/prompt/interrupt/dtmf/error in; text/end out),
  session auth, barge-in, silence reprompts, turn and duration limits, abuse handling, redacted
  transcript buffer.
- `agent/brain.py`: streaming LLM with tool loop and per-session memory; `agent/llm.py` (OpenAI
  adapter); `agent/guardrails.py` (system prompt, decision-request refusal, reply policy screen,
  sentence chunker); persona prompts in `agent/prompts/`.
- Tests: 62 total (unit + WebSocket integration with a scripted LLM and the mock core). Lint clean.
- **Not verified against real services:** no real LLM, Twilio call, or Indian-language STT/TTS
  was exercised. The "is <institution> approved" flow is tested end to end only with a scripted LLM.

## V3 - Personas and tools

- Keypad PIN/OTP verification (`agent/verification.py`), session policy and gated tool runner
  (`agent/policy.py`): tool visibility by verification state, confirm-first for irreversible tools,
  commitment date policy, public lookup limit, injected faculty id.
- Spoken-date parser (`speech/dates.py`) and local `resolve_date` tool; multilingual yes/no
  (`speech/intent.py`); per-purpose outbound scripts (`agent/personas.py`); public daily call limit
  (`ratelimit.py`); tool-outage handling and `add_context` in the brain.
- Mock core: officer, faculty, OTP, reminder, report, upload-link and APH endpoints.
- **English only by default** (`ENABLED_LANGUAGES=en`); other languages dormant but tested.
- Tests: 186 total, including scripted calls for voice test-matrix rows 1-9 (row 10, voicemail, is V4).

## Gather graph (no LLM in the call path) + README

- `app/graph/`: engine, per-call state, TwiML rendering and nodes for the public, institution
  (inbound and outbound), officer (keypad PIN) and faculty (keypad OTP) conversations; routes
  `/voice/g/turn` and `/voice/g/resume`.
- `CONVERSATION_MODE` = `gather` (default) | `relay` | `hybrid`; hybrid hands off to the LLM relay
  and resumes the graph. New session claims `verified`, `via_graph`.
- Spoken-time parser, no/number-choice intent helpers, `SessionPolicy.commitment_error`.
- `scripts/simulate_call.py` console phone; `tests/phone.py` fake phone; `.env.example`;
  full root `README.md`.
- Tests: 223 total; in gather mode a stub LLM fails the test if it is ever called.

## Cleanup

- The old Sentinel project (`finl call`) was moved out of the repo folder; it was never part of
  this repository (it was gitignored).
- Removed settings, dependencies and config that came from that project and nothing read:
  Redis/database URLs, outbox relay, dial pad, OTel, Sarvam, SMS sender, retry settings;
  sqlalchemy, aiosqlite, asyncpg, alembic, redis, pyyaml, import-linter. A fresh environment
  built from `pyproject.toml` passes the whole suite.
- One shared `TranscriptLine`; dropped the unused `Input.confidence`; tidied `routes_graph`.
- Ran `ruff format` over the project (mechanical, no behaviour change).
