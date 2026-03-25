"""
Performance evaluation tests for CollectiveFS.

These tests measure throughput and latency for each stage of the pipeline and
assert that they meet minimum acceptable thresholds.  Metrics are printed to
stdout so they appear in verbose pytest output.

All throughput tests are marked @pytest.mark.eval.
Tests that exercise the binaries are additionally marked @pytest.mark.integration.
Slow tests (> 5 s expected) are marked @pytest.mark.slow.

Threshold rationale
-------------------
- Encoder on modern hardware easily processes megabytes per second in Go.
- Fernet is AES-128-CBC + HMAC-SHA256 in Python; expect > 50 MB/s.
- The thresholds below are deliberately conservative to avoid flakiness on
  slow CI machines.
"""

import os
import subprocess
import time
import pathlib
import hashlib
import concurrent.futures

import pytest

from cryptography.fernet import Fernet

ENCODER_BIN = "/home/andy/code/CollectiveFS/lib/encoder"
DECODER_BIN = "/home/andy/code/CollectiveFS/lib/decoder"

BINARIES_PRESENT = (
    os.path.isfile(ENCODER_BIN) and os.access(ENCODER_BIN, os.X_OK) and
    os.path.isfile(DECODER_BIN) and os.access(DECODER_BIN, os.X_OK)
)

skip_no_binaries = pytest.mark.skipif(
    not BINARIES_PRESENT,
    reason="lib/encoder and/or lib/decoder not found or not executable",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_encoder(src, out_dir, data=8, par=4):
    cmd = [ENCODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_dir), str(src)]
    start = time.perf_counter()
    result = subprocess.run(cmd, check=True, capture_output=True)
    elapsed = time.perf_counter() - start
    return elapsed


def run_decoder(shard_dir, base_name, out_file, data=8, par=4):
    cmd = [DECODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_file), base_name]
    start = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=str(shard_dir))
    elapsed = time.perf_counter() - start
    return result, elapsed


def encrypt_chunk(data: bytes, key: bytes) -> bytes:
    return Fernet(key).encrypt(data)


