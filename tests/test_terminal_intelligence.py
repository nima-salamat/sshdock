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
    assert "ghostSuffix = ghostCandidate.slice(prefix.length)" in src
    assert "ghostSuffix = value.slice(requestedPrefix.length)" in src


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

def test_terminal_html_uses_arrow_right_only_for_inline_ai_acceptance():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "ev.key === 'ArrowRight' && ghostSuffix && inputModelKnown" in src
    assert "(ev.key === 'Tab' || ev.key === 'ArrowRight')" not in src
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



def test_predictor_returns_five_context_ranked_suggestions_including_empty_prompt():
    predictor = CommandPredictor()
    predictor.ingest(
        [
            "docker ps",
            "docker images",
            "docker ps",
            "docker compose ps",
            "docker logs web",
            "docker inspect web",
            "docker stats",
        ],
        server_id="server",
        cwd="/srv/app",
        shell="/bin/bash",
    )

    prefix_results = predictor.suggest(
        "docker",
        server_id="server",
        cwd="/srv/app",
        shell="bash",
        limit=5,
    )
    empty_results = predictor.suggest(
        "",
        server_id="server",
        cwd="/srv/app",
        shell="bash",
        limit=5,
    )

    assert isinstance(prefix_results, list)
    assert 1 <= len(prefix_results) <= 5
    assert all(command.startswith("docker") for command in prefix_results)
    assert isinstance(empty_results, list)
    assert 1 <= len(empty_results) <= 5
    assert len({command.casefold() for command in empty_results}) == len(empty_results)


def test_suggestion_list_is_deduplicated_and_bounded():
    from app.terminal.predictor import normalize_suggestions

    result = normalize_suggestions(
        ["git status", "git log", "GIT STATUS", "git diff", "git pull", "git push", "git branch"],
        limit=5,
    )

    assert result == ["git status", "git log", "git diff", "git pull", "git push"]


def test_terminal_popup_is_keyboard_operable_and_uses_predictor():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")

    assert 'id="sshdock-suggestions"' in html
    assert "bridge.suggest_commands" in html
    assert "ev.code === 'Space'" in html
    assert "suggestionsVisible && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')" in html
    assert "suggestionsVisible && ev.key === 'Enter'" in html
    assert "suggestionsVisible && ev.key === 'Tab'" in html
    assert "hideSuggestions();" in html
    assert "function acceptSuggestion(index)" in html
    assert "def set_suggestions_provider" in widget
    assert "def _predict_terminal_commands" in main
    assert "limit=5" in main


def test_terminal_ai_rich_bridge_settings_and_feedback_are_wired():
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    settings = (ROOT / "app/storage/settings.py").read_text(encoding="utf-8")
    dialog = (ROOT / "app/ui/dialogs.py").read_text(encoding="utf-8")

    assert "def suggest_command_details" in widget
    assert "suggestion_accepted = Signal(str)" in widget
    assert "def set_suggestions_detail_provider" in widget
    assert "def set_terminal_ai_settings" in widget
    assert "def _on_suggestion_accepted" in widget
    assert "_predict_terminal_command_details" in main
    assert "record_suggestion_acceptance" in main
    assert "terminal_ai_shadow_enabled" in main
    assert "terminal_ai_persist_learning" in main
    assert "suggest_command_details" in html
    assert "report_suggestion_accepted" in html
    assert "setTerminalAISettings" in html
    assert "ai-learning" in html
    assert "terminal_ai_shadow_enabled" in settings
    assert "Terminal intelligence" in dialog


def test_terminal_ai_css_exposes_shadow_and_confidence_metadata():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "suggestion-confidence" in html
    assert "suggestion-source" in html
    assert "AI command suggestions" in html
    assert "sshdock-ai-breathe" in html
    assert "terminalAI.shadowEnabled" in html


def test_main_window_applies_terminal_ai_preferences_to_existing_terminals():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "terminal_ai_suggestions_enabled" in main
    assert "terminal_ai_learn_history" in main
    assert "set_terminal_ai_settings" in main
    assert "clear_terminal_learning_state" in main

