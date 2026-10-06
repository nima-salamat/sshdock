from __future__ import annotations

import json
import shlex
import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DockerResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int


def _clean_container_ref(name: str) -> str:
    """Normalize docker ps Names / ID values before passing to the CLI."""
    if name is None:
        return ""
    text = str(name).strip()
    if not text or text in {"False", "True", "None"}:
        return ""
    # docker ps Names may be "/foo" or "foo,bar"
    text = text.lstrip("/")
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text


class DockerManager:
    _usage_cache: dict[int, tuple[float, set[str], set[str], set[str]]] = {}
    _usage_locks: dict[int, asyncio.Lock] = {}
    # Per-SSH connection: "docker compose" | "docker-compose" | None (unknown)
    _compose_bin: dict[int, str | None] = {}

    def __init__(self, ssh):
        self.ssh = ssh

    @property
    def _usage_key(self) -> int:
        return id(self.ssh)

    async def run(self, args: list[str], timeout: float | None = 30) -> DockerResult:
        # Never leak Python bools / None into the remote CLI.
        clean_args: list[str] = []
        for x in args:
            if isinstance(x, bool) or x is None:
                continue
            s = str(x).strip()
            if not s or s in {"False", "True", "None"}:
                continue
            clean_args.append(s)
        command = "docker " + " ".join(shlex.quote(a) for a in clean_args)
        result = await self.ssh.run(command, timeout)
        exit_code = getattr(result, "exit_code", getattr(result, "exit_status", getattr(result, "returncode", 1)))
        return DockerResult(command, result.stdout or "", result.stderr or "", exit_code)

    async def detect(self):
        result = await self.run(["version", "--format", "{{.Server.Version}}"])
        return result.exit_code == 0, result

    async def ps(self, all_: bool = True):
        args = ["ps"]
        if all_:
            args.append("-a")
        # --no-trunc so full container IDs appear in magic tables
        args += ["--no-trunc", "--format", "{{json .}}"]
        result = await self.run(args)
        return self._json_lines(result)

    async def images(self):
        return self._json_lines(
            await self.run(["images", "--no-trunc", "--format", "{{json .}}"]),
            keys=("ID", "Repository", "Tag", "Size"),
        )

    async def volumes(self):
        return self._json_lines(await self.run(["volume", "ls", "--format", "{{json .}}"]),
                                 keys=("Name", "Driver", "Scope"))

    async def networks(self):
        return self._json_lines(
            await self.run(["network", "ls", "--no-trunc", "--format", "{{json .}}"]),
            keys=("ID", "Name", "Driver", "Scope"),
        )

    async def services(self):
        """Swarm services: docker service ls."""
        return self._json_lines(
            await self.run(["service", "ls", "--format", "{{json .}}"]),
        )

    async def system_df(self) -> list[dict[str, Any]]:
        """Disk usage summary: docker system df (one row per type)."""
        r = await self.run(["system", "df", "--format", "{{json .}}"])
        if r.exit_code == 0 and (r.stdout or "").strip():
            rows: list[dict[str, Any]] = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except json.JSONDecodeError:
                    continue
            if rows:
                return rows
        # Fallback: parse plain table
        r2 = await self.run(["system", "df"])
        rows = []
        lines = (r2.stdout or "").splitlines()
        for line in lines[1:] if lines else []:
            parts = line.split()
            if len(parts) >= 4:
                rows.append({
                    "Type": parts[0],
                    "TotalCount": parts[1],
                    "Active": parts[2],
                    "Size": parts[3],
                    "Reclaimable": " ".join(parts[4:]) if len(parts) > 4 else "",
                })
        return rows

    async def contexts(self):
        return self._json_lines(
            await self.run(["context", "ls", "--format", "{{json .}}"]),
        )

    async def plugins(self):
        return self._json_lines(
            await self.run(["plugin", "ls", "--format", "{{json .}}"]),
        )

    async def nodes(self):
        return self._json_lines(
            await self.run(["node", "ls", "--format", "{{json .}}"]),
        )

    async def stacks(self):
        return self._json_lines(
            await self.run(["stack", "ls", "--format", "{{json .}}"]),
        )

    async def secrets(self):
        return self._json_lines(
            await self.run(["secret", "ls", "--format", "{{json .}}"]),
        )

    async def configs(self):
        return self._json_lines(
            await self.run(["config", "ls", "--format", "{{json .}}"]),
        )

    async def resource_usage(self, max_age: float = 5.0):
        """Return resources that are actually referenced by containers.

        Source of truth is always container inspect (``docker ps -a`` mounts +
        networks), including *stopped* containers.  Optional RefCount /
        network ``.Containers`` counts are merged when available, but many
        Docker installs leave ``UsageData`` empty (RefCount = -1) — relying on
        that alone marked every volume/network as unused.
        """
        key = self._usage_key
        now = time.monotonic()
        cached = self._usage_cache.get(key)
        if cached and now - cached[0] < max_age:
            return set(cached[1]), set(cached[2])

        lock = self._usage_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._usage_cache.get(key)
            if cached and now - cached[0] < max_age:
                return set(cached[1]), set(cached[2])

            used_volumes: set[str] = set()
            used_networks: set[str] = set()
            used_images: set[str] = set()

            # ── 1) Primary: mounts + networks from every container (running + stopped)
            # This is the only reliable signal when UsageData is missing.
            try:
                result = await self.ssh.run(
                    "docker ps -aq 2>/dev/null | xargs -r docker inspect --format "
                    + shlex.quote(
                        '{{range .Mounts}}{{if eq .Type "volume"}}V\t{{.Name}}\n{{end}}{{end}}'
                        "{{range $k,$v := .NetworkSettings.Networks}}N\t{{$k}}\n{{end}}"
                    ),
                    timeout=45,
                )
                for line in (result.stdout or "").splitlines():
                    kind, sep, value = line.partition("\t")
                    if not sep or not value.strip():
                        continue
                    if kind == "V":
                        used_volumes.add(value.strip())
                    elif kind == "N":
                        used_networks.add(value.strip())
            except Exception:
                pass

            # ── 2) Optional: RefCount / live network endpoints (when Docker reports them)
            # Use xargs to avoid ARG_MAX; ignore RefCount <= 0 and the -1 "unknown" sentinel.
            volume_script = (
                "docker volume ls -q 2>/dev/null | xargs -r docker volume inspect "
                "--format '{{.Name}}\\t{{if .UsageData}}{{.UsageData.RefCount}}{{else}}-1{{end}}' "
                "2>/dev/null || true"
            )
            network_script = (
                "docker network ls -q 2>/dev/null | xargs -r docker network inspect "
                "--format '{{.Name}}\\t{{len .Containers}}' 2>/dev/null || true"
            )
            try:
                vres, nres = await asyncio.gather(
                    self.ssh.run("sh -lc " + shlex.quote(volume_script), timeout=30),
                    self.ssh.run("sh -lc " + shlex.quote(network_script), timeout=30),
                )
                for line in (vres.stdout or "").splitlines():
                    name, sep, count = line.partition("\t")
                    if sep and name.strip():
                        try:
                            if int(count.strip()) > 0:
                                used_volumes.add(name.strip())
                        except ValueError:
                            pass
                for line in (nres.stdout or "").splitlines():
                    name, sep, count = line.partition("\t")
                    if sep and name.strip():
                        try:
                            if int(count.strip()) > 0:
                                used_networks.add(name.strip())
                        except ValueError:
                            pass
            except Exception:
                pass

            # Built-in networks are never "unused" for prune purposes.
            used_networks.update({"bridge", "host", "none"})

            try:
                image_result = await self.ssh.run(
                    "docker ps -a --format 'I\t{{.Image}}\t{{.ImageID}}'", timeout=30
                )
                for line in (image_result.stdout or "").splitlines():
                    parts = line.split("\t", 2)
                    if len(parts) < 3:
                        continue
                    image_ref, image_id = (part.strip() for part in parts[1:3])
                    if image_ref:
                        used_images.add(image_ref)
                        if ":" in image_ref:
                            used_images.add(image_ref.rsplit(":", 1)[0])
                    if image_id:
                        used_images.add(image_id)
                        short_id = image_id.removeprefix("sha256:")
                        if short_id:
                            used_images.add(short_id)
                            used_images.add(short_id[:12])
            except Exception:
                pass

            self._usage_cache[key] = (time.monotonic(), used_volumes, used_networks, used_images)
            return set(used_volumes), set(used_networks)

    async def used_images(self, max_age: float = 5.0) -> set[str]:
        """Image names / ids currently referenced by any container."""
        key = self._usage_key
        now = time.monotonic()
        cached = self._usage_cache.get(key)
        if cached and now - cached[0] < max_age and len(cached) >= 4:
            return set(cached[3])
        await self.resource_usage(max_age=0)  # refresh
        cached = self._usage_cache.get(key)
        if cached and len(cached) >= 4:
            return set(cached[3])
        return set()

    async def inspect(self, name: str):

        ref = _clean_container_ref(name)
        if not ref:
            return DockerResult("docker inspect", "", "empty container name", 1)
        return await self.run(["inspect", ref])

    async def action(self, action: str, name: str):
        ref = _clean_container_ref(name)
        if not ref:
            return DockerResult(f"docker {action}", "", "empty container name", 1)
        # Never treat flags as the action name
        act = str(action).strip().lstrip("-")
        return await self.run([act, ref], timeout=120)

    async def remove(self, name: str, force: bool = False):
        ref = _clean_container_ref(name)
        if not ref:
            return DockerResult("docker rm", "", "empty container name", 1)
        args = ["rm"]
        if force is True:
            args.append("-f")
        args.append(ref)
        return await self.run(args, timeout=120)

    async def logs(self, name: str, tail: str = "300", follow: bool = True):
        ref = _clean_container_ref(name)
        command = "docker logs --timestamps --tail " + shlex.quote(str(tail))
        if follow:
            command += " -f"
        command += " " + shlex.quote(ref)
        return await self.ssh.start_command(command)

    async def stats(self, name: str | None = None):
        args = ["stats", "--no-stream", "--format", "{{json .}}"]
        if name:
            ref = _clean_container_ref(name)
            if ref:
                args.append(ref)
        return self._json_lines(await self.run(args))

    async def stats_all_with_quota(self) -> list[dict[str, Any]]:
        """Fetch stats for all running containers + their CPU quota.

        Returns a list of dicts, each containing the original
        ``docker stats`` fields PLUS a ``_quota_cores`` field (the
        container's CPU quota in cores, or None if unlimited) and
        ``_cpu_pct_normalised`` (CPU% re-scaled relative to the quota
        instead of host capacity).

        This fixes the long-standing bug where a container limited to
        ``--cpus=2`` on an 8-core host would show 25% CPU when it was
        actually at 100% of its quota.
        """
        from app.docker.stats_math import (
            compute_container_cpu_pct,
            get_container_cpu_quota,
            normalise_cpu_pct_by_quota,
            parse_docker_stats_json_line,
        )
        # 1. Get the list of running container IDs + names.
        ps_lines = await self.ps(all_=False)
        if not ps_lines:
            return []
        # 2. Fetch inspect data for all containers in one call (fast).
        ids = [str(c.get("ID") or "") for c in ps_lines if c.get("ID")]
        inspect_map: dict[str, dict[str, Any]] = {}
        if ids:
            inspect_result = await self.run(
                ["inspect", "--format", "{{json .}}"] + ids,
                timeout=30,
            )
            for line in inspect_result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    cid = obj.get("Id") or ""
                    if cid:
                        inspect_map[cid] = obj
                except json.JSONDecodeError:
                    continue
        # 3. Fetch docker stats for all running containers at once.
        stats_result = await self.run(
            ["stats", "--no-stream", "--format", "{{json .}}"],
            timeout=30,
        )
        # 4. Merge + compute.
        results: list[dict[str, Any]] = []
        for line in stats_result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = parse_docker_stats_json_line(line)
            if parsed is None:
                continue
            # Look up the container's inspect data by matching name or ID.
            container_name = (parsed.get("Name") or "").lstrip("/")
            inspect_data = None
            for cid, idata in inspect_map.items():
                iname = (idata.get("Name") or "").lstrip("/")
                if iname == container_name or cid.startswith(container_name):
                    inspect_data = idata
                    break
            quota = None
            if inspect_data:
                quota = get_container_cpu_quota(inspect_data)
            parsed["_quota_cores"] = quota
            cpu_pct = parsed.get("_cpu_pct_num")
            if cpu_pct is not None:
                parsed["_cpu_pct_normalised"] = normalise_cpu_pct_by_quota(cpu_pct, quota)
            else:
                parsed["_cpu_pct_normalised"] = None
            results.append(parsed)
        return results

    async def events(self):
        return await self.ssh.start_command('docker events --format "{{json .}}"')

    async def detect_compose_bin(self, force: bool = False) -> str | None:
        """Return the remote Compose binary with single-flight detection.

        Bulk Compose actions often ask this concurrently for several projects.
        The previous implementation let every caller observe an empty cache and
        then queue a separate ``compose version`` SSH command behind the command
        lock. That made a 5-project bulk action look like it was hanging before
        the real commands even started.
        """
        key = self._usage_key
        locks = getattr(self.ssh, "_compose_detect_locks", None)
        if locks is None:
            locks = {}
            setattr(self.ssh, "_compose_detect_locks", locks)
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force and key in self._compose_bin:
                return self._compose_bin[key]
            # Prefer modern plugin first.
            r = await self.run(["compose", "version"], timeout=15)
            if r.exit_code == 0:
                self._compose_bin[key] = "docker compose"
                return "docker compose"
            # Fallback to standalone v1 binary.
            r2 = await self.ssh.run("docker-compose version", timeout=15)
            if getattr(r2, "exit_status", 1) == 0 or (getattr(r2, "stdout", "") or "").strip():
                self._compose_bin[key] = "docker-compose"
                return "docker-compose"
            self._compose_bin[key] = None
            return None

    async def compose(self, args: list[str], timeout: float | None = 3600) -> DockerResult:
        """Run compose with automatic fallback: docker compose → docker-compose."""
        bin_cmd = await self.detect_compose_bin()
        if not bin_cmd:
            return DockerResult(
                "compose " + " ".join(args),
                "",
                "Neither 'docker compose' nor 'docker-compose' is available on the remote host.",
                127,
            )
        if bin_cmd == "docker compose":
            return await self.run(["compose", *args], timeout=timeout)
        # docker-compose (v1) — pass args directly after the binary
        clean_args: list[str] = []
        for x in args:
            if isinstance(x, bool) or x is None:
                continue
            s = str(x).strip()
            if not s or s in {"False", "True", "None"}:
                continue
            clean_args.append(s)
        command = "docker-compose " + " ".join(shlex.quote(a) for a in clean_args)
        result = await self.ssh.run(command, timeout)
        exit_code = getattr(result, "exit_code", getattr(result, "exit_status", getattr(result, "returncode", 1)))
        return DockerResult(command, result.stdout or "", result.stderr or "", exit_code)

    async def compose_ls(self, all_: bool = True) -> list[dict[str, Any]]:
        """List compose projects; include stopped when all_=True (docker compose ls -a)."""
        base = ["ls"]
        if all_:
            base.append("-a")  # show stopped / down projects
        r = await self.compose([*base, "--format", "json"])
        if r.exit_code == 0 and (r.stdout or "").strip():
            try:
                rows = json.loads(r.stdout)
                if isinstance(rows, dict):
                    rows = rows.get("Projects") or rows.get("projects") or []
                if isinstance(rows, list):
                    return rows
            except json.JSONDecodeError:
                pass
        # Fallback: plain table (retry with and without -a for older binaries)
        for extra in (["-a"] if all_ else [], []):
            r2 = await self.compose(["ls", *extra])
            if r2.exit_code != 0:
                continue
            rows = []
            lines = (r2.stdout or "").splitlines()
            for line in lines[1:] if lines and "NAME" in lines[0].upper() else lines:
                parts = line.split()
                if not parts:
                    continue
                name = parts[0]
                status = " ".join(parts[1:]) if len(parts) > 1 else "—"
                rows.append({"Name": name, "Status": status, "ConfigFiles": ""})
            if rows:
                return rows
        return []

    async def compose_ps(self, all_: bool = True) -> list[dict[str, Any]]:
        """Containers belonging to compose projects: docker compose ps."""
        args = ["ps"]
        if all_:
            args.append("-a")
        r = await self.compose([*args, "--format", "json"])
        if r.exit_code == 0 and (r.stdout or "").strip():
            rows: list[dict[str, Any]] = []
            # compose ps --format json may be NDJSON or a single array
            text = r.stdout.strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [x for x in parsed if isinstance(x, dict)]
                if isinstance(parsed, dict):
                    return [parsed]
            except json.JSONDecodeError:
                pass
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except json.JSONDecodeError:
                    continue
            if rows:
                return rows
        # Plain-text fallback
        r2 = await self.compose([*args])
        rows = []
        lines = (r2.stdout or "").splitlines()
        for line in lines[1:] if lines else []:
            parts = line.split()
            if not parts:
                continue
            rows.append({
                "Name": parts[0],
                "Service": parts[1] if len(parts) > 1 else "",
                "Status": " ".join(parts[2:]) if len(parts) > 2 else "",
            })
        return rows

    async def prune(self, target: str):
        return await self.run([target, "prune", "-f"], timeout=300)

    async def container_exec(self, name: str, command: list[str], timeout: float = 60):
        ref = _clean_container_ref(name)
        return await self.run(["exec", ref, *command], timeout=timeout)

    async def container_path(self, name: str, path: str, timeout: float = 60) -> dict[str, str] | None:
        ref = _clean_container_ref(name)
        script = "p=$1; if [ -d \"$p\" ]; then printf 'd\n'; elif [ -f \"$p\" ]; then printf 'f\n'; else exit 4; fi; stat -c '%s\t%Y' -- \"$p\" 2>/dev/null || stat -f '%z\t%m' -- \"$p\" 2>/dev/null"
        result = await self.run(["exec", ref, "sh", "-lc", script, "--", path], timeout=timeout)
        if result.exit_code != 0:
            return None
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return None
        kind = lines[0].strip()
        size, _, mtime = lines[1].partition("\t")
        return {"type": kind, "size": size, "mtime": mtime}

    async def listening_ports(self, timeout: float = 25) -> dict[int, str]:
        """Host TCP/UDP ports in use on the remote machine.

        Returns map of port -> short reason (docker / process).
        Combines `docker ps` published ports with `ss`/`netstat` when available.
        """
        used: dict[int, str] = {}
        # 1) Docker published ports
        try:
            rows = await self.ps(all_=True)
        except Exception:
            rows = []
        for row in rows:
            ports_str = str(row.get("Ports") or "")
            cname = str(row.get("Names") or row.get("Name") or row.get("ID") or "?")
            cname = cname.lstrip("/").split(",")[0]
            for part in ports_str.replace(",", " ").split():
                # formats: 0.0.0.0:8080->80/tcp  :::8080->80/tcp  8080/tcp
                if "->" in part:
                    left = part.split("->", 1)[0]
                    if ":" in left:
                        host_p = left.rsplit(":", 1)[-1]
                    else:
                        host_p = left
                    if host_p.isdigit():
                        used[int(host_p)] = f"docker:{cname}"
        # 2) OS listeners (ss preferred)
        script = r"""
set +e
if command -v ss >/dev/null 2>&1; then
  ss -lntuH 2>/dev/null | awk '{print $5}' 
elif command -v netstat >/dev/null 2>&1; then
  netstat -lntu 2>/dev/null | awk 'NR>2 {print $4}'
fi
"""
        try:
            result = await self.ssh.run("sh -lc " + shlex.quote(script), timeout=timeout)
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                # *:8080  0.0.0.0:8080  [::]:8080  127.0.0.1:53
                if line.endswith(":*"):
                    continue
                if ":" in line:
                    port_s = line.rsplit(":", 1)[-1]
                    # strip ipv6 brackets residue
                    port_s = port_s.strip("[]")
                    if port_s.isdigit():
                        p = int(port_s)
                        if p not in used:
                            used[p] = "host"
        except Exception:
            pass
        return used

    @staticmethod
    def parse_host_ports(mappings: str) -> list[int]:
        """Extract host-side ports from docker -p style mappings.

        Examples: 8080:80, 127.0.0.1:8443:443, 80, 0.0.0.0:3000:3000/tcp
        """
        ports: list[int] = []
        for raw in (mappings or "").split(","):
            part = raw.strip()
            if not part:
                continue
            # drop protocol suffix
            if "/" in part:
                part = part.split("/", 1)[0]
            bits = part.split(":")
            host_p = None
            if len(bits) == 1 and bits[0].isdigit():
                host_p = int(bits[0])
            elif len(bits) == 2 and bits[0].isdigit():
                host_p = int(bits[0])  # host:container
            elif len(bits) >= 3:
                # ip:host:container
                if bits[-2].isdigit():
                    host_p = int(bits[-2])
            if host_p is not None and 1 <= host_p <= 65535:
                ports.append(host_p)
        return ports

    @staticmethod
    def _json_lines(result: DockerResult, keys: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        if result.exit_code != 0:
            import logging
            detail = (result.stderr or result.stdout or "Docker command failed").strip()
            logging.getLogger(__name__).warning(
                "docker command failed (exit_code=%s): %s — %s",
                result.exit_code,
                result.command[:160],
                detail[:300],
            )
            # A failed list command is not the same as an empty result. Raise
            # so the UI can show the actual failure instead of rendering an
            # empty table and making a Docker/SSH problem look like zero rows.
            raise RuntimeError(
                f"Docker command failed (exit {result.exit_code}): {detail[:300]}"
            )
        items: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if keys:
                raw_item = item
                item = {k: raw_item.get(k) for k in keys}
                item["_raw"] = raw_item
            items.append(item)
        return items
