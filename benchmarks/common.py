"""
common.py — Shared utilities for CollectiveFS benchmarks.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, List, Optional

from cryptography.fernet import Fernet
from rich.console import Console
from rich.table import Table
from rich import box

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
ENCODER = PROJECT_ROOT / "lib" / "encoder"
DECODER = PROJECT_ROOT / "lib" / "decoder"
RESULTS_DIR = Path(__file__).parent / "results"
CHARTS_DIR = Path(__file__).parent / "charts"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

console = Console()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BenchResult:
    """Single benchmark measurement."""
    name: str
    category: str
    value: float                  # primary metric (e.g. MB/s or ms)
    unit: str                     # "MB/s", "ms", "ops/s", etc.
    samples: List[float] = field(default_factory=list)
    mean: float = 0.0
    stdev: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    metadata: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def make_result(
    name: str,
    category: str,
    samples: List[float],
    unit: str,
    metadata: Optional[dict] = None,
) -> BenchResult:
    """Build a BenchResult from a list of samples (in unit units)."""
    if not samples:
        return BenchResult(
            name=name, category=category, value=0.0, unit=unit,
            metadata=metadata or {}, skipped=True, skip_reason="no samples"
        )
    mean = statistics.mean(samples)
    stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return BenchResult(
        name=name,
        category=category,
        value=mean,
        unit=unit,
        samples=samples,
        mean=mean,
        stdev=stdev,
        min_val=min(samples),
        max_val=max(samples),
        metadata=metadata or {},
    )


def skipped_result(name: str, category: str, reason: str, unit: str = "N/A") -> BenchResult:
    return BenchResult(
        name=name, category=category, value=0.0, unit=unit,
        skipped=True, skip_reason=reason
    )


# ---------------------------------------------------------------------------
# BenchSuite
# ---------------------------------------------------------------------------

class BenchSuite:
    """Collects BenchResult objects and renders a rich report."""

    def __init__(self, name: str = "CollectiveFS Benchmarks"):
        self.name = name
        self.results: List[BenchResult] = []

    def add(self, result: BenchResult) -> None:
        self.results.append(result)

    def save_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "suite": self.name,
            "system": system_info(),
            "results": [r.to_dict() for r in self.results],
        }
        path.write_text(json.dumps(payload, indent=2))
        console.print(f"[green]Results saved to {path}[/green]")

    def print_table(self) -> None:
        table = Table(
            title=f"[bold cyan]{self.name}[/bold cyan]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Benchmark", style="cyan", no_wrap=False, min_width=38)
        table.add_column("Value", justify="right", style="bold green", min_width=10)
        table.add_column("Unit", style="yellow", min_width=6)
        table.add_column("Stdev", justify="right", style="dim", min_width=8)
        table.add_column("Min", justify="right", style="dim", min_width=8)
        table.add_column("Max", justify="right", style="dim", min_width=8)
        table.add_column("Notes", style="dim", min_width=20)

        current_category = None
        for r in self.results:
            if r.category != current_category:
                current_category = r.category
                table.add_row(
                    f"[bold white]── {r.category} ──[/bold white]",
                    "", "", "", "", "", "",
                    style="on grey11",
                )
            if r.skipped:
                table.add_row(
                    f"  {r.name}",
                    "[dim]SKIP[/dim]",
                    r.unit,
                    "–", "–", "–",
                    f"[dim]{r.skip_reason}[/dim]",
                )
            else:
                notes = _format_metadata(r.metadata)
                table.add_row(
                    f"  {r.name}",
                    f"{r.value:.3f}",
                    r.unit,
                    f"{r.stdev:.3f}",
                    f"{r.min_val:.3f}",
                    f"{r.max_val:.3f}",
                    notes,
                )

        console.print(table)


def _format_metadata(meta: dict) -> str:
    if not meta:
        return ""
    parts = []
    for k, v in list(meta.items())[:3]:
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Core benchmark helpers
# ---------------------------------------------------------------------------

def encoder_available() -> bool:
    return ENCODER.exists() and os.access(ENCODER, os.X_OK)


def decoder_available() -> bool:
    return DECODER.exists() and os.access(DECODER, os.X_OK)


def run_encoder(src: Path, out_dir: Path, data: int = 8, par: int = 4) -> float:
    """
    Invoke the Reed-Solomon encoder binary.

    Returns elapsed wall-clock seconds.
    Raises RuntimeError on non-zero exit.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ENCODER),
        f"-data={data}",
        f"-par={par}",
        f"-out={out_dir}",
        str(src),
    ]
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True)
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(
            f"encoder failed (rc={result.returncode}): {result.stderr.decode()}"
        )
    return elapsed


def run_decoder(
    shard_dir: Path,
    out_dir: Path,
    filename: str,
    data: int = 8,
    par: int = 4,
) -> float:
    """
    Invoke the Reed-Solomon decoder binary.

    shard_dir: directory containing shards named <filename>.0, .1, ...
    out_dir:   directory to write the reconstructed file into.
    filename:  base filename (e.g. "myfile.bin").

    Returns elapsed wall-clock seconds.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # Decoder needs the path to the base filename (without shard index).
    shard_base = shard_dir / filename
    out_path = out_dir / filename
    cmd = [
        str(DECODER),
        f"-data={data}",
        f"-par={par}",
        f"-out={out_path}",
        str(shard_base),
    ]
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True)
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        raise RuntimeError(
            f"decoder failed (rc={result.returncode}): {result.stderr.decode()}"
        )
    return elapsed


def make_random_file(path: Path, size_bytes: int) -> Path:
    """Write *size_bytes* of os.urandom to *path*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size_bytes))
    return path


def fernet_key() -> bytes:
    """Return a fresh Fernet key (URL-safe base64-encoded 32-byte key)."""
    return Fernet.generate_key()


def timed(fn: Callable, iterations: int = 5) -> List[float]:
    """
    Run *fn* N times, return list of elapsed seconds per call.

    Does NOT include a warmup run — callers should decide whether to
    discard the first sample themselves if they need warmup semantics.
    """
    results: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        results.append(time.perf_counter() - t0)
    return results


def timed_with_warmup(fn: Callable, iterations: int = 5) -> List[float]:
    """
    Run *fn* once (warmup, discarded), then N measured iterations.
    Returns list of elapsed seconds.
    """
    fn()  # warmup
    return timed(fn, iterations)


def throughput_mbps(size_bytes: int, elapsed_seconds: float) -> float:
    """Convert bytes and elapsed time to MB/s."""
    if elapsed_seconds <= 0:
        return 0.0
    return (size_bytes / (1024 * 1024)) / elapsed_seconds


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

def system_info() -> dict:
    import psutil
    vm = psutil.virtual_memory()
    return {
        "python_version": sys.version,
        "os": platform.platform(),
        "cpu_count_logical": os.cpu_count(),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "ram_total_gb": round(vm.total / (1024 ** 3), 2),
        "ram_available_gb": round(vm.available / (1024 ** 3), 2),
        "hostname": platform.node(),
        "machine": platform.machine(),
        "encoder_path": str(ENCODER),
        "decoder_path": str(DECODER),
    }


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------

FILE_SIZES = {
    "64KB":  64 * 1024,
    "256KB": 256 * 1024,
    "1MB":   1 * 1024 * 1024,
    "4MB":   4 * 1024 * 1024,
    "16MB":  16 * 1024 * 1024,
    "64MB":  64 * 1024 * 1024,
}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}"
        n //= 1024
    return f"{n}TB"
