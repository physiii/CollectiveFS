#!/usr/bin/env bash
# Install CollectiveFS auto-start:
#   * node  -> system LaunchDaemon (root) so it bypasses macOS Local Network
#              privacy and can trade over the LAN on boot.
#   * mount -> per-user LaunchAgent (fuse-t mounts in the user session).
# Idempotent. Uses sudo for the daemon.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${HOME}"
mkdir -p "$HOME_DIR/Library/LaunchAgents" "$HOME_DIR/Library/Logs"

render() { sed -e "s#@REPO@#$REPO#g" -e "s#@HOME@#$HOME_DIR#g" "$1"; }

# --- node: system LaunchDaemon ---
NODE_PLIST=/Library/LaunchDaemons/com.collectivefs.node.plist
render "$REPO/launchd/com.collectivefs.node.plist.in" | sudo tee "$NODE_PLIST" >/dev/null
sudo chown root:wheel "$NODE_PLIST"; sudo chmod 644 "$NODE_PLIST"
sudo launchctl bootout system "$NODE_PLIST" 2>/dev/null || true
sudo launchctl bootstrap system "$NODE_PLIST" 2>/dev/null || sudo launchctl load -w "$NODE_PLIST"
echo "loaded node (system LaunchDaemon)"

# --- mount: user LaunchAgent ---
MOUNT_PLIST="$HOME_DIR/Library/LaunchAgents/com.collectivefs.mount.plist"
render "$REPO/launchd/com.collectivefs.mount.plist.in" > "$MOUNT_PLIST"
launchctl unload "$MOUNT_PLIST" 2>/dev/null || true
launchctl load -w "$MOUNT_PLIST"
echo "loaded mount (user LaunchAgent)"
echo "Done."
