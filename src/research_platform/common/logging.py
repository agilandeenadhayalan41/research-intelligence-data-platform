"""JSON logging with an explicit allowlist of non-sensitive context."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("run_id", "source", "environment"):
            if hasattr(record, name):
                payload[name] = str(getattr(record, name))
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Configure only the platform logger; repeated calls do not add handlers."""
    logger = logging.getLogger("research_platform")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
