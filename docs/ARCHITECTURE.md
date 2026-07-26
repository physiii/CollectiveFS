# CollectiveFS Architecture

CollectiveFS is a distributed, peer-to-peer file storage system. Files are split into shards using Reed-Solomon erasure coding, encrypted with Fernet, and distributed across a network of untrusted peers.

## How it works

```
Upload:   File → Split into 8 data shards → Calculate 4 parity shards → Encrypt each shard → Store

Download: Collect shards → Decrypt → Reed-Solomon reconstruct → Original file
```

With 8 data + 4 parity shards, you can lose up to 4 shards and still recover the original file.

## Components

```
CollectiveFS/
├── api/                    ← FastAPI REST backend (the main service)
│   ├── main.py             ← All endpoints, background pipeline, peer discovery
│   ├── models.py           ← Pydantic models (FileMetadata, contracts, QoS, etc.)
│   ├── contracts.py        ← Peer contract engine (challenges, QoS, enforcement)
│   ├── config_service.py   ← Runtime config: validation, persistence, audit log
│   ├── system_service.py   ← Host + collective telemetry for the System section
│   ├── files_service.py    ← Folder tree over flat file metadata
│   └── agent_service.py    ← Pluggable agent backends + config-mutation protocol
├── lib/                    ← Go encoder/decoder binaries
│   ├── cmd/encoder/        ← Encoder source (splits file + computes parity)
│   ├── cmd/decoder/        ← Decoder source (reconstructs from shards)
│   ├── Makefile            ← Build with: cd lib && make
│   └── go.mod              ← Go module (uses local reedsolomon library)
├── reedsolomon/            ← Klaus Post's Reed-Solomon library (Go, vendored)
├── cfs_fuse.py             ← FUSE filesystem layer (mount as native directory)
├── cfs.py                  ← Original CLI prototype
├── mcp_server.py           ← MCP server for Claude Code integration
├── ui/                     ← React console (Files + System sections)
├── tests/                  ← Test suite (see docs/TESTING.md)
├── Dockerfile              ← Multi-stage build (Node.js UI + Python runtime)
├── docker-compose.yml      ← Single-node Docker setup
└── docker-compose.cluster.yml ← 3-node cluster setup
```

## Data flow

### Upload pipeline

1. **Client** sends file via `POST /api/files/upload` (multipart form)
2. **API** saves to temp file, assigns UUID, returns immediately with `status: processing`
3. **Background task** runs:
   - `lib/encoder -data 8 -par 4 -out <proc_dir> <file>` splits into 12 shards
   - Each shard encrypted in-place with Fernet (AES-128-CBC + HMAC-SHA256)
   - Metadata JSON written to `~/.collective/tree/<file_id>.json`
4. **Status** broadcast via WebSocket and SSE to connected clients

### Download pipeline

1. **Client** requests `GET /api/files/<id>/download`
2. **API** reads metadata, locates shards in `~/.collective/proc/<file_id>/`
3. **Decoder** runs Reed-Solomon reconstruction (tolerates up to 4 missing shards)
4. **Decrypted** file streamed to client

### Storage layout

```
~/.collective/
├── key              ← Fernet encryption key (generated once)
├── tree/            ← File metadata JSONs
│   └── <file_id>.json
├── proc/            ← Encrypted shards
│   └── <file_id>/
│       ├── file.bin.0   (data shard)
│       ├── file.bin.1
│       ├── ...
│       └── file.bin.11  (parity shard)
├── cache/           ← Reconstructed files (temporary)
├── contracts/       ← Peer contract JSONs
│   └── <contract_id>.json
└── public/          ← Reserved for future use
```

## Cluster architecture

A cluster is N nodes, each running the same API server. Nodes discover each other via environment variables and announce at startup.

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  node1   │────▶│  node2   │────▶│  node3   │
│ :8001    │◀────│ :8002    │◀────│ :8003    │
└──────────┘     └──────────┘     └──────────┘
     │                │                │
     ▼                ▼                ▼
  Volume 1         Volume 2         Volume 3
  (isolated)       (isolated)       (isolated)
