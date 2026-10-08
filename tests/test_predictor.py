from app.terminal.predictor import (
    CommandPredictor,
    parse_history_output,
    sanitize_command,
)


def test_sanitize_command_rejects_secrets():
    assert sanitize_command("echo TOKEN=super-secret") is None
    assert sanitize_command("curl https://user:pass@example.com") is None
    assert sanitize_command("docker login -p secret registry.example") is None
    assert sanitize_command("cat private_key=value") is None


def test_sanitize_command_keeps_normal_commands():
    assert sanitize_command("docker compose ps") == "docker compose ps"
    assert sanitize_command("git status") == "git status"


def test_predictor_prefix_and_frequency():
    predictor = CommandPredictor()
    predictor.ingest(["docker compose ps"] * 4 + ["docker compose up -d"])
    assert predictor.suggest("docker co") == "docker compose ps"


def test_predictor_recency_and_determinism():
    predictor = CommandPredictor()
    predictor.ingest(["docker ps", "git status", "docker ps"])
    first = predictor.suggest("d")
    second = predictor.suggest("d")
    assert first == second == "docker ps"


def test_predictor_context_transition():
    predictor = CommandPredictor()
    predictor.ingest(["docker compose up", "docker compose ps", "docker compose up"] * 3)
    assert predictor.suggest("docker c") == "docker compose ps"


def test_predictor_cwd_affinity():
    predictor = CommandPredictor()
    predictor.add_command("ls -la", cwd="/srv")
    predictor.add_command("ls -lah", cwd="/tmp")
    assert predictor.suggest("ls -", cwd="/srv") == "ls -la"


def test_predictor_shell_affinity():
    predictor = CommandPredictor()
    predictor.add_command("rg TODO", shell="/bin/zsh")
    predictor.add_command("rg --hidden TODO", shell="/bin/bash")
    assert predictor.suggest("rg ", shell="zsh") == "rg TODO"


def test_predictor_separates_servers():
    predictor = CommandPredictor()
    predictor.add_command("docker compose ps", server_id="a")
    predictor.add_command("kubectl get pods", server_id="b")
    assert predictor.suggest("docker", server_id="a") == "docker compose ps"
    assert predictor.suggest("docker", server_id="b") is None


def test_predictor_ignores_empty_and_oversized_commands():
    predictor = CommandPredictor()
    assert not predictor.add_command("")
    assert not predictor.add_command("x" * 4097)
    assert predictor.history() == ()


def test_history_parser_handles_bash_zsh_fish():
    output = """__SSHDock_SHELL__=/bin/zsh
__BASH__
git status
__ZSH__
: 1720000000:0;docker compose ps
__FISH__
- cmd: ls -la
"""
    snapshot = parse_history_output(output)
    assert snapshot.shell == "/bin/zsh"
    assert snapshot.commands == ("docker compose ps",)


def test_predictor_ranked_candidates_include_confidence_and_learning():
    predictor = CommandPredictor()
    predictor.ingest(
        ["git status", "git status", "git diff", "git log"],
        server_id="server",
        cwd="/srv",
        shell="/bin/bash",
    )
    ranked = predictor.suggest_ranked(
        "git ",
        server_id="server",
        cwd="/srv",
        shell="bash",
        limit=3,
    )
    assert ranked
    assert ranked[0].command in {"git status", "git diff", "git log"}
    assert 50 <= ranked[0].confidence <= 99
    assert ranked[0].source

    target = ranked[-1].command
    assert predictor.record_suggestion_acceptance(
        target,
        server_id="server",
        cwd="/srv",
        shell="/bin/bash",
    )
    assert target in predictor.history(server_id="server")


def test_predictor_state_round_trip_preserves_learning():
    predictor = CommandPredictor()
    predictor.ingest(
        ["docker ps", "docker images", "docker ps"],
        server_id="a",
        cwd="/srv",
        shell="/bin/bash",
    )
    predictor.record_suggestion_acceptance(
        "docker images",
        server_id="a",
        cwd="/srv",
        shell="/bin/bash",
    )
    predictor.record_command_result("docker images", success=True, server_id="a")
    state = predictor.state_dict()

    restored = CommandPredictor()
    restored.load_state_dict(state)
    assert restored.history(server_id="a") == predictor.history(server_id="a")
    assert restored.suggest(
        "docker ",
        server_id="a",
        cwd="/srv",
        shell="bash",
    ) == predictor.suggest(
        "docker ",
        server_id="a",
        cwd="/srv",
        shell="bash",
    )


def test_predictor_remote_history_snapshot_is_idempotent():
    predictor = CommandPredictor()
    snapshot = parse_history_output(
        "__SSHDock_SHELL__=/bin/bash\n"
        "__BASH__\n"
        "git status\n"
        "git log\n"
    )
    assert predictor.ingest_snapshot(snapshot, server_id="srv") == 2
    assert predictor.ingest_snapshot(snapshot, server_id="srv") == 0
    assert predictor.history(server_id="srv") == ("git status", "git log")
