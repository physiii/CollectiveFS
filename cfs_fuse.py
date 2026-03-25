#!/usr/bin/env python3
"""
cfs_fuse.py - FUSE filesystem module for CollectiveFS.

Mounts CollectiveFS as a native filesystem using pyfuse3 (libfuse3 bindings).
Files stored in the CollectiveFS pipeline (Reed-Solomon encoded, Fernet encrypted,
WebRTC distributed) appear as ordinary files under the mount point.

Install requirements:
    pip install pyfuse3    (also needs libfuse3-dev on Linux)
"""

import os
import sys
import stat
import time
import json
import errno
import logging
import argparse
import tempfile
import threading
import subprocess
import glob as _glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import pyfuse3
    import pyfuse3_asyncio
    FUSE_AVAILABLE = True
except ImportError:
    FUSE_AVAILABLE = False

    # Minimal stub so the class body can be defined and imported for unit tests
    # without needing the real pyfuse3 installed.
    class pyfuse3:  # type: ignore[no-redef]
        ROOT_INODE = 1

        FUSEError = OSError

        class EntryAttributes:
            """Stub for pyfuse3.EntryAttributes."""
            def __init__(self):
                self.st_ino = 0
                self.entry_timeout = 300.0
                self.attr_timeout = 300.0
                self.st_mode = 0
                self.st_nlink = 1
                self.st_uid = os.getuid()
                self.st_gid = os.getgid()
                self.st_rdev = 0
                self.st_size = 0
                self.st_blksize = 4096
                self.st_blocks = 0
                self.st_atime_ns = 0
                self.st_mtime_ns = 0
                self.st_ctime_ns = 0

        class Operations:
            """Stub base class."""
            pass

        @staticmethod
        def main(workers=None):
            raise RuntimeError("pyfuse3 is not installed; cannot mount filesystem.")

        @staticmethod
        def init(ops, mountpoint, options):
            raise RuntimeError("pyfuse3 is not installed; cannot mount filesystem.")

        @staticmethod
        def close(unmount=True):
            pass

        class RequestContext:
            uid = os.getuid()
            gid = os.getgid()
            pid = os.getpid()
            umask = 0o022

    class pyfuse3_asyncio:  # type: ignore[no-redef]
        @staticmethod
        def enable():
            pass


__all__ = ["CFSFilesystem"]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT_INODE = 1
_INODE_FIRST_FILE = 2        # file inodes start here
_DEFAULT_COLLECTIVE = os.path.expanduser("~/.collective")
_TREE_SUBDIR = "tree"
_CACHE_SUBDIR = "cache"
_PROC_SUBDIR = "proc"
_REFRESH_INTERVAL = 30       # seconds between inode-table refreshes


# ---------------------------------------------------------------------------
# Helper dataclass-like container
# ---------------------------------------------------------------------------

class _FileEntry:
    """In-memory representation of a single file visible in the mount."""

    __slots__ = (
        "inode", "name", "file_id", "size", "mtime_ns",
        "status", "folder", "chunks", "json_path",
    )

    def __init__(
        self,
        inode: int,
        name: str,
        file_id: str,
        size: int,
        mtime_ns: int,
        status: str,
        folder: Optional[str],
        chunks: int,
        json_path: str,
    ):
        self.inode = inode
        self.name = name
        self.file_id = file_id
        self.size = size
        self.mtime_ns = mtime_ns
        self.status = status
        self.folder = folder
        self.chunks = chunks
        self.json_path = json_path


# ---------------------------------------------------------------------------
# Main FUSE filesystem class
# ---------------------------------------------------------------------------

