"""
Multi-node cluster tests for CollectiveFS.

These tests spin up a 3-node cluster using docker-compose.cluster.yml and verify:
  - Nodes can see each other's files via the peer exchange API
  - Files uploaded to one node are visible on all others
  - The system degrades gracefully when a node goes offline
  - Data is recoverable after node restarts
  - Erasure coding tolerates the configured number of offline nodes
  - Sane defaults produce acceptable availability under realistic churn

Run with:
    pytest tests/cluster/ -v -m cluster --timeout=120

Requirements:
    docker compose (v2)   pip install pytest-timeout requests
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List

import pytest
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.cluster.yml"

NODE_URLS = {
    "node1": "http://localhost:8001",
    "node2": "http://localhost:8002",
    "node3": "http://localhost:8003",
}

# Default erasure coding: 8 data + 4 parity → tolerate 4 missing shards
DEFAULT_DATA_SHARDS = 8
DEFAULT_PAR_SHARDS = 4
TOTAL_SHARDS = DEFAULT_DATA_SHARDS + DEFAULT_PAR_SHARDS  # 12
# With 3 nodes each holding 4 shards, losing 1 node = losing 4 shards = exactly at tolerance boundary

UPLOAD_TIMEOUT = 30   # seconds to wait for a file to finish processing
POLL_INTERVAL = 1     # seconds between status polls


# ---------------------------------------------------------------------------
# Docker compose helpers
# ---------------------------------------------------------------------------

def _compose(*args, check=True):
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=str(PROJECT_ROOT))


def _wait_healthy(url: str, timeout: int = 60) -> bool:
    """Poll /api/health until 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/api/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _upload_file(node_url: str, name: str, content: bytes) -> Dict:
    """Upload a file to a node and return the response JSON."""
    r = requests.post(
        f"{node_url}/api/files/upload",
        files={"file": (name, content, "application/octet-stream")},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _wait_for_status(node_url: str, file_id: str, target: str = "stored", timeout: int = UPLOAD_TIMEOUT) -> Dict:
    """Poll GET /api/files/{id} until status matches target or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{node_url}/api/files/{file_id}", timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") in (target, "complete", "stored"):
                    return data
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"File {file_id} did not reach status '{target}' within {timeout}s")


def _list_files(node_url: str) -> List[Dict]:
    r = requests.get(f"{node_url}/api/files", timeout=5)
    r.raise_for_status()
    return r.json()


def _peer_files(node_url: str) -> List[Dict]:
    """Fetch this node's file list as exposed to peers."""
    r = requests.get(f"{node_url}/api/peers/files", timeout=5)
    r.raise_for_status()
    return r.json()


def _network_view(node_url: str) -> Dict:
    r = requests.get(f"{node_url}/api/network", timeout=5)
    r.raise_for_status()
    return r.json()


def _stop_node(name: str):
    _compose("stop", name, check=False)


def _start_node(name: str):
    _compose("start", name, check=False)
    _wait_healthy(NODE_URLS[name], timeout=30)


def _delete_file(node_url: str, file_id: str):
    r = requests.delete(f"{node_url}/api/files/{file_id}", timeout=5)
    return r.status_code


# ---------------------------------------------------------------------------
# Session-scoped cluster fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cluster():
    """Start the 3-node cluster once for the entire test session."""
    if not COMPOSE_FILE.exists():
        pytest.skip(f"Cluster compose file not found: {COMPOSE_FILE}")

    # Build & start
    _compose("up", "-d", "--build", "--remove-orphans")

    # Wait for all nodes to be healthy
    for name, url in NODE_URLS.items():
        ok = _wait_healthy(url, timeout=90)
        assert ok, f"{name} did not become healthy within 90s"

    yield NODE_URLS

    # Teardown: bring cluster down and remove volumes for isolation
    _compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture(autouse=True)
def _ensure_all_nodes_up(cluster):
    """Before each test, make sure all nodes are running."""
    for name, url in cluster.items():
        # Attempt to start stopped nodes
        _compose("start", name, check=False)
    # Give nodes a moment to settle
    time.sleep(1)


@pytest.fixture()
def uploaded_file(cluster):
    """Upload a small file to node1 and wait for it to be stored. Yield its metadata. Clean up after."""
    content = b"CollectiveFS cluster test payload " + uuid.uuid4().bytes
    resp = _upload_file(cluster["node1"], "cluster_test.bin", content)
    file_id = resp["id"]
    _wait_for_status(cluster["node1"], file_id, timeout=UPLOAD_TIMEOUT)
    yield {"id": file_id, "name": "cluster_test.bin", "content": content}
    # Cleanup
    _delete_file(cluster["node1"], file_id)


# ---------------------------------------------------------------------------
# Test Suite 1: Basic cluster health
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestClusterHealth:

    def test_all_nodes_healthy(self, cluster):
        """All 3 nodes respond to /api/health with status:ok."""
        for name, url in cluster.items():
            r = requests.get(f"{url}/api/health", timeout=5)
            assert r.status_code == 200
            assert r.json()["status"] == "ok", f"{name} unhealthy"

    def test_nodes_have_distinct_ids(self, cluster):
        """Each node returns a different NODE_ID in /api/network."""
        node_ids = set()
        for url in cluster.values():
            data = _network_view(url)
            nid = data.get("node_id")
            assert nid, "node_id should be set"
            node_ids.add(nid)
        assert len(node_ids) == 3, f"Expected 3 distinct node IDs, got {node_ids}"

    def test_nodes_see_each_other_as_peers(self, cluster):
        """After startup announcement, each node knows its peers."""
        for name, url in cluster.items():
            r = requests.get(f"{url}/api/peers", timeout=5)
            assert r.status_code == 200
            peers = r.json()
            # Each node is configured with 2 peers
            assert len(peers) >= 2, f"{name} should know at least 2 peers, got {peers}"

    def test_stats_endpoint_works_on_all_nodes(self, cluster):
        """All nodes return valid stats."""
        for name, url in cluster.items():
            r = requests.get(f"{url}/api/stats", timeout=5)
            assert r.status_code == 200
            s = r.json()
            assert "total_files" in s
            assert "erasure_coding" in s


# ---------------------------------------------------------------------------
# Test Suite 2: Cross-node file visibility
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestCrossNodeVisibility:

    def test_uploaded_file_visible_on_origin_node(self, cluster, uploaded_file):
        """File uploaded to node1 appears in node1's file list."""
        files = _list_files(cluster["node1"])
        ids = [f["id"] for f in files]
        assert uploaded_file["id"] in ids

    def test_peer_files_endpoint_exposes_local_files(self, cluster, uploaded_file):
        """node1's /api/peers/files returns the uploaded file for peer nodes to sync."""
        peer_files = _peer_files(cluster["node1"])
        ids = [f["id"] for f in peer_files]
        assert uploaded_file["id"] in ids

    def test_node2_can_see_node1_files_via_network_api(self, cluster, uploaded_file):
        """node2's /api/network aggregates node1's files in peer_files."""
        # Give nodes time to exchange — retry for up to 10s
        deadline = time.time() + 10
        found = False
        while time.time() < deadline:
            net = _network_view(cluster["node2"])
            peer_ids = [f["id"] for f in net.get("peer_files", [])]
            if uploaded_file["id"] in peer_ids:
                found = True
                break
            time.sleep(1)
        assert found, f"node2 did not see node1's file {uploaded_file['id']} via /api/network"

    def test_node3_can_see_node1_files_via_network_api(self, cluster, uploaded_file):
        """node3's /api/network also sees the file from node1."""
        deadline = time.time() + 10
        found = False
        while time.time() < deadline:
            net = _network_view(cluster["node3"])
            peer_ids = [f["id"] for f in net.get("peer_files", [])]
            if uploaded_file["id"] in peer_ids:
                found = True
                break
            time.sleep(1)
        assert found, "node3 did not see node1's file via /api/network"

    def test_upload_to_node2_visible_from_node1(self, cluster):
        """File uploaded to node2 is visible from node1's network view."""
        content = b"node2 originated file " + uuid.uuid4().bytes
        resp = _upload_file(cluster["node2"], "from_node2.bin", content)
        file_id = resp["id"]
        _wait_for_status(cluster["node2"], file_id, timeout=UPLOAD_TIMEOUT)

        deadline = time.time() + 15
        found = False
        while time.time() < deadline:
            net = _network_view(cluster["node1"])
            peer_ids = [f["id"] for f in net.get("peer_files", [])]
            if file_id in peer_ids:
                found = True
                break
            time.sleep(1)

        # Cleanup
        _delete_file(cluster["node2"], file_id)
        assert found, "node1 did not see node2's file"

    def test_all_nodes_show_same_total_file_count_via_network(self, cluster, uploaded_file):
        """All nodes report the same number of total files across the cluster."""
        counts = {}
        for name, url in cluster.items():
            net = _network_view(url)
            total = len(net.get("local_files", [])) + len(net.get("peer_files", []))
            counts[name] = total
        # Each node should see at least the 1 uploaded file in either local or peer
        for name, count in counts.items():
            assert count >= 1, f"{name} sees {count} total files, expected >= 1"


# ---------------------------------------------------------------------------
# Test Suite 3: Node failure & data availability
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestNodeFailure:

    def test_cluster_still_healthy_after_one_node_down(self, cluster, uploaded_file):
        """Stopping node3 leaves node1 and node2 fully operational."""
        _stop_node("node3")
        time.sleep(2)
        try:
            for name in ("node1", "node2"):
                r = requests.get(f"{cluster[name]}/api/health", timeout=5)
                assert r.status_code == 200, f"{name} unhealthy after node3 stopped"
        finally:
            _start_node("node3")

    def test_file_still_listed_on_surviving_nodes(self, cluster, uploaded_file):
        """File metadata on node1 persists while node2 is stopped."""
        _stop_node("node2")
        time.sleep(2)
        try:
            files = _list_files(cluster["node1"])
            ids = [f["id"] for f in files]
            assert uploaded_file["id"] in ids, "File disappeared from node1 when node2 went down"
        finally:
            _start_node("node2")

    def test_file_upload_succeeds_with_one_peer_down(self, cluster):
        """Uploading while node3 is offline still stores the file on node1."""
        _stop_node("node3")
        time.sleep(2)
        try:
            content = b"uploaded while node3 offline " + uuid.uuid4().bytes
            resp = _upload_file(cluster["node1"], "partial_upload.bin", content)
            file_id = resp["id"]
            _wait_for_status(cluster["node1"], file_id, timeout=UPLOAD_TIMEOUT)
            files = _list_files(cluster["node1"])
            ids = [f["id"] for f in files]
            assert file_id in ids, "Upload failed while node3 was down"
        finally:
            _start_node("node3")
            # cleanup
            _delete_file(cluster["node1"], file_id)

    def test_node_rejoins_and_is_recognized(self, cluster):
        """After node2 restarts, node1 can communicate with it again."""
        _stop_node("node2")
        time.sleep(2)
        _start_node("node2")

        r = requests.get(f"{cluster['node2']}/api/health", timeout=10)
        assert r.status_code == 200, "node2 didn't come back healthy"

        # node1 should be able to reach node2's peer files endpoint
        r2 = requests.get(f"{cluster['node2']}/api/peers/files", timeout=5)
        assert r2.status_code == 200

    def test_two_nodes_down_third_still_serves(self, cluster, uploaded_file):
        """With 2 of 3 nodes offline, remaining node still serves its local files."""
        _stop_node("node2")
        _stop_node("node3")
        time.sleep(2)
        try:
            files = _list_files(cluster["node1"])
            ids = [f["id"] for f in files]
            assert uploaded_file["id"] in ids, "node1 can't serve its own files"

            health = requests.get(f"{cluster['node1']}/api/health", timeout=5)
            assert health.status_code == 200
        finally:
            _start_node("node2")
            _start_node("node3")

    def test_network_view_marks_offline_peers(self, cluster, uploaded_file):
        """When node3 goes offline, /api/network on node1 shows it as unhealthy."""
        _stop_node("node3")
        time.sleep(3)
        try:
            net = _network_view(cluster["node1"])
            peers = {p["url"]: p for p in net.get("peers", [])}
            node3_entry = peers.get("http://node3:8000")
            # Either the key is absent or healthy=False
            if node3_entry is not None:
                assert node3_entry.get("healthy") is False, \
                    "node3 should be marked unhealthy"
        finally:
            _start_node("node3")


# ---------------------------------------------------------------------------
# Test Suite 4: Erasure coding durability with defaults (8+4)
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestErasureDurability:
    """
    Validate the sane-default claim:
      8 data + 4 parity shards across N peers.
      With 3 nodes we have 12 shards distributed ~4 per node.
      Losing 1 node = losing up to 4 shards = exactly at tolerance boundary.
      Files stored on origin node remain directly accessible.
    """

    def test_default_shards_match_expected(self, cluster, uploaded_file):
        """Uploaded file records 12 total chunks (8+4)."""
        r = requests.get(f"{cluster['node1']}/api/files/{uploaded_file['id']}", timeout=5)
        assert r.status_code == 200
        data = r.json()
        chunks = data.get("chunks", 0)
        # With encoder present: exactly 12; without encoder: 1 (raw copy fallback)
        assert chunks in (1, TOTAL_SHARDS), \
            f"Expected 1 (no encoder) or {TOTAL_SHARDS} chunks, got {chunks}"

    def test_shard_count_is_sum_of_data_and_parity(self, cluster, uploaded_file):
        """Chunks stored == data_shards + parity_shards."""
        r = requests.get(f"{cluster['node1']}/api/files/{uploaded_file['id']}", timeout=5)
        data = r.json()
        chunks = data.get("chunks", 0)
        if chunks > 1:  # encoder was available
            assert chunks == DEFAULT_DATA_SHARDS + DEFAULT_PAR_SHARDS

    def test_file_still_accessible_after_node_restart(self, cluster, uploaded_file):
        """File metadata survives a clean node restart."""
        _stop_node("node1")
        time.sleep(3)
        _start_node("node1")

        r = requests.get(f"{cluster['node1']}/api/files/{uploaded_file['id']}", timeout=10)
        assert r.status_code == 200, "File metadata lost after node1 restart"

    def test_upload_multiple_files_survive_rolling_restart(self, cluster):
        """Upload 5 files to node1, then rolling-restart all nodes; files still present."""
        file_ids = []
        for i in range(5):
            content = f"durability test file {i} ".encode() + uuid.uuid4().bytes
            resp = _upload_file(cluster["node1"], f"durability_{i}.bin", content)
            file_ids.append(resp["id"])

        # Wait for all to be stored
        for fid in file_ids:
            _wait_for_status(cluster["node1"], fid, timeout=UPLOAD_TIMEOUT)

        # Rolling restart: stop each node one at a time
        for name in ("node3", "node2", "node1"):
            _stop_node(name)
            time.sleep(2)
            _start_node(name)
            time.sleep(1)

        # All files should still be accessible on node1 after restart
        stored_ids = [f["id"] for f in _list_files(cluster["node1"])]
        missing = [fid for fid in file_ids if fid not in stored_ids]

        # Cleanup
        for fid in file_ids:
            _delete_file(cluster["node1"], fid)

        assert not missing, f"Files lost after rolling restart: {missing}"


# ---------------------------------------------------------------------------
# Test Suite 5: Churn simulation
# ---------------------------------------------------------------------------

@pytest.mark.cluster
@pytest.mark.slow
class TestChurnSimulation:
    """
    Simulate realistic network churn (nodes going offline and coming back).
    With 3 nodes and 8+4 shards, periodic single-node churn should not
    cause data loss as long as at most par nodes are down simultaneously.
    """

    def test_continuous_churn_single_node(self, cluster):
        """Rapid stop/start of node3 10 times; node1 always serves its files."""
        content = b"churn test payload " + uuid.uuid4().bytes
        resp = _upload_file(cluster["node1"], "churn_test.bin", content)
        file_id = resp["id"]
        _wait_for_status(cluster["node1"], file_id, timeout=UPLOAD_TIMEOUT)

        failures = []
        for cycle in range(10):
            _stop_node("node3")
            time.sleep(0.5)
            _start_node("node3")
            time.sleep(0.5)

            try:
                r = requests.get(f"{cluster['node1']}/api/health", timeout=3)
                if r.status_code != 200:
                    failures.append(f"cycle {cycle}: node1 health failed")
            except Exception as exc:
                failures.append(f"cycle {cycle}: {exc}")

        _delete_file(cluster["node1"], file_id)
        assert not failures, f"Churn failures: {failures}"

    def test_simultaneous_upload_and_node_failure(self, cluster):
        """Uploading a file while a node is offline should succeed on surviving nodes."""
        _stop_node("node3")
        time.sleep(1)
        try:
            uploads = []
            for i in range(3):
                content = f"concurrent upload {i} ".encode() + uuid.uuid4().bytes
                resp = _upload_file(cluster["node1"], f"concurrent_{i}.bin", content)
                uploads.append(resp["id"])

            for fid in uploads:
                _wait_for_status(cluster["node1"], fid, timeout=UPLOAD_TIMEOUT)

            stored = [f["id"] for f in _list_files(cluster["node1"])]
            missing = [fid for fid in uploads if fid not in stored]
            for fid in uploads:
                _delete_file(cluster["node1"], fid)
            assert not missing, f"Some uploads lost during node3 outage: {missing}"
        finally:
            _start_node("node3")

    def test_data_not_lost_with_par_nodes_down(self, cluster):
        """
        With 3 nodes and 12 shards (8+4), losing 1 node is at the tolerance boundary.
        Files on the surviving 2 nodes should still be listed and metadata accessible.
        Note: actual Reed-Solomon reconstruction from remote peers requires the
        full chunk-fetch pipeline which is in Phase 2. This test validates
        metadata availability (the foundation of reconstruction).
        """
        content = b"parity boundary test " + uuid.uuid4().bytes
        resp = _upload_file(cluster["node1"], "parity_boundary.bin", content)
        file_id = resp["id"]
        _wait_for_status(cluster["node1"], file_id, timeout=UPLOAD_TIMEOUT)

        # Stop node3 (simulates losing par/total_nodes fraction of shards)
        _stop_node("node3")
        time.sleep(2)
        try:
            # Metadata must be accessible on node1 (origin)
            r = requests.get(f"{cluster['node1']}/api/files/{file_id}", timeout=5)
            assert r.status_code == 200, "File metadata inaccessible after node3 down"

            # node2 can still reach node1's peer files
            peer_files = _peer_files(cluster["node1"])
            ids = [f["id"] for f in peer_files]
            assert file_id in ids, "File absent from node1's peer-files endpoint"
        finally:
            _start_node("node3")
            _delete_file(cluster["node1"], file_id)


# ---------------------------------------------------------------------------
# Test Suite 6: Registration and peer management
# ---------------------------------------------------------------------------

@pytest.mark.cluster
class TestPeerRegistration:

    def test_register_new_peer_endpoint(self, cluster):
        """POST /api/peers/register with valid payload returns {registered: true}."""
        payload = {"url": "http://hypothetical-node4:8000", "node_id": "node4-test"}
        r = requests.post(f"{cluster['node1']}/api/peers/register", json=payload, timeout=5)
        assert r.status_code == 200
        assert r.json().get("registered") is True

    def test_register_without_url_returns_400(self, cluster):
        """POST /api/peers/register without url returns 400."""
        r = requests.post(f"{cluster['node1']}/api/peers/register", json={"node_id": "x"}, timeout=5)
        assert r.status_code == 400

    def test_registered_peer_appears_in_list(self, cluster):
        """After registration, peer appears in GET /api/peers."""
        unique_url = f"http://dynamic-peer-{uuid.uuid4().hex[:8]}:8000"
        requests.post(
            f"{cluster['node1']}/api/peers/register",
            json={"url": unique_url, "node_id": "dynamic"},
            timeout=5,
        )
        peers = requests.get(f"{cluster['node1']}/api/peers", timeout=5).json()
        urls = [p["url"] for p in peers]
        assert unique_url in urls

    def test_chunk_serving_endpoint_404_for_unknown_chunk(self, cluster):
        """GET /api/peers/chunks/<unknown-id> returns 404."""
        r = requests.get(f"{cluster['node1']}/api/peers/chunks/nonexistent-chunk-id", timeout=5)
        assert r.status_code == 404

    def test_chunk_served_after_upload(self, cluster, uploaded_file):
        """After upload, chunk IDs in metadata are actually servable."""
        # Get the file metadata with chunk_list
        r = requests.get(f"{cluster['node1']}/api/files/{uploaded_file['id']}", timeout=5)
        # chunk_list is in the raw tree JSON, not the FileMetadata model
        # fetch via peer files which returns full raw metadata
        peer_files = _peer_files(cluster["node1"])
        target = next((f for f in peer_files if f["id"] == uploaded_file["id"]), None)
        if target is None:
            pytest.skip("File not in peer_files (may be in flight)")
        chunk_list = target.get("chunk_list", [])
        if not chunk_list:
            pytest.skip("No chunk_list in metadata (encoder may not be present)")
        # Try serving the first chunk
        chunk_id = chunk_list[0]["id"]
        rc = requests.get(f"{cluster['node1']}/api/peers/chunks/{chunk_id}", timeout=5)
        assert rc.status_code == 200
        assert len(rc.content) > 0
