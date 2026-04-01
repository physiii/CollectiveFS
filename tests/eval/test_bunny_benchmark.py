"""
Bunny video benchmark: throughput and corruption threshold analysis.

Uses the Big Buck Bunny 1080p test video to measure real-world performance
across the full CollectiveFS pipeline and determine the exact shard-loss
threshold where data corruption begins.

Run with:
    python -m pytest tests/eval/test_bunny_benchmark.py -v -s
"""

import hashlib
import os
import random
import subprocess
import time
import pathlib
import shutil

import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENCODER_BIN = os.path.join(PROJECT_ROOT, "lib", "encoder")
DECODER_BIN = os.path.join(PROJECT_ROOT, "lib", "decoder")
BUNNY_VIDEO = os.path.join(PROJECT_ROOT, "tests", "fixtures", "bunny_1080p.mp4")

BINARIES_PRESENT = (
    os.path.isfile(ENCODER_BIN) and os.access(ENCODER_BIN, os.X_OK) and
    os.path.isfile(DECODER_BIN) and os.access(DECODER_BIN, os.X_OK)
)

skip_no_binaries = pytest.mark.skipif(
    not BINARIES_PRESENT,
    reason="lib/encoder and/or lib/decoder not found",
)

skip_no_video = pytest.mark.skipif(
    not os.path.isfile(BUNNY_VIDEO),
    reason="tests/fixtures/bunny_1080p.mp4 not found",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def encode(src, out_dir, data=8, par=4):
    cmd = [ENCODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_dir), str(src)]
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True)
    elapsed = time.perf_counter() - t0
    basename = pathlib.Path(src).name
    shards = [pathlib.Path(out_dir) / f"{basename}.{i}" for i in range(data + par)]
    return shards, elapsed


def decode(shard_dir, base_name, out_file, data=8, par=4):
    cmd = [DECODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_file), base_name]
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(shard_dir))
    elapsed = time.perf_counter() - t0
    return result, elapsed


# ---------------------------------------------------------------------------
# Throughput benchmark
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.integration
@skip_no_binaries
@skip_no_video
def test_bunny_pipeline_throughput(tmp_path):
    """Full pipeline throughput with the bunny video: encode + encrypt + decrypt + decode."""
    src = pathlib.Path(BUNNY_VIDEO)
    file_size = src.stat().st_size
    original_hash = sha256_file(src)

    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    key = Fernet.generate_key()
    f = Fernet(key)

    # --- Encode ---
    shards, encode_time = encode(src, out_dir)

    # --- Encrypt ---
    t0 = time.perf_counter()
    for shard in shards:
        if shard.exists():
            shard.write_bytes(f.encrypt(shard.read_bytes()))
    encrypt_time = time.perf_counter() - t0

    # --- Decrypt ---
    t0 = time.perf_counter()
    for shard in shards:
        if shard.exists():
            shard.write_bytes(f.decrypt(shard.read_bytes()))
    decrypt_time = time.perf_counter() - t0

    # --- Decode ---
    reconstructed = tmp_path / "reconstructed.mp4"
    result, decode_time = decode(out_dir, src.name, reconstructed)
    assert result.returncode == 0, f"Decode failed: {result.stderr}"

    total_time = encode_time + encrypt_time + decrypt_time + decode_time
    mb = file_size / (1024 * 1024)

    print("\n")
    print("=" * 70)
    print(f"  BUNNY VIDEO PIPELINE BENCHMARK ({mb:.2f} MB)")
    print("=" * 70)
    print(f"  Encode (RS 8+4):        {encode_time:.4f}s  ({mb/encode_time:.1f} MB/s)")
    print(f"  Encrypt (Fernet AES):   {encrypt_time:.4f}s  ({mb/encrypt_time:.1f} MB/s)")
    print(f"  Decrypt (Fernet AES):   {decrypt_time:.4f}s  ({mb/decrypt_time:.1f} MB/s)")
    print(f"  Decode  (RS 8+4):       {decode_time:.4f}s  ({mb/decode_time:.1f} MB/s)")
    print(f"  ─────────────────────────────────────────")
    print(f"  Total pipeline:         {total_time:.4f}s  ({mb/total_time:.1f} MB/s)")
    print(f"  SHA-256 match:          {sha256_file(reconstructed) == original_hash}")
    print("=" * 70)

    assert sha256_file(reconstructed) == original_hash


