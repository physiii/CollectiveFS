#!/usr/bin/env python3
"""cfs_mount.py — mount a CollectiveFS node as a read-only network filesystem.

Files in the collective live as encrypted, sharded chunks; they only become
bytes when the node reconstructs them on demand. This exposes a node's
namespace as an ordinary directory tree over FUSE, so `ls` lists files, `stat`
reports real sizes, and *any* program that opens a file — Nautilus, Totem, VLC,
mpv, `cat` — reads it live. Each read is translated into an HTTP Range request
against `/api/files/{id}/download`, so a video fast-starts and seeks without
ever downloading the whole thing: playback streams straight from the mesh, the
same way it would from an NFS/SMB share.

The node to mount is resolved with zero configuration where possible:
  1. an explicit --node / $CFS_NODE / config `node_url`, else
  2. mDNS/DNS-SD discovery of `_collectivefs._tcp` on the LAN, else
  3. the `discovery.seeds` in the config file, else
  4. http://localhost:8010
Every candidate is health-checked (`GET /api/health`) and the first live one
wins. Config lives at $CFS_CONFIG or ~/.config/collectivefs/config.json.

Usage:
    cfs_mount.py <mountpoint> [--node URL] [--config PATH] [--foreground]

Read-only by design — writes return EROFS.
"""
import argparse
import errno
import json
import os
import socket
import stat
import threading
import time
from collections import OrderedDict
from datetime import datetime
from urllib import error as urlerror
from urllib import request as urlrequest

from fuse import FUSE, FuseOSError, Operations

BLOCK = 1 << 20          # 1 MiB cache granularity
READAHEAD = 4            # blocks fetched per miss (coalesced into one request)
CACHE_BLOCKS = 192       # LRU cap (~192 MiB of hot media across all files)
LIST_TTL = 5.0           # seconds before the namespace is re-fetched
TOKEN_HEADER = "X-CFS-Token"
SERVICE_TYPE = "_collectivefs._tcp.local."

CONFIG_PATH = os.environ.get(
    "CFS_CONFIG", os.path.expanduser("~/.config/collectivefs/config.json")
)
DEFAULT_CONFIG = {
    "node_url": None,                 # explicit override; null => discover
    "mount_point": "~/CollectiveFS",
    "discovery": {
        "mdns": True,                 # browse _collectivefs._tcp on the LAN
        "mdns_timeout": 3.0,
        "seeds": [],                  # fallback base URLs, tried in order
    },
}


# ---------------------------------------------------------------------------
# Config + node discovery
# ---------------------------------------------------------------------------
def load_config(path=CONFIG_PATH):
    """Merge a JSON config file over the defaults (missing file => defaults)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        with open(path) as fh:
            user = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return cfg
    for key, val in user.items():
        if key == "discovery" and isinstance(val, dict):
            cfg["discovery"].update(val)
        else:
            cfg[key] = val
    return cfg


def _health_ok(url, timeout=3.0):
    try:
        with urlrequest.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except (urlerror.URLError, OSError, ValueError):
        return False


def _discover_mdns(timeout=3.0):
    """Browse the LAN for `_collectivefs._tcp` services; return their URLs.

    Zero-config bootstrap: a node advertises itself over mDNS, so a fresh
    client with no seed still finds one. (Returns [] when zeroconf is missing
    or the network — e.g. a NAT'd VM — does not carry multicast; the seed list
    covers that case.)
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found = {}

    class _Listener:
        def _record(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=int(timeout * 1000))
            if not info:
                return
            props = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in (info.properties or {}).items()
            }
            url = props.get("url")
            if not url and info.addresses:
                url = f"http://{socket.inet_ntoa(info.addresses[0])}:{info.port}"
            if url:
                found[url.rstrip("/")] = props.get("node_id")

        add_service = _record
        update_service = _record

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE_TYPE, _Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return list(found.keys())


def resolve_node(explicit, config):
    """Return the base URL of the first reachable node, or raise."""
    disc = config.get("discovery", {})
    candidates = []
    if explicit:
        candidates.append(explicit)
    if config.get("node_url"):
        candidates.append(config["node_url"])
    if disc.get("mdns", True):
        candidates += _discover_mdns(float(disc.get("mdns_timeout", 3.0)))
    candidates += list(disc.get("seeds", []))
    candidates.append("http://localhost:8010")

    seen, ordered = set(), []
    for c in candidates:
        c = (c or "").rstrip("/")
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    for url in ordered:
        if _health_ok(url):
            return url
    raise SystemExit(
        "cfs_mount: no reachable CollectiveFS node (tried: "
        + ", ".join(ordered) + ")"
    )


