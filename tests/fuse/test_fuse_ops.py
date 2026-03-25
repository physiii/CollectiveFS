"""
Tests for the CollectiveFS FUSE filesystem layer (cfs_fuse.py).

These tests exercise the CFSFilesystem class logic WITHOUT requiring an actual
kernel FUSE mount.  They call internal helper methods and inspect side-effects
on the temporary .collective directory tree, using monkeypatch to isolate the
class from real subprocess calls (encoder/decoder).

The cfs_fuse module is imported with a try/except guard — all tests in this
file are skipped gracefully if the import fails (e.g. in a minimal CI
environment that hasn't installed cfs_fuse's optional dependencies).

API notes (from cfs_fuse.py inspection):
  - CFSFilesystem.__init__ takes `collective_path`, not `root`
  - All pyfuse3 operation methods are async (getattr, readdir, write, unlink…)
  - _run_encoder() calls subprocess.run with the encoder binary
  - _reconstruct_file() calls subprocess.run with the decoder binary
"""

import json
import os
import pathlib
import subprocess
import threading
import pytest


# ---------------------------------------------------------------------------
# Conditional import — skip entire module if cfs_fuse is not importable
# ---------------------------------------------------------------------------

try:
    import sys
    sys.path.insert(0, "/home/andy/code/CollectiveFS")
    import cfs_fuse  # noqa: F401
    from cfs_fuse import CFSFilesystem
    FUSE_AVAILABLE = True
except (ImportError, Exception):
    FUSE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FUSE_AVAILABLE,
    reason="cfs_fuse module not importable — FUSE tests skipped",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def collective_dir(tmp_path):
    """
    Create a temporary .collective directory tree and return its path.
    """
    collective = tmp_path / ".collective"
    for subdir in ("proc", "cache", "public", "tree"):
        (collective / subdir).mkdir(parents=True)
    return collective


@pytest.fixture
def fuse_fs(collective_dir):
    """
    Return an initialised CFSFilesystem pointed at a temporary .collective
    directory.  The filesystem is NOT mounted — we call its methods directly.
    """
    return CFSFilesystem(
        collective_path=str(collective_dir),
        encoder_path="/home/andy/code/CollectiveFS/lib/encoder",
        decoder_path="/home/andy/code/CollectiveFS/lib/decoder",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meta(tree_dir, file_id, name, folder="/fake/folder"):
    """Write a minimal metadata JSON into tree_dir and return the path."""
    meta = {
        "id": file_id,
        "name": name,
        "size": 4096,
        "folder": folder,
        "number_of_chunks": 12,
        "chunks": [],
        "status": "stored",
    }
    p = pathlib.Path(tree_dir) / f"{file_id}.json"
    p.write_text(json.dumps(meta, indent=2))
    return p


class _FakeCompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = b""
        self.stderr = b""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.fuse
def test_fuse_write_triggers_encoding(fuse_fs, collective_dir, monkeypatch):
    """
    Calling _run_encoder() (the method invoked during release()) must call
    subprocess.run with a command that includes the encoder binary path.

    We monkeypatch subprocess.run so no actual encoding takes place; we inspect
    the call arguments to verify the encoder binary and -data/-par flags appear.
    """
    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = pathlib.Path(collective_dir) / "proc" / "hello.bin"
    src.write_bytes(os.urandom(4096))
    fuse_fs._run_encoder(src, "hello.bin")

    assert "cmd" in captured, "subprocess.run was not called — encoder was not invoked"
    cmd = captured["cmd"]
    assert any("encoder" in str(arg) for arg in cmd), (
        f"Encoder binary not found in subprocess.run call: {cmd}"
    )
    assert any("-data" in str(a) or "--data" in str(a) for a in cmd), (
        f"-data flag not found in encoder command: {cmd}"
    )
    assert any("-par" in str(a) or "--par" in str(a) for a in cmd), (
        f"-par flag not found in encoder command: {cmd}"
    )


@pytest.mark.fuse
def test_fuse_write_creates_metadata(fuse_fs, collective_dir, monkeypatch):
    """
    After _run_encoder() completes (with a mocked subprocess), a metadata
    JSON file must appear in .collective/tree/ with the correct name and
    number_of_chunks fields.
    """
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: _FakeCompletedProcess(returncode=0))

    src = pathlib.Path(collective_dir) / "proc" / "video.mp4"
    src.write_bytes(os.urandom(8192))
    fuse_fs._run_encoder(src, "video.mp4")

    tree_dir = pathlib.Path(collective_dir) / "tree"
    json_files = list(tree_dir.glob("*.json"))
    assert len(json_files) == 1, (
        f"Expected 1 metadata JSON in tree, found {len(json_files)}"
    )
    meta = json.loads(json_files[0].read_text())
    assert meta["name"] == "video.mp4", f"Wrong name in metadata: {meta['name']!r}"
    assert meta["number_of_chunks"] == 12, (
        f"Expected 12 chunks in metadata, got {meta['number_of_chunks']}"
    )


@pytest.mark.fuse
def test_fuse_read_triggers_reconstruction(fuse_fs, collective_dir, monkeypatch):
    """
    Calling _reconstruct_file() must invoke subprocess.run with the decoder
    binary and write output to the cache directory.

    We monkeypatch subprocess.run to write expected content to the output path,
    then verify the returned bytes match.
    """
    file_id = "read-test-id"
    original_data = b"hello from CollectiveFS" * 100
    tree_dir = pathlib.Path(collective_dir) / "tree"
    cache_dir = pathlib.Path(collective_dir) / "cache"
    _make_meta(tree_dir, file_id, "hello.bin")

    expected_out = cache_dir / file_id

    def fake_decode(cmd, *args, **kwargs):
        # Write the "reconstructed" content to where the decoder would write it
        expected_out.write_bytes(original_data)
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_decode)

    # Force an inode refresh so the filesystem sees the new metadata
    fuse_fs._last_refresh = 0.0
    fuse_fs._refresh_inodes()

    with fuse_fs._inode_lock:
        inodes = dict(fuse_fs._inode_map)
    assert inodes, "No inodes loaded — metadata not picked up"

    entry = next(iter(inodes.values()))
    result = fuse_fs._reconstruct_file(entry)
    assert result == original_data, "Reconstructed content does not match expected"


