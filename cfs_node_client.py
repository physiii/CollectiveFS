"""Platform-neutral client for a local CollectiveFS node.

Extracted from ``cfs_mount.py`` so both the Linux mount (pyfuse3) and the macOS
mount (``cfs_mount_macos.py``, fusepy + fuse-t) share one HTTP surface and one
set of metrics counters. Nothing here imports a FUSE binding, so it loads on
any platform.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

import httpx

# How long a directory listing is trusted before the node is asked again. Short,
# because another machine may have written into the same account.
DEFAULT_TTL = 1.0
BLOCK_SIZE = 4096


class OpStats:
    """Per-operation counters, drained each time they are reported."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.ops: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
        )
        self.read_bytes = 0
        self.write_bytes = 0
        self.since = time.time()

    def record(self, name: str, elapsed_ms: float, failed: bool = False) -> None:
        with self._lock:
            entry = self.ops[name]
            entry["count"] += 1
            entry["total_ms"] += elapsed_ms
            entry["max_ms"] = max(entry["max_ms"], elapsed_ms)
            if failed:
                entry["errors"] += 1

    def add_read(self, count: int) -> None:
        with self._lock:
            self.read_bytes += count

    def add_write(self, count: int) -> None:
        with self._lock:
            self.write_bytes += count

    def drain(self) -> Dict[str, Any]:
        with self._lock:
            interval = max(time.time() - self.since, 0.001)
            payload = {
                "interval_seconds": interval,
                "read_bytes": self.read_bytes,
                "write_bytes": self.write_bytes,
                "ops": {
                    name: {
                        "count": int(entry["count"]),
                        "errors": int(entry["errors"]),
                        "avg_ms": round(entry["total_ms"] / entry["count"], 3)
                        if entry["count"]
                        else 0.0,
                        "max_ms": round(entry["max_ms"], 3),
                    }
                    for name, entry in self.ops.items()
                },
            }
            self._reset()
            return payload


class NodeClient:
    """Blocking HTTP calls to the local node."""

    def __init__(self, base: str, token: str, timeout: float = 300.0):
        self.base = base.rstrip("/")
        self.token = token
        self._client = httpx.Client(
            base_url=self.base,
            timeout=timeout,
            headers={"x-cfs-token": token},
        )

    def close(self) -> None:
        self._client.close()

    def tree(self) -> Dict[str, Any]:
        response = self._client.get("/api/files/tree", params={"scope": "network"})
        response.raise_for_status()
        return response.json()

    def download(self, file_id: str) -> bytes:
        response = self._client.get(f"/api/files/{file_id}/download")
        response.raise_for_status()
        return response.content

    def upload(self, name: str, folder: str, payload: bytes,
               symlink: str = "") -> Dict[str, Any]:
        response = self._client.post(
            "/api/files/upload",
            files={"file": (name, payload, "application/octet-stream")},
            data={"folder": folder, "symlink": symlink},
        )
        response.raise_for_status()
        return response.json()

    def delete(self, file_id: str) -> None:
        response = self._client.delete(f"/api/files/{file_id}")
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()

    def patch(self, file_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        response = self._client.patch(f"/api/files/{file_id}", json=body)
        response.raise_for_status()
        return response.json()

    def mkdir(self, path: str) -> None:
        response = self._client.post("/api/folders", json={"path": path})
        response.raise_for_status()

    def rmdir(self, path: str) -> None:
        response = self._client.delete("/api/folders", params={"path": path})
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()

    def report(self, payload: Dict[str, Any]) -> None:
        try:
            self._client.post("/api/fs/metrics", json=payload, timeout=10.0)
        except httpx.HTTPError:
            pass  # Telemetry must never disturb the filesystem.

    def stats(self) -> Dict[str, Any]:
        response = self._client.get("/api/system/overview")
        response.raise_for_status()
        return response.json()

    def account_token(self) -> str:
        """The node's own default account token, so a bare mount just works."""
        response = self._client.get("/api/account")
        response.raise_for_status()
        return response.json()["token"]


def parse_time(value: Optional[str]) -> float:
    if not value:
        return time.time()
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
