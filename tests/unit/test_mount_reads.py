"""Reads through the mount fetch ranges, not whole files.

A player asks for ~128 KiB at a time and seeks freely. The mount used to answer
each of those by downloading the entire file, keeping up to 32 whole files in
RAM and expiring them after 30 seconds -- so a 64 KiB read of a 420 MB video
cost 4.3 s, and playing it for longer than half a minute downloaded all of it
again, repeatedly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cfs_mount
from cfs_mount import READ_BLOCK, CollectiveFS, Node, _runs


class FakeClient:
    """A node that honours Range, recording exactly what was asked for."""

    def __init__(self, payload: bytes, *, honour_range: bool = True):
        self.payload = payload
        self.honour_range = honour_range
        self.requests: list[tuple[int, int]] = []
        self.whole_file_downloads = 0

    def download(self, file_id: str) -> bytes:
        self.whole_file_downloads += 1
        return self.payload

    def download_range(self, file_id: str, start: int, length: int) -> bytes:
        self.requests.append((start, length))
        if not self.honour_range:
            # An older node ignores Range and returns 200 with everything; the
            # client slices locally so the bytes are still right.
            return self.payload[start:start + length]
        return self.payload[start:start + length]

    @property
    def bytes_fetched(self) -> int:
        return sum(length for _, length in self.requests)


def _fs(payload: bytes, **kwargs) -> tuple[CollectiveFS, FakeClient, Node]:
    client = FakeClient(payload, **kwargs)
    fs = CollectiveFS.__new__(CollectiveFS)
    fs.client = client
    fs._pending = {}
    fs._blocks = cfs_mount.OrderedDict()
    fs._blocks_bytes = 0
    node = Node(2, "clip.mp4", False, file_id="file-1", size=len(payload))
    return fs, client, node


def test_a_small_read_does_not_pull_the_whole_file() -> None:
    payload = bytes(range(256)) * (40 * READ_BLOCK // 256)  # 40 MiB
    fs, client, node = _fs(payload)

    got = fs._read_at(node, 0, 65536)

    assert got == payload[:65536]
    assert client.whole_file_downloads == 0
    # One block, not forty megabytes.
    assert client.bytes_fetched == READ_BLOCK


def test_seeking_fetches_only_the_blocks_touched() -> None:
    payload = bytes(range(256)) * (40 * READ_BLOCK // 256)
    fs, client, node = _fs(payload)

    offset = 33 * READ_BLOCK + 1234
    got = fs._read_at(node, offset, 4096)

    assert got == payload[offset:offset + 4096]
    assert client.requests == [(33 * READ_BLOCK, READ_BLOCK)]


def test_a_read_spanning_blocks_coalesces_into_one_request() -> None:
    payload = bytes(range(256)) * (8 * READ_BLOCK // 256)
    fs, client, node = _fs(payload)

    # Starting 16 bytes before a boundary and running 2 MiB touches four
    # blocks; all four are contiguous, so they cost one round trip.
    start = READ_BLOCK - 16
    length = READ_BLOCK * 2 + 32
    got = fs._read_at(node, start, length)

    assert got == payload[start:start + length]
    assert len(client.requests) == 1
    assert client.requests[0] == (0, 4 * READ_BLOCK)


def test_cached_blocks_are_not_refetched() -> None:
    payload = bytes(range(256)) * (8 * READ_BLOCK // 256)
    fs, client, node = _fs(payload)

    for index in range(8):
        fs._read_at(node, index * 65536, 65536)

    # Every read lands in the first block, which is fetched once.
    assert len(client.requests) == 1


def test_the_cache_is_bounded_by_bytes_not_by_file_count(monkeypatch) -> None:
    monkeypatch.setattr(cfs_mount, "READ_CACHE_BYTES", 4 * READ_BLOCK)
    payload = bytes(1) * (16 * READ_BLOCK)
    fs, client, node = _fs(payload)

    for index in range(16):
        fs._read_at(node, index * READ_BLOCK, 32)

    assert fs._blocks_bytes <= 4 * READ_BLOCK
    assert len(fs._blocks) <= 4


def test_reads_past_the_end_stop_at_the_end() -> None:
    payload = b"x" * 1000
    fs, client, node = _fs(payload)

    assert fs._read_at(node, 900, 65536) == payload[900:]
    assert fs._read_at(node, 5000, 4096) == b""


def test_an_unwritten_file_is_served_from_the_pending_copy(tmp_path: Path) -> None:
    payload = b"just written, not yet encoded"
    staged = tmp_path / "pending.bin"
    staged.write_bytes(payload)
    fs, client, node = _fs(payload)
    fs._pending[node.file_id] = staged

    assert fs._read_at(node, 5, 7) == payload[5:12]
    assert client.requests == []


def test_invalidate_drops_only_that_files_blocks() -> None:
    payload = b"a" * (4 * READ_BLOCK)
    fs, client, node = _fs(payload)
    other = Node(3, "other.mp4", False, file_id="file-2", size=len(payload))

    fs._read_at(node, 0, 32)
    fs._read_at(other, 0, 32)
    before = fs._blocks_bytes
    fs._invalidate("file-1")

    assert all(key[0] == "file-2" for key in fs._blocks)
    assert fs._blocks_bytes == before - READ_BLOCK


@pytest.mark.parametrize(
    ("indexes", "expected"),
    [
        ([], []),
        ([3], [(3, 3)]),
        ([0, 1, 2], [(0, 2)]),
        ([0, 1, 5, 6, 9], [(0, 1), (5, 6), (9, 9)]),
    ],
)
def test_runs_collapse_contiguous_blocks(indexes, expected) -> None:
    assert _runs(indexes) == expected
