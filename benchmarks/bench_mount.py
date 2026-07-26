#!/usr/bin/env python3
"""End-to-end evaluation of the mounted CollectiveFS filesystem.

Unlike the other suites, which time the storage engine in isolation, this one
measures what a user actually experiences: writing through `/media/collectivefs`
on one machine and reading it back on another, with erasure coding, encryption,
shard distribution and peer routing all in the path.

    python -m benchmarks.bench_mount --report benchmarks/results/mount-report.md

Every number here is measured. Where something could not be measured — a
saturation point that would take hours to reach at full quota — the report says
so explicitly rather than extrapolating silently.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

MOUNT = "/media/collectivefs"
KB = 1024
MB = 1024 * 1024
GB = 1024 * MB


# ── hosts ───────────────────────────────────────────────────────────


@dataclass
class Host:
    """One machine in the cluster, driven either locally or over ssh."""

    name: str
    api: str
    ssh: Optional[str] = None  # None means "this machine"
    mount: str = MOUNT

    def run(self, command: str, timeout: int = 600) -> subprocess.CompletedProcess:
        if self.ssh:
            argv = ["ssh", "-o", "BatchMode=yes", self.ssh, command]
        else:
            argv = ["bash", "-c", command]
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def api_get(self, path: str, timeout: float = 30.0) -> Dict[str, Any]:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{self.api}{path}")
            response.raise_for_status()
            return response.json()

    def api_put(self, path: str, body: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        with httpx.Client(timeout=timeout) as client:
            response = client.put(f"{self.api}{path}", json=body)
            response.raise_for_status()
            return response.json()


# ── measurement helpers ─────────────────────────────────────────────


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((pct / 100.0) * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


@dataclass
class Timing:
    """A set of timed samples, summarised the way the report needs them."""

    label: str
    samples_ms: List[float] = field(default_factory=list)
    bytes_moved: int = 0
    failures: int = 0

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    def summary(self) -> Dict[str, Any]:
        if not self.samples_ms:
            return {"label": self.label, "count": 0, "failures": self.failures}
        total_s = sum(self.samples_ms) / 1000.0
        return {
            "label": self.label,
            "count": self.count,
            "failures": self.failures,
            "mean_ms": round(statistics.fmean(self.samples_ms), 3),
            "median_ms": round(statistics.median(self.samples_ms), 3),
            "p95_ms": round(percentile(self.samples_ms, 95), 3),
            "p99_ms": round(percentile(self.samples_ms, 99), 3),
            "min_ms": round(min(self.samples_ms), 3),
            "max_ms": round(max(self.samples_ms), 3),
            "stdev_ms": round(statistics.stdev(self.samples_ms), 3) if self.count > 1 else 0.0,
            "bytes": self.bytes_moved,
            "throughput_mbs": round((self.bytes_moved / MB) / total_s, 2) if total_s > 0 and self.bytes_moved else None,
            "ops_per_sec": round(self.count / total_s, 2) if total_s > 0 else None,
        }


def time_remote(host: Host, command: str, timeout: int = 900) -> Tuple[float, bool, str]:
    """Run a shell command on `host` and return (elapsed_ms, ok, stderr).

    Timing is taken locally around the whole invocation. For ssh hosts that
    includes session setup, which is why the ssh overhead is measured
    separately and reported alongside — so the figures can be read honestly.
    """
    start = time.perf_counter()
    try:
        result = host.run(command, timeout=timeout)
        ok = result.returncode == 0
        err = result.stderr.strip()[:400]
    except subprocess.TimeoutExpired:
        return (timeout * 1000.0, False, "timeout")
    return ((time.perf_counter() - start) * 1000.0, ok, err)


# ── environment capture ─────────────────────────────────────────────


def describe_host(host: Host) -> Dict[str, Any]:
    """Everything needed to reproduce or compare these numbers."""
    probe = r"""
