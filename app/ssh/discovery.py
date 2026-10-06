from __future__ import annotations

import glob
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SSHAlias:
    alias: str
    host: str = ""
    user: str = ""
    port: int = 22
    identity_files: list[str] = field(default_factory=list)
    identities_only: bool = False
    proxy_jump: str | None = None
    proxy_command: str | None = None
    forward_agent: bool = False
    server_alive_interval: int = 30
    server_alive_count_max: int = 3
    options: dict[str, str] = field(default_factory=dict)
    source_file: str = ""


def ssh_config_candidates() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = [home / ".ssh" / "config"]
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            candidates.append(Path(program_data) / "ssh" / "ssh_config")
    else:
        candidates.append(Path("/etc/ssh/ssh_config"))
    return [p for p in candidates if p.exists()]


def _unquote_tokens(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def _expand_include(path_token: str, base_dir: Path) -> list[Path]:
    """Resolve an Include path (supports ~, relative, and simple globs)."""
    expanded = os.path.expanduser(os.path.expandvars(path_token.strip()))
    p = Path(expanded)
    if not p.is_absolute():
        p = base_dir / p
    pattern = str(p)
    matches: list[Path] = []
    if any(ch in pattern for ch in "*?["):
        for m in sorted(glob.glob(pattern)):
            mp = Path(m)
            if mp.is_file():
                matches.append(mp)
    else:
        if p.is_file():
            matches.append(p)
        # Also try common config.d style without explicit glob
        elif p.is_dir():
            for child in sorted(p.glob("*")):
                if child.is_file() and not child.name.startswith("."):
                    matches.append(child)
    return matches


def discover_aliases(paths: list[Path] | None = None) -> list[SSHAlias]:
    paths = paths or ssh_config_candidates()
    aliases: list[SSHAlias] = []
    seen_files: set[Path] = set()
    for path in paths:
        aliases.extend(_parse_file(path, seen_files=seen_files))
    dedup: dict[str, SSHAlias] = {}
    for alias in aliases:
        # Prefer first occurrence (user config usually comes before system)
        if alias.alias not in dedup:
            dedup[alias.alias] = alias
    return sorted(dedup.values(), key=lambda a: a.alias.lower())


def _parse_file(path: Path, seen_files: set[Path] | None = None) -> list[SSHAlias]:
    if seen_files is None:
        seen_files = set()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved in seen_files:
        return []
    seen_files.add(resolved)

    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results: list[SSHAlias] = []
    blocks: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    names: list[str] = []
    base_dir = path.parent

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        lower = line.lower()

        # Global Include (outside Host) — process immediately
        if lower.startswith("include "):
            tokens = _unquote_tokens(line[8:].strip())
            for tok in tokens:
                for inc in _expand_include(tok, base_dir):
                    results.extend(_parse_file(inc, seen_files=seen_files))
            continue

        if lower.startswith("host "):
            if current is not None:
                for name in names:
                    if not _is_pattern_alias(name):
                        blocks.append((name, dict(current)))
            names = _unquote_tokens(line[5:].strip())
            current = {}
            continue

        # Match blocks are advanced / conditional — skip content for alias listing
        # (we only surface concrete Host names)
        if lower.startswith("match "):
            if current is not None:
                for name in names:
                    if not _is_pattern_alias(name):
                        blocks.append((name, dict(current)))
            current = None
            names = []
            continue

        if current is None:
            continue

        # Include inside a Host block is rare but legal; still process for completeness
        if lower.startswith("include "):
            tokens = _unquote_tokens(line[8:].strip())
            for tok in tokens:
                for inc in _expand_include(tok, base_dir):
                    results.extend(_parse_file(inc, seen_files=seen_files))
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        key_l = key.lower()
        # Accumulate multi-value options (IdentityFile can appear multiple times)
        if key_l == "identityfile":
            existing = current.get(key_l, "")
            current[key_l] = (existing + " " + value.strip()).strip() if existing else value.strip()
        else:
            current[key_l] = value.strip()

    if current is not None:
        for name in names:
            if not _is_pattern_alias(name):
                blocks.append((name, dict(current)))

    for alias, opts in blocks:
        identity = opts.get("identityfile", "")
        identity_files = []
        for item in _unquote_tokens(identity):
            identity_files.append(os.path.expanduser(os.path.expandvars(item)))
        results.append(
            SSHAlias(
                alias=alias,
                host=opts.get("hostname", alias),
                user=opts.get("user", ""),
                port=_safe_int(opts.get("port"), 22),
                identity_files=identity_files,
                identities_only=opts.get("identitiesonly", "no").lower() in {"yes", "true"},
                proxy_jump=opts.get("proxyjump"),
                proxy_command=opts.get("proxycommand"),
                forward_agent=opts.get("forwardagent", "no").lower() in {"yes", "true"},
                server_alive_interval=_safe_int(opts.get("serveraliveinterval"), 30),
                server_alive_count_max=_safe_int(opts.get("serveralivecountmax"), 3),
                options=opts,
                source_file=str(path),
            )
        )
    return results


def _safe_int(value: str | None, fallback: int) -> int:
    try:
        return int(value or fallback)
    except ValueError:
        return fallback


def _is_pattern_alias(value: str) -> bool:
    return any(ch in value for ch in "*?!") or value.lower() in {"*", "default"}
