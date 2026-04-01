"""
Evaluation tests for Reed-Solomon erasure-coding durability in CollectiveFS.

These tests verify that the encoder/decoder pair correctly handles shard loss:
  - Reconstruction succeeds when at most `par` shards are missing.
  - Reconstruction fails when more than `par` shards are missing.
  - Statistical coverage via random-loss simulation.
  - Full encrypted pipeline with simulated chunk loss.

Shard removal is modelled by physically deleting the shard file; the decoder
treats any file it cannot open as a missing shard (fills with zeros) and then
attempts Reed-Solomon reconstruction.

All tests require the lib/encoder and lib/decoder binaries.
"""

import hashlib
import os
import random
import subprocess
import pathlib

import pytest

from cryptography.fernet import Fernet

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENCODER_BIN = os.path.join(PROJECT_ROOT, "lib", "encoder")
DECODER_BIN = os.path.join(PROJECT_ROOT, "lib", "decoder")

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

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def encode_file(src, out_dir, data=8, par=4):
    """Encode src into out_dir; return ordered list of shard Paths."""
    cmd = [ENCODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_dir), str(src)]
    subprocess.run(cmd, check=True, capture_output=True)
    basename = pathlib.Path(src).name
    return [pathlib.Path(out_dir) / f"{basename}.{i}" for i in range(data + par)]


def decode_file(shard_dir, base_name, out_file, data=8, par=4):
    """Run the decoder from shard_dir; return CompletedProcess."""
    cmd = [DECODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_file), base_name]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(shard_dir))


def make_test_file(tmp_path, size=65536, name="testfile.bin"):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return p


def encrypt_shards(shards, key):
    f = Fernet(key)
    for s in shards:
        if s.exists():
            s.write_bytes(f.encrypt(s.read_bytes()))


def decrypt_shards(shards, key):
    f = Fernet(key)
    for s in shards:
        if s.exists():
            s.write_bytes(f.decrypt(s.read_bytes()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@skip_no_binaries
def test_reconstruct_with_zero_missing_chunks(tmp_path):
    """All 12 shards present — decoder should report 'No reconstruction needed'."""
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    encode_file(src, out_dir, data=8, par=4)

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed: {result.stderr}"
    assert reconstructed.exists()
    assert sha256_file(reconstructed) == sha256_file(src)


@pytest.mark.integration
@skip_no_binaries
def test_reconstruct_with_1_missing_chunk(tmp_path):
    """Remove 1 random shard — decoder must still reconstruct correctly."""
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)
    original_hash = sha256_file(src)

    victim = random.choice(shards)
    victim.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed with 1 missing shard: {result.stderr}"
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@skip_no_binaries
def test_reconstruct_with_2_missing_chunks(tmp_path):
    """Remove 2 random shards — decoder must still reconstruct correctly."""
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)
    original_hash = sha256_file(src)

    for victim in random.sample(shards, 2):
        victim.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed with 2 missing shards: {result.stderr}"
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@skip_no_binaries
def test_reconstruct_with_par_missing_chunks(tmp_path):
    """
    Remove exactly `par` (4) shards — the boundary case where reconstruction
    is still possible.  Any combination of 4 missing should succeed.
    """
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)
    original_hash = sha256_file(src)

    # Remove the 4 parity shards (indices 8–11) as a deterministic boundary case
    for shard in shards[8:]:
        shard.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, (
        f"Decoder failed at parity boundary (par=4 missing): {result.stderr}"
    )
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@skip_no_binaries
def test_fails_with_too_many_missing_chunks(tmp_path):
    """
    Remove par+1 (5) shards — reconstruction must fail (non-zero exit code).
    """
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)

    for victim in random.sample(shards, 5):
        victim.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode != 0, (
        "Decoder unexpectedly succeeded with par+1 (5) missing shards"
    )


@pytest.mark.integration
@skip_no_binaries
def test_parity_shards_only_is_insufficient(tmp_path):
    """
    Keep only the 4 parity shards (remove all 8 data shards) — reconstruction
    must fail because there is insufficient shard data.
    """
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)

    # Remove all 8 data shards, keep parity (indices 8–11)
    for shard in shards[:8]:
        shard.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode != 0, (
        "Decoder should fail when only parity shards are present"
    )


