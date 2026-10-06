"""Tests for SSH error classification — especially the bcrypt / KDF branch.

These tests pin the behaviour that the user-facing error dialog shows an
actionable, NON-retryable message when asyncssh raises::

    KeyEncryptionError: OpenSSH private key encryption requires bcrypt
    with KDF support

instead of the previous generic "SSH connection failed" / retryable=True
fallback.
"""
from __future__ import annotations

import base64

import asyncssh
import pytest
from asyncssh.pbe import KeyEncryptionError

from app.ssh.errors import classify_exception


# --------------------------------------------------------------------------
# KeyEncryptionError — the exact exception class asyncssh raises from
# public_key.py:2557 when bcrypt is missing and the user feeds it an
# encrypted OpenSSH key.
# --------------------------------------------------------------------------

def test_classify_bcrypt_missing_error_is_actionable_and_not_retryable():
    exc = KeyEncryptionError(
        "OpenSSH private key encryption requires bcrypt with KDF support"
    )
    title, reason, retryable = classify_exception(exc)

    assert retryable is False, (
        "Retrying with the same key + missing bcrypt must not be retried — "
        "it will fail identically and confuse the user."
    )
    assert "bcrypt" in reason.lower()
    assert "pip install" in reason, "Reason should tell the user how to fix it"
    # Title should be more specific than the generic "SSH connection failed".
    assert title != "SSH connection failed"


def test_classify_other_key_encryption_error_still_not_retryable():
    """Wrong passphrase / corrupted key — also not retryable with same input."""
    exc = KeyEncryptionError("Incorrect passphrase")
    title, reason, retryable = classify_exception(exc)

    assert retryable is False
    assert "Incorrect passphrase" in reason
    assert title == "Private key decryption failed"


# --------------------------------------------------------------------------
# Regression: the existing branches must keep their original semantics so
# the error dialog in the UI keeps behaving the same way for other faults.
# --------------------------------------------------------------------------

def test_classify_permission_denied_still_not_retryable():
    title, reason, retryable = classify_exception(asyncssh.PermissionDenied("no"))

    assert title == "Authentication failed"
    assert retryable is False


def test_classify_host_key_not_verifiable_still_not_retryable():
    title, reason, retryable = classify_exception(asyncssh.HostKeyNotVerifiable("fp"))

    assert title == "SSH host key rejected"
    assert retryable is False


def test_classify_timeout_is_retryable():
    title, _reason, retryable = classify_exception(TimeoutError("slow"))

    assert title == "Connection lost or timed out"
    assert retryable is True


def test_classify_connection_lost_is_retryable():
    title, _reason, retryable = classify_exception(asyncssh.ConnectionLost("drop"))

    assert title == "Connection lost or timed out"
    assert retryable is True


def test_classify_generic_error_falls_through_to_retryable():
    title, _reason, retryable = classify_exception(RuntimeError("boom"))

    assert title == "SSH connection failed"
    assert retryable is True


# --------------------------------------------------------------------------
# Encrypted-key sniffer used by the preflight check in app.ssh.client.
# We construct minimal in-memory OpenSSH-format blobs and verify the
# detector classifies them correctly, WITHOUT needing bcrypt installed
# to run the test.
# --------------------------------------------------------------------------

def _build_openssh_key_blob(cipher_name: bytes) -> bytes:
    """Build a minimal ``openssh-key-v1`` PEM blob with the given cipher.

    Only the prefix up to the cipher_name field needs to be valid for the
    sniffer — it never parses past the cipher name. The rest of the blob
    is filler.
    """
    magic = b"openssh-key-v1\x00"

    def ssh_string(s: bytes) -> bytes:
        return len(s).to_bytes(4, "big") + s

    # cipher_name, kdf, kdf_data, nkeys, pubkey, privkey, mac
    raw = magic + ssh_string(cipher_name) + ssh_string(b"none") + ssh_string(b"") \
        + (1).to_bytes(4, "big") + ssh_string(b"pub-placeholder") \
        + ssh_string(b"priv-placeholder") + b"mac-placeholder"
    body = base64.b64encode(raw).decode("ascii")
    # Wrap to ~70 cols like ssh-keygen does — not strictly required by the
    # sniffer, but keeps the file realistic.
    lines = [body[i:i + 70] for i in range(0, len(body), 70)]
    return ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
            + "\n".join(lines)
            + "\n-----END OPENSSH PRIVATE KEY-----\n").encode("ascii")


@pytest.fixture
def encrypted_openssh_key(tmp_path):
    p = tmp_path / "id_encrypted"
    p.write_bytes(_build_openssh_key_blob(b"aes256-ctr"))
    return str(p)


@pytest.fixture
def unencrypted_openssh_key(tmp_path):
    p = tmp_path / "id_plain"
    p.write_bytes(_build_openssh_key_blob(b"none"))
    return str(p)


@pytest.fixture
def legacy_pem_key(tmp_path):
    """Old-style PEM (PKCS#1/PKCS#8) — not affected by the bcrypt issue."""
    p = tmp_path / "id_rsa"
    p.write_bytes(
        b"-----BEGIN RSA PRIVATE KEY-----\n"
        b"MIIBOQIBAAJBALW0g1q4bH9P+J9P9X9X9X9X9X9X9X9X9X9X9X9X9X9X9X9X9X9X8=\n"
        b"-----END RSA PRIVATE KEY-----\n"
    )
    return str(p)


def test_encrypted_openssh_key_is_detected(encrypted_openssh_key):
    from app.ssh.client import _looks_like_encrypted_openssh_key

    assert _looks_like_encrypted_openssh_key(encrypted_openssh_key) is True


def test_unencrypted_openssh_key_is_not_flagged(unencrypted_openssh_key):
    from app.ssh.client import _looks_like_encrypted_openssh_key

    assert _looks_like_encrypted_openssh_key(unencrypted_openssh_key) is False


def test_legacy_pem_key_is_not_flagged(legacy_pem_key):
    from app.ssh.client import _looks_like_encrypted_openssh_key

    assert _looks_like_encrypted_openssh_key(legacy_pem_key) is False


def test_missing_file_is_not_flagged(tmp_path):
    from app.ssh.client import _looks_like_encrypted_openssh_key

    assert _looks_like_encrypted_openssh_key(str(tmp_path / "nope")) is False


def test_unreadable_file_falls_back_to_false(tmp_path, monkeypatch):
    """If the file exists but cannot be read, the sniffer must not raise —
    it should let asyncssh produce its own error rather than crash the
    preflight check.
    """
    from app.ssh import client as ssh_client

    p = tmp_path / "weird"
    p.write_bytes(_build_openssh_key_blob(b"aes256-ctr"))

    original_open = open

    def blocked_open(path, *args, **kwargs):
        if str(path) == str(p):
            raise OSError("permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", blocked_open)
    assert ssh_client._looks_like_encrypted_openssh_key(str(p)) is False

