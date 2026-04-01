"""
Peer contract engine for CollectiveFS.

Implements:
- Tiered storage contracts (HOT / WARM / COLD) with configurable SLAs
- Proof-of-storage challenges (random byte-offset HMAC verification)
- Storage accounting ledger (contributed vs consumed)
- QoS scoring (challenge pass rate, latency, availability)
- Enforcement state machine (active → probation → suspended → evicted)
- Reciprocity: if a peer drops your chunks you drop theirs
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from api.models import (
    ChallengeRecord,
    ChallengeRequest,
    ChallengeResponse,
    ContractStatus,
    ContractSummary,
    ContractTier,
    PeerContract,
    QoSMetrics,
    TierConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER_CONFIGS: Dict[ContractTier, TierConfig] = {
    ContractTier.HOT: TierConfig(
        name=ContractTier.HOT,
        challenge_interval_s=30,       # challenge every 30 seconds
        max_response_s=1.0,            # must respond within 1 second
        storage_multiplier=2.0,        # contribute 2x what you consume
        challenge_positions=8,         # check 8 random positions
        window_size=32,                # 32 bytes per position
        probation_threshold=0.8,       # score below this → probation
        eviction_threshold=0.3,        # score below this → evicted
        max_violations=3,
    ),
    ContractTier.WARM: TierConfig(
        name=ContractTier.WARM,
        challenge_interval_s=300,      # every 5 minutes
        max_response_s=60.0,           # 60 seconds to respond
        storage_multiplier=1.0,        # 1:1 ratio
        challenge_positions=6,
        window_size=32,
        probation_threshold=0.7,
        eviction_threshold=0.3,
        max_violations=5,
    ),
    ContractTier.COLD: TierConfig(
        name=ContractTier.COLD,
        challenge_interval_s=3600,     # every hour
        max_response_s=3600.0,         # 1 hour to respond
        storage_multiplier=0.5,        # can consume 2x what you contribute
        challenge_positions=4,
        window_size=32,
        probation_threshold=0.6,
        eviction_threshold=0.2,
        max_violations=10,
    ),
}


def get_tier_config(tier: ContractTier) -> TierConfig:
    return TIER_CONFIGS[tier]


# ---------------------------------------------------------------------------
# Proof-of-storage challenge logic
# ---------------------------------------------------------------------------


def generate_challenge(
    shard_path: Path,
    shard_id: str,
    tier: ContractTier,
) -> Optional[ChallengeRecord]:
    """Generate a proof-of-storage challenge for a shard we hold locally.

    Picks random byte offsets, reads a window at each, and computes the
    expected HMAC.  Returns None if the shard file doesn't exist or is
    too small.
    """
    if not shard_path.exists():
        return None

    file_size = shard_path.stat().st_size
    cfg = get_tier_config(tier)

    if file_size < cfg.window_size:
        return None

    max_offset = file_size - cfg.window_size
    offsets = sorted(
        secrets.randbelow(max_offset + 1) for _ in range(cfg.challenge_positions)
    )
    nonce = secrets.token_hex(16)

    # Read expected bytes and compute HMAC
    expected_hash = _compute_proof(shard_path, offsets, cfg.window_size, nonce)

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=cfg.max_response_s)

    return ChallengeRecord(
        challenge_id=str(uuid.uuid4()),
        shard_id=shard_id,
        offsets=offsets,
        window_size=cfg.window_size,
        nonce=nonce,
        expected_hash=expected_hash,
        issued_at=now.isoformat(),
        deadline=deadline.isoformat(),
    )


def _compute_proof(
    shard_path: Path,
    offsets: List[int],
    window_size: int,
    nonce: str,
) -> str:
    """Read bytes at offsets from a shard file and compute HMAC-SHA256."""
    data = b""
    with open(shard_path, "rb") as f:
        for offset in sorted(offsets):
            f.seek(offset)
            data += f.read(window_size)
    return hmac.new(
        nonce.encode("utf-8"), data, hashlib.sha256
    ).hexdigest()


def compute_proof_from_bytes(
    shard_bytes: bytes,
    offsets: List[int],
    window_size: int,
    nonce: str,
) -> str:
    """Compute proof from in-memory shard bytes (used by the responder)."""
    data = b""
    for offset in sorted(offsets):
        data += shard_bytes[offset : offset + window_size]
    return hmac.new(
        nonce.encode("utf-8"), data, hashlib.sha256
    ).hexdigest()


def verify_challenge(
    challenge: ChallengeRecord,
    proof: str,
) -> bool:
    """Check if a peer's proof matches the expected hash."""
    return hmac.compare_digest(challenge.expected_hash, proof)


