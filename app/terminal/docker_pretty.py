"""mdocker: emit OSC, reserve exactly 20 shell lines, then normal PS1."""

from __future__ import annotations

import base64

TABLE_RESERVE_LINES = 20

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
    printf '  or: docker <cmd> --magic   /   mps, mimages, …\n' >&2
    return 1
  fi
  __sshdock_magic "$@"
}
alias m-docker='mdocker'
alias magic-docker='mdocker'

# Intentionally do not shadow the real docker command.
# The native docker executable/function remains untouched so existing
# user aliases, functions, and wrappers continue to work.

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


def install_command() -> str:
    b64 = base64.b64encode(DOCKER_PRETTY_SH.encode("utf-8")).decode("ascii")
    return (
        "stty -echo 2>/dev/null; "
        f"echo '{b64}' | (base64 -d 2>/dev/null || base64 --decode 2>/dev/null) "
        "> /tmp/.sshdock_docker.sh 2>/dev/null && "
        ". /tmp/.sshdock_docker.sh 2>/dev/null; "
        "stty echo 2>/dev/null; "
        "printf '\\r\\033[K'"
    )


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
