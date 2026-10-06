from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_containers_page_module_exists():
    text = (ROOT / "app/ui/pages/containers_page.py").read_text(encoding="utf-8")
    assert "class ContainersPage" in text
    assert "set_rows" in text
    assert "ContainerInfo" in text

def test_main_window_uses_containers_page():
    text = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
    assert "ContainersPage" in text
    assert "ContainerService" in text

def test_containers_use_real_checkbox_and_shape_status():
    text = (ROOT / "app/ui/pages/containers_page.py").read_text(encoding="utf-8")
    assert "QCheckBox" in text
    assert "status_text = \"● RUN\" if running else \"○ STOP\"" in text
    assert "_checkbox_for_row" in text
