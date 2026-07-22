from __future__ import annotations

from fastapi.responses import JSONResponse

from app.server.exceptions.envelope import ErrorEnvelope, error_envelope


def json_error_response(
    *,
    code: int,
    msg: str,
    data=None,
    status_code: int | None = None,
    headers=None,
) -> JSONResponse:
    """Handlers / middleware 统一出口：业务码查表得 HTTP 状态，不手传 status_code。"""
    envelope = error_envelope(
        code=code,
        msg=msg,
        data=data,
        status_code=status_code,
        headers=headers,
    )
    return envelope_to_response(envelope)


def envelope_to_response(envelope: ErrorEnvelope) -> JSONResponse:
    return JSONResponse(
        status_code=envelope.status_code,
        content=envelope.body,
        headers=dict(envelope.headers) if envelope.headers else None,
    )
