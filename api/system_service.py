"""Host + collective telemetry for the System & Infrastructure section.

The payload deliberately mirrors Custodian's ``/api/system/overview`` shape
(``ResourceGauge`` / ``DiskUsage`` / network counters) so the same chart and
meter components render against either service. On top of the host metrics it
adds what only CollectiveFS knows: quota headroom, shard durability, and the
erasure-coding fault budget.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # psutil is the good path; the fallbacks below keep the section alive without it.
    import psutil
except ImportError:  # pragma: no cover - exercised only on minimal images
    psutil = None  # type: ignore[assignment]


def _status_for(percent: Optional[float], warn: float = 75.0, bad: float = 90.0) -> str:
    if percent is None:
        return "unknown"
    if percent >= bad:
        return "critical"
    if percent >= warn:
        return "warning"
    return "healthy"


def _gauge(
    gauge_id: str,
    label: str,
    percent: Optional[float],
    *,
    value: Optional[float] = None,
    unit: str = "percent",
    detail: Optional[str] = None,
    warn: float = 75.0,
    bad: float = 90.0,
) -> Dict[str, Any]:
    return {
        "id": gauge_id,
        "label": label,
        "percent": round(percent, 1) if percent is not None else None,
        "value": value,
        "unit": unit,
        "status": _status_for(percent, warn, bad),
        "detail": detail,
    }


# ── host metrics ────────────────────────────────────────────────────────


def _cpu() -> Dict[str, Any]:
    if psutil is None:
        return _gauge("cpu", "CPU", None, detail="psutil not installed")
    # interval=None reads since the previous call, so polling gives live values
    # without blocking the request for a sampling window.
    percent = psutil.cpu_percent(interval=None)
    cores = psutil.cpu_count(logical=True) or 0
    return _gauge("cpu", "CPU", percent, detail=f"{cores} logical cores")


def _memory() -> Dict[str, Any]:
    if psutil is None:
        return _gauge("memory", "Memory", None, detail="psutil not installed")
    mem = psutil.virtual_memory()
    return _gauge(
        "memory",
        "Memory",
        mem.percent,
        value=mem.used,
        unit="bytes",
        detail=f"{mem.used} of {mem.total} bytes used",
    )


def _swap() -> Optional[Dict[str, Any]]:
    if psutil is None:
        return None
    swap = psutil.swap_memory()
    if not swap.total:
        return None
    return _gauge(
        "swap",
        "Swap",
        swap.percent,
        value=swap.used,
        unit="bytes",
        detail=f"{swap.used} of {swap.total} bytes used",
    )


def _load_average() -> List[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return []


def _uptime_seconds() -> Optional[float]:
    if psutil is not None:
        try:
            return round(time.time() - psutil.boot_time(), 0)
        except (OSError, RuntimeError):
            pass
    try:
        with open("/proc/uptime") as fh:
            return round(float(fh.read().split()[0]), 0)
    except (OSError, ValueError, IndexError):
        return None


def _disk(disk_id: str, label: str, path: Path) -> Dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return {
            "id": disk_id,
            "label": label,
            "path": str(path),
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "used_percent": None,
            "status": "unknown",
        }
    percent = (usage.used / usage.total * 100) if usage.total else None
    return {
        "id": disk_id,
        "label": label,
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round(percent, 1) if percent is not None else None,
        "status": _status_for(percent, 80.0, 92.0),
    }


# Per-container veth pairs are ephemeral and numerous; loopback is not a link.
_HIDDEN_IFACE_PREFIXES = ("veth", "lo")
# Bridges and tunnels carry real traffic, but it is traffic that also crosses a
# physical link. Summing both would double-count node bandwidth, so they are
# reported and marked rather than folded into the totals.
_VIRTUAL_IFACE_PREFIXES = ("br-", "docker", "virbr", "tun", "tap", "wg", "vmnet")


def _network_interfaces() -> List[Dict[str, Any]]:
    if psutil is None:
        return []
    out: List[Dict[str, Any]] = []
    try:
        counters = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
    except (OSError, RuntimeError):
        return []
    for name, counter in counters.items():
        if name.startswith(_HIDDEN_IFACE_PREFIXES):
            continue
        info = stats.get(name)
        out.append(
            {
                "name": name,
                "virtual": name.startswith(_VIRTUAL_IFACE_PREFIXES),
                "up": bool(info.isup) if info else True,
                "speed_mbps": int(info.speed) if info and info.speed else None,
                "rx_bytes": counter.bytes_recv,
                "tx_bytes": counter.bytes_sent,
                "rx_packets": counter.packets_recv,
                "tx_packets": counter.packets_sent,
                "errors": counter.errin + counter.errout,
                "drops": counter.dropin + counter.dropout,
            }
        )
    # Physical links first, then by throughput, so the meaningful rows lead.
    out.sort(key=lambda item: (item["virtual"], -(item["rx_bytes"] + item["tx_bytes"])))
    return out


# ── collective metrics ──────────────────────────────────────────────────


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def collective_usage(
    root: Path,
    config: Dict[str, Any],
    files: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Quota accounting plus the durability picture for stored shards."""
    quota = int(config.get("storage", {}).get("quota_bytes") or 0)
    reserve = int(config.get("storage", {}).get("reserve_bytes") or 0)
    watermark = int(config.get("storage", {}).get("high_watermark_percent") or 90)

    shards_root = root / "proc"
    # Everything under proc/ occupies the pledged quota, including shards held
    # for other nodes — that is what actually consumes the disk. But our own
    # storage overhead has to be measured against our own files only, or
    # hosting a peer's data makes our expansion ratio meaningless.
    used = _dir_size(shards_root)
    hosted = _dir_size(shards_root / "_peers")
    own = max(used - hosted, 0)
    logical = sum(int(item.get("size") or 0) for item in files)
    percent = (used / quota * 100) if quota else None

    # The quota is a promise; the disk under it is shared with the host and can
    # be filled by anything. Track how much of the pledge is actually backed by
    # free space right now, because that is the number that can bite.
    try:
        device = shutil.disk_usage(str(root))
        device_total, device_free = device.total, device.free
    except OSError:
        device_total = device_free = None
    unbacked = (
        max(quota - used - device_free, 0) if device_free is not None and quota else 0
    )

    # The `<base>.size` sidecar is not a shard; counting it inflates every
    # total by one. A shard verified onto a peer still counts as available —
    # that is the point of distributing it.
    shard_total = 0
    shard_available = 0
    shard_local = 0
    placement: Dict[str, int] = {}
    for item in files:
        for chunk in item.get("chunk_list", []) or []:
            chunk_path = chunk.get("path") or ""
            if chunk_path.endswith(".size"):
                continue
            shard_total += 1
            here = bool(chunk_path) and Path(chunk_path).exists()
            peer = chunk.get("peer")
            if here:
                shard_local += 1
            if here or peer:
                shard_available += 1
            where = peer if (peer and not here) else "local"
            placement[where] = placement.get(where, 0) + 1

    data_shards = int(config.get("erasure", {}).get("data_shards") or 0)
    parity_shards = int(config.get("erasure", {}).get("parity_shards") or 0)
    durability = (
        round(shard_available / shard_total * 100, 1) if shard_total else 100.0
    )

    return {
        "root": str(root),
        "quota_bytes": quota,
        "reserve_bytes": reserve,
        "used_bytes": used,
        "own_bytes": own,
        "hosted_bytes": hosted,
        "logical_bytes": logical,
        "free_bytes": max(quota - used, 0),
        "device_total_bytes": device_total,
        "device_free_bytes": device_free,
        "unbacked_bytes": unbacked,
        "quota_fully_backed": unbacked == 0,
        "used_percent": round(percent, 1) if percent is not None else None,
        "high_watermark_percent": watermark,
        "accepting_writes": percent is None or percent < watermark,
        # Our shards against our data — the erasure overhead, nothing else.
        "expansion_ratio": round(own / logical, 2) if logical else None,
        "files": len(files),
        "shards_total": shard_total,
        "shards_available": shard_available,
        "shards_local": shard_local,
        "shards_remote": max(shard_available - shard_local, 0),
        "shards_missing": max(shard_total - shard_available, 0),
        "placement": placement,
        "durability_percent": durability,
        "data_shards": data_shards,
        "parity_shards": parity_shards,
        "fault_tolerance": parity_shards,
        "status": _status_for(percent, float(watermark) * 0.85, float(watermark)),
    }


