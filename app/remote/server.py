#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, json, os, platform, secrets, struct, sys, time

PROTOCOL=1
MAX_FRAME=4*1024*1024
MAX_CONCURRENCY=32
IDLE_TIMEOUT=45.0

def frame(v):
    raw=json.dumps(v,ensure_ascii=False,separators=(",",":")).encode()
    if len(raw)>MAX_FRAME: raise ValueError("frame too large")
    return struct.pack("!I",len(raw))+raw

async def read_frame(r):
    h=await r.readexactly(4)
    n=struct.unpack("!I",h)[0]
    if n<=0 or n>MAX_FRAME: raise ValueError("invalid frame size")
    v=json.loads((await r.readexactly(n)).decode())
    if not isinstance(v,dict): raise ValueError("frame must be object")
    return v

class Agent:
    def __init__(self, token, idle):
        self.token=token
        self.idle=max(5.0,float(idle))
        self.sem=asyncio.Semaphore(MAX_CONCURRENCY)
        self.auth=False
        self.server=None
        self.stop=asyncio.Event()

    async def handle(self,r,w):
        write_lock=asyncio.Lock()
        tasks=set()

        async def write_response(payload):
            async with write_lock:
                w.write(frame(payload))
                await w.drain()

        async def process(q):
            rid=str(q.get("id",""))
            try:
                result=await self.dispatch(str(q.get("method","")),q.get("params") or {})
                await write_response({"id":rid,"ok":True,"result":result})
            except Exception as exc:
                await write_response({"id":rid,"ok":False,"error":{
                    "type":type(exc).__name__,"message":str(exc)[:1000]}})

        try:
            while not self.stop.is_set():
                try:
                    q=await asyncio.wait_for(read_frame(r),self.idle)
                except asyncio.TimeoutError:
                    return
                if q is None: return
                task=asyncio.create_task(process(q),name="sshdock-agent-request")
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (asyncio.IncompleteReadError,ConnectionError,OSError):
            pass
        finally:
            pending=tuple(tasks)
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)
            w.close()
            try: await w.wait_closed()
            except Exception: pass

    async def dispatch(self,method,params):
        if method=="hello":
            token=str(params.get("token",""))
            if not secrets.compare_digest(token,self.token): raise PermissionError("invalid agent token")
            self.auth=True
            return {"protocol":PROTOCOL,"pid":os.getpid(),"machine":platform.machine()}
        if not self.auth: raise PermissionError("agent authentication required")
        async with self.sem:
            if method=="exec": return await self.exec_cmd(params)
            if method=="system_info":
                return {"pid":os.getpid(),"platform":platform.platform(),
                        "python":platform.python_version(),"machine":platform.machine(),
                        "hostname":platform.node(),"cwd":os.getcwd()}
            if method=="shutdown":
                self.stop.set()
                return {"closed":True}
        raise ValueError(f"unknown method: {method}")

    async def exec_cmd(self,params):
        command=str(params.get("command",""))
        if not command: raise ValueError("command is required")
        timeout=params.get("timeout")
        timeout=float(timeout) if timeout is not None else None
        p=await asyncio.create_subprocess_shell(command,stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE,
                                                start_new_session=True)
        try:
            out,err=await (asyncio.wait_for(p.communicate(),timeout) if timeout else p.communicate())
        except asyncio.TimeoutError:
            try: os.killpg(p.pid,9)
            except Exception:
                try: p.kill()
                except Exception: pass
            await p.wait()
            raise TimeoutError(f"command timed out after {timeout}s")
        return {"stdout":out.decode("utf-8","replace") if out else "",
                "stderr":err.decode("utf-8","replace") if err else "",
                "exit_code":int(p.returncode or 0)}

async def main(port,idle):
    token=secrets.token_urlsafe(32)
    a=Agent(token,idle)
    a.server=await asyncio.start_server(a.handle,"127.0.0.1",port)
    sock=(a.server.sockets or [])[0]
    selected=int(sock.getsockname()[1])
    sys.stdout.write(json.dumps({"protocol":PROTOCOL,"port":selected,"token":token},separators=(",",":"))+"\\n")
    sys.stdout.flush()
    await a.stop.wait()
    a.server.close()
    await a.server.wait_closed()

p=argparse.ArgumentParser()
p.add_argument("--port",type=int,default=0)
p.add_argument("--idle-timeout",type=float,default=IDLE_TIMEOUT)
args=p.parse_args()
try: asyncio.run(main(args.port,args.idle_timeout))
except KeyboardInterrupt: pass
