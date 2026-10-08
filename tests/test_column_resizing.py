"""Tests verifying that ALL table columns are user-resizable.

The user reported that they couldn't resize columns in the app's tables.
The root cause: several tables used ``QHeaderView.ResizeMode.Stretch``,
``ResizeToContents``, or ``Fixed`` — none of which allow the user to drag
column borders. Only ``Interactive`` mode allows user dragging.

These tests verify (via static source inspection) that every QTableWidget
in the codebase either:
  - Calls ``_make_columns_resizable`` (which sets all columns to Interactive), or
  - Directly sets ``Interactive`` mode on every column.

They also verify that ``_make_columns_resizable`` itself uses Interactive
for ALL columns (not Stretch for "stretch_cols" — that was the original
bug, since Stretch mode blocks user dragging).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MAIN_WINDOW_SRC = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
CONTAINERS_PAGE_SRC = (ROOT / "app" / "ui" / "pages" / "containers_page.py").read_text(encoding="utf-8")
COMPOSE_PANEL_SRC = (ROOT / "app" / "ui" / "compose_panel.py").read_text(encoding="utf-8")
BROWSER_SRC = (ROOT / "app" / "files" / "browser.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# _make_columns_resizable — the central helper
# --------------------------------------------------------------------------

def test_make_columns_resizable_uses_interactive_for_all_columns():
    """The helper must set Interactive on EVERY column, including stretch_cols.

    The original bug: stretch_cols used ``Stretch`` mode, which auto-sizes
    the column and IGNORES user drag attempts. Only ``Interactive`` allows
    the user to resize.
    """
    # Find the _make_columns_resizable method body
    assert "def _make_columns_resizable(self, table" in MAIN_WINDOW_SRC
    # Every column must get Interactive, not Stretch
    assert "QHeaderView.ResizeMode.Interactive" in MAIN_WINDOW_SRC
    # The old buggy line (Stretch for stretch_cols) must be gone
    assert 'h.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)' not in MAIN_WINDOW_SRC


def test_make_columns_resizable_disables_stretch_last_section():
    """setStretchLastSection(False) so the user can resize the last column too."""
    assert "h.setStretchLastSection(False)" in MAIN_WINDOW_SRC


def test_make_columns_resizable_enables_horizontal_scrollbar():
    """When columns don't fit, show a scrollbar instead of forcing stretch."""
    assert "ScrollBarAsNeeded" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# overview_processes — was ResizeToContents + Stretch, now resizable
# --------------------------------------------------------------------------

def test_overview_processes_is_resizable():
    """Previously: ResizeToContents for cols 0-4, Stretch for col 5.
    Now: must call _make_columns_resizable.
    """
    # The old ResizeToContents pattern must be gone for this table
    assert 'overview_processes.horizontalHeader(); [h.setSectionResizeMode(i,QHeaderView.ResizeMode.ResizeToContents)' not in MAIN_WINDOW_SRC
    # The new call must be present
    assert "_make_columns_resizable(self.overview_processes" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# compose_table (main_window) — was ResizeToContents + Stretch + Fixed
# --------------------------------------------------------------------------

def test_compose_table_in_main_window_is_resizable():
    """Previously: ResizeToContents for cols 0-2, Stretch for col 3, Fixed for col 4.
    Now: must call _make_columns_resizable.
    """
    # The old Fixed mode for col 4 must be gone
    assert 'h.setSectionResizeMode(4,QHeaderView.ResizeMode.Fixed)' not in MAIN_WINDOW_SRC
    # The new call must be present
    assert "_make_columns_resizable(self.compose_table" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# shortcuts_table — was Stretch + ResizeToContents
# --------------------------------------------------------------------------

def test_shortcuts_table_is_resizable():
    """Previously: Stretch for col 0, ResizeToContents for col 1.
    Now: must call _make_columns_resizable.
    """
    assert "self.shortcuts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)" not in MAIN_WINDOW_SRC
    assert "_make_columns_resizable(self.shortcuts_table" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# task_table — was Stretch for cols 0 and 3
# --------------------------------------------------------------------------

def test_task_table_is_resizable():
    """Previously: Stretch for cols 0 and 3.
    Now: must call _make_columns_resizable.
    """
    assert "self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)" not in MAIN_WINDOW_SRC
    assert "_make_columns_resizable(self.task_table" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# server_overview_table — had no resize mode at all
# --------------------------------------------------------------------------

def test_server_overview_table_is_resizable():
    """Previously: no resize mode set (default = Interactive on most platforms,
    but no explicit config). Now: must call _make_columns_resizable for
    consistency and to set proper initial widths + scrollbar.
    """
    assert "_make_columns_resizable(self.server_overview_table" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# Other tables that already called _make_columns_resizable — verify they
