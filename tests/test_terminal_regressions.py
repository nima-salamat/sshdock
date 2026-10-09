from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_terminal_starts_the_real_user_shell_before_integration():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "self.channel = await self.ssh.open_shell()" in src
    assert "--noprofile" not in src
    assert "--rcfile" not in src
    assert "ZDOTDIR=" not in src
    assert "await self._install_shell_integration()" in src


def test_terminal_integration_preserves_native_docker_command():
    src = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "mdocker() {" in src
    assert "docker() {" in src
    assert "command docker" in src
    assert "m-docker='mdocker'" in src
    assert "magic-docker='mdocker'" in src
def test_terminal_explorer_restores_button_factory():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "QFileDialog, QApplication," in src
    assert "def _button(self, icon_name: str, tooltip: str, callback):" in src
    assert "self.search_btn = self._button(" in src

def test_terminal_explorer_buttons_schedule_async_callbacks():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "import inspect" in src
    assert "result = callback()" in src
    assert "if inspect.isawaitable(result):" in src
    assert "self._spawn(result)" in src

def test_terminal_panel_toggles_show_active_state():
    src = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    styles = (ROOT / "app/ui/styles.py").read_text(encoding="utf-8")
    assert 'files_btn.setObjectName("terminalPanelToggle")' in src
    assert 'sessions_btn.setObjectName("terminalPanelToggle")' in src
    assert "files_btn.setCheckable(True)" in src
    assert "sessions_btn.setCheckable(True)" in src
    assert "files_btn.setChecked(False)" in src
    assert "sessions_btn.setChecked(True)" in src
    assert "self.terminal_files_toggle.setChecked(visible)" in src
    assert "btn.setChecked(visible)" in src
    assert "QPushButton#terminalPanelToggle:checked" in styles


def test_terminal_webengine_fit_api_is_real_and_syncs_overlay():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "fit: function ()" in src
    assert "fitAddon.fit();" in src
    assert "window._sshdockSync()" in src



def test_terminal_webengine_waits_for_webchannel_ready_before_showing_view():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "self._view.show()" in src
    assert "def _on_page_ready(self)" in src
    assert "if not self._ready or not self._pending_writes:" in src
    assert "QTimer.singleShot(80, self._flush_pending)" not in src
    assert "self._view.show()" in src[src.index("def _on_page_ready"):]

def test_virtual_tables_follow_xterm_alternate_screen():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "activeBuffer.type === 'alternate'" in src
    assert "b.style.display = 'none';" in src
    assert "term.buffer.onBufferChange" in src





def test_terminal_bootstrap_uses_non_echoing_pty_and_cleans_bash_history():
    client = (ROOT / "app/ssh/client.py").read_text(encoding="utf-8")
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "term_modes={asyncssh.PTY_ECHO: 0}" in client
    assert "\\033[2K" in session and "\\033[2J" in session and "\\033[H" in session
    assert "history -d $HISTCMD" in session
    assert "install_command(shell_name=shell_name, manage_echo=False)" in session
    assert "stty echo 2>/dev/null" in session
    assert 'stty -echo 2>/dev/null\\r' not in session

def test_terminal_magic_supports_native_docker_magic_form_for_posix_shells():
    docker = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "docker() {" in docker
    assert "--magic|--mdocker)" in docker
    assert "__sshdock_magic" in docker

def test_terminal_magic_supports_native_docker_magic_form_for_fish():
    docker = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "function docker" in docker
    assert "= --magic" in docker
    assert "= --mdocker" in docker
    assert "command docker $argv" in docker


def test_magic_table_supports_equal_columns_resize_and_copy_context():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "table-layout: fixed" in src
    assert "const equalWidth" in src
    assert "<colgroup>" in src
    assert "getBoundingClientRect().width" in src
    assert "data-col" in src
    assert "contextmenu" in src
    assert "Copy cell" in src
    assert "Copy row" in src
    assert "bridge.copy_text" in src
    assert "def copy_text(self, text: str)" in widget
    assert "copy_requested = Signal(str)" in widget