```

Each node:
- Has its own isolated storage volume
- Knows its peers via `PEER_URLS` env var
- Registers with peers at startup (`POST /api/peers/register`)
- Exposes its files to peers (`GET /api/peers/files`)
- Can serve individual shards to peers (`GET /api/peers/chunks/<id>`)

### Erasure coding and fault tolerance

Default: **8 data shards + 4 parity shards = 12 total**

| Shards missing | Can reconstruct? |
|----------------|------------------|
| 0              | Yes              |
| 1-4            | Yes              |
| 5+             | No               |

With 3 nodes each holding ~4 shards, losing 1 node = losing ~4 shards = exactly at the tolerance boundary.

## Encryption

- **Algorithm**: Fernet (AES-128-CBC + HMAC-SHA256)
- **Key**: Single symmetric key per node, generated on first use at `~/.collective/key`
- **Granularity**: Each shard encrypted independently with a random IV
- **Tamper detection**: HMAC-SHA256 verification on decrypt; corrupted shards raise `InvalidToken`

## Peer contracts

Peers enter bilateral **contracts** that govern storage obligations, challenge frequency, and eviction rules. Each contract specifies a tier:

| Tier | Challenge Interval | Response Deadline | Storage Multiplier | Max Violations |
|------|-------------------|-------------------|-------------------|---------------|
| **HOT** | 30 s | 1 second | 2.0x | 3 |
| **WARM** | 5 min | 60 seconds | 1.0x | 5 |
| **COLD** | 1 hour | 1 hour | 0.5x | 10 |

### Proof-of-storage challenges

Challenges verify that a peer actually holds the shard it claims to store:

1. Challenger picks N random byte offsets in the shard
2. Sends `{shard_id, offsets, window_size, nonce}` to peer
3. Peer reads bytes at those positions, returns `HMAC-SHA256(nonce, bytes)`
4. Challenger verifies against its own local copy

A nonce prevents replay attacks; random offsets prevent pre-computation.

### QoS scoring

Each contract tracks a composite score (0.0 – 1.0):

| Component | Weight | Measures |
|-----------|--------|----------|
| Challenge pass rate | 40% | How often proofs verify correctly |
| Availability | 25% | Uptime ping success rate |
| Latency | 20% | Response time vs. tier deadline |
| Storage ratio | 15% | Contributed vs. consumed per tier multiplier |

### Enforcement state machine

```
ACTIVE ──low score──→ PROBATION ──recovers──→ ACTIVE
                          │
                    low score / max violations
                          ↓
                      SUSPENDED ──recovers──→ PROBATION
                          │
                      still bad
                          ↓
                       EVICTED (terminal → drop their shards)
