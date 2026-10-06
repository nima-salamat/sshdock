from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from app.ssh.client import SSHServerConnection, SSHChannel
from app.core.runtime import create_task

# OSC 7: ESC ] 7 ; <payload> BEL   or   ESC ] 7 ; <payload> ESC \
_OSC7_RE = re.compile(
    rb"\x1b\]7;([^\x07\x1b]*)(?:\x07|\x1b\\)"
)
_OSC_MAGIC_RE = re.compile(
    rb"\x1b\]777;sshdock-magic;([^\x07\x1b]*)(?:\x07|\x1b\\)"
)


class TerminalSession(QObject):
    output = Signal(bytes)
    status = Signal(str)
    closed = Signal(str)
    cwd_changed = Signal(str)  # emitted when shell reports a new working directory
    magic_command = Signal(str)  # mdocker / docker --magic

    def __init__(self, ssh: SSHServerConnection, title: str, owned_transport: SSHServerConnection | None = None):
        super().__init__()
        self.id = uuid4().hex
        self.title = title
        self.ssh = ssh
        self.owned_transport = owned_transport
        self.channel: SSHChannel | None = None
        self.reader_task: asyncio.Task | None = None
        self.running = False
        self._closing = False
        self._closed_emitted = False
        self._close_lock = asyncio.Lock()
        self.cwd = "/"
        self._osc_buf = b""  # carry partial OSC sequences across reads

    async def start(self):
        if self.running or self.channel:
            return
        self._closing = False
        self.status.emit("● Connecting…")
        try:
            # Open the user's normal interactive shell first. This preserves
            # ~/.bashrc, ~/.zshrc, aliases, functions, PATH customizations,
            # prompt plugins and other user shell integration.
            self.channel = await self.ssh.open_shell()
            self.running = True
            self.status.emit("● Connected")
            self.reader_task = create_task(
                self._reader(),
                name=f"terminal-reader-{self.id[:8]}",
                owner="terminal",
            )
            # Let the user's shell finish normal startup before augmenting it.
            await asyncio.sleep(0.15)
            await self._install_shell_integration()
        except Exception as exc:
            self.running = False
            self.channel = None
            self.status.emit(f"● Failed: {exc}")
            raise

    async def _install_shell_integration(self):
        """Install SSHDock terminal integration on the already-running user shell.

        The shell itself is never replaced, so the user's normal startup files,
        aliases, functions, PATH, and installed prompt tooling remain available.
        """
        if not self.channel or self.channel.closed:
            return

        setup = (
            "stty -echo 2>/dev/null; "
            "export TERM=xterm-256color COLORTERM=truecolor; "
            "export CLICOLOR=1; "
            "__sshdock_cwd(){ printf '\\033]7;file://sshdock%s\\007' \"$PWD\"; }; "
            "PROMPT_COMMAND=\"__sshdock_cwd${PROMPT_COMMAND:+;$PROMPT_COMMAND}\"; "
            "precmd_functions=(__sshdock_cwd); "
            "PS1='\\[\\033[01;32m\\]\\u@\\h\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '; "
            "alias ls='ls --color=auto' 2>/dev/null; "
            "alias grep='grep --color=auto' 2>/dev/null; "
            "alias fgrep='fgrep --color=auto' 2>/dev/null; "
            "alias egrep='egrep --color=auto' 2>/dev/null; "
            "alias ll='ls -alF --color=auto' 2>/dev/null; "
            "alias la='ls -A --color=auto' 2>/dev/null; "
            "command -v dircolors >/dev/null 2>&1 && eval \"$(dircolors -b 2>/dev/null)\"; "
            "__sshdock_cwd; "
            "stty echo 2>/dev/null; "
            "printf '\\r\\033[K'\\r"
        )
        try:
            await asyncio.wait_for(self.channel.write(setup.encode("utf-8")), timeout=2.5)
        except Exception:
            pass
        try:
            from app.terminal.docker_pretty import install_command
            pretty = install_command() + "\r"
            await asyncio.sleep(0.05)
            await asyncio.wait_for(self.channel.write(pretty.encode("utf-8")), timeout=3.0)
        except Exception:
            pass

    def _extract_cwd(self, data: bytes) -> bytes:
        """Parse OSC 7 (cwd) + OSC 777 magic; strip magic from stream."""
        blob = self._osc_buf + data
        self._osc_buf = b""
        for marker in (b"\x1b]7;", b"\x1b]777;"):
            if marker in blob[-80:]:
                tail_start = blob.rfind(marker)
                incomplete = blob[tail_start:]
                if b"\x07" not in incomplete and b"\x1b\\" not in incomplete:
                    self._osc_buf = incomplete
                    blob = blob[:tail_start]
                    break
        for match in _OSC7_RE.finditer(blob):
            raw = match.group(1).decode("utf-8", "replace")
            path = self._normalize_osc_path(raw)
            if path and path != self.cwd:
                self.cwd = path
                self.cwd_changed.emit(path)
        cleaned = blob
        if "_OSC_MAGIC_RE" in globals() or True:
            for match in reversed(list(_OSC_MAGIC_RE.finditer(blob))):
                payload = match.group(1).decode("utf-8", "replace").strip()
                if payload:
                    self.magic_command.emit(payload)
                cleaned = cleaned[: match.start()] + cleaned[match.end() :]
        return cleaned

    @staticmethod
    def _normalize_osc_path(raw: str) -> str:
        value = (raw or "").strip()
        if value.startswith("file://"):
            value = value[len("file://"):]
            slash = value.find("/")
            value = value[slash:] if slash >= 0 else "/"
        if not value.startswith("/"):
            value = "/" + value
        # collapse // and strip trailing slash (except root)
        while "//" in value:
            value = value.replace("//", "/")
        if len(value) > 1 and value.endswith("/"):
            value = value[:-1]
        return value or "/"

    async def _reader(self):
        try:
            while self.running and not self._closing:
                channel = self.channel
                if channel is None or channel.closed:
                    break
                data = await channel.process.stdout.read(4096)
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode("utf-8", "replace")
                try:
                    data = self._extract_cwd(data)
                except Exception:
                    pass
                if data:
                    self.output.emit(data)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if not self._closing:
                self.status.emit(f"● Disconnected: {exc}")
        finally:
            if not self._closing:
                self.running = False
                self.status.emit("● Disconnected")

    async def write(self, data: bytes):
        if self._closing or not self.channel or self.channel.closed or not self.running:
            return
        await asyncio.wait_for(self.channel.write(data), timeout=5.0)

    async def resize(self, cols: int, rows: int):
        if self._closing or not self.channel or self.channel.closed:
            return
        cols, rows = max(20, int(cols)), max(5, int(rows))
        last = getattr(self, "_last_size", None)
        if last == (cols, rows):
            return
        try:
            await asyncio.wait_for(self.channel.resize(cols, rows), timeout=3.0)
            self._last_size = (cols, rows)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return

    async def close(self):
        async with self._close_lock:
            if self._closed_emitted:
                return
            self._closing = True
            self.running = False
            current = asyncio.current_task()
            task = self.reader_task
            self.reader_task = None
            if task and task is not current:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception:
                    pass

            channel = self.channel
            self.channel = None
            if channel is not None:
                try:
                    await asyncio.wait_for(self.ssh.close_channel(channel), timeout=1.0)
                except Exception:
                    pass


            if self.owned_transport is not None:
                try:
                    await asyncio.wait_for(self.owned_transport.close(), timeout=1.5)
                except Exception:
                    pass
                self.owned_transport = None

            self.status.emit("● Closed")
            self._closed_emitted = True
            self.closed.emit(self.id)
