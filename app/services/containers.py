"""Container-focused service over DockerManager."""
from __future__ import annotations

from typing import Any

from app.docker.errors import ClassifiedDockerError, classify_docker_result
from app.docker.manager import DockerManager, DockerResult
from app.docker.models import ContainerInfo, ImageInfo, NetworkInfo, VolumeInfo


class ContainerService:
    def __init__(self, dm: DockerManager):
        self.dm = dm

    async def list_containers(self, all_: bool = True) -> list[ContainerInfo]:
        rows = await self.dm.ps(all_=all_)
        return [ContainerInfo.from_ps(r) for r in rows if r]

    async def list_images(self) -> list[ImageInfo]:
        rows = await self.dm.images()
        return [ImageInfo.from_images(r) for r in rows if r]

    async def list_volumes(self) -> list[VolumeInfo]:
        rows = await self.dm.volumes()
        return [VolumeInfo.from_ls(r) for r in rows if r]

    async def list_networks(self) -> list[NetworkInfo]:
        rows = await self.dm.networks()
        return [NetworkInfo.from_ls(r) for r in rows if r]

    async def action(self, action: str, name: str) -> tuple[DockerResult, ClassifiedDockerError]:
        result = await self.dm.action(action, name)
        return result, classify_docker_result(result)

    async def remove(self, name: str, force: bool = False) -> tuple[DockerResult, ClassifiedDockerError]:
        result = await self.dm.remove(name, force=force)
        return result, classify_docker_result(result)
