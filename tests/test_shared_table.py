from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_table_declares_common_column_and_state_primitives():
    src = (ROOT / "app/ui/widgets/table.py").read_text(encoding="utf-8")
    assert "class ColumnSpec" in src
    assert "class TableState" in src
    assert "class StandardTableWidget(QTableWidget)" in src
    assert "class TableToolbar(QWidget)" in src


def test_shared_table_has_safe_refresh_and_view_preservation():
    src = (ROOT / "app/ui/widgets/table.py").read_text(encoding="utf-8")
    assert "def preserve_view" in src
    assert "def set_loading" in src
    assert "def set_empty" in src
    assert "def set_error" in src
    assert "def set_refresh_callback" in src
    assert "def configure_auto_refresh" in src
    assert "self._refresh_task is not None" in src


def test_shared_table_has_copy_and_standard_context_menu():
    src = (ROOT / "app/ui/widgets/table.py").read_text(encoding="utf-8")
    assert "def copy_selection" in src
    assert "def copy_all" in src
    assert "def install_standard_context_menu" in src
    assert "Auto-fit columns" in src


def test_main_window_uses_shared_table_widget():
    src = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "from app.ui.widgets.table import StandardTableWidget" in src
    assert "StandardTableWidget(" in src
    assert "QTableWidget(" not in src


def test_containers_and_docker_results_use_shared_table_widget():
    containers = (ROOT / "app/ui/pages/containers_page.py").read_text(encoding="utf-8")
    docker = (ROOT / "app/ui/docker_results_table.py").read_text(encoding="utf-8")
    assert "StandardTableWidget" in containers
    assert "install_standard_context_menu" in containers
    assert "StandardTableWidget" in docker
    assert "install_standard_context_menu" in docker
