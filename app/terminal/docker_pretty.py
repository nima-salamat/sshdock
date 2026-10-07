"""mdocker: emit OSC, reserve exactly 20 shell lines, then normal PS1."""

from __future__ import annotations

import base64

TABLE_RESERVE_LINES = 20
SH_SCRIPT_PATH = "$HOME/.cache/sshdock/docker.sh"
FISH_SCRIPT_PATH = "$HOME/.cache/sshdock/docker.fish"

DOCKER_PRETTY_SH = r"""# sshdock mdocker — table-capable docker list commands
# After command: 20 blank lines, then prompt. App draws the HTML table on those lines.
__sshdock_magic() {
  printf '\033]777;sshdock-magic;%s\007' "$*"
  printf '\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n'
}

mdocker() {
  if [ $# -lt 1 ]; then
    printf 'usage: mdocker <ps|images|volume|network|stats|compose|service|system|context|plugin|node|stack|secret|config> [args]\n' >&2
    printf '  short: mdps mdpa mdimg mdvol mdnet mdstats mdcomp mdsvc mddf\n' >&2
    printf '  native docker is left untouched; use mdocker for inline tables\n' >&2
    return 1
  fi
  __sshdock_magic "$@"
}
alias m-docker='mdocker'
alias magic-docker='mdocker'

# Support the historical "docker … --magic" form as well as mdocker.
# Keep this wrapper POSIX-compatible and delegate all non-magic calls to native docker.
docker() {
  for arg in "$@"; do
    case "$arg" in
      --magic|--mdocker) __sshdock_magic "$@"; return 0 ;;
    esac
  done
  command docker "$@"
}

# Long aliases (md*)
alias mdps='mdocker ps'
alias mdpa='mdocker ps -a'
alias mdimg='mdocker images'
alias mdvol='mdocker volume ls'
alias mdnet='mdocker network ls'
alias mdstats='mdocker stats'
alias mdcomp='mdocker compose ls'
alias mdsvc='mdocker service ls'
alias mddf='mdocker system df'
alias mdctx='mdocker context ls'
alias mdplug='mdocker plugin ls'
alias mdnode='mdocker node ls'
alias mdstack='mdocker stack ls'
alias mdsecret='mdocker secret ls'
alias mdconfig='mdocker config ls'

# Short m* aliases (m + command)
alias mps='mdocker ps'
alias mpa='mdocker ps -a'
alias mimages='mdocker images'
alias mimg='mdocker images'
alias mvolume='mdocker volume ls'
alias mvol='mdocker volume ls'
alias mnetwork='mdocker network ls'
alias mnet='mdocker network ls'
alias mstats='mdocker stats'
alias mcompose='mdocker compose ls'
alias mcomp='mdocker compose ls'
alias mservice='mdocker service ls'
alias msvc='mdocker service ls'
alias mdf='mdocker system df'
alias msystem='mdocker system df'
alias mcontext='mdocker context ls'
alias mplugin='mdocker plugin ls'
alias mnode='mdocker node ls'
alias mstack='mdocker stack ls'
alias msecret='mdocker secret ls'
alias mconfig='mdocker config ls'
"""

DOCKER_PRETTY_FISH = r"""# SSHDock mdocker integration for fish
function __sshdock_magic
  set -l joined (string join ' ' -- $argv)
  printf '\e]777;sshdock-magic;%s\a' "$joined"
  printf '\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n'
end

function mdocker
  if test (count $argv) -lt 1
    printf 'usage: mdocker <ps|images|volume|network|stats|compose|service|system|context|plugin|node|stack|secret|config> [args]\n' >&2
    printf '  short: mdps mdpa mdimg mdvol mdnet mdstats mdcomp mdsvc mddf\n' >&2
    printf '  native docker is left untouched; use mdocker for inline tables\n' >&2
    return 1
  end
  __sshdock_magic $argv
end

alias m-docker='mdocker'
alias magic-docker='mdocker'
function docker
  for arg in $argv
    if test "$arg" = --magic -o "$arg" = --mdocker
      __sshdock_magic $argv
      return 0
    end
  end
  command docker $argv
end
alias mdps='mdocker ps'
alias mdpa='mdocker ps -a'
alias mdimg='mdocker images'
alias mdvol='mdocker volume ls'
alias mdnet='mdocker network ls'
alias mdstats='mdocker stats'
alias mdcomp='mdocker compose ls'
alias mdsvc='mdocker service ls'
alias mddf='mdocker system df'
alias mdctx='mdocker context ls'
alias mdplug='mdocker plugin ls'
alias mdnode='mdocker node ls'
alias mdstack='mdocker stack ls'
alias mdsecret='mdocker secret ls'
alias mdconfig='mdocker config ls'
alias mps='mdocker ps'
alias mpa='mdocker ps -a'
alias mimages='mdocker images'
alias mimg='mdocker images'
alias mvolume='mdocker volume ls'
alias mvol='mdocker volume ls'
alias mnetwork='mdocker network ls'
alias mnet='mdocker network ls'
alias mstats='mdocker stats'
alias mcompose='mdocker compose ls'
alias mcomp='mdocker compose ls'
alias mservice='mdocker service ls'
alias msvc='mdocker service ls'
alias mdf='mdocker system df'
alias msystem='mdocker system df'
alias mcontext='mdocker context ls'
alias mplugin='mdocker plugin ls'
alias mnode='mdocker node ls'
alias mstack='mdocker stack ls'
alias msecret='mdocker secret ls'
alias mconfig='mdocker config ls'
"""

