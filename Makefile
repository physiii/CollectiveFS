.PHONY: all build build-go build-ui build-docker install test test-unit test-eval \
        test-ui test-cluster test-all eval-mount clean

all: build

# ── Build ─────────────────────────────────────────────────────────────

build: build-go build-ui

build-ui:
	@echo "Building the console UI..."
	cd ui && npm install --silent && npm run build
	@echo "Done. Output: ui/dist (served by the API)"

build-go:
	@echo "Building encoder and decoder..."
	cd lib && $(MAKE) all
	@echo "Done. Binaries: lib/encoder lib/decoder"

build-docker:
	docker compose -f docker-compose.cluster.yml build

# ── Install ───────────────────────────────────────────────────────────

install:
	pip install -r requirements-test.txt

# ── Test ──────────────────────────────────────────────────────────────

test: test-unit

test-unit:
	python -m pytest tests/unit/ -v

test-eval: build-go
	python -m pytest tests/eval/ -v

# Playwright starts its own node against a throwaway store; the UI must be built.
test-ui: build-ui
	npx playwright test tests/e2e/browser.spec.js

# Full performance and evaluation report across the mounted cluster.
# Override NODES to point at a different fleet.
NODES ?= --node sonic=http://localhost:8010 --node office=http://192.168.1.43:8010@office
eval-mount:
	.venv/bin/python -m benchmarks.run_mount_eval $(NODES) \
	  --iterations 3 --op-iterations 12 --recon-iterations 5 \
	  --max-size 64MB --streams 8 --stream-size 8MB \
	  --real-tree /usr/include/python3.12 \
	  --degraded --contracts --saturate \
	  --report benchmarks/results/mount-eval.md \
	  --json benchmarks/results/mount-eval.json

test-cluster: build-go build-docker
	python -m pytest tests/cluster/ -v -m cluster --timeout=180

test-all: build-go
	python -m pytest tests/unit/ tests/eval/ -v

# ── Clean ─────────────────────────────────────────────────────────────

clean:
	cd lib && $(MAKE) clean
	rm -rf .pw-collective test-results tests/e2e/report
	docker compose -f docker-compose.cluster.yml down -v --remove-orphans 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
