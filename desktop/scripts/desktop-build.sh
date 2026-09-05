#!/usr/bin/env bash
# Build the CollectiveFS desktop bundles for Ubuntu (.deb + AppImage). Linux only.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${CARGO_HOME:-${HOME}/.cargo}/bin:${PATH}"
[ "$(uname -s)" = "Linux" ] || { echo "Linux only (use mac-build.sh on macOS)" >&2; exit 1; }
command -v cargo >/dev/null || { echo "cargo required" >&2; exit 1; }

cd "$DESKTOP_ROOT"
[ -d node_modules ] || npm install
npx --yes @tauri-apps/cli build --bundles deb appimage
echo "Bundles -> src-tauri/target/release/bundle"