```

When a peer is **evicted**, all shards held for that peer are deleted (reciprocal eviction). If they drop your chunks, their challenges fail, their score tanks, and their shards get dropped in return.

## API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/api/files` | GET | List all files |
| `/api/files/tree` | GET | Full folder hierarchy + every file entry |
| `/api/files/browse?path=` | GET | One folder's direct children + breadcrumbs |
| `/api/files/<id>` | GET | Get file metadata |
| `/api/files/<id>` | PATCH | Rename and/or move a file |
| `/api/files/upload` | POST | Upload file (optional `folder` form field) |
| `/api/files/<id>/download` | GET | Download file |
| `/api/files/<id>` | DELETE | Delete file and shards |
| `/api/folders` | POST | Create a folder |
| `/api/folders?path=` | DELETE | Forget a folder (its files move to the root) |
| `/api/stats` | GET | System statistics |
| `/api/system/overview` | GET | Host + collective telemetry for the System section |
| `/api/config` | GET | Current configuration and its schema |
| `/api/config` | PUT | Apply dotted-path configuration updates |
| `/api/config/audit` | GET | Recent configuration changes |
| `/api/agent/providers` | GET | Agent backends and which is active |
| `/api/chat` | POST | Section chat turn; may apply a configuration change |
| `/api/peers` | GET | List known peers |
| `/api/peers/register` | POST | Register a new peer |
| `/api/peers/files` | GET | This node's files (for peer sync) |
| `/api/peers/chunks/<id>` | GET | Serve a single shard |
| `/api/peers/shards` | POST | Accept a shard stored on behalf of a peer |
| `/api/peers/shards` | GET | What this node stores for other nodes |
| `/api/peers/shards/<node>/<file>/<n>` | GET | Return a stored shard to its owner |
| `/api/peers/shards/<node>/<file>` | DELETE | Drop shards held for a peer's file |
| `/api/network` | GET | Aggregate view (local + peer files) |
| `/api/contracts/tiers` | GET | List tier configurations |
| `/api/contracts` | GET/POST | List or create peer contracts |
| `/api/contracts/<id>` | GET/DELETE | Get or remove a contract |
| `/api/contracts/<id>/tier` | PATCH | Change contract tier |
| `/api/contracts/<id>/evict` | POST | Manually evict a peer |
| `/api/contracts/<id>/shards/theirs` | POST | Register shard a peer holds for us |
| `/api/contracts/<id>/shards/ours` | POST | Register shard we hold for a peer |
| `/api/contracts/<id>/challenge` | POST | Issue proof-of-storage challenge |
| `/api/contracts/challenge/respond` | POST | Respond to incoming challenge |
| `/api/contracts/health/summary` | GET | Network-wide contract health |
| `/api/status/stream` | GET | SSE status stream |
| `/ws` | WS | WebSocket status stream |

## The console

The web UI is a section console rather than a conventional file-manager chrome.
The root page is a stack of section cards; each card has three views behind one
toggle — **dashboard**, **chat**, and **skill** — plus a collapse control.
Clicking a section title opens it full-page.

```
/                     ← section cards (Files, then System & Infrastructure)
/sections/files       ← full-page file explorer
/sections/system      ← full-page infrastructure view
```

### Sections and their skills

Every section is fronted by at least one skill document (`ui/src/lib/skillDocs.js`).
The skill is not decoration: it is the contract shown in the skill view *and*
the brief the section's agent is prompted against, so what an operator reads is
what the agent follows.

| Section | Skill | Call sign | Owns |
|---------|-------|-----------|------|
| **Files** | `files` | Archivist | Folder tree, file placement, shard health, upload/download |
| **System & Infrastructure** | `system` | Infrastructure Steward | Compute, memory, network, quota, durability, peers — and node configuration |

### Files

The explorer is the primary surface. A persistent tree on the left, breadcrumbs
and list/grid views on the right, and per-file shard availability shown beside
size — because on an untrusted network *recoverable* matters more than *stored*.
Folder position lives in the URL (`?path=a/b`) so a view is linkable.

Folders are derived from each file's `folder` field, unioned with an explicit
`folders.json` so an empty folder still exists. Paths are normalised and
traversal (`..`), control characters, over-deep nesting and sibling name
collisions are all rejected before anything is written.

### System & Infrastructure

`/api/system/overview` deliberately mirrors Custodian's payload shape
(`ResourceGauge`, `DiskUsage`, network counters) so the same meters and charts
render against either service, then adds what only CollectiveFS knows: quota
headroom, shard durability, and the erasure fault budget.

Storage is reported against the **pledged quota**, not the raw filesystem — the
quota is what this node has promised the network. Bandwidth charts sum only
physical links; bridge and veth traffic also crosses them and would be
double-counted.

## Shard distribution

Encoding produces `<base>.0 … <base>.N` plus a `<base>.size` sidecar the decoder
needs. Once a file is encoded and encrypted, its shards are placed across this
node and its healthy peers.

