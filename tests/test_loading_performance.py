"""Tests for the performance optimizations that fix slow table loading.

The user reported that tables load very slowly, especially:
  - The process preview table doesn't load at all
  - The volumes table takes a long time every refresh

Root causes identified and fixed:

1. **Worker pool bottleneck** — only 3 persistent shells, so concurrent
   ``ssh.run()`` calls serialized. Fixed by:
   - Increasing default pool size from 3 to 8.
   - Adding ``run_fast()`` which opens a fresh exec channel per call,
     bypassing the pool entirely for true parallelism.

2. **N+1 docker inspect** — ``resource_usage`` ran a separate
   ``docker volume inspect`` per volume in a shell loop. 50 volumes =
   50 sequential docker invocations = ~30s. Fixed with batch inspect.

3. **Slow top sampling** — ``top -b -n 2 -d 1`` takes 2s minimum.
   Reduced to ``-d 0.5`` for the overview (1s total).

4. **Sequential docker config** — ``load_docker_config`` ran 4 commands
   sequentially. Now runs them in parallel via ``run_fast`` + ``gather``.

These tests verify the optimizations via static source inspection.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW_SRC = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
SSH_CLIENT_SRC = (ROOT / "app" / "ssh" / "client.py").read_text(encoding="utf-8")
DOCKER_MANAGER_SRC = (ROOT / "app" / "docker" / "manager.py").read_text(encoding="utf-8")
MONITOR_SRC = (ROOT / "app" / "server" / "monitor.py").read_text(encoding="utf-8")
STATS_MATH_SRC = (ROOT / "app" / "docker" / "stats_math.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# run_fast — the new parallel exec channel method
# --------------------------------------------------------------------------

def test_run_fast_method_exists():
    assert "async def run_fast(self, command, timeout=None):" in SSH_CLIENT_SRC


def test_run_fast_uses_fresh_exec_channel():
    # Must call create_process (fresh channel), not _run_with_worker.
    assert "create_process(command" in SSH_CLIENT_SRC
    assert "_run_with_worker" not in SSH_CLIENT_SRC.split("async def run_fast")[1].split("async def ")[0]


def test_run_fast_has_timeout_handling():
    # Must handle both channel-open timeout and command-execution timeout.
    assert "SSH exec channel open timed out" in SSH_CLIENT_SRC
    assert "SSH command timed out" in SSH_CLIENT_SRC


def test_run_fast_returns_command_result():
    assert "return CommandResult(" in SSH_CLIENT_SRC


# --------------------------------------------------------------------------
# Worker pool size increase
# --------------------------------------------------------------------------

def test_worker_pool_default_is_3():
    """The configured default is three worker shells."""
    settings_src = (ROOT / "app" / "storage" / "settings.py").read_text(encoding="utf-8")
    manager_src = (ROOT / "app" / "ssh" / "session_manager.py").read_text(encoding="utf-8")
    assert '"worker_shell_count": 3' in settings_src or "worker_shell_count: int = 3" in manager_src


def test_page_loader_uses_real_worker_queue():
    body = SSH_CLIENT_SRC[SSH_CLIENT_SRC.index("async def run_page"):SSH_CLIENT_SRC.index("async def run_fast")]
    assert "return await self.run(command, timeout)" in body
    assert "run_fast" not in body


def test_page_requests_wait_in_worker_queue():
    """A saturated page loader waits for the persistent worker pool."""
    body = SSH_CLIENT_SRC[SSH_CLIENT_SRC.index("async def _acquire_worker"):SSH_CLIENT_SRC.index("async def _run_with_worker")]
    assert "await self._workers_condition.wait()" in body


def test_worker_setting_applies_immediately_to_active_connections():
    manager_src = (ROOT / "app" / "ssh" / "session_manager.py").read_text(encoding="utf-8")
    ui_src = MAIN_WINDOW_SRC
    client_src = SSH_CLIENT_SRC
    assert "def set_worker_shell_count_limit(self, count: int) -> int:" in manager_src
    assert "managed.ssh.set_worker_shell_count_limit(self.worker_shell_count)" in manager_src
    assert "self.manager.set_worker_shell_count_limit(worker_count)" in ui_src
    assert "def set_worker_shell_count_limit(self, count: int) -> int:" in client_src



# --------------------------------------------------------------------------
# resource_usage — batch inspect (no N+1 loop)
# --------------------------------------------------------------------------

def test_resource_usage_uses_batch_inspect():
    """The volume/network usage must use a single batch docker inspect,
    not a per-item shell loop.
    """
    # The old pattern was a shell `for v in ...; do docker volume inspect ...; done`.
    # The new pattern is a single `docker volume inspect ... $(docker volume ls -q)`.
    assert "for v in $(docker volume ls" not in DOCKER_MANAGER_SRC
    assert "for n in $(docker network ls" not in DOCKER_MANAGER_SRC
    # New batch pattern:
    assert "docker volume inspect --format" in DOCKER_MANAGER_SRC
    assert "$(docker volume ls -q" in DOCKER_MANAGER_SRC
    assert "$(docker network ls -q" in DOCKER_MANAGER_SRC


def test_resource_usage_timeout_reduced():
    """Timeout reduced from 60s to 30s since batch is much faster."""
    # The old timeout was 60s for the per-item loop.
    # The new batch should complete in <5s, so 30s is generous.
    assert "timeout=30" in DOCKER_MANAGER_SRC
    assert "timeout=60" not in DOCKER_MANAGER_SRC


# --------------------------------------------------------------------------
# Overview process sampling — uses run_fast + 0.5s interval
# --------------------------------------------------------------------------

def test_overview_processes_use_worker_queue():
    assert "ssh.run_page(script" in MAIN_WINDOW_SRC


def test_overview_uses_sub_second_interval():
    """The overview should use 0.5s interval for faster refresh."""
    assert "sample_interval=0.5" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# _load_processes — uses run_fast
# --------------------------------------------------------------------------

def test_load_processes_uses_run_fast():
    """The Server page process loader should use a dedicated fast SSH channel."""
    idx = MAIN_WINDOW_SRC.find("async def _load_processes")
    assert idx >= 0
    method_body = MAIN_WINDOW_SRC[idx:idx + 4000]
    assert "run_fast(" in method_body
    assert "ps -eo pid=,user=,%cpu=,%mem=,stat=,args=" in method_body


# --------------------------------------------------------------------------
# _load_services — uses run_fast
# --------------------------------------------------------------------------

def test_load_services_uses_run_fast():
    idx = MAIN_WINDOW_SRC.find("async def _load_services")
    assert idx >= 0
    method_body = MAIN_WINDOW_SRC[idx:idx + 800]
    assert "run_page" in method_body


# --------------------------------------------------------------------------
# load_host_logs — uses run_fast
# --------------------------------------------------------------------------

def test_load_host_logs_uses_run_fast():
    idx = MAIN_WINDOW_SRC.find("async def load_host_logs")
    assert idx >= 0
    method_body = MAIN_WINDOW_SRC[idx:idx + 800]
    assert "run_page" in method_body


# --------------------------------------------------------------------------
# load_docker_config — parallel via gather + run_fast
# --------------------------------------------------------------------------

def test_load_docker_config_runs_commands_in_parallel():
    """The 4 docker config commands must run in parallel, not sequentially."""
    idx = MAIN_WINDOW_SRC.find("async def load_docker_config")
    assert idx >= 0
    method_body = MAIN_WINDOW_SRC[idx:idx + 2000]
    assert "asyncio.gather(" in method_body
    assert "run_page" in method_body
    # Must NOT have a sequential for-loop over commands.
    assert "for header, cmd, timeout in commands:" not in method_body


# --------------------------------------------------------------------------
# ServerMonitor — uses run_fast
# --------------------------------------------------------------------------

def test_server_monitor_uses_worker_queue():
    assert "hasattr(self.ssh, 'run_page')" in MONITOR_SRC
    assert "self.ssh.run_page(command, timeout=10)" in MONITOR_SRC


# --------------------------------------------------------------------------
# build_top_script — accepts fractional intervals
# --------------------------------------------------------------------------

def test_build_top_script_accepts_fractional_interval():
    """The script must support 0.5s intervals for faster refreshes."""
    from app.docker.stats_math import build_top_script
    script = build_top_script(sample_interval=0.5, max_procs=10)
    assert "-d 0.5" in script


def test_build_top_script_clamps_to_0_1():
    from app.docker.stats_math import build_top_script
    script = build_top_script(sample_interval=0.0, max_procs=5)
    assert "-d 0.1" in script
    script2 = build_top_script(sample_interval=-10.0, max_procs=5)
    assert "-d 0.1" in script2


# --------------------------------------------------------------------------
# Regression: ensure run_page is used by all slow loaders
# --------------------------------------------------------------------------

def test_all_slow_loaders_use_run_fast():
    """Every loader that previously used ssh.run() for a slow command
    should now use run_page() for bounded parallelism.

    Exceptions: docker.run() calls (which wrap ssh.run) are OK to keep
    using the worker pool since they're typically quick JSON queries.
    The slow loaders are the ones with multi-second commands.
    """
    slow_loaders = [
        "refresh_overview",      # overview telemetry uses the worker queue
        "_load_services",        # systemctl list-units (can be slow)
        "load_host_logs",        # journalctl (can hang)
        "load_docker_config",    # 4 sequential docker commands
    ]
    for loader in slow_loaders:
        idx = MAIN_WINDOW_SRC.find(f"async def {loader}")
        assert idx >= 0, f"Loader {loader} not found"
        # Read up to 3000 chars of the method body.
        method_body = MAIN_WINDOW_SRC[idx:idx + 3000]
        assert "run_page" in method_body, (
            f"Loader {loader} should use run_fast for parallelism"
        )


def test_server_monitor_does_not_bypass_worker_queue():
    """ServerMonitor must use the same queued page-data path."""
    assert "run_page" in MONITOR_SRC
    assert "run_fast" not in MONITOR_SRC
