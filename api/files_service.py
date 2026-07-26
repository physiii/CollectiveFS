"""Folder tree over the flat file metadata that CollectiveFS stores.

Every tree JSON carries an optional ``folder`` path. This module turns that flat
set into a navigable hierarchy for the Files explorer, and keeps a small
``folders.json`` so a folder that has been created but not yet filled still
shows up.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.RLock()

# Anything that would let a folder name escape its parent or confuse a path join.
_ILLEGAL = re.compile(r"[\x00-\x1f<>:\"\\|?*]")


class FileTreeError(ValueError):
    """Raised for invalid folder paths or conflicting names."""


def normalize_folder(raw: Optional[str]) -> str:
    """Return a clean ``a/b/c`` path. Root is the empty string."""
    if raw is None:
        return ""
    text = str(raw).strip().strip("/")
    if not text or text == ".":
        return ""
    parts: List[str] = []
    for segment in text.split("/"):
        segment = segment.strip()
        if not segment or segment == ".":
            continue
        if segment == "..":
            raise FileTreeError("folder paths may not contain '..'")
        if _ILLEGAL.search(segment):
            raise FileTreeError(f"illegal characters in folder segment {segment!r}")
        if len(segment) > 128:
            raise FileTreeError("folder segments must be 128 characters or fewer")
        parts.append(segment)
    if len(parts) > 24:
        raise FileTreeError("folder nesting is limited to 24 levels")
    return "/".join(parts)


def _parent_of(folder: str) -> str:
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def _basename(folder: str) -> str:
    return folder.rsplit("/", 1)[-1]


class FolderStore:
    """Explicit folders, so empty ones persist across restarts."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "folders.json"

    def load(self) -> List[str]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        out: List[str] = []
        for item in data:
            try:
                folder = normalize_folder(item)
            except FileTreeError:
                continue
            if folder and folder not in out:
                out.append(folder)
        return out

    def save(self, folders: List[str]) -> List[str]:
        with _lock:
            self.root.mkdir(parents=True, exist_ok=True)
            unique = sorted({f for f in folders if f})
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(unique, indent=2))
            tmp.replace(self.path)
            return unique

    def add(self, folder: str) -> List[str]:
        folder = normalize_folder(folder)
        if not folder:
            raise FileTreeError("folder name is required")
        current = self.load()
        # Materialise every ancestor so the tree has no gaps.
        parts = folder.split("/")
        for index in range(1, len(parts) + 1):
            candidate = "/".join(parts[:index])
            if candidate not in current:
                current.append(candidate)
        return self.save(current)

    def remove(self, folder: str) -> List[str]:
        folder = normalize_folder(folder)
        if not folder:
            raise FileTreeError("folder name is required")
        current = self.load()
        remaining = [
            item for item in current if item != folder and not item.startswith(f"{folder}/")
        ]
        return self.save(remaining)


# ── tree assembly ───────────────────────────────────────────────────────


def _file_entry(item: Dict[str, Any], status_overlay: Dict[str, Any]) -> Dict[str, Any]:
    file_id = item.get("id", "")
    status = item.get("status", "stored")
    overlay = status_overlay.get(file_id)
    if overlay:
        status = overlay.get("status", status)
    # The encoder's `<base>.size` sidecar is not a shard, and a shard verified
    # onto a peer is still available — otherwise every distributed file reads
    # as degraded in the explorer.
    chunks = [
        chunk
        for chunk in (item.get("chunk_list") or [])
        if not str(chunk.get("path", "")).endswith(".size")
    ]
    available = sum(
        1
        for chunk in chunks
        if (chunk.get("path") and Path(chunk["path"]).exists()) or chunk.get("peer")
    )
    remote = sum(
        1
        for chunk in chunks
        if chunk.get("peer") and not (chunk.get("path") and Path(chunk["path"]).exists())
    )
    folder = ""
    try:
        folder = normalize_folder(item.get("folder"))
    except FileTreeError:
        folder = ""
    return {
        "kind": "file",
        "id": file_id,
        "name": item.get("name", ""),
        "folder": folder,
        "path": f"{folder}/{item.get('name', '')}" if folder else item.get("name", ""),
        "size": int(item.get("size") or 0),
        "chunks": len(chunks),
        "shards_available": available,
        "shards_remote": remote,
        "shards_total": len(chunks),
        "placement": item.get("placement") or {},
        "created_at": item.get("created_at", ""),
        "status": status,
        "progress": (overlay or {}).get("progress"),
    }


def collect_folders(files: List[Dict[str, Any]], explicit: List[str]) -> List[str]:
    """Union of folders implied by files and folders created explicitly."""
    folders = set(explicit)
    for item in files:
        try:
            folder = normalize_folder(item.get("folder"))
        except FileTreeError:
            continue
        if not folder:
            continue
        parts = folder.split("/")
        for index in range(1, len(parts) + 1):
            folders.add("/".join(parts[:index]))
    return sorted(folders)


