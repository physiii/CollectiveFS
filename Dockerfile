# ──────────────────────────────────────────────
# Stage 1: Build React UI
# ──────────────────────────────────────────────
FROM node:20-alpine AS ui-builder
WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json* ./
RUN npm install

COPY ui/ ./
RUN npm run build

# ──────────────────────────────────────────────
# Stage 2: Build Go encoder/decoder for Linux
# ──────────────────────────────────────────────
FROM golang:1.22-alpine AS go-builder
WORKDIR /build

# Copy Go source and vendored reedsolomon
COPY lib/ ./lib/
COPY reedsolomon/ ./reedsolomon/

# Build encoder and decoder directly (no make in alpine)
RUN cd lib/cmd/encoder && go build -o ../../encoder . \
    && cd ../../cmd/decoder && go build -o ../../decoder .

# ──────────────────────────────────────────────
# Stage 3: Python API + static UI
# ──────────────────────────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

# System deps for cryptography and general build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY api/ ./api/

# Go source (for reference) and Linux-built binaries
COPY lib/ ./lib/
COPY --from=go-builder /build/lib/encoder ./lib/encoder
COPY --from=go-builder /build/lib/decoder ./lib/decoder

# Built React frontend
COPY --from=ui-builder /app/ui/dist ./ui/dist

# Make binaries executable
RUN chmod +x ./lib/encoder ./lib/decoder

# Collective storage directories
RUN mkdir -p /data/.collective/proc \
             /data/.collective/cache \
             /data/.collective/public \
             /data/.collective/tree

ENV COLLECTIVE_PATH=/data/.collective
ENV ENCODER_PATH=/app/lib/encoder
ENV DECODER_PATH=/app/lib/decoder
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