def test_terminal_magic_has_python_and_browser_delivery_paths():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    window = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "_OSC_MAGIC_RE = re.compile" in session
    assert "magic_command = Signal(str)" in widget
    assert "def report_magic(self, payload: str)" in widget
    assert "def _on_magic_command(self, payload: str)" in widget
    assert "registerOscHandler(777, reportMagicFromOsc)" in html
    assert "bridge.report_magic(payload)" in html
    assert 'name="mdocker-xterm"' in window



def test_mdocker_bootstrap_is_prepared_outside_the_interactive_pty():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    docker = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "await self._prepare_shell_integration()" in src
    assert "async def _prepare_shell_integration(self) -> None:" in src
    assert "prepare_command()" in src
    assert "install_command(shell_name=shell_name, manage_echo=False)" in src
    assert 'SH_SCRIPT_PATH = "$HOME/.cache/sshdock/docker.sh"' in docker
    assert 'umask 077' in docker
    assert 'chmod 700 "$HOME/.cache/sshdock"' in docker
    assert 'chmod 600 "$HOME/.cache/sshdock/docker.sh"' in docker

def test_terminal_close_has_no_obsolete_bootstrap_cleanup_path():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "_cleanup_bootstrap" not in src
    assert "_bootstrap_path" not in src



def test_terminal_has_dedicated_scrollbar_gutter():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "padding: 4px 16px 4px 4px" in src
    assert "overflow-y: scroll !important" in src
    assert ".xterm .xterm-viewport::-webkit-scrollbar" in src
    assert "QScrollBar#terminalScrollBar" in widget
    assert "self._scrollbar.hide()" in widget
    assert "bar.setVisible(int(max_line) > 0)" in widget


def test_magic_table_uses_original_ansi_renderer_on_pyte_fallback():
    window = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    ansi = (ROOT / "app/terminal/ansi_table.py").read_text(encoding="utf-8")
    assert "from app.terminal.ansi_table import format_ansi_table" in window
    assert "format_ansi_table(" in window
    assert "_GREEN" in ansi


def test_magic_table_has_prompt_fallback_and_scrollbar_sync_without_tables():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "const startAbs = Math.max(0, promptAbs - RESERVE);" in src
    assert "block._anchorPromptAbs = promptAbs;" in src
    assert "if (!term._sshdockScrollBarHook)" in src
    assert "term.onWriteParsed(function(){ syncScrollBar(); })" in src
    assert "term.onRender(function(){" in src
    assert "syncScrollBar();" in src
    assert "const anchor = markerLine >= 0" in src


def test_terminal_scrollbar_uses_xterm_buffer_position():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "function activeScrollState()" in src
    assert "normal && normal.baseY" in src
    assert "normal && normal.length" in src
    assert "term.scrollToLine" in src
    assert "function syncScrollBar()" in src
    assert "bridge.report_scroll(state.maxLine, state.viewportLine, state.rows)" in src

def test_table_wheel_is_contained_without_breaking_native_scrollbars():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "Never bubble them to xterm" in src
    assert "ev.stopPropagation();" in src
    assert "Native browser scrolling remains" in src
    assert "passive:true" in src


def test_process_sigint_uses_normal_graceful_shutdown_path():
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "import signal" in src
    assert "signal.SIGINT" in src
    assert 'getattr(signal, "SIGTERM", None)' in src
    assert "main_window.close()" in src
    assert 'widget.__class__.__name__ == "ConnectionDialog"' in src
    assert "restore_signal_handlers()" in src


