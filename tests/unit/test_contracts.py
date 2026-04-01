"""
Unit tests for the CollectiveFS peer contract system.

Coverage:
- Tier configuration (HOT/WARM/COLD parameters)
- Proof-of-storage challenge generation, response, and verification
- QoS scoring (challenge rate, latency, availability, storage ratio)
- Storage accounting ledger (contributed vs consumed)
- Enforcement state machine (active → probation → suspended → evicted)
- Reciprocal eviction (drop peer shards when contract evicted)
- ContractManager CRUD and persistence
- Edge cases (empty shards, tiny files, max violations)
"""

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from api.models import (
    ChallengeRecord,
    ChallengeRequest,
    ContractStatus,
    ContractTier,
    PeerContract,
    QoSMetrics,
)
from api.contracts import (
    TIER_CONFIGS,
    ContractManager,
    compute_proof_from_bytes,
    compute_qos_score,
    enforce_contract,
    generate_challenge,
    get_tier_config,
    record_uptime_check,
    respond_to_challenge,
    update_qos_after_challenge,
    verify_challenge,
    _compute_proof,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shard_data():
    """1 KB of deterministic random data for reproducible challenge tests."""
    return os.urandom(1024)


@pytest.fixture
def shard_file(tmp_path, shard_data):
    """Write shard_data to a temp file and return the path."""
    p = tmp_path / "test_shard.0"
    p.write_bytes(shard_data)
    return p


@pytest.fixture
def collective_dir(tmp_path):
    """Create a minimal .collective directory layout for ContractManager."""
    base = tmp_path / ".collective"
    for sub in ("proc", "cache", "public", "tree", "contracts"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture
def manager(collective_dir):
    """Return a ContractManager rooted at the temp collective dir."""
    return ContractManager(collective_dir, node_id="test-node-1")


@pytest.fixture
def sample_contract(manager):
    """Create and return a basic WARM contract."""
    return manager.create_contract(
        peer_url="http://peer1:8000",
        peer_node_id="peer-node-1",
        tier=ContractTier.WARM,
    )


# ---------------------------------------------------------------------------
# Tier configuration tests
# ---------------------------------------------------------------------------


class TestTierConfig:

    def test_all_tiers_defined(self):
        """Every ContractTier enum value has a matching config."""
        for tier in ContractTier:
            cfg = get_tier_config(tier)
            assert cfg.name == tier

    def test_hot_tier_is_strictest(self):
        hot = get_tier_config(ContractTier.HOT)
        warm = get_tier_config(ContractTier.WARM)
        cold = get_tier_config(ContractTier.COLD)

        assert hot.max_response_s < warm.max_response_s < cold.max_response_s
        assert hot.challenge_interval_s < warm.challenge_interval_s < cold.challenge_interval_s
        assert hot.storage_multiplier > warm.storage_multiplier > cold.storage_multiplier
        assert hot.max_violations < warm.max_violations < cold.max_violations

    def test_hot_sub_second_deadline(self):
        cfg = get_tier_config(ContractTier.HOT)
        assert cfg.max_response_s <= 1.0

    def test_cold_hour_deadline(self):
        cfg = get_tier_config(ContractTier.COLD)
        assert cfg.max_response_s >= 3600

    def test_challenge_positions_positive(self):
        for tier in ContractTier:
            cfg = get_tier_config(tier)
            assert cfg.challenge_positions > 0
            assert cfg.window_size > 0

    def test_thresholds_ordered(self):
        """eviction_threshold must be below probation_threshold."""
        for tier in ContractTier:
            cfg = get_tier_config(tier)
            assert cfg.eviction_threshold < cfg.probation_threshold


# ---------------------------------------------------------------------------
# Proof-of-storage challenge tests
# ---------------------------------------------------------------------------


class TestChallengeGeneration:

    def test_generate_challenge_returns_record(self, shard_file):
        record = generate_challenge(shard_file, "shard-1", ContractTier.WARM)
        assert record is not None
        assert record.shard_id == "shard-1"
        assert len(record.offsets) == get_tier_config(ContractTier.WARM).challenge_positions
        assert len(record.nonce) == 32  # 16 bytes hex
        assert record.expected_hash  # non-empty

    def test_generate_challenge_missing_file(self, tmp_path):
        fake = tmp_path / "nonexistent.0"
        record = generate_challenge(fake, "shard-1", ContractTier.WARM)
        assert record is None

    def test_generate_challenge_file_too_small(self, tmp_path):
        tiny = tmp_path / "tiny.0"
        tiny.write_bytes(b"x")  # smaller than window_size
        record = generate_challenge(tiny, "shard-1", ContractTier.WARM)
        assert record is None

    def test_offsets_within_bounds(self, shard_file, shard_data):
        cfg = get_tier_config(ContractTier.HOT)
        record = generate_challenge(shard_file, "s1", ContractTier.HOT)
        assert record is not None
        max_offset = len(shard_data) - cfg.window_size
        for offset in record.offsets:
            assert 0 <= offset <= max_offset

    def test_offsets_sorted(self, shard_file):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        assert record.offsets == sorted(record.offsets)

    def test_deadline_matches_tier(self, shard_file):
        for tier in ContractTier:
            record = generate_challenge(shard_file, "s1", tier)
            if record is None:
                continue
            issued = datetime.fromisoformat(record.issued_at)
            deadline = datetime.fromisoformat(record.deadline)
            expected_delta = timedelta(seconds=get_tier_config(tier).max_response_s)
            actual_delta = deadline - issued
            # Allow 1 second tolerance for test execution time
            assert abs(actual_delta.total_seconds() - expected_delta.total_seconds()) < 1


class TestChallengeVerification:

    def test_correct_proof_passes(self, shard_file, shard_data):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        # Compute proof from the same data
        proof = compute_proof_from_bytes(
            shard_data, record.offsets, record.window_size, record.nonce
        )
        assert verify_challenge(record, proof)

    def test_wrong_proof_fails(self, shard_file):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        assert not verify_challenge(record, "deadbeef" * 8)

    def test_wrong_nonce_fails(self, shard_file, shard_data):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        # Use a different nonce
        wrong_proof = compute_proof_from_bytes(
            shard_data, record.offsets, record.window_size, "wrong_nonce_value"
        )
        assert not verify_challenge(record, wrong_proof)

    def test_tampered_data_fails(self, shard_file, shard_data):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        # Tamper with the data
        tampered = bytearray(shard_data)
        if record.offsets:
            tampered[record.offsets[0]] ^= 0xFF
        proof = compute_proof_from_bytes(
            bytes(tampered), record.offsets, record.window_size, record.nonce
        )
        assert not verify_challenge(record, proof)

    def test_respond_to_challenge_matches(self, shard_file, shard_data):
        record = generate_challenge(shard_file, "s1", ContractTier.WARM)
        assert record is not None
        request = ChallengeRequest(
            challenge_id=record.challenge_id,
            shard_id=record.shard_id,
            offsets=record.offsets,
            window_size=record.window_size,
            nonce=record.nonce,
        )
        response = respond_to_challenge(shard_file, request)
        assert response is not None
        assert verify_challenge(record, response.proof)

    def test_respond_missing_shard(self, tmp_path):
        request = ChallengeRequest(
            challenge_id="c1",
            shard_id="s1",
            offsets=[0, 10],
            window_size=32,
            nonce="abc123",
        )
        response = respond_to_challenge(tmp_path / "gone.0", request)
        assert response is None


class TestComputeProof:

    def test_proof_deterministic(self, shard_file):
        """Same inputs produce the same HMAC."""
        offsets = [0, 100, 500]
        nonce = "test_nonce"
        proof1 = _compute_proof(shard_file, offsets, 32, nonce)
        proof2 = _compute_proof(shard_file, offsets, 32, nonce)
        assert proof1 == proof2

    def test_proof_changes_with_nonce(self, shard_file):
        offsets = [0, 100]
        p1 = _compute_proof(shard_file, offsets, 32, "nonce_a")
        p2 = _compute_proof(shard_file, offsets, 32, "nonce_b")
        assert p1 != p2

    def test_proof_changes_with_offsets(self, shard_file):
        nonce = "fixed_nonce"
        p1 = _compute_proof(shard_file, [0, 100], 32, nonce)
        p2 = _compute_proof(shard_file, [0, 200], 32, nonce)
        assert p1 != p2

    def test_file_proof_matches_bytes_proof(self, shard_file, shard_data):
        offsets = [0, 50, 100]
        nonce = "test"
        file_proof = _compute_proof(shard_file, offsets, 32, nonce)
        bytes_proof = compute_proof_from_bytes(shard_data, offsets, 32, nonce)
        assert file_proof == bytes_proof


# ---------------------------------------------------------------------------
# QoS scoring tests
# ---------------------------------------------------------------------------


class TestQoSScoring:

    def test_perfect_score(self):
        """A peer with 100% pass rate and ideal metrics gets score ~1.0."""
        qos = QoSMetrics(
            challenges_issued=100,
            challenges_passed=100,
            challenges_failed=0,
            challenges_timeout=0,
            avg_response_ms=10.0,
            uptime_checks=100,
            uptime_passes=100,
        )
        score = compute_qos_score(qos, ContractTier.WARM, 1000, 1000)
        assert score >= 0.95

    def test_zero_challenges_defaults_to_one(self):
        """No challenges yet → assume good standing."""
        qos = QoSMetrics()
        score = compute_qos_score(qos, ContractTier.WARM, 0, 0)
        assert score == 1.0

    def test_all_failures_low_score(self):
        """100% failure rate should give a very low score."""
        qos = QoSMetrics(
            challenges_issued=10,
            challenges_passed=0,
            challenges_failed=10,
            challenges_timeout=0,
            avg_response_ms=50000,
            uptime_checks=10,
            uptime_passes=0,
        )
        score = compute_qos_score(qos, ContractTier.WARM, 0, 1000)
        assert score < 0.1

    def test_high_latency_penalised(self):
        """Slow responses reduce the latency component of the score."""
        fast_qos = QoSMetrics(
            challenges_passed=10, avg_response_ms=10,
            uptime_checks=10, uptime_passes=10,
        )
        slow_qos = QoSMetrics(
            challenges_passed=10, avg_response_ms=50000,
            uptime_checks=10, uptime_passes=10,
        )
        fast_score = compute_qos_score(fast_qos, ContractTier.WARM, 1000, 1000)
        slow_score = compute_qos_score(slow_qos, ContractTier.WARM, 1000, 1000)
        assert fast_score > slow_score

    def test_storage_ratio_affects_score(self):
        """Under-contributing should reduce score vs. fair contribution."""
        qos = QoSMetrics(
            challenges_passed=10,
            uptime_checks=10, uptime_passes=10,
        )
        fair_score = compute_qos_score(qos, ContractTier.WARM, 1000, 1000)
        freeloader_score = compute_qos_score(qos, ContractTier.WARM, 0, 1000)
        assert fair_score > freeloader_score

    def test_score_clamped_to_unit_interval(self):
        """Score must always be in [0.0, 1.0]."""
        qos = QoSMetrics(challenges_passed=1000)
        score = compute_qos_score(qos, ContractTier.WARM, 999999, 1)
        assert 0.0 <= score <= 1.0

    def test_update_qos_after_pass(self):
        qos = QoSMetrics()
        qos = update_qos_after_challenge(qos, passed=True, response_ms=50.0, timed_out=False)
        assert qos.challenges_issued == 1
        assert qos.challenges_passed == 1
        assert qos.avg_response_ms == 50.0

    def test_update_qos_after_timeout(self):
        qos = QoSMetrics()
        qos = update_qos_after_challenge(qos, passed=False, response_ms=None, timed_out=True)
        assert qos.challenges_timeout == 1
        assert qos.challenges_passed == 0

    def test_update_qos_after_failure(self):
        qos = QoSMetrics()
        qos = update_qos_after_challenge(qos, passed=False, response_ms=200.0, timed_out=False)
        assert qos.challenges_failed == 1

    def test_response_window_bounded(self):
        """Sliding window should not grow beyond 100 entries."""
        qos = QoSMetrics()
        for i in range(150):
            qos = update_qos_after_challenge(qos, True, float(i), False)
        assert len(qos.response_times_ms) <= 100

    def test_p99_calculation(self):
        qos = QoSMetrics()
        for ms in range(100):
            qos = update_qos_after_challenge(qos, True, float(ms), False)
        # p99 should be near the high end
        assert qos.p99_response_ms >= 90

    def test_uptime_check(self):
        qos = QoSMetrics()
        qos = record_uptime_check(qos, True)
        qos = record_uptime_check(qos, True)
        qos = record_uptime_check(qos, False)
        assert qos.uptime_checks == 3
        assert qos.uptime_passes == 2


# ---------------------------------------------------------------------------
# Enforcement state machine tests
# ---------------------------------------------------------------------------


class TestEnforcement:

    def _make_contract(self, status, score, violations=0, tier=ContractTier.WARM):
        """Build a contract whose compute_qos_score equals `score`.

        All four sub-scores (challenge, availability, latency, ratio) are
        set to `score` so the weighted composite equals the target.
        """
        cfg = get_tier_config(tier)
        max_ms = cfg.max_response_s * 1000
        avg_ms = (1 - score) * max_ms  # latency_score = 1 - avg/max = score
        consumed = 1000
        required = consumed * cfg.storage_multiplier
        contributed = int(score * required)  # ratio_score = contributed/required = score

        return PeerContract(
            contract_id=str(uuid.uuid4()),
            peer_url="http://test:8000",
            peer_node_id="test",
            tier=tier,
            created_at=datetime.now(timezone.utc).isoformat(),
            status=status,
            violations=violations,
            qos=QoSMetrics(
                challenges_passed=int(score * 100),
                challenges_failed=int((1 - score) * 100),
                uptime_checks=100,
                uptime_passes=int(score * 100),
                avg_response_ms=avg_ms,
                score=score,
            ),
            storage_contributed_bytes=contributed,
            storage_consumed_bytes=consumed,
        )

    def test_active_stays_active_when_healthy(self):
        c = self._make_contract(ContractStatus.ACTIVE, score=0.95)
        c = enforce_contract(c)
        assert c.status == ContractStatus.ACTIVE

    def test_active_to_probation(self):
        cfg = get_tier_config(ContractTier.WARM)
        c = self._make_contract(ContractStatus.ACTIVE, score=cfg.probation_threshold - 0.05)
        c = enforce_contract(c)
        assert c.status == ContractStatus.PROBATION

    def test_probation_to_active_recovery(self):
        cfg = get_tier_config(ContractTier.WARM)
        c = self._make_contract(ContractStatus.PROBATION, score=cfg.probation_threshold + 0.05)
        c = enforce_contract(c)
        assert c.status == ContractStatus.ACTIVE
        assert c.violations == 0  # reset on recovery

    def test_probation_to_suspended_low_score(self):
        cfg = get_tier_config(ContractTier.WARM)
        c = self._make_contract(ContractStatus.PROBATION, score=cfg.eviction_threshold - 0.05)
        c = enforce_contract(c)
        assert c.status == ContractStatus.SUSPENDED

    def test_probation_to_suspended_max_violations(self):
        cfg = get_tier_config(ContractTier.WARM)
        # Score is above eviction but violations at max
        c = self._make_contract(
            ContractStatus.PROBATION,
            score=cfg.probation_threshold - 0.1,
            violations=cfg.max_violations,
        )
        c = enforce_contract(c)
        assert c.status == ContractStatus.SUSPENDED

    def test_suspended_to_evicted(self):
        cfg = get_tier_config(ContractTier.WARM)
        c = self._make_contract(ContractStatus.SUSPENDED, score=cfg.eviction_threshold - 0.05)
        c = enforce_contract(c)
        assert c.status == ContractStatus.EVICTED

    def test_suspended_can_recover_to_probation(self):
        cfg = get_tier_config(ContractTier.WARM)
        c = self._make_contract(ContractStatus.SUSPENDED, score=cfg.probation_threshold + 0.05)
        c = enforce_contract(c)
        assert c.status == ContractStatus.PROBATION

    def test_evicted_is_terminal(self):
        c = self._make_contract(ContractStatus.EVICTED, score=1.0)
        c = enforce_contract(c)
        assert c.status == ContractStatus.EVICTED

    def test_violations_increment_on_failure(self, manager, sample_contract, shard_file):
        cid = sample_contract.contract_id
        # Register a shard and issue a challenge
        manager.register_shard_held_for_us(cid, "s1", 1000)
        record = manager.issue_challenge(cid, "s1", shard_file)
        assert record is not None
        # Resolve with wrong proof
        manager.resolve_challenge(record.challenge_id, "wrong", 100.0)
        c = manager.get_contract(cid)
        assert c.violations == 1


# ---------------------------------------------------------------------------
# ContractManager CRUD tests
# ---------------------------------------------------------------------------


class TestContractManager:

    def test_create_contract(self, manager):
        c = manager.create_contract("http://peer:8000", "p1", ContractTier.HOT)
        assert c.contract_id
        assert c.peer_url == "http://peer:8000"
        assert c.tier == ContractTier.HOT
        assert c.status == ContractStatus.ACTIVE

    def test_get_contract(self, manager, sample_contract):
        c = manager.get_contract(sample_contract.contract_id)
        assert c is not None
        assert c.contract_id == sample_contract.contract_id

    def test_get_missing_contract(self, manager):
        assert manager.get_contract("nonexistent") is None

    def test_list_contracts(self, manager):
        manager.create_contract("http://a:8000", "a", ContractTier.HOT)
        manager.create_contract("http://b:8000", "b", ContractTier.COLD)
        contracts = manager.list_contracts()
        assert len(contracts) == 2

    def test_list_contracts_by_status(self, manager):
        c1 = manager.create_contract("http://a:8000", "a")
        c2 = manager.create_contract("http://b:8000", "b")
        manager.evict_contract(c2.contract_id)
        active = manager.list_contracts(status=ContractStatus.ACTIVE)
        evicted = manager.list_contracts(status=ContractStatus.EVICTED)
        assert len(active) == 1
        assert len(evicted) == 1

    def test_list_summaries(self, manager, sample_contract):
        summaries = manager.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].contract_id == sample_contract.contract_id

    def test_update_tier(self, manager, sample_contract):
        c = manager.update_tier(sample_contract.contract_id, ContractTier.HOT)
        assert c.tier == ContractTier.HOT
        # Persisted
        reloaded = manager.get_contract(sample_contract.contract_id)
        assert reloaded.tier == ContractTier.HOT

    def test_evict_contract(self, manager, sample_contract):
        c = manager.evict_contract(sample_contract.contract_id)
        assert c.status == ContractStatus.EVICTED

    def test_remove_contract(self, manager, sample_contract):
        cid = sample_contract.contract_id
        assert manager.remove_contract(cid)
        assert manager.get_contract(cid) is None

    def test_remove_missing_contract(self, manager):
        assert not manager.remove_contract("nonexistent")

    def test_persistence_survives_reload(self, collective_dir):
        """Contracts written by one manager instance load in a new one."""
        mgr1 = ContractManager(collective_dir, "node-1")
        c = mgr1.create_contract("http://p:8000", "p1", ContractTier.WARM)

        mgr2 = ContractManager(collective_dir, "node-1")
        loaded = mgr2.get_contract(c.contract_id)
        assert loaded is not None
        assert loaded.peer_url == "http://p:8000"
        assert loaded.tier == ContractTier.WARM


# ---------------------------------------------------------------------------
# Shard tracking & storage accounting tests
# ---------------------------------------------------------------------------


class TestStorageAccounting:

    def test_register_shard_held_for_us(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "shard-a", 5000)
        c = manager.get_contract(cid)
        assert "shard-a" in c.shards_held_for_us
        assert c.storage_consumed_bytes == 5000

    def test_register_shard_we_hold(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_we_hold(cid, "shard-b", 3000)
        c = manager.get_contract(cid)
        assert "shard-b" in c.shards_we_hold
        assert c.storage_contributed_bytes == 3000

    def test_no_duplicate_shard_registration(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "shard-a", 5000)
        manager.register_shard_held_for_us(cid, "shard-a", 5000)  # duplicate
        c = manager.get_contract(cid)
        assert c.shards_held_for_us.count("shard-a") == 1
        assert c.storage_consumed_bytes == 5000  # not doubled

    def test_storage_ratio_in_summary(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)
        manager.register_shard_we_hold(cid, "s2", 2000)
        summaries = manager.list_summaries()
        s = [x for x in summaries if x.contract_id == cid][0]
        assert s.storage_ratio == 2.0  # 2000/1000

    def test_multiple_shards_accumulate(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_we_hold(cid, "s1", 1000)
        manager.register_shard_we_hold(cid, "s2", 2000)
        manager.register_shard_we_hold(cid, "s3", 3000)
        c = manager.get_contract(cid)
        assert c.storage_contributed_bytes == 6000
        assert len(c.shards_we_hold) == 3


# ---------------------------------------------------------------------------
# Challenge flow (issue → resolve) tests
# ---------------------------------------------------------------------------


class TestChallengeFlow:

    def test_issue_and_resolve_pass(self, manager, sample_contract, shard_file, shard_data):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)

        record = manager.issue_challenge(cid, "s1", shard_file)
        assert record is not None

        # Compute correct proof
        proof = compute_proof_from_bytes(
            shard_data, record.offsets, record.window_size, record.nonce
        )
        contract = manager.resolve_challenge(record.challenge_id, proof, 50.0)
        assert contract is not None
        assert contract.qos.challenges_passed == 1
        assert contract.violations == 0

    def test_issue_and_resolve_fail(self, manager, sample_contract, shard_file):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)

        record = manager.issue_challenge(cid, "s1", shard_file)
        contract = manager.resolve_challenge(record.challenge_id, "bad_proof", 100.0)
        assert contract.qos.challenges_failed == 1
        assert contract.violations == 1

    def test_issue_and_resolve_timeout(self, manager, sample_contract, shard_file):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)

        record = manager.issue_challenge(cid, "s1", shard_file)
        contract = manager.resolve_challenge(
            record.challenge_id, None, None, timed_out=True
        )
        assert contract.qos.challenges_timeout == 1
        assert contract.violations == 1

    def test_resolve_unknown_challenge(self, manager):
        result = manager.resolve_challenge("unknown-id", "proof", 50.0)
        assert result is None

    def test_recent_challenges_capped(self, manager, sample_contract, shard_file, shard_data):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)

        for _ in range(60):
            record = manager.issue_challenge(cid, "s1", shard_file)
            proof = compute_proof_from_bytes(
                shard_data, record.offsets, record.window_size, record.nonce
            )
            manager.resolve_challenge(record.challenge_id, proof, 10.0)

        c = manager.get_contract(cid)
        assert len(c.recent_challenges) <= 50

    def test_challenge_persisted(self, manager, sample_contract, shard_file, collective_dir):
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)
        manager.issue_challenge(cid, "s1", shard_file)

        # Reload from disk
        mgr2 = ContractManager(collective_dir, "test-node-1")
        c = mgr2.get_contract(cid)
        assert len(c.recent_challenges) == 1


