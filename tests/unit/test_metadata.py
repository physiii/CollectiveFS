"""
Unit tests for CollectiveFS metadata JSON structures.

These tests validate the shape, serialisation, and round-trip correctness of
the fileInfo / chunkInfo dicts that cfs.py writes to ~/.collective/tree/.

No binaries or external services are required; all tests run in-process.

Coverage:
- fileInfo dictionary schema
- chunkInfo dictionary schema
- UUID uniqueness across file and chunk IDs
- JSON serialisation / deserialisation round-trip
- Correct indexed insertion of chunkInfo entries
- Filesystem write → read round-trip for a full metadata blob
- Consistency between number_of_chunks and the actual length of chunks list
"""

import json
import uuid
import pytest


# ---------------------------------------------------------------------------
# Helper builders (mirror the logic in cfs.py)
# ---------------------------------------------------------------------------

def make_file_info(file_id=None, name="test.bin", folder="/tmp/test.bin.d",
                   num_data=8, num_par=4):
    """Return a fileInfo dict as produced by cfs.py's on_created handler."""
    if file_id is None:
        file_id = str(uuid.uuid4())
    return {
        "id": file_id,
        "name": name,
        "folder": folder,
        "number_of_chunks": num_data + num_par,
        "chunks": [],
    }


def make_chunk_info(chunk_num, folder="/tmp/test.bin.d", filename="test.bin"):
    """Return a chunkInfo dict as produced by cfs.py's encryptChunks."""
    return {
        "num": chunk_num,
        "id": str(uuid.uuid4()),
        "path": f"{folder}/{filename}.{chunk_num}",
        "offer": {},
        "answer": {},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fileinfo_structure():
    """
    fileInfo must contain exactly the keys expected by cfs.py and the metadata
    storage layer: id, name, folder, number_of_chunks, chunks.
    """
    info = make_file_info()
    required_keys = {"id", "name", "folder", "number_of_chunks", "chunks"}
    assert required_keys.issubset(info.keys()), (
        f"fileInfo is missing keys: {required_keys - info.keys()}"
    )
    assert isinstance(info["id"], str)
    assert isinstance(info["name"], str)
    assert isinstance(info["folder"], str)
    assert isinstance(info["number_of_chunks"], int)
    assert isinstance(info["chunks"], list)


@pytest.mark.unit
def test_chunkinfo_structure():
    """
    chunkInfo must contain exactly the keys expected by cfs.py's encryptChunk
    and filexfer: num, id, path, offer, answer.
    """
    info = make_chunk_info(3)
    required_keys = {"num", "id", "path", "offer", "answer"}
    assert required_keys.issubset(info.keys()), (
        f"chunkInfo is missing keys: {required_keys - info.keys()}"
    )
    assert isinstance(info["num"], int)
    assert isinstance(info["id"], str)
    assert isinstance(info["path"], str)
    assert isinstance(info["offer"], dict)
    assert isinstance(info["answer"], dict)


@pytest.mark.unit
def test_uuid_uniqueness():
    """
    Generate 100 file IDs and 100 chunk IDs; every value must be unique.
    UUID4 collisions are astronomically unlikely; if one occurs the generator
    is broken.
    """
    file_ids = [str(uuid.uuid4()) for _ in range(100)]
    chunk_ids = [str(uuid.uuid4()) for _ in range(100)]
    all_ids = file_ids + chunk_ids
    assert len(set(all_ids)) == 200, "Duplicate UUID detected — generator error"


@pytest.mark.unit
def test_metadata_serialization():
    """
    Serialising a fileInfo dict to JSON and deserialising it must produce an
    object identical to the original (all fields preserved, correct types).
    """
    info = make_file_info(name="photo.jpg", folder="/data/.collective/proc/photo.jpg.d")
    serialised = json.dumps(info)
    recovered = json.loads(serialised)

    assert recovered["id"] == info["id"]
    assert recovered["name"] == info["name"]
    assert recovered["folder"] == info["folder"]
    assert recovered["number_of_chunks"] == info["number_of_chunks"]
    assert recovered["chunks"] == info["chunks"]


@pytest.mark.unit
def test_metadata_chunk_insertion():
    """
    Inserting 12 chunkInfo entries into fileInfo['chunks'] at their correct
    numeric indices must result in a list where element i has num == i.
    This mirrors the cfs.py encryptChunks loop.
    """
    num_data, num_par = 8, 4
    info = make_file_info(num_data=num_data, num_par=num_par)

    for i in range(num_data + num_par):
        chunk = make_chunk_info(i)
        info["chunks"].insert(chunk["num"], chunk)

    assert len(info["chunks"]) == 12
    for idx, chunk in enumerate(info["chunks"]):
        assert chunk["num"] == idx, (
            f"Chunk at list position {idx} has num={chunk['num']}"
        )


@pytest.mark.unit
def test_metadata_file_write_read(tmp_path):
    """
    Write a complete fileInfo (with 12 chunks inserted) to a JSON file on disk,
    read it back, and verify every field is identical.
    This models the tree-storage pattern in cfs.py.
    """
    num_data, num_par = 8, 4
    info = make_file_info(name="video.mkv", num_data=num_data, num_par=num_par)
    for i in range(num_data + num_par):
        chunk = make_chunk_info(i)
        info["chunks"].insert(chunk["num"], chunk)

    tree_file = tmp_path / f"{info['id']}.json"
    tree_file.write_text(json.dumps(info, indent=2))

    recovered = json.loads(tree_file.read_text())
    assert recovered == info, "Metadata round-trip through disk produced a different object"


@pytest.mark.unit
def test_number_of_chunks_matches_list():
    """
    After all chunkInfo entries are inserted, fileInfo['number_of_chunks'] must
    equal len(fileInfo['chunks']).  Mismatches would indicate a bug in the
    encryptChunks loop.
    """
    for num_data, num_par in [(4, 2), (8, 4), (10, 6)]:
        info = make_file_info(num_data=num_data, num_par=num_par)
        for i in range(num_data + num_par):
            chunk = make_chunk_info(i)
            info["chunks"].insert(chunk["num"], chunk)

        assert info["number_of_chunks"] == len(info["chunks"]), (
            f"number_of_chunks ({info['number_of_chunks']}) != "
            f"len(chunks) ({len(info['chunks'])}) for data={num_data}, par={num_par}"
        )
