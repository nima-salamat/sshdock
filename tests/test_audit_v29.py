from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_dangling_container_placeholder():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "guided Docker Run wizard is the next container-creation surface" not in text
    assert "_create_container_exec" in text


def test_server_monitor_uses_single_ssh_command():
    text = (ROOT / "app/server/monitor.py").read_text(encoding="utf-8")
    assert "asyncio.gather" not in text
    assert "self.ssh.run(command" in text


def test_terminal_panel_has_one_close_path():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    panel = text.split("def _build_terminal_panel", 1)[1].split("def _refresh_terminal_selector", 1)[0]
    assert "Close selected terminal session" not in panel
    assert "Close this terminal session" in text


def test_pytest_scope_is_tests_only():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths=["tests"]' in text
