"""Lightweight, privacy-aware command prediction for SSHDock terminals.

The predictor deliberately avoids heavyweight ML dependencies. It combines
prefix matching with frequency, recency, context, cwd and shell affinity.
All remote history is sanitized before it reaches this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import math
import re
import shlex
from typing import Iterable


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|bearer)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(?:--password|--passwd|--token|--api-key|--secret)\s+\S+"),
    re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"(?i)\bdocker\s+login\b.*(?:\s-p\s+|\s--password(?:-stdin)?\s+)\S+"),
    re.compile(r"(?i)-----BEGIN (?:OPENSSH|RSA|EC|DSA|PRIVATE) KEY-----"),
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(?:password|passwd|token|api[_-]?key|secret|access[_-]?key|private[_-]?key)\s*="
)


def sanitize_command(command: str) -> str | None:
    """Return a safe command string, or None when it likely contains a secret."""
    value = (command or "").strip()
    if not value or len(value) > 4096:
        return None
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        return None
    if _ASSIGNMENT_SECRET.search(value):
        return None
    return value


@dataclass(frozen=True, slots=True)
class PredictionContext:
    server_id: str = ""
    cwd: str = "/"
    shell: str = ""


@dataclass(frozen=True, slots=True)
class CommandObservation:
    command: str
    cwd: str = "/"
    shell: str = ""


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    commands: tuple[str, ...]
    shell: str = ""


class CommandPredictor:
    """Deterministic lightweight predictor with bounded in-memory state."""

    def __init__(self, *, max_commands: int = 1200) -> None:
        self.max_commands = max(50, int(max_commands))
        self._history: dict[str, deque[CommandObservation]] = defaultdict(
            lambda: deque(maxlen=self.max_commands)
        )
        self._counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_seen: dict[str, dict[str, int]] = defaultdict(dict)
        self._transitions: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
        self._clock = 0

    @staticmethod
    def _scope(server_id: str | None) -> str:
        return str(server_id or "__local__")

    def add_command(
        self,
        command: str,
        *,
        server_id: str = "",
        cwd: str = "/",
        shell: str = "",
    ) -> bool:
        safe = sanitize_command(command)
        if safe is None:
            return False

        scope = self._scope(server_id)
        obs = CommandObservation(safe, cwd or "/", shell or "")
        history = self._history[scope]
        previous = history[-1].command if history else None

        if len(history) == history.maxlen:
            old = history[0]
            self._counts[scope][old.command] -= 1
            if self._counts[scope][old.command] <= 0:
                del self._counts[scope][old.command]

        history.append(obs)
        self._counts[scope][safe] += 1
        self._clock += 1
        self._last_seen[scope][safe] = self._clock

        if previous and previous != safe:
            self._transitions[scope][(previous, safe)] += 1
        return True

    def ingest(
        self,
        commands: Iterable[str],
        *,
        server_id: str = "",
        cwd: str = "/",
        shell: str = "",
    ) -> int:
        added = 0
        for command in commands:
            if self.add_command(command, server_id=server_id, cwd=cwd, shell=shell):
                added += 1
        return added

    def ingest_snapshot(self, snapshot: HistorySnapshot, *, server_id: str = "") -> int:
        return self.ingest(snapshot.commands, server_id=server_id, shell=snapshot.shell)

    def history(self, *, server_id: str = "") -> tuple[str, ...]:
        return tuple(obs.command for obs in self._history[self._scope(server_id)])

    @staticmethod
    def _prefix_parts(prefix: str) -> tuple[str, ...]:
        try:
            return tuple(shlex.split(prefix))
        except ValueError:
            return tuple(prefix.split())

    def suggest(
        self,
        prefix: str,
        *,
        server_id: str = "",
        cwd: str = "/",
        shell: str = "",
        limit: int = 1,
        min_score: float = 3.0,
    ) -> str | list[str] | None:
        """Suggest the best command(s) starting with the current editable prefix."""
        raw = prefix or ""
        if not raw.strip():
            return [] if limit != 1 else None

        scope = self._scope(server_id)
        counts = self._counts[scope]
        if not counts:
            return [] if limit != 1 else None

        current_parts = self._prefix_parts(raw)
        previous = self._history[scope][-1].command if self._history[scope] else None
        cwd_norm = cwd or "/"
        shell_norm = (shell or "").split("/")[-1].lower()

        candidates: list[tuple[float, str]] = []
        raw_cf = raw.casefold()

        for command, count in counts.items():
            if not command.casefold().startswith(raw_cf):
                continue
            if len(command) <= len(raw):
                continue

            score = 10.0
            score += min(7.0, math.log2(count + 1) * 1.8)

            last = self._last_seen[scope].get(command, 0)
            age = max(0, self._clock - last)
            score += 4.0 * math.exp(-age / 80.0)

            if previous:
                transition_count = self._transitions[scope].get((previous, command), 0)
                score += min(6.0, transition_count * 2.0)

            if current_parts:
                try:
                    candidate_parts = shlex.split(command)
                except ValueError:
                    candidate_parts = command.split()
                if candidate_parts and candidate_parts[: len(current_parts)] == list(current_parts):
                    score += 3.0

            if any(
                obs.command == command and (obs.cwd or "/") == cwd_norm
                for obs in self._history[scope]
            ):
                score += 2.0

            if shell_norm and any(
                obs.command == command
                and (obs.shell or "").split("/")[-1].lower() == shell_norm
                for obs in self._history[scope]
            ):
                score += 1.0

            candidates.append((score, command))

        candidates.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
        selected = [
            command
            for score, command in candidates
            if score >= min_score
        ][: max(1, int(limit))]

        if limit == 1:
            return selected[0] if selected else None
        return selected


def parse_history_output(output: str) -> HistorySnapshot:
    """Parse bash/zsh/fish history formats from a bounded mixed stream."""
    commands: list[str] = []
    shell = ""

    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("__SSHDock_SHELL__"):
            shell = line.split("=", 1)[-1].strip()
            continue
        if line in {"__BASH__", "__ZSH__", "__FISH__"}:
            continue
        if line.startswith(": ") and ";" in line:
            line = line.split(";", 1)[1].strip()
        elif line.startswith("- cmd:"):
            line = line.split(":", 1)[1].strip()

        safe = sanitize_command(line)
        if safe is not None:
            commands.append(safe)

    return HistorySnapshot(tuple(commands[-1200:]), shell=shell)


async def load_remote_history(ssh) -> HistorySnapshot:
    """Read a bounded set of common shell history files once per session."""
    script = (
        "printf '__SSHDock_SHELL__=__DOLLAR__{SHELL:-}__DOLLAR__\\n'; "
        "printf '__BASH__\\n'; tail -n 500 ~/.bash_history 2>/dev/null; "
        "printf '__ZSH__\\n'; tail -n 500 ~/.zsh_history 2>/dev/null; "
        "printf '__FISH__\\n'; tail -n 500 ~/.local/share/fish/fish_history 2>/dev/null"
    ).replace("__DOLLAR__", "$")

    try:
        result = await ssh.run_fast(script, timeout=8)
    except Exception:
        return HistorySnapshot(())

    if getattr(result, "exit_code", 0) != 0 and not getattr(result, "stdout", ""):
        return HistorySnapshot(())

    return parse_history_output(getattr(result, "stdout", "") or "")
