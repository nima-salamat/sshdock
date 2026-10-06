from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIALOGS_SRC = (ROOT / "app" / "ui" / "dialogs.py").read_text(encoding="utf-8")


def test_connection_dialog_uses_shared_target_helpers() -> None:
    assert "from app.ssh.targets import" in DIALOGS_SRC
    assert "resolve_target" in DIALOGS_SRC
    assert "is_alias_resolvable" in DIALOGS_SRC
    assert "parse_ssh_target" in DIALOGS_SRC


def test_connection_dialog_has_direct_target_fallback() -> None:
    assert "not found in SSH config" in DIALOGS_SRC
    assert "Connecting directly to" in DIALOGS_SRC
    assert "if not alias and not host:" in DIALOGS_SRC


def test_connection_dialog_clears_unresolvable_alias_before_profile_creation() -> None:
    assert 'self.alias.setCurrentText("")' in DIALOGS_SRC
