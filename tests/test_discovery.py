from pathlib import Path
from app.ssh.discovery import discover_aliases


def test_discovery(tmp_path: Path):
    c = tmp_path / "config"
    c.write_text(
        "Host vps\n"
        "  HostName 203.0.113.10\n"
        "  User root\n"
        "  Port 2222\n"
        "  IdentityFile ~/.ssh/id_ed25519\n"
        "  ServerAliveInterval 20\n"
    )
    a = discover_aliases([c])
    assert a[0].alias == "vps"
    assert a[0].host == "203.0.113.10"
    assert a[0].port == 2222
    assert a[0].user == "root"
    assert a[0].server_alive_interval == 20


def test_discovery_include(tmp_path: Path):
    inc = tmp_path / "extra.conf"
    inc.write_text("Host included\n  HostName 10.0.0.5\n  User ubuntu\n")
    main = tmp_path / "config"
    main.write_text(f"Include {inc.name}\nHost vps\n  HostName 203.0.113.10\n  User root\n")
    a = discover_aliases([main])
    names = {x.alias for x in a}
    assert "included" in names
    assert "vps" in names
    by = {x.alias: x for x in a}
    assert by["included"].host == "10.0.0.5"
    assert by["included"].user == "ubuntu"


def test_discovery_skips_patterns(tmp_path: Path):
    c = tmp_path / "config"
    c.write_text("Host *\n  StrictHostKeyChecking yes\nHost real\n  HostName 1.2.3.4\n")
    a = discover_aliases([c])
    assert len(a) == 1
    assert a[0].alias == "real"
