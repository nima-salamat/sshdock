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

def test_predictor_has_starter_suggestions_before_remote_history_load():
    predictor = CommandPredictor()

    # A freshly connected server may not have completed its asynchronous
    # history read when the first key or Ctrl+Space request arrives.
    shadow = predictor.suggest("d", server_id="fresh-server")
    ranked = predictor.suggest_ranked(
        "", server_id="fresh-server", limit=5, min_score=1.0
    )

    assert isinstance(shadow, str) and shadow.startswith("d")
    assert len(ranked) == 5
    assert all(candidate.source == "starter" for candidate in ranked)
    # Bootstrap commands must not become falsely learned user history.
    assert predictor.history(server_id="fresh-server") == ()

def test_directory_predictions_exclude_current_directory_targets():
    predictor = CommandPredictor()
    predictor.ingest(
        [
            "cd /srv/app",
            "cd .",
            "cd ../app",
            "cd ..",
            "ls -la",
            "git status",
        ],
        server_id="srv",
        cwd="/srv/app",
        shell="/bin/bash",
    )

    ranked = predictor.suggest_ranked(
        "cd", server_id="srv", cwd="/srv/app", shell="bash", limit=5
    )
    commands = [item.command for item in ranked]

    assert "cd .." in commands
    assert "cd /srv/app" not in commands
    assert "cd ." not in commands
    assert "cd ../app" not in commands
    shadow = predictor.suggest("cd", server_id="srv", cwd="/srv/app", shell="bash")
    assert shadow == "cd .."


def test_directory_noop_filter_handles_root_and_pwd_aliases():
    predictor = CommandPredictor()
    predictor.ingest(
        ["cd ..", "cd $PWD", "cd ${PWD}", "cd /"],
        server_id="root-server",
        cwd="/",
    )

    ranked = predictor.suggest_ranked(
        "cd", server_id="root-server", cwd="/", limit=5
    )
    assert [item.command for item in ranked] == []


def test_prediction_generalizes_context_across_sibling_directories():
    predictor = CommandPredictor()
    predictor.ingest(
        ["git status", "docker ps", "git log", "docker images", "git diff"],
        server_id="project",
        cwd="/srv/app/api",
        shell="/bin/bash",
    )

    ranked = predictor.suggest_ranked(
        "docker",
        server_id="project",
        cwd="/srv/app/web",
        shell="bash",
        limit=5,
    )
    by_command = {item.command: item for item in ranked}

    assert by_command["docker ps"].features["cwd_path"] >= 0.6
    assert by_command["docker ps"].features["family_transition"] == 1.0


def test_imported_history_does_not_falsely_claim_every_command_ran_in_root():
    predictor = CommandPredictor()
    snapshot = parse_history_output(
        "__SSHDock_SHELL__=/bin/bash\n"
        "__BASH__\n"
        "cd ../api\n"
        "git status\n"
    )
    predictor.ingest_snapshot(snapshot, server_id="srv")

    observations = predictor._history[predictor._scope("srv")]
    assert observations
    assert all(observation.cwd == "" for observation in observations)

def test_legacy_persisted_history_drops_unreliable_root_default():
    predictor = CommandPredictor()
    predictor.load_state_dict({
        "version": 2,
        "history": {
            "srv": [
                {"command": "git status", "cwd": "/", "shell": "/bin/bash"},
                {"command": "cd ../app", "cwd": "/srv", "shell": "/bin/bash"},
            ]
        },
    })

    observations = predictor._history[predictor._scope("srv")]
    assert observations[0].cwd == ""
    assert observations[1].cwd == "/srv"

def test_shell_learning_ignores_comments_and_splits_pasted_commands():
    from app.terminal.predictor import split_shell_commands

    assert split_shell_commands("# just a comment") == []
    assert split_shell_commands("git status # inspect changes") == ["git status"]
    assert split_shell_commands(
        "# pasted notes\ngit status\ndocker ps; git log --oneline"
    ) == ["git status", "docker ps", "git log --oneline"]
    assert split_shell_commands("echo value#tag") == ["echo value#tag"]
    assert split_shell_commands("git log " + chr(92)) == []
    assert split_shell_commands("git status &&") == []
    assert split_shell_commands("cat |") == []
    assert split_shell_commands("cat <<< 'inline input'") == [
        "cat <<< 'inline input'"
    ]
    assert split_shell_commands("echo '<<EOF'") == ["echo '<<EOF'"]
    assert split_shell_commands("function helper() { echo no; }") == []



