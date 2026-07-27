"""Unit tests for redundancy assessment.

The distinction that matters here is between a shard that is *gone* and a peer
that is merely *unreachable*. Getting it wrong either hides real data loss or
triggers a fleet-wide rebuild every time a node reboots.
"""

import pytest

from api.repair import Health, assess, summarise

pytestmark = pytest.mark.unit


def meta(file_id, shards, data=8, parity=4, base="/data/proc/f/file.bin"):
    """`shards` maps index -> None (local) or a peer URL."""
    chunk_list = [
        {"num": i, "id": f"{file_id}-{i}", "path": f"{base}.{i}",
         **({"peer": where} if where else {})}
        for i, where in sorted(shards.items())
    ]
    chunk_list.append({"num": 99, "id": "size", "path": f"{base}.size"})
    return {"id": file_id, "name": f"{file_id}.bin", "data_shards": data,
            "parity_shards": parity, "chunk_list": chunk_list}


def on_disk(existing):
    """Patch Path.exists so only `existing` paths count as local."""
    import api.repair as mod

    class FakePath:
        def __init__(self, p): self.p = str(p)
        def exists(self): return self.p in existing

    mod.Path = FakePath
    return mod


@pytest.fixture(autouse=True)
def restore_path():
    import api.repair as mod
    from pathlib import Path as RealPath
    yield
    mod.Path = RealPath


async def probe_holding(mapping):
    async def _probe(peer_url, origin_node, file_id):
        return mapping.get(peer_url)
    return _probe


# ── classification ──────────────────────────────────────────────────


async def test_all_shards_present_is_intact():
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(8)})
    m = meta("f1", {i: None for i in range(8)} | {i: "http://peer" for i in range(8, 12)})
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://peer": {8, 9, 10, 11}}))
    assert r["status"] == Health.INTACT
    assert r["shards_available"] == 12
    assert r["repairable"] is False


async def test_peer_lost_its_shards_is_degraded_and_repairable():
    """The office case: 8 local survive, the peer holding 4 was wiped."""
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(8)})
    m = meta("f2", {i: None for i in range(8)} | {i: "http://dead" for i in range(8, 12)})
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://dead": set()}))
    assert r["status"] == Health.DEGRADED
    assert r["repairable"] is True
    assert r["shards_available"] == 8
    assert r["missing_indices"] == [8, 9, 10, 11]
    assert r["lost_peers"] == ["http://dead"]


async def test_unreachable_peer_is_not_treated_as_lost():
    """A rebooting peer must not look like data loss."""
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(8)})
    m = meta("f3", {i: None for i in range(8)} | {i: "http://down" for i in range(8, 12)})
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://down": None}))
    assert r["status"] == Health.INTACT
    assert r["unreachable_peers"] == ["http://down"]
    assert r["repairable"] is False


async def test_below_the_data_threshold_is_unrecoverable():
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(3)})
    m = meta("f4", {i: None for i in range(3)} | {i: "http://dead" for i in range(3, 12)})
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://dead": set()}))
    assert r["status"] == Health.UNRECOVERABLE
    assert r["repairable"] is False
    assert r["shards_available"] == 3


async def test_metadata_with_no_shards_anywhere_is_orphaned():
    """A partial delete leaves a file that lists but can never be read."""
    on_disk(set())
    m = meta("f5", {i: None for i in range(12)})
    r = await assess(m, "node", default_data_shards=8, probe=await probe_holding({}))
    assert r["status"] == Health.ORPHANED
    assert r["shards_available"] == 0


async def test_partial_peer_loss_counts_what_survives():
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(6)})
    m = meta("f6", {i: None for i in range(6)} | {i: "http://peer" for i in range(6, 12)})
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://peer": {6, 7, 8}}))
    assert r["shards_available"] == 9
    assert r["status"] == Health.DEGRADED
    assert r["missing_indices"] == [9, 10, 11]


async def test_sidecar_is_not_counted_as_a_shard():
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(12)})
    m = meta("f7", {i: None for i in range(12)})
    r = await assess(m, "node", default_data_shards=8, probe=await probe_holding({}))
    assert r["shards_total"] == 12


async def test_per_file_erasure_params_are_respected():
    """A file encoded 4+2 needs 4, not the node's current default of 8."""
    on_disk({f"/data/proc/f/file.bin.{i}" for i in range(4)})
    m = meta("f8", {i: None for i in range(4)} | {i: "http://dead" for i in range(4, 6)},
             data=4, parity=2)
    r = await assess(m, "node", default_data_shards=8,
                     probe=await probe_holding({"http://dead": set()}))
    assert r["data_shards"] == 4
    assert r["status"] == Health.DEGRADED


# ── summary ─────────────────────────────────────────────────────────


def test_summary_groups_and_lists_what_needs_action():
    reports = [
        {"id": "a", "name": "a", "status": Health.INTACT, "shards_missing": 0,
         "repairable": False, "shards_available": 12, "data_shards": 8},
        {"id": "b", "name": "b", "status": Health.DEGRADED, "shards_missing": 4,
         "repairable": True, "shards_available": 8, "data_shards": 8},
        {"id": "c", "name": "c", "status": Health.UNRECOVERABLE, "shards_missing": 9,
         "repairable": False, "shards_available": 3, "data_shards": 8},
        {"id": "d", "name": "d", "status": Health.ORPHANED, "shards_missing": 12,
         "repairable": False, "shards_available": 0, "data_shards": 8},
    ]
    s = summarise(reports)
    assert s["files"] == 4
    assert s["by_status"] == {Health.INTACT: 1, Health.DEGRADED: 1,
                              Health.UNRECOVERABLE: 1, Health.ORPHANED: 1}
    assert s["shards_missing"] == 25
    assert s["repairable"] == ["b"]
    assert [u["id"] for u in s["unrecoverable"]] == ["c"]
    assert [o["id"] for o in s["orphaned"]] == ["d"]
