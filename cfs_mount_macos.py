#!/usr/bin/env python3
"""Mount a CollectiveFS account as a directory on macOS (fuse-t + fusepy).

    cfs_mount_macos.py ~/cfs/.cfs --api http://localhost:8010 [--token <token>]

The shipped ``cfs_mount.py`` is built on pyfuse3 (libfuse3), which is Linux-only.
macOS has no libfuse3, but ``fuse-t`` provides a userspace, NFS-backed FUSE with
a libfuse2-compatible ``/usr/local/lib/libfuse-t.dylib`` that the pure-Python
``fusepy`` binding can drive — no kernel extension, no reboot.

This is a faithful, path-based re-implementation of the same behaviour: it talks
to the node's HTTP API (which owns erasure coding, encryption, replication and
peer routing) via the shared ``NodeClient``. Writes buffer to a local temp file
and upload on release; reads pull the reconstructed file once and serve offsets
from a small cache. So a file written here is sharded across the cluster exactly
like one uploaded through the console.
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
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

import httpx

# fusepy locates libfuse through ctypes. fuse-t installs a libfuse2-compatible
# dylib here; point fusepy at it before the import so a bare run just works.
os.environ.setdefault("FUSE_LIBRARY_PATH", "/usr/local/lib/libfuse-t.dylib")

from fuse import FUSE, FuseOSError, Operations  # noqa: E402  (after env setup)

from cfs_node_client import (  # noqa: E402
    BLOCK_SIZE,
    DEFAULT_TTL,
    NodeClient,
    OpStats,
    parse_time,
)

log = logging.getLogger("cfs-mount-macos")

# errno for "attribute not found" — macOS spells it ENOATTR; fall back for safety.
ENOATTR = getattr(errno, "ENOATTR", getattr(errno, "ENODATA", errno.ENOTSUP))


class Entry:
    """One node in the presented tree, keyed by its collective path."""

    __slots__ = ("path", "is_dir", "file_id", "size", "mtime", "symlink")

    def __init__(self, path: str, is_dir: bool, file_id: str = "", size: int = 0,
                 mtime: float = 0.0, symlink: str = ""):
        self.path = path
        self.is_dir = is_dir
        self.file_id = file_id
        self.size = size
        self.mtime = mtime or time.time()
        self.symlink = symlink


def _internal(path: str) -> str:
    """FUSE path ('/a/b', '/') -> collective path ('a/b', '')."""
    return path.lstrip("/")


def _is_apple_double(ipath: str) -> bool:
    """macOS AppleDouble/desktop sidecars.

    fuse-t is NFS-backed and does not route xattr syscalls to FUSE, so macOS
    falls back to writing ._<name> (AppleDouble) and .DS_Store files for every
    entry. Left alone they get erasure-coded into the network and their names
    collide with the real tree (a ._git-repos file blocks mkdir git-repos).
    The mount keeps them in a per-process, in-memory store instead: macOS is
    satisfied, and nothing apple-specific ever reaches the collective.
    """
    name = ipath.rsplit("/", 1)[-1]
    return name.startswith("._") or name == ".DS_Store"


def _split(ipath: str) -> Tuple[str, str]:
    """collective path -> (folder, name), folder '' at the root."""
    pp = PurePosixPath(ipath)
    folder = str(pp.parent)
    folder = "" if folder == "." else folder
    return folder, pp.name


class CollectiveFSMac(Operations):
    """A CollectiveFS account presented as a directory tree, via fusepy."""

    def __init__(self, client: NodeClient, ttl: float = DEFAULT_TTL):
        self.client = client
        self.ttl = ttl
        self.stats = OpStats()

        self._lock = threading.RLock()
        self._by_path: Dict[str, Entry] = {"": Entry("", True)}
        self._tree_fetched = 0.0
        # A forced refresh on every lookup would turn one `ls` into a request
        # storm; debounce forcing to this floor. With ttl<=0 (strong-consistency
        # mode, needed for git's lock/rename protocol) the debounce is disabled
        # so every create/rename/unlink is immediately visible.
        self._force_floor = 0.0 if ttl <= 0 else 0.25

        # Open write buffers keyed by collective path.
        self._writers: Dict[str, Dict[str, Any]] = {}
        self._fh_paths: Dict[int, str] = {}
        self._next_fh = 1
        # Paths whose upload is in flight, each with an event that fires when it
        # lands: a released handle is no longer a writer, but the file does not
        # exist on the node yet, so readers/renamers in that window must wait.
        self._inflight: Dict[str, threading.Event] = {}
        # Just-written bodies, kept until the node reports the file encoded, so
        # read-after-write works before shards exist.
        self._pending: Dict[str, Path] = {}
        self._read_cache: Dict[str, Tuple[float, bytes]] = {}
        # In-memory extended attributes. Without a working xattr surface macOS
        # falls back to AppleDouble ._ sidecar files, which both clutter the
        # erasure-coded store and multiply the write burst. Accepting xattrs
        # (not persisted across remounts — git does not need that) keeps macOS
        # on native xattr calls instead.
        self._xattrs: Dict[str, Dict[str, bytes]] = {}
        # AppleDouble/.DS_Store sidecars, kept in memory only (see
        # _is_apple_double). ipath -> bytes.
        self._apple: Dict[str, bytes] = {}

    # ── tree state ──────────────────────────────────────────────────

    def _intern(self, path: str, is_dir: bool, file_id: str = "", size: int = 0,
                mtime: float = 0.0, symlink: str = "") -> Entry:
        entry = self._by_path.get(path)
        if entry is None:
            entry = Entry(path, is_dir, file_id, size, mtime, symlink)
            self._by_path[path] = entry
        else:
            entry.is_dir = is_dir
            if file_id:
                entry.file_id = file_id
            if size:
                entry.size = size
            if mtime:
                entry.mtime = mtime
            entry.symlink = symlink
        return entry

    def _forget(self, path: str) -> None:
        self._by_path.pop(path, None)

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
            live = {""}
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
                mtime = parse_time(entry.get("created_at"))
                self._intern(path, False, entry.get("id", ""),
                             int(entry.get("size") or 0), mtime,
                             entry.get("symlink") or "")
                if entry.get("status") in ("stored", "complete"):
                    self._release_pending(entry.get("id", ""))

            # Drop anything that vanished elsewhere, but never a path with an
            # open writer or an in-flight upload (not in the tree yet).
            for path in [p for p in self._by_path if p and p not in live]:
                if path in self._writers or path in self._inflight:
                    continue
                self._forget(path)

            self._tree_fetched = time.time()

    def _entry(self, ipath: str) -> Entry:
        entry = self._by_path.get(ipath)
        if entry is None:
            raise FuseOSError(errno.ENOENT)
        return entry

    def _children(self, path: str) -> List[Entry]:
        prefix = f"{path}/" if path else ""
        depth = len(PurePosixPath(path).parts) if path else 0
        out = []
        for candidate, entry in self._by_path.items():
            if not candidate or not candidate.startswith(prefix):
                continue
            if len(PurePosixPath(candidate).parts) != depth + 1:
                continue
            out.append(entry)
        return sorted(out, key=lambda item: item.path)

    def _settle(self, path: str, timeout: float = 120.0) -> None:
        """Block until any in-flight upload of `path` has finished."""
        event = self._inflight.get(path)
        if event is not None:
            event.wait(timeout)

    # ── read path ───────────────────────────────────────────────────

    def _fetch(self, entry: Entry) -> bytes:
        pending = self._pending.get(entry.file_id)
        if pending is not None:
            try:
                return pending.read_bytes()
            except OSError:
                self._pending.pop(entry.file_id, None)
        cached = self._read_cache.get(entry.file_id)
        if cached and (time.time() - cached[0]) < 30:
            return cached[1]
        # A file is listed the moment it is accepted, but cannot be
        # reconstructed until its shards finish encoding — the node answers 4xx
        # until then. If we no longer hold the local copy, wait briefly for
        # encoding rather than surfacing an I/O error to the caller.
        payload = None
        last_exc: Optional[Exception] = None
        for attempt in range(12):
            try:
                payload = self.client.download(entry.file_id)
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(0.25)
        if payload is None:
            raise last_exc if last_exc else httpx.HTTPError("download failed")
        self._read_cache[entry.file_id] = (time.time(), payload)
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
        if not file_id:
            try:
                path.unlink()
            except OSError:
                pass
            return
        self._pending[file_id] = path
        # Keep just-written bodies until the node confirms them stored (see
        # _refresh -> _release_pending). The cap is only a memory backstop; it
        # must be well above any write burst (git init/push touch dozens of
        # small files) or a re-open would download a not-yet-encoded file.
        while len(self._pending) > 2048:
            oldest = next(iter(self._pending))
            self._release_pending(oldest)

    def _attrs(self, entry: Entry) -> Dict[str, Any]:
        now = entry.mtime or time.time()
        base = {
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
            "st_atime": now,
            "st_mtime": now,
            "st_ctime": now,
        }
        if entry.is_dir:
            base.update(st_mode=stat.S_IFDIR | 0o755, st_nlink=2, st_size=0)
        elif entry.symlink:
            base.update(st_mode=stat.S_IFLNK | 0o777, st_nlink=1,
                        st_size=len(entry.symlink.encode("utf-8")))
        else:
            writer = self._writers.get(entry.path)
            size = writer["size"] if writer else entry.size
            base.update(st_mode=stat.S_IFREG | 0o644, st_nlink=1, st_size=size)
        return base

    def _alloc_fh(self, ipath: str) -> int:
        with self._lock:
            fh = self._next_fh
            self._next_fh += 1
            self._fh_paths[fh] = ipath
            return fh

    # ── FUSE operations (path-based) ─────────────────────────────────

    def getattr(self, path, fh=None):
        start = time.perf_counter()
        try:
            ipath = _internal(path)
            if _is_apple_double(ipath):
                if ipath not in self._apple:
                    raise FuseOSError(errno.ENOENT)
                now = time.time()
                return {"st_mode": stat.S_IFREG | 0o644, "st_nlink": 1,
                        "st_size": len(self._apple[ipath]), "st_uid": os.getuid(),
                        "st_gid": os.getgid(), "st_atime": now, "st_mtime": now,
                        "st_ctime": now}
            self._refresh()
            entry = self._by_path.get(ipath)
            if entry is None:
                self._refresh(True)
                entry = self._by_path.get(ipath)
            if entry is None:
                raise FuseOSError(errno.ENOENT)
            return self._attrs(entry)
        finally:
            self.stats.record("getattr", (time.perf_counter() - start) * 1000)

    def readdir(self, path, fh):
        self._refresh(True)
        ipath = _internal(path)
        entry = self._entry(ipath)
        if not entry.is_dir:
            raise FuseOSError(errno.ENOTDIR)
        names = [".", ".."]
        for child in self._children(ipath):
            names.append(PurePosixPath(child.path).name)
        return names

    def open(self, path, flags):
        ipath = _internal(path)
        if _is_apple_double(ipath):
            if flags & (os.O_WRONLY | os.O_RDWR):
                self._apple.setdefault(ipath, b"")
                if flags & os.O_TRUNC:
                    self._apple[ipath] = b""
            elif ipath not in self._apple:
                raise FuseOSError(errno.ENOENT)
            return self._alloc_fh(ipath)
        entry = self._entry(ipath)
        if entry.is_dir:
            raise FuseOSError(errno.EISDIR)
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR))
        if writing and ipath not in self._writers:
            handle = tempfile.NamedTemporaryFile(prefix="cfs-w-", delete=False)
            existing = b""
            if not flags & os.O_TRUNC and entry.file_id:
                try:
                    existing = self._fetch(entry)
                except httpx.HTTPError as exc:
                    handle.close()
                    os.unlink(handle.name)
                    log.error("could not stage %s for writing: %s", ipath, exc)
                    raise FuseOSError(errno.EIO)
            handle.write(existing)
            handle.flush()
            with self._lock:
                self._writers[ipath] = {
                    "handle": handle,
                    "size": len(existing),
                    "dirty": bool(flags & os.O_TRUNC),
                }
                if flags & os.O_TRUNC:
                    handle.truncate(0)
                    self._writers[ipath]["size"] = 0
        return self._alloc_fh(ipath)

    def create(self, path, mode, fi=None):
        ipath = _internal(path)
        if _is_apple_double(ipath):
            self._apple[ipath] = b""
            return self._alloc_fh(ipath)
        self._intern(ipath, False)
        handle = tempfile.NamedTemporaryFile(prefix="cfs-w-", delete=False)
        with self._lock:
            self._writers[ipath] = {"handle": handle, "size": 0, "dirty": True}
        return self._alloc_fh(ipath)

    def read(self, path, size, offset, fh):
        start = time.perf_counter()
        try:
            ipath = _internal(path)
            if _is_apple_double(ipath):
                data = self._apple.get(ipath, b"")
                return data[offset:offset + size]
            writer = self._writers.get(ipath)
            if writer:
                writer["handle"].flush()
                with open(writer["handle"].name, "rb") as source:
                    source.seek(offset)
                    payload = source.read(size)
            else:
                self._settle(ipath)
                entry = self._entry(ipath)
                if not entry.file_id:
                    return b""
                try:
                    blob = self._fetch(entry)
                except httpx.HTTPError as exc:
                    log.error("read %s failed: %s", ipath, exc)
                    raise FuseOSError(errno.EIO)
                payload = blob[offset:offset + size]
            self.stats.add_read(len(payload))
            return payload
        finally:
            self.stats.record("read", (time.perf_counter() - start) * 1000)

    def write(self, path, data, offset, fh):
        start = time.perf_counter()
        try:
            ipath = _internal(path)
            if _is_apple_double(ipath):
                buf = bytearray(self._apple.get(ipath, b""))
                if offset > len(buf):
                    buf.extend(b"\0" * (offset - len(buf)))
                buf[offset:offset + len(data)] = data
                self._apple[ipath] = bytes(buf)
                return len(data)
            writer = self._writers.get(ipath)
            if writer is None:
                raise FuseOSError(errno.EBADF)
            handle = writer["handle"]
            handle.seek(offset)
            handle.write(data)
            handle.flush()
            writer["size"] = max(writer["size"], offset + len(data))
            writer["dirty"] = True
            self.stats.add_write(len(data))
            return len(data)
        finally:
            self.stats.record("write", (time.perf_counter() - start) * 1000)

    def truncate(self, path, length, fh=None):
        ipath = _internal(path)
        if _is_apple_double(ipath):
            data = self._apple.get(ipath, b"")
            self._apple[ipath] = data[:length].ljust(length, b"\0")
            return 0
        entry = self._by_path.get(ipath)
        writer = self._writers.get(ipath)
        if writer:
            writer["handle"].truncate(length)
            writer["handle"].flush()
            writer["size"] = length
            writer["dirty"] = True
            return 0
        if entry and (entry.file_id or self._inflight.get(ipath)):
            self._settle(ipath)
            try:
                blob = self._fetch(entry)
                trimmed = blob[:length].ljust(length, b"\0")
                folder, name = _split(ipath)
                previous = entry.file_id
                result = self.client.upload(name, folder, trimmed)
                entry.file_id = result.get("id", "")
                entry.size = length
                self._invalidate(previous)
                if previous and previous != entry.file_id:
                    self.client.delete(previous)
                self._tree_fetched = 0.0
            except httpx.HTTPError as exc:
                log.error("truncate of %s failed: %s", ipath, exc)
                raise FuseOSError(errno.EIO)
        return 0

    def flush(self, path, fh):
        ipath = _internal(path)
        if _is_apple_double(ipath):
            return 0
        writer = self._writers.get(ipath)
        if writer:
            writer["handle"].flush()
        return 0

    def fsync(self, path, datasync, fh):
        return self.flush(path, fh)

    def release(self, path, fh):
        start = time.perf_counter()
        ipath = _internal(path)
        if _is_apple_double(ipath):
            with self._lock:
                self._fh_paths.pop(fh, None)
            return 0
        with self._lock:
            self._fh_paths.pop(fh, None)
            writer = self._writers.pop(ipath, None)
        if writer is None:
            self.stats.record("release", (time.perf_counter() - start) * 1000)
            return 0

        settled = threading.Event()
        self._inflight[ipath] = settled
        entry = self._intern(ipath, False)
        handle = writer["handle"]
        handle.flush()
        handle.close()

        # Publish the final size before the upload so a caller that stats the
        # file mid-upload sees the real size, not the zero it was created with.
        entry.size = writer["size"]
        entry.mtime = time.time()

        failed = False
        try:
            if not writer["dirty"]:
                return 0
            with open(handle.name, "rb") as source:
                payload = source.read()
            folder, name = _split(ipath)
            previous = entry.file_id
            result = self.client.upload(name, folder, payload)
            entry.file_id = result.get("id", "")
            entry.size = len(payload)
            entry.mtime = time.time()
            self._invalidate(previous)
            if previous and previous != entry.file_id:
                try:
                    self.client.delete(previous)
                except httpx.HTTPError:
                    log.warning("could not remove superseded %s", previous)
            self._tree_fetched = 0.0
            return 0
        except httpx.HTTPError as exc:
            failed = True
            log.error("upload of %s failed: %s", ipath, exc)
            raise FuseOSError(errno.EIO)
        finally:
            self._inflight.pop(ipath, None)
            settled.set()
            if entry.file_id:
                self._hold_pending(entry.file_id, Path(handle.name))
            else:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
            self.stats.record("release", (time.perf_counter() - start) * 1000, failed)

    def unlink(self, path):
        ipath = _internal(path)
        if _is_apple_double(ipath):
            self._apple.pop(ipath, None)
            return 0
        entry = self._by_path.get(ipath)
        if entry is None or entry.is_dir:
            raise FuseOSError(errno.ENOENT)
        self._settle(ipath)
        try:
            if entry.file_id:
                self.client.delete(entry.file_id)
        except httpx.HTTPError as exc:
            log.error("unlink %s failed: %s", ipath, exc)
            raise FuseOSError(errno.EIO)
        self._invalidate(entry.file_id)
        self._forget(ipath)
        self._tree_fetched = 0.0
        return 0

    def mkdir(self, path, mode):
        ipath = _internal(path)
        if ipath in self._by_path:
            raise FuseOSError(errno.EEXIST)
        try:
            self.client.mkdir(ipath)
        except httpx.HTTPError as exc:
            log.error("mkdir %s failed: %s", ipath, exc)
            raise FuseOSError(errno.EIO)
        self._intern(ipath, True)
        self._tree_fetched = 0.0
        return 0

    def rmdir(self, path):
        ipath = _internal(path)
        entry = self._by_path.get(ipath)
        if entry is None or not entry.is_dir:
            raise FuseOSError(errno.ENOENT)
        if self._children(ipath):
            raise FuseOSError(errno.ENOTEMPTY)
        try:
            self.client.rmdir(ipath)
        except httpx.HTTPError as exc:
            log.error("rmdir %s failed: %s", ipath, exc)
            raise FuseOSError(errno.EIO)
        self._forget(ipath)
        self._tree_fetched = 0.0
        return 0

    def symlink(self, target, source):
        """Create symlink `target` pointing at `source` (fusepy arg order)."""
        ipath = _internal(target)
        if ipath in self._by_path:
            raise FuseOSError(errno.EEXIST)
        folder, name = _split(ipath)
        try:
            result = self.client.upload(name, folder, source.encode("utf-8"), source)
        except httpx.HTTPError as exc:
            log.error("symlink %s -> %s failed: %s", ipath, source, exc)
            raise FuseOSError(errno.EIO)
        self._intern(ipath, False, result.get("id", ""),
                     len(source.encode("utf-8")), 0.0, source)
        self._tree_fetched = 0.0
        return 0

    def readlink(self, path):
        entry = self._entry(_internal(path))
        if not entry.symlink:
            raise FuseOSError(errno.EINVAL)
        return entry.symlink

    def rename(self, old, new):
        old_path = _internal(old)
        new_path = _internal(new)
        entry = self._by_path.get(old_path)
        if entry is None:
            raise FuseOSError(errno.ENOENT)
        self._settle(old_path)

        if entry.is_dir:
            self._refresh(True)
            moved = [child for child in list(self._by_path.values())
                     if child.path == old_path or child.path.startswith(f"{old_path}/")]
            try:
                self.client.mkdir(new_path)
                for child in moved:
                    if child.is_dir or not child.file_id:
                        continue
                    suffix = child.path[len(old_path):].lstrip("/")
                    target_folder = str(PurePosixPath(f"{new_path}/{suffix}").parent)
                    target_folder = "" if target_folder == "." else target_folder
                    self.client.patch(child.file_id, {"folder": target_folder})
                self.client.rmdir(old_path)
            except httpx.HTTPError as exc:
                log.error("rename %s -> %s failed: %s", old_path, new_path, exc)
                raise FuseOSError(errno.EIO)
        else:
            existing = self._by_path.get(new_path)
            folder, name = _split(new_path)
            try:
                if existing is not None and not existing.is_dir and existing.file_id:
                    # POSIX rename replaces the destination silently.
                    self.client.delete(existing.file_id)
                    self._forget(new_path)
                self.client.patch(entry.file_id, {"name": name, "folder": folder})
            except httpx.HTTPError as exc:
                log.error("rename %s -> %s failed: %s", old_path, new_path, exc)
                raise FuseOSError(errno.EIO)

        self._forget(old_path)
        self._tree_fetched = 0.0
        self._refresh(True)
        return 0

    def statfs(self, path):
        try:
            overview = self.client.stats()
            collective = overview.get("collective", {})
            total = int(collective.get("quota_bytes") or 0)
            used = int(collective.get("used_bytes") or 0)
        except (httpx.HTTPError, ValueError):
            total = used = 0
        blocks = max(total // BLOCK_SIZE, 1)
        bfree = max((total - used) // BLOCK_SIZE, 0)
        return {
            "f_bsize": BLOCK_SIZE,
            "f_frsize": BLOCK_SIZE,
            "f_blocks": blocks,
            "f_bfree": bfree,
            "f_bavail": bfree,
            "f_files": len([e for e in self._by_path.values() if not e.is_dir]),
            "f_ffree": 1 << 20,
            "f_favail": 1 << 20,
            "f_namemax": 255,
        }

    # POSIX bookkeeping the store does not model: accept and ignore, so tools
    # that chmod/chown/touch/setxattr do not fail the whole operation.
    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def utimens(self, path, times=None):
        entry = self._by_path.get(_internal(path))
        if entry is not None and times:
            entry.mtime = times[1]
        return 0

    def access(self, path, amode):
        return 0

    def getxattr(self, path, name, position=0):
        store = self._xattrs.get(_internal(path))
        if store is not None and name in store:
            return store[name]
        raise FuseOSError(ENOATTR)

    def setxattr(self, path, name, value, options, position=0):
        self._xattrs.setdefault(_internal(path), {})[name] = value
        return 0

    def removexattr(self, path, name):
        store = self._xattrs.get(_internal(path))
        if not store or name not in store:
            raise FuseOSError(ENOATTR)
        del store[name]
        return 0

    def listxattr(self, path):
        return list(self._xattrs.get(_internal(path), {}).keys())


def _report_loop(fs: CollectiveFSMac, mountpoint: str, node_label: str, interval: float):
    """Push performance counters to the node so the UI can chart them."""
    while True:
        time.sleep(interval)
        payload = fs.stats.drain()
        payload["mountpoint"] = mountpoint
        payload["node"] = node_label
        payload["files"] = len([e for e in fs._by_path.values() if not e.is_dir])
        payload["cache_entries"] = len(fs._read_cache)
        try:
            fs.client.report(payload)
        except Exception as exc:  # never let telemetry kill the mount
            log.debug("metrics report failed: %s", exc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Mount a CollectiveFS account as a directory (macOS / fuse-t).")
    parser.add_argument("mountpoint", help="where to mount, e.g. ~/cfs/.cfs")
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
    parser.add_argument("--volname", default="collectivefs", help="volume name in Finder")
    parser.add_argument("--threads", action="store_true",
                        help="allow multithreaded FUSE (default: single-threaded)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    mountpoint = os.path.abspath(os.path.expanduser(args.mountpoint))

    token = args.token.strip()
    if not token:
        try:
            with httpx.Client(base_url=args.api, timeout=15.0) as probe:
                token = probe.get("/api/account").json()["token"]
            log.info("using this node's default account token")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("no --token given and the node could not supply one: %s", exc)
            return 2

    os.makedirs(mountpoint, exist_ok=True)
    client = NodeClient(args.api, token)
    try:
        client.tree()
    except httpx.HTTPError as exc:
        log.error("cannot reach the node at %s: %s", args.api, exc)
        return 2

    fs = CollectiveFSMac(client, ttl=args.ttl)
    reporter = threading.Thread(
        target=_report_loop,
        args=(fs, mountpoint, os.uname().nodename, args.metrics_interval),
        daemon=True,
    )
    reporter.start()

    log.info("mounting %s from %s (fuse-t: %s)", mountpoint, args.api,
             os.environ.get("FUSE_LIBRARY_PATH"))
    # fuse-t honours the standard macOS mount options. noappledouble/noapplexattr
    # keep Finder's ._* and xattr chatter off the erasure-coded store.
    fuse_opts = dict(
        foreground=True,
        nothreads=not args.threads,
        allow_other=args.allow_other,
        fsname="collectivefs",
        volname=args.volname,
        noappledouble=True,
        noapplexattr=True,
    )
    try:
        FUSE(fs, mountpoint, **fuse_opts)
    except RuntimeError as exc:
        # fusepy raises a bare RuntimeError('1') when the FUSE mount fails.
        log.error("mount failed (%s). Is fuse-t installed and the node reachable?", exc)
        return 1
    finally:
        client.close()
        log.info("unmounted %s", mountpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
