"""Tests for the alias-resolution fallback in the connect dialog.

When the user types something in the alias field that does NOT match any
SSH config alias, the dialog should:

  1. Use the typed text as a direct hostname / IP.
  2. Parse ``[user@]host[:port]`` so the user can type a full SSH target
     string and have it split across the form fields automatically.
  3. Clear the ``alias`` field on the resulting SSHProfile so asyncssh
     does NOT receive ``config=[...]`` and try to look the text up — it
     just connects directly to the parsed host.
  4. Preserve the original text as the profile display name.

The pure-Python decision logic lives in ``app.ssh.targets`` so it can be
unit-tested headlessly. The dialog wiring is verified via static source
checks (same style as ``test_ui_static.py``).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIALOGS_SRC = (ROOT / "app" / "ui" / "dialogs.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parse_ssh_target — pure-Python target string parser
# --------------------------------------------------------------------------

def test_parse_plain_hostname():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("myserver")
    assert host == "myserver"
    assert user is None
    assert port is None


def test_parse_ipv4_address():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("192.168.1.5")
    assert host == "192.168.1.5"
    assert user is None
    assert port is None


def test_parse_hostname_with_port():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("myserver:2222")
    assert host == "myserver"
    assert user is None
    assert port == 2222


def test_parse_user_at_host():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("root@192.168.1.5")
    assert host == "192.168.1.5"
    assert user == "root"
    assert port is None


def test_parse_user_at_host_with_port():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("root@192.168.1.5:2222")
    assert host == "192.168.1.5"
    assert user == "root"
    assert port == 2222


def test_parse_ipv6_literal():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("[::1]")
    assert host == "::1"
    assert user is None
    assert port is None


def test_parse_ipv6_literal_with_port():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("[::1]:2222")
    assert host == "::1"
    assert user is None
    assert port == 2222


def test_parse_user_at_ipv6_with_port():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("root@[::1]:2222")
    assert host == "::1"
    assert user == "root"
    assert port == 2222


def test_parse_empty_string():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("")
    assert host == ""
    assert user is None
    assert port is None


def test_parse_whitespace_only():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("   ")
    assert host == ""
    assert user is None
    assert port is None


def test_parse_unparseable_falls_back_to_raw_text():
    """If the string doesn't match any known pattern, return it as-is.

    The caller can then decide to use it as a hostname and let asyncssh /
    the OS resolver deal with it.
    """
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("some weird text with spaces")
    # The regex rejects spaces, so this falls back to returning the
    # stripped text as the host.
    assert host == "some weird text with spaces"
    assert user is None
    assert port is None


def test_parse_strips_surrounding_whitespace():
    from app.ssh.targets import parse_ssh_target
    host, user, port = parse_ssh_target("  root@myserver:2222  ")
    assert host == "myserver"
    assert user == "root"
    assert port == 2222


# --------------------------------------------------------------------------
# is_alias_resolvable — check against a list of SSHAlias-like objects
# --------------------------------------------------------------------------

def _alias(name: str, host: str = "10.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(alias=name, host=host, user="", port=22)


def test_is_alias_resolvable_exact_match():
    from app.ssh.targets import is_alias_resolvable
    aliases = [_alias("vps"), _alias("prod")]
    assert is_alias_resolvable("vps", aliases) is True


def test_is_alias_resolvable_case_insensitive():
    from app.ssh.targets import is_alias_resolvable
    aliases = [_alias("VPS")]
    assert is_alias_resolvable("vps", aliases) is True
    assert is_alias_resolvable("Vps", aliases) is True


def test_is_alias_resolvable_no_match():
    from app.ssh.targets import is_alias_resolvable
    aliases = [_alias("vps")]
    assert is_alias_resolvable("staging", aliases) is False


def test_is_alias_resolvable_empty_aliases_list():
    from app.ssh.targets import is_alias_resolvable
    assert is_alias_resolvable("vps", []) is False


def test_is_alias_resolvable_empty_name():
    from app.ssh.targets import is_alias_resolvable
    aliases = [_alias("vps")]
    assert is_alias_resolvable("", aliases) is False
    assert is_alias_resolvable("   ", aliases) is False


# --------------------------------------------------------------------------
# resolve_target — the single decision point used by ConnectionDialog
# --------------------------------------------------------------------------

def test_resolve_target_returns_alias_action_when_resolvable():
    from app.ssh.targets import resolve_target
    aliases = [_alias("vps", host="203.0.113.10")]
    result = resolve_target("vps", aliases)
    assert result["action"] == "alias"
    assert result["alias"] == "vps"
    assert result["display_name"] == "vps"
    # host is empty — asyncssh will resolve it from the SSH config
    assert result["host"] == ""


def test_resolve_target_returns_direct_action_for_ip():
    from app.ssh.targets import resolve_target
    result = resolve_target("192.168.1.5", [])
    assert result["action"] == "direct"
    assert result["host"] == "192.168.1.5"
    assert result["alias"] is None
    assert result["display_name"] == "192.168.1.5"


def test_resolve_target_returns_direct_action_for_user_at_host():
    from app.ssh.targets import resolve_target
    result = resolve_target("root@192.168.1.5", [])
    assert result["action"] == "direct"
    assert result["host"] == "192.168.1.5"
    assert result["user"] == "root"
    assert result["port"] is None
    assert result["alias"] is None
    assert result["display_name"] == "root@192.168.1.5"


def test_resolve_target_returns_direct_action_with_port():
    from app.ssh.targets import resolve_target
    result = resolve_target("root@192.168.1.5:2222", [])
    assert result["action"] == "direct"
    assert result["host"] == "192.168.1.5"
    assert result["user"] == "root"
    assert result["port"] == 2222
    assert result["alias"] is None


def test_resolve_target_prefers_alias_match_over_direct_parse():
    """If the text matches an alias AND looks like a hostname, alias wins.

    This is correct because the user explicitly configured an alias in
    their SSH config — we should trust that over guessing.
    """
    from app.ssh.targets import resolve_target
    aliases = [_alias("myserver", host="10.0.0.5")]
    result = resolve_target("myserver", aliases)
    assert result["action"] == "alias"
    assert result["alias"] == "myserver"


def test_resolve_target_empty_input_returns_empty_action():
    from app.ssh.targets import resolve_target
    result = resolve_target("", [])
    assert result["action"] == "empty"
    assert result["host"] == ""
    assert result["alias"] is None


def test_resolve_target_unparseable_text_still_falls_back_to_direct():
    """Garbage input should not crash — it should be treated as a hostname
    and let the OS resolver / asyncssh deal with it.
    """
    from app.ssh.targets import resolve_target
    result = resolve_target("garbage with spaces", [])
    assert result["action"] == "direct"
    assert result["host"] == "garbage with spaces"
    assert result["alias"] is None


# --------------------------------------------------------------------------
# ConnectionDialog wiring — static source checks
# --------------------------------------------------------------------------

def test_dialog_imports_target_helpers():
    assert "from app.ssh.targets import" in DIALOGS_SRC
    assert "is_alias_resolvable" in DIALOGS_SRC
    assert "parse_ssh_target" in DIALOGS_SRC


def test_dialog_diagnose_has_fallback_to_direct_hostname():
    # _diagnose_alias must contain the fallback branch that parses the
    # text and fills the host field instead of just showing an error.
    assert "parse_ssh_target(name)" in DIALOGS_SRC
    assert "not found in SSH config" in DIALOGS_SRC
    assert "treating it as a direct" in DIALOGS_SRC


def test_dialog_diagnose_clears_alias_field_on_fallback():
    # After falling back, the alias field must be cleared so _accept
    # doesn't pass it to asyncssh as a config-lookup target.
    assert 'self.alias.setCurrentText("")' in DIALOGS_SRC


def test_dialog_accept_uses_is_alias_resolvable():
    # _accept must check whether the typed alias is resolvable before
    # deciding to pass it to asyncssh.
    assert "is_alias_resolvable(alias_text, self.aliases)" in DIALOGS_SRC


def test_dialog_accept_falls_back_to_direct_when_alias_unresolvable():
    # The fallback branch in _accept must:
    #   - parse the alias text
    #   - copy host into the host field
    #   - NOT set the alias on the SSHProfile (so asyncssh connects directly)
    assert "parse_ssh_target(alias_text)" in DIALOGS_SRC
    assert "Connecting directly to" in DIALOGS_SRC
    assert "was not an SSH config alias" in DIALOGS_SRC


def test_dialog_accept_preserves_alias_text_as_display_name():
    # Even when the alias is unresolvable, the original text must be
    # used as the profile display name (the ``name`` field).
    assert "name = alias_text" in DIALOGS_SRC


def test_dialog_accept_does_not_block_on_unresolvable_alias():
    # The old code showed a warning and returned if neither alias nor
    # host was set. The new code must NOT block when alias is set but
    # unresolvable — it should fall back to host=alias_text and continue.
    assert "Enter a Host, or type an SSH alias / IP and click Connect." in DIALOGS_SRC
    # The guard must check `not alias and not host` (not `not alias_text and not host`)
    # because alias_text might be set but unresolvable.
    assert "if not alias and not host:" in DIALOGS_SRC


# --------------------------------------------------------------------------
# End-to-end: simulate the resolve_target decision for common scenarios
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,aliases,expected_action,expected_host,expected_user,expected_port",
    [
        # 1. Real alias → asyncssh resolves from config
        ("vps", [SimpleNamespace(alias="vps")], "alias", "", None, None),
        # 2. Bare IP → direct connection
        ("192.168.1.5", [], "direct", "192.168.1.5", None, None),
        # 3. Bare hostname → direct connection
        ("myserver", [], "direct", "myserver", None, None),
        # 4. user@host → direct, with user parsed
        ("root@192.168.1.5", [], "direct", "192.168.1.5", "root", None),
        # 5. host:port → direct, with port parsed
        ("myserver:2222", [], "direct", "myserver", None, 2222),
        # 6. user@host:port → direct, both parsed
        ("root@192.168.1.5:2222", [], "direct", "192.168.1.5", "root", 2222),
        # 7. IPv6 literal → direct
        ("[::1]", [], "direct", "::1", None, None),
        # 8. IPv6 with port → direct
        ("[::1]:2222", [], "direct", "::1", None, 2222),
        # 9. user@IPv6:port → direct
        ("root@[::1]:2222", [], "direct", "::1", "root", 2222),
        # 10. Alias match takes priority over hostname-looking text
        ("prod-server", [SimpleNamespace(alias="prod-server")], "alias", "", None, None),
    ],
)
def test_resolve_target_scenarios(text, aliases, expected_action, expected_host, expected_user, expected_port):
    from app.ssh.targets import resolve_target
    result = resolve_target(text, aliases)
    assert result["action"] == expected_action, (
        f"Expected action={expected_action!r} for text={text!r}, "
        f"got {result['action']!r}"
    )
    if expected_action == "direct":
        assert result["host"] == expected_host
        assert result["user"] == expected_user
        assert result["port"] == expected_port
        assert result["alias"] is None
        assert result["display_name"] == text
    elif expected_action == "alias":
        assert result["alias"] == text
        assert result["display_name"] == text