class CFSMount(Operations):
    def __init__(self, node, token=None):
        self.node = node.rstrip("/")
        self.token = token
        self._lock = threading.Lock()
        self._cache = OrderedDict()      # (file_id, block_index) -> bytes
        self._files = {}                 # "dir/name.ext" -> {id, size, mtime}
        self._dirs = set()               # relative directory paths
        self._listed_at = 0.0
        self._refresh(force=True)

    # ---- namespace ---------------------------------------------------------
    def _http(self, path, headers=None):
        req = urlrequest.Request(self.node + path, headers=headers or {})
        if self.token:
            req.add_header(TOKEN_HEADER, self.token)
        return urlrequest.urlopen(req, timeout=60)

    def _refresh(self, force=False):
        now = time.time()
        if not force and (now - self._listed_at) < LIST_TTL:
            return
        try:
            raw = self._http("/api/files").read()
            listing = json.loads(raw)
        except (urlerror.URLError, ValueError, OSError):
            self._listed_at = now
            return
        files, dirs = {}, set()
        for f in listing:
            fid = f.get("id")
            name = (f.get("name") or "").strip().strip("/")
            if not fid or not name:
                continue
            folder = (f.get("folder") or "").strip().strip("/")
            rel = f"{folder}/{name}" if folder else name
            # register every intermediate directory
            parts = rel.split("/")[:-1]
            acc = ""
            for p in parts:
                acc = f"{acc}/{p}" if acc else p
                dirs.add(acc)
            files[rel] = {
                "id": fid,
                "size": int(f.get("size") or 0),
                "mtime": _parse_ts(f.get("created_at")),
            }
        with self._lock:
            self._files, self._dirs, self._listed_at = files, dirs, now

    def _lookup(self, path):
        rel = path.lstrip("/")
        if rel == "":
            return "dir", None
        if rel in self._dirs:
            return "dir", None
        info = self._files.get(rel)
        if info:
            return "file", info
        return None, None

    # ---- FUSE ops ----------------------------------------------------------
    def getattr(self, path, fh=None):
        self._refresh()
        kind, info = self._lookup(path)
        if kind is None:
            raise FuseOSError(errno.ENOENT)
        if kind == "dir":
            return {
                "st_mode": stat.S_IFDIR | 0o555,
                "st_nlink": 2,
                "st_size": 0,
                "st_mtime": self._listed_at,
                "st_ctime": self._listed_at,
                "st_atime": self._listed_at,
            }
        return {
            "st_mode": stat.S_IFREG | 0o444,
            "st_nlink": 1,
            "st_size": info["size"],
            "st_mtime": info["mtime"],
            "st_ctime": info["mtime"],
            "st_atime": info["mtime"],
        }

    def readdir(self, path, fh):
        self._refresh()
        rel = path.lstrip("/")
        prefix = f"{rel}/" if rel else ""
        seen = set()
        yield "."
        yield ".."
        with self._lock:
            names = list(self._files.keys()) + list(self._dirs)
        for entry in names:
            if prefix and not entry.startswith(prefix):
                continue
            if not prefix and "/" in entry:
                child = entry.split("/", 1)[0]
            else:
                child = entry[len(prefix):].split("/", 1)[0]
            if child and child not in seen:
                seen.add(child)
                yield child

    def open(self, path, flags):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT):
            raise FuseOSError(errno.EROFS)
        kind, _ = self._lookup(path)
        if kind != "file":
            raise FuseOSError(errno.ENOENT)
        return 0

    def read(self, path, size, offset, fh):
        kind, info = self._lookup(path)
        if kind != "file":
            raise FuseOSError(errno.ENOENT)
        fid, fsize = info["id"], info["size"]
        if offset >= fsize:
            return b""
        end = min(offset + size, fsize)          # exclusive
        out = bytearray()
        pos = offset
        while pos < end:
            blk = pos // BLOCK
            data = self._block(fid, blk, fsize)
            boff = pos - blk * BLOCK
            take = min(len(data) - boff, end - pos)
            if take <= 0:
                break
            out += data[boff:boff + take]
            pos += take
        return bytes(out)

    def statfs(self, path):
        with self._lock:
            total = sum(f["size"] for f in self._files.values())
        return {
            "f_bsize": BLOCK, "f_frsize": BLOCK,
            "f_blocks": max(1, total // BLOCK), "f_bfree": 0, "f_bavail": 0,
            "f_files": len(self._files), "f_ffree": 0, "f_namemax": 255,
        }

    # ---- block cache -------------------------------------------------------
    def _block(self, fid, blk, fsize):
        key = (fid, blk)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        start = blk * BLOCK
        last = min(fsize, (blk + READAHEAD) * BLOCK) - 1
        try:
            resp = self._http(
                f"/api/files/{fid}/download", headers={"Range": f"bytes={start}-{last}"}
            )
            data = resp.read()
            if getattr(resp, "status", 206) == 200:   # server ignored Range
                data = data[start:last + 1]
        except (urlerror.URLError, OSError):
            raise FuseOSError(errno.EIO)
        with self._lock:
            for i in range(READAHEAD):
                off = i * BLOCK
                if off >= len(data):
                    break
                self._cache[(fid, blk + i)] = data[off:off + BLOCK]
                self._cache.move_to_end((fid, blk + i))
            while len(self._cache) > CACHE_BLOCKS:
                self._cache.popitem(last=False)
            return self._cache.get(key, b"")


def _parse_ts(value):
    if not value:
        return time.time()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


def main():
    ap = argparse.ArgumentParser(description="Mount a CollectiveFS node over FUSE.")
    ap.add_argument("mountpoint", nargs="?")
    ap.add_argument("--node", default=os.environ.get("CFS_NODE"))
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--token", default=os.environ.get("CFS_TOKEN"))
    ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--allow-other", action="store_true")
    args = ap.parse_args()

    config = load_config(args.config)
    mountpoint = args.mountpoint or os.path.expanduser(
        config.get("mount_point") or "~/CollectiveFS"
    )
    mountpoint = os.path.expanduser(mountpoint)
    token = args.token or config.get("token")

    node = resolve_node(args.node, config)
    print(f"cfs_mount: node {node} -> {mountpoint}", flush=True)

    os.makedirs(mountpoint, exist_ok=True)
    FUSE(
        CFSMount(node, token),
        mountpoint,
        foreground=args.foreground,
        ro=True,
        allow_other=args.allow_other,
        nothreads=False,
        fsname="collectivefs",
        subtype="cfs",
    )


if __name__ == "__main__":
    main()
