"""Unit tests for the telemetry payload behind the System section."""

import pytest

from api.config_service import DEFAULT_CONFIG
from api.system_service import _status_for, build_overview, collective_usage

pytestmark = pytest.mark.unit

GIB = 1024 ** 3


def config(**storage):
    merged = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
    merged["storage"].update(storage)
    return merged


def make_file(root, file_id, size, shard_sizes, missing=0):
    """Write real shard files so usage reflects bytes actually on disk."""
    shard_dir = root / "proc" / file_id
    shard_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for index, shard_size in enumerate(shard_sizes):
        path = shard_dir / f"shard.{index}"
        if index < len(shard_sizes) - missing:
            path.write_bytes(b"x" * shard_size)
        chunks.append({"num": index, "id": f"{file_id}-{index}", "path": str(path)})
    return {"id": file_id, "name": f"{file_id}.bin", "size": size, "chunk_list": chunks}


# ── status thresholds ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "percent,expected",
    [(None, "unknown"), (0, "healthy"), (74.9, "healthy"), (75, "warning"), (89.9, "warning"), (90, "critical")],
)
def test_status_thresholds(percent, expected):
    assert _status_for(percent) == expected


# ── quota accounting ────────────────────────────────────────────────


def test_hosted_shards_do_not_distort_our_expansion_ratio(tmp_path):
    """Storing a peer's data must not look like our own storage overhead."""
    files = [make_file(tmp_path, "f1", size=1000, shard_sizes=[200] * 6)]
    hosted = tmp_path / "proc" / "_peers" / "other-node" / "their-file"
    hosted.mkdir(parents=True, exist_ok=True)
    (hosted / "theirs.0").write_bytes(b"x" * 50_000)

    usage = collective_usage(tmp_path, config(), files)

    assert usage["hosted_bytes"] == 50_000
    assert usage["own_bytes"] == 1200
    # Quota accounting still counts everything on disk.
    assert usage["used_bytes"] == 51_200
    # Overhead is measured against our own files only.
    assert usage["expansion_ratio"] == 1.2


def test_usage_counts_on_disk_shards_not_logical_size(tmp_path):
    files = [make_file(tmp_path, "f1", size=1000, shard_sizes=[200] * 6)]
    usage = collective_usage(tmp_path, config(), files)

    assert usage["logical_bytes"] == 1000
    assert usage["used_bytes"] == 1200
    assert usage["expansion_ratio"] == 1.2


def test_usage_reports_shard_availability(tmp_path):
    files = [make_file(tmp_path, "f1", size=500, shard_sizes=[100] * 12, missing=3)]
    usage = collective_usage(tmp_path, config(), files)

    assert usage["shards_total"] == 12
    assert usage["shards_available"] == 9
    assert usage["shards_missing"] == 3
    assert usage["durability_percent"] == 75.0


def test_empty_node_is_fully_durable(tmp_path):
    usage = collective_usage(tmp_path, config(), [])
    assert usage["durability_percent"] == 100.0
    assert usage["shards_total"] == 0
    assert usage["accepting_writes"] is True


def test_percent_is_measured_against_the_quota(tmp_path):
    files = [make_file(tmp_path, "f1", size=400, shard_sizes=[100] * 4)]
    usage = collective_usage(tmp_path, config(quota_bytes=1000), files)
    assert usage["used_percent"] == 40.0
    assert usage["free_bytes"] == 600


def test_writes_pause_at_the_watermark(tmp_path):
    files = [make_file(tmp_path, "f1", size=900, shard_sizes=[100] * 9)]

    below = collective_usage(tmp_path, config(quota_bytes=1000, high_watermark_percent=95), files)
    assert below["accepting_writes"] is True

    at_cutoff = collective_usage(tmp_path, config(quota_bytes=1000, high_watermark_percent=90), files)
    assert at_cutoff["used_percent"] == 90.0
    assert at_cutoff["accepting_writes"] is False


