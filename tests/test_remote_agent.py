from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_remote_agent_toggle_does_not_start_when_disconnected():
    profile = SSHProfile(id="1", name="test")
    connection = SSHServerConnection(profile, SimpleNamespace(get=lambda *_: None))
    await connection.set_remote_agent_enabled(True)
    assert connection.remote_agent_enabled is True
    assert connection.remote_agent_connected is False
