# AICTE Approval & Compliance Portal (ACP) — IMPLEMENTATION.md

> **SIH 2026 · PS 26262 · AICTE Regulation Bureau · Theme: Smart Automation**
> Integrated AICTE Approval Workflow Automation, Compliance Monitoring and Alert Management System
>
> This file is the single source of truth for building the entire application end to end.
> It is written to be executed phase by phase by AI coding agents (Google Antigravity + terminal CLI agents).

---

## 0. How to use this file

### 0.1 Ownership

| Track | Owner | Scope |
|---|---|---|
| **Track P — Platform** | **Varun** | Monorepo, web app (all role portals + EVC PWA), core API, workflow/SLA/escalation engines, Document AI, messaging (Email/WhatsApp/SMS via Twilio), dashboards, reports, admin config, audit chain, i18n, theming, deployment |
| **Track V — Voice** | **Rishanth** | AI calling agent service (`apps/voice`): Twilio Voice, outbound dialer, inbound IVR routing, 4 caller personas, multilingual speech, LLM tool-calling against core API, post-call processing, in-app dictation (speech → structured EVC report) endpoint |
| **Shared** | Both | `packages/contracts` (OpenAPI + JSON schemas), integration tests, demo rehearsal |

Track V starts in parallel from Phase 1 using the contract in §13 and a mock server. Integration happens in Phase 8.

### 0.2 Rules for AI agents executing this file

1. Execute **one phase at a time**, in order, within your track. Do not start a phase until the previous phase's **Acceptance Criteria** pass.
2. Before coding a phase, read §3 (stack), §5 (repo layout), §7 (design system), and the sections that phase references.
3. Never hardcode secrets. Read everything from env (§6).
4. Every user-facing string goes through i18n (§8). No raw English literals in components.
5. Every state-changing backend action writes an audit event (§16) **in the same DB transaction**.
6. Every "now" in backend code comes from `app.core.clock.now()` (supports demo time travel, §18). Never call `datetime.now()` directly.
7. AI never makes regulatory decisions. AI output is always a **draft / suggestion / flag** that a human confirms (§1.3).
8. After each phase: run lint + type-check + tests, update `docs/CHANGELOG.md`, commit with Conventional Commits (`feat(api): ...`).
9. If a spec detail is missing, choose the simplest option consistent with this file and record it in `docs/DECISIONS.md`.

### 0.3 Phase map

| Phase | Track | Name | Est. days |
|---|---|---|---|
| P0 | P | Repo, infra, tooling | 1 |
| P1 | P | Auth, RBAC, design system, theming, i18n shell | 3 |
| P2 | P | Workflow engine + application lifecycle | 4 |
| P3 | P | Document AI + Case Review workspace | 3 |
| P4 | P | EVC scheduling + Expert PWA | 3 |
| P5 | P | SAC/SHC committee + conditions → undertakings | 2 |
| P6 | P | Compliance & document validity tracking | 2 |
| P7 | P | SLA scheduler, escalation ladder, Twilio messaging | 3 |
| P8 | P+V | Voice integration (core side) | 2 |
| P9 | P | Dashboards, analytics, reports, India map | 3 |
| P10 | P | Admin configuration portal | 2 |
| P11 | P | AI extras: RAG assistant, breach prediction, anomalies, case summary | 3 |
| P12 | P+V | Hardening, E2E tests, demo control panel, deployment, rehearsal | 3 |
| V0–V7 | V | Voice agent service (parallel, see §20) | ~14 |

---

## 1. Product scope

### 1.1 One-line

A single, secure, configurable web platform (plus an installable PWA for field inspectors) that automates AICTE approval workflows end to end — routing, scrutiny, EVC, SAC/SHC, conditional approvals — and continuously monitors undertakings and document validity, with multi-channel alerts (Email, WhatsApp, SMS, AI voice calls), escalation, dashboards, reports and a tamper-evident audit trail.

### 1.2 PS coverage map (judging checklist)

| PS requirement | Where implemented |
|---|---|
| A. Auto-routing by APH pathway, auto-advance, return/revert with reasons, standard communications, e-case summaries | §9 Workflow engine, §14 AI (letters, summaries), P2, P3, P5 |
| B. Real-time dashboard, ageing, workload, bottlenecks, alerts, role dashboards, audit trail | §15 screens, P9, §16 |
| C. EVC queue, scheduling, timeline tracking, reminders, escalation | P4, §10 |
| D. Undertakings record, deadline notices, online submission, verification, escalation | P5, P6, §10 |
| E. Lease/document validity, advance alerts, renewal submission, verification, overdue escalation | P6, §10 |
| F. Configurable notifications, auto-escalation, consolidated reports, audit of alerts | §10, §11, P7, P9, P10 |
| Configurable rules aligned with APH | §9.2 rule schema, H1/H2 admin screens |
| Secure storage, RBAC, confidentiality, traceability | §17, §16 |
| Integration with existing AICTE systems | §9.7 adapter interface (mocked) |
| Human oversight | §1.3 |

### 1.3 Regulatory safeguard (non-negotiable design rules)

- The system **supports**, never replaces, statutory scrutiny, verification, inspection and decisions.
- Stage completion, returns, verification, committee decisions and approvals require an **authenticated human action** (with OTP/eSign where marked).
- AI outputs are labelled in the UI with an `AI` badge and confidence. They are drafts until a human accepts them.
- The voice agent can **inform, remind, collect declarations/commitments, answer FAQs, and create drafts/tickets**. It can never approve, reject, grant extensions, or record decisions.
- Auto-advance only happens when the preceding stage was **completed by an authorised human** and the rule permits it.

---

## 2. Users, roles and voice personas

### 2.1 Application roles (RBAC)

| Role code | Who | Primary surface |
|---|---|---|
| `INSTITUTION` | Institution staff / nodal officer | Web (responsive) |
| `SCRUTINY_OFFICER` | AICTE scrutiny officer | Web (desktop-first) |
| `REGIONAL_OFFICER` | Regional office head/officer | Web |
| `BUREAU_HEAD` | Regulation Bureau / competent authority | Web |
| `COMMITTEE_MEMBER` | SAC/SHC member | Web |
| `EVC_EXPERT` | Expert Visit Committee member | PWA (mobile-first) |
| `ADMIN` | System admin | Web |
| `PUBLIC` | Students/parents (no login) | Public lookup page + voice |
| `FACULTY` | Faculty (light login via Faculty ID + OTP) | Minimal web page + voice |

Region scoping: officers are bound to one or more regions via `user_regions`. All queries are filtered by region unless the role is `BUREAU_HEAD` or `ADMIN`.

### 2.2 Voice personas (Track V)

| Persona | Direction | Volume | Auth | What they can do |
|---|---|---|---|---|
| **Institution staff** | Mostly **outbound** (+ inbound) | High | Outbound: number matches registered contact + name confirm. Sensitive details: OTP | Hear reminders (deficiency pending, undertaking due, document expiring, EVC visit), give committed date + reason, receive upload link on WhatsApp/SMS, ask status, ask APH FAQs |
| **AICTE officers** | **Inbound** voice queries | Medium | Registered mobile + 6-digit voice PIN (or OTP) | "My overdue cases", "status of APP-2026-0451", region stats, "email me pendency report", "trigger reminder for APP-…" (with spoken confirmation) |
| **Students & parents** | **Inbound** public | High, bursty | None (public data only), rate-limited by caller number | "Is <institution> AICTE approved?", approved courses/intake for the academic year, how to file a grievance, raise a grievance ticket (SMS ticket ID) |
| **Faculty** | Inbound | Low | Faculty ID + OTP | Faculty profile/registration status, whether they are flagged in a duplicate-listing check (and how to resolve), how to update details |

Optional (if time permits): **EVC expert** inbound dictation of the inspection report → draft report in PWA (same backend as in-app dictation).

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Monorepo | **pnpm workspaces + Turborepo** (JS) and **uv** (Python) | Fast, agent-friendly, single repo for both tracks |
| Web app | **Next.js 15 (App Router) + TypeScript + React 19** | SSR dashboards, route groups per role, PWA support |
| Styling/UI | **Tailwind CSS v4 + shadcn/ui (Radix) + lucide-react** | Fast, accessible components; matches reference UI |
| Theming | **next-themes** (light/dark/system) with CSS variable tokens | Both reference screens (light desktop, dark mobile) |
| i18n | **next-intl** (6 locales) + Noto Sans script fonts | Locale-prefixed routes, ICU messages, Intl formatting |
| Data fetching | **TanStack Query v5** + **openapi-fetch** (typed client generated from OpenAPI) | Typed API, caching, optimistic updates |
| Forms | **react-hook-form + zod** | Wizard validation |
| Tables | **TanStack Table** | Queues, reports |
| Charts | **Recharts** | KPIs, ageing, trends |
| India map | **react-simple-maps** + India states TopoJSON | Region heatmap |
| PDF viewer | **react-pdf (pdf.js)** with highlight overlay layer | OCR bounding-box highlights like the reference |
| PWA | **Serwist** (service worker) + IndexedDB (**idb**) for offline queue | Expert app offline sync, camera, GPS |
| Realtime | **Server-Sent Events** from API (Redis pub/sub fan-out) | Live dashboards, notification bell |
| Core API | **Python 3.12 + FastAPI + Pydantic v2** | AI-heavy backend, async, auto OpenAPI |
| ORM/migrations | **SQLAlchemy 2.0 (async) + Alembic** | Mature, typed |
| Database | **PostgreSQL 16 + pgvector** | Relational + embeddings for RAG |
| Cache/queue broker | **Redis 7** | Celery broker, pub/sub, rate limits, demo clock |
| Background jobs | **Celery 5 + Celery Beat** | SLA scanner, notifications, OCR, reports |
| Object storage | **MinIO** (S3-compatible; swap to S3/GCS in prod) | Documents, photos, recordings, reports |
| OCR + extraction | **LLM vision extraction** (Gemini Flash-class, configurable) with **Tesseract/PaddleOCR** fallback for bounding boxes | Field extraction + highlight boxes |
| LLM | Provider-agnostic `LLMClient` (default **Gemini**, swappable to Claude/OpenAI via env) | Summaries, letters, RAG, voice brain |
| Embeddings | Multilingual embedding model (configurable; e.g. Gemini embeddings or `bge-m3`) | RAG over APH in 6 languages |
| ML | **LightGBM / scikit-learn** | SLA breach prediction |
| Reports | **WeasyPrint** (PDF), **openpyxl** (Excel) | Scheduled + on-demand reports |
| Email | **Twilio SendGrid** (outbound + Inbound Parse) | Twilio stack as required, open/bounce tracking |
| WhatsApp / SMS | **Twilio Messaging API** (WhatsApp Business sender + SMS) | Alerts, upload links, inbound document submission |
| OTP | **Twilio Verify** (SMS/WhatsApp channel) | Login + eSign-style confirmation |
| Voice | **Twilio Programmable Voice + ConversationRelay** (fallback: **Media Streams + Sarvam AI STT/TTS**) | Real-time multilingual voice agent |
| Voice service | **Python FastAPI + WebSockets** (`apps/voice`) | Same language as core, LLM tool-calling |
| Auth | Own JWT (access 15m + refresh 7d, httpOnly cookies) + OTP via Twilio Verify; RBAC + region scope middleware | Simple, demo-able, government-style OTP login |
| Testing | **pytest + httpx**, **Vitest**, **Playwright** E2E | Phase acceptance |
| Lint/format | **ruff + mypy** (Py), **eslint + prettier + tsc** (TS) | Agent-enforced quality |
| Local infra | **Docker Compose** + **cloudflared/ngrok** tunnel for Twilio webhooks | One-command dev |
| Deploy | Single VM with Docker Compose + **Caddy** (auto HTTPS) | Simple and reliable for demo |

---

## 4. System architecture

```mermaid
flowchart LR
  subgraph Clients
    W[Next.js Web App\nall role portals]
    PWA[EVC Expert PWA\n/expert/*]
    PUB[Public lookup page]
  end

  subgraph Core["apps/api (FastAPI)"]
    API[REST API /api/v1]
    SSE[SSE stream]
    WF[Workflow Engine]
    SLA[Deadline & Escalation Engine]
    NOTI[Notification Orchestrator]
    DOCAI[Document AI]
    AIS[AI Services\nsummary, letters, RAG, anomalies, prediction]
    AUD[Audit Chain]
    INT[/internal/voice/* API/]
    WH[Webhooks: SendGrid, WhatsApp, SMS status]
  end

  subgraph Workers["Celery workers + beat"]
    J1[deadline_scan every 60s]
    J2[send_notification]
    J3[ocr_extract]
    J4[reports]
    J5[ml_score]
  end

  subgraph Voice["apps/voice (FastAPI, Rishanth)"]
    VIN[Inbound TwiML + IVR]
    VOUT[Outbound dialer]
    WS[ConversationRelay WebSocket]
    BRAIN[LLM agent + tools]
    POST[Post-call processor]
  end

  DB[(Postgres + pgvector)]
  R[(Redis)]
  S3[(MinIO)]
  TW[Twilio: Voice, Messaging, Verify, SendGrid]
  LLM[LLM provider]

  W & PWA & PUB --> API
  W --> SSE
  API --> WF & DOCAI & AIS & AUD
  WF --> SLA
  SLA --> NOTI
  NOTI --> J2 --> TW
  NOTI -->|call job| VOUT
  VOUT --> TW
  TW -->|inbound call| VIN --> WS --> BRAIN
  BRAIN -->|tools| INT
  POST --> INT
  TW -->|inbound email/WA, status callbacks| WH
  Core & Workers --> DB & R & S3
  DOCAI & AIS & BRAIN --> LLM
```

### 4.1 Event bus

Domain events are written to `outbox_events` in the same transaction as the change, then a Celery task publishes them to Redis pub/sub (`acp.events`). Consumers: SSE broadcaster, notification orchestrator, audit projector (if not inline), ML re-scorer.

Event names (`snake.case`):
`application.submitted`, `application.routed`, `stage.activated`, `stage.completed`, `stage.returned`, `stage.resubmitted`, `assignment.created`, `evc.queued`, `evc.scheduled`, `evc.report_submitted`, `committee.decision_recorded`, `condition.created`, `compliance.submitted`, `compliance.verified`, `compliance.rejected`, `document.expiring`, `document.expired`, `deadline.reminder_due`, `deadline.breached`, `escalation.raised`, `notification.sent`, `notification.delivered`, `call.completed`, `commitment.recorded`, `commitment.broken`, `grievance.created`.

---

## 5. Repository layout

