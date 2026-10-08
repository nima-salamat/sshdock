from __future__ import annotations

import sys

from app.core.models import ProxySettings, ProxyType, SSHProfile
from app.ssh.discovery import discover_aliases


def resolve_proxy_target(profile: SSHProfile) -> tuple[str, int]:
    """Resolve the real SSH endpoint needed by the local proxy bridge.

    AsyncSSH can resolve an SSH alias itself, but the local proxy process needs
    an actual hostname because SSH aliases are local configuration names.
    """
    host = str(profile.host or "").strip()
    port = int(profile.port or 22)

    if profile.alias:
        wanted = profile.alias.strip().lower()
        for alias in discover_aliases():
            if alias.alias.strip().lower() == wanted:
                host = str(alias.host or profile.alias).strip()
                if profile.port == 22 and alias.port:
                    port = int(alias.port)
                break

    return host, max(1, min(65535, port))


def validate_proxy_settings(settings: ProxySettings) -> str | None:
    if not settings.enabled:
        return None
    if not settings.host.strip():
        return "Proxy is enabled but no proxy host was configured."
    if not (1 <= int(settings.port) <= 65535):
        return "Proxy port must be between 1 and 65535."
    if settings.type not in (ProxyType.SOCKS5, ProxyType.HTTP_CONNECT):
        return f"Unsupported proxy type: {settings.type!s}"
    return None


def build_proxy_command(
    settings: ProxySettings,
    *,
    target_host: str,
    target_port: int,
) -> list[str] | None:
    """Build a portable AsyncSSH ProxyCommand using SSHDock's own bridge."""
    error = validate_proxy_settings(settings)
    if error:
        raise ValueError(error)
    if not settings.enabled:
        return None

    return [
        sys.executable,
        "-u",
        "-m",
        "app.ssh.proxy_bridge",
        "--type",
        settings.type.value,
        "--proxy-host",
        settings.host.strip(),
        "--proxy-port",
        str(int(settings.port)),
        "--target-host",
        target_host,
        "--target-port",
        str(int(target_port)),
    ]