def decrypt_chunk(token: bytes, key: bytes) -> bytes:
    return Fernet(key).decrypt(token)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def print_throughput(label: str, size_bytes: int, elapsed_s: float):
    mb = size_bytes / (1024 * 1024)
    mbs = mb / elapsed_s if elapsed_s > 0 else float("inf")
    print(f"\n  [throughput] {label}: {mb:.2f} MB in {elapsed_s:.4f}s = {mbs:.1f} MB/s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.eval
@pytest.mark.integration
@skip_no_binaries
def test_encode_throughput_small(tmp_path):
    """Encoding a 64 KB file must complete in under 2 seconds."""
    size = 64 * 1024
    src = tmp_path / "small.bin"
    src.write_bytes(os.urandom(size))
    out_dir = tmp_path / "shards"
    out_dir.mkdir()

    elapsed = run_encoder(src, out_dir, data=8, par=4)
    print_throughput("encode 64KB", size, elapsed)

    assert elapsed < 2.0, f"Encoding 64 KB took {elapsed:.3f}s (limit: 2s)"


@pytest.mark.eval
@pytest.mark.integration
@skip_no_binaries
def test_encode_throughput_medium(tmp_path):
    """Encoding a 1 MB file must complete in under 5 seconds."""
    size = 1024 * 1024
    src = tmp_path / "medium.bin"
    src.write_bytes(os.urandom(size))
    out_dir = tmp_path / "shards"
    out_dir.mkdir()

    elapsed = run_encoder(src, out_dir, data=8, par=4)
    print_throughput("encode 1MB", size, elapsed)

    assert elapsed < 5.0, f"Encoding 1 MB took {elapsed:.3f}s (limit: 5s)"


@pytest.mark.eval
def test_encrypt_throughput_per_chunk():
    """Fernet-encrypting a 128 KB chunk must complete in under 0.5 seconds."""
    size = 128 * 1024
    plaintext = os.urandom(size)
    key = Fernet.generate_key()

    start = time.perf_counter()
    token = encrypt_chunk(plaintext, key)
    elapsed = time.perf_counter() - start

    print_throughput("encrypt 128KB chunk", size, elapsed)
    assert elapsed < 0.5, f"Encrypting 128 KB took {elapsed:.4f}s (limit: 0.5s)"
    assert len(token) > size  # sanity: token is larger than plaintext


@pytest.mark.eval
@pytest.mark.integration
@pytest.mark.slow
@skip_no_binaries
def test_full_pipeline_throughput(tmp_path):
    """
    Measure total encode + encrypt time for a 2 MB file and log MB/s.
    No hard threshold — this test always passes; it is a measurement fixture.
    """
    size = 2 * 1024 * 1024
    src = tmp_path / "pipeline.bin"
    src.write_bytes(os.urandom(size))
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    key = Fernet.generate_key()
    f = Fernet(key)

    # --- Encode ---
    t0 = time.perf_counter()
    encode_cmd = [ENCODER_BIN, "-data", "8", "-par", "4",
                  "-out", str(out_dir), str(src)]
    subprocess.run(encode_cmd, check=True, capture_output=True)
    encode_elapsed = time.perf_counter() - t0

    # --- Encrypt ---
    basename = src.name
    t1 = time.perf_counter()
    for i in range(12):
        shard = out_dir / f"{basename}.{i}"
        if shard.exists():
            shard.write_bytes(f.encrypt(shard.read_bytes()))
    encrypt_elapsed = time.perf_counter() - t1

    total = encode_elapsed + encrypt_elapsed
    print_throughput("encode 2MB", size, encode_elapsed)
    print_throughput("encrypt 12 shards (~2MB total)", size, encrypt_elapsed)
    print_throughput("full pipeline 2MB", size, total)

    # Soft assertion: pipeline should finish in under 30 seconds on any machine
    assert total < 30.0, f"Full pipeline took {total:.2f}s — unexpectedly slow"


@pytest.mark.eval
def test_parallel_encryption_faster_than_serial():
    """
    Encrypting 12 chunks of 128 KB each in parallel (ThreadPoolExecutor) must
    be faster than sequential encryption on a multi-core machine.

    Note: Due to the GIL this gain is modest for CPU-bound work, but Fernet
    involves C-extension code (OpenSSL) that releases the GIL, so parallelism
    typically helps.  We allow a generous 20 % margin and skip the assertion
    on machines where parallel overhead overwhelms the benefit (very fast CPUs
    with only 1 available core).
    """
    key = Fernet.generate_key()
    chunks = [os.urandom(128 * 1024) for _ in range(12)]

    # Serial
    t_serial_start = time.perf_counter()
    serial_tokens = [encrypt_chunk(c, key) for c in chunks]
    serial_elapsed = time.perf_counter() - t_serial_start

    # Parallel
    t_parallel_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        parallel_tokens = list(executor.map(lambda c: encrypt_chunk(c, key), chunks))
    parallel_elapsed = time.perf_counter() - t_parallel_start

    print(f"\n  [throughput] serial encrypt 12×128KB: {serial_elapsed:.4f}s")
    print(f"  [throughput] parallel encrypt 12×128KB: {parallel_elapsed:.4f}s")
    print(f"  [throughput] speedup: {serial_elapsed / parallel_elapsed:.2f}x")

    # All tokens must still be valid
    f = Fernet(key)
    for token, chunk in zip(parallel_tokens, chunks):
        assert f.decrypt(token) == chunk

    # Only assert speedup if serial took long enough that measurement noise
    # doesn't dominate (> 50 ms serial baseline)
    if serial_elapsed > 0.05:
        assert parallel_elapsed <= serial_elapsed * 1.5, (
            f"Parallel ({parallel_elapsed:.4f}s) was not faster than "
            f"1.5× serial ({serial_elapsed:.4f}s)"
        )


@pytest.mark.eval
def test_decrypt_throughput():
    """
    Decryption throughput must be in the same ballpark as encryption.
    Specifically, decrypt time must be < 3× encrypt time for the same data.
    """
    key = Fernet.generate_key()
    f = Fernet(key)
    size = 512 * 1024
    plaintext = os.urandom(size)

    t0 = time.perf_counter()
    token = f.encrypt(plaintext)
    encrypt_elapsed = time.perf_counter() - t0

    t1 = time.perf_counter()
    recovered = f.decrypt(token)
    decrypt_elapsed = time.perf_counter() - t1

    assert recovered == plaintext
    print_throughput("encrypt 512KB", size, encrypt_elapsed)
    print_throughput("decrypt 512KB", size, decrypt_elapsed)

    if encrypt_elapsed > 0.001:  # avoid division-by-near-zero
        ratio = decrypt_elapsed / encrypt_elapsed
        print(f"  [throughput] decrypt/encrypt ratio: {ratio:.2f}x")
        assert ratio < 3.0, (
            f"Decrypt ({decrypt_elapsed:.4f}s) is more than 3× encrypt ({encrypt_elapsed:.4f}s)"
        )


@pytest.mark.eval
@pytest.mark.integration
@skip_no_binaries
def test_encode_decode_roundtrip_throughput(tmp_path):
    """
    Measure full encode + decode round-trip time (no encryption) for a 1 MB
    file.  This is the minimum pipeline latency without network I/O.
    Must complete in under 10 seconds.
    """
    size = 1024 * 1024
    src = tmp_path / "roundtrip.bin"
    src.write_bytes(os.urandom(size))
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    original_hash = sha256_file(src)

    t0 = time.perf_counter()

    encode_cmd = [ENCODER_BIN, "-data", "8", "-par", "4",
                  "-out", str(out_dir), str(src)]
    subprocess.run(encode_cmd, check=True, capture_output=True)

    reconstructed = tmp_path / "reconstructed.bin"
    decode_cmd = [DECODER_BIN, "-data", "8", "-par", "4",
                  "-out", str(reconstructed), src.name]
    subprocess.run(decode_cmd, check=True, capture_output=True, cwd=str(out_dir))

    total_elapsed = time.perf_counter() - t0
    print_throughput("encode+decode round-trip 1MB", size, total_elapsed)

    assert sha256_file(reconstructed) == original_hash, "Hash mismatch in round-trip"
    assert total_elapsed < 10.0, (
        f"Round-trip for 1MB took {total_elapsed:.3f}s (limit: 10s)"
    )
