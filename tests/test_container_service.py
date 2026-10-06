from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.containers import ContainerService


class FakeDocker:
    def __init__(self):
        self.calls: list[tuple] = []

    async def ps(self, all_=True):
        self.calls.append(("ps", all_))
        return [{"ID": "1", "Names": "/web", "State": "running"}, {}]

    async def images(self):
        self.calls.append(("images",))
        return [{"ID": "sha256:1", "Repository": "nginx", "Tag": "1.27"}]

    async def volumes(self):
        self.calls.append(("volumes",))
        return [{"Name": "data", "Driver": "local"}]

    async def networks(self):
        self.calls.append(("networks",))
        return [{"ID": "net1", "Name": "frontend"}]

    async def action(self, action, name):
        self.calls.append(("action", action, name))
        return SimpleNamespace(exit_code=0, stdout="ok", stderr="", command=f"docker {action} {name}")

    async def remove(self, name, force=False):
        self.calls.append(("remove", name, force))
        return SimpleNamespace(exit_code=1, stdout="", stderr="not found", command="docker rm")


def test_container_service_maps_docker_rows_to_typed_models() -> None:
    async def scenario() -> None:
        docker = FakeDocker()
        service = ContainerService(docker)

        containers = await service.list_containers()
        images = await service.list_images()
        volumes = await service.list_volumes()
        networks = await service.list_networks()

        assert [item.name for item in containers] == ["web"]
        assert containers[0].running is True
        assert images[0].ref == "nginx:1.27"
        assert volumes[0].name == "data"
        assert networks[0].name == "frontend"
        assert docker.calls == [("ps", True), ("images",), ("volumes",), ("networks",)]

    asyncio.run(scenario())


def test_container_service_classifies_actions() -> None:
    async def scenario() -> None:
        docker = FakeDocker()
        service = ContainerService(docker)

        result, classified = await service.action("start", "web")
        assert result.exit_code == 0
        assert classified.kind.value == "ok"

        result, classified = await service.remove("web")
        assert result.exit_code == 1
        assert classified.kind.value == "not_found"

    asyncio.run(scenario())
