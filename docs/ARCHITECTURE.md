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
│   └── models.py           ← Pydantic models (FileMetadata, UploadResponse, etc.)
├── lib/                    ← Go encoder/decoder binaries
│   ├── cmd/encoder/        ← Encoder source (splits file + computes parity)
│   ├── cmd/decoder/        ← Decoder source (reconstructs from shards)
│   ├── Makefile            ← Build with: cd lib && make
│   └── go.mod              ← Go module (uses local reedsolomon library)
├── reedsolomon/            ← Klaus Post's Reed-Solomon library (Go, vendored)
├── cfs_fuse.py             ← FUSE filesystem layer (mount as native directory)
├── cfs.py                  ← Original CLI prototype
├── mcp_server.py           ← MCP server for Claude Code integration
├── ui/                     ← React frontend
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
- **Key**: Single symmetric key per node, stored in `~/.collective/key`
- **Granularity**: Each shard encrypted independently with a random IV
- **Tamper detection**: HMAC-SHA256 verification on decrypt; corrupted shards raise `InvalidToken`

## API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/api/files` | GET | List all files |
| `/api/files/<id>` | GET | Get file metadata |
| `/api/files/upload` | POST | Upload file |
| `/api/files/<id>/download` | GET | Download file |
| `/api/files/<id>` | DELETE | Delete file and shards |
| `/api/stats` | GET | System statistics |
| `/api/peers` | GET | List known peers |
| `/api/peers/register` | POST | Register a new peer |
| `/api/peers/files` | GET | This node's files (for peer sync) |
| `/api/peers/chunks/<id>` | GET | Serve a single shard |
| `/api/network` | GET | Aggregate view (local + peer files) |
| `/api/status/stream` | GET | SSE status stream |
| `/ws` | WS | WebSocket status stream |
