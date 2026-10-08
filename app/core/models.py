from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

class ConnectionState(str, Enum):
    DISCONNECTED="disconnected"; CONNECTING="connecting"; CONNECTED="connected"; RECONNECTING="reconnecting"; AUTH_FAILED="authentication_failed"; HOST_KEY_ERROR="host_key_error"; TIMEOUT="timeout"; ERROR="error"
class AuthMethod(str, Enum):
    AUTO="auto"; PASSWORD="password"; PRIVATE_KEY="private_key"; AGENT="agent"


class ProxyType(str, Enum):
    SOCKS5 = "socks5"
    HTTP_CONNECT = "http_connect"


@dataclass
class ProxySettings:
    """Connection proxy configuration.

    Proxy passwords are intentionally excluded from this model. They must not
    be serialized into profile/UI JSON or exposed through the normal model API.
    """

    enabled: bool = False
    type: ProxyType = ProxyType.SOCKS5
    host: str = ""
    port: int = 1080

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "type": self.type.value,
            "host": str(self.host or "").strip(),
            "port": max(1, min(65535, int(self.port))),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProxySettings":
        raw = dict(data or {})
        try:
            proxy_type = ProxyType(raw.get("type", ProxyType.SOCKS5.value))
        except ValueError:
            proxy_type = ProxyType.SOCKS5
        try:
            port = int(raw.get("port", 1080))
        except (TypeError, ValueError):
            port = 1080
        return cls(
            enabled=bool(raw.get("enabled", False)),
            type=proxy_type,
            host=str(raw.get("host", "") or "").strip(),
            port=max(1, min(65535, port)),
        )
@dataclass
class SSHProfile:
    id:str; name:str; host:str=""; username:str=""; port:int=22; alias:str|None=None; identity_files:list[str]=field(default_factory=list); identities_only:bool=False; proxy_jump:str|None=None; proxy_command:str|None=None; proxy:ProxySettings=field(default_factory=ProxySettings); forward_agent:bool=False; server_alive_interval:int=30; server_alive_count_max:int=3; auth_method:AuthMethod=AuthMethod.AUTO; private_key:str|None=None; custom_options:dict[str,str]=field(default_factory=dict); connect_timeout:float=10.; keepalive:int=30; auto_reconnect:bool=True; max_retries:int=5; retry_delay:float=2.; persist_secrets:bool=False
    def to_dict(self)->dict[str,Any]:
        d=asdict(self)
        d['auth_method']=self.auth_method.value
        d['proxy']=self.proxy.to_dict()
        return d
    @classmethod
    def from_dict(cls,d:dict[str,Any])->'SSHProfile':
        d=dict(d)
        d['auth_method']=AuthMethod(d.get('auth_method','auto'))
        raw_proxy=d.pop('proxy', None)
        profile=cls(**d)
        profile.proxy=raw_proxy if isinstance(raw_proxy, ProxySettings) else ProxySettings.from_dict(raw_proxy if isinstance(raw_proxy, dict) else None)
        return profile
@dataclass
class ConnectionErrorInfo:
    title:str; message:str; reason:str=""; fingerprint:str|None=None; retryable:bool=True