def test_open_suggestion_palette_refreshes_when_terminal_context_changes():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("function requestGhostPrediction(")
    end = html.index("function setInputModel(", start)
    prediction = html[start:end]

    # The palette refreshes itself while open, and inline shadow ranking is
    # skipped so one keystroke does not trigger duplicate Python ranking calls.
    assert "if (suggestionsVisible) {" in prediction
    assert "requestSuggestions(false);" in prediction
    assert "hideGhost();" in prediction
    assert prediction.index("if (suggestionsVisible) {") < prediction.index(
        "if (ghostPredictionTimer !== null)"
    )
    assert "if (inputModelKnown) requestGhostPrediction(true);" in html

def test_live_terminal_cwd_is_pushed_into_prediction_context():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    start = main.index("def _terminal_cwd_changed(")
    end = main.index("def _set_terminal_status(", start)
    handler = main[start:end]

    assert "term.set_completion_context(" in handler
    assert "cwd=normalized" in handler
    assert "shell=self.terminal_shells.get(sid, \"\")" in handler
    assert handler.index("term.set_completion_context(") < handler.index("if sid != current:")

def test_multiline_paste_is_not_forwarded_to_learner_before_submission():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("function observeInput(")
    end = html.index("/* Printable insertion is only unambiguous", start)
    paste_path = html[start:end]

    assert "value.length > 1 && /[\\r\\n]/.test(value)" in paste_path
    assert "bridge.report_command(value)" not in paste_path
    assert "Never report multiline paste as a" in paste_path
    assert "inputModelKnown = pasteEndsAtCommandBoundary &&" in paste_path
    assert "pasteContainsShellBlock" in paste_path
    assert "pasteContainsHereDoc" in paste_path

def test_tab_keeps_native_shell_completion_even_with_ai_popup_open():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("term.attachCustomKeyEventHandler(function (ev)")
    end = html.index("window.sshdockTerm = {", start)
    keys = html[start:end]

    tab_branch = keys.index("suggestionsVisible && ev.key === 'Tab'")
    enter_branch = keys.index("suggestionsVisible && ev.key === 'Enter'")
    ghost_branch = keys.index("ev.key === 'ArrowRight' && ghostSuffix")
    assert tab_branch < ghost_branch
    assert enter_branch < tab_branch
    assert "hideSuggestions();" in keys[tab_branch:ghost_branch]
    assert "return true;" in keys[tab_branch:ghost_branch]
    assert "sendInput(ghostSuffix);" in keys[ghost_branch:]

def test_terminal_ai_prediction_is_debounced_and_ghost_layout_is_frame_batched():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("function requestGhostPrediction(")
    end = html.index("function setInputModel(", start)
    prediction = html[start:end]

    assert "GHOST_PREDICTION_DEBOUNCE_MS = 180" in html
    assert "setTimeout(function ()" in prediction
    assert "cachedMatches" in prediction
    assert "requestedPrefix !== inputModel" in prediction
    assert "SUGGESTION_REFRESH_DEBOUNCE_MS = 180" in html
    assert "ghostPositionFramePending" in html
    assert "if (!ghostSuffix || !inputModelKnown || ghostPositionFramePending) return;" in html


def test_terminal_context_changes_force_a_fresh_prediction():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("setCompletionContext: function (data)")
    end = html.index("setTheme: function", start)
    context_setter = html[start:end]

    assert "completionContext.cwd = String(value.cwd || '/');" in context_setter
    assert "completionContext.shell = String(value.shell || '');" in context_setter
    assert "requestGhostPrediction(true)" in context_setter

def test_terminal_history_learning_yields_between_batches_and_saves_off_thread():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    predictor = (ROOT / "app/terminal/predictor.py").read_text(encoding="utf-8")

    start = main.index("async def _load_terminal_predictor_history(")
    end = main.index("def _terminal_command_submitted(", start)
    importer = main[start:end]
    assert "prepare_snapshot_ingest" in importer
    assert "batch_size = 32" in importer
    assert "await asyncio.sleep(0.005)" in importer
    assert "finish_snapshot_ingest" in importer

    assert "await asyncio.to_thread(" in predictor
    assert "parse_history_output, getattr(result, \"stdout\", \"\") or \"\"" in predictor
    assert "await asyncio.to_thread(self.store.save_terminal_learning_state, state)" in main




