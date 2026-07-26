# Testing CollectiveFS

## Quick reference

| Test Tier | Command | Tests | Time | Docker? |
|-----------|---------|-------|------|---------|
| **Unit** | `make test-unit` | 245 pass | ~1.5s | No |
| **Eval** | `make test-eval` | 33 pass, 3 skip | ~2s | No |
| **Benchmarks** | `python benchmarks/run_all.py` | Full suite | ~45s | No |
| **e2e API** | `pytest tests/e2e/test_api.py -v -m api` | 27 pass | ~4s | No (needs a running node) |
| **Playwright UI** | `npx playwright test --project=chromium` | 26 pass | ~25s | No (starts its own node) |
| **Cluster** | `pytest tests/cluster/ -v -m cluster --timeout=180` | 39 pass, 6 known failures | ~4.5 min | Yes (3-node) |

The unit tier covers the storage engine (`test_contracts`, `test_crypto`,
`test_metadata`) and the console services behind the UI: `test_config_service`
(validation, persistence, audit), `test_files_service` (folder tree, path
safety, move/rename), `test_system_service` (quota accounting, shard
durability, telemetry shape), `test_agent_service` (provider registry, the
`ACTION` protocol, and the deterministic interpreter), and `test_replication`
(shard placement — that no peer ever exceeds the parity budget).

```bash
# Run all local tests (no Docker needed)
make test-all

# Run just unit tests (fastest, no binaries needed)
make test

# Run evaluation tests (needs Go binaries)
make test-eval

# Run bunny video benchmarks (needs Go binaries + test fixture)
python -m pytest tests/eval/test_bunny_benchmark.py -v -s

# Run Playwright browser tests. The config starts the API itself (which also
# serves ui/dist) against a throwaway .pw-collective store — build the UI first.
(cd ui && npm run build)
npx playwright test tests/e2e/browser.spec.js --project=chromium

# Point either browser or API tests at a node on a different port
CFS_TEST_PORT=8123 npx playwright test --project=firefox
CFS_API_URL=http://localhost:8123 pytest tests/e2e/test_api.py -v -m api

# Run cluster tests (needs Docker)
make test-cluster
```

## Recommended test strategies

**Smoke check (~3s, no Docker):**
```bash
make test-unit && make test-eval
```

**Full local validation (~45s, no Docker):**
```bash
make test-unit && make test-eval && python benchmarks/run_all.py
```

**Full e2e with Docker (~2.5 min):**
```bash
docker compose -f docker-compose.yml up -d
curl -sf http://localhost:8000/api/health
source .venv/bin/activate
pytest tests/e2e/test_api.py -v
npx playwright test --project=chromium
```

**Full cluster (~4.5 min):**
```bash
docker compose -f docker-compose.cluster.yml up -d
source .venv/bin/activate
pytest tests/cluster/ -v -m cluster --timeout=180
```

## Test structure

```
tests/
├── unit/                     ← Fast, no dependencies beyond Python
│   ├── test_crypto.py        ← Fernet encryption/decryption (20 tests)
│   ├── test_metadata.py      ← File/chunk metadata schemas (7 tests)
│   ├── test_contracts.py     ← Peer contract system (75 tests)
│   ├── test_config_service.py← Config validation, persistence, audit (34 tests)
│   ├── test_files_service.py ← Folder tree, path safety, move/rename (35 tests)
│   ├── test_system_service.py← Quota, durability, telemetry shape (16 tests)
│   ├── test_agent_service.py ← Providers, ACTION protocol, interpreter (42 tests)
│   └── test_replication.py   ← Shard placement and the parity budget (13 tests)
├── eval/                     ← Requires lib/encoder + lib/decoder binaries
│   ├── test_pipeline.py      ← Full encode → encrypt → decrypt → decode (10 tests)
│   ├── test_durability.py    ← Shard deletion and RS reconstruction (10 tests)
│   ├── test_integrity.py     ← SHA-256 preservation, tamper detection (6 tests)
│   ├── test_throughput.py    ← Performance benchmarks (7 tests)
│   └── test_bunny_benchmark.py ← Bunny video: throughput, corruption threshold, parity comparison (3 tests)
├── e2e/                      ← Requires running API server
│   ├── test_api.py           ← REST API: files, folders, system, config, chat (27 tests)
│   └── browser.spec.js       ← Playwright console tests (26 per engine)
├── fuse/                     ← Requires pyfuse3
│   └── test_fuse_ops.py      ← FUSE filesystem operations (8 tests)
└── cluster/                  ← Requires Docker Compose
    ├── test_multinode.py     ← Cluster health, peer discovery, metadata (28 tests)
    ├── test_data_integrity.py← Upload → download → SHA-256 verify (7 tests)
    └── test_node_drop.py     ← Progressive node drop until corruption (10 tests)
```

