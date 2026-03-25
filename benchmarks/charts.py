"""
charts.py — Chart generation for CollectiveFS benchmark results.

All charts use a dark background with a blue/purple colour palette.
Called by run_all.py with --charts flag, or directly as a module.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from benchmarks.common import CHARTS_DIR, console

# Lazy import matplotlib so the rest of the benchmarks work even if
# matplotlib is not installed (unlikely, but defensive).
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _MATPLOTLIB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Palette & style
# ---------------------------------------------------------------------------

COLORS = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EF4444"]
GRID_COLOR = "#374151"
TEXT_COLOR = "#F9FAFB"
AXIS_COLOR = "#6B7280"
BG_COLOR = "#111827"
PANEL_COLOR = "#1F2937"
BRAND = "CollectiveFS"


def _setup_style() -> None:
    plt.style.use("dark_background")
    plt.rcParams.update({
        "figure.facecolor": BG_COLOR,
        "axes.facecolor": PANEL_COLOR,
        "axes.edgecolor": AXIS_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "text.color": TEXT_COLOR,
        "grid.color": GRID_COLOR,
        "grid.linestyle": "--",
        "grid.alpha": 0.5,
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "legend.facecolor": PANEL_COLOR,
        "legend.edgecolor": AXIS_COLOR,
    })


def _save(fig: "plt.Figure", filename: str) -> Path:
    out = CHARTS_DIR / filename
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    console.print(f"  [green]Saved chart:[/green] {out}")
    return out


def _brand_stamp(ax: "plt.Axes") -> None:
    ax.annotate(
        f"© {BRAND}",
        xy=(1.0, -0.12),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=8,
        color=AXIS_COLOR,
    )


# ---------------------------------------------------------------------------
# Public helpers: accept pre-extracted data or raw JSON path
# ---------------------------------------------------------------------------

def _extract(results: List[dict], category: str, name_fragment: str) -> List[dict]:
    return [
        r for r in results
        if r.get("category") == category
        and name_fragment in r.get("name", "")
        and not r.get("skipped", False)
    ]


# ---------------------------------------------------------------------------
# 1. Throughput by file size
# ---------------------------------------------------------------------------

def chart_throughput_by_filesize(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    """
    Line chart: raw_disk_write vs cfs_encode_write vs cfs_full_write_pipeline
    x = file size label, y = MB/s.
    """
    if not _MATPLOTLIB_AVAILABLE:
        console.print("[yellow]  matplotlib not available — skipping chart[/yellow]")
        return CHARTS_DIR / "throughput_by_filesize.png"

    _setup_style()

    series = {
        "raw_disk_write":          ("raw_disk_write",          COLORS[0], "o"),
        "encode_only":             ("cfs_encode_write",        COLORS[1], "s"),
        "encode+encrypt (write)":  ("cfs_full_write_pipeline", COLORS[2], "^"),
        "full_roundtrip":          ("cfs_full_roundtrip",       COLORS[3], "D"),
    }

    size_order = ["64KB", "256KB", "1MB", "4MB", "16MB", "64MB"]
    fig, ax = plt.subplots(figsize=(10, 6))

    for label, (fragment, color, marker) in series.items():
        xs, ys = [], []
        for size_label in size_order:
            matches = [
                r for r in results
                if r.get("category") == "I/O Pipeline"
                and fragment in r.get("name", "")
                and f"[{size_label}]" in r.get("name", "")
                and not r.get("skipped", False)
            ]
            if matches:
                xs.append(size_label)
                ys.append(matches[0]["mean"])
        if xs:
            ax.plot(xs, ys, label=label, color=color, marker=marker,
                    linewidth=2, markersize=7)

    ax.set_title("Pipeline Throughput by File Size")
    ax.set_xlabel("File Size")
    ax.set_ylabel("Throughput (MB/s)")
    ax.legend(loc="best")
    ax.grid(True)
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "throughput_by_filesize.png")


# ---------------------------------------------------------------------------
# 2. Sharding throughput (bar chart)
# ---------------------------------------------------------------------------

def chart_sharding_throughput(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "sharding_throughput.png"

    _setup_style()

    matches = [
        r for r in results
        if r.get("category") == "Sharding"
        and "encode_throughput [data=" in r.get("name", "")
        and not r.get("skipped", False)
    ]

    if not matches:
        console.print("[yellow]  No sharding throughput data — skipping chart[/yellow]")
        return CHARTS_DIR / "sharding_throughput.png"

    labels = [r["name"].split("[")[-1].rstrip("]") for r in matches]
    values = [r["mean"] for r in matches]
    errors = [r["stdev"] for r in matches]

    fig, ax = plt.subplots(figsize=(11, 6))
    bar_colors = [COLORS[i % len(COLORS)] for i in range(len(labels))]
    bars = ax.bar(labels, values, color=bar_colors, yerr=errors,
                  capsize=4, error_kw={"ecolor": TEXT_COLOR, "alpha": 0.7})

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height() + 0.5,
                f"{val:.1f}", ha="center", va="bottom", fontsize=9, color=TEXT_COLOR)

    ax.set_title("Encode Throughput by Shard Configuration (4 MB file)")
    ax.set_xlabel("Shard Config (data+par)")
    ax.set_ylabel("Throughput (MB/s)")
    ax.grid(True, axis="y")
    _brand_stamp(ax)
    fig.tight_layout()
    plt.xticks(rotation=30, ha="right")

    return _save(fig, "sharding_throughput.png")


# ---------------------------------------------------------------------------
# 3. Recovery time vs missing shards
# ---------------------------------------------------------------------------

def chart_recovery_time(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "recovery_time.png"

    _setup_style()

    missing_labels = ["0", "1", "2", "3", "4"]
    values, errors = [], []
    for m in missing_labels:
        fragment = f"[missing={m}" if m != "4" else "[missing=4"
        matches = [
            r for r in results
            if r.get("category") == "Recovery"
            and "recovery_time" in r.get("name", "")
            and fragment in r.get("name", "")
            and not r.get("skipped", False)
        ]
        if matches:
            values.append(matches[0]["mean"])
            errors.append(matches[0]["stdev"])
        else:
            values.append(0.0)
            errors.append(0.0)

    fig, ax = plt.subplots(figsize=(9, 6))
    bar_colors = [COLORS[0]] + [COLORS[1]] * 3 + [COLORS[4]]
    bars = ax.bar(
        [f"{m} missing" for m in missing_labels],
        values,
        color=bar_colors,
        yerr=errors,
        capsize=4,
        error_kw={"ecolor": TEXT_COLOR, "alpha": 0.7},
    )

    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height() + 0.3,
                    f"{val:.1f}ms", ha="center", va="bottom", fontsize=9)

    # Annotate tolerance boundary
    ax.axvline(x=3.5, color=COLORS[4], linestyle="--", linewidth=1.5, alpha=0.7)
    ax.text(3.6, max(v for v in values if v > 0) * 0.8,
            "Tolerance boundary", color=COLORS[4], fontsize=9, va="center")

    ax.set_title("Recovery Time vs. Missing Shards (8+4 config, 4 MB)")
    ax.set_xlabel("Missing Shards")
    ax.set_ylabel("Recovery Time (ms)")
    ax.grid(True, axis="y")
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "recovery_time.png")


# ---------------------------------------------------------------------------
# 4. Crypto comparison (Fernet vs AES-GCM)
# ---------------------------------------------------------------------------

def chart_crypto_comparison(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "crypto_comparison.png"

    _setup_style()

    ops = ["encrypt", "decrypt"]
    algos = ["Fernet", "AES-256-GCM"]
    algo_fragments = ["fernet", "aesgcm"]

    fig, ax = plt.subplots(figsize=(9, 6))

    x = np.arange(len(ops))
    width = 0.35
    bar_groups = []

    for idx, (algo, frag) in enumerate(zip(algos, algo_fragments)):
        vals = []
        for op in ops:
            matches = [
                r for r in results
                if r.get("category") == "Crypto"
                and "fernet_vs_aesgcm" in r.get("name", "")
                and frag in r.get("name", "")
                and op in r.get("name", "")
                and not r.get("skipped", False)
            ]
            vals.append(matches[0]["mean"] if matches else 0.0)

        offset = (idx - 0.5) * width
        bars = ax.bar(x + offset, vals, width, label=algo, color=COLORS[idx])
        bar_groups.append(bars)

        for bar, val in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height() + 0.5,
                    f"{val:.0f}", ha="center", va="bottom", fontsize=9)

    ax.set_title("Fernet vs AES-256-GCM Throughput (1 MB)")
    ax.set_xticks(x)
    ax.set_xticklabels(ops)
    ax.set_ylabel("Throughput (MB/s)")
    ax.legend()
    ax.grid(True, axis="y")
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "crypto_comparison.png")


# ---------------------------------------------------------------------------
# 5. Parallel encryption speedup
# ---------------------------------------------------------------------------

def chart_parallel_speedup(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "parallel_speedup.png"

    _setup_style()

    matches = [
        r for r in results
        if r.get("category") == "Crypto"
        and "parallel_encrypt_speedup" in r.get("name", "")
        and not r.get("skipped", False)
    ]

    if not matches:
        console.print("[yellow]  No parallel speedup data — skipping chart[/yellow]")
        return CHARTS_DIR / "parallel_speedup.png"

    # Extract thread counts and throughputs
    thread_vals = []
    for r in matches:
        meta = r.get("metadata", {})
        threads = meta.get("threads", 1)
        mean = r["mean"]
        thread_vals.append((threads, mean))

    thread_vals.sort(key=lambda x: x[0])
    threads = [t for t, _ in thread_vals]
    mbps = [m for _, m in thread_vals]

    # Ideal linear speedup baseline
    base = mbps[0] if mbps else 1.0
    ideal = [base * t / threads[0] for t in threads]

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.plot(threads, mbps, color=COLORS[0], marker="o", linewidth=2,
            markersize=8, label="Actual")
    ax.plot(threads, ideal, color=COLORS[2], linestyle="--", linewidth=1.5,
            label="Ideal linear", alpha=0.7)

    ax.fill_between(threads, mbps, ideal, alpha=0.1, color=COLORS[0])

    ax.set_title("Parallel Encryption Speedup (12 × 512 KB shards)")
    ax.set_xlabel("Thread Count")
    ax.set_ylabel("Throughput (MB/s)")
    ax.set_xticks(threads)
    ax.legend()
    ax.grid(True)
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "parallel_speedup.png")


# ---------------------------------------------------------------------------
# 6. WAL latency distribution (histogram)
# ---------------------------------------------------------------------------

def chart_wal_latency_distribution(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "wal_latency_distribution.png"

    _setup_style()

    matches = [
        r for r in results
        if r.get("category") == "Journal"
        and "metadata_write_latency" in r.get("name", "")
        and not r.get("skipped", False)
    ]

    if not matches:
        console.print("[yellow]  No WAL latency distribution data — skipping chart[/yellow]")
        return CHARTS_DIR / "wal_latency_distribution.png"

    raw = matches[0]
    samples = raw.get("samples", [])
    if not samples:
        return CHARTS_DIR / "wal_latency_distribution.png"

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(samples, bins=40, color=COLORS[1], edgecolor=BG_COLOR, alpha=0.85)

    meta = raw.get("metadata", {})
    p50 = meta.get("p50_us", 0.0)
    p99 = meta.get("p99_us", 0.0)

    ax.axvline(p50, color=COLORS[2], linestyle="--", linewidth=2, label=f"p50 = {p50:.1f} µs")
    ax.axvline(p99, color=COLORS[4], linestyle="--", linewidth=2, label=f"p99 = {p99:.1f} µs")

    ax.set_title("WAL Metadata Write Latency Distribution (buffered)")
    ax.set_xlabel("Latency (µs)")
    ax.set_ylabel("Count")
    ax.legend()
    ax.grid(True, axis="y")
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "wal_latency_distribution.png")


# ---------------------------------------------------------------------------
# 7. Cluster timeline (if cluster data available)
# ---------------------------------------------------------------------------

def chart_cluster_timeline(results: List[dict], out_dir: Optional[Path] = None) -> Path:
    if not _MATPLOTLIB_AVAILABLE:
        return CHARTS_DIR / "cluster_timeline.png"

    _setup_style()

    cluster_results = [
        r for r in results
        if r.get("category") == "Cluster"
        and not r.get("skipped", False)
    ]

    if not cluster_results:
        console.print("[yellow]  No cluster data — skipping cluster_timeline chart[/yellow]")
        return CHARTS_DIR / "cluster_timeline.png"

    labels = [r["name"] for r in cluster_results]
    values = [r["mean"] for r in cluster_results]
    units = [r["unit"] for r in cluster_results]
    errors = [r["stdev"] for r in cluster_results]

    fig, ax = plt.subplots(figsize=(12, max(5, len(labels) * 0.5 + 2)))
    y_pos = range(len(labels))
    bars = ax.barh(list(y_pos), values, xerr=errors,
                   color=[COLORS[i % len(COLORS)] for i in range(len(labels))],
                   capsize=4, error_kw={"ecolor": TEXT_COLOR, "alpha": 0.7})

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels([l[:50] for l in labels], fontsize=9)
    ax.set_xlabel("Value")
    ax.set_title("Cluster Benchmark Results")
    ax.grid(True, axis="x")
    _brand_stamp(ax)
    fig.tight_layout()

    return _save(fig, "cluster_timeline.png")


# ---------------------------------------------------------------------------
# Master chart generator
# ---------------------------------------------------------------------------

def generate_all_charts(results: List[dict]) -> List[Path]:
    """
    Generate all charts from a list of BenchResult dicts.
    Returns list of generated file paths.
    """
    if not _MATPLOTLIB_AVAILABLE:
        console.print("[red]  matplotlib/numpy not installed — cannot generate charts[/red]")
        return []

    console.print("\n[bold cyan]Generating charts...[/bold cyan]")
    generated = []

    for fn in [
        chart_throughput_by_filesize,
        chart_sharding_throughput,
        chart_recovery_time,
        chart_crypto_comparison,
        chart_parallel_speedup,
        chart_wal_latency_distribution,
        chart_cluster_timeline,
    ]:
        try:
            path = fn(results)
            generated.append(path)
        except Exception as exc:
            console.print(f"[red]  Chart {fn.__name__} failed: {exc}[/red]")

    return generated


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        console.print("[red]Usage: python -m benchmarks.charts <results.json>[/red]")
        sys.exit(1)

    results_path = Path(sys.argv[1])
    payload = json.loads(results_path.read_text())
    results_data = payload.get("results", payload)

    generated = generate_all_charts(results_data)
    console.print(f"\n[green]Generated {len(generated)} charts in {CHARTS_DIR}[/green]")
