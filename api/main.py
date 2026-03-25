import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import aiofiles
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from api.models import FileMetadata, SystemStats, UploadResponse, StatusUpdate

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COLLECTIVE_PATH = Path(os.environ.get("COLLECTIVE_PATH", Path.home() / ".collective"))
ENCODER_PATH = Path(os.environ.get("ENCODER_PATH", "./lib/encoder"))
DECODER_PATH = Path(os.environ.get("DECODER_PATH", "./lib/decoder"))
PORT = int(os.environ.get("PORT", 8000))
NODE_ID = os.environ.get("NODE_ID", str(uuid.uuid4()))
# Comma-separated list of peer base URLs e.g. "http://node2:8000,http://node3:8000"
_PEER_URLS_RAW = os.environ.get("PEER_URLS", "")
ENCODER_DATA_SHARDS = int(os.environ.get("ENCODER_DATA_SHARDS", 8))
ENCODER_PAR_SHARDS = int(os.environ.get("ENCODER_PAR_SHARDS", 4))

TREE_DIR = COLLECTIVE_PATH / "tree"
PROC_DIR = COLLECTIVE_PATH / "proc"
CACHE_DIR = COLLECTIVE_PATH / "cache"
PUBLIC_DIR = COLLECTIVE_PATH / "public"

for _d in [TREE_DIR, PROC_DIR, CACHE_DIR, PUBLIC_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared in-memory state
# ---------------------------------------------------------------------------
# Maps file_id -> StatusUpdate dict for in-flight operations
_file_statuses: Dict[str, Dict[str, Any]] = {}
# Active WebSocket connections
_ws_connections: List[WebSocket] = []
# SSE subscribers (asyncio queues)
_sse_queues: List[asyncio.Queue] = []
# Known peers: url -> {"url", "node_id", "last_seen", "healthy"}
_peers: Dict[str, Dict[str, Any]] = {}
# Pre-parse peer URLs from env at startup
for _purl in [u.strip() for u in _PEER_URLS_RAW.split(",") if u.strip()]:
    _peers[_purl] = {"url": _purl, "node_id": None, "last_seen": None, "healthy": False}

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="CollectiveFS API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _read_tree_json(file_id: str) -> Optional[Dict[str, Any]]:
    path = TREE_DIR / f"{file_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_tree_json(file_id: str, data: Dict[str, Any]) -> None:
    path = TREE_DIR / f"{file_id}.json"
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)


def _list_all_tree() -> List[Dict[str, Any]]:
    results = []
    if not TREE_DIR.exists():
        return results
    for p in TREE_DIR.glob("*.json"):
        try:
            with open(p) as fh:
                data = json.load(fh)
            results.append(data)
        except Exception:
            continue
    return results


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return total


async def _broadcast_status(update: Dict[str, Any]) -> None:
    """Push a status update to all WebSocket clients and SSE queues."""
    msg = json.dumps(update)
    dead = []
    for ws in list(_ws_connections):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            _ws_connections.remove(ws)
        except ValueError:
            pass
    for q in _sse_queues:
        try:
            q.put_nowait(update)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------

