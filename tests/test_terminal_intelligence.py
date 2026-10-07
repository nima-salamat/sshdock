from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_terminal_bridge_exposes_prediction_api():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "@Slot(str, str, str, str, result=str)" in src
    assert "def predict_command" in src
    assert "def set_completion_provider" in src
    assert "command_submitted = Signal(str)" in src


def test_terminal_html_has_ghost_completion_and_tab_acceptance():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert 'id="sshdock-ghost"' in src
    assert "requestGhostPrediction" in src
    assert "bridge.predict_command" in src
    assert "ev.key === 'Tab'" in src
    assert "bridge.send_input(ghostSuffix)" in src
    assert "term.onData(function (data)" in src
    assert "bridge.report_command(submitted)" in src


def test_terminal_html_keeps_xterm_scrollbar_visible():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "id="sshdock-scroll-gutter"" in src
    assert "id="sshdock-scroll-thumb"" in src
    assert "scrollbar-width: none" in src
    assert "scrollBarWidth = 0" in src
    assert "pointerdown" in src
    assert "term.scrollToLine" in src
    assert "padding-right: 18px" in src


def test_terminal_html_allows_ghost_acceptance_with_tab_or_arrow_right():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "(ev.key === 'Tab' || ev.key === 'ArrowRight')" in src
    assert "ghostSuffix && inputModelKnown" in src


def test_terminal_session_restores_pty_echo_after_integration():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert 'self.channel.write(b"stty -echo 2>/dev/null\\r")' in src
    assert 'b"stty echo 2>/dev/null; printf \'\\r\\033[K\'\\r"' in src


def test_terminal_html_disables_ghost_completion_for_alternate_screen():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "buf.type === 'alternate'" in src
    assert "inputModelKnown = false" in src


def test_main_window_wires_predictor_and_remote_history():
    src = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "CommandPredictor" in src
    assert "load_remote_history" in src
    assert "set_completion_provider" in src
    assert "_load_terminal_predictor_history" in src
    assert "_terminal_command_submitted" in src
