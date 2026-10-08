from pathlib import Path

from app.terminal.predictor import CommandPredictor

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
    assert "sendInput(ghostSuffix)" in src
    assert "term.onData(function (data)" in src
    assert "bridge.report_command(submitted)" in src




def test_terminal_native_scrollbar_contract():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "QScrollBar" in src
    assert "self._scrollbar.valueChanged.connect(self._scrollbar_value_changed)" in src
    assert "def _on_scroll_changed" in src
    assert "function activeScrollState()" in html
    assert "function syncScrollBar()" in html
    assert "term.onWriteParsed(function(){ syncScrollBar(); })" in html
    assert "term.onScroll(function(){ syncScrollBar(); })" in html
    assert "scrollToLine: function (line)" in html
    assert "padding-right: 18px" in html
    assert "sshdock-scroll-gutter" not in html

def test_terminal_html_preserves_whitespace_in_ghost_display():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "normalizeGhostCandidate" in src
    assert "renderGhostSuffix" in src
    assert "ghost-gap" in src
    assert "ghostSuffix = value.slice(inputModel.length)" in src


def test_predictor_repairs_missing_shell_separator_for_cd_path_predictions():
    predictor = CommandPredictor()
    predictor.ingest(["cd/opt/django", "pwd"], server_id="server")
    assert predictor.suggest("cd", server_id="server") == "cd /opt/django"
    assert predictor.suggest("cd /", server_id="server") == "cd /opt/django"


def test_terminal_html_preserves_ansi_colors():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "minimumContrastRatio: 1" in src
    assert "rgba(148, 163, 184, 0.72)" in src
    assert "ghostAnchorX" in src
    assert "scheduleGhostPosition" in src
    assert "getBoundingClientRect()" in src
    assert "cursorX >= ghostAnchorX" in src






def test_terminal_session_hides_bootstrap_commands_and_clears_screen():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert 'payload = "; ".join(parts)' in src
    assert "install_command(shell_name=shell_name, manage_echo=False)" in src
    assert "033[2J" in src
    assert "033[H" in src
    assert "printf" in src
    assert "\\033[2K" in src and "\\033[2J" in src and "\\033[H" in src
    assert "history -d $HISTCMD" in src
    assert "stty echo 2>/dev/null" in src


def test_terminal_session_preserves_color_capable_shell_environment():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    client = (ROOT / "app/ssh/client.py").read_text(encoding="utf-8")
    assert "TERM=" in session and "xterm-256color" in session
    assert "COLORTERM=" in session and "truecolor" in session
    assert "CLICOLOR=1" in session
    assert "dircolors -b" in session
    assert "ls --color=auto" in session
    assert "grep --color=auto" in session
    assert "PS1=" in session
    assert "033[1;32m" in session
    assert "033[1;36m" in session
    assert "033[1;33m" in session
    assert "033[0;90m" in session
    assert "033[1;34m" in session
    assert "PROMPT=" in session
    assert "%F{green}" in session
    assert "set -gx TERM xterm-256color" in session
    assert '"TERM": "xterm-256color"' in client
    assert '"COLORTERM": "truecolor"' in client
    assert '"CLICOLOR": "1"' in client

def test_terminal_html_allows_ghost_acceptance_with_tab_or_arrow_right():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "(ev.key === 'Tab' || ev.key === 'ArrowRight')" in src
    assert "sendInput(ghostSuffix)" in src
    assert "ghostSuffix && inputModelKnown" in src





def test_terminal_session_restores_pty_echo_after_integration():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    client = (ROOT / "app/ssh/client.py").read_text(encoding="utf-8")
    assert "term_modes={asyncssh.PTY_ECHO: 0}" in client
    assert "stty echo 2>/dev/null" in src
    assert "history -d $HISTCMD" in src
    assert "033[2J" in src

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
