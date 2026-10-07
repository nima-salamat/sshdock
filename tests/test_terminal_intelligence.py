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


def test_terminal_native_scrollbar_contract():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "QScrollBar" in src
    assert "self._scrollbar.valueChanged.connect(self._scrollbar_value_changed)" in src
    assert "def _on_scroll_changed" in src
    assert "bridge.report_scroll(maxLine, viewportLine, rows)" in html
    assert "scrollToLine: function (line)" in html
    assert "padding-right: 18px" in html
    assert "sshdock-scroll-gutter" not in html


def test_terminal_html_preserves_whitespace_in_ghost_display():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "replace(/ /g, '\\u00a0')" in src
    assert "ghostSuffix = value.slice(inputModel.length)" in src


def test_terminal_html_preserves_ansi_colors():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "minimumContrastRatio: 1" in src


def test_terminal_session_preserves_color_capable_shell_environment():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert 'TERM=\"xterm-256color\"' in src
    assert 'COLORTERM=\"truecolor\"' in src
    assert "dircolors -b" in src
    assert "alias ls >/dev/null 2>&1 || alias ls='ls --color=auto'" in src
    assert "set -gx TERM xterm-256color" in src
    assert "functions -q ls; or function ls; command ls --color=auto $argv; end;" in src


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
