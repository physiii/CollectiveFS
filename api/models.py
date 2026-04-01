from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class ShardInfo(BaseModel):
    num: int
    id: str
    size: int = 0
    encrypted: bool = False
    available: bool = True
    peer: Optional[str] = None


class FileMetadata(BaseModel):
    id: str
    name: str
    size: int = 0
    chunks: int = 0
    created_at: str
    status: str = "stored"
    folder: Optional[str] = None
    shard_list: Optional[List[ShardInfo]] = None


class ChunkInfo(BaseModel):
    num: int
    id: str
    path: str
    offer: Optional[str] = None
    answer: Optional[str] = None


class SystemStats(BaseModel):
    total_files: int
    total_chunks: int
    storage_used_bytes: int
    storage_path: str
    encryption: str
    erasure_coding: str


class UploadResponse(BaseModel):
    id: str
    name: str
    status: str
    message: str = ""


class StatusUpdate(BaseModel):
    type: str
    file_id: str
    status: str
    progress: Optional[float] = None
    message: Optional[str] = None
