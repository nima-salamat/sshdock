"""Pure-Python helpers for handling pasted private keys.

This module is deliberately Qt-free so it can be unit-tested headlessly
and reused from non-UI code paths (e.g. CLI importers, tests).

The ConnectionDialog uses :func:`write_pasted_key_to_temp` to turn a
pasted private key string into a 0600-permission file path that the
existing asyncssh-based SSH client can consume unchanged.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def write_pasted_key_to_temp(text: str, *, label: str = "pasted") -> Path:
    """Write *text* to a 0600-permission temp file under the user's cache dir.

    Returns the path. The caller treats this path as the ``private_key``
    field of the resulting SSHProfile, so the existing asyncssh-based SSH
    client code needs no changes.

    The directory is created with 0700 perms and the file with 0600 so the
    key is not world-readable on multi-user machines.

    The *label* is sanitised and embedded in the filename so the user can
    tell multiple pasted keys apart if they ever look at the cache dir.
    """
    base = Path.home() / ".cache" / "sshdock" / "pasted-keys"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:24] or "key"
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{ts}-{safe_label}-", suffix=".key", dir=str(base)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
        os.chmod(tmp_path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return Path(tmp_path)


def looks_like_private_key(text: str) -> bool:
    """Cheap heuristic: does *text* look like a private key?

    Used by the paste dialog to warn (not block) the user when the pasted
    content doesn't match a recognised PEM or OpenSSH key header.
    """
    if not text or not text.strip():
        return False
    first_line = text.strip().splitlines()[0]
    if "BEGIN" in first_line.upper() and (
        "PRIVATE KEY" in first_line.upper() or "RSA" in first_line.upper()
    ):
        return True
    if "openssh-key-v1" in text:
        return True
    return False
