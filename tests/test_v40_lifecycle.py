import ast
from pathlib import Path

ROOT=Path(__file__).parents[1]/'app'

def _read(name):
    return (ROOT/name).read_text()

def test_terminal_removed_signal_matches_emit():
    s=_read('ssh/session_manager.py')
    assert 'terminal_removed = Signal(str, str)' in s
    assert 'terminal_removed.emit(pid, tid)' in s

def test_task_manager_never_cancels_current_task():
    s=_read('workers/task_manager.py')
    assert 'current = asyncio.current_task()' in s
    assert 'r.task is not current' in s

def test_shutdown_module_parses():
    ast.parse(_read('workers/task_manager.py'))
    ast.parse(_read('ssh/session_manager.py'))