def test_usage_status_escalates_with_pressure(tmp_path):
    files = [make_file(tmp_path, "f1", size=950, shard_sizes=[950])]
    usage = collective_usage(tmp_path, config(quota_bytes=1000, high_watermark_percent=90), files)
    assert usage["status"] == "critical"


def test_free_bytes_never_goes_negative(tmp_path):
    files = [make_file(tmp_path, "f1", size=5000, shard_sizes=[5000])]
    usage = collective_usage(tmp_path, config(quota_bytes=1000), files)
    assert usage["free_bytes"] == 0
    assert usage["accepting_writes"] is False


# ── full overview ───────────────────────────────────────────────────


def test_backing_disk_is_not_listed_twice(tmp_path):
    """A bind mount reports a different st_dev while being the same filesystem."""
    overview = build_overview(root=tmp_path, node_id="n", config=config(), files=[], peers=[])
    ids = [disk["id"] for disk in overview["disks"]]
    assert ids[0] == "collective"
    assert overview["disks"][0]["label"] == "Backing Disk"
    # tmp_path lives on the root filesystem here, so the duplicate must be dropped.
    sizes = {(disk["total_bytes"], disk["used_bytes"]) for disk in overview["disks"]}
    assert len(sizes) == len(overview["disks"])


def test_usage_reports_device_headroom(tmp_path):
    files = [make_file(tmp_path, "f1", size=100, shard_sizes=[100])]
    usage = collective_usage(tmp_path, config(quota_bytes=1 * GIB), files)

    assert usage["device_total_bytes"] > 0
    assert usage["device_free_bytes"] > 0
    # A 1 GiB pledge on a real disk is comfortably backed.
    assert usage["unbacked_bytes"] == 0
    assert usage["quota_fully_backed"] is True


def test_pledge_beyond_free_space_is_flagged(tmp_path):
    """A quota can legally exceed what is free — the node must say so."""
    files = [make_file(tmp_path, "f1", size=100, shard_sizes=[100])]
    import shutil as _shutil

    huge = _shutil.disk_usage(str(tmp_path)).total
    usage = collective_usage(tmp_path, config(quota_bytes=huge), files)

    assert usage["unbacked_bytes"] > 0
    assert usage["quota_fully_backed"] is False


def test_overview_has_everything_the_section_renders(tmp_path):
    files = [make_file(tmp_path, "f1", size=800, shard_sizes=[100] * 8)]
    overview = build_overview(
        root=tmp_path,
        node_id="node-1",
        config=config(),
        files=files,
        peers=[{"url": "http://a", "healthy": True}, {"url": "http://b", "healthy": False}],
        contract_health={"total_contracts": 2},
    )

    assert overview["node_id"] == "node-1"
    assert overview["hostname"]
    assert overview["cpu"]["id"] == "cpu"
    assert overview["memory"]["unit"] == "bytes"
    assert overview["quota"]["id"] == "quota"
    assert overview["peers"] == {
        "total": 2,
        "online": 1,
        "items": [{"url": "http://a", "healthy": True}, {"url": "http://b", "healthy": False}],
    }
    assert overview["contracts"]["total_contracts"] == 2
    assert overview["disks"][0]["path"] == str(tmp_path)


def test_overview_reports_the_erasure_fault_budget(tmp_path):
    cfg = config()
    cfg["erasure"] = {"data_shards": 10, "parity_shards": 5}
    overview = build_overview(root=tmp_path, node_id="n", config=cfg, files=[], peers=[])

    assert overview["erasure"] == {
        "data_shards": 10,
        "parity_shards": 5,
        "total_shards": 15,
        "can_lose": 5,
        "overhead_percent": 50.0,
    }


def test_network_entries_are_classified(tmp_path):
    overview = build_overview(root=tmp_path, node_id="n", config=config(), files=[], peers=[])
    for iface in overview["network"]:
        assert isinstance(iface["virtual"], bool)
        assert not iface["name"].startswith(("veth", "lo"))
    # Physical links sort ahead of virtual ones.
    kinds = [iface["virtual"] for iface in overview["network"]]
    assert kinds == sorted(kinds)