def respond_to_challenge(
    shard_path: Path,
    request: ChallengeRequest,
) -> Optional[ChallengeResponse]:
    """Respond to an incoming proof-of-storage challenge.

    Reads the requested bytes from the local shard and returns the HMAC
    proof.  Returns None if the shard is missing.
    """
    if not shard_path.exists():
        return None

    proof = _compute_proof(
        shard_path, request.offsets, request.window_size, request.nonce
    )
    return ChallengeResponse(
        challenge_id=request.challenge_id,
        proof=proof,
    )


# ---------------------------------------------------------------------------
# QoS scoring
# ---------------------------------------------------------------------------

# Weights for the composite score
_W_CHALLENGE = 0.40
_W_AVAILABILITY = 0.25
_W_LATENCY = 0.20
_W_RATIO = 0.15

# Maximum number of response times to keep in the sliding window
_RESPONSE_WINDOW = 100


def compute_qos_score(
    qos: QoSMetrics,
    tier: ContractTier,
    storage_contributed: int,
    storage_consumed: int,
) -> float:
    """Compute a composite QoS score in [0.0, 1.0]."""
    cfg = get_tier_config(tier)

    # 1. Challenge pass rate
    total = qos.challenges_passed + qos.challenges_failed + qos.challenges_timeout
    challenge_score = qos.challenges_passed / total if total > 0 else 1.0

    # 2. Availability
    avail_score = (
        qos.uptime_passes / qos.uptime_checks if qos.uptime_checks > 0 else 1.0
    )

    # 3. Latency (how close avg response is to the tier's deadline)
    if qos.avg_response_ms > 0 and cfg.max_response_s > 0:
        max_ms = cfg.max_response_s * 1000
        # 1.0 if instant, 0.0 if at deadline, negative if over
        latency_score = max(0.0, 1.0 - (qos.avg_response_ms / max_ms))
    else:
        latency_score = 1.0

    # 4. Storage ratio fairness
    required = storage_consumed * cfg.storage_multiplier
    if required > 0:
        ratio_score = min(1.0, storage_contributed / required)
    else:
        ratio_score = 1.0

    score = (
        _W_CHALLENGE * challenge_score
        + _W_AVAILABILITY * avail_score
        + _W_LATENCY * latency_score
        + _W_RATIO * ratio_score
    )
    return round(max(0.0, min(1.0, score)), 4)


def update_qos_after_challenge(
    qos: QoSMetrics,
    passed: bool,
    response_ms: Optional[float],
    timed_out: bool,
) -> QoSMetrics:
    """Return an updated QoSMetrics after a challenge result."""
    qos.challenges_issued += 1
    if timed_out:
        qos.challenges_timeout += 1
    elif passed:
        qos.challenges_passed += 1
    else:
        qos.challenges_failed += 1

    if response_ms is not None:
        qos.response_times_ms.append(response_ms)
        # Keep sliding window bounded
        if len(qos.response_times_ms) > _RESPONSE_WINDOW:
            qos.response_times_ms = qos.response_times_ms[-_RESPONSE_WINDOW:]
        qos.avg_response_ms = round(
            sum(qos.response_times_ms) / len(qos.response_times_ms), 2
        )
        sorted_times = sorted(qos.response_times_ms)
        p99_idx = max(0, int(len(sorted_times) * 0.99) - 1)
        qos.p99_response_ms = sorted_times[p99_idx]

    qos.last_seen = datetime.now(timezone.utc).isoformat()
    return qos


