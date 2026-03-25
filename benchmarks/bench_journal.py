"""
bench_journal.py — FS Journaling & Metadata benchmarks for CollectiveFS.

Implements a simple WAL (Write-Ahead Log) and benchmarks metadata operations:
append latency (durable + buffered), replay speed, checkpoint, percentile latency,
concurrent writes, crash recovery simulation, and tree scan latency.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

from benchmarks.common import (
    BenchResult,
    BenchSuite,
    make_result,
    console,
)

CATEGORY = "Journal"
ITERATIONS = 5


# ---------------------------------------------------------------------------
# WAL implementation
# ---------------------------------------------------------------------------

class WAL:
    """Simple write-ahead log for CollectiveFS metadata operations."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.touch()

    def append(self, op: str, payload: dict) -> None:
        """Append a journal entry atomically (with fsync)."""
        entry = json.dumps({"op": op, "ts": time.time(), **payload}) + "\n"
        with open(self.path, "a") as f:
            f.write(entry)
            f.flush()
            os.fsync(f.fileno())

    def append_buffered(self, op: str, payload: dict) -> None:
        """Append without fsync (higher throughput, less durable)."""
        entry = json.dumps({"op": op, "ts": time.time(), **payload}) + "\n"
        with open(self.path, "a") as f:
            f.write(entry)

    def replay(self) -> list:
        """Read and parse all journal entries."""
        entries = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # corrupted entry — skip
        return entries

    def checkpoint(self, tree_dir: Path) -> None:
        """Apply all pending journal entries to the tree dir."""
        tree_dir.mkdir(parents=True, exist_ok=True)
        for entry in self.replay():
            if entry["op"] == "write":
                tree_path = tree_dir / f"{entry['file_id']}.json"
                tree_path.write_text(json.dumps(entry.get("metadata", {})))
            elif entry["op"] == "delete":
                tree_path = tree_dir / f"{entry['file_id']}.json"
                tree_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------

