from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import pytest

from app.core.models import SSHProfile
from app.remote.agent import (
    AgentCommandResult,
    AgentProtocolError,
    RemoteAgentClient,
    decode_frame,
    encode_frame,
)
from app.ssh.client import SSHServerConnection
from app.storage.settings import _DEFAULT_UI


def test_agent_frame_roundtrip():
    payload = {"id": "1", "method": "exec", "params": {"command": "printf ok"}}
    encoded = encode_frame(payload)
    assert struct.unpack("!I", encoded[:4])[0] == len(encoded) - 4
    assert decode_frame(encoded[4:]) == payload


def test_agent_frame_rejects_invalid_json():
    with pytest.raises(AgentProtocolError):
        decode_frame(b"not-json")


def test_agent_command_result_model():
    result = AgentCommandResult("ok", "", 0)
    assert result.stdout == "ok"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_remote_agent_is_disabled_by_default():
    assert _DEFAULT_UI["remote_agent_enabled"] is False
    profile = SSHProfile(id="1", name="test")
    connection = SSHServerConnection(profile, SimpleNamespace(get=lambda *_: None))
    assert connection.remote_agent_enabled is False
    assert connection.remote_agent_connected is False


def test_remote_agent_toggle_does_not_start_when_disconnected():
    profile = SSHProfile(id="1", name="test")
    connection = SSHServerConnection(profile, SimpleNamespace(get=lambda *_: None))
    connection._remote_agent_enabled = True
    assert connection.remote_agent_enabled is True
    assert connection.remote_agent_connected is False

def test_terminal_bootstrap_does_not_write_setup_commands_to_pty():
    from pathlib import Path
    text = Path("app/terminal/session.py").read_text(encoding="utf-8")
    start = text.index("    async def start(self):")
    end = text.index("    def _extract_cwd", start)
    body = text[start:end]
    assert "self.channel.write(" not in body
    assert "await asyncio.sleep(0.15)" not in body
    assert "_prepare_shell_bootstrap" in body
    assert "_write_bootstrap" in body


def test_terminal_bootstrap_uses_non_pty_exec_and_controlled_bash_rc():
    from pathlib import Path
    text = Path("app/terminal/session.py").read_text(encoding="utf-8")
    assert "self.ssh.start_command("cat > " + shlex.quote(path))" in text
    assert "--noprofile --rcfile" in text
    assert "DOCKER_PRETTY_SH" in text
