"""Pure-Python helpers for resolving SSH connection targets.

This module is deliberately Qt-free so it can be unit-tested headlessly.

The ConnectionDialog uses :func:`is_alias_resolvable` to decide whether a
user-typed alias string should be looked up in SSH config or treated as a
direct hostname/IP, and :func:`parse_ssh_target` to split a string like
``"root@192.168.1.5:2222"`` into its ``(host, user, port)`` components.

The fallback rule is:

  1. User types something in the alias field.
  2. We try to resolve it against the discovered SSH config aliases.
  3. If it matches → load the alias normally (asyncssh gets config files).
  4. If it does NOT match → treat the text as a direct hostname/IP:
       - Parse ``[user@]host[:port]`` if possible.
       - Use the original text as the profile display name.
       - Clear the ``alias`` field so asyncssh does NOT receive
         ``config=[...]`` and does not try to look the text up — it just
         connects directly to the parsed host with the explicitly
         supplied user / port / key.
"""
from __future__ import annotations

import re
from typing import Sequence

# A relaxed SSH target pattern.
#
# Accepts:
#   host
#   host:port
#   user@host
#   user@host:port
#   [::1]               (IPv6 literal, no port)
#   [::1]:2222          (IPv6 literal with port)
#   user@[::1]:2222
#
# Rejects:
#   anything with spaces, multiple '@', or a port that isn't digits.
#
_SSH_TARGET_RE = re.compile(
    r"""
    ^
    (?: (?P<user> [^@\s:]+) @ )?          # optional user@
    (?P<host>                             # host (IPv4, hostname, or [IPv6])
        \[ [^\]]+ \]                      #   [IPv6 literal]
        |
        [^\s:@]+                          #   hostname / IPv4
    )
    (?: : (?P<port> \d+) )?               # optional :port
    $
    """,
    re.VERBOSE,
)


def parse_ssh_target(text: str) -> tuple[str, str | None, int | None]:
    """Split *text* into ``(host, user, port)``.

    *text* may be in any of these forms:

      * ``"hostname"``
      * ``"192.168.1.5"``
      * ``"[::1]"``  (IPv6 literal)
      * ``"host:2222"``
      * ``"user@host"``
      * ``"user@host:2222"``
      * ``"user@[::1]:2222"``

    Returns ``(host, user_or_None, port_or_None)``. If *text* doesn't
    match any of the accepted forms, returns ``(text, None, None)`` —
    i.e. the whole string becomes the host and the caller can decide
    what to do with it.

    The ``host`` component has surrounding brackets stripped (so ``[::1]``
    becomes ``::1``), which is the form asyncssh's ``connect()`` expects.
    """
    if not text:
        return "", None, None
    stripped = text.strip()
    if not stripped:
        return "", None, None
    m = _SSH_TARGET_RE.match(stripped)
    if not m:
        # Unparseable — return as-is so the caller can still try.
        return stripped, None, None
    host = m.group("host")
    # Strip IPv6 brackets: [::1] -> ::1
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    user = m.group("user") or None
    port_str = m.group("port")
    port = int(port_str) if port_str else None
    return host, user, port


def is_alias_resolvable(name: str, aliases: Sequence) -> bool:
    """Return True if *name* matches the ``alias`` field of any SSHAlias.

    *aliases* is a sequence of objects with an ``alias`` string attribute
    (typically ``list[SSHAlias]`` from :func:`app.ssh.discovery.discover_aliases`).

    The match is case-insensitive — SSH config Host patterns are
    case-insensitive on most platforms.
    """
    if not name:
        return False
    needle = name.strip().lower()
    if not needle:
        return False
    for a in aliases:
        try:
            if a.alias and a.alias.lower() == needle:
                return True
        except AttributeError:
            continue
    return False


def resolve_target(
    text: str,
    aliases: Sequence,
) -> dict:
    """Decide what to do with a user-typed alias / host string.

    Returns a dict with keys:

      * ``action``: ``"alias"`` if *text* matches a configured SSH alias,
        or ``"direct"`` if it should be treated as a direct hostname/IP.
      * ``host``: the hostname/IP to connect to.
      * ``user``: optional username parsed from ``user@host`` syntax.
      * ``port``: optional port parsed from ``host:port`` syntax.
      * ``display_name``: the original *text* (for the profile name).
      * ``alias``: the alias string if action is ``"alias"``, else ``None``.

    This is the single decision point used by ConnectionDialog so the
    fallback logic can be unit-tested without a Qt event loop.
    """
    text = (text or "").strip()
    if not text:
        return {
            "action": "empty",
            "host": "",
            "user": None,
            "port": None,
            "display_name": "",
            "alias": None,
        }
    if is_alias_resolvable(text, aliases):
        return {
            "action": "alias",
            "host": "",  # asyncssh will resolve from config
            "user": None,
            "port": None,
            "display_name": text,
            "alias": text,
        }
    # Not a known alias — parse as a direct hostname/IP.
    host, user, port = parse_ssh_target(text)
    return {
        "action": "direct",
        "host": host or text,
        "user": user,
        "port": port,
        "display_name": text,
        "alias": None,
    }
