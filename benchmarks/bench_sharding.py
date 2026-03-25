"""
bench_sharding.py — Sharding & Erasure-coding benchmarks for CollectiveFS.

Tests encode/decode throughput across different shard configurations, parallelism,
file sizes, and recovery scenarios.
"""

from __future__ import annotations

import os
import shutil
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

import psutil

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

CATEGORY = "Sharding"
ITERATIONS = 5
BENCH_FILE_SIZE = 4 * 1024 * 1024   # 4 MB default for single-file tests


class BenchmarkSharding:
    """Sharding and erasure coding benchmarks."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations

    # ------------------------------------------------------------------
    # Encode throughput by shard count
    # ------------------------------------------------------------------

    def test_encode_throughput_by_shard_count(self) -> List[BenchResult]:
        results = []
        configs = [4, 6, 8, 12, 16, 24, 32]

        if not encoder_available():
            for data in configs:
                name = f"encode_throughput [data={data}, par={data // 2}]"
                r = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
                self.suite.add(r)
                results.append(r)
            return results

        size_bytes = BENCH_FILE_SIZE
        console.print("  [dim]encode_throughput_by_shard_count...[/dim]")

        for data in configs:
            par = data // 2
            name = f"encode_throughput [data={data}, par={par}]"
            samples: List[float] = []

            def _run(d=data, p=par):
                with tempfile.TemporaryDirectory() as tmp:
                    src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                    out_dir = Path(tmp) / "shards"
                    return run_encoder(src, out_dir, data=d, par=p)

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            mbps = [throughput_mbps(size_bytes, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s",
                            {"data": data, "par": par, "file_size": "4MB"})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Decode throughput vs missing shard count
    # ------------------------------------------------------------------

    def test_decode_throughput_vs_missing_shards(self) -> List[BenchResult]:
        results = []
        data, par = 8, 4
        missing_counts = [0, 1, 2, 3, 4]
        size_bytes = BENCH_FILE_SIZE

        if not encoder_available() or not decoder_available():
            for m in missing_counts:
                name = f"decode_throughput [missing={m}]"
                r = skipped_result(name, CATEGORY, "encoder/decoder not found", "MB/s")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]decode_throughput_vs_missing_shards...[/dim]")

        for missing in missing_counts:
            name = f"decode_throughput [missing={missing}]"
            samples: List[float] = []

            def _run(m=missing):
                with tempfile.TemporaryDirectory() as tmp:
                    tmpdir = Path(tmp)
                    src = make_random_file(tmpdir / "src.bin", size_bytes)
                    shard_dir = tmpdir / "shards"
                    run_encoder(src, shard_dir, data=data, par=par)

                    # Delete/zero-out `m` shards (first m shards)
                    shards = sorted(shard_dir.iterdir())
                    for shard in shards[:m]:
                        shard.unlink()

                    out_dir = tmpdir / "out"
                    t0 = time.perf_counter()
                    run_decoder(shard_dir, out_dir, "src.bin", data=data, par=par)
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            mbps = [throughput_mbps(size_bytes, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s",
                            {"missing_shards": missing, "data": data, "par": par})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Parallel encode speedup (concurrent files)
    # ------------------------------------------------------------------

    def test_parallel_encode_speedup(self) -> List[BenchResult]:
        results = []
        concurrency_levels = [1, 2, 4, 8]
        size_bytes = 1 * 1024 * 1024  # 1 MB per file

        if not encoder_available():
            for c in concurrency_levels:
                name = f"parallel_encode [workers={c}]"
                r = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]parallel_encode_speedup...[/dim]")

        for workers in concurrency_levels:
            name = f"parallel_encode [workers={workers}]"
            n_files = max(workers, 4)  # always encode at least 4 files total
            samples: List[float] = []

            def _run(w=workers, n=n_files):
                with tempfile.TemporaryDirectory() as tmp:
                    tmpdir = Path(tmp)
                    srcs = [
                        make_random_file(tmpdir / f"src_{i}.bin", size_bytes)
                        for i in range(n)
                    ]
                    t0 = time.perf_counter()
                    with ThreadPoolExecutor(max_workers=w) as pool:
                        futs = [
                            pool.submit(
                                run_encoder,
                                s,
                                tmpdir / f"shards_{i}",
                                8, 4,
                            )
                            for i, s in enumerate(srcs)
                        ]
                        for fut in as_completed(futs):
                            fut.result()
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            total_bytes = size_bytes * n_files
            mbps = [throughput_mbps(total_bytes, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s",
                            {"workers": workers, "files_per_run": n_files})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Shard size distribution (stddev of shard file sizes)
    # ------------------------------------------------------------------

    def test_shard_size_distribution(self) -> List[BenchResult]:
        results = []
        test_sizes = {
            "1MB": 1 * 1024 * 1024,
            "4MB": 4 * 1024 * 1024,
            "16MB": 16 * 1024 * 1024,
        }
        data, par = 8, 4

        if not encoder_available():
            for label in test_sizes:
                name = f"shard_size_distribution [{label}]"
                r = skipped_result(name, CATEGORY, "encoder binary not found", "bytes_stdev")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]shard_size_distribution...[/dim]")

        for label, size_bytes in test_sizes.items():
            name = f"shard_size_distribution [{label}]"

            with tempfile.TemporaryDirectory() as tmp:
                src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                shard_dir = Path(tmp) / "shards"
                run_encoder(src, shard_dir, data=data, par=par)
                sizes = [s.stat().st_size for s in sorted(shard_dir.iterdir())]

            stdev = statistics.stdev(sizes) if len(sizes) > 1 else 0.0
            mean_sz = statistics.mean(sizes)
            r = make_result(name, CATEGORY, [stdev], "bytes_stdev",
                            {"mean_shard_bytes": round(mean_sz),
                             "n_shards": len(sizes),
                             "file_size": label})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Optimal shard config finder (best MB/s for 4 MB file)
    # ------------------------------------------------------------------

    def test_optimal_shard_config_finder(self) -> BenchResult:
        name = "optimal_shard_config_finder [4MB]"
        if not encoder_available():
            r = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
            self.suite.add(r)
            return r

        size_bytes = BENCH_FILE_SIZE
        console.print("  [dim]optimal_shard_config_finder...[/dim]")

        configs = [(4, 2), (6, 3), (8, 4), (12, 4), (16, 4), (16, 8), (24, 6)]
        best_mbps = 0.0
        best_config = (8, 4)
        all_results = {}

        for data, par in configs:
            def _run(d=data, p=par):
                with tempfile.TemporaryDirectory() as tmp:
                    src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                    out_dir = Path(tmp) / "shards"
                    return run_encoder(src, out_dir, data=d, par=p)

            _run()  # warmup
            times = [_run() for _ in range(3)]
            mean_mbps = throughput_mbps(size_bytes, statistics.mean(times))
            all_results[f"{data}+{par}"] = round(mean_mbps, 2)
            if mean_mbps > best_mbps:
                best_mbps = mean_mbps
                best_config = (data, par)

        r = make_result(name, CATEGORY, [best_mbps], "MB/s",
                        {"best_config": f"{best_config[0]}+{best_config[1]}",
                         "all_configs": all_results})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Parity ratio throughput: 8+2, 8+4, 8+6, 8+8
    # ------------------------------------------------------------------

    def test_parity_ratio_throughput(self) -> List[BenchResult]:
        results = []
        configs = [(8, 2), (8, 4), (8, 6), (8, 8)]
        size_bytes = BENCH_FILE_SIZE

        if not encoder_available():
            for data, par in configs:
                name = f"parity_ratio_throughput [data={data}, par={par}]"
                r = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]parity_ratio_throughput...[/dim]")

        for data, par in configs:
            name = f"parity_ratio_throughput [data={data}, par={par}]"
            overhead_pct = round(par / data * 100, 1)
            samples: List[float] = []

            def _run(d=data, p=par):
                with tempfile.TemporaryDirectory() as tmp:
                    src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                    out_dir = Path(tmp) / "shards"
                    return run_encoder(src, out_dir, data=d, par=p)

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            mbps = [throughput_mbps(size_bytes, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s",
                            {"data": data, "par": par,
                             "storage_overhead_pct": overhead_pct})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Small file sharding overhead (fixed-cost floor)
    # ------------------------------------------------------------------

    def test_small_file_sharding_overhead(self) -> List[BenchResult]:
        results = []
        small_sizes = {
            "1KB":  1 * 1024,
            "4KB":  4 * 1024,
            "16KB": 16 * 1024,
        }

        if not encoder_available():
            for label in small_sizes:
                name = f"small_file_sharding_overhead [{label}]"
                r = skipped_result(name, CATEGORY, "encoder binary not found", "ms")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]small_file_sharding_overhead...[/dim]")

        for label, size_bytes in small_sizes.items():
            name = f"small_file_sharding_overhead [{label}]"
            samples: List[float] = []

            def _run(sz=size_bytes):
                with tempfile.TemporaryDirectory() as tmp:
                    src = make_random_file(Path(tmp) / "src.bin", sz)
                    out_dir = Path(tmp) / "shards"
                    return run_encoder(src, out_dir, data=4, par=2)

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            ms_samples = [s * 1000 for s in samples]
            r = make_result(name, CATEGORY, ms_samples, "ms",
                            {"file_size": label, "file_bytes": size_bytes})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Memory during encode (peak RSS via psutil)
    # ------------------------------------------------------------------

    def test_memory_during_encode(self) -> BenchResult:
        name = "memory_during_encode [4MB]"
        if not encoder_available():
            r = skipped_result(name, CATEGORY, "encoder binary not found", "MB_rss")
            self.suite.add(r)
            return r

        size_bytes = BENCH_FILE_SIZE
        console.print("  [dim]memory_during_encode...[/dim]")

        proc = psutil.Process()
        rss_deltas: List[float] = []

        for _ in range(self.iterations):
            with tempfile.TemporaryDirectory() as tmp:
                src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                out_dir = Path(tmp) / "shards"
                rss_before = proc.memory_info().rss
                run_encoder(src, out_dir, data=8, par=4)
                rss_after = proc.memory_info().rss
                delta_mb = (rss_after - rss_before) / (1024 * 1024)
                rss_deltas.append(delta_mb)

        r = make_result(name, CATEGORY, rss_deltas, "MB_rss_delta",
                        {"file_size": "4MB", "data": 8, "par": 4})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Encode linear scaling (verify ~linear throughput with file size)
    # ------------------------------------------------------------------

    def test_encode_linear_scaling(self) -> BenchResult:
        name = "encode_linear_scaling"
        if not encoder_available():
            r = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
            self.suite.add(r)
            return r

        console.print("  [dim]encode_linear_scaling...[/dim]")

        sizes_bytes = [
            256 * 1024,        # 256 KB
            512 * 1024,        # 512 KB
            1 * 1024 * 1024,   # 1 MB
            4 * 1024 * 1024,   # 4 MB
            16 * 1024 * 1024,  # 16 MB
        ]
        throughputs = []

        for sz in sizes_bytes:
            def _run(s=sz):
                with tempfile.TemporaryDirectory() as tmp:
                    src = make_random_file(Path(tmp) / "src.bin", s)
                    out_dir = Path(tmp) / "shards"
                    return run_encoder(src, out_dir, data=8, par=4)

            _run()  # warmup
            times = [_run() for _ in range(3)]
            mbps = throughput_mbps(sz, statistics.mean(times))
            throughputs.append(mbps)

        # Coefficient of variation of throughput (lower = more linear)
        cv = (statistics.stdev(throughputs) / statistics.mean(throughputs) * 100
              if statistics.mean(throughputs) > 0 else 0.0)

        r = make_result(name, CATEGORY, throughputs, "MB/s",
                        {"throughput_cv_pct": round(cv, 2),
                         "interpretation": "lower CV = more linear scaling"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Shard count vs recovery time
    # ------------------------------------------------------------------

    def test_shard_count_vs_recovery_time(self) -> List[BenchResult]:
        results = []
        configs = [(4, 2, 1), (8, 4, 2), (12, 4, 3), (16, 4, 4)]
        size_bytes = BENCH_FILE_SIZE

        if not encoder_available() or not decoder_available():
            for data, par, missing in configs:
                name = f"shard_count_vs_recovery [data={data}, missing={missing}]"
                r = skipped_result(name, CATEGORY, "encoder/decoder not found", "ms")
                self.suite.add(r)
                results.append(r)
            return results

        console.print("  [dim]shard_count_vs_recovery_time...[/dim]")

        for data, par, missing in configs:
            name = f"shard_count_vs_recovery [data={data}, missing={missing}]"
            samples: List[float] = []

            def _run(d=data, p=par, m=missing):
                with tempfile.TemporaryDirectory() as tmp:
                    tmpdir = Path(tmp)
                    src = make_random_file(tmpdir / "src.bin", size_bytes)
                    shard_dir = tmpdir / "shards"
                    run_encoder(src, shard_dir, data=d, par=p)
                    shards = sorted(shard_dir.iterdir())
                    for shard in shards[:m]:
                        shard.unlink()
                    out_dir = tmpdir / "out"
                    t0 = time.perf_counter()
                    run_decoder(shard_dir, out_dir, "src.bin", data=d, par=p)
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            ms_samples = [s * 1000 for s in samples]
            r = make_result(name, CATEGORY, ms_samples, "ms",
                            {"data": data, "par": par, "missing": missing})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self) -> BenchSuite:
        console.print(f"\n[bold cyan]Running Sharding benchmarks[/bold cyan]")
        self.test_encode_throughput_by_shard_count()
        self.test_decode_throughput_vs_missing_shards()
        self.test_parallel_encode_speedup()
        self.test_shard_size_distribution()
        self.test_optimal_shard_config_finder()
        self.test_parity_ratio_throughput()
        self.test_small_file_sharding_overhead()
        self.test_memory_during_encode()
        self.test_encode_linear_scaling()
        self.test_shard_count_vs_recovery_time()
        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    suite = BenchSuite("Sharding Benchmarks")
    runner = BenchmarkSharding(suite)
    runner.run_all()
    suite.print_table()