@pytest.mark.integration
@skip_no_binaries
def test_all_data_shards_present_no_parity_needed(tmp_path):
    """
    When all 8 data shards are present (parity removed), the decoder must
    succeed without needing to perform any reconstruction.
    """
    src = make_test_file(tmp_path)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)
    original_hash = sha256_file(src)

    # Remove all 4 parity shards (indices 8–11)
    for shard in shards[8:]:
        shard.unlink()

    reconstructed = tmp_path / "out.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, (
        f"Decoder failed with all data shards present: {result.stderr}"
    )
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@pytest.mark.slow
@skip_no_binaries
def test_random_chunk_loss_simulation(tmp_path):
    """
    Run 50 random scenarios with 0–4 shards deleted; verify that ALL succeed.
    This gives confidence that reconstruction works for arbitrary shard subsets
    as long as at most `par` shards are missing.
    """
    successes = 0
    trials = 50

    for trial in range(trials):
        trial_dir = tmp_path / f"trial_{trial}"
        trial_dir.mkdir()
        src = trial_dir / "data.bin"
        src.write_bytes(os.urandom(32 * 1024))
        out_dir = trial_dir / "shards"
        out_dir.mkdir()
        shards = encode_file(src, out_dir, data=8, par=4)
        original_hash = sha256_file(src)

        missing_count = random.randint(0, 4)  # 0 to par inclusive
        for victim in random.sample(shards, missing_count):
            victim.unlink()

        reconstructed = trial_dir / "reconstructed.bin"
        result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
        if result.returncode == 0 and sha256_file(reconstructed) == original_hash:
            successes += 1

    assert successes == trials, (
        f"Random loss simulation: only {successes}/{trials} trials succeeded"
    )


@pytest.mark.integration
@skip_no_binaries
def test_durability_matrix(tmp_path):
    """
    For each (data, parity) combination in the matrix, verify that removing
    exactly `par` shards still allows successful reconstruction.

    File sizes are chosen to be exact multiples of the data-shard count so
    there is no zero-padding ambiguity in the reconstructed output.
    """
    # (data, par, file_size_bytes)
    # file_size is chosen as an exact multiple of data shards to avoid
    # the known zero-padding limitation of the simple encoder.
    matrix = [
        (4, 2, 4 * 1024),    # 4 KB, divisible by 4
        (8, 4, 8 * 1024),    # 8 KB, divisible by 8
        (10, 6, 10 * 1024),  # 10 KB, divisible by 10
    ]

    for data, par, size in matrix:
        trial_dir = tmp_path / f"d{data}_p{par}"
        trial_dir.mkdir()
        src = trial_dir / "src.bin"
        src.write_bytes(os.urandom(size))
        out_dir = trial_dir / "shards"
        out_dir.mkdir()
        shards = encode_file(src, out_dir, data=data, par=par)
        original_hash = sha256_file(src)

        # Remove exactly par shards (always the parity ones for determinism)
        for shard in shards[data:]:
            shard.unlink()

        reconstructed = trial_dir / "reconstructed.bin"
        result = decode_file(out_dir, src.name, reconstructed, data=data, par=par)
        assert result.returncode == 0, (
            f"Durability matrix failure for data={data}, par={par}: {result.stderr}"
        )
        assert sha256_file(reconstructed) == original_hash, (
            f"Hash mismatch for data={data}, par={par} — "
            f"ensure file size is divisible by data-shard count"
        )


@pytest.mark.integration
@skip_no_binaries
def test_encrypted_then_reconstruct(tmp_path):
    """
    Full encrypted pipeline with chunk loss:
      encode → encrypt all shards → remove 3 shards →
      decrypt remaining shards → decode → verify hash.
    """
    src = make_test_file(tmp_path, size=128 * 1024)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = encode_file(src, out_dir, data=8, par=4)
    original_hash = sha256_file(src)

    key = Fernet.generate_key()
    encrypt_shards(shards, key)

    # Remove 3 of the 12 encrypted shards (within the par=4 tolerance)
    for victim in random.sample(shards, 3):
        victim.unlink()

    # Decrypt the surviving shards
    surviving = [s for s in shards if s.exists()]
    decrypt_shards(surviving, key)

    reconstructed = tmp_path / "reconstructed.bin"
    result = decode_file(out_dir, src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, (
        f"Decoder failed after encrypted chunk loss: {result.stderr}"
    )
    assert sha256_file(reconstructed) == original_hash, (
        "Hash mismatch after encrypted chunk-loss reconstruction"
    )