def test_terminal_explorer_has_unified_upload_and_creation_actions():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "class _UploadSelectionDialog(QDialog):" in src
    assert 'add_files.clicked.connect(self._add_files)' in src
    assert 'add_folder.clicked.connect(self._add_folder)' in src
    assert "self.selected_paths" in src
    assert 'self.new_folder_btn = self._button("fa5s.folder-plus"' in src
    assert 'self.new_file_btn = self._button("fa5s.file"' in src
    assert 'self.upload_btn = self._button(' in src
    assert '"fa5s.upload", "Upload files and folders"' in src
    assert 'self.download_btn = self._button(' in src
    assert '"fa5s.download", "Download selected files/folders"' in src
    assert "self.upload_folder_btn" not in src
    assert 'self.refresh_btn = self._button("fa5s.sync-alt", "Reload", self.refresh)' in src
    assert 'self.status.setText(f"{visible} items · shell cwd")' in src
    assert "self.count_label" not in src
    assert "async def new_folder(self):" in src
    assert "async def new_file(self):" in src
    assert "await self.backend.mkdir(path)" in src
    assert 'await self.backend.write_text(path, "")' in src
    assert "self._spawn(self._open_file(Entry(" in src
def test_terminal_creation_is_serialized_and_titles_are_unique():
    src = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "self._terminal_creation_in_progress = False" in src
    assert "if self._terminal_creation_in_progress:" in src
    assert "self._terminal_creation_in_progress = True" in src
    assert "self._terminal_creation_in_progress = False" in src
    assert "def _unique_terminal_title(self, base_title: str) -> str:" in src
    assert 'return f"{base} {suffix}"' in src
    assert 'and not getattr(self, "_terminal_creation_in_progress", False)' in src


def test_terminal_explorer_uses_list_parent_and_collapsible_search():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert 'fa5s.search' in src
    assert 'self.search_btn.setCheckable(True)' in src
    assert 'self.search.setVisible(False)' in src
    assert 'self.search_btn.isChecked()' in src
    assert 'self.search.clear()' in src
    assert 'QTreeWidgetItem([".."])' in src
    assert '"parent": True' in src
    assert 'terminalExplorerPathLabel' in src
    assert 'self.path_edit' not in src
    assert 'self.up_btn' not in src


def test_terminal_explorer_search_matches_path_and_type():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert 'str(data.path or "")' in src
    assert '"directory" if data.is_dir else "file"' in src
    assert 'ft.key' in src


def test_terminal_explorer_has_transfer_progress_delete_and_media_controls():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "class _TransferDialog(QDialog):" in src
    assert "self.progress = QProgressBar()" in src
    assert "ETA" in src
    assert "set_cancel_callback" in src
    assert "async def delete_selected(self):" in src
    assert "Delete these selected items?" in src
    assert "self.delete_btn = self._button(" in src
    assert "IMAGE_EXTS" in src
    assert "VIDEO_EXTS" in src
    assert "AUDIO_EXTS" in src
    assert 'qta.icon("fa5s.times", color=remove_color)' in src
    assert "def open_media(self, path, *, is_video, is_audio=False):" in src
    assert "Audio is still downloading" in src
    assert '"Source"' in src
    assert 'save_media_btn = QPushButton("Save As")' in src
    assert 'save_media_btn.setEnabled(False)' in src
    assert "shutil.copy2(temp_path, destination)" in src
    assert "task.cancel()" in src


def test_server_file_backend_exposes_sftp_progress_hooks_for_transfers():
    src = (ROOT / "app/files/browser.py").read_text(encoding="utf-8")
    assert "async def download(self, path: str, local: str, progress_handler=None):" in src
    assert "async def upload(self, local: str, remote: str, progress_handler=None):" in src
    assert "progress_handler=progress_handler" in src


def test_terminal_explorer_queue_rows_have_remove_buttons_and_file_type_icons():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "class _QueueRowWidget(QWidget):" in src
    assert 'remove_btn.setToolTip("Remove from list")' in src
    assert 'remove_btn.setIcon(_queue_icon("fa5s.times"))' in src
    assert "_remove_item" in src
    assert '_queue_icon(ent.name, is_dir=ent.is_dir)' in src


