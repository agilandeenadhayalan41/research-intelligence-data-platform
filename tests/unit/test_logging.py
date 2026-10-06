import json
import logging

import pytest

from research_platform.common.logging import JsonFormatter, configure_logging


def test_json_formatter_preserves_safe_context() -> None:
    record = logging.LogRecord(
        "research_platform.test", logging.INFO, __file__, 1, "event %s", ("ready",), None
    )
    record.run_id = "synthetic-run"
    record.source = "synthetic"
    record.environment = "local"
    record.private_payload = "must not appear"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "event ready"
    assert payload["level"] == "INFO"
    assert payload["source"] == "synthetic"
    assert payload["environment"] == "local"
    assert payload["run_id"] == "synthetic-run"
    assert "timestamp" in payload
    assert "private_payload" not in payload


def test_configuration_is_idempotent_and_does_not_change_root_logger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = logging.getLogger("research_platform")
    root_handlers = logging.getLogger().handlers[:]
    previous = (logger.handlers[:], logger.level, logger.propagate)
    try:
        configure_logging()
        configure_logging()
        assert len(logger.handlers) == 1
        assert logging.getLogger().handlers == root_handlers
        logger.info("configuration_ready", extra={"environment": "local"})
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["message"] == "configuration_ready"
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous[0]
        logger.setLevel(previous[1])
        logger.propagate = previous[2]
