"""
Integration / evaluation tests for the full CollectiveFS pipeline.

These tests exercise the real encode → encrypt → decrypt → decode flow using:
  - The actual lib/encoder and lib/decoder Go binaries (via subprocess)
  - Real Fernet encryption (cryptography.fernet)
  - SHA-256 hashing to verify end-to-end data integrity

Tests require the encoder and decoder binaries to be present.  They are
skipped automatically when the binaries are absent so CI can still run unit
tests without the compiled Go code.
"""

import hashlib
import os
import subprocess
import pytest

from cryptography.fernet import Fernet, InvalidToken

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENCODER_BIN = os.path.join(PROJECT_ROOT, "lib", "encoder")
DECODER_BIN = os.path.join(PROJECT_ROOT, "lib", "decoder")

ENCODER_PRESENT = os.path.isfile(ENCODER_BIN) and os.access(ENCODER_BIN, os.X_OK)
DECODER_PRESENT = os.path.isfile(DECODER_BIN) and os.access(DECODER_BIN, os.X_OK)
BINARIES_PRESENT = ENCODER_PRESENT and DECODER_PRESENT

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
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_encoder(input_file, out_dir, data=8, par=4):
    """Run the encoder binary; return list of shard Paths in index order."""
    import pathlib
    cmd = [ENCODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_dir), str(input_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    result.check_returncode()
    basename = pathlib.Path(input_file).name
    return [pathlib.Path(out_dir) / f"{basename}.{i}" for i in range(data + par)]


def run_decoder(base_file, out_file, data=8, par=4):
    """
    Run the decoder binary from the directory containing the shards.
    base_file must be an absolute path; cwd is set to its parent.
    """
    import pathlib
    base = pathlib.Path(base_file)
    cmd = [DECODER_BIN, "-data", str(data), "-par", str(par),
           "-out", str(out_file), base.name]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(base.parent))
    return result


def encrypt_shards(shard_paths, key):
    """Encrypt every shard file in-place with the given Fernet key."""
    f = Fernet(key)
    for p in shard_paths:
        if p.exists():
            plaintext = p.read_bytes()
            p.write_bytes(f.encrypt(plaintext))


def decrypt_shards(shard_paths, key):
    """Decrypt every shard file in-place with the given Fernet key."""
    f = Fernet(key)
    for p in shard_paths:
        if p.exists():
            token = p.read_bytes()
            p.write_bytes(f.decrypt(token))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@skip_no_binaries
def test_encoder_produces_correct_shard_count(sample_file_small, tmp_path):
    """
    Running the encoder with -data 8 -par 4 must produce exactly 12 shard
    files in the output directory.
    """
    shards = run_encoder(sample_file_small, tmp_path, data=8, par=4)
    existing = [s for s in shards if s.exists()]
    assert len(existing) == 12, f"Expected 12 shards, got {len(existing)}"


@pytest.mark.integration
@skip_no_binaries
def test_encoder_shard_naming_convention(sample_file_small, tmp_path):
    """
    Encoder output files must follow the naming pattern:
      <original-filename>.<index>  (e.g. sample_small.bin.0 … sample_small.bin.11)
    """
    shards = run_encoder(sample_file_small, tmp_path, data=8, par=4)
    for i, shard in enumerate(shards):
        assert shard.exists(), f"Expected shard {shard.name} to exist"
        assert shard.name.endswith(f".{i}"), (
            f"Shard {shard.name} does not end with .{i}"
        )


