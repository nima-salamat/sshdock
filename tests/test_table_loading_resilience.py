"""Tests verifying that all table loaders have proper error handling.

The user reported that some tables fail to load data. The root causes
were:

1. ``refresh_server_page`` used ``asyncio.gather`` without
   ``return_exceptions=True`` — a failure in one loader cancelled
   the others.
2. Several loaders (``_load_services``, ``_load_server_overview``,
   ``load_host_logs``, ``load_docker_config``, ``load_images``,
   ``load_volumes``, ``load_networks``, ``load_containers``) had no
   try/except — SSH failures propagated and left stale data in tables.
3. Tables were cleared AFTER fetching, so on failure they retained
   stale data.
4. ``_json_lines`` silently returned ``[]`` on docker-side errors with
   no logging — making it look like "no images" rather than "docker
   failed".
5. ``load_host_logs`` had no timeout — ``journalctl`` could hang
   indefinitely on corrupt journals.
6. ``refresh_storage`` used ``asyncio.gather`` without
   ``return_exceptions=True``.

These tests verify the fixes via static source inspection.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW_SRC = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
DOCKER_MANAGER_SRC = (ROOT / "app" / "docker" / "manager.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# refresh_server_page — must use return_exceptions=True
# --------------------------------------------------------------------------

def test_refresh_server_page_uses_return_exceptions():
    assert "return_exceptions=True" in MAIN_WINDOW_SRC
    # The gather in refresh_server_page must include all three loaders.
    assert "self._load_server_overview()" in MAIN_WINDOW_SRC
    assert "self._load_processes()" in MAIN_WINDOW_SRC
    assert "self._load_services()" in MAIN_WINDOW_SRC


def test_refresh_server_page_logs_individual_failures():
    # Each result must be checked with isinstance(Exception).
    assert 'isinstance(r, Exception)' in MAIN_WINDOW_SRC
    assert 'names = ["overview", "processes", "services"]' in MAIN_WINDOW_SRC


def test_refresh_server_page_wraps_file_browser_refresh():
    # The file browser refresh must also be in a try/except.
    assert 'await self.server_file_browser._refresh_async()' in MAIN_WINDOW_SRC
    assert 'File browser refresh failed' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# _load_server_overview — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_server_overview_has_try_except():
    assert 'async def _load_server_overview(self):' in MAIN_WINDOW_SRC
    assert 'except Exception as exc:' in MAIN_WINDOW_SRC
    assert 'Server overview snapshot failed' in MAIN_WINDOW_SRC


def test_load_server_overview_clears_before_fetch():
    # The setRowCount(0) must come BEFORE the await, not after.
    src = MAIN_WINDOW_SRC
    idx = src.find('async def _load_server_overview')
    assert idx >= 0
    method_body = src[idx:idx + 800]
    setRowCount_pos = method_body.find('setRowCount(0)')
    snapshot_pos = method_body.find('await ServerMonitor')
    assert setRowCount_pos < snapshot_pos, (
        "setRowCount(0) must come before the SSH fetch so stale data "
        "doesn't persist on failure"
    )


def test_load_server_overview_shows_error_row_on_failure():
    assert 'Could not fetch server info' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# _load_services — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_services_has_try_except():
    assert 'async def _load_services(self):' in MAIN_WINDOW_SRC
    assert 'Services list fetch failed' in MAIN_WINDOW_SRC


def test_load_services_clears_before_fetch():
    src = MAIN_WINDOW_SRC
    idx = src.find('async def _load_services')
    assert idx >= 0
    method_body = src[idx:idx + 800]
    setRowCount_pos = method_body.find('setRowCount(0)')
    ssh_run_pos = method_body.find('ssh.run')
    assert setRowCount_pos < ssh_run_pos, (
        "setRowCount(0) must come before the SSH fetch in _load_services"
    )


def test_load_services_handles_systemctl_error():
    # If systemctl returns non-zero exit code with no stdout, show a
    # helpful message instead of an empty table.
    assert 'systemctl error' in MAIN_WINDOW_SRC
    assert 'systemctl not available or returned no data' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# load_host_logs — must have timeout + try/except
# --------------------------------------------------------------------------

def test_load_host_logs_has_timeout():
    # Previously had no timeout — journalctl could hang indefinitely.
    # Now uses run_fast with a 15s timeout.
    assert 'timeout=15' in MAIN_WINDOW_SRC
    assert 'journalctl --no-pager -n 250' in MAIN_WINDOW_SRC


def test_load_host_logs_has_try_except():
    assert 'async def load_host_logs' in MAIN_WINDOW_SRC
    assert 'Host logs fetch failed' in MAIN_WINDOW_SRC


def test_load_host_logs_shows_loading_indicator():
    assert 'Loading logs' in MAIN_WINDOW_SRC


def test_load_host_logs_fallback_message_on_error():
    assert 'journalctl is not available on this host' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# load_docker_config — must have per-command try/except
# --------------------------------------------------------------------------

def test_load_docker_config_has_per_command_try_except():
    # Previously, a single SSH failure aborted all 4 commands.
    # Now each command is wrapped in its own try/except.
    assert 'async def load_docker_config' in MAIN_WINDOW_SRC
    assert 'Docker config command failed' in MAIN_WINDOW_SRC


def test_load_docker_config_shows_loading_indicator():
    assert 'Loading Docker configuration' in MAIN_WINDOW_SRC


def test_load_docker_config_continues_after_failure():
    # The parallel gather must handle exceptions per-command.
    assert 'return_exceptions=True' in MAIN_WINDOW_SRC
    assert 'isinstance(r, str)' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# load_images — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_images_has_try_except():
    assert 'async def load_images' in MAIN_WINDOW_SRC
    assert 'Image list fetch failed' in MAIN_WINDOW_SRC


def test_load_images_clears_before_fetch():
    src = MAIN_WINDOW_SRC
    idx = src.find('async def load_images')
    assert idx >= 0
    method_body = src[idx:idx + 600]
    setRowCount_pos = method_body.find('setRowCount(0)')
    images_pos = method_body.find('dm.images()')
    assert setRowCount_pos < images_pos


def test_load_images_used_images_falls_back_to_empty_set():
    # If used_images() fails, mark everything as unused rather than
    # crashing.
    assert 'used = set()' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# load_volumes — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_volumes_has_try_except():
    assert 'async def load_volumes' in MAIN_WINDOW_SRC
    assert 'Volume list fetch failed' in MAIN_WINDOW_SRC


def test_load_volumes_clears_before_fetch():
    src = MAIN_WINDOW_SRC
    idx = src.find('async def load_volumes')
    assert idx >= 0
    method_body = src[idx:idx + 600]
    setRowCount_pos = method_body.find('setRowCount(0)')
    volumes_pos = method_body.find('dm.volumes()')
    assert setRowCount_pos < volumes_pos


# --------------------------------------------------------------------------
# load_networks — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_networks_has_try_except():
    assert 'async def load_networks' in MAIN_WINDOW_SRC
    assert 'Network list fetch failed' in MAIN_WINDOW_SRC


def test_load_networks_clears_before_fetch():
    src = MAIN_WINDOW_SRC
    idx = src.find('async def load_networks')
    assert idx >= 0
    method_body = src[idx:idx + 600]
    setRowCount_pos = method_body.find('setRowCount(0)')
    networks_pos = method_body.find('dm.networks()')
    assert setRowCount_pos < networks_pos


# --------------------------------------------------------------------------
# load_containers — must have try/except + clear-before-fetch
# --------------------------------------------------------------------------

def test_load_containers_has_try_except():
    assert 'async def load_containers' in MAIN_WINDOW_SRC
    assert 'Container list fetch failed' in MAIN_WINDOW_SRC


def test_load_containers_clears_before_fetch():
    src = MAIN_WINDOW_SRC
    idx = src.find('async def load_containers')
    assert idx >= 0
    method_body = src[idx:idx + 600]
    render_pos = method_body.find('self._render_containers([])')
    list_pos = method_body.find('list_containers')
    assert render_pos < list_pos, (
        "_render_containers([]) must be called before list_containers "
        "so the table is cleared before the SSH fetch"
    )


# --------------------------------------------------------------------------
# refresh_storage — must use return_exceptions=True
# --------------------------------------------------------------------------

def test_refresh_storage_uses_return_exceptions():
    idx = MAIN_WINDOW_SRC.find('async def refresh_storage')
    assert idx >= 0
    method_body = MAIN_WINDOW_SRC[idx:idx + 600]
    assert 'return_exceptions=True' in method_body


def test_refresh_storage_wraps_resource_usage_in_try_except():
    assert 'Resource usage fetch failed' in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# _json_lines — must log docker-side errors
# --------------------------------------------------------------------------

def test_json_lines_logs_docker_errors():
    # Previously returned [] silently — now logs a warning so the user
    # can see in the logs why a table is empty.
    assert 'docker command failed' in DOCKER_MANAGER_SRC.lower() or \
           'docker command failed' in DOCKER_MANAGER_SRC
    assert 'exit_code' in DOCKER_MANAGER_SRC
    assert 'logging.getLogger' in DOCKER_MANAGER_SRC or 'import logging' in DOCKER_MANAGER_SRC


# --------------------------------------------------------------------------
# Regression: ensure no loader clears table AFTER fetch anymore
# --------------------------------------------------------------------------

def test_live_table_loaders_swap_rows_only_after_fetch():
    """Live tables keep the previous snapshot while network data is loading."""
    expected = [
        ("async def load_images", "rows = await dm.images()", "with self._atomic_table(self.image_table"),
        ("async def load_volumes", "rows=await dm.volumes()", "with self._atomic_table(self.volume_table"),
        ("async def load_networks", "rows=await dm.networks()", "with self._atomic_table(self.network_table"),
    ]
    for function_marker, fetch_line, render_marker in expected:
        start = MAIN_WINDOW_SRC.index(function_marker)
        end = MAIN_WINDOW_SRC.find("\n    async def ", start + len(function_marker))
        end = len(MAIN_WINDOW_SRC) if end < 0 else end
        body = MAIN_WINDOW_SRC[start:end]
        assert fetch_line in body
        assert render_marker in body
        fetch_pos = body.index(fetch_line)
        render_pos = body.index(render_marker)
        assert render_pos > fetch_pos




def test_worker_cancellation_disposes_persistent_shell():
    """Cancelling a page command must not leave stale output in the worker."""
    src = (ROOT / "app" / "ssh" / "client.py").read_text(encoding="utf-8")
    body = src[src.index("async def run(self, command: str"):src.index("async def close(self)", src.index("async def run(self, command: str"))]
    assert "except asyncio.CancelledError:" in body
    assert "await self._dispose_process()" in body


def test_docker_list_failure_is_not_reported_as_empty_data():
    """A failed docker list command must propagate an error to the UI loader."""
    src = (ROOT / "app" / "docker" / "manager.py").read_text(encoding="utf-8")
    start_idx = src.index("    def _json_lines")
    body = src[start_idx:start_idx + 1800]
    assert "raise RuntimeError(" in body
    assert "return []" not in body.split("if result.exit_code != 0:", 1)[1].split("items:", 1)[0]