# still do (regression: make sure we didn't accidentally remove any calls).
# --------------------------------------------------------------------------

def test_image_table_still_resizable():
    assert "_make_columns_resizable(self.image_table" in MAIN_WINDOW_SRC


def test_volume_table_still_resizable():
    assert "_make_columns_resizable(self.volume_table" in MAIN_WINDOW_SRC


def test_network_table_still_resizable():
    assert "_make_columns_resizable(self.network_table" in MAIN_WINDOW_SRC


def test_process_table_still_resizable():
    assert "_make_columns_resizable(self.process_table" in MAIN_WINDOW_SRC


def test_service_table_still_resizable():
    assert "_make_columns_resizable(self.service_table" in MAIN_WINDOW_SRC


# --------------------------------------------------------------------------
# compose_panel.py — was ResizeToContents + Stretch + Fixed
# --------------------------------------------------------------------------

def test_compose_panel_table_is_resizable():
    """The compose panel's table must use Interactive for all columns."""
    # Old modes must be gone
    assert "ResizeMode.ResizeToContents" not in COMPOSE_PANEL_SRC
    assert "ResizeMode.Stretch" not in COMPOSE_PANEL_SRC
    assert "ResizeMode.Fixed" not in COMPOSE_PANEL_SRC
    # Compose now delegates common resize behavior to StandardTableWidget.
    assert "StandardTableWidget" in COMPOSE_PANEL_SRC
    assert "ColumnSpec" in COMPOSE_PANEL_SRC
    assert "setStretchLastSection(False)" in COMPOSE_PANEL_SRC


# --------------------------------------------------------------------------
# browser.py — was Stretch for col 0
# --------------------------------------------------------------------------

def test_file_browser_table_is_resizable():
    """The remote file browser's table must use Interactive for all columns."""
    # Old Stretch mode for col 0 must be gone
    assert "setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)" not in BROWSER_SRC
    # File browser delegates common resize behavior to StandardTableWidget.
    assert "StandardTableWidget" in BROWSER_SRC
    assert "ColumnSpec" in BROWSER_SRC
    assert "setStretchLastSection(False)" in BROWSER_SRC


# --------------------------------------------------------------------------
# containers_page.py — fallback must also be Interactive
# --------------------------------------------------------------------------

def test_containers_page_fallback_is_interactive():
    """When make_columns_resizable is not provided, the fallback must
    still set Interactive mode (not Stretch) for all columns.
    """
    # The old Stretch fallback must be gone
    assert 'setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)' not in CONTAINERS_PAGE_SRC
    # The new Interactive fallback must be present
    assert "QHeaderView.ResizeMode.Interactive" in CONTAINERS_PAGE_SRC


def test_containers_page_main_path_calls_make_columns_resizable():
    """The main path (with make_columns_resizable provided) must still call it."""
    assert "make_columns_resizable(self.table" in CONTAINERS_PAGE_SRC


# --------------------------------------------------------------------------
# No remaining Stretch / ResizeToContents / Fixed on table columns
# --------------------------------------------------------------------------

def test_no_remaining_stretch_mode_on_table_columns_in_main_window():
    """Make sure no table in main_window.py still uses Stretch mode for
    individual columns (which would block user resizing).
    """
    # Look for patterns like: setSectionResizeMode(N, QHeaderView.ResizeMode.Stretch)
    # These are all bugs — they should be Interactive.
    import re
    matches = re.findall(
        r'setSectionResizeMode\(\s*\d+\s*,\s*QHeaderView\.ResizeMode\.Stretch\s*\)',
        MAIN_WINDOW_SRC,
    )
    assert matches == [], (
        f"Found Stretch mode on individual columns in main_window.py — "
        f"these block user resizing: {matches}"
    )


def test_no_remaining_resizetocontents_on_table_columns_in_main_window():
    """ResizeToContents also blocks user dragging — must be gone."""
    import re
    matches = re.findall(
        r'setSectionResizeMode\(\s*\d+\s*,\s*QHeaderView\.ResizeMode\.ResizeToContents\s*\)',
        MAIN_WINDOW_SRC,
    )
    assert matches == [], (
        f"Found ResizeToContents mode on individual columns in main_window.py — "
        f"these block user resizing: {matches}"
    )


def test_no_remaining_fixed_mode_on_table_columns_in_main_window():
    """Fixed mode also blocks user dragging — must be gone."""
    import re
    matches = re.findall(
        r'setSectionResizeMode\(\s*\d+\s*,\s*QHeaderView\.ResizeMode\.Fixed\s*\)',
        MAIN_WINDOW_SRC,
    )
    assert matches == [], (
        f"Found Fixed mode on individual columns in main_window.py — "
        f"these block user resizing: {matches}"
    )
