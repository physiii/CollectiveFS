#!/usr/bin/env bash
# Refresh the CollectiveFS mirror of a bare git repo.
#
# git's lock/rename protocol cannot run reliably on the CFS mount (async,
# erasure-coded, peer-unioned store), so the push target is a local authoritative
# bare repo and this script mirrors its packed contents into the CFS namespace,
# where they are chunked, encrypted and traded across the mesh — and cloneable.
#
#   scripts/cfs-journal-sync.sh [SRC_BARE_REPO] [DST_ON_MOUNT]
set -euo pipefail
SRC="${1:-$HOME/cfs/.git-remotes/journal.git}"
DST="${2:-$HOME/cfs/.cfs/git-repos/journal}"

[ -d "$SRC" ] || { echo "source repo not found: $SRC" >&2; exit 1; }
mountpoint -q "$(dirname "$(dirname "$DST")")" 2>/dev/null || \
  mount | grep -q "$HOME/cfs/.cfs" || { echo "CFS mount not present at ~/cfs/.cfs" >&2; exit 1; }

echo "Packing $SRC …"
git -C "$SRC" gc --quiet

echo "Mirroring into $DST …"
mkdir -p "$DST/objects" "$DST/refs" "$DST/info"
# Static files only — create/overwrite, never rename/unlink on the mount.
cp "$SRC/HEAD" "$DST/HEAD"
cp "$SRC/config" "$DST/config"
[ -f "$SRC/packed-refs" ] && cp "$SRC/packed-refs" "$DST/packed-refs" || true
cp -R "$SRC/objects/." "$DST/objects/" 2>/dev/null || cp -R "$SRC/objects/." "$DST/objects/"
[ -d "$SRC/refs" ] && cp -R "$SRC/refs/." "$DST/refs/" 2>/dev/null || true
[ -f "$SRC/info/exclude" ] && cp "$SRC/info/exclude" "$DST/info/exclude" || true

echo "Done. Clone/pull with:  git clone $DST"
