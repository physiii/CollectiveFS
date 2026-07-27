"""Detecting and repairing lost redundancy.

Erasure coding survives a node loss, but surviving it is not the same as
recovering from it. After a peer disappears, every file that had shards there is
still readable and has *no redundancy left* — the next loss is fatal. Nothing
puts that redundancy back on its own.

This module answers two questions and acts on the second:

- What do we actually still have? Metadata records where a shard was placed, not
  whether it is still there, so a peer is asked rather than trusted.
- What can be rebuilt? A file with at least `data_shards` shards can be
  reconstructed and re-encoded, which regenerates every missing shard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import httpx


class Health:
    """What a scan concluded about one file."""

    INTACT = "intact"
    DEGRADED = "degraded"        # readable, but fewer shards than it should have
    UNRECOVERABLE = "unrecoverable"  # fewer than data_shards remain
    ORPHANED = "orphaned"        # metadata with no shards anywhere


async def peer_shard_index(
    peer_url: str, origin_node: str, file_id: str, timeout: float = 10.0
) -> Optional[Set[int]]:
    """Which shard indices a peer actually holds for us.

    `None` means the peer could not be reached — unknown, not empty. Treating
    unreachable as empty would turn a reboot into a false data-loss report and
    trigger pointless rebuilds.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{peer_url}/api/peers/shards/{origin_node}/{file_id}"
            )
        if response.status_code == 404:
            return set()
        if response.status_code >= 400:
            return None
        return {int(i) for i in response.json().get("indices", [])}
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def assess(
    metadata: Dict[str, Any],
    origin_node: str,
    *,
    default_data_shards: int,
    probe: Callable = peer_shard_index,
) -> Dict[str, Any]:
    """Classify one file by what is genuinely still retrievable."""
    file_id = metadata.get("id", "")
    chunks = [
        chunk for chunk in metadata.get("chunk_list", [])
        if not str(chunk.get("path", "")).endswith(".size")
    ]
    data_shards = int(metadata.get("data_shards") or default_data_shards)

    def index_of(chunk: Dict[str, Any]) -> Optional[int]:
        suffix = str(chunk.get("path", "")).rsplit(".", 1)[-1]
        try:
            return int(suffix)
        except ValueError:
            return None

    local: Set[int] = set()
    claimed: Dict[str, Set[int]] = {}
    for chunk in chunks:
        index = index_of(chunk)
        if index is None:
            continue
        if chunk.get("path") and Path(chunk["path"]).exists():
            local.add(index)
        elif chunk.get("peer"):
            claimed.setdefault(chunk["peer"], set()).add(index)

    confirmed: Set[int] = set()
    unreachable: List[str] = []
    lost_peers: List[str] = []
    for peer_url, indices in claimed.items():
        held = await probe(peer_url, origin_node, file_id)
        if held is None:
            unreachable.append(peer_url)
            # Unknown: assume still there rather than rebuild on a hunch.
            confirmed |= indices
            continue
        present = indices & held
        confirmed |= present
        if indices - present:
            lost_peers.append(peer_url)

    available = local | confirmed
    total = len({index_of(c) for c in chunks} - {None})

    if not available:
        status = Health.ORPHANED
    elif len(available) < data_shards:
        status = Health.UNRECOVERABLE
    elif len(available) < total:
        status = Health.DEGRADED
    else:
        status = Health.INTACT

    return {
        "id": file_id,
        "name": metadata.get("name", ""),
        "status": status,
        "data_shards": data_shards,
        "shards_total": total,
        "shards_local": len(local),
        "shards_on_peers": len(confirmed),
        "shards_available": len(available),
        "shards_missing": max(total - len(available), 0),
        "missing_indices": sorted(set(range(total)) - available),
        "lost_peers": lost_peers,
        "unreachable_peers": unreachable,
        "repairable": status == Health.DEGRADED,
    }


def summarise(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for report in reports:
        counts[report["status"]] = counts.get(report["status"], 0) + 1
    return {
        "files": len(reports),
        "by_status": counts,
        "shards_missing": sum(r["shards_missing"] for r in reports),
        "repairable": [r["id"] for r in reports if r["repairable"]],
        "unrecoverable": [
            {"id": r["id"], "name": r["name"], "have": r["shards_available"],
             "need": r["data_shards"]}
            for r in reports if r["status"] == Health.UNRECOVERABLE
        ],
        "orphaned": [
            {"id": r["id"], "name": r["name"]}
            for r in reports if r["status"] == Health.ORPHANED
        ],
    }
