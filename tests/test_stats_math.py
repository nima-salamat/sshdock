"""Tests for accurate container stats and process CPU% calculation.

These tests pin the behaviour of the pure-Python helpers in
``app.docker.stats_math`` that fix two accuracy bugs:

1. **Container CPU%** — Docker CLI computes CPU% relative to the host's
   total CPU capacity. A container with ``--cpus=2`` on an 8-core host
   shows 25% when it's actually at 100% of its 2-CPU quota. We
   re-scale the percentage by the quota.

2. **Process CPU%** — ``ps -eo %cpu`` reports the *lifetime average*
   CPU usage. A daemon running for a week at 0.1% that spikes to 100%
   will still show 0.1% in ``ps`` output. We sample ``/proc/[pid]/stat``
   twice and compute the delta, exactly like ``top`` / ``htop``.
"""
from __future__ import annotations

import pytest

from app.docker.stats_math import (
    DEFAULT_CLK_TCK,
    build_top_script,
    compute_container_cpu_pct,
    compute_container_mem_pct,
    compute_process_cpu_pct,
    compute_process_mem_pct,
    get_container_cpu_quota,
    normalise_cpu_pct_by_quota,
    parse_docker_stats_json_line,
    parse_top_output,
)


# --------------------------------------------------------------------------
# Container CPU% — raw counter computation
# --------------------------------------------------------------------------

def test_container_cpu_pct_basic_delta():
    """Two samples with a known delta should give the expected percentage."""
    stats = {
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000},
            "system_cpu_usage": 10_000_000,
            "online_cpus": 4,
        },
        "cpu_stats": {
            "cpu_usage": {"total_usage": 1_500_000},
            "system_cpu_usage": 11_000_000,
            "online_cpus": 4,
        },
    }
    # cpu_delta = 500_000, sys_delta = 1_000_000
    # pct = (500_000 / 1_000_000) * 4 * 100 = 200.0
    pct = compute_container_cpu_pct(stats)
    assert pct == pytest.approx(200.0, abs=0.01)


def test_container_cpu_pct_no_delta_returns_zero():
    """If the container used 0 CPU between samples, percentage is 0."""
    stats = {
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000},
            "system_cpu_usage": 10_000_000,
            "online_cpus": 2,
        },
        "cpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000},
            "system_cpu_usage": 11_000_000,
            "online_cpus": 2,
        },
    }
    pct = compute_container_cpu_pct(stats)
    assert pct == 0.0


def test_container_cpu_pct_missing_fields_returns_none():
    """Missing precpu/cpu stats should return None, not crash."""
    assert compute_container_cpu_pct({}) is None
    assert compute_container_cpu_pct({"cpu_stats": {}}) is None
    assert compute_container_cpu_pct({
        "precpu_stats": {"cpu_usage": {}},
        "cpu_stats": {"cpu_usage": {"total_usage": 100}},
    }) is None


def test_container_cpu_pct_negative_delta_returns_none():
    """If cpu_delta is negative (clock rollover / restart), return None."""
    stats = {
        "precpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000},
            "system_cpu_usage": 10_000_000,
        },
        "cpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000},  # less than precpu
            "system_cpu_usage": 11_000_000,
        },
    }
    assert compute_container_cpu_pct(stats) is None


def test_container_cpu_pct_zero_sys_delta_returns_none():
    """If system_delta is 0, we can't compute a percentage."""
    stats = {
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000},
            "system_cpu_usage": 10_000_000,
        },
        "cpu_stats": {
            "cpu_usage": {"total_usage": 1_500_000},
            "system_cpu_usage": 10_000_000,  # same as pre
        },
    }
    assert compute_container_cpu_pct(stats) is None


# --------------------------------------------------------------------------
# CPU quota extraction from docker inspect
# --------------------------------------------------------------------------

def test_quota_from_nano_cpus():
    """Modern Docker: HostConfig.NanoCpus = 2e9 means 2.0 cores."""
    inspect = {"HostConfig": {"NanoCpus": 2_000_000_000}}
    assert get_container_cpu_quota(inspect) == pytest.approx(2.0)


def test_quota_from_cpu_quota_and_period():
    """Legacy: CpuQuota=200000, CpuPeriod=100000 → 2.0 cores."""
    inspect = {"HostConfig": {"CpuQuota": 200_000, "CpuPeriod": 100_000}}
    assert get_container_cpu_quota(inspect) == pytest.approx(2.0)


