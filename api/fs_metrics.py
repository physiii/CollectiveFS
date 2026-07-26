"""Filesystem performance counters reported by the FUSE mount.

The mount is the only place that sees real filesystem behaviour — how long a
read blocked, how much a write actually moved — so it reports here and the
System section charts it alongside CPU and network.

Samples are kept in memory in a rolling window. Losing them on restart is fine;
this is a live performance view, not an audit trail.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

# One sample per mount report (~every few seconds); 720 keeps roughly an hour.
MAX_SAMPLES = 720

# Operations worth tracking separately. Anything else is folded into "other".
TRACKED_OPS = (
    "read",
    "write",
    "create",
    "unlink",
    "rename",
    "mkdir",
    "rmdir",
    "readdir",
    "getattr",
    "lookup",
    "truncate",
    "release",
)


class MetricsStore:
    """Rolling window of samples reported by one or more mounts."""

    def __init__(self, max_samples: int = MAX_SAMPLES):
        self._samples: Deque[Dict[str, Any]] = deque(maxlen=max_samples)
        self._mounts: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Store one report from a mount."""
        now = time.time()
        mount = str(payload.get("mountpoint") or "unknown")
        ops = payload.get("ops") or {}

        sample = {
            "ts": now,
            "mountpoint": mount,
            "node": payload.get("node", ""),
            "interval_seconds": float(payload.get("interval_seconds") or 0) or None,
            "read_bytes": int(payload.get("read_bytes") or 0),
            "write_bytes": int(payload.get("write_bytes") or 0),
            "ops": {
                name: {
                    "count": int(entry.get("count") or 0),
                    "errors": int(entry.get("errors") or 0),
                    # Milliseconds, already averaged by the mount.
                    "avg_ms": float(entry.get("avg_ms") or 0.0),
                    "max_ms": float(entry.get("max_ms") or 0.0),
                }
                for name, entry in ops.items()
                if isinstance(entry, dict)
            },
        }

        with self._lock:
            self._samples.append(sample)
            self._mounts[mount] = {
                "mountpoint": mount,
                "node": sample["node"],
                "last_seen": now,
                "files": payload.get("files"),
                "cache_entries": payload.get("cache_entries"),
            }
        return sample

    def snapshot(self, window_seconds: float = 300.0) -> Dict[str, Any]:
        """Aggregate the recent window into something chartable."""
        cutoff = time.time() - window_seconds
        with self._lock:
            samples = [s for s in self._samples if s["ts"] >= cutoff]
            mounts = list(self._mounts.values())

        series = []
        totals = {"read_bytes": 0, "write_bytes": 0, "ops": 0, "errors": 0}
        per_op: Dict[str, Dict[str, float]] = {}

        for sample in samples:
            interval = sample["interval_seconds"] or 1.0
            op_count = sum(entry["count"] for entry in sample["ops"].values())
            errors = sum(entry["errors"] for entry in sample["ops"].values())
            # Weight the latency average by call count so a single slow call in
            # a busy interval does not dominate the number.
            weighted = sum(
                entry["avg_ms"] * entry["count"] for entry in sample["ops"].values()
            )
            series.append(
                {
                    "_ts": int(sample["ts"] * 1000),
                    "read_bps": sample["read_bytes"] / interval,
                    "write_bps": sample["write_bytes"] / interval,
                    "ops_per_sec": op_count / interval,
                    "avg_latency_ms": (weighted / op_count) if op_count else 0.0,
                }
            )

            totals["read_bytes"] += sample["read_bytes"]
            totals["write_bytes"] += sample["write_bytes"]
            totals["ops"] += op_count
            totals["errors"] += errors

            for name, entry in sample["ops"].items():
                bucket = per_op.setdefault(
                    name, {"count": 0, "errors": 0, "weighted_ms": 0.0, "max_ms": 0.0}
                )
                bucket["count"] += entry["count"]
                bucket["errors"] += entry["errors"]
                bucket["weighted_ms"] += entry["avg_ms"] * entry["count"]
                bucket["max_ms"] = max(bucket["max_ms"], entry["max_ms"])

        operations = sorted(
            (
                {
                    "op": name,
                    "count": int(bucket["count"]),
                    "errors": int(bucket["errors"]),
                    "avg_ms": round(bucket["weighted_ms"] / bucket["count"], 2)
                    if bucket["count"]
                    else 0.0,
                    "max_ms": round(bucket["max_ms"], 2),
                }
                for name, bucket in per_op.items()
            ),
            key=lambda item: item["count"],
            reverse=True,
        )

        span = (samples[-1]["ts"] - samples[0]["ts"]) if len(samples) > 1 else 0.0
        return {
            "mounts": mounts,
            "series": series,
            "operations": operations,
            "totals": {
                **totals,
                "window_seconds": round(span, 1),
                "read_bps": totals["read_bytes"] / span if span else 0.0,
                "write_bps": totals["write_bytes"] / span if span else 0.0,
                "ops_per_sec": totals["ops"] / span if span else 0.0,
            },
            "active": bool(mounts) and any(
                time.time() - mount["last_seen"] < 30 for mount in mounts
            ),
        }
