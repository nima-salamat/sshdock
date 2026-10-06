from __future__ import annotations

import asyncio
import sys
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMessageBox
from app.core.logging import configure_logging
from app.ssh.discovery import discover_aliases
from app.ssh.session_manager import SessionManager
from app.ssh.client import AuthMaterial
from app.storage.settings import SettingsStore
from app.storage.secure import CredentialStore
from app.ui.dialogs import ConnectionDialog
from app.ui.main_window import MainWindow
from app.ui.styles import DARK

async def async_main(app: QApplication) -> None:
    configure_logging()
    app.setStyleSheet(DARK)
    # Faster tooltips app-wide (Qt still has a small initial delay; duration is show time)
    try:
        from PySide6.QtWidgets import QToolTip
        from PySide6.QtGui import QFont
        # Style tip appearance; delay is partially controlled per-widget via setToolTipDuration
        app.setStyleSheet(DARK + "\nQToolTip { padding:6px 8px; border:1px solid #2a313a; background:#15191f; color:#e9edf2; }")
    except Exception:
        pass

    store = SettingsStore()
    profiles = store.load_profiles()
    aliases = discover_aliases()
    dialog = ConnectionDialog(profiles, aliases, store=store)
    # Multi-monitor: center on cursor screen / clamp above taskbar (also done in showEvent)
    from app.ui.dialogs import place_dialog_on_best_screen
    place_dialog_on_best_screen(dialog)

    if dialog.exec() != ConnectionDialog.DialogCode.Accepted or not dialog.result_profile:
        return
    profile = dialog.result_profile
    if profile.persist_secrets:
        creds = CredentialStore()
        if dialog.password.text():
            creds.set(profile.id, "password", dialog.password.text())
        if dialog.passphrase.text():
            creds.set(profile.id, "passphrase", dialog.passphrase.text())
    # Reload profiles after possible deletions in the dialog
    profiles = store.load_profiles()
    if profile.id not in [p.id for p in profiles]:
        profiles.append(profile)
        store.save_profiles(profiles)

    prefs = store.load_ui_prefs()
    manager = SessionManager(
        worker_shell_count=int(prefs.get("worker_shell_count", 3)),
        remote_agent_enabled=bool(prefs.get("remote_agent_enabled", False)),
    )
    window = MainWindow(manager, profiles, store)
    window._shutdown_event = asyncio.Event()

    # Open main window on the same screen / area as the connect dialog
    dlg_screen = dialog.screen()
    target = dlg_screen if dlg_screen is not None else app.primaryScreen()
    if target is not None:
        ag = target.availableGeometry()
        w = min(1440, max(1100, ag.width() - 48))
        h = min(920, max(720, ag.height() - 48))
        window.resize(w, h)
        # Center on that screen
        geo = window.frameGeometry()
        geo.moveCenter(ag.center())
        window.move(geo.topLeft())
    else:
        window.resize(1280, 840)

    window.show()
    auth = AuthMaterial(
        password=dialog.password.text() or None,
        passphrase=dialog.passphrase.text() or None,
    )
    try:
        await window.add_server_and_connect(profile, auth)
    except Exception as exc:
        from app.ui.dialogs import ErrorDialog
        ErrorDialog("SSH connection failed", "Could not establish the SSH session.", str(exc), window).exec()
    # MainWindow.closeEvent performs async shutdown while the qasync loop is alive.
    await window._shutdown_event.wait()