def test_quota_none_when_no_limit():
    """No quota fields → None (container can use the whole host)."""
    assert get_container_cpu_quota({"HostConfig": {}}) is None
    assert get_container_cpu_quota({}) is None


def test_quota_zero_nano_cpus_falls_through_to_legacy():
    """NanoCpus=0 should fall through to CpuQuota/CpuPeriod."""
    inspect = {"HostConfig": {"NanoCpus": 0, "CpuQuota": 100_000, "CpuPeriod": 100_000}}
    assert get_container_cpu_quota(inspect) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# CPU% normalisation by quota
# --------------------------------------------------------------------------

def test_normalise_cpu_pct_with_quota():
    """25% host-relative on a 2-CPU quota → 12.5% quota-relative.

    Wait — that's wrong. Let me re-derive:
    - Docker CLI: pct = (cpu_delta / sys_delta) * online_cpus * 100
    - This gives "cores used * 100" (i.e. 100% = 1 full core).
    - So 200% means 2 cores are being used.
    - If the container has a quota of 2 cores, 200% usage = 100% of quota.
    - → normalised = 200 / 2 = 100.0
    """
    # 200% (2 cores used) with a 2-core quota → 100%
    assert normalise_cpu_pct_by_quota(200.0, 2.0) == pytest.approx(100.0)
    # 100% (1 core used) with a 2-core quota → 50%
    assert normalise_cpu_pct_by_quota(100.0, 2.0) == pytest.approx(50.0)
    # 50% (0.5 cores) with a 1-core quota → 50%
    assert normalise_cpu_pct_by_quota(50.0, 1.0) == pytest.approx(50.0)


def test_normalise_cpu_pct_no_quota_returns_original():
    """No quota → return the host-relative percentage unchanged."""
    assert normalise_cpu_pct_by_quota(42.5, None) == pytest.approx(42.5)
    assert normalise_cpu_pct_by_quota(42.5, 0) == pytest.approx(42.5)


def test_normalise_cpu_pct_clamps_to_100():
    """Quota-relative percentage should never exceed 100%."""
    # 300% (3 cores used) with a 2-core quota → 150% → clamped to 100%
    assert normalise_cpu_pct_by_quota(300.0, 2.0) == pytest.approx(100.0)


def test_normalise_cpu_pct_none_input_returns_none():
    assert normalise_cpu_pct_by_quota(None, 2.0) is None


# --------------------------------------------------------------------------
# Container memory percentage
# --------------------------------------------------------------------------

def test_container_mem_pct_basic():
    # usage = 500MB, limit = 1GB → 48.83% (500*1024*1024 / 1024**3 * 100)
    stats = {"memory_stats": {"usage": 500 * 1024 * 1024, "limit": 1_073_741_824}}
    assert compute_container_mem_pct(stats) == pytest.approx(48.83, abs=0.1)
    # usage = exactly half of limit → 50%
    stats2 = {"memory_stats": {"usage": 536_870_912, "limit": 1_073_741_824}}
    assert compute_container_mem_pct(stats2) == pytest.approx(50.0)


def test_container_mem_pct_no_limit_returns_none():
    stats = {"memory_stats": {"usage": 524_288_000, "limit": 0}}
    assert compute_container_mem_pct(stats) is None


def test_container_mem_pct_missing_returns_none():
    assert compute_container_mem_pct({}) is None


# --------------------------------------------------------------------------
# docker stats JSON line parsing
# --------------------------------------------------------------------------

def test_parse_docker_stats_json_line_extracts_numeric_fields():
    line = '{"Name": "web", "CPUPerc": "0.42%", "MemPerc": "12.34%", "MemUsage": "120MiB / 4GiB", "PIDs": "5"}'
    obj = parse_docker_stats_json_line(line)
    assert obj is not None
    assert obj["Name"] == "web"
    assert obj["_cpu_pct_num"] == pytest.approx(0.42)
    assert obj["_mem_pct_num"] == pytest.approx(12.34)
    assert obj["_mem_used"] == "120MiB"
    assert obj["_mem_limit"] == "4GiB"


def test_parse_docker_stats_json_line_empty_returns_none():
    assert parse_docker_stats_json_line("") is None
    assert parse_docker_stats_json_line("   ") is None


def test_parse_docker_stats_json_line_invalid_json_returns_none():
    assert parse_docker_stats_json_line("not json") is None


