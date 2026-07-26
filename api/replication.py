"""Spreading a file's shards across peers, and getting them back.

Encoding produces `<base>.0 … <base>.N` plus a `<base>.size` sidecar the decoder
needs. This module decides which of those shards live here and which live on a
peer, ships them, verifies they arrived intact, and stages them all back into
one directory when the file is read.

The placement rule is the property that makes the split safe: no single peer may
hold more than `parity_shards` of a file, so losing any one peer stays inside
the Reed-Solomon fault budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

SIZE_SUFFIX = ".size"


class ReplicationError(RuntimeError):
    """A shard could not be placed on, or retrieved from, a peer."""


# ── shard identity ──────────────────────────────────────────────────


def is_size_sidecar(chunk: Dict[str, Any]) -> bool:
    """The `.size` sidecar is not a shard; it carries the original length."""
    return str(chunk.get("path", "")).endswith(SIZE_SUFFIX)


def shard_index(chunk: Dict[str, Any]) -> Optional[int]:
    """The numeric suffix the encoder wrote, or None for the sidecar."""
    suffix = str(chunk.get("path", "")).rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return None


def data_shards_only(chunk_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [chunk for chunk in chunk_list if not is_size_sidecar(chunk)]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ── placement ───────────────────────────────────────────────────────


def plan_placement(
    chunk_list: List[Dict[str, Any]],
    peer_urls: List[str],
    parity_shards: int,
) -> Dict[int, Optional[str]]:
    """Map shard index -> peer URL (or None to keep it here).

    Each peer is capped at `parity_shards` shards so that losing a whole peer
    never exceeds what Reed-Solomon can rebuild. The `.size` sidecar always
    stays local: it is tiny and the origin is what reassembles the file.
    """
    placement: Dict[int, Optional[str]] = {}
    shards = data_shards_only(chunk_list)
    if not peer_urls or parity_shards <= 0:
        for chunk in shards:
            index = shard_index(chunk)
            if index is not None:
                placement[index] = None
        return placement

    capacity = {url: parity_shards for url in peer_urls}
    cursor = 0
    for chunk in shards:
        index = shard_index(chunk)
        if index is None:
            continue
        target: Optional[str] = None
        # Walk the ring once looking for a peer with room left.
        for offset in range(len(peer_urls)):
            candidate = peer_urls[(cursor + offset) % len(peer_urls)]
            if capacity[candidate] > 0:
                target = candidate
                capacity[candidate] -= 1
                cursor = (cursor + offset + 1) % len(peer_urls)
                break
        placement[index] = target
    return placement


def placement_summary(chunk_list: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many shards of this file sit where, for the UI."""
    summary: Dict[str, int] = {}
    for chunk in data_shards_only(chunk_list):
        where = chunk.get("peer") or "local"
        summary[where] = summary.get(where, 0) + 1
    return summary


# ── shipping shards out ─────────────────────────────────────────────


async def push_shard(
    client: httpx.AsyncClient,
    peer_url: str,
    *,
    origin_node: str,
    origin_url: str,
    file_id: str,
    index: int,
    name: str,
    payload: bytes,
    shard_id: str = "",
) -> str:
    """Upload one shard to a peer and return the digest it stored.

    The peer echoes back the digest of what it wrote; comparing it to ours is
    what licenses dropping the local copy.
    """
    response = await client.post(
        f"{peer_url}/api/peers/shards",
        files={"shard": (name, payload, "application/octet-stream")},
        data={
            "origin_node": origin_node,
            "origin_url": origin_url,
            "file_id": file_id,
            "index": str(index),
            "name": name,
            # Carried so the peer can answer a proof-of-storage challenge about
            # this shard: challenges name a shard id, not a path.
            "shard_id": shard_id,
        },
    )
    if response.status_code >= 400:
        raise ReplicationError(f"{peer_url} rejected shard {index}: HTTP {response.status_code}")
    body = response.json()
    stored = body.get("digest", "")
    if not stored:
        raise ReplicationError(f"{peer_url} did not report a digest for shard {index}")
    return stored