class CFSFilesystem(pyfuse3.Operations):
    """
    FUSE filesystem that exposes CollectiveFS files as ordinary files.

    Virtual tree layout:
        /                    ← inode 1 (root directory)
        /<filename>          ← inode ≥ 2 (one per file in tree metadata)

    On read:  decoder subprocess reconstructs the file into the cache dir.
    On write: bytes are buffered in a temp file; on release() the encoder
              subprocess runs to encode/encrypt/distribute the file.
    """

    def __init__(
        self,
        collective_path: str = _DEFAULT_COLLECTIVE,
        encoder_path: str = "lib/encoder",
        decoder_path: str = "lib/decoder",
        program_path: Optional[str] = None,
    ):
        super().__init__()

        self._collective = Path(collective_path)
        self._tree_dir = self._collective / _TREE_SUBDIR
        self._cache_dir = self._collective / _CACHE_SUBDIR
        self._proc_dir = self._collective / _PROC_SUBDIR

        # Resolve encoder/decoder relative to the program directory when not
        # absolute paths are given.
        _prog = Path(program_path) if program_path else Path(__file__).parent
        self._encoder = (
            Path(encoder_path) if Path(encoder_path).is_absolute()
            else _prog / encoder_path
        )
        self._decoder = (
            Path(decoder_path) if Path(decoder_path).is_absolute()
            else _prog / decoder_path
        )

        # inode → _FileEntry
        self._inode_map: Dict[int, _FileEntry] = {}
        # name → inode (for fast lookup by name)
        self._name_map: Dict[bytes, int] = {}
        # next free inode number
        self._next_inode: int = _INODE_FIRST_FILE
        # lock for the inode table
        self._inode_lock = threading.Lock()

        # Write buffers: inode → (NamedTemporaryFile, filename)
        self._write_bufs: Dict[int, Tuple[object, str]] = {}

        # Read cache: inode → bytes (reconstructed file contents)
        self._read_cache: Dict[int, bytes] = {}

        # Timestamp of last metadata refresh
        self._last_refresh: float = 0.0

        # Ensure directories exist
        for d in (self._tree_dir, self._cache_dir, self._proc_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Initial population of the inode table
        self._refresh_inodes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_inodes(self) -> None:
        """Re-read ~/.collective/tree/*.json and rebuild the inode table."""
        now = time.time()
        if now - self._last_refresh < _REFRESH_INTERVAL:
            return
        self._last_refresh = now

        new_inode_map: Dict[int, _FileEntry] = {}
        new_name_map: Dict[bytes, int] = {}

        json_files = sorted(self._tree_dir.glob("*.json"))
        for json_path in json_files:
            try:
                with open(json_path, "r") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not read metadata %s: %s", json_path, exc)
                continue

            name: str = meta.get("name", json_path.stem)
            file_id: str = meta.get("id", json_path.stem)
            size: int = int(meta.get("size", 0))
            status: str = meta.get("status", "stored")
            folder: Optional[str] = meta.get("folder")
            chunks: int = int(meta.get("number_of_chunks", meta.get("chunks", 0)))

            mtime_ns: int
            try:
                mtime_ns = int(json_path.stat().st_mtime * 1e9)
            except OSError:
                mtime_ns = int(now * 1e9)

            name_bytes = name.encode("utf-8", errors="replace")

            with self._inode_lock:
                # Re-use existing inode if same file_id is already known
                existing_inode: Optional[int] = None
                for ino, entry in self._inode_map.items():
                    if entry.file_id == file_id:
                        existing_inode = ino
                        break
                if existing_inode is None:
                    existing_inode = self._next_inode
                    self._next_inode += 1

            entry = _FileEntry(
                inode=existing_inode,
                name=name,
                file_id=file_id,
                size=size,
                mtime_ns=mtime_ns,
                status=status,
                folder=folder,
                chunks=chunks,
                json_path=str(json_path),
            )
            new_inode_map[existing_inode] = entry
            new_name_map[name_bytes] = existing_inode

        with self._inode_lock:
            self._inode_map = new_inode_map
            self._name_map = new_name_map

        log.debug("Refreshed inode table: %d files", len(new_inode_map))

    def _make_entry_attr(self, inode: int) -> "pyfuse3.EntryAttributes":
        """Build a pyfuse3.EntryAttributes for the given inode."""
        attr = pyfuse3.EntryAttributes()
        attr.st_ino = inode
        attr.entry_timeout = 30.0
        attr.attr_timeout = 30.0

        now_ns = int(time.time() * 1e9)

        if inode == ROOT_INODE:
            attr.st_mode = stat.S_IFDIR | 0o755
            attr.st_nlink = 2
            attr.st_size = 0
            attr.st_atime_ns = now_ns
            attr.st_mtime_ns = now_ns
            attr.st_ctime_ns = now_ns
        else:
            entry = self._inode_map.get(inode)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            attr.st_mode = stat.S_IFREG | 0o644
            attr.st_nlink = 1
            attr.st_size = entry.size
            attr.st_atime_ns = entry.mtime_ns
            attr.st_mtime_ns = entry.mtime_ns
            attr.st_ctime_ns = entry.mtime_ns

        attr.st_uid = os.getuid()
        attr.st_gid = os.getgid()
        attr.st_rdev = 0
        attr.st_blksize = 4096
        attr.st_blocks = max(1, (attr.st_size + 511) // 512)
        return attr

    def _reconstruct_file(self, entry: _FileEntry) -> bytes:
        """
        Run the decoder subprocess to reconstruct file bytes from chunks.

        Caches the result in self._read_cache[entry.inode].
        """
        cached = self._read_cache.get(entry.inode)
        if cached is not None:
            return cached

        out_path = self._cache_dir / entry.file_id
        if not out_path.exists():
            if not self._decoder.exists():
                log.error("Decoder binary not found: %s", self._decoder)
                raise pyfuse3.FUSEError(errno.EIO)

            cmd = [
                str(self._decoder),
                "--id", entry.file_id,
                "--out", str(out_path),
            ]
            if entry.folder:
                cmd += ["--folder", entry.folder]

            log.debug("Running decoder: %s", cmd)
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                log.error(
                    "Decoder failed (exit %d): %s",
                    result.returncode,
                    result.stderr.decode(errors="replace"),
                )
                raise pyfuse3.FUSEError(errno.EIO)

        try:
            data = out_path.read_bytes()
        except OSError as exc:
            log.error("Cannot read reconstructed file %s: %s", out_path, exc)
            raise pyfuse3.FUSEError(errno.EIO)

        self._read_cache[entry.inode] = data
        return data

    def _run_encoder(self, file_path: Path, name: str) -> None:
        """
        Run the encoder subprocess on a newly written file.

        Creates metadata JSON in the tree directory so the file becomes
        visible on the next readdir/refresh.
        """
        if not self._encoder.exists():
            log.error("Encoder binary not found: %s", self._encoder)
            return

        import uuid as _uuid
        file_id = str(_uuid.uuid4())
        out_folder = self._proc_dir / (name + ".d")
        out_folder.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self._encoder),
            "--data", "8",
            "--par", "4",
            "--out", str(out_folder),
            str(file_path),
        ]
        log.debug("Running encoder: %s", cmd)
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            log.error(
                "Encoder failed (exit %d): %s",
                result.returncode,
                result.stderr.decode(errors="replace"),
            )
            return

        size = file_path.stat().st_size
        meta = {
            "id": file_id,
            "name": name,
            "size": size,
            "folder": str(out_folder),
            "number_of_chunks": 12,
            "chunks": [],
            "status": "stored",
        }
        meta_path = self._tree_dir / f"{file_id}.json"
        with open(meta_path, "w") as fh:
            json.dump(meta, fh, indent=2)

        log.info("Encoded and stored file '%s' as %s", name, file_id)
        # Force a refresh on the next readdir
        self._last_refresh = 0.0

    # ------------------------------------------------------------------
    # pyfuse3.Operations interface
    # ------------------------------------------------------------------

    async def getattr(self, inode: int, ctx=None) -> "pyfuse3.EntryAttributes":
        """Return file attributes for the given inode."""
        if inode == ROOT_INODE:
            return self._make_entry_attr(ROOT_INODE)

        self._refresh_inodes()
        with self._inode_lock:
            if inode not in self._inode_map:
                raise pyfuse3.FUSEError(errno.ENOENT)
        return self._make_entry_attr(inode)

    async def lookup(
        self, parent_inode: int, name: bytes, ctx=None
    ) -> "pyfuse3.EntryAttributes":
        """Look up a name in the virtual directory and return its attributes."""
        if parent_inode != ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)

        self._refresh_inodes()
        with self._inode_lock:
            inode = self._name_map.get(name)
        if inode is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._make_entry_attr(inode)

    async def readdir(self, inode: int, start_id: int, token) -> None:
        """
        Enumerate directory entries.

        Yields `.` and `..` for the root, then all known file entries.
        """
        if inode != ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOTDIR)

        self._last_refresh = 0.0  # force refresh on every readdir
        self._refresh_inodes()

        entries: List[Tuple[int, bytes, "pyfuse3.EntryAttributes"]] = []

        if start_id == 0:
            entries.append((ROOT_INODE, b".", self._make_entry_attr(ROOT_INODE)))
            entries.append((ROOT_INODE, b"..", self._make_entry_attr(ROOT_INODE)))

        with self._inode_lock:
            file_inodes = sorted(self._inode_map.keys())

        for ino in file_inodes:
            if ino <= start_id:
                continue
            with self._inode_lock:
                entry = self._inode_map.get(ino)
            if entry is None:
                continue
            name_bytes = entry.name.encode("utf-8", errors="replace")
            attr = self._make_entry_attr(ino)
            if not pyfuse3.readdir_reply(token, name_bytes, attr, ino):
                break

    async def open(
        self, inode: int, flags: int, ctx=None
    ) -> "pyfuse3.FileInfo":
        """
        Open a file.

        For read-only opens, trigger reconstruction via the decoder subprocess.
        For write opens, prepare a temporary buffer.
        """
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC
        is_write = bool(flags & write_flags)

        with self._inode_lock:
            entry = self._inode_map.get(inode)

        if entry is None and not is_write:
            raise pyfuse3.FUSEError(errno.ENOENT)

        if not is_write and entry is not None:
            try:
                self._reconstruct_file(entry)
            except pyfuse3.FUSEError:
                raise
            except Exception as exc:
                log.error("Unexpected error reconstructing inode %d: %s", inode, exc)
                raise pyfuse3.FUSEError(errno.EIO)

        fi = pyfuse3.FileInfo(fh=inode)
        return fi

    async def read(self, inode: int, off: int, size: int) -> bytes:
        """Return bytes from the reconstructed file."""
        cached = self._read_cache.get(inode)
        if cached is None:
            with self._inode_lock:
                entry = self._inode_map.get(inode)
            if entry is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            cached = self._reconstruct_file(entry)
        return cached[off : off + size]

    async def create(
        self,
        parent_inode: int,
        name: bytes,
        mode: int,
        flags: int,
        ctx=None,
    ) -> Tuple["pyfuse3.FileInfo", "pyfuse3.EntryAttributes"]:
        """
        Create a new file.

        Allocates a new inode and a temporary write buffer. The actual
        encode/encrypt pipeline is triggered when the file is released.
        """
        if parent_inode != ROOT_INODE:
            raise pyfuse3.FUSEError(errno.EPERM)

        file_name = name.decode("utf-8", errors="replace")

        with self._inode_lock:
            inode = self._next_inode
            self._next_inode += 1

        # Create a placeholder entry (size 0, status "writing")
        entry = _FileEntry(
            inode=inode,
            name=file_name,
            file_id="",           # will be assigned by encoder
            size=0,
            mtime_ns=int(time.time() * 1e9),
            status="writing",
            folder=None,
            chunks=0,
            json_path="",
        )
        with self._inode_lock:
            self._inode_map[inode] = entry
            self._name_map[name] = inode

        # Open a temporary file for buffering writes
        tmp = tempfile.NamedTemporaryFile(
            dir=str(self._proc_dir),
            prefix=f"cfs_write_{inode}_",
            delete=False,
        )
        self._write_bufs[inode] = (tmp, file_name)

        fi = pyfuse3.FileInfo(fh=inode)
        attr = self._make_entry_attr(inode)
        return fi, attr

    async def write(self, inode: int, off: int, buf: bytes) -> int:
        """Write bytes to the temporary buffer for a file being created."""
        buf_entry = self._write_bufs.get(inode)
        if buf_entry is None:
            raise pyfuse3.FUSEError(errno.EBADF)

        tmp_file, _ = buf_entry
        tmp_file.seek(off)
        written = tmp_file.write(buf)
        tmp_file.flush()

        # Update the size in the inode entry
        with self._inode_lock:
            entry = self._inode_map.get(inode)
            if entry is not None:
                entry.size = max(entry.size, off + written)

        return written

    async def release(self, inode: int) -> None:
        """
        Finalize a write.

        Flushes the temporary buffer, then triggers the encode/encrypt
        pipeline via the encoder subprocess in a background thread.
        """
        buf_entry = self._write_bufs.pop(inode, None)
        if buf_entry is None:
            # Read-only open; nothing to do
            return

        tmp_file, file_name = buf_entry
        tmp_path = Path(tmp_file.name)
        tmp_file.flush()
        tmp_file.close()

        log.debug("Releasing write buffer for inode %d (%s)", inode, file_name)

        # Run encoder in a background thread to avoid blocking FUSE
        def _encode():
            try:
                self._run_encoder(tmp_path, file_name)
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        t = threading.Thread(target=_encode, daemon=True, name=f"cfs-encode-{inode}")
        t.start()

    async def unlink(self, parent_inode: int, name: bytes, ctx=None) -> None:
        """
        Delete a file from CollectiveFS.

        Removes the metadata JSON from the tree directory.  The actual
        chunk files are left in place (garbage-collection is out of scope
        for the FUSE layer).
        """
        if parent_inode != ROOT_INODE:
            raise pyfuse3.FUSEError(errno.EACCES)

        self._refresh_inodes()
        with self._inode_lock:
            inode = self._name_map.get(name)
        if inode is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        with self._inode_lock:
            entry = self._inode_map.pop(inode, None)
            self._name_map.pop(name, None)

        if entry and entry.json_path:
            try:
                Path(entry.json_path).unlink()
                log.info("Deleted metadata for '%s' (%s)", entry.name, entry.file_id)
            except OSError as exc:
                log.warning("Could not remove metadata %s: %s", entry.json_path, exc)

        # Evict from read cache
        self._read_cache.pop(inode, None)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """
    Parse command-line arguments and mount the CollectiveFS FUSE filesystem.

    Usage:
        python cfs_fuse.py <mountpoint> [options]
    """
    parser = argparse.ArgumentParser(
        description="Mount CollectiveFS as a FUSE filesystem",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "mountpoint",
        help="Directory where the filesystem will be mounted",
    )
    parser.add_argument(
        "--collective-path",
        default=_DEFAULT_COLLECTIVE,
        help="Path to the .collective metadata directory",
    )
    parser.add_argument(
        "--encoder",
        default="lib/encoder",
        help="Path to the encoder binary (absolute or relative to this script)",
    )
    parser.add_argument(
        "--decoder",
        default="lib/decoder",
        help="Path to the decoder binary (absolute or relative to this script)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable verbose debug logging",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker threads for pyfuse3",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not FUSE_AVAILABLE:
        print(
            "ERROR: pyfuse3 is not installed.\n"
            "Install it with:\n\n"
            "    pip install pyfuse3\n\n"
            "You may also need the system package for libfuse3:\n\n"
            "    # Debian/Ubuntu:\n"
            "    sudo apt install libfuse3-dev fuse3\n\n"
            "    # Fedora/RHEL:\n"
            "    sudo dnf install fuse3-devel\n",
            file=sys.stderr,
        )
        return 1

    mountpoint = args.mountpoint
    if not os.path.isdir(mountpoint):
        print(f"ERROR: mountpoint '{mountpoint}' does not exist or is not a directory.", file=sys.stderr)
        return 1

    pyfuse3_asyncio.enable()

    ops = CFSFilesystem(
        collective_path=args.collective_path,
        encoder_path=args.encoder,
        decoder_path=args.decoder,
        program_path=os.path.dirname(os.path.abspath(__file__)),
    )

    fuse_options = set(pyfuse3.default_options)  # type: ignore[attr-defined]
    fuse_options.add("fsname=collectivefs")
    if args.debug:
        fuse_options.add("debug")

    pyfuse3.init(ops, mountpoint, fuse_options)
    log.info("CollectiveFS mounted at %s", mountpoint)

    try:
        import trio
        trio.run(pyfuse3.main, args.workers)
    except KeyboardInterrupt:
        log.info("Unmounting CollectiveFS…")
    finally:
        pyfuse3.close(unmount=True)
        log.info("CollectiveFS unmounted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
