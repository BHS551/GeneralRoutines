"""
Utilidades de manejo de errores: reintentos con backoff exponencial
y registro de fallos de herramientas para la salvaguarda de revisión humana.
"""

import time
from collections.abc import Callable
from typing import Any, TypeVar

from utils.logger import get_logger

log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_MAX_RETRIES = 2
_BACKOFF_DELAYS = [2, 4]


def with_retry(tool_name: str, fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """
    Ejecuta `fn` con hasta _MAX_RETRIES reintentos.
    Si falla todas las veces, anota la información faltante y devuelve None.
    Nunca propaga la excepción al agente (el agente continúa con datos parciales).
    """
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 2):
        try:
            result = fn(*args, **kwargs)
            if attempt > 1:
                log.info(
                    "Herramienta recuperada tras reintento",
                    extra={"tool": tool_name, "attempt": attempt},
                )
            return result
        except Exception as exc:
            last_exc = exc
            log.warning(
                "Fallo de herramienta",
                extra={"tool": tool_name, "attempt": attempt, "error": str(exc)},
            )
            if attempt <= _MAX_RETRIES:
                time.sleep(_BACKOFF_DELAYS[attempt - 1])

    log.error(
        "Herramienta no disponible tras todos los reintentos — continuando con datos parciales",
        extra={"tool": tool_name, "final_error": str(last_exc)},
    )
    return None


class ToolFailureTracker:
    """
    Acumula fallos de herramientas durante una ejecución para incluirlos
    en el ticket y decidir si se necesita revisión humana.
    """

    def __init__(self) -> None:
        self._failures: list[str] = []
        self._missing_data: list[str] = []

    def record_failure(self, tool: str, missing_info: str) -> None:
        self._failures.append(tool)
        self._missing_data.append(missing_info)
        log.warning(
            "Dato faltante por fallo de herramienta",
            extra={"tool": tool, "missing": missing_info},
        )

    @property
    def has_failures(self) -> bool:
        return bool(self._failures)

    @property
    def summary(self) -> str:
        if not self._failures:
            return ""
        parts = [f"- {t}: {m}" for t, m in zip(self._failures, self._missing_data)]
        return "**Datos no disponibles por fallos de herramientas:**\n" + "\n".join(parts)

    @property
    def failed_tools(self) -> list[str]:
        return list(self._failures)


def needs_human_review(confidence_pct: int, tracker: ToolFailureTracker) -> bool:
    """Determina si el ticket debe marcarse para revisión humana."""
    threshold = int(__import__("os").environ.get("CONFIDENCE_THRESHOLD", "60"))
    return confidence_pct < threshold or tracker.has_failures
