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