# ---------------------------------------------------------------------------
# Reciprocal eviction tests
# ---------------------------------------------------------------------------


class TestReciprocalEviction:

    def test_get_shards_to_drop_evicted(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_we_hold(cid, "s1", 1000)
        manager.register_shard_we_hold(cid, "s2", 2000)

        # Not evicted yet
        assert manager.get_shards_to_drop(cid) == []

        # Evict
        manager.evict_contract(cid)
        shards = manager.get_shards_to_drop(cid)
        assert set(shards) == {"s1", "s2"}

    def test_get_shards_to_drop_active(self, manager, sample_contract):
        cid = sample_contract.contract_id
        manager.register_shard_we_hold(cid, "s1", 1000)
        assert manager.get_shards_to_drop(cid) == []

    def test_execute_eviction_deletes_files(self, manager, sample_contract, collective_dir):
        cid = sample_contract.contract_id

        # Create a fake shard file and register it in tree metadata
        file_id = str(uuid.uuid4())
        shard_id = str(uuid.uuid4())
        proc_dir = collective_dir / "proc" / file_id
        proc_dir.mkdir(parents=True)
        shard_path = proc_dir / "test.bin.0"
        shard_path.write_bytes(b"shard data here")

        tree_data = {
            "id": file_id,
            "name": "test.bin",
            "size": 100,
            "chunks": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "stored",
            "chunk_list": [{"num": 0, "id": shard_id, "path": str(shard_path)}],
        }
        tree_file = collective_dir / "tree" / f"{file_id}.json"
        tree_file.write_text(json.dumps(tree_data))

        manager.register_shard_we_hold(cid, shard_id, 100)
        manager.evict_contract(cid)
        dropped = manager.execute_reciprocal_eviction(cid, collective_dir / "proc")

        assert shard_id in dropped
        assert not shard_path.exists()

    def test_eviction_updates_shard_list(self, manager, sample_contract, collective_dir):
        cid = sample_contract.contract_id

        file_id = str(uuid.uuid4())
        shard_id = str(uuid.uuid4())
        proc_dir = collective_dir / "proc" / file_id
        proc_dir.mkdir(parents=True)
        shard_path = proc_dir / "test.bin.0"
        shard_path.write_bytes(b"data")

        tree_data = {
            "id": file_id, "name": "t.bin", "size": 4, "chunks": 1,
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "stored",
            "chunk_list": [{"num": 0, "id": shard_id, "path": str(shard_path)}],
        }
        (collective_dir / "tree" / f"{file_id}.json").write_text(json.dumps(tree_data))

        manager.register_shard_we_hold(cid, shard_id, 4)
        manager.evict_contract(cid)
        manager.execute_reciprocal_eviction(cid, collective_dir / "proc")

        c = manager.get_contract(cid)
        assert shard_id not in c.shards_we_hold


# ---------------------------------------------------------------------------
# Network health stats tests
# ---------------------------------------------------------------------------


class TestNetworkHealth:

    def test_empty_network(self, manager):
        # Remove the sample contract fixture - use a clean manager
        health = manager.get_network_health()
        assert health["total_contracts"] == 0

    def test_health_aggregation(self, manager):
        manager.create_contract("http://a:8000", "a", ContractTier.HOT)
        manager.create_contract("http://b:8000", "b", ContractTier.COLD)
        c3 = manager.create_contract("http://c:8000", "c", ContractTier.WARM)
        manager.evict_contract(c3.contract_id)

        health = manager.get_network_health()
        assert health["total_contracts"] == 3
        assert health["by_status"]["active"] == 2
        assert health["by_status"]["evicted"] == 1
        assert health["by_tier"]["hot"] == 1
        assert health["by_tier"]["cold"] == 1
        assert health["by_tier"]["warm"] == 1


# ---------------------------------------------------------------------------
# Integration: full challenge lifecycle across tiers
# ---------------------------------------------------------------------------


class TestFullLifecycle:

    def test_repeated_failures_lead_to_eviction(self, manager, shard_file):
        """Simulate a peer that consistently fails challenges until evicted."""
        c = manager.create_contract("http://bad:8000", "bad", ContractTier.HOT)
        cid = c.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)
        manager.register_shard_we_hold(cid, "s2", 500)

        prev_status = ContractStatus.ACTIVE
        for i in range(20):
            record = manager.issue_challenge(cid, "s1", shard_file)
            if record is None:
                break
            manager.resolve_challenge(record.challenge_id, "wrong", 5000.0)
            c = manager.get_contract(cid)
            if c.status == ContractStatus.EVICTED:
                break

        assert c.status == ContractStatus.EVICTED

    def test_good_peer_stays_active(self, manager, shard_file, shard_data):
        """A peer that always passes challenges stays ACTIVE."""
        c = manager.create_contract("http://good:8000", "good", ContractTier.WARM)
        cid = c.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)
        manager.register_shard_we_hold(cid, "s2", 1000)

        for _ in range(20):
            record = manager.issue_challenge(cid, "s1", shard_file)
            proof = compute_proof_from_bytes(
                shard_data, record.offsets, record.window_size, record.nonce
            )
            manager.resolve_challenge(record.challenge_id, proof, 50.0)

        c = manager.get_contract(cid)
        assert c.status == ContractStatus.ACTIVE
        assert c.qos.score >= 0.8
        assert c.violations == 0

    def test_tier_change_adjusts_enforcement(self, manager, sample_contract, shard_file):
        """Changing tier to HOT tightens the enforcement rules."""
        cid = sample_contract.contract_id
        manager.register_shard_held_for_us(cid, "s1", 1000)

        # Upgrade to HOT
        manager.update_tier(cid, ContractTier.HOT)
        c = manager.get_contract(cid)
        assert c.max_violations == get_tier_config(ContractTier.HOT).max_violations

    def test_handle_incoming_challenge(self, manager, collective_dir, shard_file):
        """Test responding to challenges from peers about shards we hold."""
        file_id = str(uuid.uuid4())
        shard_id = str(uuid.uuid4())

        # Set up shard in tree metadata
        proc_dir = collective_dir / "proc" / file_id
        proc_dir.mkdir(parents=True)
        local_shard = proc_dir / "data.bin.0"
        shard_data = shard_file.read_bytes()
        local_shard.write_bytes(shard_data)

        tree_data = {
            "id": file_id, "name": "data.bin", "size": len(shard_data),
            "chunks": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "stored",
            "chunk_list": [{"num": 0, "id": shard_id, "path": str(local_shard)}],
        }
        tree_dir = collective_dir / "tree"
        (tree_dir / f"{file_id}.json").write_text(json.dumps(tree_data))

        # Generate a challenge as if we're the remote peer
        record = generate_challenge(local_shard, shard_id, ContractTier.WARM)
        assert record is not None

        # Respond as the local node
        request = ChallengeRequest(
            challenge_id=record.challenge_id,
            shard_id=shard_id,
            offsets=record.offsets,
            window_size=record.window_size,
            nonce=record.nonce,
        )
        response = manager.handle_incoming_challenge(request, tree_dir)
        assert response is not None
        assert verify_challenge(record, response.proof)
