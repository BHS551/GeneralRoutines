"""
Monitor de uso de tokens y salud de la rutina.
Registra métricas por sesión para detectar degradación o abuso de límites de tasa.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)

# Límite conservador para alertar antes de alcanzar el tope de la cuenta
_WARN_TOKENS_PER_HOUR = int(os.environ.get("WARN_TOKENS_PER_HOUR", "80000"))


@dataclass
class SessionMetrics:
    session_id: str
    started_at: float = field(default_factory=time.time)
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    phase_reached: int = 0
    ticket_created: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "elapsed_s": round(self.elapsed_seconds, 1),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "phase_reached": self.phase_reached,
            "ticket_created": self.ticket_created,
        }


_active_sessions: dict[str, SessionMetrics] = {}


def start_session(session_id: str) -> SessionMetrics:
    metrics = SessionMetrics(session_id=session_id)
    _active_sessions[session_id] = metrics
    log.info("Sesión de rutina iniciada", extra={"session_id": session_id})
    return metrics


def end_session(session_id: str) -> SessionMetrics | None:
    metrics = _active_sessions.pop(session_id, None)
    if metrics:
        log.info("Sesión de rutina finalizada", extra=metrics.to_dict())
        _check_token_rate(metrics)
    return metrics


def record_token_usage(session_id: str, input_tokens: int, output_tokens: int) -> None:
    if session_id in _active_sessions:
        m = _active_sessions[session_id]
        m.input_tokens += input_tokens
        m.output_tokens += output_tokens


def record_tool_call(session_id: str, tool: str, success: bool) -> None:
    if session_id in _active_sessions:
        m = _active_sessions[session_id]
        m.tool_calls += 1
        if not success:
            m.tool_failures += 1
    log.info(
        "Tool call",
        extra={"session_id": session_id, "tool": tool, "success": success},
    )


def record_phase(session_id: str, phase: int) -> None:
    if session_id in _active_sessions:
        _active_sessions[session_id].phase_reached = phase


def record_ticket(session_id: str, jira_key: str) -> None:
    if session_id in _active_sessions:
        _active_sessions[session_id].ticket_created = jira_key


def _check_token_rate(metrics: SessionMetrics) -> None:
    """Emite una advertencia si el consumo de tokens supera el umbral horario estimado."""
    if metrics.elapsed_seconds < 1:
        return
    rate_per_hour = metrics.total_tokens * 3600 / metrics.elapsed_seconds
    if rate_per_hour > _WARN_TOKENS_PER_HOUR:
        log.warning(
            "Uso de tokens elevado — revisar límites de tasa",
            extra={
                "estimated_tokens_per_hour": int(rate_per_hour),
                "warn_threshold": _WARN_TOKENS_PER_HOUR,
                "session_id": metrics.session_id,
            },
        )
