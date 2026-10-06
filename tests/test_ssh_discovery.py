from __future__ import annotations

import os
from pathlib import Path

from app.ssh.discovery import discover_aliases


def test_discover_aliases_parses_common_ssh_options(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        """
Host prod
    HostName 203.0.113.10
    User deploy
    Port 2200
    IdentityFile ~/.ssh/id_ed25519
    IdentityFile ~/.ssh/id_rsa
    IdentitiesOnly yes
    ProxyJump bastion
    ForwardAgent yes
    ServerAliveInterval 45
    ServerAliveCountMax 9
    """
        .strip(),
        encoding="utf-8",
    )

    aliases = discover_aliases([config])

    assert len(aliases) == 1
    alias = aliases[0]
    assert alias.alias == "prod"
    assert alias.host == "203.0.113.10"
    assert alias.user == "deploy"
    assert alias.port == 2200
    assert alias.identity_files == [
        os.path.expanduser("~/.ssh/id_ed25519"),
        os.path.expanduser("~/.ssh/id_rsa"),
    ]
    assert alias.identities_only is True
    assert alias.proxy_jump == "bastion"
    assert alias.forward_agent is True
    assert alias.server_alive_interval == 45
    assert alias.server_alive_count_max == 9


def test_discover_aliases_ignores_wildcard_and_match_blocks(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        """
Host *
    User ignored
Host prod stage
    HostName 192.0.2.10
Match host ignored
    User nobody
        """.strip(),
        encoding="utf-8",
    )

    aliases = discover_aliases([config])
    assert [item.alias for item in aliases] == ["prod", "stage"]


def test_discover_aliases_supports_include_globs(tmp_path: Path) -> None:
    main = tmp_path / "config"
    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    (conf_dir / "10-first.conf").write_text(
        "Host first\n    HostName 192.0.2.1\n", encoding="utf-8"
    )
    (conf_dir / "20-second.conf").write_text(
        "Host second\n    HostName 192.0.2.2\n", encoding="utf-8"
    )
    main.write_text("Include conf.d/*.conf\n", encoding="utf-8")

    aliases = discover_aliases([main])
    assert [item.alias for item in aliases] == ["first", "second"]


def test_discover_aliases_deduplicates_using_first_occurrence(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("Host prod\n    HostName 192.0.2.10\n", encoding="utf-8")
    second.write_text("Host prod\n    HostName 192.0.2.20\n", encoding="utf-8")

    aliases = discover_aliases([first, second])

    assert len(aliases) == 1
    assert aliases[0].host == "192.0.2.10"


def test_discover_aliases_uses_default_port_for_invalid_port(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text(
        "Host prod\n    HostName 192.0.2.10\n    Port nope\n",
        encoding="utf-8",
    )

    alias = discover_aliases([config])[0]
    assert alias.port == 22


def test_discover_aliases_handles_include_cycles(tmp_path: Path) -> None:
    first = tmp_path / "first.conf"
    second = tmp_path / "second.conf"
    first.write_text(
        "Include second.conf\nHost first\n    HostName 192.0.2.1\n",
        encoding="utf-8",
    )
    second.write_text(
        "Include first.conf\nHost second\n    HostName 192.0.2.2\n",
        encoding="utf-8",
    )

    aliases = discover_aliases([first])

    assert [item.alias for item in aliases] == ["first", "second"]