## Running tests

### Unit tests

No setup needed. These test cryptography, metadata, and peer contracts in pure Python.

```bash
python -m pytest tests/unit/ -v
```

Contract tests cover: tier configuration, proof-of-storage challenge generation/verification, QoS scoring, storage accounting, enforcement state machine (active → probation → suspended → evicted), reciprocal eviction, and full lifecycle simulations.

### Evaluation tests

These test the full encode/decode pipeline using the Go binaries.

```bash
# Build the binaries first
make build

# Run evaluation tests
python -m pytest tests/eval/ -v
```

If the binaries aren't built, these tests skip automatically.

### Bunny video benchmarks

These use a 1 MB Big Buck Bunny 1080p video clip to measure real-world
pipeline performance and find the exact corruption threshold.

```bash
# Download the test fixture (1 MB clip)
mkdir -p tests/fixtures
curl -L -o tests/fixtures/bunny_1080p.mp4 \
  'https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4'

# Build binaries if not already built
make build

# Run benchmarks (use -s to see the output tables)
python -m pytest tests/eval/test_bunny_benchmark.py -v -s
```

The benchmark produces three reports:

1. **Pipeline throughput** — encode + encrypt + decrypt + decode timing with
   MB/s rates and SHA-256 verification
2. **Corruption threshold** — progressively deletes 0–12 shards and reports
   INTACT / CORRUPTED / UNRECOVERABLE for each count, finding the exact point
   where data loss begins
3. **Parity comparison** — compares RS configs (4+2, 8+4, 6+6, 4+8) showing
   max shard loss, fault tolerance %, storage overhead %, and encode time

If the bunny video isn't present, these tests skip automatically.

### E2E API tests

These need a running API server. Uploads are processed asynchronously, so the
tests poll for completion before asserting.

```bash
# In one terminal, start the server:
python -m uvicorn api.main:app --port 8000

# In another terminal, run the tests:
python -m pytest tests/e2e/test_api.py -v -m api
```

### Playwright browser tests

These drive the console against a live node: both sections, the file explorer,
the live charts, and the section chat's ability to change node configuration.

```bash
# Install Playwright (one-time setup)
npm install @playwright/test
npx playwright install chromium

# Option 1: Playwright starts the node itself (uses playwright.config.js).
# It serves ui/dist, so build the UI first.
make test-ui

# Option 2: pick a port and/or engine
CFS_TEST_PORT=8123 npx playwright test tests/e2e/browser.spec.js --project=chromium
```

The suite covers 26 tests per engine (chromium and firefox), grouped by surface:

| Suite | Tests | What it covers |
|-------|-------|----------------|
| Console shell | 3 | Section order, dashboard/chat/skill tri-view, collapse persistence |
| Files explorer | 7 | Tree/row/breadcrumb navigation, list and grid views, recursive search, shard map, rename + move, folder create/remove, delete |
| System & Infrastructure | 3 | All six telemetry panels, live charts, erasure state |
| Configuration | 4 | Quick controls write through, invalid quota and shard counts rejected, audit log |
| Section chat | 8 | Provider switcher, provider persistence, quota/erasure/relative/boolean changes, refusal, question answering, inline diff rendering |

Chat tests pin the `builtin` provider so the suite is deterministic and needs no
model. The CLI providers are exercised separately — see *Agent backends* in
`docs/ARCHITECTURE.md`.

Fixtures use unique filenames, so the suite is safe to run repeatedly against a
node that already holds data.

### Cluster tests

These spin up a 3-node Docker cluster. They take longer (~2 minutes).

```bash
# Build images and run tests
make test-cluster

# Or manually:
docker compose -f docker-compose.cluster.yml up -d --build
python -m pytest tests/cluster/ -v -m cluster --timeout=180
docker compose -f docker-compose.cluster.yml down -v
```

### FUSE tests

Require `pyfuse3` and Linux with FUSE support. Skipped automatically if unavailable.

```bash
pip install pyfuse3
python -m pytest tests/fuse/ -v
```

## Key test scenarios

### Bunny video benchmarks

`tests/eval/test_bunny_benchmark.py` runs three benchmarks against the 1 MB
Big Buck Bunny video clip:

**Pipeline throughput** (typical results on a standard machine):

| Stage | Throughput |
|-------|-----------|
| Encode (RS 8+4) | ~3 MB/s |
| Encrypt (Fernet AES) | ~20 MB/s |
| Decrypt (Fernet AES) | ~190 MB/s |
| Decode (RS 8+4) | ~4 MB/s |
| **Full pipeline** | **~1.7 MB/s** |

