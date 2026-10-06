from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_terminal_explorer_is_dedicated_and_cwd_coupled():
    text = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "class TerminalFileExplorer" in text
    assert "self.change_dir(data.path)" in text
    assert "Current shell directory" in text
    assert "itemExpanded.connect" in text


def test_terminal_cwd_is_reported_through_osc7():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "__sshdock_cwd" in session
    assert "cwd_changed = Signal(str)" in widget
    assert "registerOscHandler(7" in html


def test_main_window_uses_terminal_explorer_without_placeholder():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "TerminalFileExplorer" in text
    assert "_set_terminal_explorer_visible" in text
    assert "terminal_file_placeholder" not in text
