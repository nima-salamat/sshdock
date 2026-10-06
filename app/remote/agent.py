from __future__ import annotations
import asyncio, json, shlex, struct, uuid
from dataclasses import dataclass

from app.core.runtime import create_task
from pathlib import Path

PROTOCOL=1
MAX_FRAME=4*1024*1024

@dataclass(slots=True)
class AgentCommandResult:
    stdout:str
    stderr:str
    exit_code:int

class AgentProtocolError(RuntimeError): pass

def encode_frame(value, max_size=MAX_FRAME):
    raw=json.dumps(value,ensure_ascii=False,separators=(",",":")).encode()
    if len(raw)>max_size: raise AgentProtocolError("agent frame too large")
    return struct.pack("!I",len(raw))+raw

def decode_frame(raw,max_size=MAX_FRAME):
    if len(raw)>max_size: raise AgentProtocolError("agent frame too large")
    try:
        value=json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentProtocolError("invalid agent frame") from exc
    if not isinstance(value,dict): raise AgentProtocolError("agent frame must be an object")
    return value

class RemoteAgentClient:
    def __init__(self,ssh):
        self.ssh=ssh
        self.connection=None
        self.listener=None
        self.reader=None
        self.writer=None
        self.process=None
        self.remote_path=None
        self.remote_port=None
        self.local_port=None
        self.token=None
        self._startup_stage="idle"
        self._reader_task=None
        self._write_lock=asyncio.Lock()
        self._pending={}
        self._seq=0
        self._closed=True

    @property
    def connected(self):
        return not self._closed and self.writer is not None and self.listener is not None and self.process is not None

    async def start(self):
        if self.connected:
            return
        self._closed=False
        self.connection=self.ssh.connection
        if self.connection is None or self.connection.is_closed():
            raise RuntimeError("SSH transport unavailable")
        self.remote_path=f"/tmp/sshdock-agent-{uuid.uuid4().hex[:16]}.py"
        self._startup_stage="python-probe"
        try:
            probe = await self.ssh.run_fast(
                "command -v python3 || command -v python || true",
                timeout=3,
            )
            python_bin = (probe.stdout or "").strip().splitlines()[0] if probe.stdout else ""
            if not python_bin:
                raise RuntimeError("SSHDock remote agent requires Python 3 on the server")

            self._startup_stage="bootstrap-upload"
            await self._upload()

            self._startup_stage="agent-process"
            cmd = f"exec {shlex.quote(python_bin)} -u {shlex.quote(self.remote_path)} --port 0"
            self.process=await self.connection.create_process(
                cmd,encoding="utf-8",errors="replace"
            )
            try:
                line=await asyncio.wait_for(self.process.stdout.readline(),4)
            except asyncio.TimeoutError as exc:
                err=""
                try:
                    err=await asyncio.wait_for(self.process.stderr.read(4096),0.25)
                except Exception:
                    pass
                detail=err.strip() if err else "no handshake received"
                raise RuntimeError(f"remote agent handshake timeout: {detail}") from exc
            if not line:
                err=""
                try:
                    err=(await asyncio.wait_for(self.process.stderr.read(4096),0.25)).strip()
                except Exception:
                    pass
                raise RuntimeError(f"remote agent exited before handshake{': '+err if err else ''}")
            try:
                hello=json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid remote agent handshake: {line[:300]!r}") from exc
            if int(hello.get("protocol",0))!=PROTOCOL:
                raise RuntimeError("remote agent protocol mismatch")
            self.remote_port=int(hello["port"])
            if not (1<=self.remote_port<=65535):
                raise RuntimeError("remote agent returned invalid port")
            self.token=str(hello["token"])

            self._startup_stage="ssh-forward"
            self.listener=await self.connection.forward_local_port(
                "127.0.0.1",0,"127.0.0.1",self.remote_port
            )
            self.local_port=int(self.listener.get_port())

            self._startup_stage="rpc-connect"
            self.reader,self.writer=await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1",self.local_port),4
            )
            self._reader_task=create_task(
                self._reader_loop(),name="remote-agent-reader",owner="remote-agent"
            )
            self._startup_stage="rpc-auth"
            await self.request(
                "hello",{"protocol":PROTOCOL,"token":self.token},timeout=4
            )
            self._startup_stage="ready"
        except Exception as exc:
            stage=self._startup_stage
            await self.close()
            raise RuntimeError(f"Remote agent startup failed during {stage}: {exc!r}") from exc

    async def _upload(self):
        src=Path(__file__).with_name("server.py").read_text(encoding="utf-8")
        p=await self.connection.create_process("cat > "+shlex.quote(self.remote_path),encoding="utf-8",errors="replace")
        p.stdin.write(src)
        p.stdin.write_eof()
        await asyncio.wait_for(p.wait_closed(),5)
        code=getattr(p,"exit_status",None)
        if code not in (None,0): raise RuntimeError(f"remote agent upload failed: {code}")

    async def request(self,method,params=None,timeout=15.0):
        if self.writer is None: raise RuntimeError("remote agent not connected")
        self._seq+=1
        rid=str(self._seq)
        fut=asyncio.get_running_loop().create_future()
        self._pending[rid]=fut
        async with self._write_lock:
            self.writer.write(encode_frame({"id":rid,"method":method,"params":params or {}}))
            await self.writer.drain()
        try:
            return await (asyncio.wait_for(fut,timeout) if timeout is not None else fut)
        finally:
            self._pending.pop(rid,None)

    async def _reader_loop(self):
        try:
            while True:
                h=await self.reader.readexactly(4)
                n=struct.unpack("!I",h)[0]
                if n<=0 or n>MAX_FRAME: raise AgentProtocolError("invalid frame size")
                msg=decode_frame(await self.reader.readexactly(n))
                fut=self._pending.get(str(msg.get("id","")))
                if fut is None or fut.done(): continue
                if msg.get("ok"): fut.set_result(msg.get("result"))
                else:
                    e=msg.get("error") or {}
                    fut.set_exception(AgentProtocolError(str(e.get("message") or "remote agent request failed")))
        except asyncio.CancelledError: pass
        except Exception as exc:
            for fut in tuple(self._pending.values()):
                if not fut.done(): fut.set_exception(exc)

    async def exec(self,command,timeout=None):
        r=await self.request("exec",{"command":command,"timeout":timeout},timeout=(timeout or 15)+2)
        if not isinstance(r,dict): raise AgentProtocolError("invalid exec result")
        return AgentCommandResult(str(r.get("stdout") or ""),str(r.get("stderr") or ""),int(r.get("exit_code",0)))

    async def close(self):
        if self._closed: return
        self._closed=True
        try:
            if self.writer is not None:
                try: await asyncio.wait_for(self.request("shutdown",{}),2.0)
                except Exception: pass
        finally:
            if self.writer is not None:
                try: self.writer.close(); await asyncio.wait_for(self.writer.wait_closed(),.5)
                except Exception: pass
            self.writer=None; self.reader=None
            if self._reader_task is not None:
                t=self._reader_task; self._reader_task=None
                if t is not asyncio.current_task():
                    t.cancel()
                    try: await asyncio.wait_for(t,.5)
                    except (asyncio.CancelledError,asyncio.TimeoutError): pass
            if self.listener is not None:
                try: self.listener.close(); await asyncio.wait_for(self.listener.wait_closed(),.8)
                except Exception: pass
                self.listener=None
            if self.process is not None:
                try: self.process.terminate()
                except Exception: pass
                try: await asyncio.wait_for(self.process.wait_closed(),1.0)
                except Exception: pass
                self.process=None
            if self.remote_path and self.connection is not None and not self.connection.is_closed():
                try:
                    await asyncio.wait_for(self.connection.run("rm -f "+shlex.quote(self.remote_path),check=False),2.0)
                except Exception: pass
            self._reject(RuntimeError("remote agent closed"))
            self.remote_path=None; self.remote_port=None; self.local_port=None; self.token=None; self._startup_stage="closed"

    def _reject(self,exc):
        for fut in tuple(self._pending.values()):
            if not fut.done(): fut.set_exception(exc)
        self._pending.clear()
