from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from app.core.models import SSHProfile
from app.ssh.client import (
    AuthMaterial,
    CommandResult,
    SSHServerConnection,
    _looks_like_encrypted_openssh_key,
)


def _private_key_blob(cipher: bytes) -> str:
    magic = b"openssh-key-v1\0"
    encoded = (
        magic
        + len(cipher).to_bytes(4, "big")
        + cipher
        + len(b"none").to_bytes(4, "big")
        + b"none"
        + len(b"").to_bytes(4, "big")
        + b""
    )
    return (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + base64.b64encode(encoded).decode("ascii")
        + "\n-----END OPENSSH PRIVATE KEY-----\n"
    )


def test_command_result_exposes_asyncssh_style_aliases() -> None:
    result = CommandResult("echo ok", "ok", "", 3)
    assert result.returncode == 3
    assert result.exit_status == 3


def test_worker_shell_limit_is_clamped() -> None:
    profile = SSHProfile(id="1", name="test")
    connection = SSHServerConnection(profile, credentials=SimpleNamespace(get=lambda *_: None))

    assert connection.set_worker_shell_count_limit(0) == 1
    assert connection.worker_shell_count == 1
    assert connection.set_worker_shell_count_limit(99) == 32
    assert connection.worker_shell_count == 32


def test_connect_kwargs_uses_explicit_auth_material() -> None:
    profile = SSHProfile(
        id="1",
        name="test",
        host="server.example",
        username="deploy",
        port=2222,
        private_key="~/.ssh/id_ed25519",
        connect_timeout=7,
        keepalive=11,
    )
    connection = SSHServerConnection(
        profile,
        credentials=SimpleNamespace(get=lambda *_: None),
        auth=AuthMaterial(password="secret", passphrase="passphrase"),
    )

    kwargs = connection._connect_kwargs()

    assert kwargs["username"] == "deploy"
    assert kwargs["port"] == 2222
    assert kwargs["password"] == "secret"
    assert kwargs["passphrase"] == "passphrase"
    assert kwargs["client_keys"]
    assert kwargs["connect_timeout"] == 7
    assert kwargs["keepalive_interval"] == 11
    assert kwargs["server_alive_count_max"] == 3
    assert kwargs["config"] is None


def test_encrypted_openssh_key_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "encrypted"
    path.write_text(_private_key_blob(b"aes256-ctr"), encoding="utf-8")
    assert _looks_like_encrypted_openssh_key(str(path)) is True


def test_unencrypted_openssh_key_is_not_detected_as_encrypted(tmp_path: Path) -> None:
    path = tmp_path / "plain"
    path.write_text(_private_key_blob(b"none"), encoding="utf-8")
    assert _looks_like_encrypted_openssh_key(str(path)) is False


def test_non_openssh_key_and_missing_file_return_false(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("not a private key", encoding="utf-8")
    assert _looks_like_encrypted_openssh_key(str(plain)) is False
    assert _looks_like_encrypted_openssh_key(str(tmp_path / "missing")) is False