async def distribute(
    *,
    file_id: str,
    metadata: Dict[str, Any],
    peer_urls: List[str],
    origin_node: str,
    origin_url: str,
    parity_shards: int,
    keep_local_copy: bool,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Place this file's shards across the given peers.

    Mutates `metadata["chunk_list"]` in place, recording for each shard which
    peer holds it and the digest that was verified. A shard is only dropped
    locally after the peer's digest matches ours, so a failed hand-off degrades
    to "still stored here" rather than data loss.
    """
    chunk_list = metadata.get("chunk_list", [])
    placement = plan_placement(chunk_list, peer_urls, parity_shards)

    placed = 0
    failures: List[str] = []
    by_index = {shard_index(chunk): chunk for chunk in data_shards_only(chunk_list)}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for index, target in sorted(placement.items()):
            chunk = by_index.get(index)
            if chunk is None or target is None:
                if chunk is not None:
                    chunk.setdefault("peer", None)
                    chunk["local"] = True
                continue

            path = Path(chunk.get("path", ""))
            if not path.exists():
                failures.append(f"shard {index}: local copy missing")
                continue

            payload = path.read_bytes()
            local_digest = digest(payload)
            try:
                remote_digest = await push_shard(
                    client,
                    target,
                    origin_node=origin_node,
                    origin_url=origin_url,
                    file_id=file_id,
                    index=index,
                    name=path.name,
                    payload=payload,
                    shard_id=chunk.get("id", ""),
                )
            except (httpx.HTTPError, ReplicationError) as exc:
                failures.append(f"shard {index}: {exc}")
                chunk["local"] = True
                continue

            if remote_digest != local_digest:
                failures.append(f"shard {index}: digest mismatch at {target}")
                chunk["local"] = True
                continue

            chunk["peer"] = target
            chunk["digest"] = local_digest
            # Remember the size before the local copy goes away, or the shard
            # reads as 0 bytes everywhere it is displayed.
            chunk["size"] = len(payload)
            placed += 1

            if not keep_local_copy:
                try:
                    path.unlink()
                    chunk["local"] = False
                except OSError as exc:
                    # Verified remotely but still present here — harmless.
                    failures.append(f"shard {index}: could not drop local copy ({exc})")
                    chunk["local"] = True
            else:
                chunk["local"] = True

    return {
        "placed": placed,
        "failures": failures,
        "summary": placement_summary(chunk_list),
    }


# ── getting shards back ─────────────────────────────────────────────


async def fetch_shard(
    client: httpx.AsyncClient,
    chunk: Dict[str, Any],
    *,
    origin_node: str,
    file_id: str,
    index: int,
) -> bytes:
    peer_url = chunk.get("peer")
    if not peer_url:
        raise ReplicationError(f"shard {index} has no recorded peer")
    response = await client.get(
        f"{peer_url}/api/peers/shards/{origin_node}/{file_id}/{index}"
    )
    if response.status_code >= 400:
        raise ReplicationError(f"{peer_url} could not return shard {index}: HTTP {response.status_code}")
    payload = response.content
    expected = chunk.get("digest")
    if expected and digest(payload) != expected:
        raise ReplicationError(f"shard {index} from {peer_url} failed its digest check")
    return payload


async def gather_shards(
    *,
    metadata: Dict[str, Any],
    origin_node: str,
    file_id: str,
    fernet: Any = None,
    timeout: float = 60.0,
) -> Tuple[Path, str, List[str]]:
    """Stage every available shard, decrypted, into one temp directory.

    Returns (directory, shard base name, problems). Decryption happens here
    rather than in place because the decoder reads the raw files off disk —
    handing it ciphertext would silently reconstruct garbage.
    """
    chunk_list = metadata.get("chunk_list", [])
    staging = Path(tempfile.mkdtemp(prefix=f"cfs-{file_id[:8]}-"))
    problems: List[str] = []
    base_name = ""

    remote: List[Tuple[int, Dict[str, Any]]] = []

    for chunk in chunk_list:
        path = Path(chunk.get("path", ""))
        name = path.name
        if not name:
            continue
        if not is_size_sidecar(chunk) and not base_name:
            base_name = name.rsplit(".", 1)[0]

        if path.exists():
            payload = path.read_bytes()
            if chunk.get("encrypted") and fernet is not None:
                try:
                    payload = fernet.decrypt(payload)
                except Exception as exc:  # InvalidToken and friends
                    problems.append(f"{name}: decrypt failed ({exc})")
                    continue
            (staging / name).write_bytes(payload)
            continue

        index = shard_index(chunk)
        if index is None:
            problems.append(f"{name}: sidecar missing locally")
            continue
        remote.append((index, chunk))

    if remote:
        async with httpx.AsyncClient(timeout=timeout) as client:
            results = await asyncio.gather(
                *(
                    fetch_shard(client, chunk, origin_node=origin_node, file_id=file_id, index=index)
                    for index, chunk in remote
                ),
                return_exceptions=True,
            )
        for (index, chunk), result in zip(remote, results):
            name = Path(chunk.get("path", "")).name
            if isinstance(result, Exception):
                problems.append(f"{name}: {result}")
                continue
            payload = result
            if chunk.get("encrypted") and fernet is not None:
                try:
                    payload = fernet.decrypt(payload)
                except Exception as exc:
                    problems.append(f"{name}: decrypt failed ({exc})")
                    continue
            (staging / name).write_bytes(payload)

    return staging, base_name, problems


def cleanup(staging: Path) -> None:
    shutil.rmtree(str(staging), ignore_errors=True)
