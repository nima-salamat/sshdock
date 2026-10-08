from __future__ import annotations

import asyncio
import logging
import re
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from app.ssh.client import SSHServerConnection, SSHChannel
from app.core.runtime import create_task

log = logging.getLogger(__name__)

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
            # Prepare mdocker outside the live PTY so its base64 payload is
            # never echoed by the interactive shell.
            await self._prepare_shell_integration()
            # Let the user's shell finish normal startup before augmenting it.
            await asyncio.sleep(0.15)
            await self._install_shell_integration()
        except Exception as exc:
            self.running = False
            self.channel = None
            self.status.emit(f"● Failed: {exc}")
            raise

    async def _prepare_shell_integration(self) -> None:
        from app.terminal.docker_pretty import prepare_command

        try:
            result = await self.ssh.run(prepare_command(), timeout=5.0)
            exit_code = getattr(result, "exit_code", getattr(result, "returncode", 0))
            if int(exit_code or 0) != 0:
                log.debug("SSHDock shell integration preparation returned %s", exit_code)
        except Exception as exc:
            log.debug("Could not prepare SSHDock shell integration: %s", exc, exc_info=True)

    async def _install_shell_integration(self):
        """Install SSHDock terminal integration on the already-running user shell.

        The shell itself is never replaced, so the user's normal startup files,
        aliases, functions, PATH, and installed prompt tooling remain available.
        """
        if not self.channel or self.channel.closed:
            return


        shell_name = ""
        try:
            shell_probe = await self.ssh.run_fast(
                "printf '%s' \"$(basename \"$SHELL\")\"",
                timeout=3.0,
            )
            shell_name = (getattr(shell_probe, "stdout", "") or "").strip().lower()
        except Exception:
            shell_name = ""

        if shell_name == "bash":
            setup = (
                "export TERM=\"xterm-256color\" COLORTERM=\"truecolor\" CLICOLOR=1; "
                "__sshdock_cwd(){ printf '\\033]7;file://sshdock%s\\007' \"$PWD\"; }; "
                "if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare -a'; then "
                "case \";${PROMPT_COMMAND[*]};\" in *\";__sshdock_cwd;\"*) ;; *) "
                "PROMPT_COMMAND=(\"__sshdock_cwd\" \"${PROMPT_COMMAND[@]}\");; esac; "
                "case \";${PROMPT_COMMAND[*]};\" in *\";__sshdock_apply_prompt;\"*) ;; *) "
                "PROMPT_COMMAND+=(\"__sshdock_apply_prompt\");; esac; "
                "else "
                "case \";${PROMPT_COMMAND:-};\" in *\";__sshdock_cwd;\"*) ;; *) "
                "if [ -n \"${PROMPT_COMMAND:-}\" ]; then PROMPT_COMMAND=\"__sshdock_cwd;${PROMPT_COMMAND}\"; "
                "else PROMPT_COMMAND=\"__sshdock_cwd\"; fi;; esac; "
                "case \";${PROMPT_COMMAND:-};\" in *\";__sshdock_apply_prompt;\"*) ;; *) "
                "if [ -n \"${PROMPT_COMMAND:-}\" ]; then PROMPT_COMMAND=\"${PROMPT_COMMAND};__sshdock_apply_prompt\"; "
                "else PROMPT_COMMAND=\"__sshdock_apply_prompt\"; fi;; esac; "
                "fi; "
                "__sshdock_apply_prompt(){ case \"${PS1:-}\" in *'@'*:*'# '|*'@'*:*'$ ') "
                "PS1='\\[\\033[1;32m\\]${debian_chroot:+($debian_chroot)}\\u\\[\\033[0;90m\\]@\\[\\033[1;36m\\]\\h\\[\\033[0;90m\\]:\\[\\033[1;34m\\]\\w\\[\\033[0m\\] \\[\\033[1;33m\\]\\$\\[\\033[0m\\] ';; esac; }; "
                "__sshdock_apply_prompt; "
                "alias ls >/dev/null 2>&1 || alias ls='ls --color=auto' 2>/dev/null; "
                "alias grep >/dev/null 2>&1 || alias grep='grep --color=auto' 2>/dev/null; "
                "alias fgrep >/dev/null 2>&1 || alias fgrep='fgrep --color=auto' 2>/dev/null; "
                "alias egrep >/dev/null 2>&1 || alias egrep='egrep --color=auto' 2>/dev/null; "
                "alias ll >/dev/null 2>&1 || alias ll='ls -alF --color=auto' 2>/dev/null; "
                "alias la >/dev/null 2>&1 || alias la='ls -A --color=auto' 2>/dev/null; "
                "command -v dircolors >/dev/null 2>&1 && eval \"$(dircolors -b 2>/dev/null)\"; "
            )
        elif shell_name == "zsh":
            setup = (
                "export TERM=\"xterm-256color\" COLORTERM=\"truecolor\" CLICOLOR=1; "
                "command -v dircolors >/dev/null 2>&1 && eval \"$(dircolors -b 2>/dev/null)\"; "
                "__sshdock_cwd(){ printf '\\033]7;file://sshdock%s\\007' \"$PWD\"; }; "
                "autoload -Uz add-zsh-hook 2>/dev/null; "
                "add-zsh-hook -d precmd __sshdock_cwd 2>/dev/null; "
                "add-zsh-hook precmd __sshdock_cwd; "
                "__sshdock_cwd; "
                "case \"${PROMPT:-}\" in ''|*'%n@%m:%~%# ') "
                "PROMPT='%F{green}%n%f%F{242}@%f%F{cyan}%m%f%F{242}:%f%F{blue}%~%f %F{yellow}%#%f '; "
                ";; esac; "
                "alias ls >/dev/null 2>&1 || alias ls='ls --color=auto' 2>/dev/null; "
                "alias grep >/dev/null 2>&1 || alias grep='grep --color=auto' 2>/dev/null; "
                "alias fgrep >/dev/null 2>&1 || alias fgrep='fgrep --color=auto' 2>/dev/null; "
                "alias egrep >/dev/null 2>&1 || alias egrep='egrep --color=auto' 2>/dev/null; "
                "alias ll >/dev/null 2>&1 || alias ll='ls -alF --color=auto' 2>/dev/null; "
                "alias la >/dev/null 2>&1 || alias la='ls -A --color=auto' 2>/dev/null; "
            )
        elif shell_name == "fish":
            setup = (
                "set -gx TERM xterm-256color; set -gx COLORTERM truecolor; set -gx CLICOLOR 1; "
                "functions -q ls; or function ls; command ls --color=auto $argv; end; "
                "functions -q ll; or function ll; command ls -alF --color=auto $argv; end; "
                "functions -q la; or function la; command ls -A --color=auto $argv; end; "
                "functions -q grep; or function grep; command grep --color=auto $argv; end; "
                "functions -q __sshdock_cwd; or function __sshdock_cwd --on-event fish_prompt; "
                "printf '\\033]7;file://sshdock%s\\007' \"$PWD\"; end; "
                "__sshdock_cwd; "

            )
        else:
            setup = ""
        try:
            from app.terminal.docker_pretty import install_command

            parts = [
                # ECHO is disabled at PTY allocation time, so this clears the
                # one startup prompt instead of leaving a duplicate prompt.
                "printf '\\r\\033[2K'",
            ]
            if setup:
                parts.append(setup)
            parts.append(install_command(shell_name=shell_name, manage_echo=False))
            if shell_name == "bash":
                # The complete bootstrap is one Bash history event. Remove it
                # before restoring normal terminal echo behavior.
                parts.append("history -d $HISTCMD 2>/dev/null || true")
            parts.append("stty echo 2>/dev/null")
            parts.append("printf '\\r\\033[K'")

            parts = [part.strip().rstrip(";") for part in parts if part.strip()]
            payload = "; ".join(parts) + "\r"
            await asyncio.wait_for(
                self.channel.write(payload.encode("utf-8")),
                timeout=8.0,
            )
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.channel and not self.channel.closed:
                try:
                    await asyncio.wait_for(
                        self.channel.write(b"stty echo 2>/dev/null\r"),
                        timeout=2.0,
                    )
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