def test_parse_docker_stats_json_line_missing_fields():
    line = '{"Name": "web"}'
    obj = parse_docker_stats_json_line(line)
    assert obj is not None
    assert obj["Name"] == "web"
    assert obj["_cpu_pct_num"] is None
    assert obj["_mem_pct_num"] is None


# --------------------------------------------------------------------------
# Process CPU% — delta-based instantaneous calculation
# --------------------------------------------------------------------------

def test_process_cpu_pct_basic():
    """A process that used 100 jiffies in 1 second on a 100Hz clock = 100%."""
    # sample1=0, sample2=100, elapsed=1.0s, clk_tck=100
    # delta_seconds = 100/100 = 1.0
    # pct = (1.0 / 1.0) * 100 = 100%
    pct = compute_process_cpu_pct(0, 100, 1.0, clk_tck=100)
    assert pct == pytest.approx(100.0)


def test_process_cpu_pct_half_core():
    """50 jiffies in 1 second on 100Hz = 50% (half a core)."""
    pct = compute_process_cpu_pct(0, 50, 1.0, clk_tck=100)
    assert pct == pytest.approx(50.0)


def test_process_cpu_pct_two_seconds_elapsed():
    """100 jiffies over 2 seconds = 50%."""
    pct = compute_process_cpu_pct(0, 100, 2.0, clk_tck=100)
    assert pct == pytest.approx(50.0)


def test_process_cpu_pct_multi_core():
    """200 jiffies in 1 second = 200% (2 full cores)."""
    pct = compute_process_cpu_pct(0, 200, 1.0, clk_tck=100)
    assert pct == pytest.approx(200.0)


def test_process_cpu_pct_negative_delta_returns_zero():
    """If sample2 < sample1 (process restarted), return 0."""
    pct = compute_process_cpu_pct(100, 50, 1.0, clk_tck=100)
    assert pct == 0.0


def test_process_cpu_pct_zero_elapsed_returns_none():
    """Division by zero should return None, not crash."""
    assert compute_process_cpu_pct(0, 100, 0.0) is None
    assert compute_process_cpu_pct(0, 100, -1.0) is None


def test_process_cpu_pct_uses_default_clk_tck():
    """If clk_tck is not passed, DEFAULT_CLK_TCK (100) should be used."""
    pct = compute_process_cpu_pct(0, 100, 1.0)
    assert pct == pytest.approx(100.0)
    assert DEFAULT_CLK_TCK == 100


# --------------------------------------------------------------------------
# Process memory percentage
# --------------------------------------------------------------------------

def test_process_mem_pct_basic():
    # RSS = 500MB, total = 4GB → 12.5%
    pct = compute_process_mem_pct(524_288_000, 4_294_967_296)
    assert pct == pytest.approx(12.2, abs=0.1)


def test_process_mem_pct_zero_total_returns_none():
    assert compute_process_mem_pct(1_000_000, 0) is None


# --------------------------------------------------------------------------
# top-style sampling script
# --------------------------------------------------------------------------

def test_build_top_script_returns_non_empty_string():
    script = build_top_script(sample_interval=1.0, max_procs=50)
    assert isinstance(script, str)
    assert len(script) > 50
    # Must use top with two samples (-n 2) for delta-based CPU%.
    assert "top" in script
    assert "-n 2" in script
    # Must pipe through awk to extract fields.
    assert "awk" in script


def test_build_top_script_uses_custom_interval():
    """The script must embed the sample interval in the top -d flag."""
    script = build_top_script(sample_interval=2.5, max_procs=10)
    # top -d accepts the interval; we use the integer part (2).
    assert "-d 2" in script
    # max_procs should appear in the head -n limit.
    assert "head -n 10" in script


def test_build_top_script_clamps_interval_to_minimum_0_1():
    """An interval of 0 or negative should be clamped to 0.1."""
    script = build_top_script(sample_interval=0.0, max_procs=5)
    assert "-d 0.1" in script
    script2 = build_top_script(sample_interval=-5.0, max_procs=5)
    assert "-d 0.1" in script2


def test_build_top_script_accepts_sub_second_intervals():
    """The script should support fractional intervals like 0.5s for faster refreshes."""
    script = build_top_script(sample_interval=0.5, max_procs=10)
    assert "-d 0.5" in script