The placement rule is the safety property:

> **No peer may hold more than `parity_shards` of a file.**

With the 8+4 default and one peer, that means 8 shards stay here and 4 go to the
peer — losing the entire peer costs exactly the fault budget, and the file still
reconstructs. The `.size` sidecar never leaves the origin.

```
upload on node A (8+4)
  ├── 8 shards   → node A
  └── 4 shards   → node B          node B can vanish; A still rebuilds the file
```

### Hand-off is verified before anything is dropped

1. The origin POSTs the shard to `POST /api/peers/shards`.
2. The peer writes it under `proc/_peers/<origin_node>/<file_id>/` and replies
   with the SHA-256 of what it actually stored.
3. Only if that digest matches the origin's does the origin drop its local copy
   and record `peer` + `digest` on the chunk.

A failed or mismatched hand-off leaves the shard where it was, so the worst case
is "less distributed than intended", never data loss. Set
`peers.keep_local_copy` to keep the origin's copy as well.

### Reads reassemble transparently

`GET /api/files/<id>/download` stages every shard into a temp directory — local
ones read off disk, remote ones fetched from the peer recorded on the chunk and
checked against their digest — decrypts them, then runs the decoder there.
Decoding in place would not work: shards are encrypted at rest, and the decoder
reads raw files, so it would happily reconstruct garbage from ciphertext.

Deleting a file asks every peer holding one of its shards to drop it first. A
peer that is down keeps an orphan, which is encrypted and useless without the
origin's key.

## Encryption

Every shard is encrypted with Fernet before it is stored, using a key generated
on first use at `$COLLECTIVE_PATH/key`. This is not optional: shards are handed
to peers the origin does not control, so plaintext at rest would defeat the
premise. The key never leaves the node — a peer stores ciphertext it cannot read
and hands the same bytes back on request.

## Configuration

Runtime configuration lives in `$COLLECTIVE_PATH/config.json` and is the source
of truth for quota, erasure parameters, upload limits, contract defaults and the
agent provider. Environment variables seed it on first boot; after that the
stored file wins for sizes and shapes (the agent provider keeps following the
environment so a compose file stays authoritative for deployment).

Every change goes through one validated path:

- values are coerced (`"500GB"`, `1024`, `"off"`, `"cold"` all work),
- per-field bounds are checked,
- cross-field rules are checked (a quota may not exceed the real filesystem, the
  reserve must stay below the quota, shard totals are capped),
- a rejected batch writes **nothing**,
- an accepted batch is appended to `config-audit.jsonl` with before/after values,
  its source and its actor.

Erasure changes apply to *subsequent* uploads. Files already encoded keep the
layout they were written with, so lowering parity never retroactively weakens
stored data — it narrows the fault budget for what comes next.

## Agent backends

Section chats are driven by a local CLI, selected at runtime:

| Provider | Command | Notes |
|----------|---------|-------|
| `codewhale` | `codewhale exec` | Default |
| `claude` | `claude -p` | |
| `codex` | `codex exec` | |
| `builtin` | — | Deterministic in-process interpreter, no LLM |

Switching provider is a configuration change (`agent.provider`), so it needs no
code change and persists across restarts. If the selected CLI is missing, the
turn falls back to `builtin` and the UI says so rather than failing silently.

### Changing the node from chat

The System agent is briefed with the live state, the current configuration and
the writable-field schema. To make a change it ends its reply with:

```
ACTION:{"type":"config.update","payload":{"storage.quota_bytes":"500GB"}}
```

The server parses that, applies it through the validated path above, and returns
the resulting diff, which the UI renders inline in the chat log. `builtin`
implements the same protocol by mapping plain-language instructions
("allocate 500GB", "set parity shards to 6", "disable challenges",
"increase space by 100GB") onto the same fields — which is why configuration
edits keep working, and stay testable, with no model available.
