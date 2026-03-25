"""
bench_recovery.py — Recovery & Durability benchmarks for CollectiveFS.

Measures decode speed with 0–4 missing shards, detection latency,
rolling reconstruction, and multi-file-size scenarios.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import List

from benchmarks.common import (
    BenchResult,
    BenchSuite,
    decoder_available,
    encoder_available,
    make_random_file,
    make_result,
    run_decoder,
    run_encoder,
    skipped_result,
    throughput_mbps,
    console,
)

CATEGORY = "Recovery"
ITERATIONS = 5
DATA_SHARDS = 8
PAR_SHARDS = 4
BENCH_FILE_SIZE = 4 * 1024 * 1024  # 4 MB


def _check_binaries(name: str, category: str) -> BenchResult | None:
    if not encoder_available() or not decoder_available():
        return skipped_result(name, category, "encoder/decoder binary not found", "ms")
    return None


class BenchmarkRecovery:
    """Recovery and durability benchmarks."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations

    # ------------------------------------------------------------------
    # Recovery time: 0 missing shards (baseline full decode)
    # ------------------------------------------------------------------

    def test_recovery_time_0_missing_shards(self) -> BenchResult:
        name = "recovery_time [missing=0, baseline]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_time_0_missing_shards...[/dim]")
        return self._recovery_time_n(name, missing=0)

    def test_recovery_time_1_missing_shard(self) -> BenchResult:
        name = "recovery_time [missing=1]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_time_1_missing_shard...[/dim]")
        return self._recovery_time_n(name, missing=1)

    def test_recovery_time_2_missing_shards(self) -> BenchResult:
        name = "recovery_time [missing=2]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_time_2_missing_shards...[/dim]")
        return self._recovery_time_n(name, missing=2)

    def test_recovery_time_3_missing_shards(self) -> BenchResult:
        name = "recovery_time [missing=3]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_time_3_missing_shards...[/dim]")
        return self._recovery_time_n(name, missing=3)

    def test_recovery_time_4_missing_shards(self) -> BenchResult:
        name = "recovery_time [missing=4, at_tolerance]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_time_4_missing_shards...[/dim]")
        return self._recovery_time_n(name, missing=4)

    def _recovery_time_n(self, name: str, missing: int) -> BenchResult:
        """Shared helper: encode file, remove `missing` shards, time the decode."""
        size_bytes = BENCH_FILE_SIZE
        samples: List[float] = []

        def _run(m=missing):
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                shard_dir = tmpdir / "shards"
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)

                shards = sorted(shard_dir.iterdir())
                for shard in shards[:m]:
                    shard.unlink()

                out_dir = tmpdir / "out"
                t0 = time.perf_counter()
                run_decoder(shard_dir, out_dir, "src.bin",
                            data=DATA_SHARDS, par=PAR_SHARDS)
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ms_samples = [s * 1000 for s in samples]
        r = make_result(name, CATEGORY, ms_samples, "ms",
                        {"missing": missing, "data": DATA_SHARDS,
                         "par": PAR_SHARDS, "file_size": "4MB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Recovery throughput MB/s
    # ------------------------------------------------------------------

    def test_recovery_throughput_mbps(self) -> BenchResult:
        name = "recovery_throughput_mbps [4 missing shards]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]recovery_throughput_mbps...[/dim]")

        size_bytes = BENCH_FILE_SIZE
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                shard_dir = tmpdir / "shards"
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                shards = sorted(shard_dir.iterdir())
                for shard in shards[:4]:
                    shard.unlink()
                out_dir = tmpdir / "out"
                t0 = time.perf_counter()
                run_decoder(shard_dir, out_dir, "src.bin",
                            data=DATA_SHARDS, par=PAR_SHARDS)
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        r = make_result(name, CATEGORY, mbps, "MB/s",
                        {"missing": 4, "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Time to detect a missing shard
    # ------------------------------------------------------------------

    def test_time_to_detect_missing_shard(self) -> BenchResult:
        name = "time_to_detect_missing_shard"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]time_to_detect_missing_shard...[/dim]")

        size_bytes = BENCH_FILE_SIZE
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                shard_dir = tmpdir / "shards"
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)

                # Delete a shard
                shards = sorted(shard_dir.iterdir())
                victim = shards[0]
                victim.unlink()

                # Time the detection (stat-based check for all expected shards)
                expected_total = DATA_SHARDS + PAR_SHARDS
                t0 = time.perf_counter()
                missing = []
                for i in range(expected_total):
                    shard_path = shard_dir / f"src.bin.{i}"
                    if not shard_path.exists():
                        missing.append(i)
                elapsed = time.perf_counter() - t0
                assert len(missing) == 1
                return elapsed

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        us_samples = [s * 1_000_000 for s in samples]
        r = make_result(name, CATEGORY, us_samples, "µs",
                        {"method": "stat_all_shards",
                         "total_shards": DATA_SHARDS + PAR_SHARDS})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Full recovery pipeline: detect → reconstruct → verify SHA256
    # ------------------------------------------------------------------

    def test_full_recovery_pipeline(self) -> BenchResult:
        name = "full_recovery_pipeline [4MB, 2 missing]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]full_recovery_pipeline...[/dim]")

        size_bytes = BENCH_FILE_SIZE
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                original_sha = hashlib.sha256(src.read_bytes()).hexdigest()
                shard_dir = tmpdir / "shards"
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)

                # Simulate data loss
                shards = sorted(shard_dir.iterdir())
                for shard in shards[:2]:
                    shard.unlink()

                t0 = time.perf_counter()

                # Step 1: detect
                expected_total = DATA_SHARDS + PAR_SHARDS
                missing_indices = [
                    i for i in range(expected_total)
                    if not (shard_dir / f"src.bin.{i}").exists()
                ]

                # Step 2: reconstruct
                out_dir = tmpdir / "out"
                run_decoder(shard_dir, out_dir, "src.bin",
                            data=DATA_SHARDS, par=PAR_SHARDS)

                # Step 3: verify
                recovered = (out_dir / "src.bin").read_bytes()
                recovered_sha = hashlib.sha256(recovered).hexdigest()

                elapsed = time.perf_counter() - t0
                assert recovered_sha == original_sha, "SHA256 mismatch!"
                assert len(missing_indices) == 2
                return elapsed

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ms_samples = [s * 1000 for s in samples]
        r = make_result(name, CATEGORY, ms_samples, "ms",
                        {"missing": 2, "verified": "sha256", "file_size": "4MB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Recovery with different file sizes
    # ------------------------------------------------------------------

    def test_recovery_with_different_file_sizes(self) -> List[BenchResult]:
        results = []
        test_sizes = {
            "64KB":  64 * 1024,
            "1MB":   1 * 1024 * 1024,
            "16MB":  16 * 1024 * 1024,
        }
        missing = 4

        if not encoder_available() or not decoder_available():
            for label in test_sizes:
                name = f"recovery_different_sizes [missing=4, {label}]"
                r = skipped_result(name, CATEGORY, "encoder/decoder not found", "ms")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]recovery_with_different_file_sizes...[/dim]")

        for label, size_bytes in test_sizes.items():
            name = f"recovery_different_sizes [missing={missing}, {label}]"
            samples: List[float] = []

            def _run(sz=size_bytes, m=missing):
                with tempfile.TemporaryDirectory() as tmp:
                    tmpdir = Path(tmp)
                    src = make_random_file(tmpdir / "src.bin", sz)
                    shard_dir = tmpdir / "shards"
                    run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                    shards = sorted(shard_dir.iterdir())
                    for shard in shards[:m]:
                        shard.unlink()
                    out_dir = tmpdir / "out"
                    t0 = time.perf_counter()
                    run_decoder(shard_dir, out_dir, "src.bin",
                                data=DATA_SHARDS, par=PAR_SHARDS)
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            ms_samples = [s * 1000 for s in samples]
            r = make_result(name, CATEGORY, ms_samples, "ms",
                            {"missing": missing, "file_size": label})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Rolling reconstruction: lose shard, reconstruct, lose another, repeat
    # ------------------------------------------------------------------

    def test_rolling_reconstruction(self) -> BenchResult:
        name = "rolling_reconstruction [3 rounds, 4MB]"
        skip = _check_binaries(name, CATEGORY)
        if skip:
            self.suite.add(skip)
            return skip

        console.print("  [dim]test_rolling_reconstruction...[/dim]")

        size_bytes = BENCH_FILE_SIZE
        rounds = 3
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                shard_dir = tmpdir / "shards"
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)

                total_elapsed = 0.0
                for round_i in range(rounds):
                    # Lose the next shard in sequence
                    shard_to_lose = shard_dir / f"src.bin.{round_i}"
                    if shard_to_lose.exists():
                        shard_to_lose.unlink()

                    out_dir = tmpdir / f"out_{round_i}"
                    t0 = time.perf_counter()
                    run_decoder(shard_dir, out_dir, "src.bin",
                                data=DATA_SHARDS, par=PAR_SHARDS)
                    total_elapsed += time.perf_counter() - t0

                return total_elapsed

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ms_samples = [s * 1000 for s in samples]
        r = make_result(name, CATEGORY, ms_samples, "ms",
                        {"rounds": rounds, "file_size": "4MB",
                         "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self) -> BenchSuite:
        console.print(f"\n[bold cyan]Running Recovery benchmarks[/bold cyan]")
        self.test_recovery_time_0_missing_shards()
        self.test_recovery_time_1_missing_shard()
        self.test_recovery_time_2_missing_shards()
        self.test_recovery_time_3_missing_shards()
        self.test_recovery_time_4_missing_shards()
        self.test_recovery_throughput_mbps()
        self.test_time_to_detect_missing_shard()
        self.test_full_recovery_pipeline()
        self.test_recovery_with_different_file_sizes()
        self.test_rolling_reconstruction()
        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from benchmarks.common import BenchSuite
    suite = BenchSuite("Recovery Benchmarks")
    runner = BenchmarkRecovery(suite)
    runner.run_all()
    suite.print_table()