def test_terminal_session_reports_and_strips_shell_exit_status():
    from app.terminal.session import TerminalSession

    session = TerminalSession(ssh=None, title="test")
    results = []
    session.command_result.connect(results.append)

    visible = session._extract_cwd(
        b"command output\x1b]777;sshdock-result;7\x07prompt"
    )

    assert visible == b"command outputprompt"
    assert results == [7]


def test_terminal_session_handles_split_exit_status_marker():
    from app.terminal.session import TerminalSession

    session = TerminalSession(ssh=None, title="test")
    results = []
    session.command_result.connect(results.append)

    first = session._extract_cwd(b"output\x1b]777;sshdock-result;1")
    second = session._extract_cwd(b"\x07prompt")

    assert first == b"output"
    assert second == b"prompt"
    assert results == [1]


def test_shell_integration_emits_exit_status_for_supported_shells():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")

    assert 'self.result_reporting_enabled = shell_name in {"bash", "zsh", "fish"}' in src
    assert src.count("sshdock-result;%s") >= 3
    assert "precmd_functions=(__sshdock_cwd" in src
    assert "set -l __sshdock_status $status" in src


def test_terminal_learning_waits_for_real_shell_result():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    submitted = main.split("def _terminal_command_submitted(", 1)[1].split(
        "def _terminal_command_result(", 1
    )[0]
    result = main.split("def _terminal_command_result(", 1)[1].split(
        "def _terminal_suggestion_accepted(", 1
    )[0]

    assert "_terminal_pending_command_results[sid]" in submitted
    assert "_command_predictor.add_command(" not in submitted
    assert "record_command_result(" in result
    assert "success=int(exit_code) == 0" in result
    assert "session.command_result.connect(" in main



def test_multiline_paste_is_never_reported_as_a_submitted_command():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("A paste can arrive as one onData payload containing multiple lines.")
    end = html.index("/* Printable insertion is only unambiguous", start)
    paste_handler = html[start:end]

    assert "Never report multiline paste as a" in paste_handler
    assert "bridge.report_command" not in paste_handler
    assert "inputModel = ''" in paste_handler
    assert "pasteEndsAtCommandBoundary" in paste_handler


def test_cancelled_input_is_cleared_without_command_submission():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    start = html.index("if (value.indexOf('\\x03') >= 0 || value.indexOf('\\x04') >= 0)")
    end = html.index("if (value.indexOf('\\x17')", start)
    interrupt_handler = html[start:end]

    assert "inputModel = ''" in interrupt_handler
    assert "hideGhost();" in interrupt_handler
    assert "bridge.report_command" not in interrupt_handler



def test_ai_suggestion_feedback_is_deferred_until_real_command_result():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    selected = main.split("def _terminal_suggestion_accepted(", 1)[1].split(
        "def _terminal_suggestion_cancelled(", 1
    )[0]
    result = main.split("def _terminal_command_result(", 1)[1].split(
        "def _terminal_suggestion_accepted(", 1
    )[0]

    assert "self._terminal_selected_suggestions[sid] = parsed[0]" in selected
    assert "record_suggestion_acceptance(" not in selected
    assert "if was_suggested:" in result
    assert "record_suggestion_acceptance(" in result
    assert "record_command_result(" in result


def test_cancelling_input_clears_unsubmitted_suggestion_feedback():
    main = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")

    assert "def _terminal_suggestion_cancelled(" in main
    assert "self._terminal_selected_suggestions.pop(sid, None)" in main
    assert "suggestion_cancelled = Signal()" in widget
    assert "def report_suggestion_cancelled(" in widget
    assert "def _on_suggestion_cancelled(" in widget
    assert html.count("bridge.report_suggestion_cancelled()") >= 3
