from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_window_has_core_actions():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "Disconnect" in text
    assert "load_containers" in text
    assert "container_action_exec" in text
    assert "start_events" in text


def test_main_window_has_terminal_entrypoint():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "def open_terminal(self):" in text

def test_main_window_has_server_lifecycle_actions():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "def add_server(self):" in text
    assert "async def add_server_and_connect(self" in text
    assert "async def reconnect_current(self):" in text
    assert "async def close_async(self):" in text


def test_overview_refresh_and_icons():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "_overview_refresh" in text
    assert "_auto_refresh_overview" in text
    assert "qtawesome" in text
    assert "overview_processes" in text

def test_main_window_supports_widget_checkboxes():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "def _container_checkbox" in text
    assert "findChild(QCheckBox)" in text