def test_build_top_script_is_single_pipeline():
    """The script must be a single pipeline (no temp files, no multi-pass).

    The previous implementation used temp files + join + multi-pass awk
    and timed out on slow SSH connections, causing ConnectionLost errors.
    The new implementation must be a single ``top | awk | sort | head``
    pipeline so it completes fast and doesn't tie up the worker shell.
    """
    script = build_top_script(sample_interval=1.0, max_procs=20)
    # No temp file creation — that was the source of the timeout.
    assert "mktemp" not in script
    assert "sample1_file" not in script
    assert "sample2_file" not in script
    assert "sample1_redo" not in script
    assert "sample2_redo" not in script
    # No join command — that was slow and fragile.
    assert "join" not in script
    # Must be a single pipeline.
    assert script.count("\n") == 0 or script.count("\n") <= 2


def test_parse_top_output_basic():
    text = (
        "PID\tUSER\tCPU%\tMEM%\tRSS_MB\tSTATE\tCOMMAND\n"
        "1234\troot\t12.50\t2.30\t512.0\tS\tnginx\n"
        "5678\tubuntu\t0.00\t0.10\t64.0\tS\tbash\n"
    )
    rows = parse_top_output(text)
    assert len(rows) == 2
    assert rows[0]["pid"] == "1234"
    assert rows[0]["user"] == "root"
    assert rows[0]["cpu_pct"] == pytest.approx(12.50)
    assert rows[0]["mem_pct"] == pytest.approx(2.30)
    assert rows[0]["rss_mb"] == pytest.approx(512.0)
    assert rows[0]["state"] == "S"
    assert rows[0]["command"] == "nginx"
    assert rows[1]["pid"] == "5678"
    assert rows[1]["cpu_pct"] == pytest.approx(0.00)


def test_parse_top_output_empty_returns_empty_list():
    assert parse_top_output("") == []
    assert parse_top_output("\n\n") == []


def test_parse_top_output_header_only_returns_empty_list():
    assert parse_top_output("PID\tUSER\tCPU%\tMEM%\tRSS_MB\tSTATE\tCOMMAND\n") == []


def test_parse_top_output_skips_malformed_lines():
    text = (
        "PID\tUSER\tCPU%\tMEM%\tRSS_MB\tSTATE\tCOMMAND\n"
        "garbage line without enough tabs\n"
        "1234\troot\t12.50\t2.30\t512.0\tS\tnginx\n"
    )
    rows = parse_top_output(text)
    assert len(rows) == 1
    assert rows[0]["pid"] == "1234"


def test_parse_top_output_handles_missing_values():
    """Malformed numeric fields should become None, not crash."""
    text = (
        "PID\tUSER\tCPU%\tMEM%\tRSS_MB\tSTATE\tCOMMAND\n"
        "1234\troot\tN/A\tN/A\tN/A\tS\tnginx\n"
    )
    rows = parse_top_output(text)
    assert len(rows) == 1
    assert rows[0]["cpu_pct"] is None
    assert rows[0]["mem_pct"] is None
    assert rows[0]["rss_mb"] is None


# --------------------------------------------------------------------------
# Integration: realistic container stats scenario
# --------------------------------------------------------------------------

def test_container_at_quota_limit_shows_100_percent():
    """A container with --cpus=2 maxing out its 2 cores should show ~100%.

    Docker CLI would report 25% (2/8 * 100) on an 8-core host, which is
    misleading. After normalisation, it should be 100% of the quota.
    """
    # Simulate Docker CLI's host-relative percentage: 2 cores on 8-core host.
    host_relative_pct = 25.0  # (2/8) * 100
    quota_cores = 2.0
    normalised = normalise_cpu_pct_by_quota(host_relative_pct, quota_cores)
    # The normalisation formula: normalised = host_relative_pct / quota_cores
    # But wait — Docker CLI multiplies by online_cpus, so 25% here means
    # 0.25 cores, not 2 cores. The real Docker CLI value for 2 cores on
    # 8 hosts is (2/8) * 8 * 100 = 200%, not 25%.
    # Let's use the realistic value.
    realistic_docker_pct = 200.0  # (cpu_delta/sys_delta) * 8 * 100
    normalised = normalise_cpu_pct_by_quota(realistic_docker_pct, quota_cores)
    assert normalised == pytest.approx(100.0, abs=0.01)


def test_container_unlimited_quota_shows_host_relative():
    """A container with no CPU quota should show the raw Docker CLI percentage."""
    docker_pct = 150.0  # 1.5 cores used
    normalised = normalise_cpu_pct_by_quota(docker_pct, None)
    assert normalised == pytest.approx(150.0)
