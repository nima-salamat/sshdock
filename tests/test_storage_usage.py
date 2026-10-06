from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.docker.manager import DockerManager


class FakeSSH:
    def __init__(self, *, container_out: str = "", volume_out: str = "", network_out: str = ""):
        self.commands: list[str] = []
        self.container_out = container_out
        self.volume_out = volume_out
        self.network_out = network_out

    async def run(self, command, timeout=None):
        self.commands.append(command)
        # Primary path: mounts + networks from all containers
        if "docker ps -aq" in command and "docker inspect" in command:
            return SimpleNamespace(stdout=self.container_out, stderr="", exit_status=0)
        if "docker volume" in command and ("inspect" in command or "ls" in command):
            return SimpleNamespace(stdout=self.volume_out, stderr="", exit_status=0)
        if "docker network" in command and ("inspect" in command or "ls" in command):
            return SimpleNamespace(stdout=self.network_out, stderr="", exit_status=0)
        if "docker ps -a --format" in command:
            return SimpleNamespace(stdout="", stderr="", exit_status=0)
        return SimpleNamespace(stdout="", stderr="", exit_status=0)


def test_resource_usage_from_container_mounts():
    """Stopped/running container mounts are the source of truth."""
    async def run():
        ssh = FakeSSH(
            container_out="V\tdata-vol\nN\tapp-net\nV\tlogs-vol\nN\tbridge\n",
            volume_out="data-vol\t-1\nlogs-vol\t-1\norphan\t0\n",
            network_out="app-net\t0\norphan-net\t0\n",
        )
        DockerManager._usage_cache.clear()
        DockerManager._usage_locks.clear()
        dm = DockerManager(ssh)
        vols, nets = await dm.resource_usage(max_age=0)
        assert vols == {"data-vol", "logs-vol"}
        assert "app-net" in nets
        assert "bridge" in nets and "host" in nets and "none" in nets
        assert "orphan-net" not in nets
        assert "orphan" not in vols

    asyncio.run(run())


def test_resource_usage_merges_positive_refcount():
    """Positive UsageData.RefCount still marks volumes used even without mounts in sample."""
    async def run():
        ssh = FakeSSH(
            container_out="",
            volume_out="v1\t1\nv2\t0\n",
            network_out="net1\t2\nnet2\t0\n",
        )
        DockerManager._usage_cache.clear()
        DockerManager._usage_locks.clear()
        dm = DockerManager(ssh)
        vols, nets = await dm.resource_usage(max_age=0)
        assert vols == {"v1"}
        assert "net1" in nets
        assert "net2" not in nets
        assert any("UsageData.RefCount" in c for c in ssh.commands)

    asyncio.run(run())


def test_resource_usage_ignores_unknown_refcount_sentinel():
    """RefCount -1 must not mark a volume used by itself."""
    async def run():
        ssh = FakeSSH(
            container_out="",
            volume_out="lonely\t-1\n",
            network_out="",
        )
        DockerManager._usage_cache.clear()
        DockerManager._usage_locks.clear()
        dm = DockerManager(ssh)
        vols, nets = await dm.resource_usage(max_age=0)
        assert "lonely" not in vols
        assert {"bridge", "host", "none"} <= nets

    asyncio.run(run())
