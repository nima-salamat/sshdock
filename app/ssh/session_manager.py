from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from app.core.models import ConnectionState, SSHProfile
from app.ssh.client import AuthMaterial, SSHServerConnection
from app.storage.secure import CredentialStore
from app.terminal.session import TerminalSession


@dataclass
class ManagedServer:
    profile: SSHProfile
    ssh: SSHServerConnection
    terminals: dict[str, TerminalSession] = field(default_factory=dict)
    reconnect_task: asyncio.Task | None = None
    terminal_open_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager(QObject):
    server_added = Signal(str, str)
    server_removed = Signal(str)
    state_changed = Signal(str, str)
    terminal_added = Signal(str, str)
    terminal_removed = Signal(str, str)

    def __init__(self, worker_shell_count: int = 3, remote_agent_enabled: bool = False):
        super().__init__()
        self.credentials = CredentialStore()
        self.worker_shell_count = max(1, min(32, int(worker_shell_count or 3)))
        self.remote_agent_enabled = bool(remote_agent_enabled)
        self.servers: dict[str, ManagedServer] = {}

    def add_server(self, profile: SSHProfile, auth: AuthMaterial | None = None):
        if profile.id in self.servers:
            return self.servers[profile.id]
        ssh = SSHServerConnection(profile, self.credentials, auth, worker_shell_count=self.worker_shell_count, remote_agent_enabled=self.remote_agent_enabled)
        managed = ManagedServer(profile, ssh)
        ssh.connection_lost.connect(lambda pid=profile.id: self._handle_connection_lost(pid))
        self.servers[profile.id] = managed
        self.server_added.emit(profile.id, profile.name)
        return managed

    def set_worker_shell_count_limit(self, count: int) -> int:
        """Apply the concurrency limit synchronously to current/future servers."""
        self.worker_shell_count = max(1, min(32, int(count)))
        for managed in self.servers.values():
            managed.ssh.set_worker_shell_count_limit(self.worker_shell_count)
        return self.worker_shell_count

    async def set_worker_shell_count(self, count: int) -> None:
        """Change the global SSH worker-shell pool size for current/future servers.

        The limit is updated synchronously by the caller before this coroutine
        starts; this method then performs the slower physical shell-pool resize.
        """
        self.set_worker_shell_count_limit(count)
        await asyncio.gather(
            *(managed.ssh.resize_worker_pool(self.worker_shell_count) for managed in self.servers.values() if managed.ssh.is_connected),
            return_exceptions=True,
        )

    async def set_remote_agent_enabled(self, enabled: bool) -> None:
        """Enable/disable the optional per-connection remote RPC accelerator."""
        self.remote_agent_enabled = bool(enabled)
        await asyncio.gather(
            *(managed.ssh.set_remote_agent_enabled(self.remote_agent_enabled)
              for managed in self.servers.values() if managed.ssh.is_connected),
            return_exceptions=True,
        )

    async def connect(self, pid: str):
        managed = self.servers[pid]
        self.state_changed.emit(pid, ConnectionState.CONNECTING.value)
        try:
            await managed.ssh.connect()
        except Exception:
            self.state_changed.emit(pid, managed.ssh.state.value)
            raise
        self.state_changed.emit(pid, ConnectionState.CONNECTED.value)

    def _handle_connection_lost(self, pid: str) -> None:
        """Start one automatic reconnect loop after an unexpected transport loss."""
        managed = self.servers.get(pid)
        if managed is None:
            return
        self.state_changed.emit(pid, ConnectionState.DISCONNECTED.value)
        if not managed.profile.auto_reconnect:
            return
        current = managed.reconnect_task
        if current is not None and not current.done():
            return
        managed.reconnect_task = asyncio.create_task(
            self._auto_reconnect(pid),
            name=f"ssh-auto-reconnect-{pid[:8]}",
        )

    async def _auto_reconnect(self, pid: str) -> None:
        managed = self.servers.get(pid)
        if managed is None:
            return
        delay = max(0.2, float(managed.profile.retry_delay or 0.2))
        retries = max(1, int(managed.profile.max_retries or 1))
        try:
            for attempt in range(1, retries + 1):
                managed = self.servers.get(pid)
                if managed is None:
                    return
                if managed.ssh.is_connected:
                    self.state_changed.emit(pid, ConnectionState.CONNECTED.value)
                    return
                self.state_changed.emit(pid, ConnectionState.RECONNECTING.value)
                try:
                    await managed.ssh.connect()
                    self.state_changed.emit(pid, ConnectionState.CONNECTED.value)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt >= retries:
                        self.state_changed.emit(pid, managed.ssh.state.value)
                        return
                    import logging
                    logging.getLogger(__name__).warning(
                        "Auto-reconnect attempt %s/%s failed for %s: %s",
                        attempt,
                        retries,
                        managed.profile.name,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2.0, 30.0)
        finally:
            managed = self.servers.get(pid)
            if managed is not None and managed.reconnect_task is asyncio.current_task():
                managed.reconnect_task = None

    async def open_terminal(self, pid: str, title: str | None = None):
        managed = self.servers[pid]
        if not managed.ssh.is_connected:
            raise RuntimeError("Server is not connected")

        async with managed.terminal_open_lock:
            terminal_transport = await managed.ssh.clone_transport()
            terminal = TerminalSession(
                terminal_transport,
                title or f"Terminal {len(managed.terminals) + 1}",
                owned_transport=terminal_transport,
            )
            managed.terminals[terminal.id] = terminal
            terminal.closed.connect(lambda tid: self._terminal_closed(pid, tid))
            try:
                await terminal.start()
            except Exception:
                managed.terminals.pop(terminal.id, None)
                try:
                    await terminal_transport.close()
                except Exception:
                    pass
                raise

            self.terminal_added.emit(pid, terminal.id)
            return terminal

    def _terminal_closed(self, pid: str, tid: str):
        managed = self.servers.get(pid)
        if managed and tid in managed.terminals:
            managed.terminals.pop(tid, None)
            self.terminal_removed.emit(pid, tid)

    async def close_terminal(self, pid: str, tid: str):
        managed = self.servers.get(pid)
        terminal = managed.terminals.get(tid) if managed else None
        if terminal:
            await terminal.close()

    async def disconnect(self, pid: str):
        managed = self.servers.get(pid)
        if not managed:
            return
        reconnect_task = managed.reconnect_task
        managed.reconnect_task = None
        if reconnect_task is not None and reconnect_task is not asyncio.current_task() and not reconnect_task.done():
            reconnect_task.cancel()
            try:
                await asyncio.wait_for(reconnect_task, timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        terminals = tuple(managed.terminals.values())
        if terminals:
            await asyncio.gather(*(terminal.close() for terminal in terminals), return_exceptions=True)
        try:
            await asyncio.wait_for(managed.ssh.close(), timeout=2.0)
        except Exception:
            pass
        self.state_changed.emit(pid, ConnectionState.DISCONNECTED.value)

    async def reconnect(self, pid: str):
        managed = self.servers[pid]
        await self.disconnect(pid)
        delay = max(0.2, managed.profile.retry_delay)
        for attempt in range(1, managed.profile.max_retries + 1):
            self.state_changed.emit(pid, ConnectionState.RECONNECTING.value)
            try:
                await managed.ssh.connect()
                self.state_changed.emit(pid, ConnectionState.CONNECTED.value)
                return
            except Exception:
                if attempt >= managed.profile.max_retries:
                    self.state_changed.emit(pid, managed.ssh.state.value)
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def remove_server(self, pid: str):
        await self.disconnect(pid)
        self.servers.pop(pid, None)
        self.server_removed.emit(pid)
