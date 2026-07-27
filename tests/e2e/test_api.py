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
import os
import pytest
import pytest_asyncio
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# These tests MUTATE config (they PUT contracts.max_peers, drive the quota, and
# upload and delete files), so the default must never be a real node. 8021 is a
# scratch port — the same one Playwright's throwaway server uses — not 8000
# (commonly held by something else, whose 404s read as "every endpoint is
# missing") and emphatically not 8010, which docker-compose publishes for the
# live node. Point CFS_API_URL at a scratch node started with its own
# COLLECTIVE_PATH; see docs/TESTING.md.
BASE_URL = os.environ.get("CFS_API_URL", "http://localhost:8021")
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


# A node holding more than this is not a scratch node, and wiping it is
# certainly not what the person running the tests meant. A handful of leftovers
# from a previous run is expected; fifty files is someone's cluster.
SCRATCH_MAX_FILES = 5


async def _guard_scratch_node(client: httpx.AsyncClient) -> None:
    """Refuse to wipe a node that looks like it holds real data.

    This suite deletes every file on its target before and after each test. If
    it is pointed at a live node — an easy mistake, the URL is one env var —
    that is unrecoverable data loss with no prompt and no undo. It has already
    happened once. Counting first turns a silent wipe into a failed test.
    """
    resp = await client.get(f"{API_PREFIX}/files")
    if resp.status_code != 200:
        return
    files = resp.json()
    count = len(files if isinstance(files, list) else files.get("files", []))
    if count > SCRATCH_MAX_FILES and not os.environ.get("CFS_ALLOW_DESTRUCTIVE"):
        pytest.exit(
            f"Refusing to run: {BASE_URL} holds {count} files, so it is not a "
            f"scratch node. This suite deletes every file on its target. Point "
            f"CFS_API_URL at a scratch node (see docs/TESTING.md), or set "
            f"CFS_ALLOW_DESTRUCTIVE=1 if you genuinely mean to wipe it.",
            returncode=2,
        )


@pytest_asyncio.fixture(autouse=True)
async def cleanup_files(client: httpx.AsyncClient):
    """
    Delete all files from the API before *and* after every test so each test
    starts from a clean state regardless of execution order.
    """
    await _guard_scratch_node(client)
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


# ---------------------------------------------------------------------------
# Files explorer: folder tree, browsing, move/rename
# ---------------------------------------------------------------------------