def test_terminal_explorer_supports_archive_extract_via_visible_terminal():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    window = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    types = (ROOT / "app/files/file_types.py").read_text(encoding="utf-8")
    assert "class _ExtractDialog(QDialog):" in src
    assert "async def extract_archive(self, ent: Entry):" in src
    assert 'classify(data.name).key == "archive"' in src
    assert "async def _detect_extractor" in src
    assert "Extractor not installed" in src
    assert 'run_command=self._run_terminal_explorer_command' in window
    assert 'session.write(payload)' in window
    assert '.tgz' in types and '.tbz2' in types and '.txz' in types
    assert '.lz4' in types and '.lzma' in types
    assert '*.7z|*.cab|*.iso|*.jar|*.war|*.ear|*.apk' in src
    assert 'elif base == "xz":' in src
    assert 'elif [ "{kind}" = "xz" ]; then' in src


def test_terminal_explorer_fixes_runtime_imports_used_by_media_and_transfer_features():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert "import asyncio" in src
    assert "import shutil" in src
    assert "import shlex" in src


def test_terminal_webchannel_report_cwd_accepts_js_string_argument():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "    @Slot(str)\n    def report_cwd(self, path: str) -> None:" in src
    assert "    @Slot()\n    def report_cwd(self, path: str) -> None:" not in src


def test_terminal_explorer_audio_video_file_types_use_dedicated_icons():
    src = (ROOT / "app/files/file_types.py").read_text(encoding="utf-8")
    explorer = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert 'FileType("video", "fa5s.file-video"' in src
    assert 'FileType("audio", "fa5s.file-audio"' in src
    assert '"video":' in src and '"audio":' in src
    assert '".flac"' in src and '".opus"' in src
    assert '".mkv"' in explorer and '".mp3"' in explorer


def test_terminal_explorer_remove_action_uses_real_times_icon():
    src = (ROOT / "app/files/terminal_explorer.py").read_text(encoding="utf-8")
    assert 'qta.icon("fa5s.times", color=remove_color)' in src



def test_terminal_input_is_not_lost_during_webchannel_or_session_startup():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    window = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "let pendingInput = '';" in html
    assert "function sendInput(data)" in html
    assert "pendingInput += value;" in html
    assert "flushPendingInput();" in html
    assert "term.setEnabled(False)" in window
    assert "term.setEnabled(True)" in window
    assert "if not self._ready or not self._pending_writes:" in widget


def test_terminal_prompt_colours_common_bash_root_prompt():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "__sshdock_apply_prompt" in session
    assert "debian_chroot" in session
    assert "033[1;32m" in session
    assert "033[1;36m" in session
    assert "033[1;33m" in session
    assert "*'@'*:*'# '" in session

def test_terminal_predictor_repairs_relative_directory_predictions():
    from app.terminal.predictor import CommandPredictor

    predictor = CommandPredictor()
    predictor.ingest(["cd./project", "cd../shared", "pushd~/work"], server_id="server")
    assert predictor.suggest("cd", server_id="server") in {"cd ./project", "cd ../shared"}
    assert predictor.suggest("pushd", server_id="server") == "pushd ~/work"

def test_terminal_ghost_invalidates_after_non_append_cursor_edits():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "ArrowLeft" in src
    assert "Delete" in src


def test_mdocker_install_command_is_shell_aware():
    from app.terminal.docker_pretty import install_command

    bash = install_command(shell_name="bash", manage_echo=False)
    fish = install_command(shell_name="fish", manage_echo=False)
    assert '. $HOME/.cache/sshdock/docker.sh' in bash
    assert 'source $HOME/.cache/sshdock/docker.fish' in fish


def test_terminal_prompt_setup_has_no_unbalanced_bash_case():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    block = session[session.index('        if shell_name == "bash":'):session.index('        elif shell_name == "zsh":')]
    assert "__sshdock_apply_prompt(){" in block
    assert ";; esac; };" in block
    assert "else " in block
    assert "fi; " in block

def test_terminal_prompt_matches_literal_root_prompt_shape():
    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "*'@'*:*'# '" in session
    assert "033[1;32m" in session
    assert "033[1;36m" in session
    assert "033[1;33m" in session



