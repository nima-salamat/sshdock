from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Iterable


def known_hosts_candidates() -> list[Path]:
    candidates = [Path.home() / ".ssh" / "known_hosts"]
    if (Path.home() / ".ssh" / "known_hosts2").exists():
        candidates.append(Path.home() / ".ssh" / "known_hosts2")
    return [p for p in candidates if p.exists()]


def fingerprint_from_public_key_blob(key_blob: bytes) -> str:
    digest = hashlib.sha256(key_blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def append_known_host(path: Path, host: str, port: int, key_line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = host if port == 22 else f"[{host}]:{port}"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{prefix} {key_line.strip()}\n")