@pytest.mark.api
@pytest.mark.asyncio
async def test_upload_into_a_folder_shows_in_the_tree(client: httpx.AsyncClient):
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file("tree-file.txt", b"in a folder"),
        data={"folder": "alpha/beta"},
    )
    assert resp.status_code in (200, 201), resp.text
    file_id = resp.json()["id"]
    await _wait_for_file(client, file_id)

    tree = (await client.get(f"{API_PREFIX}/files/tree")).json()
    paths = {folder["path"] for folder in tree["folders"]}
    assert {"alpha", "alpha/beta"} <= paths

    entry = next(item for item in tree["files"] if item["id"] == file_id)
    assert entry["folder"] == "alpha/beta"
    assert entry["path"] == "alpha/beta/tree-file.txt"
    assert entry["shards_total"] >= 1
    assert entry["shards_available"] == entry["shards_total"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_browse_returns_direct_children_and_breadcrumbs(client: httpx.AsyncClient):
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file("browse-me.txt", b"hello"),
        data={"folder": "docs/reports"},
    )
    await _wait_for_file(client, resp.json()["id"])

    docs = (await client.get(f"{API_PREFIX}/files/browse", params={"path": "docs"})).json()
    assert [folder["name"] for folder in docs["folders"]] == ["reports"]
    assert docs["files"] == []

    reports = (await client.get(f"{API_PREFIX}/files/browse", params={"path": "docs/reports"})).json()
    assert [item["name"] for item in reports["files"]] == ["browse-me.txt"]
    assert [crumb["name"] for crumb in reports["breadcrumbs"]] == ["All Files", "docs", "reports"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_browse_unknown_folder_is_404(client: httpx.AsyncClient):
    resp = await client.get(f"{API_PREFIX}/files/browse", params={"path": "no/such/folder"})
    assert resp.status_code == 404


@pytest.mark.api
@pytest.mark.asyncio
async def test_folder_traversal_is_rejected(client: httpx.AsyncClient):
    resp = await client.post(f"{API_PREFIX}/folders", json={"path": "../../etc"})
    assert resp.status_code == 400
    assert ".." in resp.json()["detail"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_rename_and_move_a_file(client: httpx.AsyncClient):
    resp = await client.post(
        f"{API_PREFIX}/files/upload", files=_make_upload_file("original.txt", b"move me")
    )
    file_id = resp.json()["id"]
    await _wait_for_file(client, file_id)

    patched = await client.patch(
        f"{API_PREFIX}/files/{file_id}", json={"name": "renamed.txt", "folder": "moved/here"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "renamed.txt"
    assert patched.json()["folder"] == "moved/here"

    listing = (await client.get(f"{API_PREFIX}/files/browse", params={"path": "moved/here"})).json()
    assert [item["name"] for item in listing["files"]] == ["renamed.txt"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_rename_collision_is_rejected(client: httpx.AsyncClient):
    first = await client.post(f"{API_PREFIX}/files/upload", files=_make_upload_file("a.txt", b"one"))
    second = await client.post(f"{API_PREFIX}/files/upload", files=_make_upload_file("b.txt", b"two"))
    await _wait_for_file(client, first.json()["id"])
    await _wait_for_file(client, second.json()["id"])

    resp = await client.patch(f"{API_PREFIX}/files/{second.json()['id']}", json={"name": "a.txt"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_deleting_a_folder_keeps_its_files(client: httpx.AsyncClient):
    resp = await client.post(
        f"{API_PREFIX}/files/upload",
        files=_make_upload_file("survivor.txt", b"keep me"),
        data={"folder": "temporary"},
    )
    file_id = resp.json()["id"]
    await _wait_for_file(client, file_id)

    deleted = await client.request("DELETE", f"{API_PREFIX}/folders", params={"path": "temporary"})
    assert deleted.status_code == 200
    assert deleted.json()["files_moved_to_root"] == 1

    still_there = await client.get(f"{API_PREFIX}/files/{file_id}")
    assert still_there.status_code == 200
    assert still_there.json()["folder"] is None


# ---------------------------------------------------------------------------
# System overview
# ---------------------------------------------------------------------------


@pytest.mark.api
@pytest.mark.asyncio
async def test_system_overview_shape(client: httpx.AsyncClient):
    resp = await client.get(f"{API_PREFIX}/system/overview")
    assert resp.status_code == 200
    data = resp.json()

    for key in ("hostname", "node_id", "cpu", "memory", "disks", "network", "collective", "erasure"):
        assert key in data, f"missing {key}"

    assert data["cpu"]["id"] == "cpu"
    assert data["collective"]["quota_bytes"] > 0
    assert data["erasure"]["total_shards"] == (
        data["erasure"]["data_shards"] + data["erasure"]["parity_shards"]
    )
    for iface in data["network"]:
        assert "virtual" in iface


@pytest.mark.api
@pytest.mark.asyncio
async def test_overview_tracks_stored_shards(client: httpx.AsyncClient):
    before = (await client.get(f"{API_PREFIX}/system/overview")).json()["collective"]

    resp = await client.post(
        f"{API_PREFIX}/files/upload", files=_make_upload_file("counted.bin", b"x" * 2048)
    )
    await _wait_for_file(client, resp.json()["id"])

    after = (await client.get(f"{API_PREFIX}/system/overview")).json()["collective"]
    assert after["files"] == before["files"] + 1
    assert after["shards_total"] > before["shards_total"]
    assert after["shards_missing"] == 0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.mark.api
@pytest.mark.asyncio
async def test_config_get_returns_config_and_schema(client: httpx.AsyncClient):
    resp = await client.get(f"{API_PREFIX}/config")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["config"]["erasure"]["data_shards"] >= 1
    fields = {spec["field"] for spec in payload["schema"]}
    assert "storage.quota_bytes" in fields


@pytest.mark.api
@pytest.mark.asyncio
async def test_config_put_applies_and_audits(client: httpx.AsyncClient):
    resp = await client.put(f"{API_PREFIX}/config", json={"updates": {"contracts.max_peers": 37}})
    assert resp.status_code == 200
    assert resp.json()["config"]["contracts"]["max_peers"] == 37

    audit = (await client.get(f"{API_PREFIX}/config/audit", params={"limit": 5})).json()
    fields = [c["field"] for entry in audit["entries"] for c in entry["changes"]]
    assert "contracts.max_peers" in fields


@pytest.mark.api
@pytest.mark.asyncio
async def test_config_rejects_impossible_quota(client: httpx.AsyncClient):
    before = (await client.get(f"{API_PREFIX}/config")).json()["config"]["storage"]["quota_bytes"]
    resp = await client.put(f"{API_PREFIX}/config", json={"updates": {"storage.quota_bytes": "9000PB"}})
    assert resp.status_code == 400
    after = (await client.get(f"{API_PREFIX}/config")).json()["config"]["storage"]["quota_bytes"]
    assert after == before


@pytest.mark.api
@pytest.mark.asyncio
async def test_erasure_config_drives_the_encoder(client: httpx.AsyncClient):
    await client.put(
        f"{API_PREFIX}/config",
        json={"updates": {"erasure.data_shards": 4, "erasure.parity_shards": 2}},
    )
    resp = await client.post(
        f"{API_PREFIX}/files/upload", files=_make_upload_file("shaped.bin", b"y" * 8192)
    )
    file_id = resp.json()["id"]
    await _wait_for_file(client, file_id)

    detail = (await client.get(f"{API_PREFIX}/files/{file_id}")).json()
    # The encoder writes one file per shard; a 4+2 layout must produce fewer
    # shards than the 8+4 default would have.
    assert detail["chunks"] <= 8, detail["chunks"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_upload_over_the_limit_is_rejected(client: httpx.AsyncClient):
    # 1 MiB is the smallest cap the schema allows.
    limit = await client.put(
        f"{API_PREFIX}/config", json={"updates": {"upload.max_file_bytes": "1MB"}}
    )
    assert limit.status_code == 200, limit.text
    try:
        resp = await client.post(
            f"{API_PREFIX}/files/upload", files=_make_upload_file("too-big.bin", b"z" * (2 * 1024 ** 2))
        )
        assert resp.status_code == 413, resp.text
        assert "upload limit" in resp.json()["detail"]
    finally:
        await client.put(
            f"{API_PREFIX}/config", json={"updates": {"upload.max_file_bytes": "1GB"}}
        )


# ---------------------------------------------------------------------------
# Agent / chat
# ---------------------------------------------------------------------------


@pytest.mark.api
@pytest.mark.asyncio
async def test_providers_lists_all_backends(client: httpx.AsyncClient):
    payload = (await client.get(f"{API_PREFIX}/agent/providers")).json()
    ids = [entry["id"] for entry in payload["providers"]]
    assert ids == ["codewhale", "claude", "codex", "builtin"]
    assert payload["active"] in ids


@pytest.mark.api
@pytest.mark.asyncio
async def test_chat_changes_configuration(client: httpx.AsyncClient):
    await client.put(f"{API_PREFIX}/config", json={"updates": {"storage.quota_bytes": "30GB"}})
    resp = await client.post(
        f"{API_PREFIX}/chat",
        json={"section": "system", "message": "allocate 70GB", "provider": "builtin"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["error"] is None
    assert payload["applied"][0]["field"] == "storage.quota_bytes"
    assert payload["applied"][0]["after"] == 70 * 1024 ** 3

    config = (await client.get(f"{API_PREFIX}/config")).json()["config"]
    assert config["storage"]["quota_bytes"] == 70 * 1024 ** 3


@pytest.mark.api
@pytest.mark.asyncio
async def test_chat_refuses_an_invalid_change(client: httpx.AsyncClient):
    resp = await client.post(
        f"{API_PREFIX}/chat",
        json={"section": "system", "message": "set parity shards to 90", "provider": "builtin"},
    )
    payload = resp.json()
    assert payload["applied"] == []
    assert "at most 32" in payload["error"]


@pytest.mark.api
@pytest.mark.asyncio
async def test_chat_requires_a_message(client: httpx.AsyncClient):
    resp = await client.post(f"{API_PREFIX}/chat", json={"section": "system", "message": "  "})
    assert resp.status_code == 400
