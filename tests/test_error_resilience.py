"""Tests for the error-resilience fixes in refresh_overview and _load_processes.

These tests verify that:

1. ``refresh_overview`` uses ``asyncio.gather(..., return_exceptions=True)``
   so a failing process-sampling task doesn't cancel the ServerMonitor
   snapshot task (which was the root cause of the
   "RuntimeError: Cannot enter into task while another task is being
   executed" crash).

2. ``_load_processes`` has a ``ps`` fallback so that if the ``top -b -n 2``
   script fails (timeout, SSH error), the table still gets populated
   instead of crashing the refresh.

3. ``build_top_script`` produces a *single-pipeline* script (no temp
   files, no multi-pass) so it completes fast and doesn't time out on
   slow SSH connections.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW_SRC = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# refresh_overview must use return_exceptions=True
# --------------------------------------------------------------------------

def test_refresh_overview_uses_return_exceptions():
    """The gather call must use return_exceptions=True.

    Without this, a failing process-sampling task cancels the
    ServerMonitor.snapshot() task mid-flight, and when the SSH worker
    shell dies, asyncio throws:
        RuntimeError: Cannot enter into task while another task is
        being executed
    """
    assert "return_exceptions=True" in MAIN_WINDOW_SRC
    # The gather call must include all three tasks.
    assert "asyncio.gather(" in MAIN_WINDOW_SRC
    assert "docker.detect()" in MAIN_WINDOW_SRC
    assert "ServerMonitor(ssh).snapshot()" in MAIN_WINDOW_SRC


def test_refresh_overview_handles_exception_results():
    """Each gather result must be checked with isinstance(Exception)."""
    assert "isinstance(detect_result, Exception)" in MAIN_WINDOW_SRC
    assert "isinstance(mon_result, Exception)" in MAIN_WINDOW_SRC
    assert "isinstance(proc, Exception)" in MAIN_WINDOW_SRC


def test_refresh_overview_processes_catches_exceptions():
    """The inner processes() coroutine must catch exceptions and return "".

    This prevents a single SSH failure from propagating up and killing
    the entire gather.
    """
    assert "except Exception as exc:" in MAIN_WINDOW_SRC
    assert 'log.warning("Process sampling failed' in MAIN_WINDOW_SRC


def test_refresh_overview_wraps_docker_ps_in_try_except():
    """The docker.ps() call after gather must also be wrapped.

    If the SSH connection died during gather, docker.ps() will also
    fail. We must not let that crash the refresh.
    """
    # Find the docker.ps() call inside refresh_overview and verify it's
    # inside a try/except block.
    assert "container_count = str(len(await docker.ps()))" in MAIN_WINDOW_SRC
    assert "except Exception:\n            container_count = " in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# _load_processes must have a ps fallback
# --------------------------------------------------------------------------

def test_load_processes_uses_fast_ps_snapshot():
    """Interactive process loading should use a quick ps snapshot and show failures."""
    assert "ssh.run_fast(" in MAIN_WINDOW_SRC
    assert "ps -eo pid=,user=,%cpu=,%mem=,stat=,args=" in MAIN_WINDOW_SRC
    assert "Server process list refresh failed:" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# build_top_script must be a single pipeline (no temp files)
# --------------------------------------------------------------------------

def test_build_top_script_has_no_temp_files():
    """The previous implementation used mktemp + join + multi-pass awk.

    That was too slow and caused ConnectionLost timeouts. The new
    implementation must be a single ``top | awk | sort | head`` pipeline.
    """
    from app.docker.stats_math import build_top_script
    script = build_top_script(sample_interval=1.0, max_procs=20)
    assert "mktemp" not in script
    assert "sample1_file" not in script
    assert "sample2_file" not in script
    assert "join" not in script
    # Must be a single pipeline.
    assert script.count("\n") <= 2


def test_build_top_script_uses_top_not_proc_parsing():
    """The script must use ``top -b -n 2`` (standard, reliable) rather
    than hand-rolled /proc/[pid]/stat parsing (fragile, slow).
    """
    from app.docker.stats_math import build_top_script
    script = build_top_script(sample_interval=1.0, max_procs=20)
    assert "top -b -n 2" in script
    # Must NOT have the old /proc/[0-9]*/stat glob loop.
    assert "/proc/[0-9]*/stat" not in script


def test_build_top_script_completes_quickly():
    """The script should be short enough to complete within the 20s SSH
    timeout even on slow connections.

    The previous script was ~3000 chars with multiple temp files. The
    new one should be under 500 chars.
    """
    from app.docker.stats_math import build_top_script
    script = build_top_script(sample_interval=1.0, max_procs=120)
    assert len(script) < 1000, (
        f"Script is too long ({len(script)} chars) — risks SSH timeout. "
        f"Previous implementation was ~3000 chars and timed out."
    )
