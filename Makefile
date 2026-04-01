.PHONY: all build build-go build-docker install test test-unit test-eval test-cluster test-all clean

all: build

# ── Build ─────────────────────────────────────────────────────────────

build: build-go

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

test-cluster: build-go build-docker
	python -m pytest tests/cluster/ -v -m cluster --timeout=180

test-all: build-go
	python -m pytest tests/unit/ tests/eval/ -v

# ── Clean ─────────────────────────────────────────────────────────────

clean:
	cd lib && $(MAKE) clean
	docker compose -f docker-compose.cluster.yml down -v --remove-orphans 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
