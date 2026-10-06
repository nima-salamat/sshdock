from __future__ import annotations
class SFTPManager:
    def __init__(self,ssh):self.ssh=ssh
    async def listdir(self,path):
        s=await self.ssh.sftp(); return await s.listdir(path)
    async def stat(self,path):
        s=await self.ssh.sftp(); return await s.stat(path)
    async def upload(self,local,remote):
        s=await self.ssh.sftp(); await s.put(local,remote)
    async def download(self,remote,local):
        s=await self.ssh.sftp(); await s.get(remote,local)
