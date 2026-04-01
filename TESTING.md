# CollectiveFS Testing Guide

## Quick Reference

| Test Tier | Command | Tests | Time | Docker? |
|-----------|---------|-------|------|---------|
| **Unit** | `make test-unit` | 102 pass | ~3.5s | No |
| **Eval** | `make test-eval` | 33 pass, 3 skip | ~2s | No |
| **Benchmarks** | `python benchmarks/run_all.py` | Full suite | ~45s | No |
| **e2e API** | `pytest tests/e2e/test_api.py -v` | 9 pass | ~0.5s | Yes |
| **Playwright UI** | `npx playwright test --project=chromium` | 54 pass, 2 skip | ~1.5 min | Yes |
| **Cluster** | `pytest tests/cluster/ -v -m cluster --timeout=180` | 39 pass, 6 known failures | ~4.5 min | Yes (3-node) |

## Recommended Test Strategies

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
# wait for healthy
curl -sf http://localhost:8000/api/health
source .venv/bin/activate
pytest tests/e2e/test_api.py -v
npx playwright test --project=chromium
```

**Full cluster (~4.5 min):**
```bash
docker compose -f docker-compose.cluster.yml up -d
# wait for all 3 nodes healthy
source .venv/bin/activate
pytest tests/cluster/ -v -m cluster --timeout=180
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
