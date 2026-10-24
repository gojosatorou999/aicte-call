"""Tool wrapper layer over core's `/internal/voice/*` (app.md 12.5, 13.13).

`voice-tools.json` is the shared contract: it decides which tools exist and
which personas may call them. `_ROUTES` maps each tool onto its core
endpoint. Both are checked against each other at load time so contract
drift fails at boot, not mid-call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.core_client import CoreClient
from app.session import Persona


class ToolNotAllowed(Exception):
    """The persona may not call this tool (guardrails turns this into a refusal)."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    personas: frozenset[Persona]
    params: dict[str, str]


@dataclass(frozen=True, slots=True)
class _Route:
    method: str
    path: str  # {placeholders} are filled from args; the rest goes in the query (GET) or JSON body


_ROUTES: dict[str, _Route] = {
    "get_case_status": _Route("GET", "/cases/{app_id}/status"),
    "get_open_items": _Route("GET", "/institutions/me/open-items"),
    "record_commitment": _Route("POST", "/commitments"),
    "log_extension_request": _Route("POST", "/extension-requests"),
    "send_upload_link": _Route("POST", "/upload-links"),
    "schedule_callback": _Route("POST", "/callbacks"),
    "search_aph": _Route("POST", "/aph/search"),
    "handoff_to_officer": _Route("POST", "/callbacks"),
    "verify_officer_pin": _Route("POST", "/officers/verify-pin"),
    "get_my_queue_summary": _Route("GET", "/officers/me/queue-summary"),
    "get_region_stats": _Route("GET", "/stats/region"),
    "list_overdue": _Route("GET", "/overdue"),
    "email_report": _Route("POST", "/reports/email"),
    "trigger_reminder": _Route("POST", "/reminders"),
    "search_institutions": _Route("GET", "/public/institutions"),
    "get_public_approval_status": _Route("GET", "/public/institutions/{institution_id}/approval"),
    "create_grievance": _Route("POST", "/grievances"),
    "send_otp": _Route("POST", "/otp/send"),
    "check_otp": _Route("POST", "/otp/check"),
    "get_faculty_status": _Route("GET", "/faculty/{faculty_id}"),
    "get_visit_checklist": _Route("GET", "/evc/visits/{visit_id}/checklist"),
    "save_evc_report_draft": _Route("PUT", "/evc/visits/{visit_id}/report-draft"),
}


_CALLBACK_KINDS = {"schedule_callback": "CALLBACK", "handoff_to_officer": "HANDOFF"}


@lru_cache(maxsize=1)
def load_tool_specs() -> dict[str, ToolSpec]:
    path = get_settings().contracts_dir / "voice-tools.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    specs = {
        t["name"]: ToolSpec(
            name=t["name"],
            personas=frozenset(Persona(p) for p in t["persona"]),
            params=dict(t["params"]),
        )
        for t in raw
    }
    missing = specs.keys() - _ROUTES.keys()
    extra = _ROUTES.keys() - specs.keys()
    if missing or extra:
        raise RuntimeError(
            f"voice-tools.json and tools._ROUTES disagree: "
            f"no route for {sorted(missing)}, not in contract {sorted(extra)}"
        )
    return specs


def tools_for(persona: Persona) -> list[ToolSpec]:
    """The allowlist the LLM is shown for this persona."""
    return [t for t in load_tool_specs().values() if persona in t.personas]


def _json_type(spec: str) -> dict[str, Any]:
    if "|" in spec:
        return {"type": "string", "enum": spec.split("|")}
    return {
        "string": {"type": "string"},
        "date": {"type": "string", "description": "ISO date, YYYY-MM-DD"},
        "datetime": {"type": "string", "description": "ISO 8601 date and time, India time"},
        "int": {"type": "integer"},
        "boolean": {"type": "boolean"},
        "object": {"type": "object"},
        "array": {"type": "array", "items": {"type": "object"}},
    }.get(spec, {"type": "string"})


def openai_tools(persona: Persona) -> list[dict[str, Any]]:
    """Function-calling schemas for the persona's allowlist. A trailing `?`
    in the contract marks an optional parameter."""
    out: list[dict[str, Any]] = []
    for spec in tools_for(persona):
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, ptype in spec.params.items():
            props[pname] = _json_type(ptype.rstrip("?"))
            if not ptype.endswith("?"):
                required.append(pname)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.name.replace("_", " "),
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            }
        )
    return out


class ToolRunner:
    """Executes a tool call on behalf of a session."""

    def __init__(self, core: CoreClient, session_jwt: str, persona: Persona) -> None:
        self._core = core
        self._jwt = session_jwt
        self._persona = persona

    def allows(self, name: str) -> bool:
        spec = load_tool_specs().get(name)
        return spec is not None and self._persona in spec.personas

    async def run(self, name: str, args: dict[str, Any]) -> Any:
        spec = load_tool_specs().get(name)
        if spec is None or self._persona not in spec.personas:
            raise ToolNotAllowed(f"{self._persona.value} may not call {name!r}")
        route = _ROUTES[name]

        body = dict(args)
        path = _fill(route.path, body)
        if route.method == "GET":
            return await self._core.get(path, session_jwt=self._jwt, params=_query(body))
        if name in _CALLBACK_KINDS:  # both tools share POST /callbacks
            body["kind"] = _CALLBACK_KINDS[name]
        return await self._core.request(route.method, path, session_jwt=self._jwt, json=body)


def _fill(template: str, args: dict[str, Any]) -> str:
    """Substitute `{x}` path params, consuming them from `args`."""
    out = template
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        key = out[start + 1 : end]
        if key not in args:
            raise ValueError(f"missing path argument {key!r}")
        out = out[:start] + str(args.pop(key)) + out[end + 1 :]
    return out


def _query(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v is not None}

