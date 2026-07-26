"""Unit tests for the runtime configuration store."""

import json

import pytest

from api.config_service import (
    DEFAULT_CONFIG,
    ConfigError,
    ConfigStore,
    apply_updates,
    describe_settings,
    human_bytes,
    parse_size,
)

pytestmark = pytest.mark.unit

GIB = 1024 ** 3


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path)


# ── size parsing ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1024, 1024),
        ("1024", 1024),
        ("1KB", 1024),
        ("1 kib", 1024),
        ("500GB", 500 * GIB),
        ("1.5TB", int(1.5 * 1024 ** 4)),
        ("2 TiB", 2 * 1024 ** 4),
        ("1,024", 1024),
    ],
)
def test_parse_size_accepts_common_forms(raw, expected):
    assert parse_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "GB", True, "abc"])
def test_parse_size_rejects_nonsense(raw):
    with pytest.raises(ConfigError):
        parse_size(raw)


def test_human_bytes_round_trips_readably():
    assert human_bytes(500 * GIB) == "500 GB"
    assert human_bytes(None) == "n/a"


# ── load / persist ──────────────────────────────────────────────────


def test_defaults_when_no_file(store):
    assert store.load() == DEFAULT_CONFIG


def test_saved_values_survive_a_reload(store, tmp_path):
    apply_updates(store, {"storage.quota_bytes": "120GB"})
    # A brand new store reads from disk; this is what a process restart does.
    reloaded = ConfigStore(tmp_path).load()
    assert reloaded["storage"]["quota_bytes"] == 120 * GIB


def test_partial_file_is_merged_over_defaults(store, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"erasure": {"parity_shards": 9}}))
    config = ConfigStore(tmp_path).load()
    assert config["erasure"]["parity_shards"] == 9
    assert config["erasure"]["data_shards"] == DEFAULT_CONFIG["erasure"]["data_shards"]
    assert config["storage"] == DEFAULT_CONFIG["storage"]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    assert ConfigStore(tmp_path).load() == DEFAULT_CONFIG


def test_env_seeds_provider_even_after_a_save(store, tmp_path, monkeypatch):
    apply_updates(store, {"storage.quota_bytes": "80GB"})
    monkeypatch.setenv("AGENT_PROVIDER", "codex")
    monkeypatch.setenv("COLLECTIVE_QUOTA_BYTES", "999GB")
    config = ConfigStore(tmp_path).load()
    assert config["agent"]["provider"] == "codex"
    # Sizes stay under operator control once a config has been written.
    assert config["storage"]["quota_bytes"] == 80 * GIB


# ── updates ─────────────────────────────────────────────────────────


def test_apply_reports_the_diff(store):
    _, changes = apply_updates(store, {"erasure.parity_shards": 6})
    assert changes == [
        {
            "field": "erasure.parity_shards",
            "label": "Reed-Solomon parity shards",
            "type": "int",
            "before": 4,
            "after": 6,
        }
    ]


def test_setting_the_same_value_is_a_no_op(store):
    apply_updates(store, {"erasure.parity_shards": 6})
    _, changes = apply_updates(store, {"erasure.parity_shards": 6})
    assert changes == []


def test_sizes_accept_human_units(store):
    config, _ = apply_updates(store, {"upload.max_file_bytes": "2GB"})
    assert config["upload"]["max_file_bytes"] == 2 * GIB


def test_booleans_accept_words(store):
    config, _ = apply_updates(store, {"contracts.challenges_enabled": "off"})
    assert config["contracts"]["challenges_enabled"] is False


def test_unknown_field_is_rejected(store):
    with pytest.raises(ConfigError, match="unknown setting"):
        apply_updates(store, {"storage.magic": 1})


def test_out_of_range_is_rejected(store):
    with pytest.raises(ConfigError, match="at most 32"):
        apply_updates(store, {"erasure.parity_shards": 99})


def test_quota_above_the_filesystem_is_rejected(store):
    with pytest.raises(ConfigError, match="exceeds the filesystem size"):
        apply_updates(store, {"storage.quota_bytes": "9000PB"})


def test_reserve_must_stay_below_quota(store):
    with pytest.raises(ConfigError, match="must be smaller than"):
        apply_updates(store, {"storage.reserve_bytes": "500GB", "storage.quota_bytes": "10GB"})


def test_upload_limit_cannot_exceed_quota(store):
    with pytest.raises(ConfigError, match="cannot exceed the storage quota"):
        apply_updates(store, {"upload.max_file_bytes": "400GB", "storage.quota_bytes": "100GB"})


def test_shard_totals_are_capped(store):
    with pytest.raises(ConfigError, match="40 or fewer"):
        apply_updates(store, {"erasure.data_shards": 30, "erasure.parity_shards": 20})


def test_a_rejected_batch_writes_nothing(store):
    before = store.load()
    with pytest.raises(ConfigError):
        apply_updates(store, {"erasure.data_shards": 12, "erasure.parity_shards": 99})
    assert store.load() == before


def test_empty_update_is_an_error(store):
    with pytest.raises(ConfigError, match="no changes requested"):
        apply_updates(store, {})


# ── audit ───────────────────────────────────────────────────────────


def test_audit_records_source_and_diff(store):
    apply_updates(store, {"contracts.default_tier": "cold"}, source="chat:system", actor="agent:codewhale")
    entries = store.recent_audit()
    assert len(entries) == 1
    assert entries[0]["source"] == "chat:system"
    assert entries[0]["actor"] == "agent:codewhale"
    assert entries[0]["changes"][0]["after"] == "cold"


def test_audit_is_newest_first(store):
    apply_updates(store, {"contracts.max_peers": 10})
    apply_updates(store, {"contracts.max_peers": 20})
    entries = store.recent_audit()
    assert entries[0]["changes"][0]["after"] == 20
    assert entries[1]["changes"][0]["after"] == 10


def test_rejected_changes_are_not_audited(store):
    with pytest.raises(ConfigError):
        apply_updates(store, {"erasure.parity_shards": 99})
    assert store.recent_audit() == []


# ── schema ──────────────────────────────────────────────────────────


def test_schema_describes_every_writable_field():
    fields = {spec["field"] for spec in describe_settings()}
    assert "storage.quota_bytes" in fields
    assert "agent.provider" in fields
    for spec in describe_settings():
        assert spec["label"]
        assert spec["type"] in {"bytes", "int", "bool", "enum", "str"}
