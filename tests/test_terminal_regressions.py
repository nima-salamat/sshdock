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
    assert "self.up_btn = self._button(" in src


def test_terminal_webengine_fit_api_is_real_and_syncs_overlay():
    src = (ROOT / "app/terminal/web/terminal.html").read_text(encoding="utf-8")
    assert "fit: function ()" in src
    assert "fitAddon.fit();" in src
    assert "window._sshdockSync()" in src


def test_terminal_webengine_keeps_proven_first_commit_load_order():
    src = (ROOT / "app/terminal/web_widget.py").read_text(encoding="utf-8")
    assert "self._view.show()" in src
    assert "QTimer.singleShot(80, self._flush_pending)" in src
