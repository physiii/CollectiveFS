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
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from api.models import (
    ChallengeRequest,
    ChallengeResponse,
    ContractCreateRequest,
    ContractStatus,
    ContractSummary,
    ContractTier,
    FileMetadata,
    PeerContract,
    ShardInfo,
    StatusUpdate,
    SystemStats,
    TierConfig,
    UploadResponse,
)
from api.contracts import ContractManager, TIER_CONFIGS, respond_to_challenge
from api import agent_service, files_service, replication, system_service
from api.config_service import ConfigError, ConfigStore, apply_updates, describe_settings
from api.files_service import FileTreeError, FolderStore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COLLECTIVE_PATH = Path(os.environ.get("COLLECTIVE_PATH", Path.home() / ".collective"))
ENCODER_PATH = Path(os.environ.get("ENCODER_PATH", "./lib/encoder")).resolve()
DECODER_PATH = Path(os.environ.get("DECODER_PATH", "./lib/decoder")).resolve()
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
# Shards this node stores on behalf of other nodes.
PEER_SHARD_DIR = PROC_DIR / "_peers"
KEY_PATH = COLLECTIVE_PATH / "key"

for _d in [TREE_DIR, PROC_DIR, CACHE_DIR, PUBLIC_DIR, PEER_SHARD_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# Where this node can be reached by peers. Needed before shards can be handed
# out, since a peer has to be able to hand them back.
OWN_URL = os.environ.get("OWN_URL", "").rstrip("/")


def _load_fernet():
    """The symmetric key used for every shard, created once per node.

    Generated on first use rather than shipped, so a node that has never run
    has no key material on disk. Without this the service silently stored
    shards in plaintext — which is untenable once shards leave the machine.
    """
    from cryptography.fernet import Fernet

    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            KEY_PATH.chmod(0o600)
        except OSError:
            pass
    try:
        return Fernet(KEY_PATH.read_bytes().strip())
    except Exception:
        return None

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

# Contract manager
contract_mgr = ContractManager(COLLECTIVE_PATH, NODE_ID)

# Runtime configuration (quota, erasure parameters, agent provider) and the
# explicit-folder index backing the Files explorer.
config_store = ConfigStore(COLLECTIVE_PATH)
folder_store = FolderStore(COLLECTIVE_PATH)


def _erasure_params() -> tuple[int, int]:
    """Shard counts for the *next* upload, from config with env as the seed."""
    cfg = config_store.load().get("erasure", {})
    return (
        int(cfg.get("data_shards") or ENCODER_DATA_SHARDS),
        int(cfg.get("parity_shards") or ENCODER_PAR_SHARDS),
    )

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


def _build_shard_list(data: Dict[str, Any]) -> List[ShardInfo]:
    """Build shard info from stored tree metadata.

    The encoder also writes a `<base>.size` sidecar; it is not a shard and must
    not be counted as one, or every file reads as having one more shard than it
    really does. A shard held by a peer counts as available — that is the whole
    point of distributing it.
    """
    shards = []
    for chunk in replication.data_shards_only(data.get("chunk_list", [])):
        index = replication.shard_index(chunk)
        if index is None:
            continue
        chunk_path = Path(chunk.get("path", ""))
        size = int(chunk.get("size") or 0)
        held_locally = chunk_path.exists()
        if held_locally:
            try:
                size = chunk_path.stat().st_size
            except OSError:
                pass
        peer = chunk.get("peer")
        shards.append(ShardInfo(
            num=index,
            id=chunk.get("id", f"shard-{index}"),
            size=size,
            encrypted=chunk.get("encrypted", False),
            available=held_locally or bool(peer),
            peer=peer or "local",
        ))
    shards.sort(key=lambda shard: shard.num)
    return shards


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

def _run_encode_pipeline(
    file_id: str, src_path: str, file_name: str, folder: str = ""
) -> None:
    """Synchronous encode + encrypt pipeline run in a thread pool executor."""
    try:
        data_shards, parity_shards = _erasure_params()
        # Update status: processing
        _file_statuses[file_id] = {
            "type": "status",
            "file_id": file_id,
            "status": "processing",
            "progress": 0.1,
            "message": f"Encoding with Reed-Solomon {data_shards}+{parity_shards}…",
        }

        # Attempt to run the encoder binary if it exists and is executable
        chunks: List[Dict[str, Any]] = []
        if ENCODER_PATH.exists() and os.access(str(ENCODER_PATH), os.X_OK):
            out_dir = str(PROC_DIR / file_id)
            os.makedirs(out_dir, exist_ok=True)
            result = subprocess.run(
                [
                    str(ENCODER_PATH),
                    "--data", str(data_shards),
                    "--par",  str(parity_shards),
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

        # Encrypt every shard at rest. Shards may be handed to untrusted peers,
        # so this is not optional; the key is created on first use.
        fernet = _load_fernet()
        if fernet is not None:
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
            "folder": folder or None,
            "data_shards": data_shards,
            "parity_shards": parity_shards,
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


def _healthy_peer_urls() -> List[str]:
    return [
        peer["url"]
        for peer in _peers.values()
        if peer.get("healthy") and peer.get("url")
    ]


async def _distribute_shards(file_id: str) -> None:
    """Hand this file's shards to peers, once it is encoded and encrypted."""
    config = config_store.load()
    peers_cfg = config.get("peers", {})
    if not peers_cfg.get("distribute_shards", True):
        return
    peer_urls = _healthy_peer_urls()
    if not peer_urls:
        return
    if not OWN_URL:
        # A peer that cannot reach us back could never return our shards.
        return

    metadata = _read_tree_json(file_id)
    if metadata is None:
        return

    _file_statuses[file_id] = {
        "type": "status",
        "file_id": file_id,
        "status": "distributing",
        "progress": 0.9,
        "message": f"Placing shards across {len(peer_urls)} peer(s)…",
    }
    await _broadcast_status(_file_statuses[file_id])

    result = await replication.distribute(
        file_id=file_id,
        metadata=metadata,
        peer_urls=peer_urls,
        origin_node=NODE_ID,
        origin_url=OWN_URL,
        parity_shards=int(metadata.get("parity_shards") or ENCODER_PAR_SHARDS),
        keep_local_copy=bool(peers_cfg.get("keep_local_copy", False)),
    )
    metadata["placement"] = result["summary"]
    _write_tree_json(file_id, metadata)

    placed = result["placed"]
    message = (
        f"Stored across {len(result['summary'])} location(s): "
        + ", ".join(f"{where.replace('http://', '')}×{count}" for where, count in result["summary"].items())
    )
    if result["failures"]:
        message += f" — {len(result['failures'])} shard(s) stayed local"
    _file_statuses[file_id] = {
        "type": "status",
        "file_id": file_id,
        "status": "complete",
        "progress": 1.0,
        "message": message,
        "placed": placed,
    }


async def _async_encode_pipeline(
    file_id: str, src_path: str, file_name: str, folder: str = ""
) -> None:
    """Run the encode pipeline in a thread executor, then broadcast result."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _run_encode_pipeline, file_id, src_path, file_name, folder
    )
    if _file_statuses.get(file_id, {}).get("status") == "complete":
        try:
            await _distribute_shards(file_id)
        except Exception as exc:
            # Distribution is best-effort: the file is already stored here.
            _file_statuses[file_id]["message"] = f"Stored locally; distribution failed: {exc}"
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


@app.get("/api/files/tree")
async def files_tree() -> Dict[str, Any]:
    """Full folder hierarchy plus every file entry, for the explorer."""
    return files_service.build_tree(
        _list_all_tree(), folder_store.load(), _file_statuses
    )


@app.get("/api/files/browse")
async def files_browse(path: str = Query("", description="Folder path")) -> Dict[str, Any]:
    """Entries directly inside one folder, with breadcrumbs."""
    tree = files_service.build_tree(
        _list_all_tree(), folder_store.load(), _file_statuses
    )
    try:
        return files_service.list_directory(tree, path)
    except FileTreeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/folders")
async def create_folder(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        folders = folder_store.add(body.get("path") or body.get("name") or "")
    except FileTreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"folders": folders}


@app.delete("/api/folders")
async def delete_folder(path: str = Query(...)) -> Dict[str, Any]:
    """Forget a folder. Files inside it are moved to the root, never deleted."""
    try:
        target = files_service.normalize_folder(path)
    except FileTreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not target:
        raise HTTPException(status_code=400, detail="cannot remove the root folder")

    moved = 0
    for item in _list_all_tree():
        try:
            folder = files_service.normalize_folder(item.get("folder"))
        except FileTreeError:
            continue
        if folder == target or folder.startswith(f"{target}/"):
            item["folder"] = None
            _write_tree_json(item["id"], item)
            moved += 1

    folders = folder_store.remove(target)
    return {"folders": folders, "files_moved_to_root": moved}


@app.patch("/api/files/{file_id}", response_model=FileMetadata)
async def update_file(file_id: str, body: Dict[str, Any]) -> FileMetadata:
    """Rename a file and/or move it to another folder."""
    data = _read_tree_json(file_id)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        new_name, new_folder = files_service.validate_move(
            body.get("name"), body.get("folder"), _list_all_tree(), file_id
        )
    except FileTreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if new_name is not None:
        data["name"] = new_name
    if new_folder is not None:
        data["folder"] = new_folder or None
        if new_folder:
            folder_store.add(new_folder)
    _write_tree_json(file_id, data)

    return FileMetadata(
        id=data["id"],
        name=data.get("name", ""),
        size=data.get("size", 0),
        chunks=data.get("chunks", 0),
        created_at=data.get("created_at", ""),
        status=data.get("status", "stored"),
        folder=data.get("folder"),
    )


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
        shard_list=_build_shard_list(data),
    )


@app.post("/api/files/upload", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder: str = Form(""),
) -> UploadResponse:
    file_id = str(uuid.uuid4())
    file_name = file.filename or "unknown"

    try:
        folder = files_service.normalize_folder(folder)
    except FileTreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    config = config_store.load()
    max_bytes = int(config.get("upload", {}).get("max_file_bytes") or 0)
    usage = system_service.collective_usage(COLLECTIVE_PATH, config, _list_all_tree())
    if not usage["accepting_writes"]:
        raise HTTPException(
            status_code=507,
            detail=(
                f"Storage is at {usage['used_percent']}% of the configured quota "
                f"(cutoff {usage['high_watermark_percent']}%). Raise "
                "storage.quota_bytes or free space before uploading."
            ),
        )

    # Save to temp location, aborting as soon as the size limit is crossed so a
    # huge upload cannot fill the disk before it is rejected.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_{file_name}")
    written = 0
    try:
        async with aiofiles.open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if max_bytes and written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds the configured upload limit of "
                            f"{max_bytes} bytes (upload.max_file_bytes)."
                        ),
                    )
                await out.write(chunk)
    except HTTPException:
        os.close(tmp_fd)
        os.remove(tmp_path)
        raise
    except Exception as exc:
        os.close(tmp_fd)
        os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        try:
            os.close(tmp_fd)
        except Exception:
            pass

    if folder:
        folder_store.add(folder)

    # Seed in-memory status
    _file_statuses[file_id] = {
        "type": "status",
        "file_id": file_id,
        "status": "processing",
        "progress": 0.0,
        "message": "Upload received, starting pipeline…",
    }

    background_tasks.add_task(
        _async_encode_pipeline, file_id, tmp_path, file_name, folder
    )

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

    # Ask every peer holding a shard of this file to drop it, before the
    # metadata that records where they are goes away.
    peer_urls = {
        chunk.get("peer")
        for chunk in data.get("chunk_list", [])
        if chunk.get("peer")
    }
    for peer_url in peer_urls:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(f"{peer_url}/api/peers/shards/{NODE_ID}/{file_id}")
        except httpx.HTTPError:
            # A peer that is down keeps an orphan shard; it is encrypted and
            # useless without our key, and re-deleting later is harmless.
            pass

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
        # Shards are encrypted at rest and some may live on a peer, so they are
        # collected and decrypted into a staging directory rather than decoded
        # in place — the decoder reads raw files and would happily reconstruct
        # garbage from ciphertext.
        staging, shard_base, problems = await replication.gather_shards(
            metadata=data,
            origin_node=NODE_ID,
            file_id=file_id,
            fernet=_load_fernet(),
        )
        try:
            if shard_base:
                # Each file records the parameters it was encoded with; using
                # the current settings instead would corrupt any file stored
                # before those settings changed.
                data_shards = int(data.get("data_shards") or ENCODER_DATA_SHARDS)
                parity_shards = int(data.get("parity_shards") or ENCODER_PAR_SHARDS)
                out_file = CACHE_DIR / file_id / file_name
                out_file.parent.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    [
                        str(DECODER_PATH),
                        "-data", str(data_shards),
                        "-par", str(parity_shards),
                        "-out", str(out_file),
                        shard_base,
                    ],
                    cwd=str(staging),
                    capture_output=True,
                    timeout=300,
                )
                if result.returncode == 0 and out_file.exists():
                    file_size = out_file.stat().st_size
                    return StreamingResponse(
                        open(str(out_file), "rb"),
                        media_type="application/octet-stream",
                        headers={
                            "Content-Disposition": f'attachment; filename="{file_name}"',
                            "Content-Length": str(file_size),
                        },
                    )
                if problems:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Could not reconstruct the file. Unavailable shards: "
                            + "; ".join(problems[:6])
                        ),
                    )
        finally:
            replication.cleanup(staging)

    # Fallback: decrypt and stream the first shard
    if chunk_list:
        chunk_path = Path(chunk_list[0]["path"])
        if chunk_path.exists():
            fernet = _load_fernet()
            if fernet is not None and chunk_list[0].get("encrypted"):
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


# ── shards held for other nodes ─────────────────────────────────────
#
# The origin node keeps the metadata; this node just stores bytes on its behalf
# and hands them back on request. Replicas are namespaced by origin node so two
# peers can never collide on a file id.


def _replica_dir(origin_node: str, file_id: str) -> Path:
    safe_origin = "".join(ch for ch in origin_node if ch.isalnum() or ch in "-_")[:64]
    safe_file = "".join(ch for ch in file_id if ch.isalnum() or ch in "-_")[:64]
    if not safe_origin or not safe_file:
        raise HTTPException(status_code=400, detail="invalid origin_node or file_id")
    return PEER_SHARD_DIR / safe_origin / safe_file


def _replica_path(origin_node: str, file_id: str, index: int) -> Optional[Path]:
    directory = _replica_dir(origin_node, file_id)
    if not directory.is_dir():
        return None
    for entry in directory.iterdir():
        if entry.is_file() and entry.name.rsplit(".", 1)[-1] == str(index):
            return entry
    return None


@app.post("/api/peers/shards")
async def receive_shard(
    shard: UploadFile = File(...),
    origin_node: str = Form(...),
    origin_url: str = Form(""),
    file_id: str = Form(...),
    index: int = Form(...),
    name: str = Form(...),
) -> Dict[str, Any]:
    """Store one shard on behalf of a peer and report the digest written.

    The origin compares that digest to its own before dropping its copy, so a
    truncated or corrupted transfer can never cause silent data loss.
    """
    config = config_store.load()
    usage = system_service.collective_usage(COLLECTIVE_PATH, config, _list_all_tree())
    if not usage["accepting_writes"]:
        raise HTTPException(status_code=507, detail="node is above its storage watermark")

    safe_name = Path(name).name
    if not safe_name or safe_name.rsplit(".", 1)[-1] != str(index):
        raise HTTPException(status_code=400, detail="shard name does not match its index")

    directory = _replica_dir(origin_node, file_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = await shard.read()
    (directory / safe_name).write_bytes(payload)

    _replica_index_write(origin_node, file_id, origin_url, index, safe_name, len(payload))
    return {
        "stored": True,
        "digest": replication.digest(payload),
        "bytes": len(payload),
        "node_id": NODE_ID,
    }


def _replica_index_path() -> Path:
    return COLLECTIVE_PATH / "replicas.json"


def _replica_index_read() -> Dict[str, Any]:
    path = _replica_index_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _replica_index_write(
    origin_node: str, file_id: str, origin_url: str, index: int, name: str, size: int
) -> None:
    data = _replica_index_read()
    node = data.setdefault(origin_node, {"origin_url": origin_url, "files": {}})
    if origin_url:
        node["origin_url"] = origin_url
    entry = node["files"].setdefault(file_id, {"shards": {}})
    entry["shards"][str(index)] = {"name": name, "size": size}
    try:
        _replica_index_path().write_text(json.dumps(data, indent=2))
    except OSError:
        pass


@app.get("/api/peers/shards")
async def list_replicas() -> Dict[str, Any]:
    """What this node is holding for other nodes."""
    data = _replica_index_read()
    nodes = []
    total_shards = 0
    total_bytes = 0
    for origin_node, node in data.items():
        shard_count = sum(len(entry["shards"]) for entry in node.get("files", {}).values())
        byte_count = sum(
            shard.get("size", 0)
            for entry in node.get("files", {}).values()
            for shard in entry["shards"].values()
        )
        total_shards += shard_count
        total_bytes += byte_count
        nodes.append(
            {
                "origin_node": origin_node,
                "origin_url": node.get("origin_url", ""),
                "files": len(node.get("files", {})),
                "shards": shard_count,
                "bytes": byte_count,
            }
        )
    return {"nodes": nodes, "shards": total_shards, "bytes": total_bytes}


@app.get("/api/peers/shards/{origin_node}/{file_id}/{index}")
async def serve_replica(origin_node: str, file_id: str, index: int):
    """Hand a stored shard back to the node that owns it."""
    path = _replica_path(origin_node, file_id, index)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="shard not held by this node")
    return StreamingResponse(open(str(path), "rb"), media_type="application/octet-stream")


@app.delete("/api/peers/shards/{origin_node}/{file_id}")
async def drop_replicas(origin_node: str, file_id: str) -> Dict[str, Any]:
    """Drop every shard held for one of a peer's files."""
    directory = _replica_dir(origin_node, file_id)
    removed = 0
    if directory.is_dir():
        removed = len([entry for entry in directory.iterdir() if entry.is_file()])
        shutil.rmtree(str(directory), ignore_errors=True)
    data = _replica_index_read()
    if origin_node in data:
        data[origin_node].get("files", {}).pop(file_id, None)
        try:
            _replica_index_path().write_text(json.dumps(data, indent=2))
        except OSError:
            pass
    return {"dropped": removed}


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
# Contract routes
# ---------------------------------------------------------------------------


@app.get("/api/contracts/tiers", response_model=List[TierConfig])
async def list_tiers() -> List[TierConfig]:
    """Return the configuration for every contract tier."""
    return list(TIER_CONFIGS.values())


@app.post("/api/contracts", response_model=PeerContract)
async def create_contract(body: ContractCreateRequest) -> PeerContract:
    """Establish a new peer contract."""
    return contract_mgr.create_contract(body.peer_url, body.peer_node_id, body.tier)


@app.get("/api/contracts", response_model=List[ContractSummary])
async def list_contracts(
    status: Optional[str] = None,
) -> List[ContractSummary]:
    """List all contracts with optional status filter."""
    if status:
        try:
            cs = ContractStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
        return [
            s
            for s in contract_mgr.list_summaries()
            if s.status == cs
        ]
    return contract_mgr.list_summaries()


@app.get("/api/contracts/{contract_id}", response_model=PeerContract)
async def get_contract(contract_id: str) -> PeerContract:
    """Get full contract details including QoS and recent challenges."""
    c = contract_mgr.get_contract(contract_id)
    if c is None:
        raise HTTPException(404, "Contract not found")
    return c


@app.patch("/api/contracts/{contract_id}/tier")
async def change_tier(contract_id: str, body: Dict[str, Any]) -> PeerContract:
    """Change a contract's tier (hot/warm/cold)."""
    tier_str = body.get("tier", "")
    try:
        tier = ContractTier(tier_str)
    except ValueError:
        raise HTTPException(400, f"Invalid tier: {tier_str}")
    c = contract_mgr.update_tier(contract_id, tier)
    if c is None:
        raise HTTPException(404, "Contract not found")
    return c


@app.post("/api/contracts/{contract_id}/evict")
async def evict_peer(contract_id: str) -> Dict[str, Any]:
    """Manually evict a peer and trigger reciprocal shard drop."""
    c = contract_mgr.evict_contract(contract_id)
    if c is None:
        raise HTTPException(404, "Contract not found")
    dropped = contract_mgr.execute_reciprocal_eviction(contract_id, PROC_DIR)
    return {"evicted": True, "shards_dropped": dropped}


@app.delete("/api/contracts/{contract_id}")
async def delete_contract(contract_id: str) -> Dict[str, bool]:
    """Remove a contract entirely."""
    if not contract_mgr.remove_contract(contract_id):
        raise HTTPException(404, "Contract not found")
    return {"deleted": True}


@app.post("/api/contracts/{contract_id}/shards/theirs")
async def register_shard_they_hold(
    contract_id: str, body: Dict[str, Any]
) -> Dict[str, bool]:
    """Register that a peer is holding a shard for us."""
    shard_id = body.get("shard_id", "")
    size_bytes = body.get("size_bytes", 0)
    if not shard_id:
        raise HTTPException(400, "shard_id required")
    contract_mgr.register_shard_held_for_us(contract_id, shard_id, size_bytes)
    return {"registered": True}


@app.post("/api/contracts/{contract_id}/shards/ours")
async def register_shard_we_hold(
    contract_id: str, body: Dict[str, Any]
) -> Dict[str, bool]:
    """Register that we are holding a shard for a peer."""
    shard_id = body.get("shard_id", "")
    size_bytes = body.get("size_bytes", 0)
    if not shard_id:
        raise HTTPException(400, "shard_id required")
    contract_mgr.register_shard_we_hold(contract_id, shard_id, size_bytes)
    return {"registered": True}


@app.post("/api/contracts/{contract_id}/challenge")
async def issue_challenge(contract_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Issue a proof-of-storage challenge to a peer.

    Requires shard_id and shard_path (local path to our copy of the shard,
    used to generate the expected answer).
    """
    shard_id = body.get("shard_id", "")
    shard_path_str = body.get("shard_path", "")
    if not shard_id or not shard_path_str:
        raise HTTPException(400, "shard_id and shard_path required")
    shard_path = Path(shard_path_str)
    if not shard_path.exists():
        raise HTTPException(400, "Local shard copy not found")
    record = contract_mgr.issue_challenge(contract_id, shard_id, shard_path)
    if record is None:
        raise HTTPException(422, "Could not generate challenge")
    return record.model_dump()


@app.post("/api/contracts/challenge/respond", response_model=ChallengeResponse)
async def respond_challenge(body: ChallengeRequest) -> ChallengeResponse:
    """Respond to an incoming proof-of-storage challenge from a peer.

    Reads the requested byte positions from our local copy of the shard
    and returns the HMAC proof.
    """
    resp = contract_mgr.handle_incoming_challenge(body, TREE_DIR)
    if resp is None:
        raise HTTPException(404, "Shard not found locally")
    return resp


@app.post("/api/contracts/{contract_id}/challenge/{challenge_id}/resolve")
async def resolve_challenge_endpoint(
    contract_id: str, challenge_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Resolve a pending challenge with a peer's proof."""
    proof = body.get("proof")
    response_ms = body.get("response_ms")
    timed_out = body.get("timed_out", False)
    contract = contract_mgr.resolve_challenge(
        challenge_id, proof, response_ms, timed_out
    )
    if contract is None:
        raise HTTPException(404, "Challenge or contract not found")
    return {
        "passed": not timed_out and proof is not None,
        "status": contract.status.value,
        "qos_score": contract.qos.score,
        "violations": contract.violations,
    }


@app.get("/api/contracts/health/summary")
async def contracts_health() -> Dict[str, Any]:
    """Network-wide contract health dashboard."""
    return contract_mgr.get_network_health()


# ---------------------------------------------------------------------------
# System & Infrastructure
# ---------------------------------------------------------------------------


def _contract_health_safe() -> Dict[str, Any]:
    try:
        return contract_mgr.get_network_health()
    except Exception:
        return {}


@app.get("/api/system/overview")
async def system_overview() -> Dict[str, Any]:
    """Host telemetry plus collective quota, shard durability and peer state."""
    return system_service.build_overview(
        root=COLLECTIVE_PATH,
        node_id=NODE_ID,
        config=config_store.load(),
        files=_list_all_tree(),
        peers=list(_peers.values()),
        contract_health=_contract_health_safe(),
        hosted_for_peers=await list_replicas(),
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@app.get("/api/config")
async def get_config() -> Dict[str, Any]:
    return {"config": config_store.load(), "schema": describe_settings()}


@app.put("/api/config")
async def put_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Apply dotted-path updates, e.g. {"storage.quota_bytes": "500GB"}."""
    updates = body.get("updates") if isinstance(body.get("updates"), dict) else body
    try:
        config, changes = apply_updates(
            config_store, updates, source="api", actor=body.get("actor", "operator")
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"config": config, "changes": changes}


@app.get("/api/config/audit")
async def get_config_audit(limit: int = Query(25, ge=1, le=200)) -> Dict[str, Any]:
    return {"entries": config_store.recent_audit(limit)}


# ---------------------------------------------------------------------------
# Agent / section chat
# ---------------------------------------------------------------------------


@app.get("/api/agent/providers")
async def agent_providers() -> Dict[str, Any]:
    config = config_store.load()
    return {
        "providers": agent_service.provider_status(),
        "active": config.get("agent", {}).get("provider", "codewhale"),
        "model": config.get("agent", {}).get("model", ""),
    }


def _chat_context(section: str) -> Dict[str, Any]:
    """The live state handed to the agent for grounding."""
    files = _list_all_tree()
    config = config_store.load()
    usage = system_service.collective_usage(COLLECTIVE_PATH, config, files)
    peers = list(_peers.values())
    context: Dict[str, Any] = {
        "section": section,
        "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "node_id": NODE_ID,
        "files": len(files),
        "collective": usage,
        "peers_total": len(peers),
        "peers_online": len([peer for peer in peers if peer.get("healthy")]),
    }
    if section == "files":
        tree = files_service.build_tree(files, folder_store.load(), _file_statuses)
        context["folders"] = [folder["path"] for folder in tree["folders"] if folder["path"]]
        context["recent_files"] = [
            {
                "name": entry["name"],
                "folder": entry["folder"],
                "size": entry["size"],
                "status": entry["status"],
                "shards": f"{entry['shards_available']}/{entry['shards_total']}",
            }
            for entry in sorted(
                tree["files"], key=lambda item: item["created_at"], reverse=True
            )[:20]
        ]
    else:
        context["contracts"] = _contract_health_safe()
    return context


@app.post("/api/chat")
async def chat(body: Dict[str, Any]) -> Dict[str, Any]:
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    section = (body.get("section") or "system").strip().lower()
    history = body.get("history") if isinstance(body.get("history"), list) else []

    result = await agent_service.run_chat(
        store=config_store,
        section=section,
        message=message,
        history=history,
        context=_chat_context(section),
        provider_override=body.get("provider"),
    )
    if result.get("applied"):
        await _broadcast_status(
            {
                "type": "config",
                "section": section,
                "changes": result["applied"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Start contract enforcement loop on startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _start_contract_enforcement():
    await contract_mgr.start()


@app.on_event("shutdown")
async def _stop_contract_enforcement():
    await contract_mgr.stop()


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
