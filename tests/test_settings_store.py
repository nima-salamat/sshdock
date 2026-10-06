from __future__ import annotations

import json
from pathlib import Path

from app.core.models import AuthMethod, SSHProfile
from app.storage.settings import SettingsStore


def _store(tmp_path: Path, monkeypatch) -> SettingsStore:
    monkeypatch.setattr("app.storage.settings.user_config_dir", lambda *args: str(tmp_path))
    return SettingsStore()


def _profile(profile_id: str = "profile-1") -> SSHProfile:
    return SSHProfile(
        id=profile_id,
        name="Production",
        host="203.0.113.10",
        username="deploy",
        port=2222,
        auth_method=AuthMethod.PRIVATE_KEY,
        private_key="~/.ssh/id_ed25519",
        persist_secrets=True,
    )


def test_profiles_round_trip(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    profile = _profile()
    store.save_profiles([profile])

    loaded = store.load_profiles()

    assert loaded == [profile]
    assert loaded[0].auth_method is AuthMethod.PRIVATE_KEY


def test_load_profiles_rejects_non_object_json(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text("[]", encoding="utf-8")
    assert store.load_profiles() == []


def test_load_profiles_skips_invalid_items(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.path.write_text(
        json.dumps({"profiles": [{"id": "ok", "name": "Valid"}, {"bad": "item"}]}),
        encoding="utf-8",
    )

    loaded = store.load_profiles()

    assert [item.id for item in loaded] == ["ok"]


def test_ui_preferences_keep_defaults_and_ignore_unknown_keys(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.ui_path.write_text(
        json.dumps({"theme": "light", "worker_shell_count": 5, "unexpected": "value"}),
        encoding="utf-8",
    )

    prefs = store.load_ui_prefs()

    assert prefs["theme"] == "light"
    assert prefs["worker_shell_count"] == 5
    assert "unexpected" not in prefs


def test_compose_project_add_replaces_same_name(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.add_compose_project("server-1", {"name": "web", "path": "/srv/one"})
    store.add_compose_project("server-1", {"name": "web", "path": "/srv/two"})

    assert store.load_compose_projects() == {
        "server-1": [{"name": "web", "path": "/srv/two"}]
    }


def test_compose_project_remove_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.add_compose_project("server-1", {"name": "web"})
    store.remove_compose_project("server-1", "web")
    store.remove_compose_project("server-1", "web")
    assert store.load_compose_projects()["server-1"] == []


def test_export_and_import_bundle(tmp_path: Path, monkeypatch) -> None:
    source = _store(tmp_path / "source", monkeypatch)
    profile = _profile("exported")
    source.save_profiles([profile])
    source.save_ui_prefs({"theme": "light"})
    source.add_compose_project("server-1", {"name": "web", "path": "/srv/web"})

    bundle = tmp_path / "bundle.json"
    source.export_bundle(bundle)

    target = _store(tmp_path / "target", monkeypatch)
    counts = target.import_bundle(bundle, merge=False)

    assert counts == {"profiles": 1, "compose": 1}
    assert target.load_profiles() == [profile]
    assert target.load_compose_projects()["server-1"][0]["name"] == "web"
    assert target.load_ui_prefs()["theme"] == "light"
