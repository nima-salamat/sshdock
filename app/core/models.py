from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

class ConnectionState(str, Enum):
    DISCONNECTED="disconnected"; CONNECTING="connecting"; CONNECTED="connected"; RECONNECTING="reconnecting"; AUTH_FAILED="authentication_failed"; HOST_KEY_ERROR="host_key_error"; TIMEOUT="timeout"; ERROR="error"
class AuthMethod(str, Enum):
    AUTO="auto"; PASSWORD="password"; PRIVATE_KEY="private_key"; AGENT="agent"
@dataclass
class SSHProfile:
    id:str; name:str; host:str=""; username:str=""; port:int=22; alias:str|None=None; identity_files:list[str]=field(default_factory=list); identities_only:bool=False; proxy_jump:str|None=None; proxy_command:str|None=None; forward_agent:bool=False; server_alive_interval:int=30; server_alive_count_max:int=3; auth_method:AuthMethod=AuthMethod.AUTO; private_key:str|None=None; custom_options:dict[str,str]=field(default_factory=dict); connect_timeout:float=10.; keepalive:int=30; auto_reconnect:bool=True; max_retries:int=5; retry_delay:float=2.; persist_secrets:bool=False
    def to_dict(self)->dict[str,Any]:
        d=asdict(self); d['auth_method']=self.auth_method.value; return d
    @classmethod
    def from_dict(cls,d:dict[str,Any])->'SSHProfile':
        d=dict(d); d['auth_method']=AuthMethod(d.get('auth_method','auto')); return cls(**d)
@dataclass
class ConnectionErrorInfo:
    title:str; message:str; reason:str=""; fingerprint:str|None=None; retryable:bool=True
