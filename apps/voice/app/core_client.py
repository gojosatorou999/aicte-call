"""HTTP client for core's internal voice API (app.md 13.13).

Every request carries `Authorization: Bearer <INTERNAL_SERVICE_TOKEN>` and,
when a call is in progress, `X-Voice-Session: <session JWT>`. A small
circuit breaker stops us hammering a dead core while a caller waits.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.obs import get_logger

log = get_logger(__name__)


class CoreError(Exception):
    """Core returned an error or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class CoreUnavailable(CoreError):
    """Circuit open or transport failure - callers should degrade gracefully."""


class CoreClient:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._s = settings or get_settings()
        self._http = httpx.AsyncClient(
            base_url=self._s.core_base_url.rstrip("/"),
            timeout=self._s.core_timeout_sec,
            transport=transport,
        )
        self._failures = 0
        self._opened_at: float | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- circuit breaker ---------------------------------------------------
    def _check_circuit(self) -> None:
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at >= self._s.core_circuit_reset_sec:
            self._opened_at = None  # half-open: let one request probe
            self._failures = self._s.core_circuit_open_after - 1
            return
        raise CoreUnavailable("core circuit open")

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._s.core_circuit_open_after:
            self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    # -- requests ------------------------------------------------------------
    def _headers(self, session_jwt: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._s.internal_service_token.get_secret_value()}"}
        if session_jwt:
            headers["X-Voice-Session"] = session_jwt
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        session_jwt: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        self._check_circuit()
        try:
            resp = await self._http.request(
                method, path, params=params, json=json, headers=self._headers(session_jwt)
            )
        except httpx.HTTPError as exc:
            self._record_failure()
            log.warning("core_unreachable", path=path, error=type(exc).__name__)
            raise CoreUnavailable(f"core unreachable: {type(exc).__name__}") from exc

        if resp.status_code >= 500:
            self._record_failure()
            raise CoreUnavailable(f"core {resp.status_code}", status=resp.status_code)
        self._record_success()
        if resp.status_code >= 400:
            # Contract error shape: {"error": {"code": "...", "params": {...}}}
            code = None
            with contextlib.suppress(ValueError, AttributeError):
                code = resp.json().get("error", {}).get("code")
            raise CoreError(f"core {resp.status_code}", status=resp.status_code, code=code)
        return resp.json() if resp.content else None

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw: Any) -> Any:
        return await self.request("PUT", path, **kw)

    async def ping(self) -> bool:
        """Reachability probe for /health. Any HTTP response counts as up."""
        try:
            await self._http.get("/callers/lookup", params={"phone": ""}, headers=self._headers(None))
        except httpx.HTTPError:
            return False
        return True