```
aicte-acp/
├── IMPLEMENTATION.md
├── README.md
├── package.json                # pnpm workspace root
├── pnpm-workspace.yaml
├── turbo.json
├── .env.example
├── docs/
│   ├── CHANGELOG.md
│   ├── DECISIONS.md
│   └── DEMO_SCRIPT.md
├── infra/
│   ├── docker-compose.yml      # postgres, redis, minio, api, worker, beat, voice, web, caddy
│   ├── docker-compose.dev.yml
│   ├── Caddyfile
│   └── postgres/init.sql       # CREATE EXTENSION vector; pgcrypto;
├── data/
│   ├── aph/                    # APH PDF(s) for RAG (download manually from AICTE site)
│   └── seed/                   # seed JSON (regions, app types, rules, templates)
├── packages/
│   └── contracts/
│       ├── openapi.json        # exported from apps/api (CI step)
│       ├── voice-tools.json    # tool schemas shared with apps/voice
│       ├── workflow-rule.schema.json
│       ├── escalation-policy.schema.json
│       └── ts/                 # generated TS client (openapi-typescript)
├── apps/
│   ├── web/                    # Next.js (Varun)
│   │   ├── app/[locale]/
│   │   │   ├── (auth)/login/
│   │   │   ├── (public)/verify/          # public institution lookup
│   │   │   ├── (public)/faculty/
│   │   │   ├── institution/...
│   │   │   ├── officer/...
│   │   │   ├── regional/...
│   │   │   ├── bureau/...
│   │   │   ├── committee/...
│   │   │   ├── expert/...                # PWA, mobile-first
│   │   │   ├── admin/...
│   │   │   └── demo/
│   │   ├── components/
│   │   │   ├── ui/                       # shadcn primitives
│   │   │   ├── shell/                    # TopBar, Sidebar, Breadcrumbs, ActionBar
│   │   │   ├── case/                     # CaseFilePanel, StageStepper, DocViewer, AIPanel
│   │   │   ├── status/                   # StatusChip, SlaBadge, SlaPill, ConfidenceRing
│   │   │   ├── charts/
│   │   │   └── expert/                   # CameraCapture, GpsPill, Checklist, DictateButton
│   │   ├── messages/{en,hi,te,ml,ta,kn}.json
│   │   ├── i18n/                         # next-intl config, request.ts, routing.ts
│   │   ├── lib/api.ts                    # openapi-fetch client
│   │   ├── lib/sse.ts
│   │   ├── styles/tokens.css
│   │   ├── public/brand/                 # logos (team-provided)
│   │   ├── public/manifest.webmanifest
│   │   └── sw.ts                         # Serwist
│   ├── api/                    # FastAPI core (Varun)
│   │   ├── pyproject.toml
│   │   ├── alembic/
│   │   └── app/
│   │       ├── main.py
│   │       ├── core/           # config, clock, security, deps, i18n, errors, logging
│   │       ├── db/             # session, base, models/*.py
│   │       ├── schemas/        # pydantic
│   │       ├── routers/        # auth, institutions, applications, stages, documents,
│   │       │                   # evc, committee, compliance, notifications, dashboards,
│   │       │                   # reports, admin, audit, public, faculty, ai, webhooks,
│   │       │                   # internal_voice, demo, sse
│   │       ├── services/
│   │       │   ├── workflow/   # engine.py, rules.py, transitions.py, assignment.py
│   │       │   ├── deadlines/  # clock.py, scanner.py, policies.py, escalation.py
│   │       │   ├── notify/     # orchestrator.py, templates.py, channels/{email,whatsapp,sms}.py
│   │       │   ├── docai/      # extract.py, ocr.py, validators.py
│   │       │   ├── ai/         # llm.py, summary.py, letters.py, rag.py, anomalies.py, predict.py
│   │       │   ├── evc/        # scheduler.py, conflicts.py
│   │       │   ├── reports/
│   │       │   ├── audit/      # chain.py
│   │       │   └── integrations/aicte_adapter.py  # mock of existing AICTE systems
│   │       ├── workers/        # celery_app.py, tasks/*.py, beat_schedule.py
│   │       └── seed/           # seed.py (Faker en_IN)
│   │   └── tests/
│   └── voice/                  # AI calling agent (Rishanth)
│       ├── pyproject.toml
│       └── app/
│           ├── main.py
│           ├── twiml.py              # inbound/outbound TwiML
│           ├── relay_ws.py           # ConversationRelay WebSocket handler
│           ├── ivr.py                # language + persona routing
│           ├── dialer.py             # outbound call jobs, retries, calling hours
│           ├── agent/
│           │   ├── brain.py          # LLM streaming + tool loop
│           │   ├── personas/{institution,officer,public,faculty,expert}.py
│           │   ├── prompts/{persona}.{lang}.md
│           │   ├── tools.py          # wrappers over core /internal/voice/*
│           │   └── guardrails.py
│           ├── speech/               # language config, Sarvam fallback adapter
│           ├── postcall.py           # summary + structured outcome → core
│           ├── dictation.py          # in-app audio → structured EVC report draft
│           └── core_client.py
│       └── tests/
└── .github/workflows/ci.yml
```

---

## 6. Environment variables (`.env.example`)

```dotenv
# ---------- General ----------
APP_ENV=development                 # development | demo | production
DEMO_MODE=true                      # enables time travel, fixed OTP, demo panel
DEMO_FIXED_OTP=123456
PUBLIC_WEB_URL=http://localhost:3000
PUBLIC_API_URL=http://localhost:8000
PUBLIC_VOICE_URL=https://<tunnel>.trycloudflare.com   # must be public for Twilio
DEFAULT_TIMEZONE=Asia/Kolkata

# ---------- Security ----------
JWT_SECRET=change-me
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=7
INTERNAL_SERVICE_TOKEN=change-me-voice-to-core   # apps/voice -> apps/api
FIELD_ENCRYPTION_KEY=change-me-32-bytes

# ---------- Data ----------
DATABASE_URL=postgresql+asyncpg://acp:acp@postgres:5432/acp
REDIS_URL=redis://redis:6379/0
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=minio
S3_SECRET_KEY=minio123
S3_BUCKET=acp

# ---------- AI ----------
LLM_PROVIDER=gemini                 # gemini | anthropic | openai
LLM_MODEL=<fast-model-id>
LLM_MODEL_VISION=<vision-model-id>
LLM_API_KEY=
EMBEDDING_MODEL=<multilingual-embedding-model-id>
OCR_FALLBACK=tesseract              # tesseract | paddle

# ---------- Twilio ----------
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_VERIFY_SERVICE_SID=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886      # sandbox number in dev
TWILIO_SMS_FROM=
TWILIO_VOICE_NUMBER=
TWILIO_MESSAGING_STATUS_CALLBACK=${PUBLIC_API_URL}/api/v1/webhooks/twilio/status
SENDGRID_API_KEY=
SENDGRID_FROM_EMAIL=noreply@<your-domain>
SENDGRID_INBOUND_DOMAIN=reply.<your-domain>     # MX → mx.sendgrid.net
SENDGRID_WEBHOOK_PUBLIC_KEY=

# ---------- Voice (Track V) ----------
VOICE_STT_PROVIDER=google          # ConversationRelay transcription provider
VOICE_TTS_PROVIDER=google          # or elevenlabs / amazon
SARVAM_API_KEY=                    # fallback for Indian-language STT/TTS
CALL_HOURS_START=10:00
CALL_HOURS_END=18:00
CALL_MAX_ATTEMPTS=3
CALL_RETRY_GAP_MIN=120
```

---

## 7. Design system (from reference screens)

### 7.1 Visual language

Two reference screens define the UI:

1. **Officer Case Review (desktop, light)**: dark navy top bar with emblem + AICTE logo and title "ALL INDIA COUNCIL FOR TECHNICAL EDUCATION / APPROVAL & COMPLIANCE PORTAL", global search, gold SLA pill ("12 Days Left (On-Time)"), mail / WhatsApp / bell icons with count badges, officer profile with avatar. Left sidebar (Dashboard, Queue, Applications, Compliance, Document Vault, Analytics, Settings), version tag at bottom ("v4.2.0-Production"). Breadcrumbs (Queue › SCR-402 › APP-2026-0451). Three-column workspace: **Application Case File** (title, App ID, type line, vertical stage stepper, key facts list) | **Document Viewer** (zoom, page x of y, rotate, "Compare Side-by-Side" primary button, OCR highlight boxes, "AI OCR: verified" chip) | **AI Pre-Scrutiny Verification** (confidence ring "98%", check cards: Duplicate Faculty Check [CLEARED], Land Area Norms [Meets AICTE APH Norms], Infrastructure Verification [100% COMPLETE], Deficiency/Anomalies amber warning card). Sticky bottom action bar: **Return for Deficiency** (red outline), **Refer to Senior** (neutral outline), **AI Assistant Chat** (purple tint), **CLEAR STAGE & FORWARD** (solid blue).
2. **EVC Field Inspection (mobile, dark)**: app header with logo and kebab menu; green "GPS Locked • Inside Campus Radius 15m" pill + "Offline Sync" pill; "Geotagged Campus Photo Verification" card with live camera viewfinder (REC dot, corner brackets, crosshair), overlay with GPS lat/long, altitude, timestamp IST, and "TAMPER-PROOF • HASH: 8f9a2c…88" badge; "Retake / Add Photo (Max 5)" button; "On-Site Inspection Checklist" with scrollable tabs (Land & Building, Labs & Equip., Faculty Attd., Safety…), checklist rows with status pills (VERIFIED green, IN PROGRESS amber), photo thumbnails, "Attach Lab Photos" button; bottom: "Hold to Dictate AI Voice Note" with waveform; full-width primary "SUBMIT INSPECTION REPORT (eSign / OTP)".

Every screen must exist in **both light and dark** themes. Desktop screens default to light; the expert PWA defaults to dark. Users can switch.

### 7.2 Tokens (`apps/web/styles/tokens.css`)

```css
:root {
  --radius: 12px;
  /* brand */
  --brand-navy: #0E1A33;        /* top bar */
  --brand-navy-2: #15264A;
  --primary: #2563EB;           /* CLEAR STAGE, Compare */
  --primary-fg: #FFFFFF;
  --ai: #6D28D9;                /* AI accents */
  --ai-soft: #EDE9FE;
  --success: #16A34A;  --success-soft: #DCFCE7;
  --warning: #D97706;  --warning-soft: #FEF3C7;
  --danger:  #DC2626;  --danger-soft:  #FEE2E2;
  --gold-1: #B88A1E;   --gold-2: #E2B84A;  /* SLA pill */
  /* surfaces (light) */
  --bg: #F4F6FA;
  --surface: #FFFFFF;
  --surface-2: #F8FAFC;
  --border: #E3E8EF;
  --text: #0F172A;
  --text-muted: #5B6475;
  --topbar-bg: var(--brand-navy);
  --topbar-fg: #FFFFFF;
}
:root:not([data-theme="light"]) { }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0A1120; --surface: #111A2E; --surface-2: #16213A;
    --border: #22314F; --text: #E6EAF2; --text-muted: #94A0B8;
    --topbar-bg: #070D1A;
    --success-soft: #0F2E1D; --warning-soft: #33250A; --danger-soft: #3A1414; --ai-soft: #231A3F;
  }
}
:root[data-theme="dark"] {
  --bg: #0A1120; --surface: #111A2E; --surface-2: #16213A;
  --border: #22314F; --text: #E6EAF2; --text-muted: #94A0B8;
  --topbar-bg: #070D1A;
  --success-soft: #0F2E1D; --warning-soft: #33250A; --danger-soft: #3A1414; --ai-soft: #231A3F;
}
body { background: var(--bg); color: var(--text); }
```

Map tokens into Tailwind v4 via `@theme inline` so classes like `bg-surface`, `text-muted`, `border-border` work. `next-themes` sets `data-theme`.

### 7.3 Typography

- Latin: **Inter** (UI), **JetBrains Mono** (IDs, hashes).
- Indic scripts (mandatory for i18n): **Noto Sans Devanagari** (hi), **Noto Sans Telugu** (te), **Noto Sans Malayalam** (ml), **Noto Sans Tamil** (ta), **Noto Sans Kannada** (kn). Load via `next/font/google`; apply the script font by locale on `<html lang>`.
- Scale: 12 / 14 (body) / 16 / 20 / 24 / 30. Indic locales get `line-height: 1.6` (vs 1.5) to avoid clipping.

### 7.4 Layout specs

- **Top bar**: 72px, `--topbar-bg`. Left: emblem + AICTE logo (32px each, from `/public/brand/`, team-provided assets) + two-line title. Center: search (max-w 520px, placeholder "Search App ID, Institution, Faculty ID…"). Right: `SlaPill` (only on case pages), mail/WhatsApp/bell `IconBadge`s (counts from SSE), language switcher, theme toggle, profile menu.
- **Sidebar**: 240px, collapsible to 72px; active item gets left 3px primary bar + `surface-2` background. Items are role-dependent (§15). Bottom: Settings + version chip.
- **Case Review grid**: `grid-cols-[340px_minmax(0,1fr)_380px] gap-6` at ≥1280px; below 1280px the AI panel becomes a right drawer; below 768px all stack with tabs.
- **ActionBar**: sticky bottom, 72px, `surface` + top border; buttons right-aligned; left shows "AICTE GOV.IN" footer text.
- **Mobile (expert)**: 16px gutters, cards with 16px radius, bottom fixed area for dictate + submit (safe-area insets).

### 7.5 Core components (build in P1, reuse everywhere)

| Component | Spec |
|---|---|
| `StatusChip` | Application/stage statuses with color mapping; i18n labels |
| `SlaBadge` | 🟢 on-time / 🟡 due ≤ 3 days / 🔴 overdue + "N days" |
| `SlaPill` | Gold gradient pill: "{n} Days Left (On-Time)" / amber "Due Soon" / red "{n} Days Overdue" |
| `ConfidenceRing` | SVG ring 0–100, green ≥ 90, amber 70–89, red < 70 |
| `StageStepper` | Vertical (case file) and horizontal (institution) variants; states done/active/pending/returned |
| `CheckCard` | Icon + title + status tag + body (AI panel cards) |
| `AiBadge` | Purple "AI" chip with tooltip "AI-generated — verify before use" |
| `DocViewer` | react-pdf + zoom/page/rotate + highlight overlay (`boxes: {page,x,y,w,h,label,status}`) + compare mode |
| `Timeline` | Vertical event list with actor, time, icon, channel |
| `AuditDrawer` | Right drawer listing hash-chained events for an entity |
| `FileDropzone` | Drag-drop, progress, OCR status |
| `IconBadge` | Icon + count bubble |
| `KpiCard`, `AgeingChart`, `IndiaHeatmap`, `DataTable` | Dashboards |
| `ChannelIcons` | 📧💬📱📞 with delivered/read/failed states |
| `AiAssistantPanel` | Slide-in chat with citations |
| Expert: `GpsPill`, `CameraCapture`, `PhotoOverlay`, `ChecklistTabs`, `DictateButton`, `OfflineSyncPill` | PWA |

---

## 8. Internationalisation (6 languages)

