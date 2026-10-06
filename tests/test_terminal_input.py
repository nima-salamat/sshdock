from pathlib import Path


def test_terminal_emulator_source_has_real_terminal_key_paths():
    src = Path(__file__).parents[1].joinpath("app", "terminal", "emulator.py").read_text(encoding="utf-8")
    assert "Qt.Key.Key_Tab: b\"\\t\"" in src
    assert "Qt.Key.Key_Backtab: b\"\\x1b[Z\"" in src
    assert "Qt.Key.Key_C" in src and "Qt.KeyboardModifier.ShiftModifier" in src
    assert "_TerminalCursor" in src
    assert "getattr(char, \"underline\", False)" in src
