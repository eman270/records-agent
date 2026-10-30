"""Langfuse tracing for the records agent.

Credentials come from the process environment (Compose env_file or Render).
With either project key missing, the agent runs unchanged.

Langfuse 4.16+ needs OpenTelemetry 1.45, which google-adk 2.12 rejects.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

_ready = False
_adk = False
_reported = False


def _keys_present() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    )


def tracing_enabled() -> bool:
    return _ready


def init_langfuse(*, instrument_adk: bool = False) -> bool:
    """Connect once per process. ADK instrumentation can be requested later."""
    global _ready, _adk, _reported
    if not _keys_present():
        if not _reported:
            print(
                "Langfuse: off. Add LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, "
                "and LANGFUSE_BASE_URL to the environment."
            )
            _reported = True
        return False
    if not _ready:
        os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "development")
        try:
            from langfuse import get_client

            get_client()
        except Exception as exc:
            if not _reported:
                print(f"Langfuse: could not start ({exc})")
                _reported = True
            return False
        _ready = True
        if not _reported:
            host = os.getenv("LANGFUSE_BASE_URL", "").strip() or "https://cloud.langfuse.com"
            print(f"Langfuse: sending traces to {host}")
            _reported = True
    if instrument_adk and not _adk:
        try:
            from openinference.instrumentation.google_adk import GoogleADKInstrumentor

            GoogleADKInstrumentor().instrument()
            _adk = True
        except Exception as exc:
            print(f"Langfuse: ADK instrumentation failed ({exc})")
    return _ready


def flush_traces() -> None:
    if not _ready:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        print(f"Langfuse: flush failed ({exc})")


@contextmanager
def agent_run(name: str, *, user_id: str, session_id: str, tags: list[str]):
    """Name one ADK run. The ADK integration supplies the nested generations and tools."""
    if not _ready:
        yield
        return
    from langfuse import propagate_attributes

    try:
        with propagate_attributes(
            trace_name=name,
            user_id=str(user_id),
            session_id=str(session_id),
            tags=tags,
        ):
            yield
    finally:
        flush_traces()


def attach_request_trace(app, *, trace_name: str, tags: list[str]) -> None:
    """Name traces for agent runs that enter through HTTP rather than run_question."""

    class _TraceMiddleware:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http" or scope.get("method") != "POST" or not _ready:
                await self.inner(scope, receive, send)
                return
            from langfuse import propagate_attributes

            try:
                with propagate_attributes(trace_name=trace_name, tags=list(tags)):
                    await self.inner(scope, receive, send)
            finally:
                flush_traces()

    app.add_middleware(_TraceMiddleware)
