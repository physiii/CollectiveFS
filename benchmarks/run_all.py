#!/usr/bin/env python3
"""
CollectiveFS Performance Benchmark Suite
=========================================

Usage:
    python -m benchmarks.run_all [OPTIONS]

Options:
    --suite io,sharding,crypto,recovery,journal,cluster
        Comma-separated list of suites to run.
        Default: io,sharding,crypto,recovery,journal   (cluster excluded by default)

    --sizes 64KB,1MB,16MB
        Comma-separated file sizes for I/O benchmarks.
        Accepted tokens: 64KB, 256KB, 1MB, 4MB, 16MB, 64MB
        Default: all sizes

    --iterations N
        Number of measured iterations per benchmark (default: 5).

    --output results/latest.json
        Where to write the JSON results.
        Default: benchmarks/results/YYYY-MM-DD_HH-MM-SS.json

    --charts
        Generate matplotlib charts after running benchmarks.

    --quick
        1 iteration, small files only (64KB, 256KB, 1MB). Fast smoke-test.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Ensure the project root is on sys.path so `python -m benchmarks.run_all` works
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from benchmarks.common import (
    BenchSuite,
    FILE_SIZES,
    RESULTS_DIR,
    encoder_available,
    decoder_available,
    system_info,
    console,
)

ALL_SUITES = ["io", "sharding", "crypto", "recovery", "journal", "cluster"]
DEFAULT_SUITES = ["io", "sharding", "crypto", "recovery", "journal"]

QUICK_SIZES = {k: v for k, v in FILE_SIZES.items() if k in ("64KB", "256KB", "1MB")}


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CollectiveFS Performance Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--suite",
        default=",".join(DEFAULT_SUITES),
        help=f"Comma-separated suites: {','.join(ALL_SUITES)}. Default: {','.join(DEFAULT_SUITES)}",
    )
    parser.add_argument(
        "--sizes",
        default=None,
        help="Comma-separated file sizes for I/O benchmarks (e.g. 64KB,1MB,16MB).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Measured iterations per benchmark (default: 5).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Default: benchmarks/results/YYYY-MM-DD_HH-MM-SS.json",
    )
    parser.add_argument(
        "--charts",
        action="store_true",
        help="Generate matplotlib charts after running benchmarks.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run 1 iteration with small files only (smoke test).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Suite runners
# ---------------------------------------------------------------------------

def run_io_suite(suite: BenchSuite, iterations: int, sizes: dict) -> None:
    from benchmarks.bench_io import BenchmarkIO
    runner = BenchmarkIO(suite, iterations=iterations)
    runner.run_all(sizes=sizes)


def run_sharding_suite(suite: BenchSuite, iterations: int) -> None:
    from benchmarks.bench_sharding import BenchmarkSharding
    runner = BenchmarkSharding(suite, iterations=iterations)
    runner.run_all()


def run_crypto_suite(suite: BenchSuite, iterations: int) -> None:
    from benchmarks.bench_crypto import BenchmarkCrypto
    runner = BenchmarkCrypto(suite, iterations=iterations)
    runner.run_all()


def run_recovery_suite(suite: BenchSuite, iterations: int) -> None:
    from benchmarks.bench_recovery import BenchmarkRecovery
    runner = BenchmarkRecovery(suite, iterations=iterations)
    runner.run_all()


def run_journal_suite(suite: BenchSuite, iterations: int) -> None:
    from benchmarks.bench_journal import BenchmarkJournal
    runner = BenchmarkJournal(suite, iterations=iterations)
    runner.run_all()


def run_cluster_suite(suite: BenchSuite, iterations: int) -> None:
    from benchmarks.bench_cluster import BenchmarkCluster
    runner = BenchmarkCluster(suite, iterations=iterations)
    runner.run_all()


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _find(results, category: str, fragment: str):
    """Return the first non-skipped BenchResult matching category and name fragment."""
    for r in results:
        if (r.category == category
                and fragment in r.name
                and not r.skipped):
            return r
    return None


def _best_mbps(results, category: str, fragment: str) -> float:
    """Return the highest mean MB/s across all results matching the fragment."""
    candidates = [
        r for r in results
        if r.category == category
        and fragment in r.name
        and not r.skipped
        and r.unit == "MB/s"
    ]
    return max((r.mean for r in candidates), default=0.0)


def print_performance_summary(suite: BenchSuite) -> dict:
    """Print a 'Performance Summary' panel and return summary dict."""
    results = suite.results

    best_read = _best_mbps(results, "I/O Pipeline", "raw_disk_read")
    best_write = _best_mbps(results, "I/O Pipeline", "raw_disk_write")
    best_encode = _best_mbps(results, "I/O Pipeline", "cfs_encode_write")
    best_pipeline = _best_mbps(results, "I/O Pipeline", "cfs_full_write_pipeline")

    # Sharding speedup: parallel_encode[workers=8] / parallel_encode[workers=1]
    sharding_single = _find(results, "Sharding", "parallel_encode [workers=1]")
    sharding_eight = _find(results, "Sharding", "parallel_encode [workers=8]")
    if sharding_single and sharding_eight and sharding_single.mean > 0:
        sharding_speedup = sharding_eight.mean / sharding_single.mean
    else:
        sharding_speedup = None

    # Encryption overhead: full_pipeline vs raw_write (at 4MB)
    raw_4mb = _find(results, "I/O Pipeline", "raw_disk_write [4MB]")
    pipe_4mb = _find(results, "I/O Pipeline", "cfs_full_write_pipeline [4MB]")
    if raw_4mb and pipe_4mb and raw_4mb.mean > 0:
        enc_overhead_pct = (pipe_4mb.mean / raw_4mb.mean - 1.0) * 100.0
    else:
        enc_overhead_pct = None

    # NB: pipeline is slower → throughput ratio < 1 → negative overhead makes no sense here,
    # so we invert: overhead = (raw / pipeline - 1) * 100
    # Actually: overhead means pipeline is X% slower, so:
    # raw speed is higher → overhead = (raw_speed / pipeline_speed - 1) * 100
    if raw_4mb and pipe_4mb and pipe_4mb.mean > 0:
        enc_overhead_pct = (raw_4mb.mean / pipe_4mb.mean - 1.0) * 100.0
    else:
        enc_overhead_pct = None

    # Recovery time at boundary (4 missing shards)
    recovery_4 = _find(results, "Recovery", "recovery_time [missing=4")
    recovery_time_ms = recovery_4.mean if recovery_4 else None

    # WAL append rate
    wal_buffered = _find(results, "Journal", "wal_append_buffered_ops_per_sec")
    wal_fsync = _find(results, "Journal", "wal_append_ops_per_sec")
    wal_rate = wal_buffered.mean if wal_buffered else (wal_fsync.mean if wal_fsync else None)

    # Determine bottleneck
    bottleneck = _identify_bottleneck(
        best_read, best_write, best_pipeline, enc_overhead_pct, recovery_time_ms
    )

    # Build summary
    summary = {
        "best_read_mbps": round(best_read, 2),
        "best_write_mbps": round(best_write, 2),
        "best_encode_mbps": round(best_encode, 2),
        "best_pipeline_mbps": round(best_pipeline, 2),
        "sharding_parallel_speedup": round(sharding_speedup, 2) if sharding_speedup else None,
        "enc_overhead_vs_raw_pct": round(enc_overhead_pct, 1) if enc_overhead_pct is not None else None,
        "recovery_4_missing_ms": round(recovery_time_ms, 2) if recovery_time_ms else None,
        "wal_rate_ops_per_sec": round(wal_rate, 0) if wal_rate else None,
        "bottleneck": bottleneck,
    }

    # Rich panel
    lines = [
        f"[bold cyan]Best raw read throughput:[/bold cyan]    {best_read:.1f} MB/s",
        f"[bold cyan]Best raw write throughput:[/bold cyan]   {best_write:.1f} MB/s",
        f"[bold cyan]Best encode throughput:[/bold cyan]      {best_encode:.1f} MB/s",
        f"[bold cyan]Full pipeline throughput:[/bold cyan]    {best_pipeline:.1f} MB/s",
    ]
    if sharding_speedup is not None:
        lines.append(
            f"[bold cyan]Parallel sharding speedup:[/bold cyan]  {sharding_speedup:.2f}×  (1→8 workers)"
        )
    if enc_overhead_pct is not None:
        lines.append(
            f"[bold cyan]Encryption overhead:[/bold cyan]        {enc_overhead_pct:.1f}%  slower vs raw"
        )
    if recovery_time_ms is not None:
        lines.append(
            f"[bold cyan]Recovery time (4 missing):[/bold cyan]  {recovery_time_ms:.1f} ms"
        )
    if wal_rate is not None:
        lines.append(
            f"[bold cyan]WAL append rate (buffered):[/bold cyan] {wal_rate:,.0f} ops/s"
        )

    lines.append("")
    lines.append(f"[bold yellow]System bottleneck:[/bold yellow] {bottleneck}")

    console.print(Panel(
        "\n".join(lines),
        title="[bold white]Performance Summary[/bold white]",
        border_style="blue",
        expand=False,
    ))

    return summary


def _identify_bottleneck(
    read_mbps: float,
    write_mbps: float,
    pipeline_mbps: float,
    enc_overhead: Optional[float],
    recovery_ms: Optional[float],
) -> str:
    """
    Heuristic determination of the primary system bottleneck.
    """
    candidates = []

    if pipeline_mbps > 0 and write_mbps > 0:
        pipeline_fraction = pipeline_mbps / write_mbps
        if pipeline_fraction < 0.3:
            candidates.append(("Reed-Solomon encoding (encode throughput << raw write)", pipeline_fraction))
        elif enc_overhead is not None and enc_overhead > 50:
            candidates.append(("Fernet encryption overhead (>50% slower than raw)", enc_overhead))
        elif pipeline_fraction < 0.6:
            candidates.append(("Encoder subprocess launch overhead", pipeline_fraction))

    if recovery_ms is not None and recovery_ms > 500:
        candidates.append(("Recovery I/O (>500ms to reconstruct 4MB from 4 missing shards)", recovery_ms))

    if not candidates:
        if write_mbps > 0 and pipeline_mbps > 0:
            pct_overhead = (write_mbps / pipeline_mbps - 1.0) * 100 if pipeline_mbps > 0 else 0
            if pct_overhead > 200:
                return "RS encoder subprocess (large fixed launch overhead dominates small files)"
            elif pct_overhead > 50:
                return "Fernet encryption + encoder combined (50–200% overhead vs raw disk)"
            else:
                return "Disk I/O (pipeline is within 50% of raw disk speed — well optimised)"
        return "Insufficient data to identify bottleneck"

    candidates.sort(key=lambda x: x[1])
    return candidates[-1][0]


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # Determine which suites to run
    requested_suites = [s.strip().lower() for s in args.suite.split(",")]
    invalid = [s for s in requested_suites if s not in ALL_SUITES]
    if invalid:
        console.print(f"[red]Unknown suites: {invalid}. Valid: {ALL_SUITES}[/red]")
        return 1

    # Determine file sizes
    if args.quick:
        iterations = 1
        sizes = QUICK_SIZES
        console.print("[yellow]Quick mode: 1 iteration, small files only.[/yellow]")
    else:
        iterations = args.iterations
        if args.sizes:
            size_tokens = [s.strip() for s in args.sizes.split(",")]
            sizes = {k: v for k, v in FILE_SIZES.items() if k in size_tokens}
            if not sizes:
                console.print(f"[red]No valid sizes in --sizes {args.sizes}[/red]")
                return 1
        else:
            sizes = FILE_SIZES

    # Output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = RESULTS_DIR / f"{ts}.json"

    # Print header
    console.print(Rule("[bold cyan]CollectiveFS Benchmark Suite[/bold cyan]"))
    console.print(f"[dim]Suites  : {', '.join(requested_suites)}[/dim]")
    console.print(f"[dim]Sizes   : {', '.join(sizes.keys())}[/dim]")
    console.print(f"[dim]Iters   : {iterations}[/dim]")
    console.print(f"[dim]Output  : {output_path}[/dim]")
    sinfo = system_info()
    console.print(
        f"[dim]System  : {sinfo['os']} | "
        f"{sinfo['cpu_count_logical']} logical CPUs | "
        f"{sinfo['ram_total_gb']} GB RAM | "
        f"Python {sinfo['python_version'].split()[0]}[/dim]"
    )
    if not encoder_available():
        console.print("[yellow]Warning: encoder binary not found at lib/encoder — encoder tests will be skipped.[/yellow]")
    if not decoder_available():
        console.print("[yellow]Warning: decoder binary not found at lib/decoder — decoder tests will be skipped.[/yellow]")

    # Create a single master suite
    suite = BenchSuite("CollectiveFS Benchmark Suite")

    wall_start = time.perf_counter()

    # Run requested suites
    suite_dispatch = {
        "io":        lambda: run_io_suite(suite, iterations, sizes),
        "sharding":  lambda: run_sharding_suite(suite, iterations),
        "crypto":    lambda: run_crypto_suite(suite, iterations),
        "recovery":  lambda: run_recovery_suite(suite, iterations),
        "journal":   lambda: run_journal_suite(suite, iterations),
        "cluster":   lambda: run_cluster_suite(suite, iterations),
    }

    for suite_name in requested_suites:
        try:
            suite_dispatch[suite_name]()
        except Exception as exc:
            console.print(f"[red]Suite '{suite_name}' crashed: {exc}[/red]")
            import traceback
            traceback.print_exc()

    wall_elapsed = time.perf_counter() - wall_start

    # Print full results table
    console.print(Rule("[bold white]Full Results[/bold white]"))
    suite.print_table()

    # Performance summary
    console.print()
    summary = print_performance_summary(suite)

    # Save JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    payload = {
        "suite": suite.name,
        "wall_time_seconds": round(wall_elapsed, 2),
        "system": sinfo,
        "config": {
            "suites": requested_suites,
            "iterations": iterations,
            "sizes": list(sizes.keys()),
        },
        "performance_summary": summary,
        "results": [r.to_dict() for r in suite.results],
    }
    output_path.write_text(json.dumps(payload, indent=2))
    console.print(f"\n[green]Results saved → {output_path}[/green]")
    console.print(f"[dim]Total benchmark time: {wall_elapsed:.1f}s[/dim]")

    # Generate charts
    if args.charts:
        from benchmarks.charts import generate_all_charts
        generate_all_charts([r.to_dict() for r in suite.results])

    # Final verdict
    console.print()
    console.print(Rule("[bold cyan]Benchmark Complete[/bold cyan]"))
    bottleneck = summary.get("bottleneck", "unknown")
    console.print(Panel(
        f"[bold white]Primary bottleneck:[/bold white]\n  {bottleneck}\n\n"
        f"[dim]Full JSON results: {output_path.resolve()}[/dim]",
        title="[bold yellow]Verdict[/bold yellow]",
        border_style="yellow",
        expand=False,
    ))

    return 0


if __name__ == "__main__":
    sys.exit(main())
