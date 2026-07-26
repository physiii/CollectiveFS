#!/usr/bin/env python3
"""Run the mounted-filesystem evaluation across the cluster and write a report.

    python -m benchmarks.run_mount_eval \
        --node sonic=http://localhost:8010 \
        --node office=http://192.168.1.43:8010@office \
        --report benchmarks/results/mount-eval.md

Each `--node` is `name=api_url[@ssh_target]`. Omitting the ssh target means the
node is this machine. Results are written as both JSON (for diffing across runs)
and Markdown (for reading).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.bench_mount import (
    GB,
    KB,
    MB,
    SIZE_LADDER,
    Host,
    bench_contracts,
    bench_degraded_read,
    bench_metadata_ops,
    bench_parallel_load,
    bench_real_tree,
    bench_reconciliation,
    bench_saturation,
    bench_write_read,
    collect_mount_metrics,
    collect_placement,
    describe_host,
    measure_link,
    ui_parity,
)

RESULTS_DIR = Path(__file__).parent / "results"


# ── formatting ──────────────────────────────────────────────────────


def human_bytes(value: Optional[float]) -> str:
    if value is None:
        return "—"
    value = float(value)
    if abs(value) < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024
        if abs(value) < 1024:
            return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"
    return f"{value:.1f} EB"


def ms(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    return f"{value:.1f} ms"


def mbs(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f} MB/s"


def table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "_No data._\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out) + "\n"


# ── report ──────────────────────────────────────────────────────────


def build_report(data: Dict[str, Any]) -> str:
    started = data["started_at"]
    nodes = data["nodes"]
    parts: List[str] = []

    parts.append("# CollectiveFS — Performance and Evaluation Report\n")
    parts.append(
        f"Generated {started} · harness `benchmarks/run_mount_eval.py` · "
        f"duration {data['duration_s'] / 60:.1f} min\n"
    )
    parts.append(
        "Every figure below was measured against the live cluster through the "
        f"`{data['mount']}` mount, with erasure coding, encryption, shard "
        "distribution and peer routing all in the path. Nothing is extrapolated; "
        "where a measurement was not possible the row says so.\n"
    )

    # ── compute ─────────────────────────────────────────────────────
    parts.append("\n## 1. Compute under test\n")
    rows = []
    for name, info in nodes.items():
        env = info["env"]
        mem_gb = int(env.get("mem_total_kb", 0) or 0) / (1024 * 1024)
        rows.append([
            f"**{name}**",
            env.get("cpu_model", "?"),
            f"{env.get('cpu_cores', '?')} threads",
            f"{mem_gb:.0f} GB",
            env.get("os", "?"),
            env.get("kernel", "?"),
        ])
    parts.append(table(
        ["Node", "CPU", "Parallelism", "Memory", "OS", "Kernel"], rows))

    rows = []
    for name, info in nodes.items():
        env = info["env"]
        rota = env.get("backing_rota", "")
        media = "SSD/NVMe" if rota == "0" else ("HDD" if rota == "1" else "unknown")
        model = env.get("backing_model") or ""
        if model:
            media = f"{model} ({media})" if rota in ("0", "1") else model
        rows.append([
            f"**{name}**",
            env.get("node_id", "?")[:8],
            human_bytes(env.get("quota_bytes")),
            human_bytes(env.get("used_bytes")),
            human_bytes(env.get("backing_total")),
            human_bytes(env.get("device_free_bytes")),
            media,
            env.get("erasure", "?"),
        ])
    parts.append("\n")
    parts.append(table(
        ["Node", "Node ID", "Pledged quota", "Used", "Backing disk", "Free on disk",
         "Media", "Erasure"], rows))

    link = data.get("link", {})
    parts.append("\n### Interconnect\n")
    parts.append(table(
        ["Measurement", "Value"],
        [
            ["ICMP round trip", link.get("rtt", "—")],
            ["Node API round trip (median)", ms(link.get("api_rtt_ms"))],
            ["Shell/ssh invocation overhead (median)", ms(link.get("shell_overhead_ms"))],
        ],
    ))
    parts.append(
        "\n> Operation timings taken on the remote node include the shell "
        "invocation overhead above. Subtract it when comparing a remote "
        "single-operation latency against a local one.\n"
    )

    # ── throughput ──────────────────────────────────────────────────
    parts.append("\n## 2. Throughput by file size\n")
    parts.append(
        "Each size was written through the mount and read back, with a SHA-256 "
        "check on every round trip. Write time is measured until `cp` returns — "
        "the upload happens on close, so that is the honest end of the write.\n"
    )
    for name, info in nodes.items():
        rows = []
        for entry in info.get("throughput", []):
            write, read = entry["write"], entry["read"]
            rows.append([
                entry["size_label"],
                human_bytes(entry["size_bytes"]),
                mbs(write.get("throughput_mbs")),
                ms(write.get("median_ms")),
                mbs(read.get("throughput_mbs")),
                ms(read.get("median_ms")),
                f"{entry['integrity_verified']}/{entry['integrity_attempted']}",
            ])
        parts.append(f"\n### {name}\n")
        parts.append(table(
            ["Size", "Bytes", "Write", "Write median", "Read", "Read median",
             "SHA-256 verified"], rows))

    # ── latency ─────────────────────────────────────────────────────
    parts.append("\n## 3. Operation latency\n")
    parts.append(
        "Per-operation cost as a shell experiences it. These are whole-command "
        "timings, so each includes process startup.\n"
    )
    for name, info in nodes.items():
        rows = []
        for entry in info.get("metadata_ops", []):
            if not entry.get("count"):
                rows.append([entry["label"], "—", "—", "—", "—", f"{entry.get('failures', 0)} failed"])
                continue
            rows.append([
                entry["label"],
                str(entry["count"]),
                ms(entry.get("median_ms")),
                ms(entry.get("p95_ms")),
                ms(entry.get("max_ms")),
                str(entry.get("failures", 0)),
            ])
        parts.append(f"\n### {name}\n")
        parts.append(table(["Operation", "Samples", "Median", "p95", "Max", "Failures"], rows))

    # ── mount-reported metrics ──────────────────────────────────────
    parts.append("\n## 4. Kernel-level operation mix\n")
    parts.append(
        "Reported by the FUSE layer itself, so these exclude shell startup and "
        "show the true cost of each filesystem call.\n"
    )
    for name, info in nodes.items():
        metrics = info.get("mount_metrics", {})
        operations = metrics.get("operations", [])
        rows = [
            [
                entry["op"],
                str(entry["count"]),
                f"{entry['avg_ms']} ms",
                f"{entry['max_ms']} ms",
                str(entry["errors"]),
            ]
            for entry in operations[:14]
        ]
        totals = metrics.get("totals", {})
        parts.append(f"\n### {name}\n")
        if totals:
            parts.append(
                f"Window {totals.get('window_seconds', 0):.0f}s · "
                f"{totals.get('ops', 0)} operations · "
                f"read {mbs((totals.get('read_bps') or 0) / MB)} · "
                f"write {mbs((totals.get('write_bps') or 0) / MB)} · "
                f"{totals.get('errors', 0)} errors\n\n"
            )
        parts.append(table(["FUSE operation", "Calls", "Mean", "Peak", "Errors"], rows))

    # ── concurrency ─────────────────────────────────────────────────
    parts.append("\n## 5. Concurrent load\n")
    rows = []
    for name, info in nodes.items():
        for entry in info.get("parallel", []):
            rows.append([
                name,
                str(entry["streams"]),
                human_bytes(entry["size_bytes"]),
                human_bytes(entry["total_bytes"]),
                f"{entry['elapsed_s']} s",
                mbs(entry.get("aggregate_mbs")),
                f"{entry['files_landed']}/{entry['streams']}",
            ])
    parts.append(table(
        ["Node", "Streams", "Per file", "Total", "Elapsed", "Aggregate", "Landed"], rows))

    # ── real workload ───────────────────────────────────────────────
    real_rows = []
    for name, info in nodes.items():
        entry = info.get("real_tree")
        if not entry or entry.get("error"):
            continue
        real_rows.append([
            f"**{name}**",
            f"{entry['files']} files",
            human_bytes(entry["bytes"]),
            human_bytes(entry["mean_file_bytes"]),
            f"{entry['write_s']} s",
            f"{entry.get('write_files_per_s')}/s",
            f"{entry['read_s']} s",
            f"{entry.get('read_files_per_s')}/s",
            "identical" if entry.get("identical") else "**differs**",
        ])
    if real_rows:
        parts.append("\n## 6. Real directory tree\n")
        parts.append(
            "A genuine source tree copied in, read back, and compared with "
            "`diff -r` — every byte of every file, both directions. This is the "
            "shape most real data has: many small files, where per-file cost "
            "dominates and raw throughput barely matters.\n"
        )
        parts.append(table(
            ["Node", "Files", "Total", "Mean file", "Write", "Write rate",
             "Read", "Read rate", "Verified"], real_rows))

    # ── reconciliation ──────────────────────────────────────────────
    parts.append("\n## 7. Cross-node reconciliation\n")
    parts.append(
        "Time from a write completing on one machine to the file being visible, "
        "then readable, on the other.\n"
    )
    rows = []
    for entry in data.get("reconciliation", []):
        visible, readable = entry["visible"], entry["readable"]
        rows.append([
            f"{entry['from']} → {entry['to']}",
            str(visible.get("count", 0)),
            ms(visible.get("median_ms")),
            ms(visible.get("p95_ms")),
            ms(readable.get("median_ms")),
            ms(readable.get("max_ms")),
            str(visible.get("failures", 0) + readable.get("failures", 0)),
        ])
    parts.append(table(
        ["Direction", "Samples", "Visible median", "Visible p95",
         "Readable median", "Readable max", "Failures"], rows))

    # ── distribution ────────────────────────────────────────────────
    parts.append("\n## 8. Shard distribution\n")
    rows = []
    for name, info in nodes.items():
        placement = info.get("placement", {}).get("collective", {})
        hosted = info.get("placement", {}).get("hosted_for_peers", {})
        rows.append([
            f"**{name}**",
            str(placement.get("files", 0)),
            str(placement.get("shards_total", 0)),
            str(placement.get("shards_local", 0)),
            str(placement.get("shards_remote", 0)),
            str(hosted.get("shards", 0)),
            str(placement.get("shards_missing", 0)),
            human_bytes(placement.get("own_bytes")),
            human_bytes(placement.get("hosted_bytes")),
            f"{placement.get('expansion_ratio', '—')}x",
        ])
    parts.append(table(
        ["Node", "Files", "Shards", "Held here", "On peers", "Stored for peers",
         "Missing", "Own shards", "Hosted for peers", "Expansion"], rows))
    parts.append(
        "\n> Expansion is our shards against our own data — the erasure-coding "
        "overhead. Data stored for peers occupies the quota but is counted "
        "separately, since it is not our storage cost.\n"
    )

    degraded = data.get("degraded")
    if degraded:
        parts.append("\n### Fault tolerance\n")
        parts.append(table(
            ["Check", "Result"],
            [
                ["File size", human_bytes(degraded.get("size_bytes"))],
                ["Shard placement", json.dumps(degraded.get("placement", {}))],
                ["Peer holding remote shards", "stopped"],
                ["Read succeeded", "yes" if degraded.get("read_ok") else "**no**"],
                ["Read time", ms(degraded.get("read_ms"))],
                ["SHA-256 matched", "yes" if degraded.get("integrity_ok") else "**no**"],
            ],
        ))

    # ── saturation ──────────────────────────────────────────────────
    saturation = data.get("saturation")
    if saturation:
        parts.append("\n## 9. Quota saturation\n")
        parts.append(
            "The production pledge is 1 TB per node. Filling that to its cutoff "
            "would take hours and a terabyte of disk, and the behaviour under "
            "test — what the node does when it runs out of pledged room — is "
            "identical at any quota. So the quota was temporarily lowered, "
            "driven past the cutoff, and restored.\n"
        )
        parts.append(table(
            ["Measurement", "Value"],
            [
                ["Temporary quota", human_bytes(saturation.get("temporary_quota_bytes"))],
                ["Write cutoff watermark", f"{saturation.get('watermark_percent')}%"],
                ["Data written before cutoff", human_bytes(saturation.get("bytes_written"))],
                ["Files written", str(saturation.get("files_written"))],
                ["Elapsed", f"{saturation.get('elapsed_s')} s"],
                ["Cutoff triggered", "yes" if saturation.get("cutoff_triggered") else "no"],
                ["Usage at stop", f"{saturation.get('used_percent_at_stop')}%"],
                ["Accepting writes after cutoff", "no" if saturation.get("accepting_writes_after") is False else "yes"],
                ["Node response", f"`{saturation.get('message', '')}`"],
                ["Quota restored to", human_bytes(saturation.get("restored_quota_bytes"))],
            ],
        ))

    # ── contracts ───────────────────────────────────────────────────
    contracts = data.get("contracts")
    if contracts:
        parts.append("\n## 10. Peer contracts and proof-of-storage\n")
        parts.append(
            "Contracts are how a node verifies a peer is really holding what it "
            "claims: it asks for a hash of bytes at random offsets in a shard, "
            "with a nonce so the answer cannot be replayed. This runs on a timer "
            "per contract, so its cost is part of steady-state load.\n"
        )
        if contracts.get("error"):
            parts.append(f"\n> Not measured: {contracts['error']}\n")
        issue = contracts.get("issue", {})
        respond = contracts.get("respond", {})
        resolve = contracts.get("resolve", {})
        qos = contracts.get("qos", {})
        if issue.get("count") or respond.get("count"):
            parts.append("\n### Challenge round trip\n")
            parts.append(table(
                ["Stage", "Samples", "Median", "p95", "Max", "Failures"],
                [
                    ["Origin builds the challenge", str(issue.get("count", 0)),
                     ms(issue.get("median_ms")), ms(issue.get("p95_ms")),
                     ms(issue.get("max_ms")), str(issue.get("failures", 0))],
                    ["Peer computes the proof", str(respond.get("count", 0)),
                     ms(respond.get("median_ms")), ms(respond.get("p95_ms")),
                     ms(respond.get("max_ms")), str(respond.get("failures", 0))],
                    ["Origin verifies and scores", str(resolve.get("count", 0)),
                     ms(resolve.get("median_ms")), ms(resolve.get("p95_ms")),
                     ms(resolve.get("max_ms")), str(resolve.get("failures", 0))],
                ],
            ))
            total_ms = sum(
                stage.get("median_ms") or 0 for stage in (issue, respond, resolve)
            )
            parts.append(f"\nEnd-to-end proof of one shard: **{ms(total_ms)}** (sum of medians).\n")

        parts.append("\n### Outcome\n")
        parts.append(table(
            ["Measurement", "Value"],
            [
                ["Contract created", "yes" if contracts.get("created") else "no"],
                ["Tier", str(contracts.get("tier", "—"))],
                ["Shards placed on the peer", str(contracts.get("shards_on_peer", 0))],
                ["Challenges attempted", str(contracts.get("challenges_attempted", 0))],
                ["Challenges passed", str(contracts.get("challenges_passed", 0))],
                ["QoS score", str(qos.get("score", "—"))],
                ["Challenge pass rate", str(qos.get("challenge_pass_rate", "—"))],
            ],
        ))

        tiers = contracts.get("tiers") or []
        if tiers:
            parts.append("\n### Tier configuration\n")
            parts.append(table(
                ["Tier", "Challenge interval", "Response deadline", "Storage multiplier", "Max violations"],
                [
                    [
                        str(tier.get("tier")),
                        f"{tier.get('challenge_interval_seconds')} s",
                        f"{tier.get('response_deadline_seconds')} s",
                        f"{tier.get('storage_multiplier')}x",
                        str(tier.get("max_violations")),
                    ]
                    for tier in tiers
                ],
            ))

    # ── UI parity ───────────────────────────────────────────────────
    parts.append("\n## 11. Console and mount parity\n")
    parts.append(
        "The web console and the mount are two views of one namespace, so they "
        "must list exactly the same files.\n"
    )
    rows = []
    collisions_seen: Dict[str, int] = {}
    for name, info in nodes.items():
        parity = info.get("ui_parity", {})
        if not parity.get("checked"):
            rows.append([name, "—", "—", "—", "not checked"])
            continue
        collisions_seen.update(parity.get("colliding_paths") or {})
        rows.append([
            f"**{name}**",
            str(parity.get("ui_entries")),
            str(parity.get("ui_paths")),
            str(parity.get("mount_paths")),
            "identical" if parity.get("identical") else
            f"differs (console-only {len(parity.get('only_in_ui', []))}, "
            f"mount-only {len(parity.get('only_in_mount', []))})",
        ])
    parts.append(table(
        ["Node", "Console entries", "Distinct paths", "Paths in mount", "Result"], rows))
    if collisions_seen:
        parts.append(
            "\n> The console lists files by id, so two files can share a path. A "
            "POSIX filesystem cannot represent that, so the mount shows one of "
            "each. Colliding paths in this namespace: "
            + ", ".join(f"`{path}` ×{count}" for path, count in collisions_seen.items())
            + ".\n"
        )

    # ── observations ────────────────────────────────────────────────
    errors = data.get("errors") or []
    if errors:
        parts.append("\n## 12. Phases that did not complete\n")
        parts.append(
            "Recorded rather than hidden — a missing measurement is itself a "
            "result.\n"
        )
        parts.append(table(
            ["Phase", "Error"],
            [[entry["phase"], f"`{entry['error'][:160]}`"] for entry in errors],
        ))
        parts.append("\n## 13. Observations\n")
    else:
        parts.append("\n## 12. Observations\n")
    for line in data.get("observations", []):
        parts.append(f"- {line}\n")

    parts.append(f"\n## {14 if errors else 13}. Reproducing\n")
    parts.append("```bash\n")
    parts.append(f"{data.get('command', 'python -m benchmarks.run_mount_eval')}\n")
    parts.append("```\n")
    parts.append(
        f"\nRaw measurements: `{data.get('json_path', '')}`\n"
    )
    return "".join(parts)


# ── observations ────────────────────────────────────────────────────


def derive_observations(data: Dict[str, Any]) -> List[str]:
    """Statements the numbers support, written only where the data backs them."""
    notes: List[str] = []
    nodes = data["nodes"]

    for name, info in nodes.items():
        rows = info.get("throughput", [])
        if not rows:
            continue
        best_write = max(
            (r for r in rows if r["write"].get("throughput_mbs")),
            key=lambda r: r["write"]["throughput_mbs"],
            default=None,
        )
        best_read = max(
            (r for r in rows if r["read"].get("throughput_mbs")),
            key=lambda r: r["read"]["throughput_mbs"],
            default=None,
        )
        if best_write and best_read:
            notes.append(
                f"**{name}** peaks at {mbs(best_write['write']['throughput_mbs'])} write "
                f"({best_write['size_label']}) and {mbs(best_read['read']['throughput_mbs'])} read "
                f"({best_read['size_label']})."
            )
        small = next((r for r in rows if r["size_bytes"] <= 64 * KB), None)
        large = next((r for r in reversed(rows) if r["size_bytes"] >= 8 * MB), None)
        if small and large and small["write"].get("throughput_mbs") and large["write"].get("throughput_mbs"):
            ratio = large["write"]["throughput_mbs"] / max(small["write"]["throughput_mbs"], 0.001)
            notes.append(
                f"On **{name}** a {large['size_label']} write moves data {ratio:.0f}× faster per byte "
                f"than a {small['size_label']} one — small files are dominated by the fixed cost of "
                "encoding and the round trip, not by size."
            )

    total_verified = sum(
        entry["integrity_verified"]
        for info in nodes.values()
        for entry in info.get("throughput", [])
    )
    total_attempted = sum(
        entry["integrity_attempted"]
        for info in nodes.values()
        for entry in info.get("throughput", [])
    )
    if total_attempted:
        notes.append(
            f"Every round trip was hash-checked: **{total_verified}/{total_attempted}** "
            "files came back byte-identical after erasure coding, encryption, "
            "distribution across two machines and reconstruction."
        )

    for entry in data.get("reconciliation", []):
        median = entry["readable"].get("median_ms")
        if median:
            notes.append(
                f"A file written on **{entry['from']}** is readable on **{entry['to']}** "
                f"in {ms(median)} (median)."
            )

    contracts = data.get("contracts") or {}
    if contracts.get("challenges_passed"):
        issue = contracts.get("issue", {})
        respond = contracts.get("respond", {})
        resolve = contracts.get("resolve", {})
        total = sum(stage.get("median_ms") or 0 for stage in (issue, respond, resolve))
        notes.append(
            f"Proof-of-storage works end to end: **{contracts['challenges_passed']}/"
            f"{contracts.get('challenges_attempted')}** challenges verified, at "
            f"{ms(total)} per shard — cheap enough to run continuously across a fleet."
        )

    degraded = data.get("degraded")
    if degraded and degraded.get("read_ok") and degraded.get("integrity_ok"):
        notes.append(
            "With the peer holding a file's remote shards stopped, the file still "
            f"reconstructed byte-identically in {ms(degraded.get('read_ms'))} — the "
            "parity budget holds in practice, not just on paper."
        )

    saturation = data.get("saturation")
    if saturation and saturation.get("cutoff_triggered"):
        notes.append(
            f"The write cutoff works: the node refused new writes at "
            f"{saturation.get('used_percent_at_stop')}% of its pledged quota and "
            "reported why, rather than filling the host disk."
        )

    parity_ok = [
        name for name, info in nodes.items()
        if info.get("ui_parity", {}).get("identical")
    ]
    if parity_ok:
        notes.append(
            f"Console and mount list identical file sets on {', '.join(parity_ok)} — "
            "the UI is a view of the same namespace, not a separate index."
        )
    return notes


# ── main ────────────────────────────────────────────────────────────


def parse_node(spec: str) -> Host:
    """`name=api_url` for this machine, `name=api_url@ssh_target` for a remote.

    '@' rather than ':' because an API URL already contains colons, and a port
    is indistinguishable from an ssh target once you start splitting on them.
    """
    name, _, rest = spec.partition("=")
    api, sep, ssh = rest.partition("@")
    return Host(name=name.strip(), api=api.strip().rstrip("/"), ssh=ssh.strip() or None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the mounted filesystem across the cluster.")
    parser.add_argument("--node", action="append", required=True,
                        help="name=api_url[@ssh_target]; omit @ssh for this machine")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--op-iterations", type=int, default=10)
    parser.add_argument("--recon-iterations", type=int, default=5)
    parser.add_argument("--max-size", default="64MB",
                        help="largest file in the throughput ladder")
    parser.add_argument("--streams", type=int, default=8)
    parser.add_argument("--stream-size", default="8MB")
    parser.add_argument("--saturate", action="store_true",
                        help="drive a node past its write cutoff (uses a temporary small quota)")
    parser.add_argument("--degraded", action="store_true",
                        help="stop a peer mid-read to prove the parity budget")
    parser.add_argument("--real-tree", default=None,
                        help="a real directory to copy in, read back and diff")
    parser.add_argument("--contracts", action="store_true",
                        help="create a peer contract and time proof-of-storage challenges")
    parser.add_argument("--report", default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    def parse_size(text: str) -> int:
        for label, size in SIZE_LADDER:
            if label.lower() == text.lower():
                return size
        return int(text)

    limit = parse_size(args.max_size)
    ladder = [(label, size) for label, size in SIZE_LADDER if size <= limit]

    hosts = [parse_node(spec) for spec in args.node]
    prefix = f"bench{int(time.time())}"
    started = time.perf_counter()

    data: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "mount": hosts[0].mount,
        "harness_host": platform.node(),
        "nodes": {},
        "command": "python -m benchmarks.run_mount_eval "
                   + " ".join(f"--node {spec}" for spec in args.node)
                   + f" --iterations {args.iterations} --max-size {args.max_size}"
                   + (" --saturate" if args.saturate else "")
                   + (" --degraded" if args.degraded else "")
                   + (" --contracts" if args.contracts else "")
                   + (f" --real-tree {args.real_tree}" if args.real_tree else ""),
    }

    def phase(label: str, func, *fargs, **fkwargs):
        """Run one measurement phase, recording a failure instead of losing the run.

        A long evaluation that throws away thirty minutes of good measurements
        because its last step failed is worse than useless.
        """
        print(f"→ {label}")
        try:
            return func(*fargs, **fkwargs)
        except Exception as exc:  # noqa: BLE001 - any failure is worth reporting
            print(f"  ! {label} failed: {exc}")
            data.setdefault("errors", []).append({"phase": label, "error": str(exc)})
            return None

    print("→ capturing environment")
    for host in hosts:
        data["nodes"][host.name] = {"env": describe_host(host)}

    if len(hosts) >= 2:
        print("→ measuring the link")
        data["link"] = measure_link(hosts[0], hosts[1])

    for host in hosts:
        data["nodes"][host.name]["throughput"] = phase(
            f"throughput on {host.name}", bench_write_read, host, ladder, args.iterations, prefix
        ) or []
        data["nodes"][host.name]["metadata_ops"] = phase(
            f"operation latency on {host.name}", bench_metadata_ops, host, args.op_iterations, prefix
        ) or []
        parallel = phase(
            f"concurrent load on {host.name}", bench_parallel_load,
            host, args.streams, parse_size(args.stream_size), prefix
        )
        data["nodes"][host.name]["parallel"] = [parallel] if parallel else []

    if args.real_tree:
        for host in hosts:
            data["nodes"][host.name]["real_tree"] = phase(
                f"real directory tree on {host.name}", bench_real_tree,
                host, args.real_tree, prefix,
            )

    if len(hosts) >= 2:
        data["reconciliation"] = []
        for writer, reader in ((hosts[0], hosts[1]), (hosts[1], hosts[0])):
            entry = phase(
                f"reconciliation {writer.name} → {reader.name}",
                bench_reconciliation, writer, reader, args.recon_iterations, prefix,
            )
            if entry:
                entry["from"] = writer.name
                entry["to"] = reader.name
                data["reconciliation"].append(entry)

    if args.degraded and len(hosts) >= 2:
        data["degraded"] = phase(
            "degraded read (stopping a peer)", bench_degraded_read, hosts[0], hosts[1], 8 * MB, prefix
        )

    if args.contracts and len(hosts) >= 2:
        data["contracts"] = phase(
            "peer contracts and proof-of-storage", bench_contracts, hosts[0], hosts[1]
        )

    if args.saturate:
        data["saturation"] = phase(
            "quota saturation", bench_saturation,
            hosts[0], hosts[1] if len(hosts) > 1 else hosts[0], 400 * MB, 32 * MB, prefix
        )

    for host in hosts:
        data["nodes"][host.name]["mount_metrics"] = collect_mount_metrics(host)
        data["nodes"][host.name]["placement"] = collect_placement(host)
        data["nodes"][host.name]["ui_parity"] = ui_parity(host)

    data["duration_s"] = round(time.perf_counter() - started, 1)
    data["observations"] = derive_observations(data)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_path = Path(args.json or RESULTS_DIR / f"mount-eval_{stamp}.json")
    report_path = Path(args.report or RESULTS_DIR / f"mount-eval_{stamp}.md")
    data["json_path"] = str(json_path)

    json_path.write_text(json.dumps(data, indent=2, default=str))
    report_path.write_text(build_report(data))
    print(f"\nJSON:   {json_path}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
