from __future__ import annotations

import asyncio
import logging
import os
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncssh
from asyncssh.pbe import KeyEncryptionError

from app.core.models import ConnectionState, SSHProfile, ConnectionErrorInfo
from app.core.runtime import create_task
from app.ssh.discovery import ssh_config_candidates
from app.ssh.errors import classify_exception
from app.storage.secure import CredentialStore

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# bcrypt availability probe.
#
# asyncssh declares ``bcrypt`` as an *optional* extra. If the user tries to
# load an encrypted OpenSSH-format private key (-----BEGIN OPENSSH PRIVATE
# KEY-----) without bcrypt installed, asyncssh raises:
#     KeyEncryptionError: OpenSSH private key encryption requires bcrypt
#     with KDF support
# We probe once at import time and expose the result so the connect() path
# can fail fast with an actionable message instead of a confusing
# "SSH connection failed" dialog after the TCP handshake already succeeded.
# ----------------------------------------------------------------------
def _probe_bcrypt() -> bool:
    try:
        import bcrypt  # type: ignore[import-not-found]

        return hasattr(bcrypt, "kdf")
    except ImportError:
        return False


_BCRYPT_AVAILABLE = _probe_bcrypt()


def _looks_like_encrypted_openssh_key(path: str) -> bool:
    """Return True if ``path`` is an OpenSSH-format private key that needs
    a passphrase to decrypt.

    Encrypted OpenSSH keys begin with ``-----BEGIN OPENSSH PRIVATE KEY-----``
    and their binary blob advertises a non-``none`` cipher. We do a cheap
    header-only check here; we deliberately avoid parsing the whole file
    (which would itself require bcrypt and re-raise the same error we are
    trying to detect).
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
        if b"BEGIN OPENSSH PRIVATE KEY" not in head:
            return False
        # Read enough of the body to inspect the cipher name. The base64
        # blob starts with "openssh-key-v1\0" followed by SSH-serialized
        # fields (cipher_name, kdf, kdf_data, ...). We only need a hint,
        # so look for the literal 'none' cipher token after the magic.
        import base64

        with open(path, "rb") as fh:
            pem = fh.read()
        body = b"".join(
            line for line in pem.split(b"\n")
            if line and not line.startswith(b"-----")
        )
        try:
            raw = base64.b64decode(body)
        except Exception:
            return False
        magic = b"openssh-key-v1\x00"
        if not raw.startswith(magic):
            return False
        # cipher_name is a uint32-length-prefixed string right after magic.
        idx = len(magic)
        if idx + 4 > len(raw):
            return False
        clen = int.from_bytes(raw[idx:idx + 4], "big")
        idx += 4
        if idx + clen > len(raw):
            return False
        cipher_name = raw[idx:idx + clen]
        # b"none" => unencrypted; anything else (aes256-ctr, aes256-gcm,
        # chacha20-poly1305@openssh.com, ...) => passphrase-encrypted.
        return cipher_name != b"none"
    except OSError:
        return False
    except Exception:
        # Never let the preflight probe raise — fall back to letting
        # asyncssh produce its own error if our sniff was inconclusive.
        return False


@dataclass
class AuthMaterial:
    password: str | None = None
    passphrase: str | None = None


@dataclass
class CommandResult:
    """Small result object compatible with the fields used by the UI/services."""

    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @property
    def returncode(self) -> int:
        return self.exit_code

    @property
    def exit_status(self) -> int:
        """AsyncSSH-compatible alias used by older callers."""
        return self.exit_code


class SSHClientProtocol(asyncssh.SSHClient):
    def __init__(self, cb=None):
        self._cb = cb

    async def validate_host_public_key(self, host, addr, port, key):
        if self._cb is None:
            return True
        fp = key.get_fingerprint("sha256")
        text = key.export_public_key().decode().strip()
        return await self._cb(host, addr, fp, text)


class SSHChannel:
    def __init__(self, conn, process):
        self.connection = conn
        self.process = process
        self.closed = False

    async def write(self, data: bytes | str):
        if self.closed:
            return
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        self.process.stdin.write(text)
        await self.process.stdin.drain()

    async def resize(self, cols, rows):
        if not self.closed and hasattr(self.process, "change_terminal_size"):
            self.process.change_terminal_size(cols, rows)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.write_eof()
        except Exception:
            pass
        try:
            self.process.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.process.wait_closed(), timeout=0.6)
        except Exception:
            pass


class _ShellWorker:
    """One persistent `sh` channel used by background SSH commands."""

    def __init__(self, owner: "SSHServerConnection", index: int):
        self.owner = owner
        self.index = index
        self.process = None
        self.lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.process is not None

    async def start(self) -> None:
        if self.alive:
            return
        if not self.owner.is_connected:
            raise RuntimeError("SSH connection is not established")
        self.process = await asyncio.wait_for(
            self.owner.connection.create_process(
                "sh", encoding="utf-8", errors="replace"
            ),
            timeout=max(10.0, self.owner.profile.connect_timeout + 5.0),
        )

    async def _dispose_process(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            process.stdin.write_eof()
        except Exception:
            pass
        try:
            process.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(process.wait_closed(), timeout=0.6)
        except Exception:
            pass

    async def close(self) -> None:
        async with self.lock:
            await self._dispose_process()

    async def run(self, command: str, timeout: float | None = None) -> CommandResult:
        """Run a command inside the already-open shell. Caller owns lock."""
        for attempt in range(2):
            if not self.alive:
                await self.start()

            token = uuid.uuid4().hex
            marker = f"__SSHDockWorker_{token}__"
            script = (
                "{ "
                + command
                + "; __sshdock_rc=$?; "
                + f"printf '\\n{marker}:%s\\n' \"$__sshdock_rc\"; "
                + "} 2>&1\n"
            )

            try:
                self.process.stdin.write(script)
                await self.process.stdin.drain()
                lines: list[str] = []
                while True:
                    line_coro = self.process.stdout.readline()
                    line = (
                        await asyncio.wait_for(line_coro, timeout)
                        if timeout is not None
                        else await line_coro
                    )
                    if not line:
                        raise asyncssh.ConnectionLost("Persistent shell closed")
                    stripped = line.strip()
                    if stripped.startswith(marker + ":"):
                        try:
                            exit_code = int(stripped.rsplit(":", 1)[1])
                        except (TypeError, ValueError):
                            exit_code = 1
                        output = "".join(lines)
                        return CommandResult(
                            command=command,
                            stdout=output,
                            stderr=output if exit_code else "",
                            exit_code=exit_code,
                        )
                    lines.append(line)
            except asyncio.CancelledError:
                # A cancelled page refresh may interrupt readline() while a
                # command is still running in this persistent shell. Reuse of
                # that shell would expose partial/stale output to the next
                # command and can make tables appear empty or hang. Tear the
                # worker down before propagating cancellation so the next
                # caller gets a clean shell.
                await self._dispose_process()
                raise
            except (asyncio.TimeoutError, asyncssh.ConnectionLost, asyncssh.ChannelOpenError, OSError):
                await self._dispose_process()
                if attempt == 0 and self.owner.is_connected:
                    continue
                raise
        raise RuntimeError("Persistent SSH worker failed")


class SSHServerConnection:
    def __init__(self, profile, credentials, auth=None, worker_shell_count: int = 3):
        self.profile = profile
        self.credentials = credentials
        self.auth = auth or AuthMaterial()
        self.connection = None
        self.state = ConnectionState.DISCONNECTED
        self.channels = set()
        self._lock = asyncio.Lock()
        self._close_requested = False
        self._host_key_cb = None
        self._worker_shell_count = max(1, int(worker_shell_count or 3))
        self._workers: list[_ShellWorker] = []
        self._workers_condition = asyncio.Condition()
        self._worker_rr = 0
        self._workers_ready = asyncio.Event()
        # Page/data loaders use this bounded execution path so the UI
        # concurrency follows the same worker count configured in Settings.
        self._page_exec_active = 0
        self._page_exec_condition = asyncio.Condition()
        self._processes: dict[int, Any] = {}
        self._process_seq = 0
        self._process_lock = asyncio.Lock()

    @property
    def is_connected(self):
        return self.connection is not None and not self.connection.is_closed()

    @property
    def worker_shell_count(self) -> int:
        return self._worker_shell_count

    def set_worker_shell_count_limit(self, count: int) -> int:
        """Apply the configured concurrency limit immediately.

        The actual persistent SSH shell pool can be resized asynchronously, but
        page-data scheduling must see the new limit immediately so an Apply in
        Settings cannot race with a page refresh and keep using the old value.
        """
        self._worker_shell_count = max(1, min(32, int(count)))
        return self._worker_shell_count

    def _known_hosts(self):
        cand = [Path.home() / ".ssh" / "known_hosts", Path.home() / ".ssh" / "known_hosts2"]
        return [str(p) for p in cand if p.exists()]

    def _keys(self):
        vals = []
        if self.profile.private_key:
            vals.append(os.path.expanduser(self.profile.private_key))
        vals.extend(os.path.expanduser(x) for x in self.profile.identity_files)
        return list(dict.fromkeys(vals))

    def _cred(self, kind):
        if kind == "password" and self.auth.password is not None:
            return self.auth.password
        if kind == "passphrase" and self.auth.passphrase is not None:
            return self.auth.passphrase
        return self.credentials.get(self.profile.id, kind) if self.profile.persist_secrets else None

    def _connect_kwargs(self):
        async def validate(host, addr, fp, text):
            if self._host_key_cb is None:
                return True
            return await self._host_key_cb(host, addr, self.profile.port, fp, text)

        def factory():
            return SSHClientProtocol(validate)

        return {
            "username": self.profile.username or None,
            "port": self.profile.port,
            "password": self._cred("password"),
            "client_keys": self._keys() or None,
            "passphrase": self._cred("passphrase"),
            "known_hosts": self._known_hosts(),
            "client_factory": factory,
            "connect_timeout": self.profile.connect_timeout,
            "keepalive_interval": self.profile.keepalive,
            "keepalive_count_max": self.profile.server_alive_count_max,
            "agent_path": os.environ.get("SSH_AUTH_SOCK") or None,
            "config": [str(p) for p in ssh_config_candidates()] if self.profile.alias else None,
        }

    async def _start_worker_pool(self) -> None:
        if not self.is_connected:
            return
        self._workers_ready.clear()
        while len(self._workers) < self._worker_shell_count:
            worker = _ShellWorker(self, len(self._workers))
            try:
                await worker.start()
            except Exception as exc:
                await worker.close()
                # Do not fail an otherwise healthy SSH connection just because the
                # server advertises a lower MaxSessions limit than our target pool.
                log.warning(
                    "Could not open SSH worker shell %s/%s for %s: %s",
                    len(self._workers) + 1,
                    self._worker_shell_count,
                    self.profile.name,
                    exc,
                )
                break
            self._workers.append(worker)
        self._workers_ready.set()
        if not self._workers:
            raise RuntimeError("SSH connected, but no worker shell could be opened")
        log.debug("SSH worker shell pool ready: %s workers for %s", len(self._workers), self.profile.name)

    async def resize_worker_pool(self, count: int) -> None:
        count = max(1, min(32, int(count)))
        self._worker_shell_count = count
        async with self._page_exec_condition:
            self._page_exec_condition.notify_all()
        if not self.is_connected:
            return
        if count > len(self._workers):
            await self._start_worker_pool()
            return
        # Keep busy workers alive and close only idle extras.
        for worker in list(self._workers[count:]):
            if worker.lock.locked():
                continue
            self._workers.remove(worker)
            await worker.close()
            if len(self._workers) <= count:
                break

    async def _acquire_worker(self) -> _ShellWorker:
        while True:
            async with self._workers_condition:
                idle = [worker for worker in self._workers if not worker.lock.locked()]
                if idle:
                    worker = idle[self._worker_rr % len(idle)]
                    self._worker_rr += 1
                    await worker.lock.acquire()
                    return worker
                await self._workers_condition.wait()

    async def _run_with_worker(self, command: str, timeout: float | None = None) -> CommandResult:
        worker = await self._acquire_worker()
        try:
            return await worker.run(command, timeout)
        finally:
            worker.lock.release()
            async with self._workers_condition:
                self._workers_condition.notify_all()

    @property
    def live_processes(self) -> list[Any]:
        return [p for p in self._processes.values() if p is not None]

    async def close_processes(self) -> None:
        processes = list(self._processes.items())
        self._processes.clear()
        for _, proc in processes:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait_closed(), timeout=1.0)
            except Exception:
                pass

    def _track_process(self, process: Any) -> Any:
        self._process_seq += 1
        pid = self._process_seq
        self._processes[pid] = process
        create_task(self._watch_process(pid, process), name="ssh-process-watch", owner="ssh-process")
        return process

    async def _watch_process(self, pid: int, process: Any) -> None:
        try:
            await process.wait_closed()
        except Exception:
            pass
        finally:
            self._processes.pop(pid, None)

    async def clone_transport(self, *, with_command_transport: bool = False):
        """Open an independent SSH transport for a long-lived interactive terminal."""
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        clone = SSHServerConnection(
            self.profile,
            self.credentials,
            self.auth,
            worker_shell_count=self._worker_shell_count,
        )
        clone._host_key_cb = self._host_key_cb
        await clone.connect(host_key_cb=self._host_key_cb, with_worker_pool=False, with_command_transport=False if not with_command_transport else True)
        return clone

    async def connect(self, host_key_cb: Callable | None = None, with_worker_pool: bool = True, with_command_transport: bool = True):
        async with self._lock:
            if self.is_connected:
                if with_worker_pool and not self._workers:
                    await self._start_worker_pool()
                return
            self._close_requested = False
            self.state = ConnectionState.CONNECTING
            self._host_key_cb = host_key_cb
            target = self.profile.alias or self.profile.host

            # Preflight: if any of the configured private keys is an
            # encrypted OpenSSH key but the ``bcrypt`` package is not
            # importable (or has no ``kdf`` function), fail fast with an
            # actionable message. asyncssh would otherwise only discover
            # this *after* the TCP handshake and surface it as a generic
            # "SSH connection failed" error.
            if not _BCRYPT_AVAILABLE:
                offending = next(
                    (k for k in self._keys() if _looks_like_encrypted_openssh_key(k)),
                    None,
                )
                if offending is not None:
                    self.state = ConnectionState.ERROR
                    title = "Missing bcrypt dependency"
                    reason = (
                        f"The private key '{offending}' is an encrypted "
                        "OpenSSH key, which requires the 'bcrypt' Python "
                        "package to decrypt. Install it with:  "
                        "pip install bcrypt   (or pip install asyncssh[bcrypt])  "
                        "and reconnect."
                    )
                    raise RuntimeError(
                        ConnectionErrorInfo(title, title, reason, retryable=False)
                    )

            try:
                self.connection = await asyncio.wait_for(
                    asyncssh.connect(target, **self._connect_kwargs()),
                    timeout=self.profile.connect_timeout + 2,
                )
                self.state = ConnectionState.CONNECTED
                if with_worker_pool:
                    await self._start_worker_pool()
            except Exception as exc:
                if self.connection:
                    self.connection.close()
                    self.connection = None
                await self._close_workers()
                if isinstance(exc, asyncssh.HostKeyNotVerifiable):
                    self.state = ConnectionState.HOST_KEY_ERROR
                elif isinstance(exc, asyncssh.PermissionDenied):
                    self.state = ConnectionState.AUTH_FAILED
                elif isinstance(exc, TimeoutError):
                    self.state = ConnectionState.TIMEOUT
                else:
                    self.state = ConnectionState.ERROR
                title, reason, retryable = classify_exception(exc)
                raise RuntimeError(
                    ConnectionErrorInfo(title, title, reason, retryable=retryable)
                ) from exc

    async def open_shell(self, cols=120, rows=32):
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        create = self.connection.create_process(
            term_type="xterm-256color",
            term_size=(cols, rows),
            encoding="utf-8",
            errors="replace",
        )
        p = await asyncio.wait_for(create, timeout=max(10.0, self.profile.connect_timeout + 5.0))
        c = SSHChannel(self, p)
        self.channels.add(c)
        return c

    async def run(self, command, timeout=None):
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        if not self._workers:
            await self._start_worker_pool()
        return await self._run_with_worker(command, timeout)

    async def run_page(self, command, timeout=None):
        """Run a page-data command through the real configured worker pool.

        The worker pool itself is the queue: when all configured workers are
        busy, the coroutine waits until one becomes available. This means every
        page request is bounded by the same Settings value and no page loader
        silently bypasses the scheduler by opening an extra SSH channel.
        """
        return await self.run(command, timeout)

    async def run_fast(self, command, timeout=None):
        """Run a command via a fresh ``ssh.exec`` channel, bypassing the
        persistent worker shell pool.

        Use this for commands that need to run *now* without waiting for
        a worker shell to become free. Each call opens a new SSH channel,
        runs the command, and closes the channel — so there's no
        contention with the worker pool.

        Trade-off: slightly higher per-command overhead (channel setup
        takes ~10-50ms) but true parallelism — up to the server's
        MaxSessions limit (typically 10+ on OpenSSH).

        Use ``run()`` for the common case (background refreshes, data
        fetching). Use ``run_fast()`` when the caller is already holding
        a worker shell via ``run()`` and needs to run a *dependent*
        command without deadlocking the pool.
        """
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        # Prefer connection.run() — portable across asyncssh versions.
        # SSHClientProcess has no reliable ``collect()`` on all releases.
        try:
            if hasattr(self.connection, "run"):
                coro = self.connection.run(command, check=False, encoding="utf-8", errors="replace")
                result = await asyncio.wait_for(coro, timeout=timeout) if timeout is not None else await coro
                stdout = getattr(result, "stdout", "") or ""
                stderr = getattr(result, "stderr", "") or ""
                exit_status = getattr(result, "exit_status", None)
                if exit_status is None:
                    exit_status = getattr(result, "returncode", 0)
                return CommandResult(
                    command=command,
                    stdout=stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace"),
                    stderr=stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace"),
                    exit_code=int(exit_status) if exit_status is not None else 0,
                )
        except asyncio.TimeoutError:
            raise TimeoutError(f"SSH command timed out after {timeout}s: {command[:80]}")

        # Fallback: create_process + read streams + wait
        try:
            proc = await asyncio.wait_for(
                self.connection.create_process(command, encoding="utf-8", errors="replace"),
                timeout=max(10.0, (timeout or 30) + 5.0),
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"SSH exec channel open timed out for: {command[:80]}")

        async def _read_stream(stream):
            if stream is None:
                return ""
            try:
                data = await stream.read()
            except Exception:
                return ""
            if data is None:
                return ""
            return data if isinstance(data, str) else data.decode("utf-8", "replace")

        async def _gather():
            out_t = asyncio.create_task(_read_stream(getattr(proc, "stdout", None)))
            err_t = asyncio.create_task(_read_stream(getattr(proc, "stderr", None)))
            stdout_s, stderr_s = await asyncio.gather(out_t, err_t)
            try:
                await proc.wait()
            except Exception:
                pass
            exit_s = getattr(proc, "exit_status", None)
            if exit_s is None:
                exit_s = getattr(proc, "returncode", 0)
            return stdout_s, stderr_s, exit_s

        try:
            stdout, stderr, exit_status = (
                await asyncio.wait_for(_gather(), timeout=timeout)
                if timeout is not None
                else await _gather()
            )
        except asyncio.TimeoutError:
            try:
                if hasattr(proc, "terminate"):
                    proc.terminate()
                elif hasattr(proc, "kill"):
                    proc.kill()
            except Exception:
                pass
            raise TimeoutError(f"SSH command timed out after {timeout}s: {command[:80]}")
        try:
            ch = getattr(proc, "channel", None)
            if ch is not None:
                ch.close()
        except Exception:
            pass
        return CommandResult(
            command=command,
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=int(exit_status) if exit_status is not None else 0,
        )

    async def start_command(self, command):
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        proc = await self.connection.create_process(command, encoding="utf-8")
        return self._track_process(proc)

    async def start_pty_command(self, command, cols=120, rows=32):
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        create = self.connection.create_process(
            command,
            term_type="xterm-256color",
            term_size=(cols, rows),
            encoding="utf-8",
            errors="replace",
        )
        proc = await asyncio.wait_for(create, timeout=max(10.0, self.profile.connect_timeout + 5.0))
        return self._track_process(proc)

    async def sftp(self):
        if not self.is_connected:
            raise RuntimeError("SSH connection is not established")
        return await self.connection.start_sftp_client()

    async def close_channel(self, ch):
        await ch.close()
        self.channels.discard(ch)

    async def _close_workers(self):
        workers = tuple(self._workers)
        self._workers.clear()
        self._workers_ready.clear()
        if workers:
            await asyncio.gather(*(worker.close() for worker in workers), return_exceptions=True)

    async def close(self):
        self._close_requested = True
        for ch in tuple(self.channels):
            await self.close_channel(ch)
        await self._close_workers()
        await self.close_processes()
        if self.connection:
            self.connection.close()
            try:
                await self.connection.wait_closed()
            except Exception:
                pass
        self.connection = None
        self.state = ConnectionState.DISCONNECTED
