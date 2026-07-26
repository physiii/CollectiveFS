"""Unit tests for shard placement across peers.

The placement rule is the safety property of the whole feature: no peer may
hold more shards than Reed-Solomon can rebuild, so losing any single peer never
loses the file.
"""

import pytest

from api.replication import (
    data_shards_only,
    digest,
    is_size_sidecar,
    placement_summary,
    plan_placement,
    shard_index,
)

pytestmark = pytest.mark.unit


def chunks(count, base="/data/proc/f1/file.bin", with_sidecar=True):
    out = [{"num": i, "id": f"c{i}", "path": f"{base}.{i}"} for i in range(count)]
    if with_sidecar:
        out.append({"num": count, "id": "size", "path": f"{base}.size"})
    return out


# ── shard identity ──────────────────────────────────────────────────


def test_sidecar_is_not_a_shard():
    assert is_size_sidecar({"path": "/x/file.bin.size"}) is True
    assert is_size_sidecar({"path": "/x/file.bin.3"}) is False


def test_shard_index_reads_the_numeric_suffix():
    assert shard_index({"path": "/x/file.bin.7"}) == 7
    assert shard_index({"path": "/x/file.bin.11"}) == 11
    assert shard_index({"path": "/x/file.bin.size"}) is None


def test_data_shards_only_drops_the_sidecar():
    listing = chunks(4)
    assert len(listing) == 5
    assert len(data_shards_only(listing)) == 4


def test_digest_is_stable():
    assert digest(b"abc") == digest(b"abc")
    assert digest(b"abc") != digest(b"abd")


# ── placement ───────────────────────────────────────────────────────


def test_without_peers_everything_stays_local():
    placement = plan_placement(chunks(12), [], parity_shards=4)
    assert set(placement) == set(range(12))
    assert all(target is None for target in placement.values())


def test_a_peer_never_exceeds_the_parity_budget():
    """8+4 with one peer: the peer may hold at most 4, or a peer outage is fatal."""
    placement = plan_placement(chunks(12), ["http://peer-a"], parity_shards=4)
    on_peer = [index for index, target in placement.items() if target == "http://peer-a"]
    local = [index for index, target in placement.items() if target is None]

    assert len(on_peer) == 4
    assert len(local) == 8
    assert len(on_peer) <= 4, "losing the peer must stay inside the fault budget"


def test_shards_spread_across_several_peers():
    placement = plan_placement(
        chunks(12), ["http://a", "http://b", "http://c"], parity_shards=4
    )
    counts = {}
    for target in placement.values():
        counts[target or "local"] = counts.get(target or "local", 0) + 1

    for peer in ("http://a", "http://b", "http://c"):
        assert counts.get(peer, 0) <= 4
    assert sum(counts.values()) == 12
    # With 3 peers at 4 each there is room for every shard to leave.
    assert counts.get("local", 0) == 0


def test_capacity_is_per_peer_not_shared():
    placement = plan_placement(chunks(12), ["http://a", "http://b"], parity_shards=2)
    counts = {}
    for target in placement.values():
        counts[target or "local"] = counts.get(target or "local", 0) + 1
    assert counts["http://a"] == 2
    assert counts["http://b"] == 2
    assert counts["local"] == 8


def test_zero_parity_keeps_everything_local():
    """With no parity there is no budget to lose a peer, so nothing leaves."""
    placement = plan_placement(chunks(8), ["http://a"], parity_shards=0)
    assert all(target is None for target in placement.values())


def test_sidecar_is_never_placed_on_a_peer():
    placement = plan_placement(chunks(12), ["http://a"], parity_shards=4)
    # The sidecar has no numeric index, so it cannot appear in the plan at all.
    assert None not in placement
    assert len(placement) == 12


def test_more_peer_capacity_than_shards_is_fine():
    placement = plan_placement(chunks(3), ["http://a", "http://b"], parity_shards=8)
    assert sum(1 for target in placement.values() if target) == 3


# ── summary ─────────────────────────────────────────────────────────


def test_placement_summary_counts_by_location():
    listing = chunks(4)
    listing[0]["peer"] = "http://a"
    listing[1]["peer"] = "http://a"
    listing[2]["peer"] = None
    assert placement_summary(listing) == {"http://a": 2, "local": 2}


def test_placement_summary_ignores_the_sidecar():
    listing = chunks(2)
    assert sum(placement_summary(listing).values()) == 2
