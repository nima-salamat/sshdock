from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from app.storage.settings import SettingsStore
from app.ui.main_window import MainWindow


class FakeManager(QObject):
    state_changed = Signal(str, str)
    def __init__(self):
        super().__init__()
        self.servers = {}


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    window = MainWindow(FakeManager(), [], SettingsStore())
    assert window.stack.count() >= 8
    assert set(("Overview", "Containers", "Images", "Storage", "Compose", "Events", "Server", "Logs", "Terminal", "Settings")) <= set(window.pages)
    for name in window.pages:
        window.select_page(name)
        assert window.stack.currentWidget() is window.pages[name]
    window.close()
    app.processEvents()
    print("QT UI SMOKE TEST PASSED")
