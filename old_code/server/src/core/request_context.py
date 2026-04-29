"""Request-scoped context helpers (request_id / trace_id)."""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_current_request_id(request_id: str) -> Token:
    value = str(request_id or "").strip()
    return _request_id_ctx.set(value)


def get_current_request_id(default: str = "") -> str:
    value = _request_id_ctx.get()
    return value or default


def reset_current_request_id(token: Token) -> None:
    _request_id_ctx.reset(token)


def set_current_trace_id(trace_id: str) -> Token:
    value = str(trace_id or "").strip()
    return _trace_id_ctx.set(value)


def get_current_trace_id(default: str = "") -> str:
    value = _trace_id_ctx.get()
    return value or default


def reset_current_trace_id(token: Token) -> None:
    _trace_id_ctx.reset(token)
