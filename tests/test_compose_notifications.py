from pathlib import Path
import ast

ROOT = Path(__file__).parents[1] / 'app'


def _read(name):
    return (ROOT / name).read_text(encoding='utf-8')


def test_compose_detection_is_single_flight():
    s = _read('docker/manager.py')
    assert '_compose_detect_locks' in s
    assert 'async with lock:' in s
    assert 'if not force and key in self._compose_bin:' in s


def test_bulk_compose_runs_all_projects_concurrently():
    s = _read('ui/compose_panel.py')
    assert 'await asyncio.gather(' in s
    assert 'return_exceptions=True' in s
    assert '"down"' in s
    assert 'NotificationBus.instance().success(' in s


def test_down_uses_live_stream_path():
    s = _read('ui/compose_panel.py')
    # down intentionally belongs to the streamed path alongside other compose
    # lifecycle actions so its remote output is visible in the project tab.
    assert 'if action in {"up", "build", "pull"}' not in s or '"down"' in s
    assert 'await ssh.start_command(shell_cmd)' in s
    assert 'NotificationBus.instance().error(f"Compose {action} failed", project)' in s


def test_notification_status_bar_mirrors_all_levels():
    s = _read('ui/main_window.py')
    assert 'if level in {"success", "error", "warning", "info"}:' in s


def test_changed_modules_parse():
    for name in ('docker/manager.py', 'ui/compose_panel.py', 'ui/notifications.py', 'ui/main_window.py'):
        ast.parse(_read(name))
