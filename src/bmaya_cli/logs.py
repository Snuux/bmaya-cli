import logging
import os
import sys
from pathlib import Path

from bmaya_cli.sessions import bmaya_home

KEEP_SERVER_LOGS = 20
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> None:
    logger = logging.getLogger("bmaya_cli")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(FORMAT)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)
    logs_dir = bmaya_home() / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        prune_old_logs(logs_dir)
        file_handler = logging.FileHandler(logs_dir / f"server-{os.getpid()}.log", encoding="utf-8")
    except OSError as exc:
        logger.warning("file logging disabled: %s", exc)
        return
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def prune_old_logs(logs_dir: Path) -> None:
    files = sorted(logs_dir.glob("server-*.log"), key=lambda p: p.stat().st_mtime)
    for old in files[:-KEEP_SERVER_LOGS]:
        try:
            old.unlink()
        except OSError:
            pass
