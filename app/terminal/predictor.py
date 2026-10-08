"""Lightweight, privacy-aware command prediction for SSHDock terminals.

The predictor deliberately avoids heavyweight ML dependencies. It combines
prefix matching with frequency, recency, context, cwd and shell affinity.
All remote history is sanitized before it reaches this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import hashlib
import json
from dataclasses import dataclass
import math
import posixpath
import re
import shlex
from typing import Any, Iterable, Mapping


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|bearer)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(?:--password|--passwd|--token|--api-key|--secret)\s+\S+"),
    re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"(?i)\bdocker\s+login\b.*(?:\s-p\s+|\s--password(?:-stdin)?\s+)\S+"),
    re.compile(r"(?i)-----BEGIN (?:OPENSSH|RSA|EC|DSA|PRIVATE) KEY-----"),
)
_NO_SPACE_PATH_COMMAND = re.compile(
    r"^(?P<command>cd|pushd|popd)(?P<target>/.*|~(?:/.*)?|\.\.?/.*)$"
)


def normalize_prediction(command: str) -> str:
    """Normalize shell-directory predictions without changing real transport."""
    value = str(command or "")
    match = _NO_SPACE_PATH_COMMAND.match(value)
    if match is None:
        return value
    return f"{match.group('command')} {match.group('target')}"


def normalize_suggestions(commands: Iterable[str] | str | None, *, limit: int = 5) -> list[str]:
    """Normalize, deduplicate, and bound completion values without Qt dependencies."""
    if isinstance(commands, str):
        commands = [commands] if commands else []
    values: list[str] = []
    seen: set[str] = set()
    for command in commands or ():
        value = normalize_prediction(str(command or "").strip())
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
        if len(values) >= max(1, min(5, int(limit))):
            break
    return values


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


@dataclass(frozen=True, slots=True)
class PredictionCandidate:
    command: str
    score: float
    confidence: float
    source: str
    reason: str
    features: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "score": round(float(self.score), 4),
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "reason": self.reason,
            "features": {str(k): round(float(v), 4) for k, v in self.features.items()},
        }


class CommandPredictor:
    """Small contextual online-learning ranker with bounded local state."""

    STATE_VERSION = 3
    DEFAULT_WEIGHTS = {
        "bias": 0.35,
        "frequency": 2.6,
        "recency": 2.2,
        "transition": 2.9,
        "cwd": 1.65,
        "cwd_path": 1.8,
        "cwd_recency": 1.6,
        "shell": 1.05,
        "family_transition": 1.4,
        "argument_fit": 0.85,
        "directory_change": 0.9,
        "directory_familiarity": 1.7,
        "parent_navigation": 0.65,
        "token_fit": 2.5,
        "prefix_fit": 1.35,
        "accepted": 2.9,
        "success": 0.75,
        "failure": -1.75,
    }

    # Safe, read-only or low-risk starter commands keep the first terminal useful
    # while remote shell history is still being fetched. Learned commands always
    # outrank these bootstrap candidates.
    STARTER_COMMANDS = (
        "ls -la",
        "pwd",
        "cd ..",
        "whoami",
        "date",
        "df -h",
        "free -h",
        "ps aux",
        "docker ps",
        "docker images",
        "docker compose ps",
        "git status",
        "git log --oneline -5",
        "systemctl --failed",
        "journalctl -n 100 --no-pager",
        "ip addr",
        "ss -tulpn",
        "du -sh .",
        "cat /etc/os-release",
        "uname -a",
        "history",
    )

    def __init__(self, *, max_commands: int = 1200) -> None:
        self.max_commands = max(50, int(max_commands))
        self._history: dict[str, deque[CommandObservation]] = defaultdict(
            lambda: deque(maxlen=self.max_commands)
        )
        self._counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._last_seen: dict[str, dict[str, int]] = defaultdict(dict)
        self._transitions: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
        self._accepted: dict[str, Counter[str]] = defaultdict(Counter)
        self._success: dict[str, Counter[str]] = defaultdict(Counter)
        self._failure: dict[str, Counter[str]] = defaultdict(Counter)
        self._snapshot_fingerprints: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=32))
        self._weights: dict[str, float] = dict(self.DEFAULT_WEIGHTS)
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
        canonical = normalize_prediction(safe)

        scope = self._scope(server_id)
        obs = CommandObservation(canonical, self._normalize_cwd(cwd) if cwd else "", shell or "")
        history = self._history[scope]
        previous = history[-1].command if history else None

        if len(history) == history.maxlen:
            old = history[0]
            self._counts[scope][old.command] -= 1
            if self._counts[scope][old.command] <= 0:
                del self._counts[scope][old.command]

        history.append(obs)
        self._counts[scope][canonical] += 1
        self._clock += 1
        self._last_seen[scope][canonical] = self._clock

        if previous and previous != canonical:
            self._transitions[scope][(previous, canonical)] += 1
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
        commands = tuple(
            normalize_prediction(command)
            for command in snapshot.commands
            if sanitize_command(command)
        )
        if not commands:
            return 0
        scope = self._scope(server_id)
        payload = json.dumps(
            {"shell": snapshot.shell or "", "commands": commands},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(payload).hexdigest()
        fingerprints = self._snapshot_fingerprints[scope]
        if fingerprint in fingerprints:
            return 0
        fingerprints.append(fingerprint)
        # Shell history files do not carry a reliable cwd for every line.
        # Keep that context unknown rather than falsely attributing all history
        # to "/" and training misleading directory affinities.
        return self.ingest(
            commands,
            server_id=server_id,
            cwd="",
            shell=snapshot.shell,
        )

    def history(self, *, server_id: str = "") -> tuple[str, ...]:
        return tuple(obs.command for obs in self._history[self._scope(server_id)])

    @staticmethod
    def _prefix_parts(prefix: str) -> tuple[str, ...]:
        try:
            return tuple(shlex.split(prefix))
        except ValueError:
            return tuple(prefix.split())

    @staticmethod
    def _shell_name(shell: str) -> str:
        return str(shell or "").replace("\\", "/").rsplit("/", 1)[-1].lower()

    @staticmethod
    def _command_parts(command: str) -> tuple[str, ...]:
        try:
            return tuple(shlex.split(command))
        except ValueError:
            return tuple(command.split())

    @staticmethod
    def _normalize_cwd(cwd: str | None) -> str:
        """Normalize an absolute POSIX cwd received from the remote shell."""
        value = str(cwd or "").strip()
        if not value or not value.startswith("/"):
            return ""
        return posixpath.normpath(value)

    @staticmethod
    def _path_segments(path: str | None) -> tuple[str, ...]:
        normalized = CommandPredictor._normalize_cwd(path)
        if not normalized or normalized == "/":
            return ()
        return tuple(part for part in normalized.split("/") if part)

    @staticmethod
    def _path_similarity(left: str | None, right: str | None) -> float:
        """Segment-aware common path-prefix score (never a string-prefix test)."""
        a = CommandPredictor._path_segments(left)
        b = CommandPredictor._path_segments(right)
        if not a or not b:
            return 0.0
        common = 0
        for left_part, right_part in zip(a, b):
            if left_part != right_part:
                break
            common += 1
        return common / max(len(a), len(b))

    @staticmethod
    def _home_for_cwd(cwd: str | None) -> str | None:
        """Infer common POSIX home roots only when the current path supports it."""
        path = CommandPredictor._normalize_cwd(cwd)
        if not path:
            return None
        segments = CommandPredictor._path_segments(path)
        if segments and segments[0] == "root":
            return "/root"
        if len(segments) >= 2 and segments[0] in {"home", "Users"}:
            return "/" + "/".join(segments[:2])
        return None

    @classmethod
    def _directory_target(cls, command: str, cwd: str | None) -> str | None:
        """Resolve simple cd targets without executing shell expansions."""
        parts = list(cls._command_parts(normalize_prediction(command)))
        if not parts:
            return None
        if parts[0] in {"builtin", "command"} and len(parts) > 1:
            parts = parts[1:]
        if parts[0] != "cd":
            return None

        args = parts[1:]
        if args and args[0] == "--":
            args = args[1:]
        if len(args) > 1:
            # Compound commands may have other side effects and aren't merely
            # directory changes; don't treat them as no-op navigation.
            return None

        current = cls._normalize_cwd(cwd)
        target = args[0] if args else "~"
        if target == "-":
            return None

        if target in {"$PWD", "${PWD}", "$(pwd)", "`pwd`"}:
            target = current
        elif target.startswith("$PWD/"):
            if not current:
                return None
            target = posixpath.join(current, target[len("$PWD/"):])
        elif target.startswith("${PWD}/"):
            if not current:
                return None
            target = posixpath.join(current, target[len("${PWD}/"):])

        home = cls._home_for_cwd(current)
        if target in {"~", "$HOME", "${HOME}"}:
            if not home:
                return None
            target = home
        elif target.startswith("~/") or target.startswith("$HOME/") or target.startswith("${HOME}/"):
            if not home:
                return None
            if target.startswith("~/"):
                suffix = target[2:]
            elif target.startswith("$HOME/"):
                suffix = target[len("$HOME/"):]
            else:
                suffix = target[len("${HOME}/"):]
            target = posixpath.join(home, suffix)

        # Do not guess the meaning of unexpanded environment variables, globs,
        # or command substitutions; they may refer to a different directory.
        if "$" in target or "`" in target or "$(" in target:
            return None
        if not target or target == ".":
            target = current
        elif not target.startswith("/"):
            if not current:
                return None
            target = posixpath.join(current, target)

        return posixpath.normpath(target) if target.startswith("/") else None

    @classmethod
    def _is_noop_directory_change(cls, command: str, cwd: str | None) -> bool:
        current = cls._normalize_cwd(cwd)
        target = cls._directory_target(command, current)
        return bool(current and target and current == target)

    @classmethod
    def _command_family(cls, command: str) -> str:
        """Generalize transitions across flags/arguments of the same executable."""
        parts = cls._command_parts(normalize_prediction(command))
        if not parts:
            return ""
        executable = parts[0].rsplit("/", 1)[-1].casefold()
        return executable

    def _build_context(self, scope: str, cwd: str, shell: str) -> dict[str, Any]:
        """Precompute contextual statistics once per ranking request."""
        observations = tuple(self._history.get(scope, ()))
        current_cwd = self._normalize_cwd(cwd)
        current_shell = self._shell_name(shell)
        cwd_hits: Counter[str] = Counter()
        shell_hits: Counter[str] = Counter()
        cwd_similarity: dict[str, float] = {}
        cwd_last_index: dict[str, int] = {}
        known_directories: Counter[str] = Counter()
        family_transitions: Counter[tuple[str, str]] = Counter()
        family_totals: Counter[str] = Counter()

        for index, observation in enumerate(observations):
            observed_cwd = self._normalize_cwd(observation.cwd)
            if observed_cwd:
                known_directories[observed_cwd] += 1
                if current_cwd and observed_cwd == current_cwd:
                    cwd_hits[observation.command] += 1
                    cwd_last_index[observation.command] = index
                elif current_cwd and observation.command:
                    similarity = self._path_similarity(current_cwd, observed_cwd)
                    if similarity > cwd_similarity.get(observation.command, 0.0):
                        cwd_similarity[observation.command] = similarity

            if current_shell and self._shell_name(observation.shell) == current_shell:
                shell_hits[observation.command] += 1

            directory_target = self._directory_target(observation.command, observed_cwd)
            if directory_target:
                known_directories[directory_target] += 1

            if index:
                previous_family = self._command_family(observations[index - 1].command)
                next_family = self._command_family(observation.command)
                if previous_family:
                    family_totals[previous_family] += 1
                    family_transitions[(previous_family, next_family)] += 1

        cwd_recency = {
            command: math.exp(-max(0, len(observations) - 1 - index) / 20.0)
            for command, index in cwd_last_index.items()
        }
        previous_family = self._command_family(observations[-1].command) if observations else ""
        path_words: set[str] = set()
        for segment in self._path_segments(current_cwd):
            path_words.update(word.casefold() for word in re.split(r"[^A-Za-z0-9]+", segment) if len(word) > 2)
        path_words.difference_update({
            "home", "users", "root", "srv", "var", "tmp", "opt", "usr",
            "local", "workspace", "workspaces", "projects", "project",
        })

        return {
            "observations": observations,
            "cwd": current_cwd,
            "shell": current_shell,
            "cwd_hits": cwd_hits,
            "shell_hits": shell_hits,
            "cwd_similarity": cwd_similarity,
            "cwd_recency": cwd_recency,
            "known_directories": known_directories,
            "family_transitions": family_transitions,
            "family_totals": family_totals,
            "previous_family": previous_family,
            "path_words": path_words,
        }

    def clear(self) -> None:
        self._history.clear()
        self._counts.clear()
        self._last_seen.clear()
        self._transitions.clear()
        self._accepted.clear()
        self._success.clear()
        self._failure.clear()
        self._snapshot_fingerprints.clear()
        self._weights = dict(self.DEFAULT_WEIGHTS)
        self._clock = 0

    def _feature_values(
        self,
        scope: str,
        command: str,
        prefix: str,
        cwd: str,
        shell: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        context = context or self._build_context(scope, cwd, shell)
        observations = context["observations"]
        counts = self._counts[scope]
        count = counts.get(command, 0)
        frequency = min(1.0, math.log1p(count) / math.log(16.0))

        last = self._last_seen[scope].get(command, 0)
        age = max(0, self._clock - last)
        recency = math.exp(-age / 80.0) if last else 0.0

        previous = observations[-1].command if observations else None
        transition_count = self._transitions[scope].get((previous, command), 0) if previous else 0
        transition = min(1.0, math.log1p(transition_count) / math.log(8.0))

        cwd_norm = context["cwd"]
        cwd_hits = context["cwd_hits"].get(command, 0)
        cwd_affinity = min(1.0, math.log1p(cwd_hits) / math.log(6.0))
        cwd_path = context["cwd_similarity"].get(command, 0.0)
        cwd_recent = context["cwd_recency"].get(command, 0.0)

        shell_hits = context["shell_hits"].get(command, 0)
        shell_affinity = min(1.0, math.log1p(shell_hits) / math.log(6.0))

        current_parts = self._prefix_parts(prefix or "")
        candidate_parts = self._command_parts(command)
        prefix_fit = 1.0 if not (prefix or "").strip() or command.casefold().startswith((prefix or "").casefold()) else 0.0

        token_fit = 0.0
        if current_parts and candidate_parts[: len(current_parts)] == current_parts:
            token_fit = 1.0
        elif len(current_parts) == 1 and candidate_parts and candidate_parts[0].casefold().startswith(current_parts[0].casefold()):
            token_fit = 0.65

        previous_family = context["previous_family"]
        candidate_family = self._command_family(command)
        family_total = context["family_totals"].get(previous_family, 0)
        family_transition = (
            context["family_transitions"].get((previous_family, candidate_family), 0) / family_total
            if previous_family and family_total
            else 0.0
        )

        path_words = context["path_words"]
        argument_words: set[str] = set()
        for token in candidate_parts[1:]:
            argument_words.update(word.casefold() for word in re.split(r"[^A-Za-z0-9]+", token) if len(word) > 2)
        argument_fit = (
            min(1.0, len(path_words.intersection(argument_words)) / max(1, len(path_words)))
            if path_words
            else 0.0
        )

        directory_target = self._directory_target(command, cwd_norm)
        directory_change = 1.0 if directory_target and directory_target != cwd_norm else 0.0
        known_directory_hits = (
            context["known_directories"].get(directory_target, 0)
            if directory_target
            else 0
        )
        directory_familiarity = (
            min(1.0, math.log1p(known_directory_hits) / math.log(4.0))
            if known_directory_hits
            else 0.0
        )
        parent_navigation = (
            1.0
            if directory_target
            and cwd_norm
            and directory_target != cwd_norm
            and directory_target == posixpath.dirname(cwd_norm)
            else 0.0
        )

        accepted_count = self._accepted[scope].get(command, 0)
        accepted_total = max(1, sum(self._accepted[scope].values()))
        accepted = min(1.0, math.log1p(accepted_count) / math.log1p(accepted_total + 2))

        success_count = self._success[scope].get(command, 0)
        failure_count = self._failure[scope].get(command, 0)
        outcomes = success_count + failure_count
        success = success_count / outcomes if outcomes else 0.0
        failure = failure_count / outcomes if outcomes else 0.0

        return {
            "frequency": frequency,
            "recency": recency,
            "transition": transition,
            "cwd": cwd_affinity,
            "cwd_path": cwd_path,
            "cwd_recency": cwd_recent,
            "shell": shell_affinity,
            "family_transition": family_transition,
            "argument_fit": argument_fit,
            "directory_change": directory_change,
            "directory_familiarity": directory_familiarity,
            "parent_navigation": parent_navigation,
            "token_fit": token_fit,
            "prefix_fit": prefix_fit,
            "accepted": accepted,
            "success": success,
            "failure": failure,
        }

    def _score(self, features: Mapping[str, float]) -> float:
        score = self._weights.get("bias", 0.0)
        for name, value in features.items():
            score += self._weights.get(name, 0.0) * float(value)
        return score

    @staticmethod
    def _reason(features: Mapping[str, float]) -> tuple[str, str]:
        ranked = sorted(
            ((name, float(value)) for name, value in features.items() if value > 0.05),
            key=lambda item: item[1],
            reverse=True,
        )
        top = ranked[0][0] if ranked else "history"
        reasons = {
            "accepted": ("learned", "You tend to accept this command"),
            "transition": ("next", "Often follows your recent command"),
            "cwd": ("cwd", "Common in this directory"),
            "shell": ("shell", "Fits this shell"),
            "frequency": ("history", "Frequently used in your history"),
            "recency": ("recent", "Used recently"),
            "token_fit": ("context", "Strong token/context match"),
            "prefix_fit": ("prefix", "Matches what you typed"),
        }
        return reasons.get(top, ("history", "Learned from terminal context"))

    @staticmethod
    def _confidence(scores: list[float], score: float) -> float:
        if not scores:
            return 0.0
        ordered = sorted(scores, reverse=True)
        margin = max(0.0, score - (ordered[1] if len(ordered) > 1 else score - 2.5))
        value = 52.0 + 35.0 * (1.0 - math.exp(-max(0.0, score) / 8.0))
        value += 13.0 * (1.0 - math.exp(-margin / 2.2))
        return max(50.0, min(99.0, value))

    def suggest_ranked(
        self,
        prefix: str,
        *,
        server_id: str = "",
        cwd: str = "/",
        shell: str = "",
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[PredictionCandidate]:
        scope = self._scope(server_id)
        counts = self._counts[scope]
        raw = prefix or ""
        empty_prefix = not raw.strip()

        # Do not make suggestions depend on an async history read completing.
        # History-backed candidates retain their learned features; starter
        # candidates only contribute prefix/context fit and never get recorded
        # as commands the user actually ran.
        candidates = dict.fromkeys(counts.keys() if counts else self.STARTER_COMMANDS)
        context = self._build_context(scope, cwd or "/", shell or "")
        scored: list[tuple[float, str, dict[str, float]]] = []

        for command in candidates:
            candidate = normalize_prediction(command)
            if not candidate:
                continue
            if not empty_prefix and not candidate.casefold().startswith(raw.casefold()):
                continue
            if len(candidate) <= len(raw):
                continue
            if self._is_noop_directory_change(candidate, cwd or "/"):
                continue
            features = self._feature_values(
                scope, candidate, raw, cwd or "/", shell or "", context=context
            )
            score = self._score(features)
            if score >= float(min_score):
                scored.append((score, candidate, features))

        scored.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
        scores = [score for score, _, _ in scored]
        selected: list[PredictionCandidate] = []
        seen: set[str] = set()

        for score, candidate, features in scored:
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            if candidate in self.STARTER_COMMANDS and candidate not in counts:
                source, reason = (
                    "starter",
                    "Safe starter suggestion while terminal history loads",
                )
            else:
                source, reason = self._reason(features)
            selected.append(
                PredictionCandidate(
                    command=candidate,
                    score=score,
                    confidence=self._confidence(scores, score),
                    source=source,
                    reason=reason,
                    features=features,
                )
            )
            if len(selected) >= max(1, min(5, int(limit))):
                break
        return selected

    def record_suggestion_acceptance(
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
        canonical = normalize_prediction(safe)
        scope = self._scope(server_id)
        features = self._feature_values(scope, canonical, canonical, cwd or "/", shell or "")
        prediction = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, self._score(features)))))
        learning_rate = 0.08
        for name, value in features.items():
            updated = self._weights.get(name, 0.0) + learning_rate * (1.0 - prediction) * value
            self._weights[name] = max(-4.0, min(6.0, updated))
        self._accepted[scope][canonical] += 1
        return True

    def record_command_result(
        self,
        command: str,
        *,
        success: bool,
        server_id: str = "",
    ) -> bool:
        safe = sanitize_command(command)
        if safe is None:
            return False
        canonical = normalize_prediction(safe)
        target = self._success if success else self._failure
        target[self._scope(server_id)][canonical] += 1
        return True

    def state_dict(self) -> dict[str, Any]:
        histories = {}
        for scope, observations in self._history.items():
            histories[scope] = [
                {"command": obs.command, "cwd": obs.cwd, "shell": obs.shell}
                for obs in list(observations)[-self.max_commands :]
            ]
        return {
            "version": self.STATE_VERSION,
            "max_commands": self.max_commands,
            "clock": self._clock,
            "weights": dict(self._weights),
            "history": histories,
            "accepted": {scope: dict(values) for scope, values in self._accepted.items() if values},
            "success": {scope: dict(values) for scope, values in self._success.items() if values},
            "failure": {scope: dict(values) for scope, values in self._failure.items() if values},
            "snapshot_fingerprints": {
                scope: list(values)[-32:]
                for scope, values in self._snapshot_fingerprints.items()
                if values
            },
        }

    def load_state_dict(self, state: Mapping[str, Any] | None) -> None:
        if not isinstance(state, Mapping):
            return
        self.clear()
        try:
            state_version = int(state.get("version", 1))
        except (TypeError, ValueError):
            state_version = 1
        raw_history = state.get("history")
        if isinstance(raw_history, Mapping):
            for scope, rows in raw_history.items():
                if not isinstance(rows, list):
                    continue
                for row in rows[-self.max_commands:]:
                    if not isinstance(row, Mapping):
                        continue
                    command = row.get("command")
                    if not isinstance(command, str):
                        continue
                    cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else ""
                    # Version-2 imported shell history was ingested without
                    # per-command cwd data and falsely defaulted to "/". Drop
                    # that unreliable location signal instead of training a
                    # misleading root-directory preference.
                    if state_version < 3 and cwd == "/":
                        cwd = ""
                    shell = row.get("shell") if isinstance(row.get("shell"), str) else ""
                    self.add_command(
                        command,
                        server_id=str(scope),
                        cwd=cwd,
                        shell=shell,
                    )

        for name, target in (
            ("accepted", self._accepted),
            ("success", self._success),
            ("failure", self._failure),
        ):
            raw = state.get(name)
            if not isinstance(raw, Mapping):
                continue
            for scope, values in raw.items():
                if not isinstance(values, Mapping):
                    continue
                counter = target[self._scope(str(scope))]
                for command, count in values.items():
                    safe = sanitize_command(str(command))
                    if safe is None:
                        continue
                    try:
                        amount = max(0, int(count))
                    except (TypeError, ValueError):
                        continue
                    if amount:
                        counter[normalize_prediction(safe)] = amount

        raw_weights = state.get("weights")
        if isinstance(raw_weights, Mapping):
            for name, default in self.DEFAULT_WEIGHTS.items():
                try:
                    self._weights[name] = max(-4.0, min(6.0, float(raw_weights.get(name, default))))
                except (TypeError, ValueError):
                    self._weights[name] = default

        fingerprints = state.get("snapshot_fingerprints")
        if isinstance(fingerprints, Mapping):
            for scope, values in fingerprints.items():
                if not isinstance(values, list):
                    continue
                bucket = self._snapshot_fingerprints[self._scope(str(scope))]
                for value in values[-32:]:
                    if isinstance(value, str) and value:
                        bucket.append(value)

        try:
            self._clock = max(self._clock, int(state.get("clock", self._clock)))
        except (TypeError, ValueError):
            pass

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
        """Suggest context-ranked commands, including safe cold-start candidates."""
        raw = prefix or ""
        empty_prefix = not raw.strip()
        if empty_prefix and limit == 1:
            return None

        scope = self._scope(server_id)
        counts = self._counts[scope]
        # Starter suggestions deliberately have a lower score than personalized
        # history, but should still be available on a brand-new server.
        threshold = float(min_score) if counts else -1.0
        ranked = self.suggest_ranked(
            raw,
            server_id=server_id,
            cwd=cwd or "/",
            shell=shell or "",
            limit=max(1, min(5, int(limit))),
            min_score=threshold,
        )
        selected = [candidate.command for candidate in ranked]
        if limit == 1:
            return selected[0] if selected else None
        return selected


def parse_history_output(output: str) -> HistorySnapshot:
    """Parse bash/zsh/fish history and keep only the detected shell's history."""
    commands: list[str] = []
    shell = ""
    source = ""
    wanted_source = ""

    for raw in (output or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        if stripped.startswith("__SSHDock_SHELL__"):
            shell = stripped.split("=", 1)[-1].strip()
            shell_name = shell.rsplit("/", 1)[-1].lower()
            wanted_source = shell_name if shell_name in {"bash", "zsh", "fish"} else ""
            continue

        if stripped == "__BASH__":
            source = "bash"
            continue
        if stripped == "__ZSH__":
            source = "zsh"
            continue
        if stripped == "__FISH__":
            source = "fish"
            continue

        if wanted_source and source != wanted_source:
            continue

        if source == "fish":
            if not stripped.startswith("- cmd:"):
                continue
            line = stripped.split(":", 1)[1].strip()
        elif stripped.startswith(": ") and ";" in stripped:
            line = stripped.split(";", 1)[1].strip()
        else:
            line = stripped

        safe = sanitize_command(line)
        if safe is not None:
            commands.append(safe)

    return HistorySnapshot(tuple(commands[-1200:]), shell=shell)





async def load_remote_history(ssh) -> HistorySnapshot:
    """Read a bounded set of common shell history files once per session."""
    script = (
        "printf '__SSHDock_SHELL__=%s\\n' \"$SHELL\"; "
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
