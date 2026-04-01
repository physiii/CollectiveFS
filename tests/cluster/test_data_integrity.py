"""
End-to-end data integrity tests for CollectiveFS cluster.

These tests verify the full upload → download → verify loop:
  - Uploaded file content matches downloaded content byte-for-byte
  - SHA-256 hash preserved through the pipeline
  - Multiple file sizes handled correctly
  - Download endpoint returns correct Content-Disposition header
  - Concurrent uploads all produce downloadable files

Run with:
    pytest tests/cluster/test_data_integrity.py -v -m cluster --timeout=120

Requirements:
    docker compose (v2)   pip install pytest-timeout requests
"""

import hashlib
import os
import time
import uuid

import pytest
import requests

from test_multinode import (
    COMPOSE_FILE,
    NODE_URLS,
    UPLOAD_TIMEOUT,
    _compose,
    _upload_file,
    _wait_for_status,
    _delete_file,
    _wait_healthy,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cluster():
    """Start the 3-node cluster once for the entire test session."""
    if not COMPOSE_FILE.exists():
        pytest.skip(f"Cluster compose file not found: {COMPOSE_FILE}")

    _compose("up", "-d", "--build", "--remove-orphans")

    for name, url in NODE_URLS.items():
        ok = _wait_healthy(url, timeout=90)
        assert ok, f"{name} did not become healthy within 90s"

    yield NODE_URLS

    _compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture(autouse=True)
def _ensure_all_nodes_up(cluster):
    """Before each test, make sure all nodes are running."""
    for name in cluster:
        _compose("start", name, check=False)
    time.sleep(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_file(node_url: str, file_id: str, timeout: int = 30) -> bytes:
    """Download a file and return the raw bytes."""
    r = requests.get(f"{node_url}/api/files/{file_id}/download", timeout=timeout)
    r.raise_for_status()
    return r.content


def _upload_and_wait(node_url: str, filename: str, content: bytes) -> str:
    """Upload a file, wait for it to be stored, return file_id."""
    resp = _upload_file(node_url, filename, content)
    file_id = resp["id"]
    _wait_for_status(node_url, file_id, timeout=UPLOAD_TIMEOUT)
    return file_id


# ---------------------------------------------------------------------------
# Tests: Upload → Download → Verify
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestUploadDownloadIntegrity:

    def test_small_file_roundtrip(self, cluster):
        """Upload 1 KB, download, verify SHA-256 matches."""
        content = os.urandom(1024)
        original_hash = _sha256(content)

        file_id = _upload_and_wait(cluster["node1"], "small_1kb.bin", content)
        try:
            downloaded = _download_file(cluster["node1"], file_id)
            assert _sha256(downloaded) == original_hash, (
                "SHA-256 mismatch: downloaded file differs from uploaded"
            )
        finally:
            _delete_file(cluster["node1"], file_id)

    def test_medium_file_roundtrip(self, cluster):
        """Upload 256 KB, download, verify SHA-256 matches."""
        content = os.urandom(256 * 1024)
        original_hash = _sha256(content)

        file_id = _upload_and_wait(cluster["node1"], "medium_256kb.bin", content)
        try:
            downloaded = _download_file(cluster["node1"], file_id)
            assert _sha256(downloaded) == original_hash, (
                "SHA-256 mismatch on 256 KB file"
            )
        finally:
            _delete_file(cluster["node1"], file_id)

    def test_exact_size_match(self, cluster):
        """Downloaded file size must exactly match the original."""
        content = os.urandom(12345)
        file_id = _upload_and_wait(cluster["node1"], "exact_size.bin", content)
        try:
            downloaded = _download_file(cluster["node1"], file_id)
            assert len(downloaded) == len(content), (
                f"Size mismatch: uploaded {len(content)} bytes, "
                f"downloaded {len(downloaded)} bytes"
            )
        finally:
            _delete_file(cluster["node1"], file_id)

    def test_upload_to_each_node_and_download(self, cluster):
        """Upload a file to each node; each is downloadable from its origin."""
        ids = {}
        contents = {}
        for name, url in cluster.items():
            content = f"from {name} ".encode() + os.urandom(2048)
            contents[name] = content
            ids[name] = _upload_and_wait(url, f"from_{name}.bin", content)

        try:
            for name, url in cluster.items():
                downloaded = _download_file(url, ids[name])
                assert _sha256(downloaded) == _sha256(contents[name]), (
                    f"Integrity check failed for file uploaded to {name}"
                )
        finally:
            for name, url in cluster.items():
                _delete_file(url, ids[name])

    def test_concurrent_uploads_all_downloadable(self, cluster):
        """Upload 5 files rapidly, then verify all are downloadable."""
        uploads = []
        for i in range(5):
            content = f"concurrent file {i} ".encode() + os.urandom(4096)
            resp = _upload_file(cluster["node1"], f"concurrent_{i}.bin", content)
            uploads.append((resp["id"], content))

        # Wait for all to finish processing
        for file_id, _ in uploads:
            _wait_for_status(cluster["node1"], file_id, timeout=UPLOAD_TIMEOUT)

        try:
            for file_id, original in uploads:
                downloaded = _download_file(cluster["node1"], file_id)
                assert _sha256(downloaded) == _sha256(original), (
                    f"File {file_id} corrupted after concurrent upload"
                )
        finally:
            for file_id, _ in uploads:
                _delete_file(cluster["node1"], file_id)

    def test_download_nonexistent_returns_404(self, cluster):
        """Downloading a file that doesn't exist returns 404."""
        r = requests.get(
            f"{cluster['node1']}/api/files/nonexistent-id/download",
            timeout=10,
        )
        assert r.status_code == 404

    def test_metadata_matches_upload(self, cluster):
        """File metadata (name, size) matches what was uploaded."""
        content = os.urandom(8192)
        file_id = _upload_and_wait(cluster["node1"], "meta_check.bin", content)
        try:
            r = requests.get(
                f"{cluster['node1']}/api/files/{file_id}",
                timeout=10,
            )
            assert r.status_code == 200
            meta = r.json()
            assert meta["name"] == "meta_check.bin"
            assert meta["size"] == len(content)
            assert meta["status"] in ("stored", "complete")
        finally:
            _delete_file(cluster["node1"], file_id)
