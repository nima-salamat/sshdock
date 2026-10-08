from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_integration_preserves_existing_prompt_hooks():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    start = src.index("    async def _install_shell_integration")
    end = src.index("    def _extract_cwd", start)
    block = src[start:end]

    assert "PS1=" in block
    assert "debian_chroot" in block
    assert "PROMPT_COMMAND" in block
    assert "add-zsh-hook precmd __sshdock_cwd" in block
    assert "__sshdock_cwd" in block
    assert "__sshdock_cwd(){ local __sshdock_status=$?; printf" in block
    assert "sshdock-result;%s" in block



def test_shell_integration_prepares_scripts_outside_the_live_terminal():
    src = (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
    docker = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "await self._prepare_shell_integration()" in src
    assert "await self.ssh.run(prepare_command(), timeout=5.0)" in src
    assert "install_command(shell_name=shell_name, manage_echo=False)" in src
    assert "def prepare_command()" in docker
    assert "def source_command(shell_name: str = \"\")" in docker


def test_fish_shell_has_a_native_mdocker_integration():
    docker = (ROOT / "app/terminal/docker_pretty.py").read_text(encoding="utf-8")
    assert "DOCKER_PRETTY_FISH" in docker
    assert "function __sshdock_magic" in docker
    assert "function mdocker" in docker
    assert "install_command(shell_name=shell_name, manage_echo=False)" in (ROOT / "app/terminal/session.py").read_text(encoding="utf-8")
