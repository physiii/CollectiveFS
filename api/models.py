from enum import Enum
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from datetime import datetime


# ---------------------------------------------------------------------------
# Existing models
# ---------------------------------------------------------------------------


class ShardInfo(BaseModel):
    num: int
    id: str
    size: int = 0
    encrypted: bool = False
    available: bool = True
    peer: Optional[str] = None


class FileMetadata(BaseModel):
    id: str
    name: str
    size: int = 0
    chunks: int = 0
    created_at: str
    status: str = "stored"
    folder: Optional[str] = None
    shard_list: Optional[List[ShardInfo]] = None


class ChunkInfo(BaseModel):
    num: int
    id: str
    path: str
    offer: Optional[str] = None
    answer: Optional[str] = None


class SystemStats(BaseModel):
    total_files: int
    total_chunks: int
    storage_used_bytes: int
    storage_path: str
    encryption: str
    erasure_coding: str


class UploadResponse(BaseModel):
    id: str
    name: str
    status: str
    message: str = ""


class StatusUpdate(BaseModel):
    type: str
    file_id: str
    status: str
    progress: Optional[float] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Contract tier enum
# ---------------------------------------------------------------------------


class ContractTier(str, Enum):
    HOT = "hot"      # sub-second response, frequent challenges
    WARM = "warm"    # minute-level response, moderate challenges
    COLD = "cold"    # hour-level response, infrequent challenges


class ContractStatus(str, Enum):
    ACTIVE = "active"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    EVICTED = "evicted"


# ---------------------------------------------------------------------------
# QoS metrics
# ---------------------------------------------------------------------------


class QoSMetrics(BaseModel):
    challenges_issued: int = 0
    challenges_passed: int = 0
    challenges_failed: int = 0
    challenges_timeout: int = 0
    avg_response_ms: float = 0.0
    p99_response_ms: float = 0.0
    response_times_ms: List[float] = Field(default_factory=list)
    uptime_checks: int = 0
    uptime_passes: int = 0
    last_seen: Optional[str] = None
    score: float = 1.0


# ---------------------------------------------------------------------------
# Storage challenge (proof-of-storage)
# ---------------------------------------------------------------------------


class ChallengeRecord(BaseModel):
    challenge_id: str
    shard_id: str
    offsets: List[int]
    window_size: int = 32
    nonce: str
    expected_hash: str
    issued_at: str
    deadline: str
    responded_at: Optional[str] = None
    passed: Optional[bool] = None
    response_ms: Optional[float] = None


class ChallengeRequest(BaseModel):
    challenge_id: str
    shard_id: str
    offsets: List[int]
    window_size: int = 32
    nonce: str


class ChallengeResponse(BaseModel):
    challenge_id: str
    proof: str


# ---------------------------------------------------------------------------
# Peer contract
# ---------------------------------------------------------------------------


class PeerContract(BaseModel):
    contract_id: str
    peer_url: str
    peer_node_id: str
    tier: ContractTier = ContractTier.WARM
    shards_held_for_us: List[str] = Field(default_factory=list)
    shards_we_hold: List[str] = Field(default_factory=list)
    storage_contributed_bytes: int = 0
    storage_consumed_bytes: int = 0
    created_at: str
    status: ContractStatus = ContractStatus.ACTIVE
    violations: int = 0
    max_violations: int = 5
    qos: QoSMetrics = Field(default_factory=QoSMetrics)
    recent_challenges: List[ChallengeRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request / response helpers for contracts
# ---------------------------------------------------------------------------


class ContractCreateRequest(BaseModel):
    peer_url: str
    peer_node_id: str
    tier: ContractTier = ContractTier.WARM


class ContractSummary(BaseModel):
    contract_id: str
    peer_url: str
    peer_node_id: str
    tier: ContractTier
    status: ContractStatus
    storage_contributed_bytes: int
    storage_consumed_bytes: int
    storage_ratio: float = 0.0
    qos_score: float = 1.0
    violations: int = 0


class TierConfig(BaseModel):
    name: ContractTier
    challenge_interval_s: float
    max_response_s: float
    storage_multiplier: float
    challenge_positions: int
    window_size: int
    probation_threshold: float
    eviction_threshold: float
    max_violations: int