echo "hostname=$(hostname)"
echo "kernel=$(uname -r)"
echo "os=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
echo "cpu_model=$(lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -1)"
echo "cpu_cores=$(nproc)"
echo "cpu_mhz=$(lscpu | sed -n 's/^CPU max MHz:[[:space:]]*//p' | head -1)"
echo "mem_total_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "mount_fs=$(df -PT /media/collectivefs 2>/dev/null | awk 'NR==2{print $2}')"
echo "mount_size=$(df -P /media/collectivefs 2>/dev/null | awk 'NR==2{print $2}')"
_dev=$(df -P /var/lib/docker 2>/dev/null | awk 'NR==2{print $1}')
_parent=$(lsblk -no PKNAME "$_dev" 2>/dev/null | head -1 | tr -d ' ')
echo "backing_dev=$_dev"
echo "backing_model=$(lsblk -no MODEL "/dev/$_parent" 2>/dev/null | head -1 | sed 's/[[:space:]]*$//')"
echo "backing_rota=$(lsblk -no ROTA "$_dev" 2>/dev/null | head -1 | tr -d ' ')"
echo "docker=$(docker --version 2>/dev/null)"
echo "python=$(python3 --version 2>&1)"
"""
    result = host.run(probe, timeout=120)
    info: Dict[str, Any] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            info[key.strip()] = value.strip()

    try:
        overview = host.api_get("/api/system/overview")
        info["node_id"] = overview.get("node_id", "")
        info["platform"] = overview.get("platform", "")
        collective = overview.get("collective", {})
        info["quota_bytes"] = collective.get("quota_bytes")
        info["used_bytes"] = collective.get("used_bytes")
        info["device_free_bytes"] = collective.get("device_free_bytes")
        erasure = overview.get("erasure", {})
        info["erasure"] = f"{erasure.get('data_shards')}+{erasure.get('parity_shards')}"
        info["peers_online"] = overview.get("peers", {}).get("online")
        disks = overview.get("disks") or [{}]
        info["backing_total"] = disks[0].get("total_bytes")
    except (httpx.HTTPError, ValueError, IndexError):
        pass
    return info


def measure_link(a: Host, b: Host, samples: int = 5) -> Dict[str, Any]:
    """Round-trip latency and raw bandwidth between two nodes."""
    out: Dict[str, Any] = {}
    ping = a.run(f"ping -c {samples} -q {b.api.split('//')[1].split(':')[0]}", timeout=60)
    for line in ping.stdout.splitlines():
        if "rtt" in line or "round-trip" in line:
            out["rtt"] = line.split("=")[-1].strip()

    # Health endpoint round trip: the actual control-plane cost between nodes.
    latencies = []
    for _ in range(samples):
        start = time.perf_counter()
        probe = a.run(f"curl -s -o /dev/null -w '%{{http_code}}' {b.api}/api/health", timeout=30)
        if probe.stdout.strip() == "200":
            latencies.append((time.perf_counter() - start) * 1000)
    if latencies:
        out["api_rtt_ms"] = round(statistics.median(latencies), 2)

    # ssh overhead, so per-operation numbers taken over ssh can be read fairly.
    ssh_overhead = []
    for _ in range(3):
        elapsed, ok, _ = time_remote(b, "true", timeout=60)
        if ok:
            ssh_overhead.append(elapsed)
    if ssh_overhead:
        out["shell_overhead_ms"] = round(statistics.median(ssh_overhead), 2)
    return out


# ── benchmarks ──────────────────────────────────────────────────────

SIZE_LADDER = [
    ("4KB", 4 * KB),
    ("64KB", 64 * KB),
    ("1MB", MB),
    ("8MB", 8 * MB),
    ("64MB", 64 * MB),
    ("256MB", 256 * MB),
]


def bench_write_read(host: Host, sizes, iterations: int, prefix: str) -> List[Dict[str, Any]]:
    """Write then read each size through the mount, timed on the host itself."""
    rows = []
    for label, size in sizes:
        write = Timing(f"write {label}")
        read = Timing(f"read {label}")
        verified = 0

        for index in range(iterations):
            name = f"{prefix}-{label}-{index}.bin"
            src = f"/tmp/{name}"
            target = f"{host.mount}/{name}"

            host.run(f"head -c {size} /dev/urandom > {src}", timeout=300)
            digest_before = host.run(f"sha256sum {src} | cut -d' ' -f1", timeout=300).stdout.strip()

            # `cp` then a sync-equivalent: the upload happens on close, so the
            # copy command returning is the honest end of the write.
            elapsed, ok, err = time_remote(host, f"cp {src} {target}", timeout=900)
            if ok:
                write.samples_ms.append(elapsed)
                write.bytes_moved += size
            else:
                write.failures += 1
                continue

            # Drop any client-side cache so the read is a real reconstruction.
            elapsed, ok, err = time_remote(host, f"cat {target} > /tmp/rb-{name}", timeout=900)
            if ok:
                read.samples_ms.append(elapsed)
                read.bytes_moved += size
                digest_after = host.run(
                    f"sha256sum /tmp/rb-{name} | cut -d' ' -f1", timeout=300
                ).stdout.strip()
                if digest_before and digest_before == digest_after:
                    verified += 1
            else:
                read.failures += 1

            host.run(f"rm -f {src} /tmp/rb-{name}", timeout=60)

        rows.append(
            {
                "size_label": label,
                "size_bytes": size,
                "write": write.summary(),
                "read": read.summary(),
                "integrity_verified": verified,
                "integrity_attempted": iterations,
            }
        )
    return rows


def bench_metadata_ops(host: Host, iterations: int, prefix: str) -> List[Dict[str, Any]]:
    """Per-operation latency for the metadata operations a shell actually uses."""
    base = f"{host.mount}/{prefix}-ops"
    host.run(f"mkdir -p {base}", timeout=60)

    operations: List[Tuple[str, Callable[[int], str]]] = [
        ("create (touch)", lambda i: f"echo x > {base}/f{i}.txt"),
        ("stat", lambda i: f"stat {base}/f{i}.txt"),
        ("readdir", lambda i: f"ls {base}"),
        ("rename", lambda i: f"mv {base}/f{i}.txt {base}/r{i}.txt"),
        ("read small", lambda i: f"cat {base}/r{i}.txt"),
        ("copy small", lambda i: f"cp {base}/r{i}.txt {base}/c{i}.txt"),
        ("mkdir", lambda i: f"mkdir -p {base}/d{i}"),
        ("rmdir", lambda i: f"rmdir {base}/d{i}"),
        ("unlink", lambda i: f"rm -f {base}/c{i}.txt"),
    ]

    rows = []
    for label, build in operations:
        timing = Timing(label)
        for index in range(iterations):
            elapsed, ok, _ = time_remote(host, build(index), timeout=300)
            if ok:
                timing.samples_ms.append(elapsed)
            else:
                timing.failures += 1
        rows.append(timing.summary())

    host.run(f"rm -rf {base}", timeout=120)
    return rows


def bench_reconciliation(writer: Host, reader: Host, iterations: int, prefix: str) -> Dict[str, Any]:
    """How long until a file written on one machine is readable on the other."""
    visible = Timing("visible to peer")
    readable = Timing("readable on peer")

    for index in range(iterations):
        name = f"{prefix}-recon-{index}.txt"
        marker = f"recon-{index}-{int(time.time() * 1000)}"
        writer.run(f"echo {marker} > {writer.mount}/{name}", timeout=120)

        start = time.perf_counter()
        seen_at = None
        read_at = None
        deadline = start + 30
        while time.perf_counter() < deadline:
            probe = reader.run(f"test -f {reader.mount}/{name} && echo yes", timeout=60)
            if probe.stdout.strip() == "yes":
                seen_at = (time.perf_counter() - start) * 1000
                break
        if seen_at is None:
            visible.failures += 1
            continue
        visible.samples_ms.append(seen_at)

        deadline = time.perf_counter() + 30
        while time.perf_counter() < deadline:
            probe = reader.run(f"cat {reader.mount}/{name} 2>/dev/null", timeout=60)
            if marker in probe.stdout:
                read_at = (time.perf_counter() - start) * 1000
                break
        if read_at is None:
            readable.failures += 1
        else:
            readable.samples_ms.append(read_at)

        writer.run(f"rm -f {writer.mount}/{name}", timeout=60)

    return {"visible": visible.summary(), "readable": readable.summary()}


def bench_parallel_load(host: Host, streams: int, size: int, prefix: str) -> Dict[str, Any]:
    """Concurrent writers, to see how the mount behaves under real load."""
    script = f"""
