from __future__ import annotations

import shlex


class ServerMonitor:
    """Collect lightweight host telemetry using one SSH command channel."""

    SCRIPT = r"""
set -u
printf 'hostname\t%s\n' "$(hostname 2>/dev/null || true)"
printf 'os\t%s\n' "$(uname -sr 2>/dev/null || true)"
printf 'docker\t%s\n' "$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
printf 'os\t%s\n' "$(uname -sr 2>/dev/null || true)"
printf 'uptime\t%s\n' "$(uptime -p 2>/dev/null || uptime 2>/dev/null || true)"
printf 'cpu\t%s\n' "$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
printf 'load\t%s\n' "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || true)"
awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{if(t>0) printf "memory_percent\t%.1f\n",(t-a)*100/t; else print "memory_percent\t0"}' /proc/meminfo 2>/dev/null
awk '/MemTotal/{printf "memory_total\t%.0fMB\n",$2/1024}' /proc/meminfo 2>/dev/null
awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{if(t>0) printf "memory_used\t%.0fMB\n",(t-a)/1024; else print "memory_used\t0MB"}' /proc/meminfo 2>/dev/null
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if(t>0) printf "memory_used_bytes\t%d\n",(t-a)*1024; else print "memory_used_bytes\t0"}' /proc/meminfo 2>/dev/null
awk '/^MemTotal:/{t=$2} END{if(t>0) printf "memory_total_bytes\t%d\n",t*1024; else print "memory_total_bytes\t0"}' /proc/meminfo 2>/dev/null
cpu=$(awk 'NR==1{u=$2+n=$3+s=$4+i=$5+w=$6+irq=$7+sir=$8+st=$9; total=u+n+s+i+w+irq+sir+st} END{if(total>0) printf "%.1f",(total-i-w)*100/total; else print "0"}' /proc/stat 2>/dev/null)
printf 'cpu_percent\t%s\n' "${cpu:-0}"
printf 'disk\t%s\n' "$(df -P / 2>/dev/null | tail -1 || true)"
printf 'docker\t%s\n' "$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
""".strip()

    def __init__(self, ssh):
        self.ssh = ssh

    async def snapshot(self) -> dict[str, str]:
        data = await self.detailed_snapshot()
        data.setdefault("load_short", (data.get("load") or "").split()[0] or "—")
        return data

    async def detailed_snapshot(self) -> dict:
        """Collect host telemetry robustly using one SSH command and client-side CPU math."""
        script = r"""set +e
printf '__META__\thostname\t%s\n' "$(hostname 2>/dev/null)"
printf '__META__\tuptime\t%s\n' "$(uptime -p 2>/dev/null || uptime 2>/dev/null)"
printf '__META__\tload\t%s\n' "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
printf '__META__\tprocesses\t%s\n' "$(ps -e --no-headers 2>/dev/null | wc -l)"
printf '__META__\tdisk\t%s\n' "$(df -hP / 2>/dev/null | tail -1 | awk '{print $3" / "$2" ("$5")"}')"
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if(t>0) printf "__META__\tmemory_percent\t%.1f\n",(t-a)*100/t; else printf "__META__\tmemory_percent\t0\n"}' /proc/meminfo 2>/dev/null
awk '/^MemTotal:/{t=$2} END{if(t>0) printf "__META__\tmemory_total\t%.0fMB\n",t/1024; else printf "__META__\tmemory_total\t0MB\n"}' /proc/meminfo 2>/dev/null
awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if(t>0) printf "__META__\tmemory_used\t%.0fMB\n",(t-a)/1024; else printf "__META__\tmemory_used\t0MB\n"}' /proc/meminfo 2>/dev/null
awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{if(t>0) printf "__META__\tswap_percent\t%.1f\n",(t-f)*100/t; else printf "__META__\tswap_percent\t0\n"}' /proc/meminfo 2>/dev/null
printf '__STAT1__\n'
cat /proc/stat 2>/dev/null
sleep 1
printf '__STAT2__\n'
cat /proc/stat 2>/dev/null
"""
        command = 'sh -lc ' + shlex.quote(script)
        # Overview telemetry is page data too: keep it inside the configured
        # worker queue instead of opening a side-channel outside the scheduler.
        if hasattr(self.ssh, 'run_page'):
            result = await self.ssh.run_page(command, timeout=10)
        else:
            result = await self.ssh.run(command, timeout=12)
        raw: dict[str, str] = {}
        stat1: dict[str, tuple[int, int]] = {}
        stat2: dict[str, tuple[int, int]] = {}
        target = None
        for line in (result.stdout or '').splitlines():
            if line == '__STAT1__':
                target = stat1; continue
            if line == '__STAT2__':
                target = stat2; continue
            if line.startswith('__META__\t'):
                parts=line.split('\t',2)
                if len(parts)==3: raw[parts[1]]=parts[2]
                continue
            if target is not None and line.startswith('cpu'):
                parts=line.split()
                if not parts or parts[0] != 'cpu' and not parts[0][3:].isdigit():
                    continue
                nums=[]
                for x in parts[1:]:
                    try: nums.append(int(x))
                    except ValueError: nums.append(0)
                if len(nums) >= 5:
                    total=sum(nums)
                    idle=nums[3] + (nums[4] if len(nums)>4 else 0)
                    target[parts[0]]=(total,idle)
        def usage(key: str) -> float:
            a=stat1.get(key); b=stat2.get(key)
            if not a or not b: return 0.0
            dt=b[0]-a[0]; di=b[1]-a[1]
            if dt <= 0: return 0.0
            return max(0.0, min(100.0, (dt-di)*100.0/dt))
        raw['cpu_percent']=round(usage('cpu'),1)
        cores=[]
        for key in stat2:
            if key.startswith('cpu') and key[3:].isdigit():
                cores.append((int(key[3:]), round(usage(key),1)))
        raw['core_percent']=[v for _,v in sorted(cores)]
        raw['cpu']=str(len(cores))
        raw['load_short']=(raw.get('load') or '').split()[0] or '—'
        return raw
