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

    def __init__(self, worker_shell_count: int = 3):
        super().__init__()
        self.credentials = CredentialStore()
        self.worker_shell_count = max(1, min(32, int(worker_shell_count or 3)))
        self.servers: dict[str, ManagedServer] = {}

    def add_server(self, profile: SSHProfile, auth: AuthMaterial | None = None):
        if profile.id in self.servers:
            return self.servers[profile.id]
        ssh = SSHServerConnection(profile, self.credentials, auth, worker_shell_count=self.worker_shell_count)
        managed = ManagedServer(profile, ssh)
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

    async def connect(self, pid: str):
        managed = self.servers[pid]
        self.state_changed.emit(pid, ConnectionState.CONNECTING.value)
        try:
            await managed.ssh.connect()
        except Exception:
            self.state_changed.emit(pid, managed.ssh.state.value)
            raise
        self.state_changed.emit(pid, ConnectionState.CONNECTED.value)

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