def build_tree(
    files: List[Dict[str, Any]],
    explicit_folders: List[str],
    status_overlay: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a nested tree plus a flat folder index the UI can navigate."""
    overlay = status_overlay or {}
    entries = [_file_entry(item, overlay) for item in files]
    folders = collect_folders(files, explicit_folders)

    # folder path -> aggregate
    index: Dict[str, Dict[str, Any]] = {
        "": {
            "kind": "folder",
            "path": "",
            "name": "All Files",
            "parent": None,
            "folders": [],
            "files": [],
            "size": 0,
            "file_count": 0,
        }
    }
    for folder in folders:
        index[folder] = {
            "kind": "folder",
            "path": folder,
            "name": _basename(folder),
            "parent": _parent_of(folder),
            "folders": [],
            "files": [],
            "size": 0,
            "file_count": 0,
        }

    for folder in folders:
        parent = _parent_of(folder)
        if parent in index:
            index[parent]["folders"].append(folder)

    for entry in entries:
        folder = entry["folder"] if entry["folder"] in index else ""
        index[folder]["files"].append(entry)

    # Roll sizes and counts up through the ancestors.
    for folder in sorted(index, key=lambda path: path.count("/"), reverse=True):
        node = index[folder]
        node["size"] += sum(item["size"] for item in node["files"])
        node["file_count"] += len(node["files"])
        parent = node["parent"]
        if parent is not None and parent in index:
            index[parent]["size"] += node["size"]
            index[parent]["file_count"] += node["file_count"]

    def nest(path: str) -> Dict[str, Any]:
        node = index[path]
        return {
            "path": node["path"],
            "name": node["name"],
            "size": node["size"],
            "file_count": node["file_count"],
            "children": [nest(child) for child in sorted(node["folders"])],
        }

    return {
        "tree": nest(""),
        "folders": [
            {
                "path": index[folder]["path"],
                "name": index[folder]["name"],
                "parent": index[folder]["parent"],
                "size": index[folder]["size"],
                "file_count": index[folder]["file_count"],
            }
            for folder in sorted(index)
        ],
        "files": entries,
        "total_files": len(entries),
        "total_size": sum(entry["size"] for entry in entries),
    }


def list_directory(
    tree: Dict[str, Any],
    path: str,
) -> Dict[str, Any]:
    """Entries directly inside ``path`` plus breadcrumb data."""
    path = normalize_folder(path)
    known = {folder["path"] for folder in tree["folders"]}
    if path not in known:
        raise FileTreeError(f"folder {path!r} does not exist")

    by_path = {folder["path"]: folder for folder in tree["folders"]}
    child_folders = [
        folder
        for folder in tree["folders"]
        if folder["parent"] == path and folder["path"] != path
    ]
    child_files = [entry for entry in tree["files"] if entry["folder"] == path]

    crumbs: List[Dict[str, str]] = [{"path": "", "name": "All Files"}]
    if path:
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            crumb = "/".join(parts[:index])
            crumbs.append({"path": crumb, "name": parts[index - 1]})

    node = by_path[path]
    return {
        "path": path,
        "name": node["name"],
        "parent": node["parent"],
        "breadcrumbs": crumbs,
        "folders": sorted(child_folders, key=lambda item: item["name"].lower()),
        "files": sorted(child_files, key=lambda item: item["name"].lower()),
        "size": node["size"],
        "file_count": node["file_count"],
    }


def validate_move(
    name: Optional[str],
    folder: Optional[str],
    existing: List[Dict[str, Any]],
    file_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Check a rename/move request against sibling names."""
    new_name = name.strip() if isinstance(name, str) else None
    if new_name is not None:
        if not new_name:
            raise FileTreeError("file name cannot be empty")
        if "/" in new_name or _ILLEGAL.search(new_name):
            raise FileTreeError(f"illegal characters in file name {new_name!r}")
        if len(new_name) > 255:
            raise FileTreeError("file names must be 255 characters or fewer")

    new_folder = normalize_folder(folder) if folder is not None else None

    target_folder = new_folder
    target_name = new_name
    for item in existing:
        if item.get("id") == file_id:
            if target_folder is None:
                target_folder = normalize_folder(item.get("folder"))
            if target_name is None:
                target_name = item.get("name", "")
            break

    for item in existing:
        if item.get("id") == file_id:
            continue
        try:
            other_folder = normalize_folder(item.get("folder"))
        except FileTreeError:
            continue
        if other_folder == target_folder and item.get("name") == target_name:
            location = target_folder or "the root folder"
            raise FileTreeError(f"{target_name!r} already exists in {location}")

    return new_name, new_folder
