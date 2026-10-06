from __future__ import annotations

import base64

from app.ssh.hostkeys import append_known_host, fingerprint_from_public_key_blob


def test_fingerprint_has_ssh_sha256_format() -> None:
    fingerprint = fingerprint_from_public_key_blob(b"test-public-key")
    assert fingerprint.startswith("SHA256:")
    encoded = fingerprint.removeprefix("SHA256:")
    assert base64.b64decode(encoded + "==")
    assert "=" not in encoded


def test_append_known_host_uses_plain_host_for_default_port(tmp_path) -> None:
    path = tmp_path / ".ssh" / "known_hosts"
    append_known_host(path, "server.example", 22, "ssh-ed25519 AAAA")
    assert path.read_text(encoding="utf-8") == "server.example ssh-ed25519 AAAA\n"


def test_append_known_host_brackets_non_default_port(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    append_known_host(path, "server.example", 2222, "ssh-rsa BBBB")
    assert path.read_text(encoding="utf-8") == "[server.example]:2222 ssh-rsa BBBB\n"


def test_append_known_host_strips_extra_key_line_whitespace(tmp_path) -> None:
    path = tmp_path / "known_hosts"
    append_known_host(path, "server.example", 22, "  ssh-ed25519 AAAA  ")
    assert path.read_text(encoding="utf-8") == "server.example ssh-ed25519 AAAA\n"
