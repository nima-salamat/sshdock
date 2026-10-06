from __future__ import annotations

from app.core.models import AuthMethod, ConnectionState, SSHProfile


def test_ssh_profile_round_trip_preserves_enum_and_defaults() -> None:
    profile = SSHProfile(
        id="p1",
        name="Production",
        host="server.example",
        username="deploy",
        port=2222,
        auth_method=AuthMethod.PRIVATE_KEY,
        identity_files=["~/.ssh/id_ed25519"],
    )

    data = profile.to_dict()
    restored = SSHProfile.from_dict(data)

    assert data["auth_method"] == "private_key"
    assert restored == profile
    assert restored.connect_timeout == 10.0
    assert restored.auto_reconnect is True


def test_connection_state_values_are_stable_strings() -> None:
    assert ConnectionState.CONNECTED.value == "connected"
    assert str(ConnectionState.CONNECTED) == "ConnectionState.CONNECTED"


def test_profile_from_dict_does_not_mutate_input() -> None:
    data = {"id": "p1", "name": "Test", "auth_method": "password"}

    SSHProfile.from_dict(data)

    assert data == {"id": "p1", "name": "Test", "auth_method": "password"}
