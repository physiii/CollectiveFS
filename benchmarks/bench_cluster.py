"""
bench_cluster.py — Cluster & Network benchmarks for CollectiveFS.

Requires a running cluster on localhost:8001, 8002, 8003.
Auto-skips all tests if no cluster node is reachable.
"""

from __future__ import annotations

import io
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from requests.exceptions import ConnectionError, Timeout

from benchmarks.common import (
    BenchResult,
    BenchSuite,
    make_result,
    make_random_file,
    skipped_result,
    throughput_mbps,
    console,
)

CATEGORY = "Cluster"
ITERATIONS = 5
NODE_URLS = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
]
PROBE_TIMEOUT = 2.0   # seconds to wait when checking if a node is up
REQUEST_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Cluster availability helpers
# ---------------------------------------------------------------------------

def _reachable_nodes() -> List[str]:
    """Return list of cluster node base-URLs that respond to /api/health."""
    alive = []
    for url in NODE_URLS:
        try:
            resp = requests.get(f"{url}/api/health", timeout=PROBE_TIMEOUT)
            if resp.status_code < 500:
                alive.append(url)
        except (ConnectionError, Timeout, Exception):
            pass
    return alive


def _cluster_available() -> Tuple[bool, List[str]]:
    nodes = _reachable_nodes()
    return bool(nodes), nodes


def _skip(name: str, reason: str = "cluster not available") -> BenchResult:
    return skipped_result(name, CATEGORY, reason, "ms")


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------