def _write_script_command(path: str, content: str) -> str:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return (
        f"echo '{b64}' | (base64 -d 2>/dev/null || base64 --decode 2>/dev/null) "
        f"> {path} 2>/dev/null"
    )


def prepare_command() -> str:
    """Prepare both integrations in a private per-user directory."""
    prefix = 'umask 077 && mkdir -p "$HOME/.cache/sshdock" && chmod 700 "$HOME/.cache/sshdock"'
    write_sh = _write_script_command(SH_SCRIPT_PATH, DOCKER_PRETTY_SH)
    write_fish = _write_script_command(FISH_SCRIPT_PATH, DOCKER_PRETTY_FISH)
    return " && ".join(
        (
            prefix,
            write_sh,
            'chmod 600 "$HOME/.cache/sshdock/docker.sh"',
            write_fish,
            'chmod 600 "$HOME/.cache/sshdock/docker.fish"',
        )
    )


def source_command(shell_name: str = "") -> str:
    if str(shell_name).strip().lower() == "fish":
        return f"source {FISH_SCRIPT_PATH} 2>/dev/null"
    return f". {SH_SCRIPT_PATH} 2>/dev/null"


def install_command(*, shell_name: str = "", manage_echo: bool = True) -> str:
    """Return the shell command used to install the mdocker integration.

    When the caller already disabled PTY echo, ``manage_echo=False`` prevents
    the long base64 payload from being echoed by the remote shell.
    """
    command = prepare_command() + " && " + source_command(shell_name)
    if manage_echo:
        return "stty -echo 2>/dev/null; " + command + "; stty echo 2>/dev/null; printf '\\r\\033[K'"
    return command

def parse_magic_kind(payload: str) -> str | None:
    """Map mdocker / docker --magic argv to a results-table kind.

    Returns None only when the command is not a known list/table target.
    """
    text = (payload or "").strip()
    parts = text.split()
    if not parts:
        return "containers"
    # Strip leading "docker" if user typed docker ps --magic
    if parts[0] == "docker":
        parts = parts[1:]
    if not parts:
        return "containers"

    cmd, rest = parts[0], parts[1:]
    cmd_l = cmd.lower()

    # containers
    if cmd_l in ("ps", "container", "containers"):
        # container ls | container ls -a
        if cmd_l in ("container", "containers"):
            if rest and rest[0] in ("ls", "list", "ps"):
                rest = rest[1:]
            elif rest and rest[0] not in ("-a", "--all", "-q", "--quiet"):
                # e.g. container inspect → not a table list
                if rest[0] not in ("ls", "list", "ps"):
                    return None
        return "containers_all" if any(a in ("-a", "--all") for a in rest) else "containers"

    # images
    if cmd_l in ("images", "image", "img"):
        if cmd_l == "image" and rest and rest[0] not in ("ls", "list", "-a", "--all"):
            if rest[0] in ("ls", "list"):
                pass
            else:
                return None
        return "images"

    # volumes
    if cmd_l in ("volume", "volumes", "vol"):
        if not rest or rest[0] in ("ls", "list"):
            return "volumes"
        return None

    # networks
    if cmd_l in ("network", "networks", "net"):
        if not rest or rest[0] in ("ls", "list"):
            return "networks"
        return None

    # stats
    if cmd_l == "stats":
        return "stats"

    # compose projects / compose ps
    if cmd_l in ("compose", "comp"):
        if not rest or rest[0] in ("ls", "list"):
            return "compose"
        if rest[0] == "ps":
            return "compose_ps"
        return "compose"  # default to project list

    # swarm services
    if cmd_l in ("service", "services", "svc"):
        if not rest or rest[0] in ("ls", "list"):
            return "services"
        return None

    # system df
    if cmd_l == "system":
        if rest and rest[0] == "df":
            return "system_df"
        return None
    if cmd_l in ("df", "disk"):
        return "system_df"

    # context
    if cmd_l in ("context", "ctx"):
        if not rest or rest[0] in ("ls", "list"):
            return "contexts"
        return None

    # plugin
    if cmd_l in ("plugin", "plugins"):
        if not rest or rest[0] in ("ls", "list"):
            return "plugins"
        return None

    # node (swarm)
    if cmd_l in ("node", "nodes"):
        if not rest or rest[0] in ("ls", "list"):
            return "nodes"
        return None

    # stack (swarm)
    if cmd_l in ("stack", "stacks"):
        if not rest or rest[0] in ("ls", "list"):
            return "stacks"
        return None

    # secret (swarm)
    if cmd_l in ("secret", "secrets"):
        if not rest or rest[0] in ("ls", "list"):
            return "secrets"
        return None

    # config (swarm)
    if cmd_l in ("config", "configs"):
        if not rest or rest[0] in ("ls", "list"):
            return "configs"
        return None

    return None


KIND_TO_CMD = {
    "containers": "mdocker ps",
    "containers_all": "mdocker ps -a",
    "images": "mdocker images",
    "volumes": "mdocker volume ls",
    "networks": "mdocker network ls",
    "stats": "mdocker stats",
    "compose": "mdocker compose ls",
    "compose_ps": "mdocker compose ps",
    "services": "mdocker service ls",
    "system_df": "mdocker system df",
    "contexts": "mdocker context ls",
    "plugins": "mdocker plugin ls",
    "nodes": "mdocker node ls",
    "stacks": "mdocker stack ls",
    "secrets": "mdocker secret ls",
    "configs": "mdocker config ls",
}