def test_shell_learning_joins_explicit_multiline_continuations():
    from app.terminal.predictor import split_shell_commands

    assert split_shell_commands("git log \\\n    --oneline -5") == [
        "git log --oneline -5"
    ]
    assert split_shell_commands('printf "line one\\nline two"') == [
        'printf "line one\\nline two"'
    ]


def test_shell_learning_rejects_incomplete_or_compound_script_blocks():
    from app.terminal.predictor import split_shell_commands

    assert split_shell_commands('echo "unfinished') == []
    assert split_shell_commands("cat <<EOF\\nnot a command\\nEOF") == []
    assert split_shell_commands(
        "if true; then\\n echo should_not_be_learned\\nfi"
    ) == []


def test_add_command_learns_simple_children_not_comments_or_fragments():
    predictor = CommandPredictor()

    assert not predictor.add_command("# comment only", server_id="paste")
    assert predictor.add_command(
        "# notes\ngit status; docker ps && git log \\\n --oneline",
        server_id="paste",
        cwd="/srv/app",
    )

    assert predictor.history(server_id="paste") == (
        "git status",
        "docker ps",
        "git log --oneline",
    )

def test_history_parser_filters_comments_and_groups_multiline_shell_entries():
    snapshot = parse_history_output(
        "__SSHDock_SHELL__=/bin/bash\n"
        "__BASH__\n"
        "# documentation comment\n"
        "git status; docker ps\n"
        "git log \\\n"
        "    --oneline\n"
        "if true; then\n"
        "  echo do_not_learn_body\n"
        "fi\n"
        "cat <<EOF\n"
        "echo do_not_learn_heredoc\n"
        "EOF\n"
        "printf 'hello\n"
        "world'\n"
        "pwd\n"
    )

    assert snapshot.commands == (
        "git status",
        "docker ps",
        "git log --oneline",
        "printf 'hello\nworld'",
        "pwd",
    )


def test_shell_control_words_inside_comments_or_quotes_are_not_script_blocks():
    from app.terminal.predictor import split_shell_commands

    assert split_shell_commands('echo "if this is only text"') == [
        'echo "if this is only text"'
    ]
    assert split_shell_commands("git status # if true; then") == ["git status"]
    assert split_shell_commands("echo ok; if true; then echo no; fi") == []

def test_predictor_reuses_context_between_prefixes_and_invalidates_when_history_changes():
    predictor = CommandPredictor()
    predictor.ingest(
        ["git status", "docker ps", "git log"],
        server_id="srv",
        cwd="/srv/app",
        shell="/bin/bash",
    )
    scope = predictor._scope("srv")
    first = predictor._build_context(scope, "/srv/app", "bash")

    assert predictor._build_context(scope, "/srv/app", "bash") is first
    predictor.suggest_ranked(
        "git", server_id="srv", cwd="/srv/app", shell="bash", limit=5
    )
    assert predictor._build_context(scope, "/srv/app", "bash") is first

    predictor.add_command("git diff", server_id="srv", cwd="/srv/app", shell="/bin/bash")
    refreshed = predictor._build_context(scope, "/srv/app", "bash")
    assert refreshed is not first
    assert refreshed["observations"][-1].command == "git diff"

