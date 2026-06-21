"""
Logger estructurado (JSON) para trazabilidad completa de cada ejecución de la rutina.
Emite una línea JSON por evento con timestamp, fase, nivel y contexto adicional.
"""

import json
import logging
import os
import sys
import time
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Añadir campos extra pasados por el caller (extra={...})
        for key, val in vars(record).items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            }:
                entry[key] = val

        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# Logger principal de fases del agente
phase_log = get_logger("routine.phase")


def log_phase(phase: int, message: str, **context: Any) -> None:
    """Registra una decisión de fase con el formato [FASE N] para trazabilidad."""
    phase_log.info(
        f"[FASE {phase}] {message}",
        extra={"phase": phase, **context},
    )
