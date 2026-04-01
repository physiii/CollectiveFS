"""
tests/e2e/test_api.py

Async Python API tests for the CollectiveFS FastAPI backend.

Requires:
    pip install pytest pytest-asyncio httpx

Run with:
    pytest tests/e2e/test_api.py -v -m api
"""

import asyncio
import io
import pytest
import pytest_asyncio
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
API_PREFIX = "/api"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    """
    Async HTTP client, one per test.

    Using function scope (the default) avoids event-loop-closed errors
    with pytest-asyncio's auto mode, where each test gets its own loop.
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def cleanup_files(client: httpx.AsyncClient):
    """
    Delete all files from the API before *and* after every test so each test
    starts from a clean state regardless of execution order.
    """
    await _delete_all_files(client)
    yield
    await _delete_all_files(client)


async def _delete_all_files(client: httpx.AsyncClient) -> None:
    """Helper: fetch the file list and DELETE every entry."""
    resp = await client.get(f"{API_PREFIX}/files")
    if resp.status_code != 200:
        return
    files = resp.json()
    for f in files:
        fid = f.get("id")
        if fid:
            await client.delete(f"{API_PREFIX}/files/{fid}")


def _make_upload_file(
    filename: str = "test.txt",
    content: bytes = b"Hello CollectiveFS",
    mime: str = "text/plain",
):
    """Return an httpx-compatible files dict for multipart upload."""
    return {"file": (filename, io.BytesIO(content), mime)}


async def _wait_for_file(client: httpx.AsyncClient, file_id: str, timeout: float = 10.0):
    """Poll until a file's status is no longer 'processing'."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"{API_PREFIX}/files/{file_id}")
        if resp.status_code == 200:
            body = resp.json()
            if body.get("status") != "processing":
                return body
        await asyncio.sleep(0.3)
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.api
@pytest.mark.asyncio
async def test_health_endpoint(client: httpx.AsyncClient):
    """GET /api/health should return 200 with status: ok."""
    resp = await client.get(f"{API_PREFIX}/health")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} – {resp.text}"
    body = resp.json()
    assert body.get("status") == "ok", f"Expected status 'ok', got: {body}"


@pytest.mark.api
@pytest.mark.asyncio
async def test_files_list_empty(client: httpx.AsyncClient):
    """GET /api/files returns an empty list when no files are stored."""
    resp = await client.get(f"{API_PREFIX}/files")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} – {resp.text}"
    body = resp.json()
    assert isinstance(body, list), f"Expected a list, got: {type(body)}"
    assert len(body) == 0, f"Expected empty list, got {len(body)} items"


@pytest.mark.api
@pytest.mark.asyncio
async def test_stats_endpoint(client: httpx.AsyncClient):
    """GET /api/stats returns a valid stats object with expected keys."""
    resp = await client.get(f"{API_PREFIX}/stats")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} – {resp.text}"
    body = resp.json()

    # Verify required top-level keys are present.
    required_keys = {"total_files", "storage_used_bytes", "encryption", "erasure_coding"}
    missing = required_keys - body.keys()
    assert not missing, f"Stats response missing keys: {missing}"

    assert isinstance(body["total_files"], int), "total_files should be an int"
    assert isinstance(body["storage_used_bytes"], int), "storage_used_bytes should be an int"
    assert isinstance(body["encryption"], str), "encryption should be a string"
    assert isinstance(body["erasure_coding"], str), "erasure_coding should be a string"


@pytest.mark.api
@pytest.mark.asyncio
async def test_upload_and_list(client: httpx.AsyncClient):
    """POST /api/files/upload then GET /api/files returns the uploaded file."""
    filename = "upload_and_list.txt"
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file(filename, b"upload and list content"),
    )
    assert resp.status_code in (200, 201), (
        f"Upload failed: {resp.status_code} – {resp.text}"
    )
    await _wait_for_file(client, resp.json()["id"])

    # The uploaded file must appear in the file list.
    list_resp = await client.get(f"{API_PREFIX}/files")
    assert list_resp.status_code == 200
    files = list_resp.json()
    names = [f.get("name") for f in files]
    assert filename in names, f"'{filename}' not found in file list: {names}"


