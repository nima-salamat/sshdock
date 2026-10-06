from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_config_dir

from app.core.models import SSHProfile


_DEFAULT_UI: dict[str, Any] = {
    "theme": "dark",
    "terminal_font_family": "Consolas",
    "terminal_font_size": 13,
    "notifications_enabled": True,
    "worker_shell_count": 3,
}


class SettingsStore:
    def __init__(self) -> None:
        self.root = Path(user_config_dir("SSHDock", "OpenAI"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "profiles.json"
        self.compose_path = self.root / "compose_projects.json"
        self.ui_path = self.root / "ui_prefs.json"

    # ── profiles ──────────────────────────────────────────────
    def load_profiles(self) -> list[SSHProfile]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        profiles: list[SSHProfile] = []
        for item in data.get("profiles", []):
            try:
                profiles.append(SSHProfile.from_dict(item))
            except (TypeError, ValueError):
                continue
        return profiles

    def save_profiles(self, profiles: list[SSHProfile]) -> None:
        payload = {"profiles": [p.to_dict() for p in profiles]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── compose projects ──────────────────────────────────────
    def load_compose_projects(self) -> dict[str, list[dict]]:
        if not self.compose_path.exists():
            return {}
        try:
            data = json.loads(self.compose_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def save_compose_projects(self, data: dict[str, list[dict]]) -> None:
        tmp = self.compose_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.compose_path)

    def add_compose_project(self, server_id: str, project: dict) -> None:
        all_p = self.load_compose_projects()
        lst = all_p.setdefault(server_id, [])
        name = project.get("name") or ""
        lst = [p for p in lst if p.get("name") != name]
        lst.append(project)
        all_p[server_id] = lst
        self.save_compose_projects(all_p)

    def remove_compose_project(self, server_id: str, name: str) -> None:
        all_p = self.load_compose_projects()
        lst = all_p.get(server_id, [])
        all_p[server_id] = [p for p in lst if p.get("name") != name]
        self.save_compose_projects(all_p)

    # ── UI preferences ────────────────────────────────────────
    def load_ui_prefs(self) -> dict[str, Any]:
        prefs = dict(_DEFAULT_UI)
        if not self.ui_path.exists():
            return prefs
        try:
            data = json.loads(self.ui_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                prefs.update({k: data[k] for k in data if k in _DEFAULT_UI or k in data})
        except (OSError, ValueError):
            pass
        return prefs

    def save_ui_prefs(self, prefs: dict[str, Any]) -> None:
        merged = dict(_DEFAULT_UI)
        merged.update(prefs)
        tmp = self.ui_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        tmp.replace(self.ui_path)

    # ── export / import ───────────────────────────────────────
    def export_bundle(self, path: Path) -> None:
        """Write profiles + compose projects + ui prefs to a single JSON file."""
        payload = {
            "version": 1,
            "profiles": [p.to_dict() for p in self.load_profiles()],
            "compose_projects": self.load_compose_projects(),
            "ui_prefs": self.load_ui_prefs(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def import_bundle(self, path: Path, *, merge: bool = True) -> dict[str, int]:
        """Import from export file. Returns counts of imported items."""
        data = json.loads(path.read_text(encoding="utf-8"))
        counts = {"profiles": 0, "compose": 0}
        # profiles
        incoming = data.get("profiles") or []
        if isinstance(incoming, list):
            existing = {p.id: p for p in self.load_profiles()} if merge else {}
            for item in incoming:
                try:
                    prof = SSHProfile.from_dict(item)
                except (TypeError, ValueError):
                    continue
                existing[prof.id] = prof
                counts["profiles"] += 1
            self.save_profiles(list(existing.values()))
        # compose
        compose = data.get("compose_projects") or {}
        if isinstance(compose, dict):
            current = self.load_compose_projects() if merge else {}
            for sid, lst in compose.items():
                if not isinstance(lst, list):
                    continue
                cur = {p.get("name"): p for p in current.get(sid, []) if isinstance(p, dict)}
                for p in lst:
                    if isinstance(p, dict) and p.get("name"):
                        cur[p["name"]] = p
                        counts["compose"] += 1
                current[sid] = list(cur.values())
            self.save_compose_projects(current)
        # ui
        ui = data.get("ui_prefs")
        if isinstance(ui, dict):
            self.save_ui_prefs(ui)
        return counts

    @staticmethod
    def new_id() -> str:
        return uuid4().hex
