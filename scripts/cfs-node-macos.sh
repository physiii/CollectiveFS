#!/usr/bin/env bash
# Run the CollectiveFS node natively on macOS (no Docker).
#
# On this VPN'd Mac, Docker Desktop's VM cannot route to the LAN subnet
# (192.168.1.0/24 is blackholed through GlobalProtect), so a containerised node
# can serve inbound but cannot announce/trade outbound. Run on the host, which
# has full LAN connectivity in both directions, and drive the fuse-t mount and
# peers from here.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
export COLLECTIVE_PATH="${COLLECTIVE_PATH:-$HOME/.collective}"
export ENCODER_PATH="${ENCODER_PATH:-$REPO/lib/encoder}"
export DECODER_PATH="${DECODER_PATH:-$REPO/lib/decoder}"
export OWN_URL="${CFS_OWN_URL:-http://$LAN_IP:8010}"
export PEER_URLS="${CFS_PEER_URLS:-http://192.168.1.40:8010,http://192.168.1.43:8010}"
export PEER_DISCOVERY_INTERVAL="${CFS_PEER_DISCOVERY_INTERVAL:-30}"
export AGENT_PROVIDER="${AGENT_PROVIDER:-builtin}"
export PORT=8010

echo "CollectiveFS node: OWN_URL=$OWN_URL  PEERS=$PEER_URLS  store=$COLLECTIVE_PATH"
cd "$REPO"
exec .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port "${PORT}"
