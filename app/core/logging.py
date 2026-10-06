from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from platformdirs import user_log_dir

_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passphrase|token|secret|private[_-]?key)\s*[:=]\s*[^\s,]+"),
    re.compile(r"(?i)(-p|--password)\s+[^\s]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,]+"),
)


def redact(value: Any) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        def repl(match: re.Match[str]) -> str:
            prefix = match.group(1) if match.lastindex else ""
            if prefix.lower().strip().startswith("authorization"):
                return f"{prefix}<redacted>"
            return f"{prefix}=<redacted>" if prefix else "<redacted>"
        text = pattern.sub(repl, text)
    return text


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        record.msg = redact(message)
        record.args = ()
        return super().format(record)


def configure_logging(level: int = logging.INFO) -> Path:
    """Configure idempotent console + persistent redacted file logging."""
    log_dir = Path(user_log_dir("SSHDock", "SSHDock"))
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "sshdock.log"

    root = logging.getLogger()
    root.setLevel(level)
    formatter = SafeFormatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    existing_paths: set[str] = set()
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            existing_paths.add(str(Path(handler.baseFilename).resolve()))

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    if str(path.resolve()) not in existing_paths:
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return path