| Code | Language | Intl locale | TTS/STT locale |
|---|---|---|---|
| `en` | English | `en-IN` | `en-IN` |
| `hi` | हिन्दी | `hi-IN` | `hi-IN` |
| `te` | తెలుగు | `te-IN` | `te-IN` |
| `ml` | മലയാളം | `ml-IN` | `ml-IN` |
| `ta` | தமிழ் | `ta-IN` | `ta-IN` |
| `kn` | ಕನ್ನಡ | `kn-IN` | `kn-IN` |

Rules:
- Routes are locale-prefixed: `/{locale}/officer/queue`. Default `en`. Language switcher preserves the path.
- `messages/<code>.json` with namespaces: `common, auth, nav, status, sla, institution, officer, regional, bureau, committee, expert, admin, public, notifications, errors`.
- Author `en.json` first. `pnpm i18n:translate` runs a script that machine-translates missing keys into the other 5 locales via the LLM, writing `"__review": true` metadata into `messages/_review/<code>.json` for human review.
- `pnpm i18n:check` fails CI if any key is missing in any locale.
- Dates/numbers via `Intl` with the locale (Indian digit grouping `12,45,000`). Dates shown as `24 Oct 2026`, times in IST.
- User's `preferred_language` (profile) drives **all outbound notifications and voice calls**. Message templates exist per language (§11.5).
- Backend error messages return `code` + `params`; the frontend translates.
- Data content (institution names, uploaded docs) is not translated.

---

## 9. Workflow engine

### 9.1 Concepts

- **ApplicationType**: e.g. `NEW_INSTITUTION`, `EXTENSION_OF_APPROVAL`, `INTAKE_INCREASE`, `NEW_COURSE`, `CHANGE_OF_NAME`, `CHANGE_OF_SITE`, `CLOSURE`, `COLLABORATION`.
- **WorkflowRule** (versioned, per application type + academic year): ordered list of stages, SLA per stage, allowed actions, auto-advance flags, clock-pause rules, conditional branches.
- **StageInstance**: runtime instance of a rule stage for one application.
- **Deadline**: a generic timer attached to any subject (stage instance, compliance item, EVC visit, document validity). Drives reminders/escalations (§10).

> Seed pathways and SLA values below are **illustrative defaults**. They are configurable in the admin UI and must be aligned with the current APH by the team before the demo.

| Pathway (PS) | Example application types (seed) |
|---|---|
| Only Scrutiny | `EXTENSION_OF_APPROVAL` (no change), `CHANGE_OF_NAME` |
| Scrutiny + EVC | `INTAKE_INCREASE`, `NEW_COURSE` |
| EVC + SAC/SHC | `CHANGE_OF_SITE` |
| Only EVC | `COLLABORATION` (illustrative) |
| Scrutiny + EVC + SAC/SHC | `NEW_INSTITUTION` |

### 9.2 Rule JSON schema (`packages/contracts/workflow-rule.schema.json`)

```json
{
  "rule_id": "NEW_INSTITUTION@2026-27@v1",
  "application_type": "NEW_INSTITUTION",
  "academic_year": "2026-27",
  "version": 1,
  "stages": [
    {
      "key": "AI_PRECHECK",
      "label_key": "stage.ai_precheck",
      "kind": "SYSTEM",
      "sla_days": 0,
      "auto_complete": true
    },
    {
      "key": "SCRUTINY",
      "label_key": "stage.scrutiny",
      "kind": "HUMAN",
      "role": "SCRUTINY_OFFICER",
      "sla_days": 15,
      "sla_calendar": "WORKING_DAYS",
      "actions": ["CLEAR", "RETURN", "REFER_SENIOR", "REQUEST_CLARIFICATION"],
      "pause_clock_on": ["RETURN", "REQUEST_CLARIFICATION"],
      "max_returns": 2,
      "auto_advance_on_clear": true,
      "escalation_policy": "STAGE_DEFAULT"
    },
    {
      "key": "EVC",
      "label_key": "stage.evc",
      "kind": "HUMAN",
      "role": "REGIONAL_OFFICER",
      "sla_days": 30,
      "sla_calendar": "CALENDAR_DAYS",
      "sub_steps": ["QUEUED", "PANEL_PROPOSED", "SCHEDULED", "VISITED", "REPORT_SUBMITTED"],
      "actions": ["SCHEDULE", "RESCHEDULE", "COMPLETE"],
      "auto_advance_on_clear": true,
      "escalation_policy": "EVC_DEFAULT"
    },
    {
      "key": "SAC",
      "label_key": "stage.sac",
      "kind": "HUMAN",
      "role": "COMMITTEE_MEMBER",
      "sla_days": 10,
      "actions": ["DECIDE"],
      "decisions": ["APPROVE", "APPROVE_WITH_CONDITIONS", "REJECT", "DEFER"],
      "requires_otp": true,
      "escalation_policy": "STAGE_DEFAULT"
    }
  ],
  "branches": [
    { "when": "application.payload.is_government == true && stage == 'SAC'", "skip": false }
  ]
}
```

Branch conditions use a tiny safe expression evaluator (e.g. `simpleeval` with a whitelisted context). No `eval`.

### 9.3 State machines

**Application.status**
```
DRAFT → SUBMITTED → IN_PROCESS → DECISION_RECORDED → {APPROVED | APPROVED_WITH_CONDITIONS | REJECTED}
                    ↘ WITHDRAWN (by institution, before decision)
```
`Application.current_stage_key` points to the active stage while `IN_PROCESS`.

**StageInstance.status**
```
PENDING → ACTIVE → COMPLETED
            │  ↘ RETURNED (clock paused) → ACTIVE (on resubmission; re-scrutiny counter++)
            ↘ REFERRED (to senior; clock continues) → ACTIVE
SKIPPED (by branch)
```

### 9.4 Engine API (`services/workflow/engine.py`)

```python
route(application_id) -> None                    # on submit: load rule, create stage instances, activate first
perform_action(stage_instance_id, action, actor, payload) -> StageInstance
    # validates role/region, action allowed, required fields (reason for RETURN),
    # applies transition, pauses/resumes deadline, writes audit + outbox in one tx
advance(application_id) -> None                  # activates next non-skipped stage or moves to DECISION_RECORDED
resubmit(application_id, actor, payload) -> None # institution response to RETURN
```

Every transition:
1. Locks the application row (`SELECT … FOR UPDATE`).
2. Validates with the rule.
3. Updates stage + application.
4. Creates/pauses/resumes/closes the `deadline`.
5. Writes `stage_transitions` row, `audit_events` row, `outbox_events` row.
6. Commits. Workers react to events (notifications, SSE).

### 9.5 Assignment (workload balancer)

On `stage.activated` for a HUMAN stage: pick a user with the stage role in the application's region, minimising `weighted_load = Σ(active cases × weight by days-in-stage) + overdue × 3`. Ties → round-robin. Write `assignments` row. Regional officer can reassign (audit reason required).

### 9.6 Returns and deficiencies

- RETURN requires ≥ 1 deficiency (from master list or custom) + free-text reason.
- Engine pauses the stage deadline, creates `deficiency_letter` (AI draft → officer edits → sends).
- Institution responds per deficiency (re-upload + remark) → `resubmit` → stage ACTIVE again, deadline resumes, re-scrutiny count increments. If `max_returns` exceeded, the action panel shows a warning (decision remains human).

### 9.7 Integration adapter

`integrations/aicte_adapter.py` defines an interface (`fetch_institution`, `fetch_faculty_registry`, `push_status`) with a **mock implementation** backed by seed data. Documented as the integration seam for existing AICTE systems.

---

## 10. Deadline, reminder and escalation engine

### 10.1 Unified `deadlines` table

Every time-bound obligation is a deadline:

| subject_type | Created when | Due date |
|---|---|---|
| `STAGE` | stage activated | activated_at + sla_days (working/calendar) |
| `EVC_VISIT` | EVC queued | queued_at + evc sla |
| `EVC_REPORT` | visit date passes | visit_date + report sla (e.g. 3 days) |
| `UNDERTAKING` | committee condition created | condition due date |
| `DOCUMENT_VALIDITY` | document with expiry verified | expiry date |
| `DEFICIENCY_RESPONSE` | stage returned | returned_at + response window (e.g. 30 days) |
| `COMMITMENT` | voice/WhatsApp commitment recorded | committed date |

Fields: `due_at`, `status (OPEN|PAUSED|MET|BREACHED|CANCELLED)`, `paused_at`, `paused_total_seconds`, `policy_key`, `last_step_index`, `owner_party (INSTITUTION|OFFICER|EXPERT|COMMITTEE)`, `escalation_level`.

Effective due = `due_at + paused_total` (+ current pause). Working-day calendars skip weekends + `holidays` table.

### 10.2 Escalation policy schema (`escalation-policy.schema.json`)

```json
{
  "key": "DOCUMENT_VALIDITY_DEFAULT",
  "applies_to": ["DOCUMENT_VALIDITY", "UNDERTAKING"],
  "steps": [
    { "offset_days": -30, "action": "NOTIFY", "to": ["INSTITUTION"], "channels": ["EMAIL"], "template": "doc_expiry_reminder" },
    { "offset_days": -15, "action": "NOTIFY", "to": ["INSTITUTION"], "channels": ["EMAIL"], "template": "doc_expiry_reminder" },
    { "offset_days": -7,  "action": "NOTIFY", "to": ["INSTITUTION"], "channels": ["EMAIL", "WHATSAPP"], "template": "doc_expiry_upload_link" },
    { "offset_days": -2,  "action": "CALL",   "to": ["INSTITUTION"], "call_purpose": "DOC_EXPIRY_REMINDER", "fallback_channels": ["SMS"] },
    { "offset_days": 0,   "action": "NOTIFY", "to": ["INSTITUTION", "ASSIGNED_OFFICER"], "channels": ["EMAIL", "WHATSAPP", "IN_APP"], "template": "doc_expired", "mark_breached": true },
    { "offset_days": 3,   "action": "ESCALATE", "level": 1, "to": ["REGIONAL_OFFICER"], "channels": ["EMAIL", "IN_APP"], "template": "escalation_l1" },
    { "offset_days": 7,   "action": "ESCALATE", "level": 2, "to": ["BUREAU_HEAD"], "channels": ["EMAIL", "IN_APP"], "template": "escalation_l2", "draft_show_cause": true }
  ],
  "quiet_hours": { "start": "20:00", "end": "09:00", "channels": ["WHATSAPP", "SMS", "CALL"] }
}
```

Seed policies: `STAGE_DEFAULT` (T-3 officer in-app+email, T0 officer+RO, T+3 RO escalation, T+7 Bureau), `EVC_DEFAULT` (expert ack reminder, expert call if unacknowledged, RO escalation on delay), `DOCUMENT_VALIDITY_DEFAULT` (above), `UNDERTAKING_DEFAULT` (same shape), `DEFICIENCY_RESPONSE_DEFAULT`, `COMMITMENT_DEFAULT` (on breach: immediate escalation L1 tagged "Broken commitment").

### 10.3 Scanner algorithm (`workers/tasks/deadline_scan.py`, beat every 60s)

```
now = clock.now()
for d in deadlines where status in (OPEN) and next_step_at <= now (indexed column):
    with lock(d):                                   # SELECT … FOR UPDATE SKIP LOCKED
        steps = policy(d).steps
        for i in range(d.last_step_index + 1, len(steps)):
            step = steps[i]
            if effective_due(d) + step.offset_days > now: break
            idem = f"{d.id}:{i}"
            if exists(notification_jobs where idempotency_key = idem): continue
            execute(step, d)                        # NOTIFY → orchestrator; CALL → call_jobs; ESCALATE → escalations row
            d.last_step_index = i
            if step.mark_breached: d.status = BREACHED; emit deadline.breached
        d.next_step_at = compute_next(d)
```

`next_step_at` is recomputed on pause/resume/time-travel so the scan only touches due rows. Time travel (§18) triggers an immediate scan.

### 10.4 Escalation records

`escalations(id, deadline_id, level, raised_to_role, raised_to_user, reason, status OPEN|ACKNOWLEDGED|ACTIONED|CLOSED, actions jsonb)`. Shown in Regional (L1) / Bureau (L2) Escalation Inbox. Actions: Take Action (opens case), Reassign, Issue Warning (sends template), Forward, Draft Show-Cause (AI draft for authority — never auto-sent).

---

## 11. Messaging via Twilio (Email, WhatsApp, SMS, OTP)

### 11.1 Notification orchestrator

```
notify(intent: NotificationIntent)
  intent = { template_key, subject_ref, recipients_spec, channels, priority, idempotency_key, context }
  1. resolve recipients (role/assigned/institution contacts) → users/contacts
  2. per recipient: language = preferred_language; channel filter by consent + quiet hours
  3. render template(template_key, channel, language, context)
  4. create notifications + notification_deliveries rows (status=QUEUED)
  5. enqueue send task per delivery (Celery, retries with backoff ×3)
  6. provider callback updates status: SENT → DELIVERED → READ / FAILED / BOUNCED
  7. IN_APP channel → SSE push to the user's bell
```

Channel fallback: if a delivery FAILS (e.g. WhatsApp undeliverable), send the same template via SMS automatically.

### 11.2 Email — Twilio SendGrid

- Outbound: SendGrid v3 Mail Send with dynamic templates or server-rendered HTML (MJML → HTML at build). Categories = template key; custom args = `delivery_id`.
- Event Webhook → `/api/v1/webhooks/sendgrid/events` (verify signature with `SENDGRID_WEBHOOK_PUBLIC_KEY`) → update delivery status (delivered/open/bounce).
- **Reply-to token**: every case email uses `Reply-To: case+{case_token}@reply.<domain>` where `case_token` is a signed short token mapping to `(subject_type, subject_id, institution_id)`.
- **Inbound Parse** on `reply.<domain>` → `/api/v1/webhooks/sendgrid/inbound` (multipart). Steps: verify sender email is a registered institution contact for that token → store raw email in `inbound_messages` → attachments to MinIO → LLM intent classification (`SUBMISSION | EXTENSION_REQUEST | QUERY | OTHER`) → if SUBMISSION and the subject is a compliance item: create `compliance_submission` (source=EMAIL) and run Document AI → officer verification queue. Auto-acknowledge via email.

### 11.3 WhatsApp — Twilio Messaging

- Dev: Twilio WhatsApp Sandbox (each test phone must send the join code once).
- Production: approved WhatsApp sender + **pre-approved Content Templates** for business-initiated messages (outside the 24h window). Create templates per language in Twilio Content Template Builder: `doc_expiry_upload_link`, `undertaking_due`, `deficiency_raised`, `evc_visit_scheduled`, `escalation_notice`, `commitment_ack`, `grievance_ticket`.
- Outbound: `messages.create(from=TWILIO_WHATSAPP_FROM, to="whatsapp:+91…", content_sid=…, content_variables=…, status_callback=…)`.
- Upload links: short signed URL `PUBLIC_WEB_URL/u/{token}` (expires 7 days, single subject, OTP on open) → mobile-friendly upload page.
- **Inbound WhatsApp** webhook `/api/v1/webhooks/twilio/whatsapp` (validate `X-Twilio-Signature`): identify contact by `From`; if media attached and the contact has exactly one open compliance item (or replied within the window to a specific reminder) → create submission (source=WHATSAPP); otherwise reply with a numbered menu of their open items. Text-only messages → simple bot: "STATUS", "HELP", item number selection.

