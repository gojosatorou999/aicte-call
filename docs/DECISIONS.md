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

## English only (user request, during V3)

- **Only English is offered on calls.** `ENABLED_LANGUAGES` defaults to `en`: no language menu, the
  greeting is English, and any other language code (caller preference from core, a key press, a URL
  parameter) is clamped to English at the single choke point `_connect`. The other five languages'
  code (phrases, date lexicons, voices) is kept but dormant; set
  `ENABLED_LANGUAGES=en,hi,te,ml,ta,kn` to bring the menu back. The test suite enables all six so
  that code stays exercised.

## V3 - personas and tools

- **PIN and OTP are keypad-only and never reach the model.** After the session starts, an
  unverified officer (PIN) or faculty member (OTP, sent automatically) is asked to key the code in;
  the relay collects `dtmf` digits, calls core, and only then lets the brain run. Spoken attempts
  get "please use the keypad", are not stored and not sent to the model. `verify_officer_pin`,
  `send_otp`, `check_otp` are hidden from the model entirely. Three wrong tries end the call.
- **`policy.py` enforces what the prompt asks.** Until verified, officers/faculty see only
  `search_aph`/`handoff_to_officer`. A typed-number ("press 9") institution session is limited the
  same way, because the contract gives institutions no OTP tool. Track P could add INSTITUTION to
  the `send_otp`/`check_otp` personas to lift that limit.
- **Confirm-first for irreversible tools** (`record_commitment`, `log_extension_request`,
  `trigger_reminder`, `create_grievance`): the first call returns `needs_confirmation` without
  touching core; the identical call is let through only after a later caller utterance that is an
  explicit yes (`speech/intent.py`). The model's own `confirmed=true` is ignored.
- **Commitment dates are checked in code:** parsed (ISO, or the caller's words via the date parser),
  not in the past, at most `COMMITMENT_MAX_DAYS` (30) ahead. Beyond that the tool answers
  `date_beyond_policy` with the latest allowed date, and extensions can only be *requested*.
- **Local `resolve_date` tool** (institution persona only) wraps the deterministic date parser so the
  model does not do date arithmetic. Weekday rules: bare weekday = next one strictly after today;
  `this` allows today; `next` = that weekday in the following Mon-Sun week. The date is always read
  back to the caller before it is saved.
- **Identity-bound parameters are injected**, not chosen by the model: `faculty_id` comes from the
  session (`subject_id` claim, from the caller lookup).
- **Public limits:** `PUBLIC_LOOKUPS_PER_CALL` registry lookups per call; `PUBLIC_CALLS_PER_DAY`
  calls per number per day, in memory per process (Redis needed for several workers, V7).
- **Tool outages:** a core error or outage comes back to the model with a hint to apologise and
  offer a callback; three outages in a row end the call with a "technical problem" message.
- **Outbound context** is built by `agent/personas.py` (`OutboundJob`, one script per call
  purpose) and read from `app.state.jobs`, which the V4 dialer will fill.

## Gather graph, no LLM in the call path (user request, hybrid chosen)

- **`CONVERSATION_MODE=gather` is the default.** The conversation is a deterministic graph of
  Twilio `<Gather>` nodes (`app/graph/`); OpenAI is not called at all. `relay` keeps the earlier
  all-LLM behaviour; `hybrid` runs the graph and hands only unanswerable questions to the LLM
  relay, then resumes the graph via the `<Connect action>` URL (`/voice/g/resume`). `hybrid`
  without `LLM_API_KEY` is the same as `gather`. The old request that described "the Gather graph"
  referred to the earlier Sentinel project; this is a fresh implementation, not a port.
- **One rulebook for both paths.** The graph calls tools through the same `GatedToolRunner` /
  `SessionPolicy` as the LLM path (visibility, date limits, faculty-id injection, lookup limits).
  Where the graph has already had an explicit yes it passes `confirmed=True` (`skip_confirm`) -
  every other rule still applies. `SessionPolicy.commitment_error` is the single date rule.
- **Graph state is in memory, per CallSid** (`graph/state.py`), like the daily limiter: one worker
  only until it moves to Redis (V7). After a restart mid-call the caller hears a technical-problem
  message and the call ends.
- **Verified status travels into the LLM relay** as a signed `verified` claim (plus `via_graph`,
  which lets the relay hand the call back when the caller says "main menu"), so hybrid mode does
  not ask for the PIN twice.
- **Typed-number institutions** go to a single "I can arrange an officer call back" node
  (`inst_limited`); they never reach the commitment flow.
- **Free-text nodes skip the global voice commands** (only the abuse check applies), so a
  complaint that mentions "the officer" or "goodbye" is not mistaken for a command.
- **Speech-to-text is Twilio's `<Gather>`,** `en-IN`, with Twilio's default model unless
  `GATHER_SPEECH_MODELS_JSON` overrides it. `bargeIn` is set on non-sensitive prompts; this attribute
  and the best `speechModel` for Indian English are unverified until a real call.
- **Institution names in the graph** are trimmed of filler words at the ends only
  ("is X approved" -> "X"); core's search does the fuzzy matching.

## Dial pad, live voice, GitHub Pages (user request)

- **Human voice = ConversationRelay, not `<Say>`.** The Gather path uses Twilio's basic `<Say>`
  voices and waits for `<Gather>` timeouts, so it cannot sound conversational. `CONVERSATION_MODE=live`
  runs the *same graph* over a ConversationRelay WebSocket (`/voice/live`): Twilio streams the
  caller's speech to us as text and speaks our replies with a neural voice, with barge-in. Still no
  LLM. Gather mode stays as the fallback for accounts without ConversationRelay.
- **Voice, STT provider and model are left to Twilio's default for en-IN** (per Twilio's voice
  table: an ElevenLabs voice, Google speech-to-text). An untested provider/model/language
  combination can fail a call, so overrides are opt-in (`RELAY_*`). `interruptSensitivity=medium`
  and `ignoreBackchannel=true` so "mm-hmm" does not cut the agent off. Per-turn graph latency is
  logged (`live_turn`, node name only).
