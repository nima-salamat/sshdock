# SSHDock

SSHDock is a cross-platform PySide6 desktop client for managing remote Linux servers and Docker through SSH. It keeps the main SSH connection persistent, runs background commands through a bounded worker-shell pool, and provides a desktop UI for containers, images, volumes, networks, Compose projects, host monitoring, logs, files, and terminals.

## Highlights

- Persistent SSH sessions with reconnect support and OpenSSH config/alias discovery
- Docker containers, images, volumes, networks, Compose, events, and resource usage
- Integrated terminal plus remote SFTP file browser
- Host CPU, memory, disk, process, service, and log views
- Bounded async task handling with cancellation/error reporting
- Dark desktop UI with keyboard shortcuts and searchable Docker tables
- Encrypted OpenSSH key support through the `bcrypt` dependency

## Architecture

The application is split into small layers:

- `app/ssh/` — SSH connection, authentication, worker shells, and session management
- `app/docker/` — Docker CLI wrapper, typed models, parsing, and resource calculations
- `app/services/` — UI-independent Docker services
- `app/server/` — host telemetry collection
- `app/files/` — SFTP and file-browser functionality
- `app/terminal/` — terminal transport/emulation and xterm.js integration
- `app/ui/` — PySide6 windows, pages, dialogs, notifications, and table rendering
- `app/workers/` — application task tracking and bounded async work

Most remote operations are executed as commands on the target server; Docker itself does not need to be installed on the desktop machine.

## Requirements

- Python 3.10+
- A reachable SSH server
- Docker on the remote server for Docker-related features
- On Windows, an SSH key/passphrase or password supported by the selected SSH profile

## Install and run

```bash
pip install -r requirements.txt
python -m app.main
```

Or on Windows:

```powershell
.\run_windows.ps1
```

SSHDock can use SSH aliases discovered from the local OpenSSH configuration, or a manually entered host, port, username, key, and credentials.

## Development checks

```bash
python -m compileall -q app
pytest -q tests
```

The test suite contains static, unit, and resilience checks. Full runtime validation of SSH/Docker behaviour still requires a real target host with the same environment used in production.

## Notes

SSHDock is a desktop administration client, not a replacement for Docker Engine or an SSH server. Remote command results depend on the permissions, Docker installation, available system utilities, SSH server limits, and network quality of the target host.

### Terminal workspace
The terminal workspace provides a persistent SSH PTY with xterm.js, a VS Code-style compact session list on the right, optional remote file explorer, terminal search via `Ctrl+Shift+F`, shell command suggestions, and the remote shell's native `Tab` completion. The WebEngine surface is kept behind a dark placeholder until ready to avoid the first-open white flash.

### Fast server data
The Server > Processes view loads lazily when opened and uses a direct SSH exec channel with a lightweight `ps` snapshot, avoiding the previous two-sample `top` delay. Server data loaders surface command failures in the UI instead of silently appearing as empty tables.
