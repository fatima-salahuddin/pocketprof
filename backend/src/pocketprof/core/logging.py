"""Structured JSON logs with a request ID (SDS 11).

Logs carry identifiers, timings and token counts, never message text, answers or marks: callers pass
those as structured fields and must not interpolate content into messages.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class ConsoleFormatter(logging.Formatter):
    """Readable single-line logs for a developer terminal."""

    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in record.__dict__.items()
                          if k not in _RESERVED and not k.startswith("_"))
        base = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:<7} " \
               f"{record.name}: {record.getMessage()}" + (f"  [{extras}]" if extras else "")
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup(json_logs: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_logs else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache", "urllib3", "fastembed",
                  "multipart", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
