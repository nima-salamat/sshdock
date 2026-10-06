"""Tests for the redesigned Connect-to-Server dialog.

These are static-source tests (same style as the rest of the suite — see
test_ui_static.py, test_terminal_input.py) so they run headless without a
display server. They verify that:

  * The new ``_SecretLineEdit`` widget exists and exposes the secret API
    we promise (show/hide eye toggle, ``secret()`` / ``set_secret()`` /
    ``clear_secret()``).
  * The ``SecureKeyPreviewDialog`` exists, is screenshot-aware, and wires
    up the platform-specific protection helper.
  * The ``PastePrivateKeyDialog`` exists, hides pasted content by default,
    and returns the pasted text via ``result_text``.
  * The ``ConnectionDialog`` builds the new key row (Browse + Paste +
    Preview + Clear + status chip) and uses ``_SecretLineEdit`` for both
    the password and passphrase fields.
  * The ``_write_pasted_key_to_temp`` helper creates a 0600-permission
    file under the user's cache dir and returns a usable path.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIALOGS_SRC = (ROOT / "app" / "ui" / "dialogs.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# _SecretLineEdit
# --------------------------------------------------------------------------

def test_secret_line_edit_class_exists():
    assert "class _SecretLineEdit(QLineEdit):" in DIALOGS_SRC


def test_secret_line_edit_starts_in_password_mode():
    # The constructor must call setEchoMode(Password) so the field is
    # masked by default — never echo plaintext on first paint.
    assert "setEchoMode(QLineEdit.EchoMode.Password)" in DIALOGS_SRC


def test_secret_line_edit_has_eye_toggle():
    # The eye toggle is the whole point of the redesign for password +
    # passphrase fields. Make sure it exists and is wired up via a real
    # QAction (not a duck-typed wrapper — PySide6's Shiboken type checking
    # rejects those at runtime).
    assert "_eye_action" in DIALOGS_SRC
    assert "QAction" in DIALOGS_SRC
    assert "_toggle_visibility" in DIALOGS_SRC
    assert "fa5s.eye" in DIALOGS_SRC or "fa5s.eye-slash" in DIALOGS_SRC


def test_secret_line_edit_uses_real_qaction_not_duck_typed_wrapper():
    # Regression: the original implementation used a _ToolButtonAction
    # shim with __getattr__ forwarding, which PySide6 rejected with:
    #   TypeError: 'QLineEdit.addAction' called with wrong argument types:
    #   QLineEdit.addAction(_ToolButtonAction, ActionPosition)
    # The fix is to pass a real QAction. Make sure the shim is gone and
    # the action is added directly.
    assert "_ToolButtonAction" not in DIALOGS_SRC, (
        "_ToolButtonAction shim must be removed — it crashes PySide6 at runtime"
    )
    assert "self.addAction(\n            self._eye_action," in DIALOGS_SRC or \
           "self.addAction(self._eye_action," in DIALOGS_SRC


def test_secret_line_edit_action_has_object_name_for_qss():
    # The QSS targets the eye toggle via #eyeToggle — make sure the
    # object name is set on the QAction so the style applies.
    assert 'setObjectName("eyeToggle")' in DIALOGS_SRC


def test_secret_line_edit_auto_re_masks_after_timeout():
    # When the user reveals the password, we must auto-hide it again
    # after a short timeout so an unattended screen doesn't leak it.
    assert "_auto_hide" in DIALOGS_SRC
    assert "QTimer" in DIALOGS_SRC
    assert "setSingleShot(True)" in DIALOGS_SRC
    # 30s is the chosen interval — long enough to read, short enough to
    # be safe.
    assert "30_000" in DIALOGS_SRC or "30000" in DIALOGS_SRC


def test_secret_line_edit_exposes_secret_api():
    assert "def secret(self)" in DIALOGS_SRC
    assert "def set_secret(self" in DIALOGS_SRC
    assert "def clear_secret(self)" in DIALOGS_SRC


# --------------------------------------------------------------------------
# Screenshot protection
# --------------------------------------------------------------------------

def test_screenshot_protection_helper_exists():
    assert "def _enable_screenshot_protection(" in DIALOGS_SRC


def test_screenshot_protection_handles_windows():
    # SetWindowDisplayAffinity is the only reliable Windows API for this.
    # We must at least attempt to call it; the test just verifies the
    # code path exists.
    assert "SetWindowDisplayAffinity" in DIALOGS_SRC
    assert "WDA_EXCLUDEFROMCAPTURE" in DIALOGS_SRC or "0x00000011" in DIALOGS_SRC
    assert "WDA_MONITOR" in DIALOGS_SRC or "0x00000001" in DIALOGS_SRC


def test_screenshot_protection_handles_macos():
    # NSWindowSharingNone is the macOS-native way to opt out of capture.
    assert "NSWindowSharingNone" in DIALOGS_SRC
    assert "setSharingType" in DIALOGS_SRC


def test_screenshot_protection_handles_linux_gracefully():
    # On Linux we cannot reliably prevent screenshots — we must at least
    # tell the user that, rather than silently claiming protection.
    assert "Linux does not support reliable screenshot prevention" in DIALOGS_SRC


# --------------------------------------------------------------------------
# SecureKeyPreviewDialog
# --------------------------------------------------------------------------

def test_secure_key_preview_dialog_class_exists():
    assert "class SecureKeyPreviewDialog(QDialog):" in DIALOGS_SRC


def test_secure_key_preview_dialog_calls_protection_in_show_event():
    # The protection must be applied from showEvent (after the window
    # handle exists), not from __init__ (before).
    assert "def showEvent(self, event)" in DIALOGS_SRC
    assert "_enable_screenshot_protection(self)" in DIALOGS_SRC


def test_secure_key_preview_dialog_has_autoclear_clipboard():
    # "Copy" must auto-clear the clipboard after a short timeout so a
    # pasted-elsewhere key doesn't linger.
    assert "_copy_with_autoclear" in DIALOGS_SRC
    assert "_wipe_clipboard" in DIALOGS_SRC
    assert "_clipboard_timer" in DIALOGS_SRC


def test_secure_key_preview_dialog_wipes_clipboard_on_close():
    assert "def closeEvent(self, event)" in DIALOGS_SRC
    assert "self._wipe_clipboard()" in DIALOGS_SRC


# --------------------------------------------------------------------------
# PastePrivateKeyDialog
# --------------------------------------------------------------------------

def test_paste_private_key_dialog_class_exists():
    assert "class PastePrivateKeyDialog(QDialog):" in DIALOGS_SRC


def test_paste_private_key_dialog_returns_text_via_result_text():
    assert "self.result_text" in DIALOGS_SRC


def test_paste_private_key_dialog_masks_by_default():
    # The "Show characters" checkbox must start unchecked so pasted
    # content is not visible at a glance.
    assert "Show characters (off by default)" in DIALOGS_SRC
    assert "self._show_cb.setChecked(False)" in DIALOGS_SRC


def test_paste_private_key_dialog_validates_key_format():
    # We don't reject unknown formats outright (the user might know
    # better), but we should at least warn.
    assert "BEGIN" in DIALOGS_SRC
    assert "PRIVATE KEY" in DIALOGS_SRC


# --------------------------------------------------------------------------
# ConnectionDialog wiring
# --------------------------------------------------------------------------

def test_connection_dialog_uses_secret_line_edit_for_password_and_passphrase():
    # Both fields must be upgraded from QLineEdit to _SecretLineEdit.
    assert "self.passphrase = _SecretLineEdit(" in DIALOGS_SRC
    assert "self.password = _SecretLineEdit(" in DIALOGS_SRC


def test_connection_dialog_has_paste_button():
    assert "self.paste_key_btn = QPushButton" in DIALOGS_SRC
    assert "def _paste_private_key(self)" in DIALOGS_SRC


def test_connection_dialog_has_preview_button():
    assert "self.preview_key_btn = QPushButton" in DIALOGS_SRC
    assert "def _preview_private_key(self)" in DIALOGS_SRC


def test_connection_dialog_has_clear_key_button():
    assert "self.clear_key_btn = QPushButton" in DIALOGS_SRC
    assert "def _clear_private_key(self)" in DIALOGS_SRC


def test_connection_dialog_has_key_status_chip():
    assert "self.key_status = QLabel" in DIALOGS_SRC
    assert "keyStatusChip" in DIALOGS_SRC
    assert "def _update_key_status(self)" in DIALOGS_SRC


def test_connection_dialog_key_status_chip_has_state_styles():
    # The chip uses QSS [state="…"] selectors for file / pasted / encrypted.
    assert '[state="file"]' in DIALOGS_SRC
    assert '[state="pasted"]' in DIALOGS_SRC
    assert '[state="encrypted"]' in DIALOGS_SRC


def test_connection_dialog_has_hero_banner():
    # Visual polish: gradient banner with icon at the top of the form.
    assert "heroBanner" in DIALOGS_SRC
    assert "qlineargradient" in DIALOGS_SRC


def test_connection_dialog_clear_form_resets_pasted_state():
    # _clear_form must wipe the pasted-key tracking fields so a previous
    # paste doesn't leak into a new connection.
    assert "self._pasted_key_text = None" in DIALOGS_SRC
    assert "self._pasted_key_path = None" in DIALOGS_SRC


def test_connection_dialog_clear_form_calls_update_key_status():
    # After clearing, the chip must refresh so it goes back to "No key set".
    assert "self._update_key_status()" in DIALOGS_SRC


def test_connection_dialog_imports_paste_helper_from_keypaste_module():
    # The Qt-free helper lives in app.ssh.keypaste so it can be unit-tested
    # without a display server. The dialog must import it from there
    # rather than defining it inline.
    assert "from app.ssh.keypaste import" in DIALOGS_SRC
    assert "write_pasted_key_to_temp" in DIALOGS_SRC


# --------------------------------------------------------------------------
# _write_pasted_key_to_temp — actually exercise the helper
# --------------------------------------------------------------------------

def test_write_pasted_key_to_temp_creates_0600_file(tmp_path, monkeypatch):
    # Redirect $HOME so the cache dir lands inside tmp_path — we don't
    # want to pollute the real home directory during tests.
    monkeypatch.setenv("HOME", str(tmp_path))

    # Import from the Qt-free helper module so the test runs headless.
    from app.ssh.keypaste import write_pasted_key_to_temp

    key_text = "-----BEGIN OPENSSH PRIVATE KEY-----\nfoo\nbar\n-----END OPENSSH PRIVATE KEY-----\n"
    path = write_pasted_key_to_temp(key_text, label="test-alias")

    assert path.exists()
    assert path.is_file()
    # File must be 0600 — not world/group readable.
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600 perms, got {oct(mode)}"
    # Content must match what we wrote (with trailing newline ensured).
    content = path.read_text(encoding="utf-8")
    assert content.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert content.endswith("\n")
    # Parent directory must be 0700.
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    assert parent_mode == 0o700, f"expected 0700 parent perms, got {oct(parent_mode)}"


def test_write_pasted_key_to_temp_uses_safe_label(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.ssh.keypaste import write_pasted_key_to_temp

    # Label with shell-unfriendly chars must be sanitised.
    path = write_pasted_key_to_temp("key-data", label="../etc/passwd")
    assert path.exists()
    # No path traversal: the file must live under the pasted-keys dir.
    assert ".." not in path.name
    assert "etc-passwd" in path.name or "passwd" in path.name


# --------------------------------------------------------------------------
# looks_like_private_key — pure-Python heuristic
# --------------------------------------------------------------------------

def test_looks_like_private_key_recognises_openssh_format():
    from app.ssh.keypaste import looks_like_private_key
    assert looks_like_private_key(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfoo\n-----END OPENSSH PRIVATE KEY-----\n"
    ) is True


def test_looks_like_private_key_recognises_rsa_pem():
    from app.ssh.keypaste import looks_like_private_key
    assert looks_like_private_key(
        "-----BEGIN RSA PRIVATE KEY-----\nfoo\n-----END RSA PRIVATE KEY-----\n"
    ) is True


def test_looks_like_private_key_recognises_pkcs8_encrypted():
    from app.ssh.keypaste import looks_like_private_key
    assert looks_like_private_key(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nfoo\n-----END ENCRYPTED PRIVATE KEY-----\n"
    ) is True


def test_looks_like_private_key_rejects_plain_text():
    from app.ssh.keypaste import looks_like_private_key
    assert looks_like_private_key("hello world") is False


def test_looks_like_private_key_rejects_empty():
    from app.ssh.keypaste import looks_like_private_key
    assert looks_like_private_key("") is False
    assert looks_like_private_key("   \n\n  ") is False


# --------------------------------------------------------------------------
# End-to-end: simulate the paste → preview flow without a display
# --------------------------------------------------------------------------

def test_paste_then_preview_flow_returns_correct_text(tmp_path, monkeypatch):
    """Verify the paste→temp-file→preview-read pipeline end-to-end.

    We can't instantiate the actual dialogs without a Qt event loop, but
    we can exercise the data path: paste writes to a temp file, and the
    helper that ``_preview_private_key`` uses to read the key back must
    return the same content.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    from app.ssh.keypaste import write_pasted_key_to_temp

    pasted = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "line1\nline2\nline3\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    path = write_pasted_key_to_temp(pasted, label="flow-test")

    # Simulate what ConnectionDialog._current_key_text does when a pasted
    # key is in play: prefer the in-memory copy, fall back to reading the
    # file path. We test the fallback path here.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        round_tripped = f.read()
    assert round_tripped == pasted


# --------------------------------------------------------------------------
# Regression: existing API surface that main_window.py depends on
# --------------------------------------------------------------------------

def test_connection_dialog_still_exposes_password_and_passphrase_attributes():
    # main_window.py:1594 reads dialog.password.text() and
    # dialog.passphrase.text(). _SecretLineEdit subclasses QLineEdit so
    # .text() still works — but we pin the attribute names here so a
    # future rename doesn't silently break the connect flow.
    assert "self.password" in DIALOGS_SRC
    assert "self.passphrase" in DIALOGS_SRC


def test_connection_dialog_still_exposes_result_profile():
    assert "self.result_profile" in DIALOGS_SRC


def test_connection_dialog_still_exposes_profiles_list_for_main_window_sync():
    # main_window.py:1591 does `self.profiles = list(dialog.profiles)`
    # after the dialog closes, so the attribute must remain a list.
    assert "self.profiles = list(profiles)" in DIALOGS_SRC
