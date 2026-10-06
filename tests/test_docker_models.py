from app.docker.models import ContainerInfo, ImageInfo, DockerEvent
from app.docker.errors import classify_docker_result, DockerErrorKind
from app.docker.manager import DockerResult


def test_container_from_ps_strips_slash_and_multi_name():
    c = ContainerInfo.from_ps({
        "ID": "abc123",
        "Names": "/web,web_alias",
        "Image": "nginx:latest",
        "Status": "Up 2 hours",
        "State": "running",
        "Ports": "80/tcp",
    })
    assert c.name == "web"
    assert c.running is True
    assert c.image == "nginx:latest"


def test_image_ref():
    img = ImageInfo.from_images({"ID": "sha", "Repository": "nginx", "Tag": "latest", "Size": "1MB"})
    assert img.ref == "nginx:latest"
    dangling = ImageInfo.from_images({"ID": "sha2", "Repository": "<none>", "Tag": "<none>"})
    assert dangling.ref == "sha2"


def test_classify_not_found():
    r = DockerResult("docker rm x", "", "Error: No such container: x", 1)
    c = classify_docker_result(r)
    assert c.kind == DockerErrorKind.NOT_FOUND


def test_classify_conflict():
    r = DockerResult("docker run", "", "Bind for 0.0.0.0:80 failed: port is already allocated", 125)
    c = classify_docker_result(r)
    assert c.kind == DockerErrorKind.CONFLICT


def test_docker_event_container():
    ev = DockerEvent.from_json({
        "time": "1",
        "Type": "container",
        "Action": "start",
        "Actor": {"Attributes": {"name": "api"}},
    })
    assert ev.affects_containers
    assert ev.name == "api"