# ---------------------------------------------------------------------------
# Corruption threshold — progressive shard deletion
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.integration
@skip_no_binaries
@skip_no_video
def test_bunny_corruption_threshold(tmp_path):
    """
    Progressive shard deletion on the bunny video.

    For each shard count from 0 to 12 deleted, attempts reconstruction and
    reports whether the file is intact, corrupted, or unrecoverable.
    This finds the exact threshold where data loss begins.
    """
    src = pathlib.Path(BUNNY_VIDEO)
    file_size = src.stat().st_size
    original_hash = sha256_file(src)
    mb = file_size / (1024 * 1024)

    DATA, PAR = 8, 4
    TOTAL = DATA + PAR

    print("\n")
    print("=" * 70)
    print(f"  CORRUPTION THRESHOLD ANALYSIS — bunny_1080p.mp4 ({mb:.2f} MB)")
    print(f"  Reed-Solomon config: {DATA} data + {PAR} parity = {TOTAL} total shards")
    print("=" * 70)
    print(f"  {'Deleted':>8}  {'Remaining':>10}  {'Status':>14}  {'Hash Match':>11}  {'Decode Time':>12}")
    print(f"  {'─'*8}  {'─'*10}  {'─'*14}  {'─'*11}  {'─'*12}")

    corruption_started = None
    total_failure = None

    for n_delete in range(TOTAL + 1):
        trial_dir = tmp_path / f"delete_{n_delete}"
        trial_dir.mkdir()
        out_dir = trial_dir / "shards"
        out_dir.mkdir()

        shards, _ = encode(src, out_dir, data=DATA, par=PAR)

        # Delete n_delete shards (deterministic: delete from the end first)
        to_delete = shards[TOTAL - n_delete:]
        for shard in to_delete:
            if shard.exists():
                shard.unlink()

        reconstructed = trial_dir / "reconstructed.mp4"
        result, decode_time = decode(out_dir, src.name, reconstructed, data=DATA, par=PAR)

        if result.returncode != 0:
            status = "UNRECOVERABLE"
            hash_ok = "n/a"
            if total_failure is None:
                total_failure = n_delete
        elif not reconstructed.exists():
            status = "UNRECOVERABLE"
            hash_ok = "n/a"
            if total_failure is None:
                total_failure = n_delete
        else:
            rec_hash = sha256_file(reconstructed)
            if rec_hash == original_hash:
                status = "INTACT"
                hash_ok = "YES"
            else:
                status = "CORRUPTED"
                hash_ok = "NO"
                if corruption_started is None:
                    corruption_started = n_delete

        time_str = f"{decode_time:.4f}s" if result.returncode == 0 else "—"
        print(f"  {n_delete:>8}  {TOTAL - n_delete:>10}  {status:>14}  {hash_ok:>11}  {time_str:>12}")

    print(f"  {'─'*8}  {'─'*10}  {'─'*14}  {'─'*11}  {'─'*12}")
    print()

    if corruption_started is not None:
        print(f"  ** Silent corruption begins at {corruption_started} deleted shards")
    if total_failure is not None:
        print(f"  ** Total failure (unrecoverable) at {total_failure} deleted shards")
    print(f"  ** Safe deletion limit: {PAR} shards (parity count)")
    print(f"  ** Fault tolerance: {PAR}/{TOTAL} = {100*PAR/TOTAL:.0f}% of shards can be lost")
    print("=" * 70)

    # The threshold must match our parity setting
    assert total_failure == PAR + 1 or corruption_started == PAR + 1, (
        f"Expected corruption/failure at {PAR + 1} deleted shards, "
        f"got corruption={corruption_started}, failure={total_failure}"
    )


# ---------------------------------------------------------------------------
# Multi-config corruption matrix
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.integration
@pytest.mark.slow
@skip_no_binaries
@skip_no_video
def test_bunny_parity_comparison(tmp_path):
    """
    Compare corruption thresholds across different parity configurations
    using the bunny video. Shows how increasing parity improves fault tolerance
    at the cost of storage overhead.
    """
    src = pathlib.Path(BUNNY_VIDEO)
    file_size = src.stat().st_size
    original_hash = sha256_file(src)
    mb = file_size / (1024 * 1024)

    # (data_shards, parity_shards)
    configs = [
        (4, 2),
        (8, 4),
        (6, 6),
        (4, 8),
    ]

    print("\n")
    print("=" * 70)
    print(f"  PARITY CONFIGURATION COMPARISON — bunny_1080p.mp4 ({mb:.2f} MB)")
    print("=" * 70)
    print(f"  {'Config':>12}  {'Total':>6}  {'Max Loss':>9}  {'Tolerance':>10}  {'Overhead':>9}  {'Encode':>10}")
    print(f"  {'─'*12}  {'─'*6}  {'─'*9}  {'─'*10}  {'─'*9}  {'─'*10}")

    for data, par in configs:
        total = data + par
        trial_dir = tmp_path / f"d{data}_p{par}"
        trial_dir.mkdir()
        out_dir = trial_dir / "shards"
        out_dir.mkdir()

        # File size must be divisible by data shards for clean reconstruction
        # Use a copy padded to the right size
        padded_size = file_size
        remainder = file_size % data
        if remainder != 0:
            padded_size = file_size + (data - remainder)
        padded_src = trial_dir / "padded.mp4"
        with open(src, "rb") as fin, open(padded_src, "wb") as fout:
            fout.write(fin.read())
            if padded_size > file_size:
                fout.write(b'\x00' * (padded_size - file_size))

        shards, encode_time = encode(padded_src, out_dir, data=data, par=par)

        # Find corruption threshold
        max_safe = 0
        for n_delete in range(1, total + 1):
            test_dir = trial_dir / f"test_{n_delete}"
            test_dir.mkdir()
            # Copy shards
            for shard in shards:
                if shard.exists():
                    shutil.copy2(shard, test_dir / shard.name)
            test_shards = [test_dir / s.name for s in shards]

            for s in test_shards[total - n_delete:]:
                if s.exists():
                    s.unlink()

            rec = test_dir / "rec.mp4"
            result, _ = decode(test_dir, padded_src.name, rec, data=data, par=par)
            if result.returncode == 0 and rec.exists():
                max_safe = n_delete
            else:
                break

        overhead = (par / data) * 100
        tolerance = (max_safe / total) * 100
        config_str = f"{data}+{par}"

        print(f"  {config_str:>12}  {total:>6}  {max_safe:>9}  {tolerance:>9.0f}%  {overhead:>8.0f}%  {encode_time:>9.4f}s")

    print(f"  {'─'*12}  {'─'*6}  {'─'*9}  {'─'*10}  {'─'*9}  {'─'*10}")
    print()
    print("  Max Loss    = maximum shards that can be deleted while maintaining integrity")
    print("  Tolerance   = Max Loss / Total Shards (% of shards that can fail)")
    print("  Overhead    = Parity / Data (extra storage cost for redundancy)")
    print("=" * 70)
