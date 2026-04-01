#!/usr/bin/env python3
"""
CollectiveFS MCP Server

Exposes CollectiveFS as a Model Context Protocol (MCP) server so Claude Code
and other MCP clients can manage distributed files directly from the CLI.

Auto-registered in ~/.claude/settings.json by running:
    python mcp_server.py --register

Usage (stdio transport, used by Claude Code):
    python mcp_server.py

Environment:
    COLLECTIVEFS_URL   Base URL of the CollectiveFS API  (default: http://localhost:8000)
    COLLECTIVEFS_URLS  Comma-separated cluster node URLs  (optional)
"""

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ImageContent,
    ListToolsResult,
    TextContent,
    Tool,
)
import mcp.types as types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("COLLECTIVEFS_URL", "http://localhost:8000").rstrip("/")
CLUSTER_URLS = [
    u.strip()
    for u in os.environ.get("COLLECTIVEFS_URLS", BASE_URL).split(",")
    if u.strip()
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)


async def _get(path: str) -> Any:
    async with _client() as c:
        r = await c.get(path)
        r.raise_for_status()
        return r.json()


async def _post(path: str, **kwargs) -> Any:
    async with _client() as c:
        r = await c.post(path, **kwargs)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> Any:
    async with _client() as c:
        r = await c.delete(path)
        r.raise_for_status()
        return r.json()


