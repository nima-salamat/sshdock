from __future__ import annotations

import hashlib
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import redact

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AppError:
    """Unified application error envelope used across UI, SSH, Docker and tasks."""

    title: str
    message: str
    details: str = ""
    operation: str = "application"
    retryable: bool = False
    error_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    cause: BaseException | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        title: str = "Unexpected error",
        message: str | None = None,
        operation: str = "application",
        retryable: bool = False,
        context: dict[str, Any] | None = None,
    ) -> "AppError":
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return cls(
            title=title,
            message=message or str(exc) or type(exc).__name__,
            details=redact(tb),
            operation=operation,
            retryable=retryable,
            cause=exc,
            context=context or {},
        )

    @property
    def fingerprint(self) -> str:
        raw = f"{self.operation}|{type(self.cause).__name__ if self.cause else ''}|{self.message}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:10]

    def log(self, level: int = logging.ERROR) -> None:
        log.log(
            level,
            "[%s] %s | operation=%s | retryable=%s | fingerprint=%s | context=%s\n%s",
            self.error_id,
            redact(self.message),
            self.operation,
            self.retryable,
            self.fingerprint,
            redact(self.context),
            self.details,
        )


class AppException(RuntimeError):
    def __init__(self, error: AppError):
        super().__init__(error.message)
        self.error = error


def normalize_error(exc: BaseException, *, operation: str = "application", **kwargs: Any) -> AppError:
    if isinstance(exc, AppException):
        error = exc.error
        if operation != "application":
            error.operation = operation
        return error
    return AppError.from_exception(exc, operation=operation, **kwargs)
