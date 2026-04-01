# Testing CollectiveFS

## Quick reference

```bash
# Run all local tests (no Docker needed)
make test-all

# Run just unit tests (fastest, no binaries needed)
make test

# Run evaluation tests (needs Go binaries)
make test-eval

# Run cluster tests (needs Docker)
make test-cluster
```

## Test structure

```
tests/
├── unit/                     ← Fast, no dependencies beyond Python
│   ├── test_crypto.py        ← Fernet encryption/decryption (20 tests)
│   └── test_metadata.py      ← File/chunk metadata schemas (7 tests)
├── eval/                     ← Requires lib/encoder + lib/decoder binaries
│   ├── test_pipeline.py      ← Full encode → encrypt → decrypt → decode (10 tests)
│   ├── test_durability.py    ← Shard deletion and RS reconstruction (10 tests)
│   ├── test_integrity.py     ← SHA-256 preservation, tamper detection (6 tests)
│   └── test_throughput.py    ← Performance benchmarks (7 tests)
├── e2e/                      ← Requires running API server
│   ├── test_api.py           ← REST API CRUD operations (9 tests)
│   └── browser.spec.js       ← Playwright browser tests
├── fuse/                     ← Requires pyfuse3
│   └── test_fuse_ops.py      ← FUSE filesystem operations (8 tests)
└── cluster/                  ← Requires Docker Compose
    ├── test_multinode.py     ← Cluster health, peer discovery, metadata (28 tests)
    ├── test_data_integrity.py← Upload → download → SHA-256 verify (7 tests)
    └── test_node_drop.py     ← Progressive node drop until corruption (10 tests)
```

## Running tests

### Unit tests

No setup needed. These test cryptography and metadata logic in pure Python.

```bash
python -m pytest tests/unit/ -v
```

### Evaluation tests

These test the full encode/decode pipeline using the Go binaries.

```bash
# Build the binaries first
make build

# Run evaluation tests
python -m pytest tests/eval/ -v
```

If the binaries aren't built, these tests skip automatically.

### E2E API tests

These need a running API server. Uploads are processed asynchronously, so the
tests poll for completion before asserting.

```bash
# In one terminal, start the server:
python -m uvicorn api.main:app --port 8000

# In another terminal, run the tests:
python -m pytest tests/e2e/test_api.py -v -m api
```

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