@pytest.mark.integration
@skip_no_binaries
def test_full_pipeline_small_file(sample_file_small, tmp_path):
    """
    Full pipeline for a 64 KB file:
      encode → encrypt all shards → decrypt all shards → decode → SHA256 match.
    """
    original_hash = sha256_file(sample_file_small)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()

    key = Fernet.generate_key()
    shards = run_encoder(sample_file_small, out_dir, data=8, par=4)
    assert len([s for s in shards if s.exists()]) == 12

    encrypt_shards(shards, key)
    decrypt_shards(shards, key)

    reconstructed = tmp_path / "reconstructed.bin"
    result = run_decoder(out_dir / sample_file_small.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed:\n{result.stderr}"

    assert sha256_file(reconstructed) == original_hash, (
        "Reconstructed file hash does not match original"
    )


@pytest.mark.integration
@pytest.mark.slow
@skip_no_binaries
def test_full_pipeline_medium_file(sample_file_medium, tmp_path):
    """
    Full pipeline for a 2 MB file — same flow as the small-file test but
    exercises larger shard sizes.  Marked slow.
    """
    original_hash = sha256_file(sample_file_medium)
    out_dir = tmp_path / "shards"
    out_dir.mkdir()

    key = Fernet.generate_key()
    shards = run_encoder(sample_file_medium, out_dir, data=8, par=4)
    encrypt_shards(shards, key)
    decrypt_shards(shards, key)

    reconstructed = tmp_path / "reconstructed.bin"
    result = run_decoder(out_dir / sample_file_medium.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed:\n{result.stderr}"
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@skip_no_binaries
def test_pipeline_with_custom_parity(tmp_path):
    """
    Test encoding with non-default parameters: 4 data shards + 2 parity shards
    (6 total) — verifies the binary honours the -data / -par flags.
    """
    src = tmp_path / "custom.bin"
    src.write_bytes(os.urandom(32 * 1024))
    original_hash = sha256_file(src)

    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = run_encoder(src, out_dir, data=4, par=2)

    existing = [s for s in shards if s.exists()]
    assert len(existing) == 6, f"Expected 6 shards, got {len(existing)}"

    reconstructed = tmp_path / "reconstructed.bin"
    result = run_decoder(out_dir / src.name, reconstructed, data=4, par=2)
    assert result.returncode == 0
    assert sha256_file(reconstructed) == original_hash


@pytest.mark.integration
@skip_no_binaries
def test_shard_encryption_does_not_affect_count(sample_file_small, tmp_path):
    """
    Encrypting shards in-place must not add or remove files from the
    output directory — shard count must remain 12 before and after encryption.
    """
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = run_encoder(sample_file_small, out_dir, data=8, par=4)
    count_before = len([s for s in shards if s.exists()])

    key = Fernet.generate_key()
    encrypt_shards(shards, key)
    count_after = len([s for s in shards if s.exists()])

    assert count_before == count_after == 12


@pytest.mark.integration
@skip_no_binaries
def test_encrypted_shards_unreadable_without_key(sample_file_small, tmp_path):
    """
    Attempting to decrypt a Fernet-encrypted shard with the wrong key must
    raise InvalidToken — confirming that encryption actually protects the data.
    """
    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = run_encoder(sample_file_small, out_dir, data=8, par=4)

    correct_key = Fernet.generate_key()
    wrong_key = Fernet.generate_key()
    encrypt_shards(shards, correct_key)

    wrong_fernet = Fernet(wrong_key)
    for shard in shards:
        if shard.exists():
            with pytest.raises(InvalidToken):
                wrong_fernet.decrypt(shard.read_bytes())
            break  # one shard is sufficient to prove the point


@pytest.mark.integration
@skip_no_binaries
def test_pipeline_is_deterministic_metadata(tmp_path):
    """
    Encoding the same file twice must:
      - Produce shards with identical content (encoder is deterministic)
      - But UUID-based chunk IDs generated by cfs.py would differ each run.
    This test checks the encoder side: same input → same shard bytes.
    """
    src = tmp_path / "repeat.bin"
    src.write_bytes(os.urandom(32 * 1024))

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    shards1 = run_encoder(src, out1, data=8, par=4)
    shards2 = run_encoder(src, out2, data=8, par=4)

    for s1, s2 in zip(shards1, shards2):
        assert s1.read_bytes() == s2.read_bytes(), (
            f"Shard {s1.name} differs between two identical encode runs"
        )


@pytest.mark.integration
@skip_no_binaries
def test_encoder_handles_empty_file(tmp_path):
    """
    Encoding a 0-byte file should either succeed (producing valid zero-content
    shards) or exit with a non-zero code and a clear error message.
    It must not crash silently or produce a partial / corrupt output.
    """
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    out_dir = tmp_path / "shards"
    out_dir.mkdir()

    cmd = [ENCODER_BIN, "-data", "8", "-par", "4",
           "-out", str(out_dir), str(empty)]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        # If encoder succeeds, it must produce exactly 12 shard files
        shards = list(out_dir.iterdir())
        assert len(shards) == 12, (
            f"Expected 12 shards for empty file, got {len(shards)}"
        )
    else:
        # Failure is acceptable — must emit an error message
        assert result.stderr.strip() or result.stdout.strip(), (
            "Encoder failed silently on empty file (no error output)"
        )


@pytest.mark.integration
@skip_no_binaries
def test_encoder_handles_binary_files(tmp_path):
    """
    Encoder must handle arbitrary binary content (PNG-like header + random
    bytes) without corruption — tests that no text-mode assumptions exist.
    """
    # Build a pseudo-PNG: PNG magic bytes followed by random data
    png_magic = b"\x89PNG\r\n\x1a\n"
    binary_content = png_magic + os.urandom(63 * 1024 - len(png_magic))
    src = tmp_path / "image.png"
    src.write_bytes(binary_content)
    original_hash = sha256_file(src)

    out_dir = tmp_path / "shards"
    out_dir.mkdir()
    shards = run_encoder(src, out_dir, data=8, par=4)
    existing = [s for s in shards if s.exists()]
    assert len(existing) == 12

    reconstructed = tmp_path / "reconstructed.png"
    result = run_decoder(out_dir / src.name, reconstructed, data=8, par=4)
    assert result.returncode == 0, f"Decoder failed on binary file:\n{result.stderr}"
    assert sha256_file(reconstructed) == original_hash