class BenchmarkCluster:
    """Cluster and network benchmarks — auto-skipped if cluster is down."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations
        available, self.nodes = _cluster_available()
        self.cluster_available = available
        if not available:
            console.print(
                "[yellow]  Cluster not reachable (localhost:8001-8003). "
                "All cluster benchmarks will be skipped.[/yellow]"
            )
        else:
            console.print(
                f"[green]  Cluster available: {self.nodes}[/green]"
            )

    # ------------------------------------------------------------------
    # Upload latency (POST to response)
    # ------------------------------------------------------------------

    def test_upload_latency_ms(self) -> BenchResult:
        name = "upload_latency_ms"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        file_data = os.urandom(64 * 1024)   # 64 KB
        samples: List[float] = []

        # warmup
        try:
            requests.post(
                f"{node}/api/files/upload",
                files={"file": ("bench_warmup.bin", io.BytesIO(file_data), "application/octet-stream")},
                timeout=REQUEST_TIMEOUT,
            )
        except Exception:
            pass

        for _ in range(self.iterations):
            try:
                t0 = time.perf_counter()
                resp = requests.post(
                    f"{node}/api/files/upload",
                    files={"file": ("bench_upload.bin", io.BytesIO(file_data), "application/octet-stream")},
                    timeout=REQUEST_TIMEOUT,
                )
                elapsed = time.perf_counter() - t0
                if resp.status_code in (200, 201, 202):
                    samples.append(elapsed * 1000)
            except Exception as exc:
                console.print(f"[red]    upload failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "no successful uploads")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms",
                        {"node": node, "file_size": "64KB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Upload-to-stored time
    # ------------------------------------------------------------------

    def test_upload_to_stored_time_ms(self) -> BenchResult:
        name = "upload_to_stored_time_ms"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        file_data = os.urandom(64 * 1024)
        samples: List[float] = []

        for _ in range(self.iterations):
            try:
                t0 = time.perf_counter()
                upload_resp = requests.post(
                    f"{node}/api/files/upload",
                    files={"file": ("bench_stored.bin", io.BytesIO(file_data), "application/octet-stream")},
                    timeout=REQUEST_TIMEOUT,
                )
                if upload_resp.status_code not in (200, 201, 202):
                    continue
                body = upload_resp.json()
                file_id = body.get("file_id") or body.get("id") or body.get("fileId")
                if not file_id:
                    continue

                # Poll status until stored or timeout
                deadline = time.perf_counter() + 30.0
                while time.perf_counter() < deadline:
                    status_resp = requests.get(
                        f"{node}/api/files/{file_id}",
                        timeout=REQUEST_TIMEOUT,
                    )
                    if status_resp.status_code == 200:
                        status = status_resp.json().get("status", "")
                        if status in ("stored", "complete", "ready", "distributed"):
                            break
                    time.sleep(0.05)

                elapsed = (time.perf_counter() - t0) * 1000
                samples.append(elapsed)
            except Exception as exc:
                console.print(f"[red]    upload_to_stored failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "no successful uploads or status checks")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms",
                        {"node": node, "file_size": "64KB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Cross-node visibility latency
    # ------------------------------------------------------------------

    def test_cross_node_visibility_latency_ms(self) -> BenchResult:
        name = "cross_node_visibility_latency_ms"
        if not self.cluster_available or len(self.nodes) < 2:
            r = _skip(name, "need at least 2 nodes")
            self.suite.add(r)
            return r

        node_a, node_b = self.nodes[0], self.nodes[1]
        file_data = os.urandom(16 * 1024)
        samples: List[float] = []

        for _ in range(self.iterations):
            try:
                upload_resp = requests.post(
                    f"{node_a}/api/files/upload",
                    files={"file": ("bench_vis.bin", io.BytesIO(file_data), "application/octet-stream")},
                    timeout=REQUEST_TIMEOUT,
                )
                if upload_resp.status_code not in (200, 201, 202):
                    continue
                body = upload_resp.json()
                file_id = body.get("file_id") or body.get("id") or body.get("fileId")
                if not file_id:
                    continue

                # Wait until visible on node_b
                t0 = time.perf_counter()
                deadline = t0 + 30.0
                visible = False
                while time.perf_counter() < deadline:
                    try:
                        check_resp = requests.get(
                            f"{node_b}/api/files/{file_id}",
                            timeout=REQUEST_TIMEOUT,
                        )
                        if check_resp.status_code == 200:
                            visible = True
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)

                if visible:
                    samples.append((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                console.print(f"[red]    cross_node_visibility failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "files never appeared on second node")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms",
                        {"node_a": node_a, "node_b": node_b})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Peer registration latency
    # ------------------------------------------------------------------

    def test_peer_registration_latency_ms(self) -> BenchResult:
        name = "peer_registration_latency_ms"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        samples: List[float] = []

        for i in range(self.iterations):
            try:
                t0 = time.perf_counter()
                resp = requests.post(
                    f"{node}/api/peers/register",
                    json={"url": f"http://bench-peer-{i}:9999", "node_id": f"bench_{i}"},
                    timeout=REQUEST_TIMEOUT,
                )
                elapsed = (time.perf_counter() - t0) * 1000
                if resp.status_code in (200, 201, 204, 409):
                    samples.append(elapsed)
            except Exception as exc:
                console.print(f"[red]    peer_register failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "peer registration endpoint not available")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms", {"node": node})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Network view aggregation (GET /api/network)
    # ------------------------------------------------------------------

    def test_network_view_aggregation_ms(self) -> BenchResult:
        name = "network_view_aggregation_ms"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        samples: List[float] = []

        # warmup
        try:
            requests.get(f"{node}/api/network", timeout=REQUEST_TIMEOUT)
        except Exception:
            pass

        for _ in range(self.iterations):
            try:
                t0 = time.perf_counter()
                resp = requests.get(f"{node}/api/network", timeout=REQUEST_TIMEOUT)
                elapsed = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    samples.append(elapsed)
            except Exception as exc:
                console.print(f"[red]    network_view failed: {exc}[/red]")

        if not samples:
            # Try /api/peers as a fallback
            for _ in range(self.iterations):
                try:
                    t0 = time.perf_counter()
                    resp = requests.get(f"{node}/api/peers", timeout=REQUEST_TIMEOUT)
                    elapsed = (time.perf_counter() - t0) * 1000
                    if resp.status_code == 200:
                        samples.append(elapsed)
                except Exception:
                    pass

        if not samples:
            r = _skip(name, "/api/network and /api/peers both unavailable")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms", {"node": node})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Node failure detection time
    # ------------------------------------------------------------------

    def test_node_failure_detection_ms(self) -> BenchResult:
        name = "node_failure_detection_ms"
        if not self.cluster_available or len(self.nodes) < 2:
            r = _skip(name, "need at least 2 nodes")
            self.suite.add(r)
            return r

        # We can't actually stop a node in a benchmark; instead we measure
        # how quickly the cluster API reports an unreachable peer as unhealthy.
        # We use a fake peer URL that will never respond.
        node = self.nodes[0]
        samples: List[float] = []

        fake_url = "http://127.0.0.1:19999"   # nothing listening here

        for _ in range(self.iterations):
            try:
                # Register a fake peer
                requests.post(
                    f"{node}/api/peers/register",
                    json={"url": fake_url, "node_id": "bench_dead_node"},
                    timeout=REQUEST_TIMEOUT,
                )
                # Time until the node reports it as unhealthy / unreachable
                t0 = time.perf_counter()
                deadline = t0 + 30.0
                detected = False
                while time.perf_counter() < deadline:
                    try:
                        resp = requests.get(f"{node}/api/peers", timeout=REQUEST_TIMEOUT)
                        if resp.status_code == 200:
                            peers = resp.json()
                            if isinstance(peers, list):
                                for peer in peers:
                                    p_url = peer.get("url", "")
                                    p_status = peer.get("status", "") or peer.get("health", "")
                                    if fake_url in p_url and p_status in (
                                        "unreachable", "unhealthy", "dead", "offline", "error"
                                    ):
                                        detected = True
                                        break
                        if detected:
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)

                if detected:
                    samples.append((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                console.print(f"[red]    node_failure_detection failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "cluster did not report unhealthy peer within 30s")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms",
                        {"detection_method": "peer_status_poll"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Node recovery time
    # ------------------------------------------------------------------

    def test_node_recovery_time_ms(self) -> BenchResult:
        name = "node_recovery_time_ms"
        # This benchmark is inherently a manual/integration test.
        # We measure the /api/health round-trip time as a proxy for
        # "time to confirm a node is healthy" after startup.
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        samples: List[float] = []

        for _ in range(self.iterations):
            try:
                t0 = time.perf_counter()
                resp = requests.get(f"{node}/api/health", timeout=REQUEST_TIMEOUT)
                elapsed = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    samples.append(elapsed)
            except Exception as exc:
                console.print(f"[red]    node_recovery failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "health endpoint not responding")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "ms",
                        {"note": "health check latency as proxy for recovery confirmation"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Concurrent uploads throughput (5 concurrent)
    # ------------------------------------------------------------------

    def test_concurrent_uploads_throughput(self) -> BenchResult:
        name = "concurrent_uploads_throughput [5 concurrent]"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        from concurrent.futures import ThreadPoolExecutor, as_completed

        node = self.nodes[0]
        n_concurrent = 5
        file_size = 64 * 1024  # 64 KB each
        total_bytes = file_size * n_concurrent
        samples: List[float] = []

        def _upload_one(idx: int) -> bool:
            data = os.urandom(file_size)
            try:
                resp = requests.post(
                    f"{node}/api/files/upload",
                    files={"file": (f"bench_conc_{idx}.bin", io.BytesIO(data), "application/octet-stream")},
                    timeout=REQUEST_TIMEOUT,
                )
                return resp.status_code in (200, 201, 202)
            except Exception:
                return False

        def _run():
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n_concurrent) as pool:
                futs = [pool.submit(_upload_one, i) for i in range(n_concurrent)]
                results = [fut.result() for fut in as_completed(futs)]
            elapsed = time.perf_counter() - t0
            return elapsed, all(results)

        _run()  # warmup
        for _ in range(self.iterations):
            elapsed, success = _run()
            if success:
                samples.append(throughput_mbps(total_bytes, elapsed))

        if not samples:
            r = _skip(name, "concurrent uploads failed")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "MB/s",
                        {"concurrent": n_concurrent, "file_size": "64KB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Peer chunk fetch throughput
    # ------------------------------------------------------------------

    def test_peer_chunk_fetch_throughput(self) -> BenchResult:
        name = "peer_chunk_fetch_throughput"
        if not self.cluster_available or len(self.nodes) < 2:
            r = _skip(name, "need at least 2 nodes")
            self.suite.add(r)
            return r

        node_a, node_b = self.nodes[0], self.nodes[1]
        file_size = 512 * 1024  # 512 KB
        samples: List[float] = []

        try:
            # Upload a file to node_a
            file_data = os.urandom(file_size)
            upload_resp = requests.post(
                f"{node_a}/api/files/upload",
                files={"file": ("bench_chunk.bin", io.BytesIO(file_data), "application/octet-stream")},
                timeout=REQUEST_TIMEOUT,
            )
            if upload_resp.status_code not in (200, 201, 202):
                r = _skip(name, "upload failed before fetch test")
                self.suite.add(r)
                return r

            body = upload_resp.json()
            file_id = body.get("file_id") or body.get("id") or body.get("fileId")
            if not file_id:
                r = _skip(name, "could not get file_id from upload response")
                self.suite.add(r)
                return r

            # Wait a moment for propagation
            time.sleep(1.0)

            # Fetch from node_b
            def _fetch():
                t0 = time.perf_counter()
                resp = requests.get(
                    f"{node_b}/api/files/{file_id}/download",
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                )
                _ = resp.content  # consume body
                return time.perf_counter() - t0

            _fetch()  # warmup
            for _ in range(self.iterations):
                try:
                    elapsed = _fetch()
                    samples.append(throughput_mbps(file_size, elapsed))
                except Exception as exc:
                    console.print(f"[red]    chunk_fetch failed: {exc}[/red]")

        except Exception as exc:
            console.print(f"[red]    peer_chunk_fetch setup failed: {exc}[/red]")

        if not samples:
            r = _skip(name, "fetch from peer node failed")
            self.suite.add(r)
            return r

        r = make_result(name, CATEGORY, samples, "MB/s",
                        {"from_node": node_b, "file_size": "512KB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Health endpoint latency p50/p99
    # ------------------------------------------------------------------

    def test_health_endpoint_latency_ms(self) -> BenchResult:
        name = "health_endpoint_latency_p50_p99"
        if not self.cluster_available:
            r = _skip(name)
            self.suite.add(r)
            return r

        node = self.nodes[0]
        n_samples = 100
        latencies: List[float] = []

        # warmup
        requests.get(f"{node}/api/health", timeout=REQUEST_TIMEOUT)

        for _ in range(n_samples):
            try:
                t0 = time.perf_counter()
                resp = requests.get(f"{node}/api/health", timeout=REQUEST_TIMEOUT)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    latencies.append(elapsed_ms)
            except Exception:
                pass

        if not latencies:
            r = _skip(name, "no successful health checks")
            self.suite.add(r)
            return r

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p99 = latencies[int(len(latencies) * 0.99)]

        r = make_result(name, CATEGORY, latencies, "ms",
                        {"p50_ms": round(p50, 3),
                         "p99_ms": round(p99, 3),
                         "n_samples": len(latencies)})
        r.value = p50
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self) -> BenchSuite:
        console.print(f"\n[bold cyan]Running Cluster benchmarks[/bold cyan]")
        if not self.cluster_available:
            console.print("[yellow]  All cluster tests skipped — cluster unreachable.[/yellow]")

        self.test_upload_latency_ms()
        self.test_upload_to_stored_time_ms()
        self.test_cross_node_visibility_latency_ms()
        self.test_peer_registration_latency_ms()
        self.test_network_view_aggregation_ms()
        self.test_node_failure_detection_ms()
        self.test_node_recovery_time_ms()
        self.test_concurrent_uploads_throughput()
        self.test_peer_chunk_fetch_throughput()
        self.test_health_endpoint_latency_ms()
        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from benchmarks.common import BenchSuite
    suite = BenchSuite("Cluster Benchmarks")
    runner = BenchmarkCluster(suite)
    runner.run_all()
    suite.print_table()