### 11.4 SMS and OTP

- **OTP**: Twilio Verify (`channel=sms` or `whatsapp`) for login and for eSign-style confirmations (committee decision, EVC report submit, compliance verification). In `DEMO_MODE`, `DEMO_FIXED_OTP` is accepted.
- **SMS alerts**: used only as fallback and for grievance ticket IDs. Note: commercial SMS to Indian numbers requires TRAI DLT registration (entity + templates). For the prototype, use Verify for OTP and keep SMS to fallback/demo numbers.

### 11.5 Templates

`message_templates(key, channel, language, subject, body, variables[], twilio_content_sid?)`. Seed all keys × 6 languages × channels (EMAIL, WHATSAPP, SMS, IN_APP). Variables use `{{institution_name}}`, `{{app_id}}`, `{{item_title}}`, `{{due_date}}`, `{{days_left}}`, `{{upload_link}}`, `{{officer_name}}`. Admin can edit and test-send (H3).

---

## 12. AI calling agent (Track V — Rishanth)

### 12.1 Architecture

```
Inbound:  Caller → Twilio number → POST /voice/inbound (TwiML)
          → <Gather> language menu (DTMF 1-6 or speech) → caller lookup (core) → persona
          → <Connect><ConversationRelay url="wss://…/relay?session=…" language=… /></Connect>
Outbound: core creates call_job → POST /voice/calls (voice service) → Twilio calls.create(url=/voice/outbound-twiml?job=…, machine_detection=Enable)
          → human answered → ConversationRelay with persona=INSTITUTION and job context
          → machine/no answer → hang up, report NO_ANSWER → core fallback SMS/WhatsApp + retry policy
Session:  relay_ws receives transcribed caller text → brain (LLM, streaming, tools) → text tokens back → Twilio TTS
Post-call: transcript + tool calls → LLM structured outcome → POST core /internal/voice/calls/{id}/outcome
Recording: Twilio call recording (dual channel) → URL stored in core; announce recording at call start
```

Verify the exact ConversationRelay TwiML attributes, WebSocket message types and per-language STT/TTS provider support in current Twilio docs before V2. If Telugu/Malayalam/Tamil/Kannada quality or support is insufficient, switch that language to the **fallback path**: `<Connect><Stream>` (Media Streams, μ-law 8kHz) → Sarvam STT → brain → Sarvam TTS → audio back on the stream. Keep the brain identical; only the speech adapter changes (`speech/` module).

### 12.2 IVR routing (`ivr.py`)

1. Greeting (bilingual EN + HI): "Welcome to the AICTE Approval and Compliance helpline. This call may be recorded."
2. Language: "For English press 1, हिन्दी के लिए 2 दबाएं, తెలుగు కోసం 3, മലയാളത്തിന് 4, தமிழுக்கு 5, ಕನ್ನಡಕ್ಕೆ 6." Also accept spoken language name. Default to caller's `preferred_language` if the number is known.
3. Caller lookup: `GET /internal/voice/callers/lookup?phone=` →
   - Known institution contact → persona `INSTITUTION`
   - Known officer → persona `OFFICER` (then ask PIN)
   - Known faculty → persona `FACULTY` (then OTP)
   - Unknown → persona `PUBLIC`, with "If you are an AICTE official or institution staff, press 9" → identify flow.
4. Connect to relay with `session` (signed JWT containing call_sid, persona, language, caller_ref, job_id?).

### 12.3 Persona behaviour

**INSTITUTION (outbound primary)** — call purposes: `DEFICIENCY_PENDING`, `UNDERTAKING_DUE`, `UNDERTAKING_OVERDUE`, `DOC_EXPIRY_REMINDER`, `DOC_EXPIRED`, `EVC_VISIT_CONFIRM`, `BROKEN_COMMITMENT`.

Script skeleton (LLM follows, not verbatim):
1. Identify: "This is an automated call from AICTE's Approval and Compliance Portal for {institution_name}. Am I speaking with {contact_name}?" (if not, ask for the nodal officer or callback time → `schedule_callback`).
2. Inform: item, due date, consequence (neutral, factual).
3. Ask: "By when will you submit it?" → normalise to a date → `record_commitment(date, reason)`. Reject dates beyond policy max (e.g. > 30 days) by explaining that only the competent authority can grant extensions and offering to log an extension request for officer review (`log_extension_request` — creates a request, does not grant).
4. Offer: "Shall I send the upload link on WhatsApp?" → `send_upload_link(channel)`.
5. Answer questions using `search_aph` (cite section); if unsure → `handoff_to_officer` (creates a callback task for the assigned officer).
6. Close: summarise commitment and next step.

**OFFICER (inbound)** — after PIN: tools `get_my_queue_summary`, `get_case_status`, `get_region_stats`, `list_overdue`, `email_report(report_type, filters)`, `trigger_reminder(subject)` (requires spoken "yes, confirm"). Answers short and numeric, offers email for details.

**PUBLIC (students/parents)** — tools `search_institutions(name, city/state)`, `get_public_approval_status(institution_id, academic_year)` (approved courses + intake), `create_grievance(category, description, institution_ref?)` → returns ticket ID and sends SMS. Never reveal non-public data (pending applications, deficiencies, officer names). Rate limit: 5 lookups per call, 10 calls/day per number.

**FACULTY** — after OTP: `get_faculty_status(faculty_id)` (registration status, institutions currently associated, duplicate flag if any), explain resolution steps, `create_grievance(category="FACULTY_RECORD")`.

**EXPERT (optional)** — `get_visit_checklist(visit_id)`; ask checklist questions section by section; `save_evc_report_draft(visit_id, answers[])`; tell the expert to review and submit in the app.

### 12.4 Guardrails (`guardrails.py`)

- System prompt (all personas): role statement, regulatory safeguard, allowed/forbidden actions, never speculate on approval outcomes, never promise extensions, privacy rules, keep answers ≤ 2 sentences unless asked, speak in session language, numbers spoken naturally in that language.
- Tool allowlist per persona; tools validate persona server-side again (core checks `X-Voice-Persona` + session claims).
- PII redaction in stored transcripts for OTP digits and phone numbers (keep last 4).
- Barge-in supported (interrupt handling) — stop TTS on caller speech.
- Silence: 2 reprompts then polite close. Max call length 6 min (public) / 10 min (others).
- Profanity/abuse: one warning, then polite termination.
- If the LLM proposes a forbidden action, `guardrails` replaces with refusal text + `handoff_to_officer`.

### 12.5 Tool schemas (`packages/contracts/voice-tools.json`)

```json
[
  {"name":"get_case_status","persona":["INSTITUTION","OFFICER"],"params":{"app_id":"string"}},
  {"name":"get_open_items","persona":["INSTITUTION"],"params":{}},
  {"name":"record_commitment","persona":["INSTITUTION"],"params":{"subject_ref":"string","committed_date":"date","reason":"string"}},
  {"name":"log_extension_request","persona":["INSTITUTION"],"params":{"subject_ref":"string","requested_date":"date","reason":"string"}},
  {"name":"send_upload_link","persona":["INSTITUTION"],"params":{"subject_ref":"string","channel":"WHATSAPP|SMS|EMAIL"}},
  {"name":"schedule_callback","persona":["INSTITUTION"],"params":{"when":"datetime","contact_name":"string"}},
  {"name":"search_aph","persona":["INSTITUTION","OFFICER","PUBLIC","FACULTY"],"params":{"query":"string","language":"string"}},
  {"name":"handoff_to_officer","persona":["INSTITUTION","FACULTY","PUBLIC"],"params":{"summary":"string"}},
  {"name":"verify_officer_pin","persona":["OFFICER"],"params":{"pin":"string"}},
  {"name":"get_my_queue_summary","persona":["OFFICER"],"params":{}},
  {"name":"get_region_stats","persona":["OFFICER"],"params":{"region_code":"string?"}},
  {"name":"list_overdue","persona":["OFFICER"],"params":{"kind":"STAGE|UNDERTAKING|DOCUMENT|EVC","limit":"int"}},
  {"name":"email_report","persona":["OFFICER"],"params":{"report_type":"string","filters":"object"}},
  {"name":"trigger_reminder","persona":["OFFICER"],"params":{"subject_ref":"string","confirmed":"boolean"}},
  {"name":"search_institutions","persona":["PUBLIC"],"params":{"name":"string","state":"string?"}},
  {"name":"get_public_approval_status","persona":["PUBLIC"],"params":{"institution_id":"string","academic_year":"string?"}},
  {"name":"create_grievance","persona":["PUBLIC","FACULTY"],"params":{"category":"string","description":"string","institution_ref":"string?"}},
  {"name":"send_otp","persona":["FACULTY","OFFICER"],"params":{}},
  {"name":"check_otp","persona":["FACULTY","OFFICER"],"params":{"code":"string"}},
  {"name":"get_faculty_status","persona":["FACULTY"],"params":{"faculty_id":"string"}},
  {"name":"get_visit_checklist","persona":["EXPERT"],"params":{"visit_id":"string"}},
  {"name":"save_evc_report_draft","persona":["EXPERT"],"params":{"visit_id":"string","answers":"array"}}
]
```

### 12.6 Outbound dialer (`dialer.py`)

- Receives `POST /voice/calls` from core with `{call_job_id, to, persona, purpose, language, context}` (HMAC-signed with `INTERNAL_SERVICE_TOKEN`).
- Enforces calling hours (`CALL_HOURS_START–END` IST, Mon–Sat, not in `holidays`) — otherwise reschedules.
- Places call with AMD; on `completed`/`no-answer`/`busy`/`failed` status callback → report to core. Retries per `CALL_MAX_ATTEMPTS` / `CALL_RETRY_GAP_MIN` (core owns retry scheduling; voice service is stateless about policy).
- Twilio trial accounts can only call **verified** numbers — verify the judges'/team phones before demo. Outbound calls to Indian numbers may show an international caller ID; note Exotel/Plivo as production India alternatives in the pitch.

### 12.7 Post-call processing (`postcall.py`)

LLM converts transcript + tool log into:
```json
{
  "outcome": "COMMITTED | NO_ANSWER | WRONG_PERSON | REFUSED | NEEDS_HELP | INFO_ONLY | CALLBACK_SCHEDULED | GRIEVANCE_CREATED",
  "summary": "2-3 sentence summary in English",
  "summary_local": "same in call language",
  "committed_date": "2026-11-20",
  "reason": "Lease registration pending at sub-registrar",
  "sentiment": "neutral",
  "follow_ups": ["upload link sent via WhatsApp"]
}
```
→ `POST /internal/voice/calls/{call_id}/outcome` with transcript (redacted) + recording URL. Core creates timeline event, commitment deadline, audit entry.

### 12.8 In-app dictation (Expert PWA "Hold to Dictate")

`POST /voice/dictation` (multipart audio webm/opus + `visit_id` + `section` + `language`) → STT (same provider as fallback path) → LLM maps speech to checklist items `{item_key, status YES|NO|PARTIAL, remark}` with confidence → returns draft JSON; the PWA highlights AI-filled fields for confirmation. Core persists only after the expert saves.

---

## 13. Core API specification (`/api/v1`)

All endpoints return JSON, errors as `{ "error": { "code": "STAGE_ACTION_NOT_ALLOWED", "params": {...} } }`. Auth via httpOnly cookie JWT. List endpoints support `?page&page_size&sort&filter[...]`.

