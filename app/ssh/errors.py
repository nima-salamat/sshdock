from __future__ import annotations

import asyncssh
from asyncssh.pbe import KeyEncryptionError


def classify_exception(exc: BaseException) -> tuple[str, str, bool]:
    """Map an asyncssh exception to (title, reason, retryable).

    The ``reason`` string is what we surface to the end user in the error
    dialog, so it should be human-readable and, where possible, point at the
    underlying cause and the remediation step.
    """

    # ------------------------------------------------------------------
    # Key / passphrase problems (NOT retryable — retrying with the same
    # material will fail identically). These are raised *before* the SSH
    # transport is established, so the server never sees a login attempt.
    # ------------------------------------------------------------------
    if isinstance(exc, KeyEncryptionError):
        msg = str(exc)
        # asyncssh raises this exact string from public_key.py when the
        # ``bcrypt`` package is missing but the user supplied an encrypted
        # OpenSSH-format private key (-----BEGIN OPENSSH PRIVATE KEY-----).
        if "bcrypt with KDF support" in msg:
            return (
                "Missing bcrypt dependency",
                (
                    "The selected private key is an encrypted OpenSSH key, "
                    "which requires the 'bcrypt' Python package to decrypt. "
                    "Install it with:  pip install bcrypt   (or "
                    "pip install asyncssh[bcrypt])  and reconnect."
                ),
                False,
            )
        # Other KeyEncryptionError cases: wrong passphrase, unknown cipher,
        # corrupted key, etc. Still not retryable with the same input.
        return "Private key decryption failed", msg, False

    # ------------------------------------------------------------------
    # Authentication / host-key problems (NOT retryable with same creds)
    # ------------------------------------------------------------------
    if isinstance(exc, asyncssh.PermissionDenied):
        return "Authentication failed", "The server rejected the supplied credentials.", False
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return "SSH host key rejected", str(exc), False

    # ------------------------------------------------------------------
    # Transient / network problems (retryable)
    # ------------------------------------------------------------------
    if isinstance(exc, (TimeoutError, asyncssh.ConnectionLost)):
        return "Connection lost or timed out", str(exc), True

    # ------------------------------------------------------------------
    # Fallback — anything else is treated as a generic, retryable SSH error.
    # ------------------------------------------------------------------
    return "SSH connection failed", str(exc), True
