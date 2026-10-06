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


def test_terminal_webengine_keeps_proven_first_commit_load_order():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "self._view.show()" in src
    assert "QTimer.singleShot(80, self._flush_pending)" in src


def test_virtual_tables_follow_xterm_alternate_screen():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "activeBuffer.type === 'alternate'" in src
    assert "b.style.display = 'none';" in src
    assert "term.buffer.onBufferChange" in src


def test_terminal_close_has_no_obsolete_bootstrap_cleanup_path():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    assert "_cleanup_bootstrap" not in src
    assert "_bootstrap_path" not in src


def test_terminal_has_dedicated_scrollbar_gutter():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "padding: 4px 16px 4px 4px" in src
    assert 'id="sshdock-scrollbar"' in src
    assert "scrollbar-width: none" in src
    assert "scrollToRatio" in src
    assert "term._core.viewport.scrollBarWidth = 0" in src


def test_virtual_table_uses_xterm_marker_for_resize_reflow():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "term.registerMarker(0)" in src
    assert "b._anchorMarker" in src
    assert "block._anchorMarker.dispose()" in src


def test_terminal_scrollbar_uses_xterm_buffer_position():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "activeScrollState()" in src
    assert "normal ? normal.baseY" in src
    assert "term.scrollToLine" in src
    assert "scrollThumb.style.transform" in src


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