class BenchmarkJournal:
    """Journaling and metadata operation benchmarks."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations

    # ------------------------------------------------------------------
    # WAL append ops/sec (fsync'd)
    # ------------------------------------------------------------------

    def test_wal_append_ops_per_sec(self) -> BenchResult:
        name = "wal_append_ops_per_sec [fsync]"
        console.print("  [dim]wal_append_ops_per_sec...[/dim]")

        n_ops = 50
        ops_per_sec_samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                wal = WAL(Path(tmp) / "journal.log")
                t0 = time.perf_counter()
                for i in range(n_ops):
                    wal.append("write", {
                        "file_id": f"file_{i:04d}",
                        "metadata": {"size": 1024 * i, "shards": 12}
                    })
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            elapsed = _run()
            ops_per_sec_samples.append(n_ops / elapsed)

        r = make_result(name, CATEGORY, ops_per_sec_samples, "ops/s",
                        {"n_ops": n_ops, "durable": True})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # WAL append ops/sec (buffered, no fsync)
    # ------------------------------------------------------------------

    def test_wal_append_buffered_ops_per_sec(self) -> BenchResult:
        name = "wal_append_buffered_ops_per_sec [no fsync]"
        console.print("  [dim]wal_append_buffered_ops_per_sec...[/dim]")

        n_ops = 1000
        ops_per_sec_samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                wal = WAL(Path(tmp) / "journal.log")
                t0 = time.perf_counter()
                for i in range(n_ops):
                    wal.append_buffered("write", {
                        "file_id": f"file_{i:04d}",
                        "metadata": {"size": 1024 * i, "shards": 12}
                    })
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            elapsed = _run()
            ops_per_sec_samples.append(n_ops / elapsed)

        r = make_result(name, CATEGORY, ops_per_sec_samples, "ops/s",
                        {"n_ops": n_ops, "durable": False})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # WAL vs direct JSON write overhead
    # ------------------------------------------------------------------

    def test_wal_vs_direct_json_write(self) -> List[BenchResult]:
        results = []
        console.print("  [dim]wal_vs_direct_json_write...[/dim]")

        n_ops = 50
        payload = {"file_id": "abc123", "metadata": {"size": 4096, "shards": 12}}

        # WAL (fsync)
        wal_samples: List[float] = []

        def _wal_run():
            with tempfile.TemporaryDirectory() as tmp:
                wal = WAL(Path(tmp) / "journal.log")
                t0 = time.perf_counter()
                for _ in range(n_ops):
                    wal.append("write", payload)
                return time.perf_counter() - t0

        _wal_run()  # warmup
        for _ in range(self.iterations):
            wal_samples.append(_wal_run())

        r_wal = make_result(
            "wal_vs_direct_json [wal_fsync]", CATEGORY,
            [n_ops / s for s in wal_samples], "ops/s",
            {"mode": "WAL_fsync", "n_ops": n_ops}
        )
        self.suite.add(r_wal)
        results.append(r_wal)

        # Direct JSON write (atomic rename)
        direct_samples: List[float] = []

        def _direct_run():
            with tempfile.TemporaryDirectory() as tmp:
                meta_dir = Path(tmp) / "meta"
                meta_dir.mkdir()
                t0 = time.perf_counter()
                for i in range(n_ops):
                    target = meta_dir / f"file_{i:04d}.json"
                    tmp_path = target.with_suffix(".tmp")
                    tmp_path.write_text(json.dumps(payload))
                    tmp_path.rename(target)
                return time.perf_counter() - t0

        _direct_run()  # warmup
        for _ in range(self.iterations):
            direct_samples.append(_direct_run())

        r_direct = make_result(
            "wal_vs_direct_json [direct_atomic]", CATEGORY,
            [n_ops / s for s in direct_samples], "ops/s",
            {"mode": "direct_rename", "n_ops": n_ops}
        )
        self.suite.add(r_direct)
        results.append(r_direct)

        return results

    # ------------------------------------------------------------------
    # WAL replay speed (entries/sec)
    # ------------------------------------------------------------------

    def test_wal_replay_speed(self) -> BenchResult:
        name = "wal_replay_speed"
        console.print("  [dim]wal_replay_speed...[/dim]")

        n_entries = 1000
        samples: List[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            wal = WAL(Path(tmp) / "journal.log")
            for i in range(n_entries):
                wal.append_buffered("write", {
                    "file_id": f"file_{i:04d}",
                    "metadata": {"size": 4096, "shards": 12, "checksum": "abc123def456"}
                })

            def _run():
                t0 = time.perf_counter()
                entries = wal.replay()
                elapsed = time.perf_counter() - t0
                assert len(entries) == n_entries
                return elapsed

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

        entries_per_sec = [n_entries / s for s in samples]
        r = make_result(name, CATEGORY, entries_per_sec, "entries/s",
                        {"n_entries": n_entries})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Journal checkpoint time (apply 1000 entries to tree dir)
    # ------------------------------------------------------------------

    def test_journal_checkpoint_time(self) -> BenchResult:
        name = "journal_checkpoint_time [1000 entries]"
        console.print("  [dim]journal_checkpoint_time...[/dim]")

        n_entries = 1000
        samples: List[float] = []

        with tempfile.TemporaryDirectory() as outer_tmp:
            wal_path = Path(outer_tmp) / "journal.log"
            wal = WAL(wal_path)
            for i in range(n_entries):
                wal.append_buffered("write", {
                    "file_id": f"file_{i:04d}",
                    "metadata": {"size": 4096 * i, "shards": 12}
                })
            # Add some deletes
            for i in range(0, 100, 10):
                wal.append_buffered("delete", {"file_id": f"file_{i:04d}"})

            def _run():
                with tempfile.TemporaryDirectory() as tmp:
                    tree_dir = Path(tmp) / "tree"
                    t0 = time.perf_counter()
                    wal.checkpoint(tree_dir)
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

        ms_samples = [s * 1000 for s in samples]
        r = make_result(name, CATEGORY, ms_samples, "ms",
                        {"n_entries": n_entries, "includes_deletes": True})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Metadata write latency p50/p99
    # ------------------------------------------------------------------

    def test_metadata_write_latency_p50_p99(self) -> BenchResult:
        name = "metadata_write_latency_p50_p99"
        console.print("  [dim]metadata_write_latency_p50_p99...[/dim]")

        n_ops = 200
        per_op_latencies: List[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            wal = WAL(Path(tmp) / "journal.log")

            # warmup
            for _ in range(5):
                wal.append_buffered("write", {"file_id": "warmup", "metadata": {}})

            for i in range(n_ops):
                t0 = time.perf_counter()
                wal.append_buffered("write", {
                    "file_id": f"file_{i:04d}",
                    "metadata": {"size": 1024, "shards": 12}
                })
                per_op_latencies.append((time.perf_counter() - t0) * 1_000_000)  # µs

        per_op_latencies.sort()
        p50 = per_op_latencies[int(len(per_op_latencies) * 0.50)]
        p99 = per_op_latencies[int(len(per_op_latencies) * 0.99)]
        p_mean = statistics.mean(per_op_latencies)

        # Store all latencies as the "samples" list; value = p50
        r = make_result(name, CATEGORY, per_op_latencies, "µs",
                        {"p50_us": round(p50, 3),
                         "p99_us": round(p99, 3),
                         "mean_us": round(p_mean, 3),
                         "n_ops": n_ops})
        # Override value to p50 for the table display
        r.value = p50
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Concurrent metadata writes (4 threads)
    # ------------------------------------------------------------------

    def test_concurrent_metadata_writes(self) -> BenchResult:
        name = "concurrent_metadata_writes [4 threads]"
        console.print("  [dim]concurrent_metadata_writes...[/dim]")

        n_threads = 4
        ops_per_thread = 100
        total_ops = n_threads * ops_per_thread
        samples: List[float] = []
        lock = threading.Lock()

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                wal_path = Path(tmp) / "journal.log"

                def write_batch(thread_id: int):
                    # Each thread writes to its own WAL file to avoid contention
                    twal = WAL(Path(tmp) / f"journal_{thread_id}.log")
                    for i in range(ops_per_thread):
                        twal.append_buffered("write", {
                            "file_id": f"t{thread_id}_file_{i:04d}",
                            "metadata": {"size": 4096}
                        })

                t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=n_threads) as pool:
                    futs = [pool.submit(write_batch, tid) for tid in range(n_threads)]
                    for fut in as_completed(futs):
                        fut.result()
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ops_per_sec = [total_ops / s for s in samples]
        r = make_result(name, CATEGORY, ops_per_sec, "ops/s",
                        {"threads": n_threads, "ops_per_thread": ops_per_thread})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Crash recovery simulation
    # ------------------------------------------------------------------

    def test_crash_recovery_simulation(self) -> BenchResult:
        name = "crash_recovery_simulation [100 entries, truncate last 10]"
        console.print("  [dim]crash_recovery_simulation...[/dim]")

        n_good = 100
        n_corrupt = 10
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                wal_path = Path(tmp) / "journal.log"
                wal = WAL(wal_path)

                # Write n_good clean entries
                for i in range(n_good):
                    wal.append_buffered("write", {
                        "file_id": f"file_{i:04d}",
                        "metadata": {"size": 4096}
                    })

                # Simulate crash: append partial/corrupt lines (no newlines, raw garbage)
                with open(wal_path, "a") as f:
                    for _ in range(n_corrupt):
                        f.write('{"op": "write", "ts": 9999, "file_id": "CORRUPT"')
                        # deliberately no closing brace or newline

                t0 = time.perf_counter()
                entries = wal.replay()  # WAL.replay skips bad entries gracefully
                elapsed = time.perf_counter() - t0

                # Should recover exactly n_good valid entries
                recovered = len(entries)
                return elapsed, recovered

            return 0.0, 0

        elapsed0, recovered0 = _run()  # warmup

        timing_samples = []
        recovered_counts = []
        for _ in range(self.iterations):
            elapsed, recovered = _run()
            timing_samples.append(elapsed * 1000)
            recovered_counts.append(recovered)

        assert all(c == n_good for c in recovered_counts), \
            f"Expected {n_good} entries, got {recovered_counts}"

        r = make_result(name, CATEGORY, timing_samples, "ms",
                        {"good_entries": n_good, "corrupt_appended": n_corrupt,
                         "recovered": n_good})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Journal file size growth (bytes/entry overhead)
    # ------------------------------------------------------------------

    def test_journal_file_size_growth(self) -> BenchResult:
        name = "journal_file_size_growth"
        console.print("  [dim]journal_file_size_growth...[/dim]")

        n_entries_list = [10, 50, 100, 500, 1000]
        bytes_per_entry: List[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            for n in n_entries_list:
                wal_path = Path(tmp) / f"journal_{n}.log"
                wal = WAL(wal_path)
                for i in range(n):
                    wal.append_buffered("write", {
                        "file_id": f"file_{i:04d}",
                        "metadata": {"size": 4096, "shards": 12,
                                     "checksum": "deadbeef01234567"}
                    })
                file_size = wal_path.stat().st_size
                bytes_per_entry.append(file_size / n)

        r = make_result(name, CATEGORY, bytes_per_entry, "bytes/entry",
                        {"sample_counts": n_entries_list})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Tree scan latency (scan *.json in tree dir)
    # ------------------------------------------------------------------

    def test_tree_scan_latency(self) -> List[BenchResult]:
        results = []
        file_counts = [100, 1000]
        console.print("  [dim]test_tree_scan_latency...[/dim]")

        for n_files in file_counts:
            name = f"tree_scan_latency [{n_files} files]"
            samples: List[float] = []

            with tempfile.TemporaryDirectory() as tmp:
                tree_dir = Path(tmp) / "tree"
                tree_dir.mkdir()
                meta = {"size": 4096, "shards": 12, "checksum": "abc123"}
                for i in range(n_files):
                    (tree_dir / f"file_{i:06d}.json").write_text(json.dumps(meta))

                def _run(td=tree_dir):
                    t0 = time.perf_counter()
                    entries = list(td.glob("*.json"))
                    # Actually parse each file (realistic scan)
                    parsed = []
                    for p in entries:
                        parsed.append(json.loads(p.read_text()))
                    return time.perf_counter() - t0

                _run()  # warmup
                for _ in range(self.iterations):
                    samples.append(_run())

            ms_samples = [s * 1000 for s in samples]
            r = make_result(name, CATEGORY, ms_samples, "ms",
                            {"n_files": n_files,
                             "includes_parse": True})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self) -> BenchSuite:
        console.print(f"\n[bold cyan]Running Journal benchmarks[/bold cyan]")
        self.test_wal_append_ops_per_sec()
        self.test_wal_append_buffered_ops_per_sec()
        self.test_wal_vs_direct_json_write()
        self.test_wal_replay_speed()
        self.test_journal_checkpoint_time()
        self.test_metadata_write_latency_p50_p99()
        self.test_concurrent_metadata_writes()
        self.test_crash_recovery_simulation()
        self.test_journal_file_size_growth()
        self.test_tree_scan_latency()
        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from benchmarks.common import BenchSuite
    suite = BenchSuite("Journal Benchmarks")
    runner = BenchmarkJournal(suite)
    runner.run_all()
    suite.print_table()
