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
    assert "# Intentionally do not shadow the real docker command." in src
    assert "m-docker() {" not in src
    assert "magic-docker() {" not in src
    assert "docker() {" not in src.replace("mdocker() {", "")


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




def test_terminal_bootstrap_disables_echo_before_payload():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert 'self.channel.write(b"stty -echo 2>/dev/null\\r")' in src
    assert "source_command(shell_name)" in src
    assert "stty echo 2>/dev/null" in src
    assert "printf '\\r\\033[K'" in src

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
    assert "source_command(shell_name)" in src
    assert 'SH_SCRIPT_PATH = "/tmp/.sshdock_docker.sh"' in docker

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


def test_magic_table_has_prompt_fallback_and_scrollbar_sync_without_tables():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "const startAbs = Math.max(0, promptAbs - RESERVE);" in src
    assert "block._anchorPromptAbs = promptAbs;" in src
    assert "if (!term._sshdockScrollBarHook)" in src
    assert "term.onWriteParsed(function(){ syncScrollBar(); })" in src
    assert "term.onRender(function(){ syncScrollBar(); })" in src
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