def _run_encode_pipeline(file_id: str, src_path: str, file_name: str) -> None:
    """Synchronous encode + encrypt pipeline run in a thread pool executor."""
    try:
        # Update status: processing
        _file_statuses[file_id] = {
            "type": "status",
            "file_id": file_id,
            "status": "processing",
            "progress": 0.1,
            "message": "Encoding with Reed-Solomon…",
        }

        # Attempt to run the encoder binary if it exists and is executable
        chunks: List[Dict[str, Any]] = []
        if ENCODER_PATH.exists() and os.access(str(ENCODER_PATH), os.X_OK):
            out_dir = str(PROC_DIR / file_id)
            os.makedirs(out_dir, exist_ok=True)
            result = subprocess.run(
                [
                    str(ENCODER_PATH),
                    "--data", str(ENCODER_DATA_SHARDS),
                    "--par",  str(ENCODER_PAR_SHARDS),
                    "--out",  out_dir,
                    src_path,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                # Discover shards written to out_dir
                for i, shard in enumerate(sorted(Path(out_dir).glob("*"))):
                    chunks.append(
                        {
                            "num": i,
                            "id": str(uuid.uuid4()),
                            "path": str(shard),
                        }
                    )
        else:
            # Encoder not present – store the raw file as a single "chunk"
            dest = PROC_DIR / file_id
            dest.mkdir(parents=True, exist_ok=True)
            dest_file = dest / file_name
            shutil.copy2(src_path, str(dest_file))
            chunks = [
                {
                    "num": 0,
                    "id": str(uuid.uuid4()),
                    "path": str(dest_file),
                }
            ]

        _file_statuses[file_id]["progress"] = 0.5
        _file_statuses[file_id]["message"] = "Encrypting shards…"

        # Encrypt each shard with Fernet if key exists
        key_path = COLLECTIVE_PATH / "key"
        if key_path.exists():
            from cryptography.fernet import Fernet

            with open(key_path, "rb") as kf:
                fernet = Fernet(kf.read().strip())
            for chunk in chunks:
                chunk_path = Path(chunk["path"])
                if chunk_path.exists():
                    with open(chunk_path, "rb") as cf:
                        data = cf.read()
                    encrypted = fernet.encrypt(data)
                    with open(chunk_path, "wb") as cf:
                        cf.write(encrypted)
                    chunk["encrypted"] = True

        _file_statuses[file_id]["progress"] = 0.8
        _file_statuses[file_id]["message"] = "Storing metadata…"

        # Get file size from original
        try:
            file_size = Path(src_path).stat().st_size
        except Exception:
            file_size = 0

        metadata: Dict[str, Any] = {
            "id": file_id,
            "name": file_name,
            "size": file_size,
            "chunks": len(chunks),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "stored",
            "folder": None,
            "chunk_list": chunks,
        }
        _write_tree_json(file_id, metadata)

        # Final status
        _file_statuses[file_id] = {
            "type": "status",
            "file_id": file_id,
            "status": "complete",
            "progress": 1.0,
            "message": "File stored successfully.",
        }

    except Exception as exc:
        _file_statuses[file_id] = {
            "type": "status",
            "file_id": file_id,
            "status": "error",
            "progress": 0,
            "message": str(exc),
        }
    finally:
        # Clean up temp source
        try:
            if os.path.exists(src_path):
                os.remove(src_path)
        except Exception:
            pass


async def _async_encode_pipeline(
    file_id: str, src_path: str, file_name: str
) -> None:
    """Run the encode pipeline in a thread executor, then broadcast result."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _run_encode_pipeline, file_id, src_path, file_name
    )
    if file_id in _file_statuses:
        await _broadcast_status(_file_statuses[file_id])


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/files", response_model=List[FileMetadata])
async def list_files() -> List[FileMetadata]:
    raw = _list_all_tree()
    files = []
    for item in raw:
        # Overlay any in-flight status
        status = item.get("status", "stored")
        if item["id"] in _file_statuses:
            status = _file_statuses[item["id"]].get("status", status)
        files.append(
            FileMetadata(
                id=item["id"],
                name=item.get("name", ""),
                size=item.get("size", 0),
                chunks=item.get("chunks", 0),
                created_at=item.get("created_at", ""),
                status=status,
                folder=item.get("folder"),
            )
        )
    return files


@app.get("/api/files/{file_id}", response_model=FileMetadata)
async def get_file(file_id: str) -> FileMetadata:
    data = _read_tree_json(file_id)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")
    status = data.get("status", "stored")
    if file_id in _file_statuses:
        status = _file_statuses[file_id].get("status", status)
    return FileMetadata(
        id=data["id"],
        name=data.get("name", ""),
        size=data.get("size", 0),
        chunks=data.get("chunks", 0),
        created_at=data.get("created_at", ""),
        status=status,
        folder=data.get("folder"),
    )


@app.post("/api/files/upload", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> UploadResponse:
    file_id = str(uuid.uuid4())
    file_name = file.filename or "unknown"

    # Save to temp location
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_{file_name}")
    try:
        async with aiofiles.open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                await out.write(chunk)
    except Exception as exc:
        os.close(tmp_fd)
        os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        try:
            os.close(tmp_fd)
        except Exception:
            pass

    # Seed in-memory status
    _file_statuses[file_id] = {
        "type": "status",
        "file_id": file_id,
        "status": "processing",
        "progress": 0.0,
        "message": "Upload received, starting pipeline…",
    }

    background_tasks.add_task(_async_encode_pipeline, file_id, tmp_path, file_name)

    return UploadResponse(
        id=file_id,
        name=file_name,
        status="processing",
        message="Upload received. Encoding pipeline started.",
    )


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str) -> Dict[str, bool]:
    data = _read_tree_json(file_id)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Remove shards
    shard_dir = PROC_DIR / file_id
    if shard_dir.exists():
        shutil.rmtree(str(shard_dir), ignore_errors=True)

    # Remove tree JSON
    tree_path = TREE_DIR / f"{file_id}.json"
    try:
        tree_path.unlink(missing_ok=True)
    except Exception:
        pass

    # Clean up status
    _file_statuses.pop(file_id, None)

    return {"deleted": True}


@app.get("/api/files/{file_id}/download")
async def download_file(file_id: str) -> StreamingResponse:
    data = _read_tree_json(file_id)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")

    file_name = data.get("name", "download")
    chunk_list = data.get("chunk_list", [])

    # Try decoder binary first
    if DECODER_PATH.exists() and os.access(str(DECODER_PATH), os.X_OK):
        out_dir = str(CACHE_DIR / file_id)
        os.makedirs(out_dir, exist_ok=True)
        shard_dir = str(PROC_DIR / file_id)
        result = subprocess.run(
            [str(DECODER_PATH), shard_dir, out_dir, file_name],
            capture_output=True,
            timeout=300,
        )
        out_file = Path(out_dir) / file_name
        if result.returncode == 0 and out_file.exists():
            return StreamingResponse(
                open(str(out_file), "rb"),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{file_name}"'
                },
            )

    # Fallback: decrypt and stream the first shard
    if chunk_list:
        chunk_path = Path(chunk_list[0]["path"])
        if chunk_path.exists():
            key_path = COLLECTIVE_PATH / "key"
            if key_path.exists() and chunk_list[0].get("encrypted"):
                from cryptography.fernet import Fernet

                with open(key_path, "rb") as kf:
                    fernet = Fernet(kf.read().strip())
                with open(chunk_path, "rb") as cf:
                    decrypted = fernet.decrypt(cf.read())

                async def _iter_bytes():
                    yield decrypted

                return StreamingResponse(
                    _iter_bytes(),
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{file_name}"'
                    },
                )
            else:
                return StreamingResponse(
                    open(str(chunk_path), "rb"),
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": f'attachment; filename="{file_name}"'
                    },
                )

    raise HTTPException(
        status_code=422, detail="Could not reconstruct file – shards unavailable."
    )


@app.get("/api/stats", response_model=SystemStats)
async def get_stats() -> SystemStats:
    all_files = _list_all_tree()
    total_chunks = sum(f.get("chunks", 0) for f in all_files)
    storage_used = _dir_size(COLLECTIVE_PATH)
    return SystemStats(
        total_files=len(all_files),
        total_chunks=total_chunks,
        storage_used_bytes=storage_used,
        storage_path=str(COLLECTIVE_PATH),
        encryption="Fernet (AES-128-CBC + HMAC-SHA256)",
        erasure_coding="Reed-Solomon 8+4",
    )


@app.get("/api/status/stream")
async def status_stream(request: Request) -> EventSourceResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_queues.append(queue)

    async def _event_generator():
        try:
            # Send all current in-flight statuses on connect
            for status in _file_statuses.values():
                yield {"data": json.dumps(status)}
            # Then stream new events
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    # Heartbeat
                    yield {"data": json.dumps({"type": "heartbeat"})}
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _sse_queues.remove(queue)
            except ValueError:
                pass

    return EventSourceResponse(_event_generator())


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_connections.append(websocket)
    try:
        # Send current statuses on connect
        for status in _file_statuses.values():
            await websocket.send_text(json.dumps(status))
        # Keep connection alive, handle incoming pings
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Echo back or handle ping
                if msg == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            _ws_connections.remove(websocket)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Peer discovery routes
# ---------------------------------------------------------------------------


@app.get("/api/peers")
async def list_peers() -> List[Dict[str, Any]]:
    """Return all known peers and their health status."""
    return list(_peers.values())


@app.post("/api/peers/register")
async def register_peer(body: Dict[str, Any]) -> Dict[str, Any]:
    """Called by another node to announce itself to this one."""
    url = body.get("url", "").rstrip("/")
    node_id = body.get("node_id", "")
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    _peers[url] = {
        "url": url,
        "node_id": node_id,
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "healthy": True,
    }
    return {"registered": True, "node_id": NODE_ID}


@app.get("/api/peers/files")
async def peer_files() -> List[Dict[str, Any]]:
    """Expose this node's file metadata for other nodes to sync."""
    return _list_all_tree()


@app.get("/api/peers/chunks/{chunk_id}")
async def serve_chunk(chunk_id: str):
    """Serve a raw encrypted shard by its chunk UUID."""
    for tree_data in _list_all_tree():
        for c in tree_data.get("chunk_list", []):
            if c.get("id") == chunk_id:
                chunk_path = Path(c["path"])
                if chunk_path.exists():
                    return StreamingResponse(
                        open(str(chunk_path), "rb"),
                        media_type="application/octet-stream",
                    )
    raise HTTPException(status_code=404, detail="Chunk not found")


@app.get("/api/network")
async def network_view() -> Dict[str, Any]:
    """Aggregate local + peer files for the network view."""
    local_files = _list_all_tree()
    peer_files_agg: List[Dict[str, Any]] = []
    for peer in list(_peers.values()):
        if not peer.get("healthy"):
            continue
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{peer['url']}/api/peers/files")
                if r.status_code == 200:
                    for f in r.json():
                        f["_peer_url"] = peer["url"]
                        f["_peer_node_id"] = peer.get("node_id")
                        peer_files_agg.append(f)
        except Exception:
            _peers[peer["url"]]["healthy"] = False
    return {
        "node_id": NODE_ID,
        "local_files": local_files,
        "peer_files": peer_files_agg,
        "peers": list(_peers.values()),
    }


# ---------------------------------------------------------------------------
# Startup: announce to known peers
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup_announce():
    """On startup, announce this node's existence to all configured peers."""
    own_url = os.environ.get("OWN_URL", "")
    if not own_url or not _peers:
        return
    payload = {"url": own_url, "node_id": NODE_ID}
    for peer_url in list(_peers.keys()):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.post(f"{peer_url}/api/peers/register", json=payload)
                if r.status_code == 200:
                    _peers[peer_url]["healthy"] = True
                    _peers[peer_url]["last_seen"] = datetime.now(timezone.utc).isoformat()
                    resp = r.json()
                    _peers[peer_url]["node_id"] = resp.get("node_id")
        except Exception:
            _peers[peer_url]["healthy"] = False


# ---------------------------------------------------------------------------
# Static files / SPA catch-all (MUST be last)
# ---------------------------------------------------------------------------

_UI_DIST = Path(__file__).parent.parent / "ui" / "dist"

if _UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        index = _UI_DIST / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"detail": "UI not built"}, status_code=404)