- **Text is made speakable** (`speech/speakable.py`): "2026-27" -> "2026 to 27", "B.Tech" -> "B Tech",
  "CSE" -> "C S E", following Twilio's advice to write numbers and abbreviations out.
- **Outbound "call me" = the old Sentinel contract.** `POST /dial/call` (form field `phone`; answer
  `{call_sid, status}` or `{detail}`) asks Twilio to ring the number with `url=/voice/outbound-twiml?d=<id>`
  and a status callback. The person on the line is identified by the number we dialled
  (`Caller.persona` from the core lookup; unknown numbers are PUBLIC). No answering-machine
  detection on these calls: a person just asked for the call and AMD adds seconds of silence.
- **The page is public, so the endpoint defends the Twilio account:** country-prefix allow-list
  (`+91`), per-number / per-address / daily limits (in memory, one worker), optional access code,
  CORS restricted to the page's origin, friendly mapping of Twilio errors (raw Twilio text is never
  shown). The earlier version relied on the trial account alone; that protection ends on upgrade.
- **Demo scenarios** (`DEMO_SCENARIOS`) let the page start the public / institution / officer /
  faculty conversations on a real phone using the mock core. Refused in production.
- **ngrok:** the authtoken is stored by ngrok itself, never in the repo or `.env`. The account's
  permanent domain is used for `PUBLIC_VOICE_URL` and `dialpad/config.js` (an address, not a secret).
  The page also accepts `?api=` and remembers it, and sends `ngrok-skip-browser-warning` so ngrok's
  free-tier interstitial does not swallow JSON.
- **GitHub Pages** publishes only `dialpad/` via a workflow (`.github/workflows/pages.yml`).
- **Mock core reads the same settings/.env as the service,** and tests ignore any local `.env`.