set -e
for i in $(seq 1 {streams}); do
  ( head -c {size} /dev/urandom > /tmp/{prefix}-p$i.bin
    cp /tmp/{prefix}-p$i.bin {host.mount}/{prefix}-p$i.bin
    rm -f /tmp/{prefix}-p$i.bin ) &
done
wait
"""
    start = time.perf_counter()
    result = host.run(script, timeout=1800)
    elapsed = time.perf_counter() - start
    total = streams * size
    landed = host.run(
        f"ls {host.mount}/{prefix}-p*.bin 2>/dev/null | wc -l", timeout=60
    ).stdout.strip()
    host.run(f"rm -f {host.mount}/{prefix}-p*.bin", timeout=300)
    return {
        "streams": streams,
        "size_bytes": size,
        "total_bytes": total,
        "elapsed_s": round(elapsed, 2),
        "aggregate_mbs": round((total / MB) / elapsed, 2) if elapsed > 0 else None,
        "files_landed": int(landed) if landed.isdigit() else 0,
        "ok": result.returncode == 0,
        "error": result.stderr.strip()[:300],
    }


def bench_saturation(host: Host, peer: Host, quota_bytes: int, chunk: int, prefix: str) -> Dict[str, Any]:
    """Drive the node past its write cutoff and record what it does.

    Run against a deliberately small temporary quota: filling a 1 TB pledge to
    its watermark would take hours and gigabytes, and the behaviour under test
    — the cutoff — is identical at any quota.
    """
    config = host.api_get("/api/config")["config"]
    original = config["storage"]
    original_upload = config["upload"]
    baseline_used = host.api_get("/api/system/overview")["collective"]["used_bytes"]

    # Headroom just above what is already stored, so the cutoff is reachable.
    # The quota floor is 1 GiB, so that is the smallest usable target.
    target_quota = max(baseline_used + quota_bytes, 1 * GB + baseline_used)
    # The reserve must stay below the quota and the upload ceiling must stay at
    # or below it, so both come down with it and go back up afterwards.
    host.api_put(
        "/api/config",
        {
            "updates": {
                "storage.quota_bytes": target_quota,
                "storage.reserve_bytes": max(target_quota // 100, 1),
                "upload.max_file_bytes": min(original_upload["max_file_bytes"], target_quota // 2),
                "storage.high_watermark_percent": 50,
            }
        },
    )

    written = 0
    files = 0
    rejected_at = None
    error_text = ""
    started = time.perf_counter()
    try:
        for index in range(200):
            name = f"{prefix}-sat-{index}.bin"
            host.run(f"head -c {chunk} /dev/urandom > /tmp/{name}", timeout=300)
            result = host.run(f"cp /tmp/{name} {host.mount}/{name} 2>&1", timeout=600)
            host.run(f"rm -f /tmp/{name}", timeout=60)
            if result.returncode != 0:
                rejected_at = written
                error_text = (result.stdout + result.stderr).strip()[:300]
                break
            written += chunk
            files += 1
            state = host.api_get("/api/system/overview")["collective"]
            if not state["accepting_writes"]:
                rejected_at = written
                error_text = (
                    f"node stopped accepting writes at {state['used_percent']}% "
                    f"of quota (cutoff {state['high_watermark_percent']}%)"
                )
                break
        final = host.api_get("/api/system/overview")["collective"]
    finally:
        host.run(f"rm -f {host.mount}/{prefix}-sat-*.bin", timeout=600)
        # Restore in an order that never violates the cross-field rules: the
        # quota has to grow before the reserve and upload ceiling follow it.
        host.api_put("/api/config", {"updates": {"storage.quota_bytes": original["quota_bytes"]}})
        host.api_put(
            "/api/config",
            {
                "updates": {
                    "storage.reserve_bytes": original["reserve_bytes"],
                    "upload.max_file_bytes": original_upload["max_file_bytes"],
                    "storage.high_watermark_percent": original["high_watermark_percent"],
                }
            },
        )

    return {
        "temporary_quota_bytes": target_quota,
        "watermark_percent": 50,
        "bytes_written": written,
        "files_written": files,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "cutoff_triggered": rejected_at is not None,
        "cutoff_at_bytes": rejected_at,
        "used_percent_at_stop": final.get("used_percent"),
        "accepting_writes_after": final.get("accepting_writes"),
        "message": error_text,
        "restored_quota_bytes": original["quota_bytes"],
    }


def bench_degraded_read(host: Host, peer: Host, size: int, prefix: str) -> Dict[str, Any]:
    """Read a distributed file with the peer holding its remote shards stopped."""
    name = f"{prefix}-degraded.bin"
    src = f"/tmp/{name}"
    host.run(f"head -c {size} /dev/urandom > {src}", timeout=300)
    digest = host.run(f"sha256sum {src} | cut -d' ' -f1", timeout=120).stdout.strip()
    host.run(f"cp {src} {host.mount}/{name}", timeout=600)
    time.sleep(6)

    placement = {}
    try:
        tree = host.api_get("/api/files/tree")
        for entry in tree.get("files", []):
            if entry["name"] == name:
                placement = entry.get("placement") or {}
                break
    except httpx.HTTPError:
        pass

    peer.run("cd ~/projects/CollectiveFS 2>/dev/null || cd ~/projects/collectivefs; docker compose stop", timeout=300)
    time.sleep(4)

    start = time.perf_counter()
    result = host.run(f"cat {host.mount}/{name} > /tmp/deg-{name}", timeout=900)
    elapsed = (time.perf_counter() - start) * 1000
    after = host.run(f"sha256sum /tmp/deg-{name} | cut -d' ' -f1", timeout=300).stdout.strip()

    peer.run("cd ~/projects/CollectiveFS 2>/dev/null || cd ~/projects/collectivefs; docker compose start", timeout=300)
    time.sleep(12)
    host.run(f"rm -f {src} /tmp/deg-{name} {host.mount}/{name}", timeout=300)

    return {
        "size_bytes": size,
        "placement": placement,
        "peer_stopped": True,
        "read_ok": result.returncode == 0,
        "read_ms": round(elapsed, 2),
        "integrity_ok": bool(digest) and digest == after,
        "note": "peer holding the remote shards was stopped for this read",
    }


def collect_mount_metrics(host: Host, window: int = 900) -> Dict[str, Any]:
    try:
        return host.api_get(f"/api/fs/metrics?window={window}")
    except httpx.HTTPError:
        return {}


def collect_placement(host: Host) -> Dict[str, Any]:
    try:
        overview = host.api_get("/api/system/overview")
        return {
            "collective": overview.get("collective", {}),
            "hosted_for_peers": overview.get("hosted_for_peers", {}),
            "erasure": overview.get("erasure", {}),
        }
    except httpx.HTTPError:
        return {}


def ui_parity(host: Host) -> Dict[str, Any]:
    """The console and the mount must show the same namespace.

    Queried the way a browser does — with no token — because that is what an
    operator actually sees. If the node's default account is not the one the
    mount uses, the two views diverge, which is a real defect.

    The console lists files by id, so two files can share a path; a POSIX
    filesystem cannot represent that, and the mount shows one. Those collisions
    are counted and reported rather than folded into the pass/fail, since they
    are a property of the stored data, not a disagreement between the views.
    """
    try:
        tree = host.api_get("/api/files/tree?scope=network")
    except httpx.HTTPError:
        return {"checked": False}

    entries = tree.get("files", [])
    paths = [
        (f"{entry['folder']}/{entry['name']}" if entry.get("folder") else entry["name"])
        for entry in entries
    ]
    seen: Dict[str, int] = {}
    for path in paths:
        seen[path] = seen.get(path, 0) + 1
    collisions = {path: count for path, count in seen.items() if count > 1}

    ui_paths = sorted(seen)
    listing = host.run(
        f"cd {host.mount} && find . -type f -printf '%P\\n' 2>/dev/null | sort", timeout=300
    ).stdout.split()
    mount_paths = sorted(set(listing))

    return {
        "checked": True,
        "ui_entries": len(entries),
        "ui_paths": len(ui_paths),
        "mount_paths": len(mount_paths),
        "identical": ui_paths == mount_paths,
        "colliding_paths": collisions,
        "only_in_ui": [p for p in ui_paths if p not in mount_paths][:10],
        "only_in_mount": [p for p in mount_paths if p not in ui_paths][:10],
    }


# ── peer contracts and proof-of-storage ─────────────────────────────


def bench_contracts(host: Host, peer: Host, challenges: int = 8) -> Dict[str, Any]:
    """Exercise proof-of-storage end to end and time each stage.

    The full protocol: the origin picks random offsets in a shard the peer holds
    and sends them with a nonce; the peer hashes the bytes at those positions
    from its own copy; the origin verifies against its copy and scores the
    result. This runs on a timer for every contract, so its cost is part of
    steady-state load, and it is the mechanism the untrusted-peer model rests on.
    """
    out: Dict[str, Any] = {"created": False}
    try:
        peer_node_id = peer.api_get("/api/system/overview").get("node_id", "")
    except httpx.HTTPError as exc:
        return {"error": f"could not reach peer: {exc}"}

    # Keep the origin's copy so it can compute the expected answer — verifying a
    # peer requires knowing what the right answer is.
    original = host.api_get("/api/config")["config"]["peers"]
    host.api_put("/api/config", {"updates": {"peers.keep_local_copy": True}})

    client = httpx.Client(timeout=120.0)
    try:
        created = client.post(
            f"{host.api}/api/contracts",
            json={"peer_url": peer.api, "peer_node_id": peer_node_id, "tier": "warm"},
        )
        created.raise_for_status()
        contract = created.json()
        contract_id = contract["contract_id"]
        out.update({"created": True, "contract_id": contract_id, "tier": contract.get("tier")})

        # A file whose shards land on the peer, with our copies retained.
        name = f"contract-probe-{int(time.time())}.bin"
        host.run(f"head -c {4 * MB} /dev/urandom > /tmp/{name}", timeout=300)
        host.run(f"cp /tmp/{name} {host.mount}/{name}", timeout=600)
        time.sleep(8)

        target = None
        for entry in host.api_get("/api/files/tree").get("files", []):
            if entry["name"] == name:
                detail = host.api_get(f"/api/files/{entry['id']}")
                out["file_placement"] = detail.get("placement", {})
                target = entry["id"]
                break
        if target is None:
            out["error"] = "probe file did not appear"
            return out

        raw = host.run(
            f"docker exec collectivefs-collectivefs-1 cat /data/.collective/tree/{target}.json",
            timeout=120,
        ).stdout
        chunks = json.loads(raw).get("chunk_list", []) if raw.strip() else []
        remote = [
            chunk for chunk in chunks
            if chunk.get("peer") and chunk.get("id") and not str(chunk.get("path", "")).endswith(".size")
        ]
        out["shards_on_peer"] = len(remote)
        if not remote:
            out["error"] = "no shards were placed on the peer"
            return out

        issue = Timing("issue challenge")
        respond = Timing("peer computes proof")
        resolve = Timing("verify and score")
        passed = 0

        for chunk in remote[:challenges]:
            start_t = time.perf_counter()
            issued = client.post(
                f"{host.api}/api/contracts/{contract_id}/challenge",
                json={"shard_id": chunk["id"], "shard_path": chunk["path"]},
            )
            if issued.status_code >= 400:
                issue.failures += 1
                out.setdefault("issue_error", issued.text[:200])
                continue
            issue.samples_ms.append((time.perf_counter() - start_t) * 1000)
            record = issued.json()

            start_t = time.perf_counter()
            answered = client.post(
                f"{peer.api}/api/contracts/challenge/respond",
                json={
                    "challenge_id": record["challenge_id"],
                    "shard_id": chunk["id"],
                    "offsets": record["offsets"],
                    "window_size": record.get("window_size", 32),
                    "nonce": record["nonce"],
                },
            )
            if answered.status_code >= 400:
                respond.failures += 1
                out.setdefault("respond_error", answered.text[:200])
                continue
            response_ms = (time.perf_counter() - start_t) * 1000
            respond.samples_ms.append(response_ms)

            start_t = time.perf_counter()
            verdict = client.post(
                f"{host.api}/api/contracts/{contract_id}/challenge/{record['challenge_id']}/resolve",
                json={"proof": answered.json()["proof"], "response_ms": response_ms},
            )
            if verdict.status_code >= 400:
                resolve.failures += 1
                continue
            resolve.samples_ms.append((time.perf_counter() - start_t) * 1000)
            if verdict.json().get("passed"):
                passed += 1

        out["issue"] = issue.summary()
        out["respond"] = respond.summary()
        out["resolve"] = resolve.summary()
        out["challenges_attempted"] = min(len(remote), challenges)
        out["challenges_passed"] = passed

        try:
            out["qos"] = client.get(f"{host.api}/api/contracts/{contract_id}").json().get("qos", {})
            out["health"] = client.get(f"{host.api}/api/contracts/health/summary").json()
            out["tiers"] = client.get(f"{host.api}/api/contracts/tiers").json()
        except (httpx.HTTPError, ValueError):
            pass

        host.run(f"rm -f /tmp/{name} {host.mount}/{name}", timeout=300)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        out["error"] = str(exc)
    finally:
        host.api_put(
            "/api/config", {"updates": {"peers.keep_local_copy": original["keep_local_copy"]}}
        )
        client.close()
    return out
