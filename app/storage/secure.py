from __future__ import annotations

import logging
import keyring

log = logging.getLogger(__name__)
SERVICE = "SSHDock"


class CredentialStore:
    def __init__(self) -> None:
        self.service = SERVICE

    def _key(self, profile_id: str, kind: str) -> str:
        return f"{profile_id}:{kind}"

    def get(self, profile_id: str, kind: str) -> str | None:
        try:
            return keyring.get_password(self.service, self._key(profile_id, kind))
        except Exception:
            log.exception("Credential lookup failed")
            return None

    def set(self, profile_id: str, kind: str, value: str) -> bool:
        try:
            keyring.set_password(self.service, self._key(profile_id, kind), value)
            return True
        except Exception:
            log.exception("Credential store failed")
            return False

    def delete(self, profile_id: str, kind: str) -> None:
        try:
            keyring.delete_password(self.service, self._key(profile_id, kind))
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            log.exception("Credential deletion failed")
