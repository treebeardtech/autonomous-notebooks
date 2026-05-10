"""File logger for nb-mcp. Writes to `.nb_mcp.log` in CWD at INFO by default.

Override with `NB_MCP_LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR) and `NB_MCP_LOG_PATH`.
"""

import logging
import os
from pathlib import Path

_configured = False
_LOGGER_NAME = "nb_mcp"


def get_logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    level_name = os.environ.get("NB_MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    path = os.environ.get("NB_MCP_LOG_PATH", ".nb_mcp.log")
    handler: logging.Handler
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path)
    except OSError:
        # Fall back to stderr if the log file can't be opened (read-only FS, etc.)
        handler = logging.StreamHandler()

    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S%z",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True
    logger.info("nb mcp logger initialised (level=%s, path=%s)", level_name, path)
    return logger