def build_overview(
    *,
    root: Path,
    node_id: str,
    config: Dict[str, Any],
    files: List[Dict[str, Any]],
    peers: List[Dict[str, Any]],
    contract_health: Optional[Dict[str, Any]] = None,
    hosted_for_peers: Optional[Dict[str, Any]] = None,
    filesystem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the full System & Infrastructure payload."""
    collective = collective_usage(root, config, files)

    # This is the disk *under* the store, shared with the host — not what
    # CollectiveFS occupies. That is `collective.used_bytes` / the quota gauge.
    disks = [_disk("collective", "Backing Disk", root)]
    host_root = _disk("root", "Host Root", Path("/"))
    # A bind mount reports a different st_dev than / while being the same
    # filesystem, so compare the numbers rather than the device id — otherwise
    # the same disk is listed twice.
    same_filesystem = (
        host_root["total_bytes"] == disks[0]["total_bytes"]
        and host_root["used_bytes"] == disks[0]["used_bytes"]
    )
    if not same_filesystem:
        disks.append(host_root)

    quota_gauge = _gauge(
        "quota",
        "Collective Quota",
        collective["used_percent"],
        value=collective["used_bytes"],
        unit="bytes",
        detail=f"{collective['used_bytes']} of {collective['quota_bytes']} bytes pledged",
        warn=float(collective["high_watermark_percent"]) * 0.85,
        bad=float(collective["high_watermark_percent"]),
    )

    online_peers = [peer for peer in peers if peer.get("healthy")]
    hosted = hosted_for_peers or {}

    return {
        "hostname": socket.gethostname(),
        "node_id": node_id,
        "platform": f"{platform.system()} {platform.release()}",
        "uptime_seconds": _uptime_seconds(),
        "load_average": _load_average(),
        "cpu": _cpu(),
        "memory": _memory(),
        "swap": _swap(),
        "quota": quota_gauge,
        "disks": disks,
        "network": _network_interfaces(),
        "collective": collective,
        "peers": {
            "total": len(peers),
            "online": len(online_peers),
            "items": peers,
        },
        # Two directions worth telling apart: shards of ours sitting on peers,
        # and shards of theirs sitting here.
        "hosted_for_peers": {
            "nodes": hosted.get("nodes", []),
            "shards": hosted.get("shards", 0),
            "bytes": hosted.get("bytes", 0),
        },
        # Live performance of the FUSE mount, when one is reporting.
        "filesystem": filesystem or {"mounts": [], "series": [], "operations": [], "active": False},
        "contracts": contract_health or {},
        "erasure": {
            "data_shards": collective["data_shards"],
            "parity_shards": collective["parity_shards"],
            "total_shards": collective["data_shards"] + collective["parity_shards"],
            "can_lose": collective["parity_shards"],
            "overhead_percent": (
                round(collective["parity_shards"] / collective["data_shards"] * 100, 1)
                if collective["data_shards"]
                else None
            ),
        },
        "generated_at": time.time(),
    }
