#!/usr/bin/env bash
# Build a universal macOS CollectiveFS.app + .dmg.
set -euo pipefail
DESKTOP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${CARGO_HOME:-${HOME}/.cargo}/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"
[ "$(uname -s)" = "Darwin" ] || { echo "macOS only" >&2; exit 1; }
command -v cargo >/dev/null || { echo "cargo required" >&2; exit 1; }
command -v node  >/dev/null || { echo "node required"  >&2; exit 1; }
xcrun --show-sdk-path >/dev/null 2>&1 || { echo "install Xcode CLT" >&2; exit 1; }

cd "$DESKTOP_ROOT"
[ -d node_modules ] || npm install
rustup target add aarch64-apple-darwin x86_64-apple-darwin >/dev/null 2>&1 || true
npx --yes @tauri-apps/cli build --target universal-apple-darwin --bundles app dmg
echo "Bundles -> src-tauri/target/universal-apple-darwin/release/bundle"
