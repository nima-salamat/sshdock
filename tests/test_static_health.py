from pathlib import Path

ROOT = Path(__file__).parents[1] / "app"


def test_application_create_task_calls_are_registered():
    allowed = {"core/runtime.py", "workers/task_manager.py", "remote/server.py"}
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "asyncio.create_task(" in text or "loop.create_task(" in text:
            offenders.append(rel)
    assert offenders == [], f"Unregistered task creation: {offenders}"


def test_long_lived_compose_stream_uses_dedicated_transport():
    s = (ROOT / "ui/compose_panel.py").read_text(encoding="utf-8")
    assert "await ssh.clone_transport()" in s
    assert '"down"' in s
    assert 'await stream_ssh.start_command(shell_cmd)' in s
    assert "await stream_ssh.close()" in s


def test_ssh_process_registry_has_natural_exit_cleanup():
    s = (ROOT / "ssh/client.py").read_text(encoding="utf-8")
    assert "def live_processes" in s
    assert "def close_processes" in s
    assert "_watch_process" in s
    assert 'name="ssh-process-watch"' in s


def test_runtime_logging_and_hooks_are_installed_at_startup():
    s = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "configure_logging()" in s
    assert "install_exception_hooks()" in s
    assert "qInstallMessageHandler" in s