def test_snapshot_ingestion_can_be_batched_and_reserved_against_duplicates():
    predictor = CommandPredictor()
    snapshot = parse_history_output(
        "__SSHDock_SHELL__=/bin/bash\n"
        "__BASH__\n"
        "git status\n"
        "docker ps\n"
    )

    prepared = predictor.prepare_snapshot_ingest(snapshot, server_id="batch")
    assert prepared is not None
    commands, fingerprint = prepared
    assert commands == ("git status", "docker ps")
    # A second terminal opening must not import the same snapshot concurrently.
    assert predictor.prepare_snapshot_ingest(snapshot, server_id="batch") is None
    assert predictor.history(server_id="batch") == ()

    predictor.ingest(commands[:1], server_id="batch", cwd="", shell=snapshot.shell)
    predictor.ingest(commands[1:], server_id="batch", cwd="", shell=snapshot.shell)
    predictor.finish_snapshot_ingest(fingerprint, server_id="batch", completed=True)

    assert predictor.history(server_id="batch") == ("git status", "docker ps")
    assert predictor.prepare_snapshot_ingest(snapshot, server_id="batch") is None


def test_remote_history_parsing_runs_outside_calling_thread(monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    import app.terminal.predictor as predictor_module

    caller_thread = threading.get_ident()
    parser_threads = []
    original_parser = predictor_module.parse_history_output

    def tracking_parser(output):
        parser_threads.append(threading.get_ident())
        return original_parser(output)

    class FakeSSH:
        async def run_fast(self, _script, timeout):
            return SimpleNamespace(
                exit_code=0,
                stdout=(
                    "__SSHDock_SHELL__=/bin/bash\n"
                    "__BASH__\n"
                    "git status\n"
                ),
            )

    monkeypatch.setattr(predictor_module, "parse_history_output", tracking_parser)
    snapshot = asyncio.run(predictor_module.load_remote_history(FakeSSH()))

    assert snapshot.commands == ("git status",)
    assert parser_threads and parser_threads[0] != caller_thread




def test_shell_learning_excludes_sshdock_magic_commands_and_aliases():
    from app.terminal.predictor import split_shell_commands

    aliases = (
        "mdocker ps", "m-docker ps", "magic-docker ps",
        "mdps", "mdpa", "mdimg", "mdvol", "mdnet", "mdstats",
        "mdcomp", "mdsvc", "mddf", "mdctx", "mdplug", "mdnode",
        "mdstack", "mdsecret", "mdconfig", "mps", "mpa", "mimages",
        "mimg", "mvolume", "mvol", "mnetwork", "mnet", "mstats",
        "mcompose", "mcomp", "mservice", "msvc", "mdf", "msystem",
        "mcontext", "mplugin", "mnode", "mstack", "msecret", "mconfig",
    )
    for command in aliases:
        assert split_shell_commands(command) == [], command

    assert split_shell_commands("docker ps --magic") == []
    assert split_shell_commands("docker --mdocker ps") == []
    assert split_shell_commands("sudo mdocker ps") == []
    assert split_shell_commands("__sshdock_magic ps") == []
    assert split_shell_commands("source $HOME/.cache/sshdock/docker.sh") == []


def test_shell_learning_skips_magic_fragments_but_keeps_ordinary_commands():
    from app.terminal.predictor import split_shell_commands

    assert split_shell_commands(
        "git status; mdocker ps; docker ps --magic; git log --oneline"
    ) == ["git status", "git log --oneline"]


def test_predictor_never_learns_or_scores_sshdock_magic_commands():
    predictor = CommandPredictor()

    assert not predictor.add_command("mdocker ps", server_id="magic")
    assert not predictor.record_suggestion_acceptance(
        "mdocker ps", server_id="magic"
    )
    assert not predictor.record_command_result(
        "docker ps --magic", success=True, server_id="magic"
    )
    assert predictor.add_command(
        "git status; mdocker ps; docker ps --magic; docker ps",
        server_id="magic",
    )
    assert predictor.history(server_id="magic") == ("git status", "docker ps")



def test_remote_history_parser_skips_sshdock_magic_aliases():
    snapshot = parse_history_output(
        "__SSHDock_SHELL__=/bin/bash\\n"
        "__BASH__\\n"
        "mdocker ps\\n"
        "docker ps --magic\\n"
        "mdps\\n"
        "source \"$HOME/.cache/sshdock/docker.sh\"\\n"
        "git status\\n"
        "docker ps\\n"
    )

    assert snapshot.commands == ("git status", "docker ps")