@pytest.mark.api
@pytest.mark.asyncio
async def test_upload_returns_correct_structure(client: httpx.AsyncClient):
    """Upload response must include 'id', 'name', and 'status' fields."""
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file("structure_check.txt", b"structure content"),
    )
    assert resp.status_code in (200, 201), (
        f"Upload failed: {resp.status_code} – {resp.text}"
    )
    body = resp.json()

    assert "id" in body, f"'id' missing from upload response: {body}"
    assert "name" in body, f"'name' missing from upload response: {body}"
    assert "status" in body, f"'status' missing from upload response: {body}"

    assert isinstance(body["id"], str) and body["id"], "id must be a non-empty string"
    assert body["name"] == "structure_check.txt", (
        f"Expected name 'structure_check.txt', got '{body['name']}'"
    )
    assert isinstance(body["status"], str) and body["status"], (
        "status must be a non-empty string"
    )


@pytest.mark.api
@pytest.mark.asyncio
async def test_delete_file(client: httpx.AsyncClient):
    """Upload a file, delete it, verify it no longer appears in the list."""
    # Upload
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file("to_delete.txt", b"delete me"),
    )
    assert resp.status_code in (200, 201), f"Upload failed: {resp.status_code} – {resp.text}"
    file_id = resp.json()["id"]
    await _wait_for_file(client, file_id)

    # Delete
    del_resp = await client.delete(f"{API_PREFIX}/files/{file_id}")
    assert del_resp.status_code in (200, 204), (
        f"Delete failed: {del_resp.status_code} – {del_resp.text}"
    )

    # Verify gone
    list_resp = await client.get(f"{API_PREFIX}/files")
    assert list_resp.status_code == 200
    ids = [f.get("id") for f in list_resp.json()]
    assert file_id not in ids, f"File {file_id} still present after delete"


@pytest.mark.api
@pytest.mark.asyncio
async def test_get_nonexistent_file(client: httpx.AsyncClient):
    """GET /api/files/<nonexistent-id> returns 404."""
    resp = await client.get(f"{API_PREFIX}/files/nonexistent-id-abc123")
    assert resp.status_code == 404, (
        f"Expected 404 for nonexistent file, got {resp.status_code}"
    )


@pytest.mark.api
@pytest.mark.asyncio
async def test_delete_nonexistent_file(client: httpx.AsyncClient):
    """DELETE /api/files/<nonexistent-id> returns 404."""
    resp = await client.delete(f"{API_PREFIX}/files/nonexistent-id-xyz789")
    assert resp.status_code == 404, (
        f"Expected 404 for nonexistent delete, got {resp.status_code}"
    )


@pytest.mark.api
@pytest.mark.asyncio
async def test_upload_multiple_files(client: httpx.AsyncClient):
    """Upload 3 distinct files and verify all appear in GET /api/files."""
    filenames = [
        ("multi_one.txt", b"content one"),
        ("multi_two.txt", b"content two"),
        ("multi_three.txt", b"content three"),
    ]

    uploaded_ids = []
    for fname, fcontent in filenames:
        resp = await client.post(
            f"{API_PREFIX}/files/upload",
            files=_make_upload_file(fname, fcontent),
        )
        assert resp.status_code in (200, 201), (
            f"Upload of '{fname}' failed: {resp.status_code} – {resp.text}"
        )
        uploaded_ids.append(resp.json()["id"])

    assert len(uploaded_ids) == 3, "Should have uploaded exactly 3 files"

    for fid in uploaded_ids:
        await _wait_for_file(client, fid)

    list_resp = await client.get(f"{API_PREFIX}/files")
    assert list_resp.status_code == 200
    files = list_resp.json()
    returned_names = {f.get("name") for f in files}
    expected_names = {fname for fname, _ in filenames}

    missing = expected_names - returned_names
    assert not missing, f"These uploaded files are missing from the list: {missing}"
