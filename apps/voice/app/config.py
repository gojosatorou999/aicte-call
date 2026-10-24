"""Runtime configuration for the ACP voice agent.

Fails at boot on invalid values - never at runtime with a caller on the
line (CLAUDE.md invariant #7). Loaded once via `get_settings()`.

Env var names follow app.md section 6; anything the voice service needs
beyond that list is documented in `.env.example`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    DEMO = "demo"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """All runtime settings. Field names mirror `.env.example` exactly."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- General ----------------------------------------------------------
    app_env: AppEnv = AppEnv.DEVELOPMENT
    demo_mode: bool = False
    default_timezone: str = "Asia/Kolkata"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Optional on-disk copy of the JSON log stream. Empty = stdout only.
    log_file: str = ""
    agent_version: str = "dev-local"
    # Where voice-tools.json (shared tool contract) lives.
    contracts_dir: Path = _REPO_ROOT / "packages" / "contracts"

    # -- Public URLs ------------------------------------------------------
    # Must be reachable by Twilio. Every webhook URL we hand to Twilio and
    # every Gather `action` is built from this, so the tunnel (ngrok) MUST
    # serve exactly this host.
    public_voice_url: str = "http://localhost:8080"

    # -- Security ---------------------------------------------------------
    internal_service_token: SecretStr = SecretStr("change-me-voice-to-core")
    # Signs the per-call session JWT sent to core as X-Voice-Session.
    # Empty -> derived from internal_service_token.
    session_jwt_secret: SecretStr = SecretStr("")
    session_jwt_ttl_sec: int = Field(default=3600, ge=60, le=86_400)
    # HMAC timestamp tolerance for core -> voice POST /voice/calls.
    hmac_tolerance_sec: int = Field(default=300, ge=30, le=3600)
    caller_hash_pepper: SecretStr = SecretStr("dev-only-do-not-use-in-prod")
    admin_api_key: SecretStr = SecretStr("")

    # -- Core API (Track P) ----------------------------------------------
    # Base of the internal voice API, e.g. http://api:8000/internal/voice
    core_base_url: str = "http://localhost:9000/internal/voice"
    core_timeout_sec: float = Field(default=4.0, ge=0.5, le=30.0)
    core_circuit_open_after: int = Field(default=4, ge=1, le=50)
    core_circuit_reset_sec: float = Field(default=20.0, ge=1.0, le=600.0)

    # -- Data -------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "sqlite+aiosqlite:///./data/voice.db"

    # -- AI ---------------------------------------------------------------
    llm_provider: Literal["openai"] = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_ms: int = Field(default=1500, ge=100, le=20_000)
    llm_max_tokens: int = Field(default=300, ge=10, le=4000)
    # Speech-to-text for the in-app dictation endpoint (not used on calls:
    # on calls Twilio <Gather> does the STT).
    stt_model: str = "gpt-4o-mini-transcribe"

    # -- Twilio -----------------------------------------------------------
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_voice_number: str = ""
    twilio_sms_from: str = ""
    # Dev-only escape hatch. NEVER enable in production: it lets anyone
    # forge webhook events.
    twilio_signature_validation_enabled: bool = True
    # JSON object {"hi": "Google.hi-IN-Standard-A", ...} overriding the
    # default <Say> voice per language (see speech/languages.py).
    twilio_voices_json: str = ""
    # JSON object {"en": "phone_call"} overriding the <Gather speechModel>
    # per language. Empty = Twilio default model.
    gather_speech_models_json: str = ""

    # -- Voice (Track V) --------------------------------------------------
    call_hours_start: str = "10:00"
    call_hours_end: str = "18:00"
    call_max_attempts: int = Field(default=3, ge=1, le=10)
    call_retry_gap_min: int = Field(default=120, ge=1, le=1440)
    # Comma-separated ISO dates on which outbound calls are not placed.
    call_holidays: str = ""
    # Calling days, Python weekday numbers (Mon=0). app.md: Mon-Sat.
    call_days: str = "0,1,2,3,4,5"
    sarvam_api_key: SecretStr = SecretStr("")  # accepted for env parity; unused (see DECISIONS.md)

    # -- Conversation -----------------------------------------------------
    max_call_duration_public_sec: int = Field(default=360, ge=30, le=3600)
    max_call_duration_default_sec: int = Field(default=600, ge=30, le=3600)
    no_input_timeout_ms: int = Field(default=6000, ge=1000, le=30_000)
    silence_reprompts: int = Field(default=2, ge=0, le=5)
    max_turns: int = Field(default=30, ge=1, le=200)
    max_attempts_per_slot: int = Field(default=3, ge=1, le=5)
    # Policy cap on a committed date (app.md 12.3: "> 30 days").
    commitment_max_days: int = Field(default=30, ge=1, le=365)
    public_calls_per_day: int = Field(default=10, ge=1, le=1000)
    public_lookups_per_call: int = Field(default=5, ge=1, le=50)
    pin_max_attempts: int = Field(default=3, ge=1, le=10)
    otp_max_attempts: int = Field(default=3, ge=1, le=10)
    emergency_number: str = "112"
    expert_persona_enabled: bool = False
    # Seconds of trailing silence before Twilio finalises an OPEN-ENDED
    # Gather (descriptions, names). 0 = "auto" everywhere.
    gather_open_speech_timeout_sec: int = Field(default=3, ge=0, le=10)

    # -- Outbox relay (post-call delivery to core) ------------------------
    relay_enabled: bool = True
    relay_poll_interval_sec: float = Field(default=1.0, ge=0.05, le=60.0)
    relay_max_attempts: int = Field(default=8, ge=1, le=50)

    # -- Dial pad (dev/demo trigger for outbound calls) -------------------
    dial_pad_enabled: bool = True
    # If set, POST /dial/call requires `Authorization: Bearer <token>`.
    dial_pad_token: SecretStr = SecretStr("")
    # Comma-separated browser origins allowed to call /dial/* (CORS), e.g.
    # the GitHub Pages origin hosting dialpad/index.html.
    dialpad_allowed_origins: str = "http://localhost:8080"

    # -- Ops --------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None

    # -- validators -------------------------------------------------------
    @field_validator("call_hours_start", "call_hours_end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, _, mm = v.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError(f"expected HH:MM, got {v!r}")
        return f"{int(hh):02d}:{int(mm):02d}"

    @field_validator("twilio_voices_json", "gather_speech_models_json")
    @classmethod
    def _json_object(cls, v: str) -> str:
        if v.strip():
            parsed = json.loads(v)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
        return v

    # -- helpers ----------------------------------------------------------
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @property
    def public_base(self) -> str:
        """`PUBLIC_VOICE_URL` as a clean https/http origin, no trailing slash."""
        return (
            self.public_voice_url.replace("wss://", "https://")
            .replace("ws://", "http://")
            .rstrip("/")
        )

    @property
    def jwt_secret(self) -> str:
        explicit = self.session_jwt_secret.get_secret_value()
        if explicit:
            return explicit
        return "session:" + self.internal_service_token.get_secret_value()

    @property
    def voice_overrides(self) -> dict[str, str]:
        return json.loads(self.twilio_voices_json) if self.twilio_voices_json.strip() else {}

    @property
    def speech_model_overrides(self) -> dict[str, str]:
        raw = self.gather_speech_models_json
        return json.loads(raw) if raw.strip() else {}

    @property
    def holiday_dates(self) -> frozenset[str]:
        return frozenset(d.strip() for d in self.call_holidays.split(",") if d.strip())

    @property
    def calling_weekdays(self) -> frozenset[int]:
        return frozenset(int(d) for d in self.call_days.split(",") if d.strip().isdigit())

    @property
    def dialpad_origins(self) -> list[str]:
        return [o.strip() for o in self.dialpad_allowed_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.get_secret_value())

    def require_production_secrets(self) -> None:
        """Called at boot. A broken production deploy must never take traffic."""
        if not self.is_production():
            return
        required = {
            "twilio_account_sid": self.twilio_account_sid,
            "twilio_auth_token": self.twilio_auth_token.get_secret_value(),
            "twilio_voice_number": self.twilio_voice_number,
            "internal_service_token": self.internal_service_token.get_secret_value(),
            "admin_api_key": self.admin_api_key.get_secret_value(),
            "llm_api_key": self.llm_api_key.get_secret_value(),
        }
        missing = [k for k, v in required.items() if not v]
        problems: list[str] = []
        if missing:
            problems.append(f"missing: {', '.join(missing)}")
        if self.internal_service_token.get_secret_value().startswith("change-me"):
            problems.append("INTERNAL_SERVICE_TOKEN is still the placeholder")
        if self.caller_hash_pepper.get_secret_value().startswith("dev-only"):
            problems.append("CALLER_HASH_PEPPER is still the dev placeholder")
        if not self.twilio_signature_validation_enabled:
            problems.append("TWILIO_SIGNATURE_VALIDATION_ENABLED must be true")
        if self.demo_mode:
            problems.append("DEMO_MODE must be false in production")
        if problems:
            raise RuntimeError("Production boot refused: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Import this, never instantiate Settings directly."""
    return Settings()