def record_uptime_check(qos: QoSMetrics, reachable: bool) -> QoSMetrics:
    """Record the result of a simple reachability ping."""
    qos.uptime_checks += 1
    if reachable:
        qos.uptime_passes += 1
    return qos


# ---------------------------------------------------------------------------
# Contract enforcement state machine
# ---------------------------------------------------------------------------


def enforce_contract(contract: PeerContract) -> PeerContract:
    """Evaluate a contract's QoS and transition status if needed.

    State machine:
        ACTIVE ──score < probation_threshold──→ PROBATION
        PROBATION ──score >= probation_threshold──→ ACTIVE
        PROBATION ──score < eviction_threshold or max_violations──→ SUSPENDED
        SUSPENDED ──score >= probation_threshold──→ PROBATION
        SUSPENDED ──score < eviction_threshold──→ EVICTED
        EVICTED is terminal
    """
    if contract.status == ContractStatus.EVICTED:
        return contract

    cfg = get_tier_config(contract.tier)

    # Recompute score
    score = compute_qos_score(
        contract.qos,
        contract.tier,
        contract.storage_contributed_bytes,
        contract.storage_consumed_bytes,
    )
    contract.qos.score = score
    contract.max_violations = cfg.max_violations

    if contract.status == ContractStatus.ACTIVE:
        if score < cfg.probation_threshold:
            contract.status = ContractStatus.PROBATION
            logger.info(
                "Contract %s → PROBATION (score=%.2f)", contract.contract_id, score
            )

    elif contract.status == ContractStatus.PROBATION:
        if score >= cfg.probation_threshold:
            contract.status = ContractStatus.ACTIVE
            contract.violations = 0
            logger.info(
                "Contract %s → ACTIVE (score=%.2f)", contract.contract_id, score
            )
        elif score < cfg.eviction_threshold or contract.violations >= cfg.max_violations:
            contract.status = ContractStatus.SUSPENDED
            logger.warning(
                "Contract %s → SUSPENDED (score=%.2f, violations=%d)",
                contract.contract_id, score, contract.violations,
            )

    elif contract.status == ContractStatus.SUSPENDED:
        if score >= cfg.probation_threshold:
            contract.status = ContractStatus.PROBATION
        elif score < cfg.eviction_threshold:
            contract.status = ContractStatus.EVICTED
            logger.warning("Contract %s → EVICTED", contract.contract_id)

    return contract


# ---------------------------------------------------------------------------
# Contract manager (persistence + orchestration)
# ---------------------------------------------------------------------------


