from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.docker.manager import DockerManager, DockerResult, _clean_container_ref


class FakeSSH:
    def __init__(self, result=None):
        self.result = result or SimpleNamespace(stdout="", stderr="", exit_status=0)
        self.commands: list[str] = []

    async def run(self, command: str, timeout: float | None = None):
        self.commands.append(command)
        return self.result


def test_clean_container_ref_handles_docker_ps_names() -> None:
    assert _clean_container_ref("/web,api") == "web"
    assert _clean_container_ref("  worker  ") == "worker"
    assert _clean_container_ref(None) == ""
    assert _clean_container_ref("True") == ""


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("8080:80", [8080]),
        ("127.0.0.1:8443:443", [8443]),
        ("0.0.0.0:3000:3000/tcp", [3000]),
        ("8080, 8443:443", [8080, 8443]),
        ("80/tcp", [80]),
        ("65535:1", [65535]),
        ("0:80,70000:1", []),
    ],
)
def test_parse_host_ports(mapping: str, expected: list[int]) -> None:
    assert DockerManager.parse_host_ports(mapping) == expected


def test_json_lines_preserves_original_raw_object() -> None:
    result = DockerResult(
        "docker ps",
        '{"ID":"abc","Names":"web","State":"running"}\n',
        "",
        0,
    )

    rows = DockerManager._json_lines(result, keys=("ID", "Names"))

    assert rows == [{"ID": "abc", "Names": "web", "_raw": {"ID": "abc", "Names": "web", "State": "running"}}]
    assert rows[0]["_raw"] is not rows[0]


def test_json_lines_ignores_invalid_lines_but_keeps_valid_rows() -> None:
    result = DockerResult("docker ps", "not-json\n{\"ID\":\"abc\"}\n", "", 0)
    assert DockerManager._json_lines(result) == [{"ID": "abc"}]


def test_json_lines_raises_for_failed_commands() -> None:
    result = DockerResult("docker ps", "", "permission denied", 13)
    with pytest.raises(RuntimeError, match="permission denied"):
        DockerManager._json_lines(result)


def test_run_quotes_arguments_and_drops_python_sentinels() -> None:
    async def scenario() -> None:
        ssh = FakeSSH(SimpleNamespace(stdout="ok", stderr="", exit_status=0))
        manager = DockerManager(ssh)

        result = await manager.run(["run", "--name", "web app", True, None, "image:latest"])

        assert result.exit_code == 0
        assert "'web app'" in result.command
        assert "True" not in result.command
        assert "None" not in result.command
        assert ssh.commands == [result.command]

    asyncio.run(scenario())


def test_remove_force_adds_only_the_force_flag() -> None:
    async def scenario() -> None:
        ssh = FakeSSH()
        manager = DockerManager(ssh)

        await manager.remove("/web", force=True)

        assert ssh.commands == ["docker rm -f web"]

    asyncio.run(scenario())


def test_action_strips_leading_action_dashes() -> None:
    async def scenario() -> None:
        ssh = FakeSSH()
        manager = DockerManager(ssh)

        await manager.action("--stop", "/web")

        assert ssh.commands == ["docker stop web"]

    asyncio.run(scenario())