@pytest.mark.fuse
def test_fuse_listdir_shows_tree_files(fuse_fs, collective_dir):
    """
    After populating .collective/tree/ with two metadata files, the inode map
    must contain entries for both filenames, visible via readdir.
    """
    tree_dir = pathlib.Path(collective_dir) / "tree"
    _make_meta(tree_dir, "id-alpha", "alpha.txt")
    _make_meta(tree_dir, "id-beta", "beta.mp4")

    # Force a full refresh
    fuse_fs._last_refresh = 0.0
    fuse_fs._refresh_inodes()

    with fuse_fs._inode_lock:
        names = {e.name for e in fuse_fs._inode_map.values()}

    assert "alpha.txt" in names, f"alpha.txt not in inode map: {names}"
    assert "beta.mp4" in names, f"beta.mp4 not in inode map: {names}"


@pytest.mark.fuse
def test_fuse_write_large_file(fuse_fs, collective_dir, monkeypatch):
    """
    Calling _run_encoder() with a 2 MB file must invoke subprocess.run exactly
    once, regardless of file size.  The command must include the encoder binary.
    """
    captured_cmds = []

    def capture_run(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", capture_run)

    src = pathlib.Path(collective_dir) / "proc" / "bigfile.bin"
    src.write_bytes(os.urandom(2 * 1024 * 1024))
    fuse_fs._run_encoder(src, "bigfile.bin")

    assert len(captured_cmds) == 1, (
        f"Expected 1 subprocess.run call, got {len(captured_cmds)}"
    )
    cmd = captured_cmds[0]
    assert any("encoder" in str(arg) for arg in cmd), (
        f"Encoder binary not found in command: {cmd}"
    )


@pytest.mark.fuse
def test_fuse_delete_removes_metadata(fuse_fs, collective_dir):
    """
    Removing an entry from the inode map and deleting its JSON file (the
    sequence performed by unlink()) must leave the tree directory empty.
    """
    tree_dir = pathlib.Path(collective_dir) / "tree"
    file_id = "delete-test-id"
    meta_file = _make_meta(tree_dir, file_id, "hello.bin")

    # Load the entry
    fuse_fs._last_refresh = 0.0
    fuse_fs._refresh_inodes()

    with fuse_fs._inode_lock:
        inodes = dict(fuse_fs._inode_map)

    assert inodes, "Pre-condition: inode map should have an entry"
    assert meta_file.exists(), "Pre-condition: metadata file must exist"

    entry = next(iter(inodes.values()))

    # Replicate what unlink() does
    with fuse_fs._inode_lock:
        fuse_fs._inode_map.pop(entry.inode, None)
        fuse_fs._name_map.pop(entry.name.encode("utf-8"), None)
    pathlib.Path(entry.json_path).unlink()
    fuse_fs._read_cache.pop(entry.inode, None)

    assert not meta_file.exists(), (
        "Metadata JSON was not removed after simulated FUSE unlink"
    )
    with fuse_fs._inode_lock:
        assert entry.inode not in fuse_fs._inode_map


@pytest.mark.fuse
def test_fuse_stat_returns_correct_size(fuse_fs, collective_dir, monkeypatch):
    """
    After _run_encoder() writes metadata with the file size, a subsequent
    inode refresh must expose that size in the inode map entry used by getattr.
    """
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: _FakeCompletedProcess(returncode=0))

    data = os.urandom(8192)
    src = pathlib.Path(collective_dir) / "proc" / "sized.bin"
    src.write_bytes(data)
    fuse_fs._run_encoder(src, "sized.bin")

    # Force a refresh to pick up the newly written metadata
    fuse_fs._last_refresh = 0.0
    fuse_fs._refresh_inodes()

    with fuse_fs._inode_lock:
        entries = list(fuse_fs._inode_map.values())

    matching = [e for e in entries if e.name == "sized.bin"]
    assert matching, "No inode entry found for 'sized.bin' after encoding"
    assert matching[0].size == len(data), (
        f"Expected size {len(data)}, got {matching[0].size}"
    )


@pytest.mark.fuse
def test_fuse_concurrent_writes(fuse_fs, collective_dir, monkeypatch):
    """
    Calling _run_encoder() for multiple files concurrently (via threads) must
    result in each file getting its own separate metadata entry in
    .collective/tree/ — no entries should be lost due to race conditions.
    """
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: _FakeCompletedProcess(returncode=0))

    filenames = [f"concurrent_{i}.bin" for i in range(8)]
    errors = []

    def encode_one(name):
        try:
            src = pathlib.Path(collective_dir) / "proc" / name
            src.write_bytes(os.urandom(4096))
            fuse_fs._run_encoder(src, name)
        except Exception as e:
            errors.append((name, e))

    threads = [threading.Thread(target=encode_one, args=(n,)) for n in filenames]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"

    tree_dir = pathlib.Path(collective_dir) / "tree"
    stored_names = {
        json.loads(p.read_text())["name"]
        for p in tree_dir.glob("*.json")
    }
    for name in filenames:
        assert name in stored_names, (
            f"{name} has no metadata entry after concurrent write — "
            f"stored names: {stored_names}"
        )