def _fmt(data: Any) -> str:
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("collectivefs")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="cfs_list_files",
            description=(
                "List all files stored in CollectiveFS. Returns file names, sizes, "
                "chunk counts, status (processing/stored), and creation dates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "node_url": {
                        "type": "string",
                        "description": "Optional: query a specific node URL instead of the default.",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="cfs_upload_file",
            description=(
                "Upload a local file to CollectiveFS. The file will be Reed-Solomon encoded "
                "(8 data + 4 parity shards), Fernet-encrypted, and stored. "
                "Returns the file ID and processing status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the local file to upload.",
                    },
                    "node_url": {
                        "type": "string",
                        "description": "Optional: upload to a specific node URL.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="cfs_upload_text",
            description=(
                "Upload text content as a named file to CollectiveFS. "
                "Useful for storing notes, configs, or code snippets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name for the stored file."},
                    "content": {"type": "string", "description": "Text content to store."},
                },
                "required": ["filename", "content"],
            },
        ),
        Tool(
            name="cfs_get_file",
            description="Get metadata for a specific file by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The file UUID."},
                },
                "required": ["file_id"],
            },
        ),
        Tool(
            name="cfs_download_file",
            description=(
                "Download and reconstruct a file from CollectiveFS by its ID. "
                "Returns the file content as text (if UTF-8 decodable) or base64."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The file UUID to download."},
                    "save_to": {
                        "type": "string",
                        "description": "Optional local path to save the file to.",
                    },
                },
                "required": ["file_id"],
            },
        ),
        Tool(
            name="cfs_delete_file",
            description="Delete a file and all its shards from CollectiveFS by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The file UUID to delete."},
                },
                "required": ["file_id"],
            },
        ),
        Tool(
            name="cfs_stats",
            description=(
                "Get CollectiveFS system statistics: total files, chunks, storage used, "
                "encryption scheme, erasure coding parameters."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cfs_network",
            description=(
                "Get the full network view: this node's files, all peer nodes, "
                "and files visible from peer nodes. Shows cross-node availability."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cfs_peers",
            description="List all known peer nodes and their health status.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cfs_register_peer",
            description="Register a new peer node with this CollectiveFS instance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL of the peer node."},
                    "node_id": {"type": "string", "description": "Optional node identifier."},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="cfs_cluster_status",
            description=(
                "Check health of all nodes in the cluster "
                f"({', '.join(CLUSTER_URLS)}). "
                "Returns per-node health, file counts, and storage stats."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cfs_search_files",
            description="Search for files by name (case-insensitive substring match).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term to match against file names."},
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:

    async def _text(s: str) -> list[TextContent]:
        return [TextContent(type="text", text=s)]

    try:
        # ------------------------------------------------------------------ #
        # cfs_list_files
        # ------------------------------------------------------------------ #
        if name == "cfs_list_files":
            url = arguments.get("node_url", "").rstrip("/") or BASE_URL
            async with httpx.AsyncClient(base_url=url, timeout=10) as c:
                r = await c.get("/api/files")
                r.raise_for_status()
                files = r.json()
            if not files:
                return await _text("No files stored in CollectiveFS yet.")
            lines = [f"{'Name':<40} {'Size':>10} {'Chunks':>8} {'Status':<12} ID"]
            lines.append("-" * 90)
            for f in files:
                size = f.get("size", 0)
                size_str = _fmt_bytes(size)
                lines.append(
                    f"{f['name']:<40} {size_str:>10} {f.get('chunks',0):>8} "
                    f"{f.get('status','?'):<12} {f['id']}"
                )
            lines.append(f"\nTotal: {len(files)} file(s)")
            return await _text("\n".join(lines))

        # ------------------------------------------------------------------ #
        # cfs_upload_file
        # ------------------------------------------------------------------ #
        elif name == "cfs_upload_file":
            path = Path(arguments["path"])
            if not path.exists():
                return await _text(f"Error: file not found: {path}")
            url = arguments.get("node_url", "").rstrip("/") or BASE_URL
            async with httpx.AsyncClient(base_url=url, timeout=60) as c:
                with open(path, "rb") as fh:
                    r = await c.post(
                        "/api/files/upload",
                        files={"file": (path.name, fh, "application/octet-stream")},
                    )
                r.raise_for_status()
                resp = r.json()
            return await _text(
                f"Upload started.\n"
                f"  File: {path.name}\n"
                f"  ID:   {resp['id']}\n"
                f"  Status: {resp['status']}\n"
                f"  Message: {resp.get('message', '')}"
            )

        # ------------------------------------------------------------------ #
        # cfs_upload_text
        # ------------------------------------------------------------------ #
        elif name == "cfs_upload_text":
            filename = arguments["filename"]
            content = arguments["content"].encode()
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
                r = await c.post(
                    "/api/files/upload",
                    files={"file": (filename, content, "text/plain")},
                )
                r.raise_for_status()
                resp = r.json()
            return await _text(
                f"Text stored as '{filename}'.\n"
                f"  ID: {resp['id']}\n"
                f"  Status: {resp['status']}"
            )

        # ------------------------------------------------------------------ #
        # cfs_get_file
        # ------------------------------------------------------------------ #
        elif name == "cfs_get_file":
            data = await _get(f"/api/files/{arguments['file_id']}")
            return await _text(_fmt(data))

        # ------------------------------------------------------------------ #
        # cfs_download_file
        # ------------------------------------------------------------------ #
        elif name == "cfs_download_file":
            file_id = arguments["file_id"]
            save_to = arguments.get("save_to")
            async with _client() as c:
                r = await c.get(f"/api/files/{file_id}/download")
                r.raise_for_status()
                raw = r.content
            if save_to:
                Path(save_to).write_bytes(raw)
                return await _text(f"Saved {len(raw):,} bytes to {save_to}")
            try:
                text = raw.decode("utf-8")
                preview = text[:4000] + ("…" if len(text) > 4000 else "")
                return await _text(f"Content ({len(raw):,} bytes):\n\n{preview}")
            except UnicodeDecodeError:
                b64 = base64.b64encode(raw[:4096]).decode()
                return await _text(
                    f"Binary file ({len(raw):,} bytes). First 4KB base64:\n{b64}"
                )

        # ------------------------------------------------------------------ #
        # cfs_delete_file
        # ------------------------------------------------------------------ #
        elif name == "cfs_delete_file":
            result = await _delete(f"/api/files/{arguments['file_id']}")
            return await _text(f"Deleted: {result}")

        # ------------------------------------------------------------------ #
        # cfs_stats
        # ------------------------------------------------------------------ #
        elif name == "cfs_stats":
            s = await _get("/api/stats")
            lines = [
                "CollectiveFS Statistics",
                "─" * 40,
                f"  Files:       {s['total_files']}",
                f"  Chunks:      {s['total_chunks']}",
                f"  Storage:     {_fmt_bytes(s['storage_used_bytes'])}",
                f"  Path:        {s['storage_path']}",
                f"  Encryption:  {s['encryption']}",
                f"  Erasure:     {s['erasure_coding']}",
            ]
            return await _text("\n".join(lines))

        # ------------------------------------------------------------------ #
        # cfs_network
        # ------------------------------------------------------------------ #
        elif name == "cfs_network":
            data = await _get("/api/network")
            lines = [
                f"Node ID: {data.get('node_id', '?')}",
                f"Local files: {len(data.get('local_files', []))}",
                f"Peer files:  {len(data.get('peer_files', []))}",
                f"Peers:       {len(data.get('peers', []))}",
                "",
            ]
            for p in data.get("peers", []):
                status = "✓ healthy" if p.get("healthy") else "✗ offline"
                lines.append(f"  {p['url']}  [{status}]  node_id={p.get('node_id','?')}")
            if data.get("peer_files"):
                lines.append("\nFiles from peers:")
                for f in data["peer_files"]:
                    lines.append(f"  {f['name']}  ({_fmt_bytes(f.get('size',0))})  from {f.get('_peer_url','?')}")
            return await _text("\n".join(lines))

        # ------------------------------------------------------------------ #
        # cfs_peers
        # ------------------------------------------------------------------ #
        elif name == "cfs_peers":
            peers = await _get("/api/peers")
            if not peers:
                return await _text("No peers registered.")
            lines = [f"{'URL':<40} {'Status':<12} {'Node ID':<36} Last Seen"]
            lines.append("-" * 100)
            for p in peers:
                status = "healthy" if p.get("healthy") else "offline"
                lines.append(
                    f"{p['url']:<40} {status:<12} "
                    f"{str(p.get('node_id','?')):<36} "
                    f"{p.get('last_seen','never')}"
                )
            return await _text("\n".join(lines))

        # ------------------------------------------------------------------ #
        # cfs_register_peer
        # ------------------------------------------------------------------ #
        elif name == "cfs_register_peer":
            result = await _post(
                "/api/peers/register",
                json={"url": arguments["url"], "node_id": arguments.get("node_id", "")},
            )
            return await _text(f"Registered peer: {_fmt(result)}")

        # ------------------------------------------------------------------ #
        # cfs_cluster_status
        # ------------------------------------------------------------------ #
        elif name == "cfs_cluster_status":
            lines = ["Cluster Status", "─" * 60]
            for url in CLUSTER_URLS:
                try:
                    async with httpx.AsyncClient(base_url=url, timeout=5) as c:
                        h = await c.get("/api/health")
                        s = await c.get("/api/stats")
                        h.raise_for_status()
                        s.raise_for_status()
                        stats = s.json()
                        lines.append(
                            f"  ✓ {url:<35} "
                            f"{stats['total_files']} files  "
                            f"{_fmt_bytes(stats['storage_used_bytes'])} stored"
                        )
                except Exception as exc:
                    lines.append(f"  ✗ {url:<35} OFFLINE ({exc})")
            return await _text("\n".join(lines))

        # ------------------------------------------------------------------ #
        # cfs_search_files
        # ------------------------------------------------------------------ #
        elif name == "cfs_search_files":
            query = arguments["query"].lower()
            files = await _get("/api/files")
            matches = [f for f in files if query in f["name"].lower()]
            if not matches:
                return await _text(f"No files matching '{query}'.")
            lines = [f"Found {len(matches)} match(es) for '{query}':\n"]
            for f in matches:
                lines.append(
                    f"  {f['name']}  "
                    f"({_fmt_bytes(f.get('size',0))}, {f.get('chunks',0)} chunks)  "
                    f"id={f['id']}"
                )
            return await _text("\n".join(lines))

        else:
            return await _text(f"Unknown tool: {name}")

    except httpx.ConnectError:
        return await _text(
            f"Cannot connect to CollectiveFS at {BASE_URL}.\n"
            "Make sure the service is running:\n"
            "  docker compose up -d   (from the CollectiveFS project root)"
        )
    except httpx.HTTPStatusError as e:
        return await _text(f"API error {e.response.status_code}: {e.response.text}")
    except Exception as exc:
        return await _text(f"Error: {exc}")


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------------------------------------------------------------------------
# Auto-registration helper
# ---------------------------------------------------------------------------

def _register():
    """Add this MCP server to ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        existing = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except Exception:
        existing = {}

    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["collectivefs"] = {
        "command": sys.executable,
        "args": [str(Path(__file__).resolve())],
        "env": {
            "COLLECTIVEFS_URL": BASE_URL,
            "COLLECTIVEFS_URLS": ",".join(CLUSTER_URLS),
        },
    }

    settings_path.write_text(json.dumps(existing, indent=2))
    print(f"Registered CollectiveFS MCP server in {settings_path}")
    print(f"  URL: {BASE_URL}")
    print(f"  Cluster nodes: {', '.join(CLUSTER_URLS)}")
    print("\nRestart Claude Code to pick up the new MCP server.")
    print("Then ask Claude: 'list my collectivefs files' or 'upload ./myfile.txt to collectivefs'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main():
    async with stdio_server() as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    if "--register" in sys.argv:
        _register()
    else:
        asyncio.run(_main())
