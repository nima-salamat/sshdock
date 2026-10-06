"""Unit tests for DockerManager compose detection (mocked SSH)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.docker.manager import DockerManager


class FakeSSH:
    def __init__(self, responses: dict[str, tuple[int, str, str]] | None = None):
        self.responses = responses or {}
        self.commands: list[str] = []

    async def run(self, command: str, timeout: float | None = None) -> Any:
        self.commands.append(command)
        for key, (code, out, err) in self.responses.items():
            if key in command:
                return SimpleNamespace(stdout=out, stderr=err, exit_status=code)
        return SimpleNamespace(stdout="", stderr="not found", exit_status=1)


def test_detect_compose_prefers_plugin():
    async def _run():
        ssh = FakeSSH({"docker compose version": (0, "Docker Compose version v2.24.0", "")})
        dm = DockerManager(ssh)
        assert await dm.detect_compose_bin() == "docker compose"
        r = await dm.compose(["ls"])
        assert "compose" in r.command
    asyncio.run(_run())


def test_detect_compose_fallback_v1():
    async def _run():
        ssh = FakeSSH({
            "docker compose version": (1, "", "unknown"),
            "docker-compose version": (0, "docker-compose version 1.29.2", ""),
        })
        dm = DockerManager(ssh)
        assert await dm.detect_compose_bin(force=True) == "docker-compose"
        r = await dm.compose(["ps"])
        assert r.command.startswith("docker-compose ")
    asyncio.run(_run())


def test_compose_missing_binary():
    async def _run():
        # Explicit failures for both binaries (empty stdout)
        ssh = FakeSSH({
            "docker compose version": (127, "", "no plugin"),
            "docker-compose version": (127, "", "not found"),
        })
        DockerManager._compose_bin.clear()
        dm = DockerManager(ssh)
        r = await dm.compose(["ls"])
        assert r.exit_code == 127
        assert "Neither" in r.stderr
    asyncio.run(_run())


def test_compose_ls_json():
    async def _run():
        payload = '[{"Name":"web","Status":"running(1)","ConfigFiles":"/app/compose.yml"}]'
        ssh = FakeSSH({
            "docker compose version": (0, "v2", ""),
            "--format": (0, payload, ""),
        })
        DockerManager._compose_bin.clear()
        dm = DockerManager(ssh)
        rows = await dm.compose_ls()
        assert len(rows) == 1
        assert rows[0]["Name"] == "web"
    asyncio.run(_run())


def test_run_strips_bool_none():
    async def _run():
        ssh = FakeSSH({"docker": (0, "ok", "")})
        dm = DockerManager(ssh)
        r = await dm.run(["ps", True, None, "-a"])  # type: ignore[list-item]
        assert "True" not in r.command
        assert "None" not in r.command
        assert "-a" in r.command
    asyncio.run(_run())
