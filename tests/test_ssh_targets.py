from __future__ import annotations

import pytest

from app.ssh.discovery import SSHAlias
from app.ssh.targets import is_alias_resolvable, parse_ssh_target, resolve_target


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("server", ("server", None, None)),
        ("server:2222", ("server", None, 2222)),
        ("root@server", ("server", "root", None)),
        ("root@server:2222", ("server", "root", 2222)),
        ("[::1]", ("::1", None, None)),
        ("[::1]:2222", ("::1", None, 2222)),
        ("root@[::1]:2222", ("::1", "root", 2222)),
        ("  root@server:2200  ", ("server", "root", 2200)),
    ],
)
def test_parse_ssh_target_supported_forms(value: str, expected: tuple[str, str | None, int | None]) -> None:
    assert parse_ssh_target(value) == expected


@pytest.mark.parametrize("value", ["", "   "])
def test_parse_ssh_target_empty_input(value: str) -> None:
    assert parse_ssh_target(value) == ("", None, None)


def test_parse_ssh_target_unparseable_value_is_preserved() -> None:
    assert parse_ssh_target("not a valid target") == ("not a valid target", None, None)


def test_is_alias_resolvable_is_case_insensitive_and_tolerates_bad_entries() -> None:
    aliases = [SSHAlias(alias="Production"), object(), SSHAlias(alias="staging")]
    assert is_alias_resolvable("production", aliases) is True
    assert is_alias_resolvable(" STAGING ", aliases) is True
    assert is_alias_resolvable("missing", aliases) is False
    assert is_alias_resolvable("   ", aliases) is False


def test_resolve_target_prefers_a_configured_alias() -> None:
    result = resolve_target("production", [SSHAlias(alias="production", host="10.0.0.10")])
    assert result == {
        "action": "alias",
        "host": "",
        "user": None,
        "port": None,
        "display_name": "production",
        "alias": "production",
    }


@pytest.mark.parametrize(
    ("value", "host", "user", "port"),
    [
        ("10.0.0.10", "10.0.0.10", None, None),
        ("deploy@10.0.0.10", "10.0.0.10", "deploy", None),
        ("deploy@10.0.0.10:2222", "10.0.0.10", "deploy", 2222),
        ("[::1]:2200", "::1", None, 2200),
    ],
)
def test_resolve_target_parses_direct_targets(
    value: str, host: str, user: str | None, port: int | None
) -> None:
    result = resolve_target(value, [])
    assert result["action"] == "direct"
    assert result["host"] == host
    assert result["user"] == user
    assert result["port"] == port
    assert result["alias"] is None
    assert result["display_name"] == value


def test_resolve_target_empty_input_has_explicit_empty_action() -> None:
    assert resolve_target("", [])["action"] == "empty"
