from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_integration_preserves_existing_prompt_hooks():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    start = src.index("    async def _install_shell_integration")
    end = src.index("    def _extract_cwd", start)
    block = src[start:end]

    assert "PS1=" not in block
    assert "PROMPT_COMMAND" in block
    assert "add-zsh-hook precmd __sshdock_cwd" in block
    assert "__sshdock_cwd" in block
    assert "__sshdock_cwd(){ printf '\\\\033]7;file://sshdock%s\\\\007'" in block