def test_terminal_bootstrap_payload_is_syntactically_valid():
    import ast
    import subprocess

    from app.terminal.docker_pretty import install_command

    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    tree = ast.parse(session)
    start_line = session[:session.index('        if shell_name == "bash":')].count("\n") + 1
    end_line = session[:session.index('        elif shell_name == "zsh":')].count("\n") + 1
    setup_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "setup" for target in node.targets)
        and start_line <= node.lineno < end_line
    )
    setup = ast.literal_eval(setup_node.value)

    parts = [setup, install_command(shell_name="bash", manage_echo=False), "printf '\\033[2J\\033[H'"]
    parts = [part.strip().rstrip(";") for part in parts if part.strip()]
    payload = "; ".join(parts)

    syntax = subprocess.run(
        ["bash", "-n"],
        input=payload,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert "; ;" not in payload

def test_bash_prompt_setup_is_syntactically_valid():
    import ast
    import subprocess

    session = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    tree = ast.parse(session)
    start_line = session[:session.index('        if shell_name == "bash":')].count("\n") + 1
    end_line = session[:session.index('        elif shell_name == "zsh":')].count("\n") + 1
    setup_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "setup" for target in node.targets)
        and start_line <= node.lineno < end_line
    )
    setup = ast.literal_eval(setup_node.value)

    syntax = subprocess.run(
        ["bash", "-n"],
        input=setup,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    probe = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", "PS1='root@TG-52087:~# '\n" + setup + "\nprintf '%s' \"$PS1\""],
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert "\\u" in probe.stdout
    assert "\\h" in probe.stdout
    assert "debian_chroot" in session
    assert "\\033[1;32m" in probe.stdout
    assert "\\033[1;36m" in probe.stdout
    assert "\\033[1;33m" in probe.stdout


def test_ssh_connection_watches_unplanned_disconnects():
    src = (ROOT / "app/ssh/client.py").read_text(encoding="utf-8")
    assert "class SSHServerConnection(QObject)" in src
    assert "connection_lost = Signal()" in src
    assert "async def _watch_connection" in src
    assert "await connection.wait_closed()" in src
    assert "self.connection_lost.emit()" in src
    assert "self._start_connection_watch()" in src


def test_session_manager_auto_reconnects_unplanned_disconnects():
    src = (ROOT / "app/ssh/session_manager.py").read_text(encoding="utf-8")
    assert "ssh.connection_lost.connect" in src
    assert "def _handle_connection_lost" in src
    assert "async def _auto_reconnect" in src
    assert "managed.profile.auto_reconnect" in src
    assert "managed.profile.max_retries" in src
    assert "reconnect_task.cancel()" in src


def test_terminal_session_has_independent_auto_reconnect_for_its_pty_transport():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "def _schedule_reconnect" in src
    assert "async def _auto_reconnect" in src
    assert "self.ssh.connect(with_worker_pool=False, with_command_transport=False)" in src
    assert "self.channel = await self.ssh.open_shell()" in src
    assert "terminal-auto-reconnect" in src
    assert "self.status.emit(\"● Reconnecting…\")" in src


def test_terminal_clipboard_shortcuts_and_right_click_are_wired():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    widget = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")

    assert "term.hasSelection()" in html
    assert "copyTextToClipboard(term.getSelection())" in html
    assert "key === 'c'" in html
    assert "key === 'v'" in html
    assert "terminalHost.addEventListener('contextmenu'" in html
    assert "requestClipboardPaste()" in html
    assert "paste: function (data)" in html
    assert "paste_requested = Signal()" in widget
    assert "def request_paste(self) -> None:" in widget
    assert "QApplication.clipboard().text()" in widget
    assert "window.sshdockTerm.paste" in widget


def test_magic_table_copy_cell_row_and_entire_table_use_real_cell_coordinates():
    html = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert """data-row="'+rowIdx+'" data-col="'+i+'"'""" in html
    assert "function tableText()" in html
    assert "sshdock-copy-table" in html
    assert "copyText(tableText())" in html
    assert "Copy cell" in html
    assert "Copy row" in html
