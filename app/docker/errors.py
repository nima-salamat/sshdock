"""Classify Docker CLI failures into stable categories."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.docker.manager import DockerResult


class DockerErrorKind(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ClassifiedDockerError:
    kind: DockerErrorKind
    message: str
    retryable: bool = False
    command: str = ""
    exit_code: int = 0


def classify_docker_result(result: DockerResult) -> ClassifiedDockerError:
    if result.exit_code == 0:
        return ClassifiedDockerError(DockerErrorKind.OK, "", command=result.command, exit_code=0)

    text = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
    cmd = result.command or ""

    if result.exit_code in {124, 137} or "timed out" in text or "timeout" in text:
        return ClassifiedDockerError(
            DockerErrorKind.TIMEOUT, (result.stderr or result.stdout or "timeout").strip(),
            retryable=True, command=cmd, exit_code=result.exit_code,
        )
    if "permission denied" in text or "access denied" in text or "cannot connect to the docker daemon" in text:
        return ClassifiedDockerError(
            DockerErrorKind.PERMISSION, (result.stderr or result.stdout or "permission denied").strip(),
            retryable=False, command=cmd, exit_code=result.exit_code,
        )
    if "no such" in text or "not found" in text or result.exit_code == 1 and "no such container" in text:
        return ClassifiedDockerError(
            DockerErrorKind.NOT_FOUND, (result.stderr or result.stdout or "not found").strip(),
            retryable=False, command=cmd, exit_code=result.exit_code,
        )
    if "conflict" in text or "already in use" in text or "is already allocated" in text:
        return ClassifiedDockerError(
            DockerErrorKind.CONFLICT, (result.stderr or result.stdout or "conflict").strip(),
            retryable=False, command=cmd, exit_code=result.exit_code,
        )
    if "cannot connect" in text or "connection refused" in text or "is the docker daemon running" in text:
        return ClassifiedDockerError(
            DockerErrorKind.UNAVAILABLE, (result.stderr or result.stdout or "docker unavailable").strip(),
            retryable=True, command=cmd, exit_code=result.exit_code,
        )
    if "invalid" in text or "malformed" in text or result.exit_code == 125:
        return ClassifiedDockerError(
            DockerErrorKind.INVALID, (result.stderr or result.stdout or "invalid argument").strip(),
            retryable=False, command=cmd, exit_code=result.exit_code,
        )
    return ClassifiedDockerError(
        DockerErrorKind.UNKNOWN, (result.stderr or result.stdout or f"exit {result.exit_code}").strip(),
        retryable=result.exit_code != 0, command=cmd, exit_code=result.exit_code,
    )
