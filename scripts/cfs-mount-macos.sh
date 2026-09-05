#!/usr/bin/env bash
# Mount the local CollectiveFS node as a directory on macOS via fuse-t + fusepy.
#
#   scripts/cfs-mount-macos.sh [mountpoint] [-- extra cfs_mount_macos.py args]
#
# Defaults: mountpoint ~/cfs/.cfs, node http://localhost:8010, foreground.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNTPOINT="${1:-$HOME/cfs/.cfs}"
[[ $# -gt 0 ]] && shift || true
[[ "${1:-}" == "--" ]] && shift || true

# fuse-t ships a libfuse2-compatible dylib; fusepy loads it from this env var.
export FUSE_LIBRARY_PATH="${FUSE_LIBRARY_PATH:-/usr/local/lib/libfuse-t.dylib}"
if [[ ! -e "$FUSE_LIBRARY_PATH" ]]; then
  echo "fuse-t library not found at $FUSE_LIBRARY_PATH." >&2
  echo "Install it with:  brew install --cask fuse-t" >&2
  exit 1
fi

# Prefer the repo venv if present, else the system python3.
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

if ! "$PY" -c "import fuse" 2>/dev/null; then
  echo "fusepy is not installed for $PY." >&2
  echo "Install it with:  $PY -m pip install -r $REPO/requirements-macos.txt" >&2
  exit 1
fi

mkdir -p "$MOUNTPOINT"

# Wait for the local node before mounting — the mount adopts the node's account
# token and lists its tree at startup. Under launchd (boot/login) the node may
# still be coming up, so poll rather than fail.
API="${CFS_API:-http://localhost:8010}"
for _ in $(seq 1 60); do
  curl -s -m 3 "$API/api/health" >/dev/null 2>&1 && break
  sleep 2
done

# ttl=0 keeps directory metadata strongly consistent with the node, which git's
# lock/rename protocol needs. Override with CFS_MOUNT_TTL for lighter workloads.
echo "Mounting CollectiveFS at $MOUNTPOINT (Ctrl-C to unmount)…"
exec "$PY" "$REPO/cfs_mount_macos.py" "$MOUNTPOINT" --ttl "${CFS_MOUNT_TTL:-0}" "$@"
