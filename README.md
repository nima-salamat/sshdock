# SSHDock

SSHDock is a cross-platform PySide6 desktop application for working with remote Linux servers over SSH.

The main idea is simple: connect to a server once, then let the application perform the repetitive commands and checks you would otherwise type manually over SSH. The UI turns common remote operations into reusable actions while keeping an integrated terminal available when you want the real shell.

Today SSHDock includes Docker administration, host monitoring, remote files/SFTP, terminals, logs, Compose operations, connection profiles, and SSH-config alias discovery. Docker and host operations are executed on the remote server; Docker does not need to be installed on the desktop.

## What SSHDock does

After an SSH connection is established, SSHDock can use that connection to:

- inspect and manage Docker containers, images, volumes, networks, events, and Compose projects;
- collect host CPU, memory, disk, process, service, and log information;
- open a persistent interactive SSH terminal;
- browse and edit remote files through SFTP;
- remember SSH profiles and discover aliases from OpenSSH configuration;
- run background remote commands without making every page of the UI open a separate connection.

In other words, SSHDock is an SSH-based remote administration client, not a replacement for SSH or Docker.

## Architecture

The code is split into small layers:

| Path | Responsibility |
| --- | --- |
| `app/ssh/` | SSH connections, authentication, config discovery, worker shells, and sessions |
| `app/docker/` | Docker CLI execution, parsing, typed models, and resource calculations |
| `app/services/` | UI-independent services built on top of lower-level modules |
| `app/server/` | Remote host telemetry and monitoring commands |
| `app/files/` | SFTP and remote file-browser functionality |
| `app/terminal/` | Interactive terminal transport, emulation, and xterm.js integration |
| `app/ui/` | PySide6 windows, dialogs, pages, tables, notifications, and widgets |
| `app/workers/` | Background task tracking and bounded asynchronous work |
| `tests/` | Unit, regression, resilience, and UI-structure tests |

The most important design boundary is that remote administration is performed through SSH. The desktop application is responsible for connection/session management, command orchestration, parsing, presentation, and local settings.

## Requirements

- Python 3.10 or newer
- A reachable SSH server
- A supported SSH authentication method (password, private key, or SSH agent)
- Docker on the remote host for Docker-related features
- Qt WebEngine support for the full xterm.js terminal; the application has a fallback terminal path if WebEngine is unavailable

## Create a virtual environment

### Linux / macOS

```bash
git clone https://github.com/nima-salamat/sshdock.git
cd sshdock

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Windows PowerShell

```powershell
git clone https://github.com/nima-salamat/sshdock.git
cd sshdock

py -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The development extra installs the application dependencies plus pytest.

You can also install the runtime dependencies directly:

```bash
python -m pip install -r requirements.txt
```

## Run the application

From an activated virtual environment:

```bash
python -m app.main
```

The package also exposes a console entry point:

```bash
sshdock
```

On Windows, the repository includes helper scripts that create `.venv` automatically when needed:

```powershell
.\run_windows.ps1
```

or:

```cmd
run_windows.cmd
```

## Connect to a server

You can either choose a discovered SSH alias or enter a direct target.

Examples:

```text
myserver
192.168.1.50
myserver:2222
root@192.168.1.50
root@192.168.1.50:2222
root@[::1]:2222
```

OpenSSH aliases are discovered from the normal user SSH configuration and, where applicable, the system configuration.

Private-key credentials are handled through the configured authentication flow. Persisted secrets use the operating-system keyring; private keys themselves should never be committed to the repository.

## Run tests manually

Run the complete suite:

```bash
python -m pytest -q
```

Run one test module:

```bash
python -m pytest -q tests/test_ssh_targets.py
```

Run a focused group:

```bash
python -m pytest -q tests/test_ssh_targets.py tests/test_ssh_client_helpers.py
python -m pytest -q tests/test_docker_manager.py tests/test_docker_parsing.py
```

Run by keyword:

```bash
python -m pytest -q -k discovery
python -m pytest -q -k docker
```

Stop on the first failure:

```bash
python -m pytest -q --maxfail=1
```

For a basic syntax check without starting the UI:

```bash
python -m compileall -q app tests
```

The automated suite intentionally favors unit and regression tests that can run without a real SSH server or Docker daemon. Tests that exercise the actual remote environment should be treated as integration/manual checks against a safe test host.

## Development notes

The project uses:

- PySide6 for the desktop UI
- AsyncSSH for SSH transport
- qasync for Qt/asyncio integration
- keyring for persisted credentials
- platformdirs for local application data
- pyte for the fallback terminal renderer
- xterm.js inside Qt WebEngine for the primary terminal experience

Remote command failures are surfaced rather than silently converted into empty data where possible. This distinction matters for administration tools: "the server returned no rows" and "the command failed" are different states.

## Security and operational expectations

SSHDock executes commands on remote machines using the permissions of the SSH account you connect with. A profile with root or passwordless sudo access has the same power here as it has in a normal SSH session.

Use host-key verification, keep private keys protected, avoid storing unnecessary passwords, and test destructive Docker operations on non-production hosts first.

## CI

GitHub Actions runs the test suite on pushes and pull requests. The same local command is used for the main verification step:

```bash
python -m compileall -q app tests
python -m pytest -q
```
