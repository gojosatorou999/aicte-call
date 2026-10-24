# Decisions

Spec gaps resolved under app.md 0.2 rule 9. Newest last.

## V0 - voice service

- **Open items path.** app.md 13.13 lists `GET /institutions/{id}/open-items`, but the institution
  is already fixed by the session JWT. The voice tool calls `/institutions/me/open-items` so the LLM
  can never pass a different institution id. Track P should accept `me` (resolved from the session).
- **Callbacks endpoint shared.** `schedule_callback` and `handoff_to_officer` both map to
  `POST /callbacks`. The voice service adds `kind: CALLBACK | HANDOFF` to the body to tell them apart.
- **Contract drift fails at boot.** `agent/tools.py` cross-checks `voice-tools.json` against its
  route table at startup (lifespan), so a renamed or added tool stops the service starting rather
  than failing during a call.
- **Signature URL.** Twilio signatures are verified against `PUBLIC_VOICE_URL` + path, not the
  request's own host, because a tunnel or proxy rewrites the latter.
- **`tzdata` dependency.** Windows and slim images have no system tz database; `zoneinfo`
  needs the `tzdata` package.
- **Mock core.** `apps/voice/mock_core` is a hand-written FastAPI mock, not Prism, until Track P
  exports `packages/contracts/openapi.json` (spec allows either).

## V1 - inbound IVR

- **Session JWT travels as a `<Parameter>`**, read from the `setup` message's `customParameters`,
  not as `?session=` on the relay URL (spec 12.1). Query strings on the relay URL are not reliably
  forwarded by Twilio. The JWT is also the only authentication of the WebSocket; it is short-lived,
  signed, and its `call_sid` must match the `setup` message.
- **"Press 9" is a claim, not proof.** A number typed on the keypad selects a persona but the
  session is marked `identity_source=ENTERED_NUMBER`. V3 must require PIN (officer) / OTP
  (faculty, institution) before releasing any non-public data for those sessions. An unknown
  entered number silently continues as PUBLIC so the registry cannot be probed.
- **Calls are addressed to core by Twilio CallSid** in `/calls/{id}/status` for inbound calls
  (`POST /calls` registers the call with `call_sid` in the body). The voice service keeps no
  call-id store yet. Track P: accept CallSid as `{id}` (or return it from `POST /calls`).
  Recording URLs are sent through the same status endpoint as `{recording_url, recording_sid}`.
- **Status callback is configured on the Twilio number**, not in TwiML: set the number's
  "Call status changes" webhook to `<PUBLIC_VOICE_URL>/voice/status`.
- **Speech providers are unverified for Indian languages.** Twilio's ConversationRelay docs do not
  list en-IN/hi-IN/te-IN/ta-IN/ml-IN/kn-IN. Defaults are Google STT/TTS (`telephony` model,
  `*-IN-Standard-A` voices), overridable per language via env. V7 must test by ear and, for weak
  languages, switch to the Sarvam `<Connect><Stream>` path.
- **Fixed IVR phrases are translated by hand** (`speech/phrases.py`); te/ml/ta/kn need review by a
  native speaker.

## V2 - relay and brain

- **Prompts are per persona, not per persona x language.** `agent/prompts/{persona}.md` is written
  in English plus a "speak in <language>" instruction; 30 near-duplicate files would drift. Revisit
  in V3 if a language performs badly.
- **Replies are screened per sentence.** The brain emits whole sentences, so a deterministic
  policy check (no approval promises, no extensions granted) can run before anything is spoken.
  Cost: first audio waits for the first sentence boundary (a long unpunctuated run is flushed at
  160 chars). The screen is narrow regex (English) and backs up the prompt; it is not a classifier.
- **Decision requests ("approve my application") are refused deterministically** (English + Hindi
  patterns), without calling the LLM, plus `handoff_to_officer` where the persona has it.
- **Silence and goodbye timing are estimated.** ConversationRelay gives no "finished speaking"
  event unless `events` is configured, so reprompt timers start after `len(text)/12` seconds plus
  `NO_INPUT_TIMEOUT_MS`, and `end` is sent after the same estimate. Replace with `tokens-played`
  events in V7 if the estimate proves flaky.
- **Barge-in:** an `interrupt` cancels the running turn; memory keeps only what the caller heard
  (`utteranceUntilInterrupt`). Half-finished tool exchanges are dropped so the history stays valid.
- **Profanity filter off** in the relay (`profanityFilter=false`) so our own abuse detection sees
  the real words; one warning, then the call ends.