**Corruption threshold** with RS(8,4) — 8 data + 4 parity = 12 total shards:

| Shards deleted | Result |
|---------------|--------|
| 0–4 | INTACT (SHA-256 matches original) |
| 5+ | UNRECOVERABLE (decoder fails cleanly) |

There is **no silent corruption** — the decoder either produces exact bytes or
fails outright. Fault tolerance = 33% of shards can be lost.

**Parity comparison** across configurations:

| Config | Max shard loss | Fault tolerance | Storage overhead |
|--------|---------------|----------------|-----------------|
| 4+2 | 2 | 33% | 50% |
| 8+4 | 4 | 33% | 50% |
| 6+6 | 6 | 50% | 100% |
| 4+8 | 8 | 67% | 200% |

### Download integrity

`tests/e2e/browser.spec.js` (Suite 9: Download Integrity) verifies that the
full upload → encode → store → decode → download pipeline preserves files
byte-for-byte:

1. Uploads a file (binary content or the bunny video)
2. Waits for RS encoding to complete
3. Downloads via the API (`GET /api/files/:id/download`)
4. Computes SHA-256 hash of the downloaded content in the browser
5. Asserts the hash matches the original exactly

This catches padding bugs, shard naming mismatches, and decoder path issues.

### Durability: shard deletion

`tests/eval/test_durability.py` physically deletes shard files and verifies Reed-Solomon reconstruction:

- **0-4 shards deleted**: reconstruction succeeds
- **5+ shards deleted**: reconstruction fails
- **50-trial random simulation**: random combinations of 0-4 deleted shards all succeed
- **Encrypted pipeline**: encrypt → delete shards → decrypt → reconstruct

### Node drop until corruption

`tests/cluster/test_node_drop.py` is the definitive distributed durability test:

1. Uploads files to the cluster
2. Progressively stops nodes one at a time
3. After each stop, verifies which files are still accessible
4. Deletes shards inside containers to simulate distributed loss
5. Records the exact threshold where data becomes unrecoverable

### Data integrity end-to-end

`tests/cluster/test_data_integrity.py` verifies the full loop:

1. Upload a file with known content
2. Download it back
3. Compare SHA-256 hash — must match exactly
4. Tests multiple sizes: 1 KB, 256 KB
5. Tests concurrent uploads (5 files at once)

## Pytest markers

| Marker | Meaning |
|--------|---------|
| `unit` | Fast unit tests |
| `integration` | Tests requiring encoder/decoder binaries |
| `eval` | Evaluation/benchmark tests |
| `fuse` | Tests requiring FUSE mount capability |
| `slow` | Tests that take > 5 seconds |
| `api` | API tests requiring a running server on port 8000 |
| `cluster` | Multi-node Docker cluster tests |

Filter by marker:

```bash
python -m pytest -m "unit" -v          # only unit tests
python -m pytest -m "not slow" -v      # skip slow tests
python -m pytest -m "cluster" -v       # only cluster tests
```

## Prerequisites

### Python (unit, eval, benchmarks, e2e API, cluster)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pip install rich numpy psutil   # for benchmarks
```

### Go (encoder/decoder binaries)
```bash
make build
```

### Playwright (browser UI tests)
```bash
npm install
npx playwright install chromium
```

### Docker (e2e, cluster)
```bash
# Single node
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d

# 3-node cluster
docker compose -f docker-compose.cluster.yml build
docker compose -f docker-compose.cluster.yml up -d
```

## Notes

- **Bunny video tests** (2 skipped in Playwright): require `tests/fixtures/bunny_1080p.mp4` fixture file
- **Cluster known failures** (6): 3 are cross-node visibility timing issues in peer discovery; 3 are shard count assertions expecting 12 but getting 13 (metadata file counted as a chunk)
- The Dockerfile includes a multi-stage Go build so encoder/decoder binaries compile for the correct architecture inside the container
- macOS (Apple Silicon) and Linux are both supported

## Troubleshooting

**Tests skip with "lib/encoder not found"**

Build the Go binaries: `make build`

**Cluster tests fail to start**

Check Docker is running: `docker info`
Check ports 8001-8003 are free: `ss -tlnp | grep 800`

**Docker mount errors on network filesystems**

Docker volume mounts (e.g. `./lib:/app/lib:ro`) fail if the repo lives on
SSHFS, NFS, or other FUSE mounts because the Docker daemon runs as root and
cannot access user-space mounts. Clone the repo to a local filesystem instead.

**Pytest collection error with pytest-asyncio**

Install the compatible version: `pip install 'pytest-asyncio==0.23.3' 'pytest>=7,<8'`
