"""Typed Docker resource models (normalized from CLI JSON)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


@dataclass(slots=True)
class ContainerInfo:
    id: str
    name: str
    image: str = ""
    status: str = ""
    state: str = ""
    ports: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_ps(cls, row: dict[str, Any]) -> ContainerInfo:
        name = _s(row.get("Names") or row.get("Name") or row.get("ID"))
        name = name.lstrip("/")
        if "," in name:
            name = name.split(",", 1)[0].strip()
        state = _s(row.get("State"))
        status = _s(row.get("Status"))
        if not state and status:
            state = status.split()[0] if status else ""
        return cls(
            id=_s(row.get("ID") or row.get("Id")),
            name=name,
            image=_s(row.get("Image")),
            status=status,
            state=state.lower(),
            ports=_s(row.get("Ports")),
            raw=dict(row),
        )

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass(slots=True)
class ImageInfo:
    id: str
    repository: str = ""
    tag: str = ""
    size: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_images(cls, row: dict[str, Any]) -> ImageInfo:
        return cls(
            id=_s(row.get("ID") or row.get("Id")),
            repository=_s(row.get("Repository")),
            tag=_s(row.get("Tag")),
            size=_s(row.get("Size")),
            raw=dict(row),
        )

    @property
    def ref(self) -> str:
        repo, tag = self.repository, self.tag
        if repo and repo != "<none>" and tag and tag != "<none>":
            return f"{repo}:{tag}"
        if repo and repo != "<none>":
            return repo
        return self.id


@dataclass(slots=True)
class VolumeInfo:
    name: str
    driver: str = ""
    scope: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_ls(cls, row: dict[str, Any]) -> VolumeInfo:
        return cls(
            name=_s(row.get("Name")),
            driver=_s(row.get("Driver")),
            scope=_s(row.get("Scope")),
            raw=dict(row),
        )


@dataclass(slots=True)
class NetworkInfo:
    id: str
    name: str
    driver: str = ""
    scope: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_ls(cls, row: dict[str, Any]) -> NetworkInfo:
        return cls(
            id=_s(row.get("ID") or row.get("Id")),
            name=_s(row.get("Name")),
            driver=_s(row.get("Driver")),
            scope=_s(row.get("Scope")),
            raw=dict(row),
        )


@dataclass(slots=True)
class DockerEvent:
    time: str = ""
    type: str = ""
    action: str = ""
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> DockerEvent:
        attrs = obj.get("Actor", {}) if isinstance(obj.get("Actor"), dict) else {}
        attributes = attrs.get("Attributes") if isinstance(attrs, dict) else {}
        if not isinstance(attributes, dict):
            attributes = {}
        name = _s(attributes.get("name") or attributes.get("image") or attrs.get("ID"))
        return cls(
            time=_s(obj.get("time") or obj.get("timeNano")),
            type=_s(obj.get("Type")),
            action=_s(obj.get("Action")),
            name=name,
            raw=dict(obj),
        )

    @property
    def affects_containers(self) -> bool:
        return self.type.lower() == "container"

    @property
    def affects_images(self) -> bool:
        return self.type.lower() == "image"

    @property
    def affects_volumes(self) -> bool:
        return self.type.lower() == "volume"

    @property
    def affects_networks(self) -> bool:
        return self.type.lower() == "network"