class ContractManager:
    """Manages peer contracts, persists to JSON files, runs the challenge loop."""

    def __init__(self, collective_path: Path, node_id: str):
        self.collective_path = collective_path
        self.node_id = node_id
        self.contracts_dir = collective_path / "contracts"
        self.contracts_dir.mkdir(parents=True, exist_ok=True)
        self.proc_dir = collective_path / "proc"

        # In-memory cache: contract_id → PeerContract
        self._contracts: Dict[str, PeerContract] = {}
        # Pending outbound challenges: challenge_id → (contract_id, ChallengeRecord)
        self._pending_challenges: Dict[str, Tuple[str, ChallengeRecord]] = {}
        # Background task handle
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False

        self._load_all()

    # -- persistence --

    def _contract_path(self, contract_id: str) -> Path:
        return self.contracts_dir / f"{contract_id}.json"

    def _load_all(self) -> None:
        for p in self.contracts_dir.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                contract = PeerContract(**data)
                self._contracts[contract.contract_id] = contract
            except Exception:
                logger.exception("Failed to load contract %s", p.name)

    def _save(self, contract: PeerContract) -> None:
        with open(self._contract_path(contract.contract_id), "w") as f:
            json.dump(contract.model_dump(), f, indent=2, default=str)

    def _delete_file(self, contract_id: str) -> None:
        path = self._contract_path(contract_id)
        if path.exists():
            path.unlink()

    # -- CRUD --

    def create_contract(
        self,
        peer_url: str,
        peer_node_id: str,
        tier: ContractTier = ContractTier.WARM,
    ) -> PeerContract:
        contract = PeerContract(
            contract_id=str(uuid.uuid4()),
            peer_url=peer_url.rstrip("/"),
            peer_node_id=peer_node_id,
            tier=tier,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._contracts[contract.contract_id] = contract
        self._save(contract)
        return contract

    def get_contract(self, contract_id: str) -> Optional[PeerContract]:
        return self._contracts.get(contract_id)

    def list_contracts(
        self, status: Optional[ContractStatus] = None
    ) -> List[PeerContract]:
        contracts = list(self._contracts.values())
        if status is not None:
            contracts = [c for c in contracts if c.status == status]
        return contracts

    def list_summaries(self) -> List[ContractSummary]:
        result = []
        for c in self._contracts.values():
            consumed = c.storage_consumed_bytes or 1
            result.append(
                ContractSummary(
                    contract_id=c.contract_id,
                    peer_url=c.peer_url,
                    peer_node_id=c.peer_node_id,
                    tier=c.tier,
                    status=c.status,
                    storage_contributed_bytes=c.storage_contributed_bytes,
                    storage_consumed_bytes=c.storage_consumed_bytes,
                    storage_ratio=round(c.storage_contributed_bytes / consumed, 2),
                    qos_score=c.qos.score,
                    violations=c.violations,
                )
            )
        return result

    def update_tier(
        self, contract_id: str, tier: ContractTier
    ) -> Optional[PeerContract]:
        contract = self._contracts.get(contract_id)
        if contract is None:
            return None
        contract.tier = tier
        contract.max_violations = get_tier_config(tier).max_violations
        self._save(contract)
        return contract

    def evict_contract(self, contract_id: str) -> Optional[PeerContract]:
        contract = self._contracts.get(contract_id)
        if contract is None:
            return None
        contract.status = ContractStatus.EVICTED
        self._save(contract)
        return contract

    def remove_contract(self, contract_id: str) -> bool:
        if contract_id not in self._contracts:
            return False
        del self._contracts[contract_id]
        self._delete_file(contract_id)
        return True

    # -- shard tracking --

    def register_shard_held_for_us(
        self, contract_id: str, shard_id: str, size_bytes: int
    ) -> None:
        contract = self._contracts.get(contract_id)
        if contract is None:
            return
        if shard_id not in contract.shards_held_for_us:
            contract.shards_held_for_us.append(shard_id)
            contract.storage_consumed_bytes += size_bytes
            self._save(contract)

    def register_shard_we_hold(
        self, contract_id: str, shard_id: str, size_bytes: int
    ) -> None:
        contract = self._contracts.get(contract_id)
        if contract is None:
            return
        if shard_id not in contract.shards_we_hold:
            contract.shards_we_hold.append(shard_id)
            contract.storage_contributed_bytes += size_bytes
            self._save(contract)

    # -- challenge orchestration --

    def _find_shard_path(self, shard_id: str) -> Optional[Path]:
        """Find local path for a shard by scanning proc directories."""
        for file_dir in self.proc_dir.iterdir():
            if not file_dir.is_dir():
                continue
            for shard_file in file_dir.iterdir():
                if shard_file.name.endswith(".size"):
                    continue
                # Match by checking the tree metadata
                # For simplicity, just look for shard files we can challenge
                if shard_file.is_file():
                    return shard_file
        return None

    def _find_shard_path_by_id(self, shard_id: str, tree_dir: Path) -> Optional[Path]:
        """Look up the actual file path for a shard ID from tree metadata."""
        for tree_file in tree_dir.glob("*.json"):
            try:
                with open(tree_file) as f:
                    data = json.load(f)
                for chunk in data.get("chunk_list", []):
                    if chunk.get("id") == shard_id:
                        p = Path(chunk["path"])
                        if p.exists():
                            return p
            except Exception:
                continue
        return None

    def issue_challenge(
        self, contract_id: str, shard_id: str, shard_path: Path
    ) -> Optional[ChallengeRecord]:
        """Generate a challenge for a specific shard a peer holds for us.

        We must have a local copy of the shard to generate the expected answer.
        """
        contract = self._contracts.get(contract_id)
        if contract is None:
            return None

        record = generate_challenge(shard_path, shard_id, contract.tier)
        if record is None:
            return None

        # Track pending challenge
        self._pending_challenges[record.challenge_id] = (contract_id, record)

        # Append to contract's recent challenges (keep last 50)
        contract.recent_challenges.append(record)
        if len(contract.recent_challenges) > 50:
            contract.recent_challenges = contract.recent_challenges[-50:]
        self._save(contract)

        return record

    def resolve_challenge(
        self,
        challenge_id: str,
        proof: Optional[str],
        response_ms: Optional[float],
        timed_out: bool = False,
    ) -> Optional[PeerContract]:
        """Resolve a pending challenge with the peer's proof (or timeout)."""
        entry = self._pending_challenges.pop(challenge_id, None)
        if entry is None:
            return None

        contract_id, record = entry
        contract = self._contracts.get(contract_id)
        if contract is None:
            return None

        passed = False
        if not timed_out and proof is not None:
            passed = verify_challenge(record, proof)

        record.passed = passed
        record.response_ms = response_ms
        record.responded_at = datetime.now(timezone.utc).isoformat()

        # Update QoS
        contract.qos = update_qos_after_challenge(
            contract.qos, passed, response_ms, timed_out
        )

        if not passed:
            contract.violations += 1

        # Run enforcement
        contract = enforce_contract(contract)
        self._save(contract)

        return contract

    def handle_incoming_challenge(
        self,
        request: ChallengeRequest,
        tree_dir: Path,
    ) -> Optional[ChallengeResponse]:
        """Respond to an incoming proof-of-storage challenge from a peer."""
        shard_path = self._find_shard_path_by_id(request.shard_id, tree_dir)
        if shard_path is None:
            return None
        return respond_to_challenge(shard_path, request)

    # -- reciprocity enforcement --

    def get_shards_to_drop(self, contract_id: str) -> List[str]:
        """Return shard IDs we should drop for an evicted/suspended peer."""
        contract = self._contracts.get(contract_id)
        if contract is None:
            return []
        if contract.status in (ContractStatus.EVICTED, ContractStatus.SUSPENDED):
            return list(contract.shards_we_hold)
        return []

    def execute_reciprocal_eviction(
        self, contract_id: str, proc_dir: Path
    ) -> List[str]:
        """Actually delete shards we hold for an evicted peer.

        Returns the list of shard IDs that were dropped.
        """
        shards = self.get_shards_to_drop(contract_id)
        if not shards:
            return []

        dropped: List[str] = []
        contract = self._contracts.get(contract_id)
        if contract is None:
            return dropped

        tree_dir = self.collective_path / "tree"
        for shard_id in shards:
            path = self._find_shard_path_by_id(shard_id, tree_dir)
            if path and path.exists():
                try:
                    path.unlink()
                    dropped.append(shard_id)
                    logger.info("Dropped shard %s (reciprocal eviction)", shard_id)
                except OSError:
                    logger.warning("Failed to drop shard %s", shard_id)

        contract.shards_we_hold = [
            s for s in contract.shards_we_hold if s not in dropped
        ]
        contract.storage_contributed_bytes = max(
            0, contract.storage_contributed_bytes
        )
        self._save(contract)
        return dropped

    # -- background enforcement loop --

    async def start(self) -> None:
        """Start the background challenge/enforcement loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._enforcement_loop())

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

    async def _enforcement_loop(self) -> None:
        """Periodically issue challenges and enforce contracts."""
        while self._running:
            try:
                await self._run_enforcement_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in enforcement loop")
            await asyncio.sleep(10)  # base tick rate

    async def _run_enforcement_cycle(self) -> None:
        """One pass: check each active contract, issue challenges if due."""
        now = datetime.now(timezone.utc)

        for contract in list(self._contracts.values()):
            if contract.status == ContractStatus.EVICTED:
                continue

            cfg = get_tier_config(contract.tier)

            # Check if a challenge is due
            last_challenge_at = None
            if contract.recent_challenges:
                last_challenge_at = datetime.fromisoformat(
                    contract.recent_challenges[-1].issued_at
                )

            interval = timedelta(seconds=cfg.challenge_interval_s)
            if last_challenge_at and (now - last_challenge_at) < interval:
                continue

            # Pick a random shard they hold for us
            if not contract.shards_held_for_us:
                continue

            shard_id = secrets.choice(contract.shards_held_for_us)
            tree_dir = self.collective_path / "tree"
            shard_path = self._find_shard_path_by_id(shard_id, tree_dir)
            if shard_path is None:
                continue

            record = self.issue_challenge(
                contract.contract_id, shard_id, shard_path
            )
            if record is None:
                continue

            # Send challenge to peer
            start_ms = time.monotonic() * 1000
            try:
                async with httpx.AsyncClient(
                    timeout=cfg.max_response_s
                ) as client:
                    req = ChallengeRequest(
                        challenge_id=record.challenge_id,
                        shard_id=record.shard_id,
                        offsets=record.offsets,
                        window_size=record.window_size,
                        nonce=record.nonce,
                    )
                    r = await client.post(
                        f"{contract.peer_url}/api/contracts/challenge/respond",
                        json=req.model_dump(),
                    )
                    elapsed_ms = time.monotonic() * 1000 - start_ms

                    if r.status_code == 200:
                        resp = ChallengeResponse(**r.json())
                        self.resolve_challenge(
                            record.challenge_id,
                            resp.proof,
                            elapsed_ms,
                            timed_out=False,
                        )
                    else:
                        self.resolve_challenge(
                            record.challenge_id, None, elapsed_ms, timed_out=False
                        )
            except (httpx.TimeoutException, httpx.ConnectError):
                elapsed_ms = time.monotonic() * 1000 - start_ms
                self.resolve_challenge(
                    record.challenge_id, None, elapsed_ms, timed_out=True
                )

            # Uptime check (simple ping)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(f"{contract.peer_url}/api/health")
                    reachable = r.status_code == 200
            except Exception:
                reachable = False
            contract.qos = record_uptime_check(contract.qos, reachable)

            # Re-enforce after all updates
            contract = enforce_contract(contract)
            self._save(contract)

            # Execute reciprocal eviction if needed
            if contract.status == ContractStatus.EVICTED:
                self.execute_reciprocal_eviction(
                    contract.contract_id, self.proc_dir
                )

    # -- stats --

    def get_network_health(self) -> Dict[str, Any]:
        """Aggregate health summary across all contracts."""
        contracts = list(self._contracts.values())
        if not contracts:
            return {
                "total_contracts": 0,
                "by_status": {},
                "by_tier": {},
                "avg_qos_score": 0,
                "total_contributed_bytes": 0,
                "total_consumed_bytes": 0,
            }

        by_status: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        scores = []
        total_contributed = 0
        total_consumed = 0

        for c in contracts:
            by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
            by_tier[c.tier.value] = by_tier.get(c.tier.value, 0) + 1
            scores.append(c.qos.score)
            total_contributed += c.storage_contributed_bytes
            total_consumed += c.storage_consumed_bytes

        return {
            "total_contracts": len(contracts),
            "by_status": by_status,
            "by_tier": by_tier,
            "avg_qos_score": round(sum(scores) / len(scores), 4) if scores else 0,
            "total_contributed_bytes": total_contributed,
            "total_consumed_bytes": total_consumed,
        }
