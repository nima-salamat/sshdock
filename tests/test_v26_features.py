from pathlib import Path
ROOT=Path(__file__).parents[1]
def test_clone_transport_no_command_channel():
 s=(ROOT/"app/ssh/client.py").read_text(); assert "with_command_transport=False" in s
def test_inline_spinner_and_storage_usage():
 s=(ROOT/"app/ui/main_window.py").read_text(); assert "def _show_page_spinner" in s and "Used by" in s
def test_single_terminal_close_button():
 s=(ROOT/"app/ui/main_window.py").read_text(); assert "Close this terminal session" in s and "Close Terminal" not in s.split("def _build_terminal_panel",1)[1].split("def _refresh_terminal_selector",1)[0]
