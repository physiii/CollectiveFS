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
# Stage 2: Python API + static UI
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

# Encoder/decoder binaries (if they exist)
COPY lib/ ./lib/

# Built React frontend
COPY --from=ui-builder /app/ui/dist ./ui/dist

# Make binaries executable (ignore errors if they don't exist)
RUN chmod +x ./lib/encoder ./lib/decoder 2>/dev/null || true

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