### 13.1 Auth & users
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/otp/send` | `{identifier, role}` → Twilio Verify |
| POST | `/auth/otp/verify` | sets cookies, returns user + roles + regions |
| POST | `/auth/refresh` | |
| POST | `/auth/logout` | |
| GET/PATCH | `/me` | profile, `preferred_language`, `theme`, notification prefs, call opt-in/time |
| POST | `/me/voice-pin` | officers set 6-digit voice PIN (hashed) |

### 13.2 Institutions & applications
| Method | Path | Notes |
|---|---|---|
| GET | `/institutions/{id}` | scoped |
| GET | `/institutions/{id}/health` | compliance health score |
| GET/POST | `/applications` | list (scoped) / create draft |
| GET/PATCH | `/applications/{id}` | draft edit |
| POST | `/applications/{id}/precheck` | runs Document AI on all docs, returns report |
| POST | `/applications/{id}/submit` | OTP required → `route()` |
| POST | `/applications/{id}/withdraw` | |
| POST | `/applications/{id}/resubmit` | deficiency responses |
| GET | `/applications/{id}/timeline` | |
| GET | `/applications/{id}/communications` | emails/WA/calls |
| GET | `/application-types` | with required document list |

### 13.3 Stages & officer work
| Method | Path | Notes |
|---|---|---|
| GET | `/queue` | officer's assigned active stages + SLA + AI flag count |
| GET | `/stages/{id}` | stage instance + allowed actions |
| POST | `/stages/{id}/actions` | `{action: CLEAR|RETURN|REFER_SENIOR|REQUEST_CLARIFICATION, reason, deficiencies[], letter_id?}` |
| POST | `/stages/{id}/deficiency-letter/draft` | AI draft |
| PUT | `/deficiency-letters/{id}` | edit |
| POST | `/deficiency-letters/{id}/send` | |
| POST | `/assignments/{id}/reassign` | RO only, reason |

### 13.4 Documents & AI
| Method | Path | Notes |
|---|---|---|
| POST | `/documents` | upload (multipart) → MinIO → enqueue `ocr_extract` |
| GET | `/documents/{id}` | metadata + signed URL + versions |
| GET | `/documents/{id}/extraction` | fields, boxes, confidence, validations |
| POST | `/documents/{id}/verify` | officer verifies extracted fields (sets validity deadline if expiry) |
| GET | `/applications/{id}/ai-report` | pre-scrutiny report (checks, flags, confidence) |
| GET | `/applications/{id}/case-summary` | AI e-case summary (generate if missing) |
| POST | `/ai/assistant` | RAG chat `{messages[], language}` → `{answer, citations[]}` |

### 13.5 EVC
| Method | Path | Notes |
|---|---|---|
| GET | `/evc/queue` | |
| POST | `/evc/{visit_id}/propose-panel` | scheduler suggestion |
| POST | `/evc/{visit_id}/schedule` | confirm panel + date → invites (email + Google-Calendar-compatible .ics) |
| GET | `/expert/visits` | expert's visits |
| POST | `/expert/visits/{id}/accept` / `/decline` | |
| GET | `/expert/visits/{id}/checklist` | |
| POST | `/expert/visits/{id}/photos` | photo + EXIF/GPS + client hash; server re-hashes, checks geofence |
| PUT | `/expert/visits/{id}/report` | save draft |
| POST | `/expert/visits/{id}/report/submit` | OTP → completes EVC sub-step |

### 13.6 Committee
| Method | Path | Notes |
|---|---|---|
| GET/POST | `/committee/meetings` | |
| GET | `/committee/meetings/{id}/agenda` | + agenda pack PDF |
| POST | `/committee/cases/{application_id}/decision` | OTP; `{decision, remarks, conditions:[{title, evidence_type, due_date, document_type?}]}` |
| GET | `/committee/meetings/{id}/minutes/draft` | AI draft minutes |

### 13.7 Compliance & documents validity
| Method | Path | Notes |
|---|---|---|
| GET | `/compliance/items` | institution: own; officer: scope; filters status/kind/due |
| GET | `/compliance/calendar` | month view data |
| POST | `/compliance/items/{id}/submissions` | upload evidence |
| GET | `/compliance/verification-queue` | officer |
| POST | `/compliance/submissions/{id}/verify` | `{result: VERIFIED|REJECTED, reason}` OTP |
| GET | `/documents/expiring` | `?within_days=30` |
| POST | `/compliance/items/{id}/remind` | manual reminder |
| POST | `/compliance/items/{id}/call` | manual AI call trigger |
| GET | `/u/{token}` | upload-link resolve (public, OTP-gated) |

### 13.8 Notifications, calls, escalations
| Method | Path | Notes |
|---|---|---|
| GET | `/notifications` | bell list |
| POST | `/notifications/{id}/read` | |
| GET | `/calls` | call log (scoped), filters outcome/purpose |
| GET | `/calls/{id}` | transcript, recording URL, outcome |
| GET | `/escalations` | inbox by level |
| POST | `/escalations/{id}/actions` | `{action: ACK|REASSIGN|WARN|FORWARD|DRAFT_SHOW_CAUSE, ...}` |
| GET | `/commitments` | incl. broken |

### 13.9 Dashboards & reports
| Method | Path | Notes |
|---|---|---|
| GET | `/dashboards/institution` | |
| GET | `/dashboards/officer` | |
| GET | `/dashboards/regional?region=` | KPIs, ageing, workload, bottlenecks |
| GET | `/dashboards/national` | + region heat data |
| GET | `/analytics/*` | stage times, return rates, top deficiencies, channel effectiveness |
| GET | `/predictions/breach-risk` | list with probability |
| POST | `/reports/run` | `{type, format: PDF|XLSX, filters}` → job id |
| GET | `/reports/{job_id}` | status + download URL |
| CRUD | `/reports/schedules` | weekly/monthly email |
| GET | `/stream` | SSE (notifications, dashboard deltas) |

### 13.10 Admin
CRUD under `/admin/`: `workflow-rules` (+ `/validate`, `/publish`), `escalation-policies`, `templates` (+ `/test-send`), `voice-config` (scripts, voices, hours, retries, `/test-call`), `document-types`, `users`, `roles/permissions`, `experts`, `regions`, `holidays`, `deficiency-reasons`, `application-types`. `GET /admin/system-health`.

### 13.11 Audit
| GET | `/audit?entity_type&entity_id&actor&from&to` | |
| GET | `/audit/verify?entity_id=` | recomputes chain → `{ok, broken_at?}` |
| GET | `/audit/export?entity_id=` | PDF |

### 13.12 Public & faculty
| GET | `/public/institutions?q=&state=` | public search |
| GET | `/public/institutions/{id}/approval?year=` | approved courses + intake |
| POST | `/public/grievances` | captcha/rate limit |
| POST | `/faculty/otp/send`, `/faculty/otp/verify` | Faculty ID + registered mobile |
| GET | `/faculty/me` | status, associations, flags |

### 13.13 Internal voice API (contract between Track P and Track V)

Base `/internal/voice`, header `Authorization: Bearer ${INTERNAL_SERVICE_TOKEN}`, plus `X-Voice-Session: <session JWT>` (persona + caller ref). Core re-checks persona permissions.

| Method | Path | Maps to tool |
|---|---|---|
| GET | `/callers/lookup?phone=` | IVR |
| GET | `/institutions/{id}/open-items` | `get_open_items` |
| GET | `/cases/{app_id}/status` | `get_case_status` |
| POST | `/commitments` | `record_commitment` |
| POST | `/extension-requests` | `log_extension_request` |
| POST | `/upload-links` | `send_upload_link` |
| POST | `/callbacks` | `schedule_callback`, `handoff_to_officer` |
| POST | `/aph/search` | `search_aph` |
| POST | `/officers/verify-pin` | `verify_officer_pin` |
| GET | `/officers/me/queue-summary` | `get_my_queue_summary` |
| GET | `/stats/region` | `get_region_stats` |
| GET | `/overdue` | `list_overdue` |
| POST | `/reports/email` | `email_report` |
| POST | `/reminders` | `trigger_reminder` |
| GET | `/public/institutions` | `search_institutions` |
| GET | `/public/institutions/{id}/approval` | `get_public_approval_status` |
| POST | `/grievances` | `create_grievance` |
| POST | `/otp/send`, `/otp/check` | OTP tools |
| GET | `/faculty/{faculty_id}` | `get_faculty_status` |
| GET | `/evc/visits/{id}/checklist` | `get_visit_checklist` |
| PUT | `/evc/visits/{id}/report-draft` | `save_evc_report_draft` |
| POST | `/calls` | voice service registers inbound call start |
| POST | `/calls/{id}/status` | Twilio status relay |
| POST | `/calls/{id}/outcome` | post-call result |

Core → voice: `POST ${PUBLIC_VOICE_URL}/voice/calls` (HMAC-signed body) for outbound jobs.

Track V develops against a **Prism mock** of `packages/contracts/openapi.json` (internal tag) until P8.

---

## 14. AI services (Track P)

| Service | Input | Output | Notes |
|---|---|---|---|
| **Document extraction** (`docai/extract.py`) | PDF/image + `document_type` | `{fields: {name: {value, confidence, page, box}}, raw_text}` | 1) render pages to PNG (pdf2image); 2) Tesseract/Paddle OCR for words + boxes; 3) vision LLM with per-type JSON schema (e.g. lease: lessor, lessee, survey_no, land_area_acres, start_date, expiry_date, registration_no, stamp_duty_amount); 4) match extracted values to OCR boxes (fuzzy) for highlights |
| **Validators** (`docai/validators.py`) | extraction + application payload + norms table | check list `[{key, status PASS|WARN|FAIL, message_key, params}]` | land area vs declared and vs norm (configurable norms table), expiry ≥ required tenure, required docs present, name match |
| **Pre-scrutiny report** | all docs + validators + anomalies | report with `confidence` (weighted mean), cards: Duplicate Faculty, Land Area Norms, Infrastructure, Deficiencies/Anomalies | Drives AI panel |
| **Anomaly detection** (`ai/anomalies.py`) | faculty lists, photos, docs | flags | duplicate faculty IDs across institutions (SQL), perceptual hash (`imagehash`) for reused photos, stamp-duty sanity vs area (rule), document date inconsistencies |
| **Deficiency letter** (`ai/letters.py`) | selected deficiencies + case | formal letter draft in institution's language + English | officer edits before send |
| **e-Case summary** (`ai/summary.py`) | case data + scrutiny + EVC + flags | 1-page structured summary | cached, regenerated on change |
| **Show-cause draft** | escalation context | draft notice | authority-only, never auto-sent |
| **Committee minutes draft** | decisions | draft minutes | |
| **RAG assistant** (`ai/rag.py`) | question + language | answer + citations (APH section/page) | chunk APH (~800 tokens, 100 overlap), embed multilingual, pgvector cosine top-k=6, answer in user language, refuse if not in sources |
| **Breach prediction** (`ai/predict.py`) | open deadlines features | probability of breach | LightGBM on seeded history: features = stage, region, type, days_elapsed/sla, officer load, returns count, institution health; nightly retrain + re-score every 15 min |
| **Inbound email intent** | email text | intent + subject match | |
| **Institution health score** | compliance history | 0–100 | rule-based: on-time ratio, open overdue, expired docs, broken commitments |

All LLM calls go through `LLMClient` with: provider switch, JSON-schema-constrained output, retry, timeout, token logging, and a `DEMO_MODE` cache (same input → cached output) so the demo is fast and deterministic.

---

## 15. Frontend screens (route map)

Locale prefix omitted for brevity. ★ = MVP-critical for demo.

### 15.1 Common
- ★ `/login` — role tabs, identifier, OTP, language switcher, theme toggle.
- ★ Shell: TopBar, Sidebar, Breadcrumbs, NotificationCenter (drawer), AiAssistantPanel (drawer), ProfileSettings (`/settings`: language, theme, channels, call opt-in/time, voice PIN for officers).

### 15.2 Institution (`/institution`)
Sidebar: Dashboard · Applications · Compliance · Document Vault · Communications · Approvals · Settings
- ★ `/dashboard` — KPI cards (Active Applications, Pending Compliances, Expiring ≤30d, Overdue), Health Score gauge, upcoming deadlines, recent notifications, quick actions.
- ★ `/applications` — table.
- ★ `/applications/new` — 8-step wizard (type → institution → courses & intake → land & building → faculty (Excel import) → documents (type-aware checklist) → **AI pre-check result** → review + OTP submit).
- ★ `/applications/[id]` — header + horizontal StageStepper + tabs (Overview, Documents, Deficiencies [respond], EVC, Timeline, Communications).
- `/applications/[id]/respond` — per-deficiency re-upload.
- ★ `/compliance` — calendar + list toggle; item drawer with Submit Evidence.
- ★ `/compliance/[itemId]/submit` — upload + AI extraction preview.
- ★ `/vault` — documents with validity badges, versions, Upload Renewal.
- `/communications` — calls (play recording, transcript, committed date), emails, WhatsApp.
- `/approvals` — LoA/EoA downloads.

### 15.3 Scrutiny officer (`/officer`)
Sidebar: Dashboard · Queue · Applications · Compliance · Document Vault · Analytics · Settings (matches reference)
- ★ `/dashboard` — cards (Assigned, Due this week, Overdue, Returned, Verifications pending), Likely-to-breach list, today's tasks.
- ★ `/queue` — table sorted by effective due; AI flags column.
- ★ `/queue/[stageId]` — **Case Review Workspace (reference image 1)**: CaseFilePanel | DocViewer | AiPanel + ActionBar. Header SlaPill bound to this stage's deadline.
- ★ Return modal — deficiency checklist + reason + AI letter editor + send.
- ★ `/compliance/verify` — verification queue + verify screen (viewer + extraction + original condition).
- ★ `/compliance/expiring` — expiry monitor with Remind / Call now.
- ★ `/calls` — scheduled/completed AI calls, outcomes, broken commitments, Call now/Retry.

### 15.4 Regional officer (`/regional`)
- ★ `/dashboard` — KPIs, stage bar, ageing stacked bar, trend line, escalated-to-me, workload table.
- `/pendency` — drill-down (stage → officer → cases), bottleneck callouts, export.
- `/workload` — per-officer load, reassign.
- ★ `/escalations` — inbox L1 with actions + show-cause draft.
- ★ `/evc` — queue, calendar, delayed.
- ★ `/evc/[visitId]/schedule` — AI-suggested panel (match score, conflict checks), swap, date picker with availability, confirm.
- `/compliance` — region compliance oversight + institution health ranking.

### 15.5 Bureau head (`/bureau`)
- ★ `/dashboard` — IndiaHeatmap (toggle: pendency / compliance health / overdue), national KPIs, region comparison table, top escalations, breach predictions.
- ★ `/reports` — prebuilt reports, filters, PDF/XLSX, schedules.
- `/escalations` — L2 inbox.
- `/analytics` — stage time trends, return rates, top deficiencies, reminder channel effectiveness (email vs WhatsApp vs call resolution rate).

### 15.6 EVC expert PWA (`/expert`) — mobile-first, dark default
- `/` — upcoming visits, pending reports, accept/decline.
- `/visits/[id]` — institution info, map link, panel, AI case summary, pre-visit docs.
- ★ `/visits/[id]/inspect` — **reference image 2**: GpsPill (geofence check vs institution lat/long + radius), OfflineSyncPill, CameraCapture (getUserMedia rear camera; capture → canvas overlay with GPS/alt/timestamp → SHA-256 in browser → upload; server re-hashes), ChecklistTabs with item status + photos, DictateButton (hold to record → `/voice/dictation` → AI-filled highlights), Submit (OTP).
- Offline: checklist answers + photos queued in IndexedDB; background sync on reconnect; pill shows pending count.

### 15.7 Committee (`/committee`)
- `/dashboard`, `/meetings/[id]` (agenda + pack PDF)
- ★ `/cases/[applicationId]` — AI e-Case Summary, documents, EVC report + photos, decision form + **ConditionsBuilder** (title, evidence type, due date, linked document type) + OTP.
- `/meetings/[id]/minutes` — AI draft, edit, publish.

### 15.8 Admin (`/admin`)
- ★ `/workflow` — rule list; visual pathway builder (drag stages from palette: AI_PRECHECK, SCRUTINY, EVC, SAC, SHC; stage config side panel: SLA, calendar, role, actions, pause rules, escalation policy); validate + publish (new version).
- ★ `/escalation` — policy editor (timeline of steps with offset, action, recipients, channels, template).
- `/templates` — per key × channel × language editor, preview, test send.
- `/voice` — scripts per purpose/persona/language, voice selection, calling hours, retries, test call.
- `/document-types`, `/users`, `/permissions`, `/experts`, `/master-data` (regions, holidays, deficiency reasons, application types, norms table).
- ★ `/audit` — explorer + Verify Integrity + export.
- `/health` — scheduler last run, queue depth, delivery stats per channel, call stats, integrations status.

### 15.9 Public & faculty
- `/verify` — "Is this institution AICTE approved?" search + result (approved courses/intake by year) + grievance form. Shows helpline number for voice.
- `/faculty` — Faculty ID + OTP login → status, associations, flags, grievance.

### 15.10 Demo
- ★ `/demo` (ADMIN + DEMO_MODE only) — clock display, ⏩ +1d / +7d / +30d / set date, run scanner now, trigger specific step for a deadline, trigger AI call to any verified number, reset & reseed.

---

## 16. Audit trail (hash-chained)

```
audit_events(
  id bigserial pk, occurred_at timestamptz, actor_type (USER|SYSTEM|VOICE|AI), actor_id,
  action text, entity_type text, entity_id uuid, application_id uuid null,
  before jsonb, after jsonb, reason text, channel text, ip inet, user_agent text,
  prev_hash char(64), hash char(64)
)
hash = sha256(prev_hash || canonical_json({occurred_at, actor_type, actor_id, action, entity_type, entity_id, before, after, reason}))
```

- Single global chain; insertion serialized with `pg_advisory_xact_lock(42)` inside the business transaction.
- `GET /audit/verify` recomputes the chain (whole or from a checkpoint) and returns the first broken id.
- DB role for the app has `INSERT, SELECT` only on `audit_events` (no UPDATE/DELETE) — enforced in `init.sql`.
- Every notification, delivery status change, call outcome, AI generation (with model id + prompt hash), verification, decision and escalation is audited.

---

## 17. Security & privacy

- RBAC + region scope enforced in a FastAPI dependency (`require(role, scope)`) and in every repository query helper.
- OTP for login; re-OTP ("eSign/OTP") for: submit application, committee decision, EVC report submit, compliance verification.
- httpOnly, Secure, SameSite=Lax cookies; CSRF token header for state-changing requests.
- Rate limits (Redis): OTP send 3/10min per identifier; public endpoints 30/min per IP.
- File uploads: MIME sniffing, size limits (25MB), ClamAV optional, served via short-lived signed URLs only.
- Field-level encryption (Fernet) for phone numbers and emails at rest; store hashed lookups for matching.
- **Do not search or store Aadhaar.** The reference mock shows "Faculty Aadhaar" in search — replace with **Faculty ID** (AICTE unique faculty identifier). If any government ID must be referenced, store only a masked form.
- Twilio webhook signature validation (`X-Twilio-Signature`), SendGrid signed event webhook verification.
- Internal voice API: bearer token + HMAC + session JWT; voice tools re-authorised in core.
- PII redaction in logs and transcripts.
- Security headers via Caddy (CSP, HSTS, frame-ancestors none).

---

## 18. Seed data, demo clock and demo control

### 18.1 Clock

`app/core/clock.py`:
```python
def now() -> datetime:
    real = datetime.now(tz=IST)
    if settings.DEMO_MODE:
        offset = redis.get("demo:clock_offset_seconds") or 0
        return real + timedelta(seconds=int(offset))
    return real
```
Frontend reads server time from `/api/v1/clock` (and SSE) for SLA badges — never the browser clock.

### 18.2 Seed (`pnpm seed` → `python -m app.seed`)

- 8 regions (illustrative: Hyderabad, Chennai, Bengaluru, Mumbai, New Delhi, Kolkata, Bhopal, Guwahati) mapped to states.
- 120 institutions (Faker `en_IN`, realistic names/cities, lat/long for geofence), 2 contacts each with preferred languages spread across the 6.
- Users: 1 admin, 2 bureau heads, 8 regional officers, 24 scrutiny officers, 10 committee members, 40 experts, institution logins; **demo accounts** documented in README (e.g. `officer@demo` / OTP `123456`).
- Application types + workflow rules + escalation policies + templates (6 langs) + document types + deficiency reasons + norms table + holidays.
- 300 applications distributed across all stages, some returned, some overdue, some approved with conditions.
- 400 compliance items with expiries spread from −20 to +120 days.
- 18 months of synthetic history for ML training and analytics charts.
- 1 **hero case** for the demo: "Government Engineering College, Thrissur — APP-2026-0451 — Intake Increase +120 (CSE)", with a sample land deed PDF whose extraction yields 5.2 acres vs norm 5.0 and a low-stamp-duty warning (matches reference image 1).
- Sample PDFs generated with reportlab (lease deeds, NOCs, building plans) so OCR has real content.

---

## 19. Phase-wise implementation — Track P (Varun)

Each phase: **Goal → Tasks → Deliverables → Acceptance Criteria → Agent prompt** (paste into Antigravity / CLI agent).

---

### Phase P0 — Repo, infra, tooling (1 day)

**Tasks**
- [ ] Create monorepo per §5: pnpm workspace, Turborepo, `apps/web` (create-next-app, TS, Tailwind v4, App Router), `apps/api` (uv, FastAPI), `apps/voice` (uv, FastAPI skeleton for Rishanth), `packages/contracts`.
- [ ] `infra/docker-compose.yml`: postgres:16 (with pgvector image `pgvector/pgvector:pg16`), redis:7, minio, api, worker, beat, voice, web, caddy. Healthchecks.
- [ ] `infra/postgres/init.sql`: extensions `vector`, `pgcrypto`; app DB role privileges (audit table insert/select only — applied after migration via a post-migrate script).
- [ ] `.env.example` per §6; `app/core/config.py` (pydantic-settings).
- [ ] Lint/format/typecheck configs; `turbo run lint typecheck test`.
- [ ] CI workflow: install, lint, typecheck, unit tests, `i18n:check`, export OpenAPI to `packages/contracts/openapi.json`, generate TS client.
- [ ] cloudflared tunnel script `pnpm tunnel` exposing api + voice for Twilio.
- [ ] `docs/DECISIONS.md`, `docs/CHANGELOG.md`.

**Acceptance**: `docker compose up` brings all services healthy; `GET /api/v1/health` → ok; web shows a placeholder page; CI green.

**Agent prompt**
> Read IMPLEMENTATION.md §3, §5, §6. Scaffold the monorepo exactly as §5 with pnpm + Turborepo and uv. Create docker-compose with postgres(pgvector), redis, minio, api, worker, beat, voice, web, caddy and healthchecks. Add config loading, lint/typecheck/test scripts and CI. Do not implement features yet. Verify `docker compose up` and `/api/v1/health`.

---

### Phase P1 — Auth, RBAC, design system, theming, i18n shell (3 days)

**Tasks**
- [ ] DB models + Alembic migration: `regions, states, institutions, institution_contacts, users, user_regions, otp_audit, user_settings` (full schema §21).
- [ ] Auth: `/auth/otp/send|verify|refresh|logout`, `/me`; Twilio Verify adapter + DEMO fixed OTP; JWT cookies; `require(role, scope)` dependency; region-scoped query helpers.
- [ ] Audit chain module (§16) + `outbox_events` + Celery publisher → Redis pub/sub; SSE endpoint `/stream`.
- [ ] Clock module (§18.1) + `/clock`.
- [ ] Web: tokens.css (§7.2), Tailwind theme mapping, next-themes (light/dark/system), fonts (§7.3), next-intl with 6 locales + `[locale]` routing + language switcher, `i18n:translate` and `i18n:check` scripts.
- [ ] Shell components: TopBar (exact layout §7.4), Sidebar (role-based items), Breadcrumbs, ActionBar, NotificationCenter drawer (SSE), ProfileSettings, IconBadge, StatusChip, SlaBadge, SlaPill, ConfidenceRing, AiBadge, Timeline, AuditDrawer, FileDropzone, DataTable, KpiCard.
- [ ] Login page (role tabs, OTP) in all 6 languages, both themes.
- [ ] Role-based route guards (middleware reads session → redirect to role home).
- [ ] Storybook-like `/dev/components` page (dev only) showing every component in both themes and all locales.

**Acceptance**: Can log in as each seeded role with OTP; land on role home with correct sidebar; theme toggle and all 6 languages work with correct script fonts; SSE bell receives a test event; audit event written for login; `/dev/components` renders all components in light and dark.

**Agent prompt**
> Implement Phase P1 of IMPLEMENTATION.md. Follow §7 (design tokens, layout specs, components) to match the reference: navy 72px top bar with emblem+AICTE logo and two-line title, search, SLA pill, mail/WhatsApp/bell badges, profile; 240px sidebar with active left bar. Implement OTP auth via Twilio Verify with DEMO_FIXED_OTP fallback, JWT cookies, RBAC + region scoping, audit chain (§16), outbox + SSE, clock (§18.1), next-intl with en/hi/te/ml/ta/kn and Noto fonts, next-themes light/dark. Build the shared components listed in §7.5 and a /dev/components gallery. All strings via i18n.

---

### Phase P2 — Workflow engine + application lifecycle (4 days)

**Tasks**
- [ ] Models: `application_types, workflow_rules, applications, application_courses, stage_instances, stage_transitions, assignments, deadlines, deficiency_reasons, deficiencies, deficiency_letters, holidays`.
- [ ] Rule JSON schema + validator; seed rules for all pathways (§9.1).
- [ ] Engine (§9.4): `route`, `perform_action`, `advance`, `resubmit`; transitions in one transaction with audit + outbox; deadline create/pause/resume/close; working-day calculator.
- [ ] Workload balancer (§9.5) + reassignment.
- [ ] Endpoints §13.2, §13.3 (letter draft can return a template stub until P11 AI; wire real AI in P3).
- [ ] Web — Institution: dashboard (basic), applications list, **8-step wizard** (pre-check step shows "pending AI" placeholder until P3), application detail with horizontal stepper and tabs, deficiency respond flow.
- [ ] Web — Officer: dashboard (basic), queue table with SlaBadge, stage detail with action buttons (Clear / Return / Refer / Clarify) and Return modal (checklist + reason + letter editor).
- [ ] Timeline + AuditDrawer wired to real data.
- [ ] Seed: application types, rules, deficiency reasons, a handful of applications.
- [ ] Tests: engine unit tests for every pathway, return/resubmit with clock pause, max_returns, auto-advance, permission denials.

**Acceptance**: Submitting a `NEW_INSTITUTION` application creates AI_PRECHECK→SCRUTINY→EVC→SAC; scrutiny is auto-assigned; officer Clear auto-advances to EVC; Return pauses SLA and notifies (in-app); institution resubmits → re-scrutiny; full timeline + audit visible; pathway tests pass for all 5 PS combinations.

**Agent prompt**
> Implement Phase P2 per IMPLEMENTATION.md §9 and §13.2–13.3. Build the configurable workflow engine with versioned JSON rules (schema in §9.2), stage state machine (§9.3), transactional transitions with audit/outbox/deadlines, workload-based assignment, return/resubmit with SLA pause. Build the institution application wizard, application detail, officer queue and stage actions UI. Write pytest coverage for all pathways.

---

### Phase P3 — Document AI + Case Review workspace (3 days)

**Tasks**
- [ ] Models: `document_types, documents, document_versions, extractions, ai_checks, ai_flags`.
- [ ] Upload pipeline: MinIO, signed URLs, `ocr_extract` Celery task (§14), per-type extraction schemas (lease deed, building plan, fire NOC, affidavit, faculty list).
- [ ] Validators + norms table; pre-scrutiny report endpoint; confidence computation.
- [ ] `LLMClient` with provider switch, JSON-schema output, DEMO cache.
- [ ] Deficiency letter AI draft (real).
- [ ] Wizard step 7 shows real AI pre-check results with "Fix" links.
- [ ] **Case Review Workspace** exactly per reference image 1: CaseFilePanel (title, App ID, type line, vertical stepper, key facts), DocViewer (zoom, page, rotate, Compare Side-by-Side, OCR highlight boxes colour-coded matched/mismatch, "AI OCR: verified" chip), AiPanel (ConfidenceRing, CheckCards: Duplicate Faculty Check, Land Area Norms, Infrastructure Verification, Deficiency/Anomalies amber), sticky ActionBar (Return for Deficiency, Refer to Senior, AI Assistant Chat, CLEAR STAGE & FORWARD). SlaPill in top bar bound to the stage deadline. Responsive behaviour per §7.4.
- [ ] Officer "verify extracted fields" interaction (accept/edit per field) → stored as human-verified.
- [ ] Hero case seed with generated sample PDFs (§18.2).

**Acceptance**: Uploading the hero lease deed extracts land area 5.2 acres, shows highlights on the PDF, Land Area Norms card shows "5.2 vs required 5.0 — Meets norms", warning card shows low stamp duty; confidence ring renders; Clear Stage & Forward works from this screen; screen matches the reference in light and dark.

**Agent prompt**
> Implement Phase P3 per IMPLEMENTATION.md §14 (extraction, validators, pre-scrutiny report, LLMClient) and §15.3 Case Review Workspace. Recreate reference image 1 precisely: three-column grid (340px | fluid | 380px), document viewer with OCR bounding-box highlights and compare mode, AI panel with confidence ring and check cards, sticky action bar. Seed the hero case APP-2026-0451 with generated sample PDFs. Support light and dark themes and all locales.

---

### Phase P4 — EVC scheduling + Expert PWA (3 days)

**Tasks**
- [ ] Models: `experts, expert_disciplines, expert_availability, evc_visits, evc_panel_members, evc_checklist_templates, evc_checklist_answers, evc_photos, evc_reports`.
- [ ] EVC queue on `stage.activated(EVC)`; scheduler: discipline match, conflict-of-interest (same state, past affiliation, recent visit to same institution), availability, distance clustering → ranked panel with match score + reasons.
- [ ] Schedule endpoint → invites via email with `.ics` attachment (Google Calendar compatible) → deadlines `EVC_VISIT`, `EVC_REPORT`.
- [ ] Regional EVC screens: queue, calendar, schedule screen (AI suggestion, swap, availability overlay, confirm).
- [ ] **Expert PWA** per reference image 2: manifest, Serwist SW, install prompt, dark default; visits list, visit detail, **inspect** screen (GpsPill geofence, CameraCapture with overlay + SHA-256, tamper badge with short hash, max 5 photos per section, ChecklistTabs with VERIFIED/IN PROGRESS pills, Attach photos, DictateButton UI (calls `/voice/dictation` — mocked until V6), SUBMIT INSPECTION REPORT (eSign / OTP)).
- [ ] Offline queue (IndexedDB) + background sync + OfflineSyncPill count.
- [ ] Server: photo EXIF/GPS validation, server-side re-hash, geofence check, store `hash`, `lat`, `lng`, `alt`, `captured_at`.
- [ ] Report submit → EVC stage COMPLETED → auto-advance to SAC.

**Acceptance**: After scrutiny clear, application appears in EVC queue; AI panel suggestion excludes conflicted experts; confirm sends invites; expert installs PWA on a phone, captures geotagged photo with overlay (works offline then syncs), fills checklist, submits with OTP; application moves to SAC.

**Agent prompt**
> Implement Phase P4 per IMPLEMENTATION.md §13.5 and §15.4/§15.6. Build EVC queue, conflict-aware panel suggestion and scheduling with .ics invites. Build the installable expert PWA (Serwist) reproducing reference image 2: GPS geofence pill, offline sync pill, camera capture with GPS/altitude/IST timestamp overlay and SHA-256 tamper hash, tabbed checklist with status pills, hold-to-dictate button (stub endpoint), OTP submit. Implement IndexedDB offline queue with background sync.

---

### Phase P5 — Committee + conditions → undertakings (2 days)

**Tasks**
- [ ] Models: `committee_meetings, committee_members, committee_agenda_items, committee_decisions, conditions`.
- [ ] Meeting + agenda management, agenda pack PDF (WeasyPrint).
- [ ] e-Case summary (real AI, §14) shown on the decision screen.
- [ ] Decision form + ConditionsBuilder + OTP → `committee.decision_recorded` → application final status → LoA PDF generated → each condition creates a `compliance_item` (kind UNDERTAKING) + deadline; conditions referencing a document type with expiry create DOCUMENT_VALIDITY items.
- [ ] Minutes draft (AI) → edit → publish.
- [ ] Institution approvals page with LoA download.

**Acceptance**: Committee member records "Approve with Conditions" with 2 conditions via OTP; application shows APPROVED_WITH_CONDITIONS; LoA downloadable; 2 compliance items appear on the institution's compliance calendar with deadlines.

**Agent prompt**
> Implement Phase P5 per IMPLEMENTATION.md §13.6 and §15.7. Build committee meetings, agenda pack PDF, AI e-case summary on the decision screen, decision form with conditions builder and OTP, LoA generation, and automatic creation of compliance items + deadlines from conditions. Add AI draft minutes.

---

### Phase P6 — Compliance & document validity tracking (2 days)

**Tasks**
- [ ] Models: `compliance_items (kind UNDERTAKING|DOCUMENT_VALIDITY), compliance_submissions, verifications, upload_links`.
- [ ] Document verify → if expiry present, create/update DOCUMENT_VALIDITY item + deadline; renewal resets.
- [ ] Endpoints §13.7 including upload-link token flow (`/u/{token}` page, OTP-gated, mobile-friendly).
- [ ] Institution: compliance calendar (month grid + list), item drawer, submit evidence with AI extraction preview, document vault with validity badges and versions.
- [ ] Officer: verification queue + verify screen; expiry monitor with filters (30/15/7/expired) and Remind / Call now buttons (Call now enqueues call job — sent to voice service from P8).
- [ ] Institution health score computation + gauge.

**Acceptance**: Lease with expiry appears in vault and validity monitor; renewal upload via portal and via upload link both create submissions; officer verifies → item closed and new expiry deadline created; health score updates.

**Agent prompt**
> Implement Phase P6 per IMPLEMENTATION.md §10.1, §13.7, §15.2–15.3. Build unified compliance items for undertakings and document validity, submissions (portal + signed upload links with OTP), officer verification, expiry monitor, compliance calendar, document vault, and the institution health score.

---

### Phase P7 — SLA scheduler, escalation ladder, Twilio messaging (3 days)

**Tasks**
- [ ] Models: `escalation_policies, escalations, notifications, notification_deliveries, message_templates, inbound_messages, commitments, call_jobs, calls` (call tables used fully in P8).
- [ ] Seed escalation policies (§10.2) and templates (all keys × 6 langs × channels).
- [ ] Deadline scanner (§10.3), idempotency, quiet hours, next_step_at recompute, breach marking, escalation creation, commitment deadlines.
- [ ] Notification orchestrator (§11.1) + channels: SendGrid email (tokenised reply-to), Twilio WhatsApp (sandbox + content templates), SMS fallback, IN_APP via SSE. Status callbacks → delivery status.
- [ ] Inbound: SendGrid Inbound Parse handler (§11.2) and WhatsApp inbound handler (§11.3) → submissions + auto-acknowledgements.
- [ ] Escalation inbox screens (Regional L1, Bureau L2) with actions; show-cause AI draft (authority-only).
- [ ] ChannelIcons on notifications/communications showing delivered/read/failed.
- [ ] `/demo` time travel triggers immediate scan.

**Acceptance**: With time travel, a lease expiring in 30 days produces email (T-30, T-15), email+WhatsApp with upload link (T-7), call job (T-2, queued), overdue alerts (T0), L1 escalation to RO (T+3), L2 to Bureau with show-cause draft (T+7); no duplicate sends on repeated scans; replying to the reminder email with a PDF creates a submission; sending a PDF on WhatsApp creates a submission.

**Agent prompt**
> Implement Phase P7 per IMPLEMENTATION.md §10 and §11. Build the deadline scanner with escalation policies, idempotency, quiet hours and breach handling; the notification orchestrator with Twilio SendGrid email (tokenised reply-to, event webhook, inbound parse), Twilio WhatsApp (sandbox + content templates, inbound media submissions), SMS fallback and in-app SSE; templates in 6 languages; escalation inbox UIs with AI show-cause draft. Verify end to end using demo time travel.

---

### Phase P8 — Voice integration, core side (2 days, with Rishanth)

**Tasks**
- [ ] Implement all `/internal/voice/*` endpoints (§13.13) with persona re-authorisation and audit (`actor_type=VOICE`).
- [ ] `call_jobs` dispatcher: escalation CALL steps and manual "Call now" → HMAC POST to voice service; retry scheduling per policy; NO_ANSWER → fallback SMS/WhatsApp.
- [ ] Call outcome handler → timeline, commitment deadline (`COMMITMENT_DEFAULT`), audit, SSE.
- [ ] Call log UIs: officer `/calls`, institution communications (recording player via signed URL, transcript, outcome chips, committed date); broken commitments list.
- [ ] Officer voice PIN setup in settings.
- [ ] Public `/verify` page + public grievance endpoints; faculty OTP page.
- [ ] Joint contract tests with Track V (run voice service against real core).

**Acceptance**: Triggering T-2 via demo panel calls a verified phone; after the call, committed date and summary appear on the case timeline within seconds; missing the commitment (time travel) raises "Broken commitment" escalation; officer inbound call answers "how many overdue" correctly; public caller gets correct approval status and a grievance SMS.

**Agent prompt**
> Implement Phase P8 per IMPLEMENTATION.md §12.6–12.7 and §13.13. Build the internal voice API with persona checks and audit, the call job dispatcher with retries and fallback, outcome ingestion creating commitments and timeline events, call log UIs with recording player and transcripts, officer voice PIN, public verify page and grievances, faculty OTP page. Add contract tests against apps/voice.

---

### Phase P9 — Dashboards, analytics, reports, India map (3 days)

**Tasks**
- [ ] Aggregation queries (materialized views refreshed every 5 min + live counts): stage counts, ageing buckets, avg processing time per stage, officer workload, bottlenecks (stage avg vs SLA), overdue by kind, compliance %, region comparisons, channel effectiveness (resolution after email/WA/call).
- [ ] Dashboards per role (§15) with KpiCard, AgeingChart, trend lines, workload table, IndiaHeatmap (state → region mapping; toggles), drill-downs to filtered tables.
- [ ] SSE deltas to update KPIs live when events occur.
- [ ] Reports: Pendency, EVC status, Undertaking compliance, Document validity, Overdue cases, Region-wise performance → PDF (WeasyPrint, branded header) + XLSX; schedules (weekly/monthly email via SendGrid).
- [ ] Export buttons on all tables.

**Acceptance**: Clearing a stage updates officer and regional dashboards live; heatmap drill-down to region → officer → case works; each of the 6 reports exports PDF and XLSX with correct numbers; a scheduled report is emailed (use time travel).

**Agent prompt**
> Implement Phase P9 per IMPLEMENTATION.md §13.9 and §15.3–15.5. Build role dashboards with KPIs, ageing, workload, bottlenecks, an India region heatmap with drill-down, live SSE updates, analytics pages, and the six consolidated reports in PDF/XLSX with scheduling.

---

### Phase P10 — Admin configuration portal (2 days)

**Tasks**
- [ ] Workflow rule builder (drag stages, config panel, branch conditions with validator, version + publish; running applications keep their rule version).
- [ ] Escalation policy editor (visual timeline).
- [ ] Template editor (key × channel × language, variables helper, preview, test send).
- [ ] Voice config (scripts per purpose/persona/language, voice, hours, retries, test call) — persisted in core, fetched by voice service.
- [ ] Masters: document types (+ extraction schema JSON), users & roles, permission matrix, experts, regions, holidays, deficiency reasons, application types, norms table.
- [ ] Audit explorer + Verify Integrity + export; System health page.

**Acceptance**: Admin changes Scrutiny SLA from 15 to 10 days and publishes v2 → new applications use v2, old ones remain v1; changing a ladder step from T-7 to T-10 is reflected in the next scan; tampering a row manually in DB is detected by Verify Integrity.

**Agent prompt**
> Implement Phase P10 per IMPLEMENTATION.md §13.10 and §15.8: visual workflow rule builder with versioning, escalation policy editor, template editor with preview/test send, voice configuration, all master data screens, audit explorer with integrity verification, and system health.

---

### Phase P11 — AI extras (3 days)

**Tasks**
- [ ] APH RAG: ingest `data/aph/*.pdf` → chunks → embeddings → pgvector; `/ai/assistant` with citations; AiAssistantPanel UI in all roles (answers in user's language); `/internal/voice/aph/search`.
- [ ] Breach prediction: training script on seeded history, nightly retrain (beat), scoring every 15 min, "Likely to breach" widgets.
- [ ] Anomaly detectors (§14): duplicate faculty, reused photo phash, stamp-duty sanity, date inconsistencies → flags in AI panel with "Dismiss with reason" (audited).
- [ ] Inbound email intent classifier.
- [ ] Evaluation page (dev): extraction accuracy on seeded docs, RAG sample Q&A.

**Acceptance**: Assistant answers "documents required for intake increase" with APH citations in Hindi and Telugu; breach list shows probabilities and correlates with seeded overdue patterns; seeded duplicate faculty appears as a flag on the affected cases.

**Agent prompt**
> Implement Phase P11 per IMPLEMENTATION.md §14: APH RAG with multilingual embeddings and citations (UI panel + internal voice endpoint), LightGBM breach prediction with retraining and scoring, anomaly detectors surfaced as AI flags with dismiss-with-reason, and inbound email intent classification.

---

### Phase P12 — Hardening, E2E, demo, deployment (3 days, with Rishanth)

**Tasks**
- [ ] `/demo` control panel complete (§15.10); reset & reseed in < 60s.
- [ ] Playwright E2E for the demo script (§24) in en + one Indic locale, light + dark.
- [ ] Load sanity: 1,000 deadlines scan < 2s; dashboards < 1s on seed.
- [ ] Accessibility pass (keyboard nav, focus rings, contrast in both themes, aria labels).
- [ ] Error/empty/loading states on every screen.
- [ ] Deployment: VM + Docker Compose + Caddy HTTPS on a domain; SendGrid domain auth + inbound MX; Twilio webhooks pointed to production URLs; WhatsApp sandbox numbers joined; verified caller numbers added.
- [ ] README with demo accounts, OTP, architecture diagram; `docs/DEMO_SCRIPT.md`; backup video recording of the demo.

**Acceptance**: Full demo script passes E2E on the deployed URL; both team members rehearse the live call segment successfully twice.

**Agent prompt**
> Implement Phase P12 per IMPLEMENTATION.md §18, §23, §24: finish the demo control panel, Playwright E2E for the full demo script in two locales and both themes, performance checks, accessibility and states polish, and production deployment with Caddy, SendGrid domain/inbound, and Twilio webhook configuration.

---

## 20. Phase-wise implementation — Track V (Rishanth)

Runs in parallel from Track P's Phase P1. Uses a Prism mock of the internal API until P8.

### V0 — Setup (1 day)
- [ ] `apps/voice` FastAPI skeleton, uv, Dockerfile, `/health`.
- [ ] Twilio account, voice number, verified test numbers; cloudflared tunnel to `/voice/*`.
- [ ] Prism mock: `npx @stoplight/prism-cli mock packages/contracts/openapi.json` (internal tag) or hand-written mock JSON until Track P exports it.
- [ ] `core_client.py` with bearer + session headers; tool wrapper layer reading `voice-tools.json`.
**Acceptance**: Calling the Twilio number plays a TwiML greeting from the service.

### V1 — Inbound IVR + session (2 days)
- [ ] `/voice/inbound` TwiML: bilingual greeting + recording notice, language `<Gather>` (DTMF + speech), caller lookup, persona selection, "press 9" identify path.
- [ ] Session JWT creation; `<Connect><ConversationRelay>` with language-specific STT/TTS settings from `speech/languages.py`.
- [ ] Recording enabled (dual channel), status callback.
**Acceptance**: Calls in all 6 languages reach the relay with the right persona in the session.

### V2 — Relay WebSocket + brain (3 days)
- [ ] `relay_ws.py`: handle setup, caller prompt, interrupt, DTMF, end messages per ConversationRelay protocol; stream LLM tokens back.
- [ ] `brain.py`: streaming LLM with tool calling, per-persona tool allowlist, conversation memory per session, max turns, silence reprompts.
- [ ] Persona prompts per language (`prompts/{persona}.{lang}.md`), guardrails (§12.4).
- [ ] Latency budget: first audio < 1.5s after caller stops speaking (use fast model, short system prompt, pre-warm).
**Acceptance**: Public persona conversation in English and Hindi answers "is <seeded institution> approved" using mock tools, with barge-in working.

### V3 — Personas & tools complete (3 days)
- [ ] Institution persona (outbound scripts by purpose), officer persona (PIN, queue, stats, email report, trigger reminder with confirmation), public persona (search, approval status, grievance), faculty persona (OTP, status, grievance).
- [ ] Date understanding in all languages ("next Friday", "20th", "इस महीने के अंत तक") → ISO date, confirm back to caller.
- [ ] Tool error handling + `handoff_to_officer`.
**Acceptance**: Scripted test calls for all 4 personas pass (§20.1 test matrix) against the mock.

### V4 — Outbound dialer (2 days)
- [ ] `POST /voice/calls` (HMAC verify) → Twilio `calls.create` with AMD, `/voice/outbound-twiml?job=` → relay with institution persona + job context (item, due date, institution, contact name, language).
- [ ] Calling-hours enforcement; status callbacks → core `/internal/voice/calls/{id}/status`.
**Acceptance**: Mock core triggers an outbound call to a verified phone; agent opens with the correct item and language; no-answer reported correctly.

### V5 — Post-call processing (1 day)
- [ ] Transcript assembly (redacted), LLM structured outcome (§12.7), local + English summary, recording URL → core outcome endpoint.
**Acceptance**: Outcome JSON validated against schema for 10 sample transcripts.

### V6 — Dictation + optional expert voice report (1 day)
- [ ] `/voice/dictation` endpoint (audio → STT → checklist mapping JSON) used by the PWA DictateButton.
- [ ] Optional expert inbound persona → `save_evc_report_draft`.
**Acceptance**: Holding dictate in the PWA and saying "Physics lab equipped, computer lab has 60 systems, fire extinguishers missing on second floor" fills the right checklist items with highlights.

### V7 — Integration & hardening (with P8/P12, 1–2 days)
- [ ] Switch from mock to real core; contract tests; Indian-language quality check — switch weak languages to Sarvam fallback path (`<Connect><Stream>`).
- [ ] Load: 5 concurrent calls; graceful failure messages; logs/metrics.
**Acceptance**: Live demo call flow (§24 step 6) passes twice in a row on the deployed stack.

### 20.1 Voice test matrix

| # | Persona | Lang | Scenario | Expected |
|---|---|---|---|---|
| 1 | Institution (out) | te | Lease expiring, commits to 20 Nov | `record_commitment(2026-11-20)`, WhatsApp link sent |
| 2 | Institution (out) | hi | Asks for 60-day extension | Explains authority, logs extension request, no grant |
| 3 | Institution (out) | en | Wrong person answers | Schedules callback |
| 4 | Officer (in) | en | "How many overdue in my queue?" | Correct number after PIN |
| 5 | Officer (in) | hi | "Email me pendency report" | `email_report` called |
| 6 | Public (in) | ml | Is institution X approved for 2026-27? | Correct courses/intake, no private data |
| 7 | Public (in) | ta | Wants to complain about fake college | Grievance created, SMS ticket |
| 8 | Faculty (in) | kn | Flagged as duplicate | Explains, creates grievance |
| 9 | Any | any | Asks to approve application | Refusal + handoff |
| 10 | Institution (out) | en | Voicemail | NO_ANSWER, fallback SMS |

---

## 21. Database schema (complete)

Conventions: `id uuid pk default gen_random_uuid()`, `created_at`, `updated_at timestamptz`, soft-delete not used (audit instead). Enums as Postgres enums.

```
-- org & identity
regions(id, code uniq, name, office_city)
states(id, code uniq, name, region_id fk)
institutions(id, aicte_id uniq, name, type GOVT|PRIVATE|AIDED|DEEMED, state_id, region_id, city, address,
             lat numeric, lng numeric, geofence_radius_m int default 300, health_score int, created_at)
institution_contacts(id, institution_id, name, designation, phone_enc, phone_hash, email_enc, email_hash,
             whatsapp_opt_in bool, call_opt_in bool, preferred_call_time text, preferred_language,
             is_nodal bool)
users(id, role enum, name, email_enc, email_hash uniq, phone_enc, phone_hash uniq, institution_id null,
      preferred_language default 'en', theme default 'system', voice_pin_hash null, is_active, last_login_at)
user_regions(user_id, region_id, pk(user_id, region_id))
faculty(id, faculty_uid uniq, name, phone_hash, discipline, institution_ids uuid[], flags jsonb)

-- workflow
application_types(id, code uniq, name_key, required_document_types text[], default_rule_id)
workflow_rules(id, rule_key, application_type, academic_year, version, definition jsonb,
               status DRAFT|PUBLISHED|RETIRED, published_at, published_by)
applications(id, app_no uniq 'APP-YYYY-NNNN', institution_id, application_type, academic_year,
             rule_id, status enum, current_stage_key, payload jsonb, submitted_at, decided_at,
             final_decision null)
application_courses(id, application_id, course_name, level, existing_intake, proposed_intake)
stage_instances(id, application_id, stage_key, seq, status enum, activated_at, completed_at,
                returns_count int, assigned_user_id null, deadline_id null, meta jsonb)
stage_transitions(id, stage_instance_id, from_status, to_status, action, actor_id, reason, payload jsonb, at)
assignments(id, stage_instance_id, user_id, assigned_by SYSTEM|user_id, reason, active bool, at)
deficiency_reasons(id, code, label_key, category)
deficiencies(id, stage_instance_id, reason_code null, custom_text, status OPEN|RESPONDED|RESOLVED,
             response_text, response_document_id null)
deficiency_letters(id, stage_instance_id, language, body_md, ai_generated bool, edited bool, sent_at, sent_by)

-- documents & AI
document_types(id, code uniq, name_key, has_expiry bool, extraction_schema jsonb, min_validity_years int null)
documents(id, institution_id, application_id null, compliance_item_id null, document_type, current_version_id,
          expiry_date null, verified_status UNVERIFIED|VERIFIED|REJECTED, verified_by, verified_at)
document_versions(id, document_id, version int, s3_key, sha256, mime, size, uploaded_by, source PORTAL|EMAIL|WHATSAPP|LINK, at)
extractions(id, document_version_id, model_id, fields jsonb, boxes jsonb, raw_text, confidence numeric, status, at)
ai_checks(id, application_id, check_key, status PASS|WARN|FAIL, message_key, params jsonb, at)
ai_flags(id, entity_type, entity_id, flag_key, severity LOW|MED|HIGH, details jsonb,
         status OPEN|DISMISSED|CONFIRMED, dismissed_reason, by, at)
case_summaries(id, application_id, language, content jsonb, model_id, source_hash, at)
norms(id, key, application_type, value numeric, unit, notes)
aph_chunks(id, source_file, section, page, language, content, embedding vector(<dim>))

-- EVC
experts(id, user_id, disciplines text[], home_state_id, affiliations uuid[], rating numeric, active)
expert_availability(id, expert_id, date, available bool)
evc_visits(id, application_id, stage_instance_id, status QUEUED|PANEL_PROPOSED|SCHEDULED|VISITED|REPORT_SUBMITTED|CANCELLED,
           visit_date null, proposed_panel jsonb, scheduled_by, deadline_visit_id, deadline_report_id)
evc_panel_members(id, visit_id, expert_id, role LEAD|MEMBER, invite_status PENDING|ACCEPTED|DECLINED, responded_at)
evc_checklist_templates(id, application_type, sections jsonb)
evc_checklist_answers(id, visit_id, expert_id, item_key, status YES|NO|PARTIAL, remark, source MANUAL|DICTATION|VOICE, ai_confidence)
evc_photos(id, visit_id, expert_id, item_key, s3_key, client_hash, server_hash, lat, lng, alt, accuracy_m,
           captured_at, inside_geofence bool)
evc_reports(id, visit_id, expert_id, recommendation, remarks, status DRAFT|SUBMITTED, submitted_at, otp_verified bool)

-- committee
committee_meetings(id, committee_type SAC|SHC, date, region_id null, status, minutes_md, minutes_published_at)
committee_members(meeting_id, user_id)
committee_agenda_items(id, meeting_id, application_id, seq)
committee_decisions(id, application_id, meeting_id, decision, remarks, decided_by, otp_verified, at)
conditions(id, decision_id, application_id, title, evidence_type, due_date, document_type null)

-- compliance
compliance_items(id, institution_id, application_id null, kind UNDERTAKING|DOCUMENT_VALIDITY,
                 condition_id null, document_id null, title, due_date, status PENDING|SUBMITTED|VERIFIED|REJECTED|OVERDUE,
                 deadline_id)
compliance_submissions(id, compliance_item_id, document_version_id, remarks, source PORTAL|EMAIL|WHATSAPP|LINK, submitted_by, at)
verifications(id, submission_id, result VERIFIED|REJECTED, reason, verified_by, otp_verified, at)
upload_links(id, token_hash, compliance_item_id, contact_id, expires_at, used_at)

-- deadlines & escalation
deadlines(id, subject_type, subject_id, institution_id null, application_id null, due_at, status,
          paused_at null, paused_total_seconds int, policy_key, last_step_index int default -1,
          next_step_at, owner_party, escalation_level int default 0)
escalation_policies(id, key uniq, definition jsonb, version, active)
escalations(id, deadline_id, level, raised_to_role, raised_to_user null, reason, status, actions jsonb, at)
holidays(date pk, name, region_id null)
commitments(id, deadline_id null, subject_type, subject_id, committed_date, reason, source CALL|WHATSAPP|EMAIL,
            call_id null, status OPEN|MET|BROKEN)
extension_requests(id, subject_type, subject_id, requested_date, reason, source, status PENDING|GRANTED|DENIED, decided_by)

-- messaging
message_templates(id, key, channel, language, subject, body, variables text[], twilio_content_sid null, version)
notifications(id, template_key, subject_type, subject_id, idempotency_key uniq, priority, created_at)
notification_deliveries(id, notification_id, recipient_user_id null, recipient_contact_id null, channel,
                        language, to_masked, provider_message_id, status QUEUED|SENT|DELIVERED|READ|FAILED|BOUNCED,
                        error, sent_at, delivered_at, read_at)
inbound_messages(id, channel EMAIL|WHATSAPP|SMS, from_masked, contact_id null, subject_type null, subject_id null,
                 intent, body_text, attachments jsonb, raw_s3_key, processed bool, at)

-- voice
call_jobs(id, deadline_id null, subject_type, subject_id, purpose, persona, to_contact_id, language,
          attempt int, max_attempts, scheduled_for, status QUEUED|DIALING|DONE|FAILED|CANCELLED)
calls(id, call_job_id null, twilio_call_sid uniq, direction INBOUND|OUTBOUND, persona, language,
      from_masked, to_masked, started_at, ended_at, duration_s, status, outcome, summary, summary_local,
      transcript jsonb, recording_s3_key, tool_log jsonb)
callbacks(id, call_id, assigned_user_id null, summary, due_at, status)
grievances(id, ticket_no uniq, category, description, institution_id null, caller_masked, source VOICE|WEB,
           status OPEN|IN_PROGRESS|CLOSED, at)

-- reports & system
report_jobs(id, type, format, filters jsonb, status, s3_key, requested_by, at)
report_schedules(id, type, format, filters jsonb, cron, recipients uuid[], last_run_at)
outbox_events(id, event, payload jsonb, published bool, at)
audit_events(... §16)
system_settings(key pk, value jsonb)
```

Indexes: `deadlines(status, next_step_at)`, `stage_instances(assigned_user_id, status)`, `applications(institution_id, status)`, `compliance_items(institution_id, status, due_date)`, `notification_deliveries(provider_message_id)`, `audit_events(entity_type, entity_id)`, HNSW on `aph_chunks.embedding`.

---

## 22. Testing strategy

| Level | Tooling | Must cover |
|---|---|---|
| Unit (API) | pytest | workflow transitions all pathways, working-day math, deadline pause/resume, scanner idempotency, escalation steps, template rendering in 6 langs, audit chain verify/tamper, RBAC/region scoping |
| Integration (API) | pytest + testcontainers (postgres, redis, minio) | upload → OCR → extraction (LLM mocked), email inbound parse, WhatsApp inbound, call outcome ingestion |
| Contract | schemathesis on OpenAPI; voice ↔ core contract tests | internal voice API |
| Voice | pytest with recorded WebSocket fixtures; scripted call simulator (text mode) | test matrix §20.1 |
| Web unit | Vitest + Testing Library | components, i18n keys present, SLA badge logic |
| E2E | Playwright | demo script §24, locales en + te, light + dark, mobile viewport for PWA |

---

## 23. Deployment

- **Target**: 1 VM (4 vCPU, 8–16 GB RAM), Ubuntu, Docker Compose, Caddy (HTTPS), domain e.g. `acp.<team-domain>`:
  - `acp.<domain>` → web
  - `api.acp.<domain>` → api (webhooks: `/api/v1/webhooks/*`)
  - `voice.acp.<domain>` → voice (Twilio voice URLs + WebSocket)
- SendGrid: authenticate sending domain; MX for `reply.<domain>` → `mx.sendgrid.net`; Inbound Parse → `api.acp.<domain>/api/v1/webhooks/sendgrid/inbound`.
- Twilio: voice number webhook → `voice.acp.<domain>/voice/inbound`; status callbacks; WhatsApp sandbox inbound → `api…/webhooks/twilio/whatsapp`.
- Backups: nightly `pg_dump` + MinIO bucket sync.
- Observability: structured JSON logs, `/admin/health`, optional Sentry.
- Fallback: local Docker Compose on a laptop with cloudflared tunnel if venue Wi-Fi blocks the VM.

---

## 24. Demo script (7 minutes)

1. **Login** as institution (Telugu UI, dark mode) → show 6-language switch and theme toggle. Submit a new application → AI pre-check flags a missing Fire NOC → fix → submit with OTP. Show auto-routed pathway.
2. **Officer** (English, light) → queue shows the case auto-assigned. Open hero case **APP-2026-0451** → Case Review Workspace: OCR highlights, 98% confidence, Land Area Norms met, stamp duty warning. Return for deficiency → AI-drafted letter → send. Institution resubmits → re-scrutiny → **Clear Stage & Forward** → moves to EVC.
3. **Regional officer** → EVC scheduling with AI panel (conflict excluded) → confirm → invites.
4. **Expert PWA on a real phone** → GPS locked, geotagged tamper-hashed photo, hold-to-dictate fills checklist → submit with OTP → application moves to SAC.
5. **Committee** → AI e-case summary → Approve with Conditions (lease renewal + fire NOC) → compliance items auto-created.
6. **Demo panel ⏩ time travel**: lease nears expiry → email → WhatsApp (judge's phone) → **live AI call to a judge's phone** in their language → commitment captured → appears on timeline instantly.
7. ⏩ past committed date → **Broken commitment** → escalation to Regional, then Bureau with AI show-cause draft.
8. Institution **replies to the reminder email with the renewed lease PDF** → auto-attached → extraction → officer verifies → closed.
9. **Inbound calls**: officer asks "how many overdue in my region?"; a parent asks "is this college approved?" → grievance SMS.
10. **Bureau dashboard**: India heatmap, breach predictions, reports export; **Audit → Verify Integrity ✅**.

Close with the Regulatory Safeguard slide: every decision is human; AI drafts, flags, reminds.

---

## 25. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Twilio trial limits (verified numbers only, WhatsApp sandbox join) | Verify judge/team numbers before demo; pre-join sandbox; keep a backup screen recording |
| Indian-language STT/TTS quality | Per-language provider config; Sarvam fallback path; demo in the strongest 2 languages live, show others recorded |
| India SMS DLT | Use Verify for OTP; SMS only as fallback on test numbers |
| LLM latency/outage during demo | DEMO_MODE response cache; fast model for voice; fallback scripted responses for hero flow |
| OCR on poor scans | Generated clean sample PDFs for hero case; confidence shown; officer edit flow |
| Venue network | Local stack + tunnel; mobile hotspot |
| APH timelines not exact | All SLAs configurable; state clearly they are illustrative defaults aligned to APH before rollout |
| Scope creep | MVP ★ screens first; extras only after P12 acceptance on MVP |

---

## 26. Definition of Done (whole project)

- [ ] All ★ screens implemented in light + dark and 6 languages, matching reference images 1 and 2.
- [ ] All 5 PS pathways route correctly with auto-advance and return/re-scrutiny.
- [ ] Deadlines exist for stages, EVC visit/report, undertakings, document validity, deficiency responses, commitments — all with reminder + escalation ladders.
- [ ] Email (SendGrid), WhatsApp, SMS fallback, in-app and AI voice calls all fire from the same orchestrator with delivery tracking.
- [ ] Inbound email reply and WhatsApp media create compliance submissions.
- [ ] Voice agent handles 4 personas (institution outbound, officer inbound, public inbound, faculty inbound) in at least English + Hindi + Telugu live, others configured.
- [ ] Role dashboards live-update; 6 reports export PDF/XLSX; scheduled reports email.
- [ ] Admin can change rules, SLAs, ladders, templates, voice scripts without code.
- [ ] Hash-chained audit covers every action; integrity verification detects tampering.
- [ ] No AI path can approve/reject/extend/decide; all decisions require human + OTP where specified.
- [ ] E2E demo script passes on the deployed URL; backup video recorded.
