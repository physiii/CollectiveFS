#!/usr/bin/env python3
"""Mount a CollectiveFS account as an ordinary directory.

    cfs_mount.py /media/collectivefs --api http://localhost:8010 --token <token>

Everything under the mount belongs to one account token. Any machine that mounts
with the same token sees the same namespace, because the node it talks to unions
its own files with the ones its peers hold for that token.

The mount drives the HTTP API rather than the storage layer directly. That is
deliberate: the API owns erasure coding, encryption, replication and peer
routing, so the filesystem gets all of it for free and a file created here is
sharded across the cluster exactly like one uploaded through the console.

Writes buffer to a local temp file and are uploaded on release — the pipeline
encodes whole files, so there is no meaningful partial-write to push. Reads pull
the reconstructed file once and serve offsets from a cache.
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import stat
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pyfuse3
import trio

log = logging.getLogger("cfs-mount")

ROOT_INODE = pyfuse3.ROOT_INODE
# How long a directory listing is trusted before the node is asked again. Short,
# because another machine may have written into the same account.
DEFAULT_TTL = 1.0
BLOCK_SIZE = 4096


# ── metrics ─────────────────────────────────────────────────────────


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


# Ordinary POSIX answers, not failures: a shell testing for a file it expects to
# be missing would otherwise show up as a filesystem error rate.
_EXPECTED_ERRNOS = {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST, errno.EISDIR, errno.ENOTDIR}


def timed(name: str):
    """Wrap a FUSE handler so every call lands in the metrics."""

    def decorate(func):
        async def wrapper(self, *args, **kwargs):
            start = time.perf_counter()
            failed = False
            try:
                return await func(self, *args, **kwargs)
            except pyfuse3.FUSEError as exc:
                failed = exc.errno not in _EXPECTED_ERRNOS
                raise
            except Exception:
                failed = True
                raise
            finally:
                self.stats.record(name, (time.perf_counter() - start) * 1000, failed)

        wrapper.__name__ = getattr(func, "__name__", name)
        return wrapper

    return decorate


# ── API client ──────────────────────────────────────────────────────


class NodeClient:
    """Blocking HTTP calls to the local node, run off the trio thread."""

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

    def upload(self, name: str, folder: str, payload: bytes) -> Dict[str, Any]:
        response = self._client.post(
            "/api/files/upload",
            files={"file": (name, payload, "application/octet-stream")},
            data={"folder": folder},
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


# ── inode bookkeeping ───────────────────────────────────────────────


class Node:
    __slots__ = ("inode", "path", "is_dir", "file_id", "size", "mtime")

    def __init__(self, inode: int, path: str, is_dir: bool, file_id: str = "",
                 size: int = 0, mtime: float = 0.0):
        self.inode = inode
        self.path = path
        self.is_dir = is_dir
        self.file_id = file_id
        self.size = size
        self.mtime = mtime or time.time()


class CollectiveFS(pyfuse3.Operations):
    """A CollectiveFS account presented as a directory tree."""

    supports_dot_lookup = True
    enable_writeback_cache = False

    def __init__(self, client: NodeClient, ttl: float = DEFAULT_TTL):
        super().__init__()
        self.client = client
        self.ttl = ttl
        self.stats = OpStats()

        self._lock = threading.RLock()
        self._by_inode: Dict[int, Node] = {}
        self._by_path: Dict[str, Node] = {}
        self._next_inode = ROOT_INODE + 1
        self._tree_fetched = 0.0
        # Kernel caching is off, so lookups and readdirs arrive constantly. A
        # forced refresh on each one turns a single `ls` into a request storm,
        # so forcing is debounced to this floor.
        self._force_floor = 0.25

        # Open write buffers keyed by inode, and the read cache.
        self._writers: Dict[int, Dict[str, Any]] = {}
        # Paths whose upload is in flight, each with an event that fires when
        # it lands. A released handle is no longer a writer, but the file does
        # not exist on the node yet either: anything touching it in that window
        # has to wait, or it sees a file with no id and no shards.
        self._inflight: Dict[str, trio.Event] = {}
        # Bytes we just wrote, kept until the node reports the file encoded.
        # A file is listed as soon as it is accepted but cannot be reconstructed
        # until its shards exist, so read-after-write would otherwise fail for
        # as long as encoding takes.
        self._pending: Dict[str, Path] = {}
        self._read_cache: Dict[str, Tuple[float, bytes]] = {}

        root = Node(ROOT_INODE, "", True)
        self._by_inode[ROOT_INODE] = root
        self._by_path[""] = root

    # ── tree state ──────────────────────────────────────────────────

    def _intern(self, path: str, is_dir: bool, file_id: str = "", size: int = 0,
                mtime: float = 0.0) -> Node:
        """Path -> Node, keeping inode numbers stable across refreshes.

        Stability matters: the kernel caches inode numbers, so reusing a number
        for a different path makes an open file suddenly point somewhere else.
        """
        node = self._by_path.get(path)
        if node is None:
            node = Node(self._next_inode, path, is_dir, file_id, size, mtime)
            self._next_inode += 1
            self._by_path[path] = node
            self._by_inode[node.inode] = node
        else:
            node.is_dir = is_dir
            if file_id:
                node.file_id = file_id
            if size:
                node.size = size
            if mtime:
                node.mtime = mtime
        return node

    def _forget_path(self, path: str) -> None:
        node = self._by_path.pop(path, None)
        if node is not None:
            self._by_inode.pop(node.inode, None)

    def _refresh(self, force: bool = False) -> None:
        age = time.time() - self._tree_fetched
        if force:
            if age < self._force_floor:
                return
        elif age < self.ttl:
            return
        try:
            tree = self.client.tree()
        except httpx.HTTPError as exc:
            log.warning("tree refresh failed: %s", exc)
            return

        with self._lock:
            live: set = {""}
            for folder in tree.get("folders", []):
                path = folder.get("path") or ""
                if not path:
                    continue
                live.add(path)
                self._intern(path, True)

            for entry in tree.get("files", []):
                folder = entry.get("folder") or ""
                path = f"{folder}/{entry['name']}" if folder else entry["name"]
                live.add(path)
                mtime = _parse_time(entry.get("created_at"))
                self._intern(path, False, entry.get("id", ""), int(entry.get("size") or 0), mtime)
                # Once the node has encoded it, the local copy is redundant.
                if entry.get("status") in ("stored", "complete"):
                    self._release_pending(entry.get("id", ""))

            # Drop anything that vanished elsewhere, but never a path with an
            # open writer — that file is mid-creation and not in the tree yet.
            for path in [p for p in self._by_path if p and p not in live]:
                node = self._by_path[path]
                if node.inode in self._writers or path in self._inflight:
                    continue
                self._forget_path(path)

            self._tree_fetched = time.time()

    def _node(self, inode: int) -> Node:
        node = self._by_inode.get(inode)
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return node

    def _children(self, path: str) -> List[Node]:
        prefix = f"{path}/" if path else ""
        depth = len(PurePosixPath(path).parts) if path else 0
        out = []
        for candidate, node in self._by_path.items():
            if not candidate or not candidate.startswith(prefix):
                continue
            if len(PurePosixPath(candidate).parts) != depth + 1:
                continue
            out.append(node)
        return sorted(out, key=lambda item: item.path)

    def _attrs(self, node: Node) -> pyfuse3.EntryAttributes:
        attr = pyfuse3.EntryAttributes()
        attr.st_ino = node.inode
        # No kernel caching of entries or attributes. Another machine can change
        # this namespace at any moment, and a cached dentry would serve a stale
        # answer — or a stale *negative* answer, hiding a file that now exists.
        # The refresh TTL below still throttles how often the node is asked.
        attr.entry_timeout = 0.0
        attr.attr_timeout = 0.0
        if node.is_dir:
            attr.st_mode = stat.S_IFDIR | 0o755
            attr.st_size = 0
            attr.st_nlink = 2
        else:
            attr.st_mode = stat.S_IFREG | 0o644
            writer = self._writers.get(node.inode)
            attr.st_size = writer["size"] if writer else node.size
            attr.st_nlink = 1
        attr.st_uid = os.getuid()
        attr.st_gid = os.getgid()
        attr.st_rdev = 0
        attr.st_blksize = BLOCK_SIZE
        attr.st_blocks = (attr.st_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        ns = int(node.mtime * 1e9)
        attr.st_atime_ns = ns
        attr.st_mtime_ns = ns
        attr.st_ctime_ns = ns
        return attr

    async def _in_thread(self, func, *args):
        return await trio.to_thread.run_sync(lambda: func(*args))

    async def _settle(self, path: str, timeout: float = 120.0) -> None:
        """Block until any upload of `path` has finished.

        `close()` returns before FUSE calls release, so a script can rename or
        read a file while its upload is still running. Without waiting, those
        operations would act on a file that has no id yet.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            event = self._inflight.get(path)
            if event is None:
                return
            with trio.move_on_after(1.0):
                await event.wait()

    # ── read path ───────────────────────────────────────────────────

    def _fetch(self, node: Node) -> bytes:
        pending = self._pending.get(node.file_id)
        if pending is not None:
            try:
                return pending.read_bytes()
            except OSError:
                self._pending.pop(node.file_id, None)
        cached = self._read_cache.get(node.file_id)
        if cached and (time.time() - cached[0]) < 30:
            return cached[1]
        payload = self.client.download(node.file_id)
        self._read_cache[node.file_id] = (time.time(), payload)
        # Keep the cache small; this is a convenience layer, not a page cache.
        if len(self._read_cache) > 32:
            oldest = min(self._read_cache, key=lambda key: self._read_cache[key][0])
            self._read_cache.pop(oldest, None)
        return payload

    def _invalidate(self, file_id: str) -> None:
        self._read_cache.pop(file_id, None)

    def _release_pending(self, file_id: str) -> None:
        path = self._pending.pop(file_id, None)
        if path is None:
            return
        try:
            path.unlink()
        except OSError:
            pass

    def _hold_pending(self, file_id: str, path: Path) -> None:
        """Keep a just-written body readable until the node has encoded it."""
        if not file_id:
            try:
                path.unlink()
            except OSError:
                pass
            return
        self._pending[file_id] = path
        # Bounded: this is a hand-off buffer, not a cache.
        while len(self._pending) > 16:
            oldest = next(iter(self._pending))
            self._release_pending(oldest)

    # ── FUSE operations ─────────────────────────────────────────────

    @timed("getattr")
    async def getattr(self, inode: int, ctx=None) -> pyfuse3.EntryAttributes:
        await self._in_thread(self._refresh)
        return self._attrs(self._node(inode))

    @timed("lookup")
    async def lookup(self, parent_inode: int, name: bytes, ctx=None) -> pyfuse3.EntryAttributes:
        await self._in_thread(self._refresh)
        parent = self._node(parent_inode)
        label = name.decode("utf-8", "surrogateescape")
        if label == ".":
            return self._attrs(parent)
        if label == "..":
            up = str(PurePosixPath(parent.path).parent) if parent.path else ""
            up = "" if up == "." else up
            return self._attrs(self._by_path.get(up, self._by_inode[ROOT_INODE]))

        path = f"{parent.path}/{label}" if parent.path else label
        node = self._by_path.get(path)
        if node is None:
            # A file written moments ago on another machine may not be in our
            # cached tree yet; one forced refresh is cheaper than a false ENOENT.
            await self._in_thread(self._refresh, True)
            node = self._by_path.get(path)
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._attrs(node)

    @timed("readdir")
    async def readdir(self, inode: int, start_id: int, token) -> None:
        await self._in_thread(self._refresh, True)
        node = self._node(inode)
        if not node.is_dir:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        for index, child in enumerate(self._children(node.path)):
            if index < start_id:
                continue
            name = PurePosixPath(child.path).name.encode("utf-8", "surrogateescape")
            if not pyfuse3.readdir_reply(token, name, self._attrs(child), index + 1):
                return

    @timed("open")
    async def open(self, inode: int, flags: int, ctx=None) -> pyfuse3.FileInfo:
        node = self._node(inode)
        if node.is_dir:
            raise pyfuse3.FUSEError(errno.EISDIR)

        writing = bool(flags & (os.O_WRONLY | os.O_RDWR))
        if writing and inode not in self._writers:
            handle = tempfile.NamedTemporaryFile(prefix="cfs-w-", delete=False)
            existing = b""
            if not flags & os.O_TRUNC and node.file_id:
                # Opening for modification: start from what is already stored,
                # or an append/partial rewrite would truncate the file.
                try:
                    existing = await self._in_thread(self._fetch, node)
                except httpx.HTTPError as exc:
                    handle.close()
                    os.unlink(handle.name)
                    log.error("could not stage %s for writing: %s", node.path, exc)
                    raise pyfuse3.FUSEError(errno.EIO)
            handle.write(existing)
            handle.flush()
            self._writers[inode] = {
                "handle": handle,
                "size": len(existing),
                "dirty": bool(flags & os.O_TRUNC),
            }
            if flags & os.O_TRUNC:
                handle.truncate(0)
                self._writers[inode]["size"] = 0

        return pyfuse3.FileInfo(fh=inode, keep_cache=False)

    @timed("read")
    async def read(self, fh: int, off: int, size: int) -> bytes:
        node = self._node(fh)
        writer = self._writers.get(fh)
        if writer:
            writer["handle"].flush()
            with open(writer["handle"].name, "rb") as source:
                source.seek(off)
                payload = source.read(size)
        else:
            await self._settle(node.path)
            if not node.file_id:
                return b""
            try:
                blob = await self._in_thread(self._fetch, node)
            except httpx.HTTPError as exc:
                log.error("read %s failed: %s", node.path, exc)
                raise pyfuse3.FUSEError(errno.EIO)
            payload = blob[off:off + size]
        self.stats.add_read(len(payload))
        return payload

    @timed("create")
    async def create(self, parent_inode: int, name: bytes, mode: int, flags: int, ctx=None):
        parent = self._node(parent_inode)
        label = name.decode("utf-8", "surrogateescape")
        path = f"{parent.path}/{label}" if parent.path else label

        node = self._intern(path, False)
        handle = tempfile.NamedTemporaryFile(prefix="cfs-w-", delete=False)
        self._writers[node.inode] = {"handle": handle, "size": 0, "dirty": True}
        return pyfuse3.FileInfo(fh=node.inode, keep_cache=False), self._attrs(node)

    @timed("write")
    async def write(self, fh: int, off: int, buf: bytes) -> int:
        writer = self._writers.get(fh)
        if writer is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        handle = writer["handle"]
        handle.seek(off)
        handle.write(buf)
        handle.flush()
        writer["size"] = max(writer["size"], off + len(buf))
        writer["dirty"] = True
        self.stats.add_write(len(buf))
        return len(buf)

    @timed("release")
    async def release(self, fh: int) -> None:
        writer = self._writers.pop(fh, None)
        if writer is None:
            return
        node = self._node(fh)
        settled = trio.Event()
        self._inflight[node.path] = settled
        handle = writer["handle"]
        handle.flush()
        handle.close()

        # Publish the final size before the upload starts. close() returns as
        # soon as release is queued, so a caller can stat the file while the
        # upload is still running; without this it sees the size the node was
        # created with — zero — and reads nothing.
        node.size = writer["size"]
        node.mtime = time.time()

        try:
            if not writer["dirty"]:
                return
            with open(handle.name, "rb") as source:
                payload = source.read()

            folder = str(PurePosixPath(node.path).parent)
            folder = "" if folder == "." else folder
            label = PurePosixPath(node.path).name

            previous = node.file_id
            result = await self._in_thread(self.client.upload, label, folder, payload)
            node.file_id = result.get("id", "")
            node.size = len(payload)
            node.mtime = time.time()
            self._invalidate(previous)

            # Replacing a file writes a new object; drop the old one or the
            # directory would show the same name twice.
            if previous and previous != node.file_id:
                try:
                    await self._in_thread(self.client.delete, previous)
                except httpx.HTTPError:
                    log.warning("could not remove superseded %s", previous)
            self._tree_fetched = 0.0
        except httpx.HTTPError as exc:
            log.error("upload of %s failed: %s", node.path, exc)
            raise pyfuse3.FUSEError(errno.EIO)
        finally:
            self._inflight.pop(node.path, None)
            settled.set()
            if node.file_id:
                self._hold_pending(node.file_id, Path(handle.name))
            else:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass

    @timed("setattr")
    async def setattr(self, inode: int, attr, fields, fh, ctx=None):
        node = self._node(inode)
        if fields.update_size:
            writer = self._writers.get(inode)
            if writer:
                writer["handle"].truncate(attr.st_size)
                writer["size"] = attr.st_size
                writer["dirty"] = True
            elif node.file_id or self._inflight.get(node.path):
                await self._settle(node.path)
                # Truncate outside an open handle: re-upload the shortened body.
                try:
                    blob = await self._in_thread(self._fetch, node)
                    trimmed = blob[: attr.st_size].ljust(attr.st_size, b"\0")
                    folder = str(PurePosixPath(node.path).parent)
                    folder = "" if folder == "." else folder
                    previous = node.file_id
                    result = await self._in_thread(
                        self.client.upload, PurePosixPath(node.path).name, folder, trimmed
                    )
                    node.file_id = result.get("id", "")
                    node.size = attr.st_size
                    self._invalidate(previous)
                    if previous and previous != node.file_id:
                        await self._in_thread(self.client.delete, previous)
                    self._tree_fetched = 0.0
                except httpx.HTTPError as exc:
                    log.error("truncate of %s failed: %s", node.path, exc)
                    raise pyfuse3.FUSEError(errno.EIO)
        if fields.update_mtime and attr.st_mtime_ns:
            node.mtime = attr.st_mtime_ns / 1e9
        return self._attrs(node)

    @timed("unlink")
    async def unlink(self, parent_inode: int, name: bytes, ctx=None) -> None:
        parent = self._node(parent_inode)
        label = name.decode("utf-8", "surrogateescape")
        path = f"{parent.path}/{label}" if parent.path else label
        node = self._by_path.get(path)
        if node is None or node.is_dir:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._settle(path)
        try:
            if node.file_id:
                await self._in_thread(self.client.delete, node.file_id)
        except httpx.HTTPError as exc:
            log.error("unlink %s failed: %s", path, exc)
            raise pyfuse3.FUSEError(errno.EIO)
        self._invalidate(node.file_id)
        self._forget_path(path)
        self._tree_fetched = 0.0

    @timed("mkdir")
    async def mkdir(self, parent_inode: int, name: bytes, mode: int, ctx=None):
        parent = self._node(parent_inode)
        label = name.decode("utf-8", "surrogateescape")
        path = f"{parent.path}/{label}" if parent.path else label
        if path in self._by_path:
            raise pyfuse3.FUSEError(errno.EEXIST)
        try:
            await self._in_thread(self.client.mkdir, path)
        except httpx.HTTPError as exc:
            log.error("mkdir %s failed: %s", path, exc)
            raise pyfuse3.FUSEError(errno.EIO)
        node = self._intern(path, True)
        self._tree_fetched = 0.0
        return self._attrs(node)

    @timed("rmdir")
    async def rmdir(self, parent_inode: int, name: bytes, ctx=None) -> None:
        parent = self._node(parent_inode)
        label = name.decode("utf-8", "surrogateescape")
        path = f"{parent.path}/{label}" if parent.path else label
        node = self._by_path.get(path)
        if node is None or not node.is_dir:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if self._children(path):
            raise pyfuse3.FUSEError(errno.ENOTEMPTY)
        try:
            await self._in_thread(self.client.rmdir, path)
        except httpx.HTTPError as exc:
            log.error("rmdir %s failed: %s", path, exc)
            raise pyfuse3.FUSEError(errno.EIO)
        self._forget_path(path)
        self._tree_fetched = 0.0

    @timed("rename")
    async def rename(self, parent_old: int, name_old: bytes, parent_new: int,
                     name_new: bytes, flags: int = 0, ctx=None) -> None:
        old_parent = self._node(parent_old)
        new_parent = self._node(parent_new)
        old_label = name_old.decode("utf-8", "surrogateescape")
        new_label = name_new.decode("utf-8", "surrogateescape")
        old_path = f"{old_parent.path}/{old_label}" if old_parent.path else old_label
        new_path = f"{new_parent.path}/{new_label}" if new_parent.path else new_label

        node = self._by_path.get(old_path)
        if node is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._settle(old_path)

        if node.is_dir:
            # Folders are labels on files, so moving one means re-labelling
            # every file beneath it.
            await self._in_thread(self._refresh, True)
            moved = [child for child in self._by_path.values()
                     if child.path == old_path or child.path.startswith(f"{old_path}/")]
            try:
                await self._in_thread(self.client.mkdir, new_path)
                for child in moved:
                    if child.is_dir or not child.file_id:
                        continue
                    suffix = child.path[len(old_path):].lstrip("/")
                    target_folder = str(PurePosixPath(f"{new_path}/{suffix}").parent)
                    target_folder = "" if target_folder == "." else target_folder
                    await self._in_thread(
                        self.client.patch, child.file_id, {"folder": target_folder}
                    )
                await self._in_thread(self.client.rmdir, old_path)
            except httpx.HTTPError as exc:
                log.error("rename %s -> %s failed: %s", old_path, new_path, exc)
                raise pyfuse3.FUSEError(errno.EIO)
        else:
            existing = self._by_path.get(new_path)
            folder = str(PurePosixPath(new_path).parent)
            folder = "" if folder == "." else folder
            try:
                if existing is not None and not existing.is_dir and existing.file_id:
                    # POSIX rename replaces the destination silently.
                    await self._in_thread(self.client.delete, existing.file_id)
                    self._forget_path(new_path)
                await self._in_thread(
                    self.client.patch, node.file_id,
                    {"name": PurePosixPath(new_path).name, "folder": folder},
                )
            except httpx.HTTPError as exc:
                log.error("rename %s -> %s failed: %s", old_path, new_path, exc)
                raise pyfuse3.FUSEError(errno.EIO)

        self._forget_path(old_path)
        self._tree_fetched = 0.0
        await self._in_thread(self._refresh, True)

    @timed("statfs")
    async def statfs(self, ctx=None) -> pyfuse3.StatvfsData:
        info = pyfuse3.StatvfsData()
        info.f_bsize = BLOCK_SIZE
        info.f_frsize = BLOCK_SIZE
        try:
            overview = await self._in_thread(self.client.stats)
            collective = overview.get("collective", {})
            total = int(collective.get("quota_bytes") or 0)
            used = int(collective.get("used_bytes") or 0)
        except (httpx.HTTPError, ValueError):
            total = used = 0
        info.f_blocks = max(total // BLOCK_SIZE, 1)
        info.f_bfree = max((total - used) // BLOCK_SIZE, 0)
        info.f_bavail = info.f_bfree
        info.f_files = len([n for n in self._by_path.values() if not n.is_dir])
        info.f_ffree = 1 << 20
        info.f_favail = info.f_ffree
        info.f_namemax = 255
        return info

    async def flush(self, fh: int) -> None:
        writer = self._writers.get(fh)
        if writer:
            writer["handle"].flush()

    async def fsync(self, fh: int, datasync: bool) -> None:
        await self.flush(fh)

    async def fsyncdir(self, fh: int, datasync: bool) -> None:
        return None

    async def opendir(self, inode: int, ctx=None) -> int:
        self._node(inode)
        return inode

    async def releasedir(self, fh: int) -> None:
        return None

    async def forget(self, inode_list) -> None:
        return None


def _parse_time(value: Optional[str]) -> float:
    if not value:
        return time.time()
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


# ── entry point ─────────────────────────────────────────────────────


async def _report_loop(fs: CollectiveFS, mountpoint: str, node_label: str, interval: float):
    """Push performance counters to the node so the UI can chart them."""
    while True:
        await trio.sleep(interval)
        payload = fs.stats.drain()
        payload["mountpoint"] = mountpoint
        payload["node"] = node_label
        payload["files"] = len([n for n in fs._by_path.values() if not n.is_dir])
        payload["cache_entries"] = len(fs._read_cache)
        try:
            await trio.to_thread.run_sync(lambda: fs.client.report(payload))
        except Exception as exc:  # never let telemetry kill the mount
            log.debug("metrics report failed: %s", exc)


async def _serve(fs: CollectiveFS, mountpoint: str, node_label: str, interval: float):
    async with trio.open_nursery() as nursery:
        nursery.start_soon(pyfuse3.main)
        nursery.start_soon(_report_loop, fs, mountpoint, node_label, interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mount a CollectiveFS account as a directory.")
    parser.add_argument("mountpoint", help="where to mount, e.g. /media/collectivefs")
    parser.add_argument("--api", default=os.environ.get("CFS_API", "http://localhost:8010"),
                        help="base URL of the local CollectiveFS node")
    parser.add_argument("--token", default=os.environ.get("CFS_TOKEN", ""),
                        help="account token; every machine using it sees the same files")
    parser.add_argument("--ttl", type=float, default=DEFAULT_TTL,
                        help="seconds a directory listing is trusted")
    parser.add_argument("--metrics-interval", type=float, default=5.0,
                        help="seconds between performance reports")
    parser.add_argument("--allow-other", action="store_true",
                        help="let other users access the mount")
    parser.add_argument("--foreground", action="store_true", help="do not daemonize")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = args.token.strip()
    if not token:
        # Adopt the node's own account so a bare `cfs_mount.py /mnt` works.
        try:
            with httpx.Client(base_url=args.api, timeout=15.0) as probe:
                token = probe.get("/api/account").json()["token"]
            log.info("using this node's default account token")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("no --token given and the node could not supply one: %s", exc)
            return 2

    os.makedirs(args.mountpoint, exist_ok=True)
    client = NodeClient(args.api, token)
    try:
        client.tree()
    except httpx.HTTPError as exc:
        log.error("cannot reach the node at %s: %s", args.api, exc)
        return 2

    fs = CollectiveFS(client, ttl=args.ttl)
    options = set(pyfuse3.default_options)
    options.add("fsname=collectivefs")
    options.discard("default_permissions")
    if args.allow_other:
        options.add("allow_other")
    if args.debug:
        options.add("debug")

    pyfuse3.init(fs, args.mountpoint, options)
    log.info("mounted %s from %s", args.mountpoint, args.api)

    try:
        trio.run(_serve, fs, args.mountpoint, os.uname().nodename, args.metrics_interval)
    except KeyboardInterrupt:
        pass
    finally:
        pyfuse3.close(unmount=True)
        client.close()
        log.info("unmounted %s", args.mountpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
