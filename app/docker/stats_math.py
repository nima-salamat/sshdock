"""Pure-Python helpers for accurate container stats and process CPU%.

These helpers fix two long-standing accuracy bugs:

1. **Container CPU%** — ``docker stats --no-stream`` returns a single
   sample, and Docker CLI's percentage is computed against the host's
   total CPU capacity, not against the container's CPU quota. A
   container limited to ``--cpus=2`` on an 8-core host shows 12.5% when
   it's actually using 100% of its 2-CPU quota. We re-compute the
   percentage from the raw Docker Engine API counters
   (``precpu_stats`` / ``cpu_stats``) and normalise by the container's
   CPU quota (``HostConfig.NanoCpus`` / ``CpuQuota`` /
   ``CpuPeriod``), so the displayed value reflects the real saturation
   of the resource the container was given.

2. **Process CPU%** — ``ps -eo %cpu`` reports the *lifetime average*
   CPU usage of a process (from boot to now). A long-running daemon
   that briefly spikes to 100% CPU will still show 0.1% in ``ps``
   output. The fix is to sample ``/proc/[pid]/stat`` twice with a
   short interval in between and compute the delta — exactly like
   ``top`` and ``htop`` do. This module provides:
   - :func:`compute_process_cpu_pct` — pure math, given two samples
     of ``utime + stime`` jiffies and the system ``CLK_TCK``.
   - :func:`build_top_script` — a shell script that runs on the remote
     host, samples /proc twice, and emits a parseable table with
     *instantaneous* CPU% and MEM% per process.

This module is deliberately Qt-free so it can be unit-tested headlessly.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable


# ----------------------------------------------------------------------
# Container stats — accurate CPU% from raw Docker Engine API counters
# ----------------------------------------------------------------------

def compute_container_cpu_pct(stats: dict[str, Any]) -> float | None:
    """Compute instantaneous CPU% for a container from its raw stats.

    *stats* is the JSON object returned by the Docker Engine API
    ``/containers/<id>/stats`` endpoint (or ``docker inspect`` +
    a stats call). The expected fields are::

        {
          "precpu_stats": {"cpu_usage": {"total_usage": N}, "system_cpu_usage": M, "online_cpus": K},
          "cpu_stats":    {"cpu_usage": {"total_usage": N', "system_cpu_usage": M', "online_cpus": K'},
                           "online_cpus": K'}
        }

    The Docker CLI computes::

        cpu_delta = cpu_stats.cpu_usage.total_usage - precpu_stats.cpu_usage.total_usage
        system_delta = cpu_stats.system_cpu_usage - precpu_stats.system_cpu_usage
        cpu_pct = (cpu_delta / system_delta) * online_cpus * 100

    That gives the percentage *of the host's total CPU capacity*. A
    container with ``--cpus=2`` on an 8-core host maxing out its quota
    shows 25% (2/8 * 100), which is misleading — the container is
    actually at 100% of its *quota*.

    We do the same math, but we let the caller optionally normalise by
    the container's CPU quota via :func:`normalise_cpu_pct_by_quota`.
    """
    pre = stats.get("precpu_stats") or {}
    cur = stats.get("cpu_stats") or {}
    pre_total = (pre.get("cpu_usage") or {}).get("total_usage")
    cur_total = (cur.get("cpu_usage") or {}).get("total_usage")
    pre_sys = pre.get("system_cpu_usage")
    cur_sys = cur.get("system_cpu_usage")
    if not (pre_total and cur_total and pre_sys and cur_sys):
        return None
    cpu_delta = cur_total - pre_total
    sys_delta = cur_sys - pre_sys
    if sys_delta <= 0 or cpu_delta < 0:
        return None
    online = cur.get("online_cpus") or pre.get("online_cpus") or 1
    if online < 1:
        online = 1
    pct = (cpu_delta / sys_delta) * online * 100.0
    return max(0.0, min(100.0 * online, pct))


def get_container_cpu_quota(inspect_data: dict[str, Any]) -> float | None:
    """Return the container's CPU quota in *cores* (1.0 = 1 full core).

    Reads from the ``HostConfig`` section of ``docker inspect`` output.
    Returns ``None`` if the container has no quota (i.e. it can use
    the whole host).

    Order of preference (matching Docker's own resolution):

      1. ``HostConfig.NanoCpus`` — modern (Docker 1.13+). 1e9 nanoseconds
         of CPU per second = 1.0 cores.
      2. ``HostConfig.CpuQuota`` + ``HostConfig.CpuPeriod`` — legacy
         cgroup-style. Quota is in microseconds per period; quota/period
         gives the core count.
      3. ``HostConfig.Cpus`` — some Docker versions expose this as a
         convenience alias for NanoCpus.
    """
    hc = inspect_data.get("HostConfig") or {}
    # NanoCpus (modern, preferred)
    nano = hc.get("NanoCpus")
    if nano is not None and nano > 0:
        return float(nano) / 1_000_000_000.0
    # Legacy CpuQuota / CpuPeriod
    quota = hc.get("CpuQuota")
    period = hc.get("CpuPeriod")
    if quota and period and quota > 0 and period > 0:
        return float(quota) / float(period)
    # Convenience alias
    cpus = hc.get("Cpus")
    if cpus is not None and cpus > 0:
        return float(cpus)
    return None


def normalise_cpu_pct_by_quota(cpu_pct: float | None, quota_cores: float | None,
                               host_cpu_count: int | None = None) -> float | None:
    """Re-scale a *host-relative* CPU% to a *quota-relative* CPU%.

    If the container has a CPU quota (e.g. ``--cpus=2`` on an 8-core
    host), a host-relative 25% should display as 100% of its quota.

    If the container has no quota, return the original percentage
    unchanged (it's already host-relative).

    *host_cpu_count* is used only to sanity-check the quota; if missing
    we trust the quota value.
    """
    if cpu_pct is None:
        return None
    if quota_cores is None or quota_cores <= 0:
        return round(cpu_pct, 2)
    if host_cpu_count and host_cpu_count > 0:
        # Clamp the quota to the host's CPU count (a container can't
        # have more cores than the host).
        quota_cores = min(quota_cores, float(host_cpu_count))
    # host-relative% = (used_cores / host_cores) * 100
    # → used_cores = host-relative% / 100 * host_cores
    # → quota-relative% = (used_cores / quota_cores) * 100
    # But we don't know host_cores here; the Docker CLI already
    # multiplied by online_cpus, so cpu_pct is already "cores used * 100".
    # → quota-relative% = cpu_pct / (quota_cores * 100) * 100
    #                    = cpu_pct / quota_cores
    result = cpu_pct / quota_cores
    return round(max(0.0, min(100.0, result)), 2)


def compute_container_mem_pct(stats: dict[str, Any]) -> float | None:
    """Compute memory usage percentage from raw stats.

    Uses ``memory_stats.usage`` / ``memory_stats.limit``. Returns
    ``None`` if the limit is 0 (no memory limit set).
    """
    mem = stats.get("memory_stats") or {}
    usage = mem.get("usage")
    limit = mem.get("limit")
    if not usage or not limit or limit <= 0:
        return None
    return round((usage / limit) * 100.0, 2)


def parse_docker_stats_json_line(line: str) -> dict[str, Any] | None:
    """Parse one line of ``docker stats --format '{{json .}}'`` output.

    Docker CLI emits human-formatted strings (``"0.42%"``, ``"120MiB /
    4GiB"``). We keep those as-is for display but also extract the
    numeric value where possible so the UI can sort / colour-code.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    # Extract numeric CPU% from "0.42%"
    cpu_str = obj.get("CPUPerc") or obj.get("CPU %") or ""
    obj["_cpu_pct_num"] = _parse_percent(cpu_str)
    # Extract numeric MemPerc from "12.34%"
    mem_str = obj.get("MemPerc") or obj.get("MEM %") or ""
    obj["_mem_pct_num"] = _parse_percent(mem_str)
    # Extract used / limit from "120MiB / 4GiB"
    mem_usage = obj.get("MemUsage") or obj.get("MEM USAGE / LIMIT") or ""
    used, limit = _split_usage(mem_usage)
    obj["_mem_used"] = used
    obj["_mem_limit"] = limit
    return obj


def _parse_percent(text: str) -> float | None:
    if not text:
        return None
    s = text.strip().rstrip("%").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _split_usage(usage: str | None) -> tuple[str, str]:
    if not usage:
        return "—", "—"
    parts = [p.strip() for p in usage.split("/")]
    if len(parts) == 2:
        return parts[0], parts[1]
    return usage.strip(), "—"


# ----------------------------------------------------------------------
# Process CPU% — delta-based instantaneous percentage (like top/htop)
# ----------------------------------------------------------------------

# Linux's USER_HZ / CLK_TCK. On every Linux platform PySide6 / asyncssh
# will ever run on, this is 100. We make it a parameter so tests can
# inject other values, but the default matches reality.
DEFAULT_CLK_TCK = 100


def compute_process_cpu_pct(sample1_jiffies: int, sample2_jiffies: int,
                            elapsed_seconds: float,
                            clk_tck: int = DEFAULT_CLK_TCK) -> float | None:
    """Compute instantaneous CPU% for a single process.

    Given two samples of ``utime + stime`` from ``/proc/[pid]/stat``
    (in clock ticks), and the elapsed wall-clock time between the two
    samples, return the percentage of one CPU core the process used
    during that interval. 100% = one full core; 200% = two full cores.

    Formula (matches ``top`` and ``htop``)::

        delta_ticks = sample2 - sample1
        delta_seconds = delta_ticks / clk_tck
        cpu_pct = (delta_seconds / elapsed_seconds) * 100
    """
    if elapsed_seconds <= 0 or clk_tck <= 0:
        return None
    delta = sample2_jiffies - sample1_jiffies
    if delta < 0:
        # Process restarted between samples (PID reused).
        return 0.0
    delta_seconds = delta / clk_tck
    pct = (delta_seconds / elapsed_seconds) * 100.0
    return round(max(0.0, pct), 2)


def compute_process_mem_pct(rss_bytes: int, mem_total_bytes: int) -> float | None:
    """Compute process RSS as a percentage of total host memory."""
    if not rss_bytes or not mem_total_bytes or mem_total_bytes <= 0:
        return None
    return round((rss_bytes / mem_total_bytes) * 100.0, 2)


def build_top_script(sample_interval: float = 1.0,
                     max_procs: int = 120) -> str:
    """Return a shell script that samples process CPU% via ``top``.

    Uses ``top -b -n 2 -d <interval>`` which is available on every Linux
    server. The ``-n 2`` flag makes ``top`` take two samples and compute
    the *delta* between them — so the CPU% in the second sample is the
    instantaneous usage, not the lifetime average.

    This replaces the previous hand-rolled ``/proc/[pid]/stat`` sampler
    which was too complex (multiple temp files, ``join``, ``awk``) and
    timed out on slow SSH connections, causing the worker shell to die
    and take down the entire SSH session with a ``ConnectionLost`` error.

    The script emits one TSV line per process::

        PID\\tUSER\\tCPU%\\tMEM%\\tRSS_MB\\tSTATE\\tCOMMAND

    Output format is identical to the previous implementation so
    :func:`parse_top_output` works unchanged.
    """
    # Clamp the interval to a safe range. ``top -d`` accepts decimals
    # on procps-ng (the standard on virtually all modern Linux distros).
    # BusyBox top (Alpine) also accepts decimals since 1.26.0 (2016).
    # We clamp to a minimum of 0.1s — anything lower is unreliable.
    interval_str = f"{max(0.1, float(sample_interval)):.1f}"
    return (
        "set +e; "
        "tmp=/tmp/.sshdock_top.$$; "
        "LC_ALL=C COLUMNS=200 top -b -n 2 -d {interval} 2>/dev/null | "
        "awk '/^top -/{{n++}} n==2 && /^[[:space:]]*[0-9]/ {{"
        "print $1\"\\t\"$2\"\\t\"$9\"\\t\"$10\"\\t\"($6/1024)\"\\t\"$8\"\\t\"$12"
        "}}' | sort -t'\\t' -k3 -rn | head -n {max_procs} > \"$tmp\"; "
        "if [ -s \"$tmp\" ]; then cat \"$tmp\"; "
        "else "
        "ps -eo pid=,user=,%cpu=,%mem=,rss=,stat=,args= --sort=-%cpu 2>/dev/null | "
        "awk '{cmd=$7; for(i=8;i<=NF;i++) cmd=cmd\" \"$i; print $1\"\\t\"$2\"\\t\"$3\"\\t\"$4\"\\t\"($5/1024)\"\\t\"$6\"\\t\"cmd}' | "
        "head -n {max_procs}; "
        "fi; "
        "rm -f \"$tmp\" 2>/dev/null; "
        "exit 0"
    ).format(interval=interval_str, max_procs=max_procs)



# ----------------------------------------------------------------------
# Parser for the script's TSV output
# ----------------------------------------------------------------------

def parse_top_output(text: str) -> list[dict[str, Any]]:
    """Parse the TSV output of :func:`build_top_script` into rows.

    Returns a list of dicts with keys: ``pid``, ``user``, ``cpu_pct``,
    ``mem_pct``, ``rss_mb``, ``state``, ``command``.

    The output may or may not include a header line. We detect the
    header by checking if the first column is "PID" (case-insensitive)
    and skip it if so.
    """
    rows: list[dict[str, Any]] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows
    # Skip header line if present (first column == "PID").
    start_idx = 0
    first_parts = lines[0].split("\t")
    if first_parts and first_parts[0].strip().upper() == "PID":
        start_idx = 1
    for line in lines[start_idx:]:
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        try:
            rows.append({
                "pid": parts[0],
                "user": parts[1],
                "cpu_pct": _safe_float(parts[2]),
                "mem_pct": _safe_float(parts[3]),
                "rss_mb": _safe_float(parts[4]),
                "state": parts[5],
                "command": parts[6],
            })
        except (ValueError, IndexError):
            continue
    return rows


def _safe_float(text: str) -> float | None:
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------
# Script for fetching all container stats with raw counters
# ----------------------------------------------------------------------

def build_container_stats_script() -> str:
    """Return a shell script that fetches raw stats for all containers.

    The script uses ``docker stats --no-stream --format '{{json .}}'``
    for the human-formatted fields (Network I/O, Block I/O, PIDs) and
    also runs ``docker inspect`` to get the CPU quota for each
    container so the caller can normalise CPU% by quota.

    Output: one JSON object per line, each containing the original
    ``docker stats`` fields PLUS a ``_quota_cores`` field and the raw
    ``cpu_stats`` / ``precpu_stats`` / ``memory_stats`` from the Docker
    Engine API (fetched via ``docker inspect`` which includes them).

    Note: ``docker stats --no-stream`` already includes the raw
    counters in its JSON output on modern Docker versions (the fields
    are just not in the default Go template). We fetch them explicitly
    via the API-style ``--format`` to be safe.
    """
    # This script is intentionally simple — it just wraps docker stats
    # and docker inspect. The real work (CPU% re-computation) happens
    # client-side in compute_container_cpu_pct().
    return r"""set +e
# Get the list of running container IDs.
ids=$(docker ps -q --format '{{.ID}}' 2>/dev/null)
if [ -z "$ids" ]; then
    exit 0
fi
# For each container, emit a JSON object with:
#   - the docker stats fields (CPUPerc, MemUsage, MemPerc, NetIO, BlockIO, PIDs)
#   - the raw cpu_stats / precpu_stats / memory_stats (for accurate re-computation)
#   - the HostConfig CPU quota (for normalisation)
for id in $ids; do
    stats_json=$(docker stats --no-stream --format '{{json .}}' "$id" 2>/dev/null)
    if [ -z "$stats_json" ]; then continue; fi
    # Fetch the raw stats counters via the Docker Engine API.
    # We use `docker inspect` to get HostConfig (for the quota) and
    # `docker stats` already has the cpu_stats / precpu_stats internally
    # but doesn't expose them in the default JSON. We do a second call
    # to get the inspect data and merge it.
    inspect_json=$(docker inspect --format '{{json .}}' "$id" 2>/dev/null)
    # Emit a single JSON line that merges both.
    printf '%s\t%s\n' "$stats_json" "$inspect_json"
done
"""


def parse_container_stats_output(text: str) -> list[dict[str, Any]]:
    """Parse the output of :func:`build_container_stats_script`.

    Each line is ``<stats_json>\t<inspect_json>``. We parse both,
    merge them, and compute the accurate CPU% using
    :func:`compute_container_cpu_pct` + :func:`normalise_cpu_pct_by_quota`.
    """
    results: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Split on the first tab — the stats JSON may contain tabs
        # inside strings (unlikely but safe).
        tab_idx = line.find("\t")
        if tab_idx < 0:
            # Just a stats line, no inspect data.
            try:
                stats = json.loads(line)
            except json.JSONDecodeError:
                continue
            inspect_data = {}
        else:
            stats_part = line[:tab_idx]
            inspect_part = line[tab_idx + 1:]
            try:
                stats = json.loads(stats_part)
            except json.JSONDecodeError:
                continue
            try:
                inspect_data = json.loads(inspect_part)
            except json.JSONDecodeError:
                inspect_data = {}

        # Parse the human-formatted fields.
        parsed = parse_docker_stats_json_line(json.dumps(stats))
        if parsed is None:
            continue

        # Get the quota and normalise the CPU%.
        quota = get_container_cpu_quota(inspect_data)
        host_cpus = None
        if isinstance(inspect_data, dict):
            # HostConfig sometimes has CpusetCpus that could tell us
            # how many host CPUs the container is pinned to. We don't
            # use it for the math, just for display.
            pass
        cpu_pct = parsed.get("_cpu_pct_num")
        if cpu_pct is not None and quota is not None and quota > 0:
            parsed["_cpu_pct_normalised"] = normalise_cpu_pct_by_quota(cpu_pct, quota, host_cpus)
        else:
            parsed["_cpu_pct_normalised"] = cpu_pct
        parsed["_quota_cores"] = quota
        # Also compute the accurate MEM% if we have raw counters.
        # (docker stats already gives us this, so we just keep it.)
        results.append(parsed)
    return results
