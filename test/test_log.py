"""Logger writes to NB_MCP_LOG_PATH at INFO level by default."""

import importlib
import logging
import os
from pathlib import Path


def test_logger_writes_file_at_info(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "nb_mcp.log"
    monkeypatch.setenv("NB_MCP_LOG_PATH", str(log_path))
    monkeypatch.setenv("NB_MCP_LOG_LEVEL", "INFO")

    # Force re-init: reset the module-level cache and drop existing handlers.
    from autonomous_notebooks import _log

    importlib.reload(_log)
    logger = logging.getLogger("nb_mcp")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    _log._configured = False

    log = _log.get_logger()
    log.info("hello info")
    log.debug("hidden debug")  # below INFO threshold
    log.warning("visible warning")

    for h in log.handlers:
        h.flush()

    body = log_path.read_text()
    assert "hello info" in body
    assert "visible warning" in body
    assert "hidden debug" not in body

    # tidy: unhook the file handler so the tmp file can be cleaned up
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    _log._configured = False
    # Restore default handler so subsequent tests in the session still work.
    if "NB_MCP_LOG_PATH" in os.environ:
        del os.environ["NB_MCP_LOG_PATH"]
