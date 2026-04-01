"""
Shared pytest fixtures for the CollectiveFS test suite.

Provides fixtures for:
- Temporary directory trees mirroring the .collective layout
- Fernet key and instance creation
- Sample files at various sizes for performance/correctness testing
- Encoder/decoder binary paths and callable wrappers
"""

import os
import subprocess
import pytest

from cryptography.fernet import Fernet

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENCODER_BIN = os.path.join(PROJECT_ROOT, "lib", "encoder")
DECODER_BIN = os.path.join(PROJECT_ROOT, "lib", "decoder")
BUNNY_VIDEO = os.path.join(PROJECT_ROOT, "tests", "fixtures", "bunny_1080p.mp4")


@pytest.fixture
def project_root():
    """Return the absolute path to the CollectiveFS project root."""
    return PROJECT_ROOT


@pytest.fixture
def encoder_path():
    """Return the absolute path to the encoder binary."""
    return ENCODER_BIN


@pytest.fixture
def decoder_path():
    """Return the absolute path to the decoder binary."""
    return DECODER_BIN


@pytest.fixture
def tmp_root(tmp_path):
    """
    Create a temporary directory tree that mirrors the .collective layout:
      <tmp_path>/
        .collective/
          proc/
          cache/
          public/
          tree/
    Returns the tmp_path root so callers can construct paths relative to it.
    """
    collective = tmp_path / ".collective"
    for subdir in ("proc", "cache", "public", "tree"):
        (collective / subdir).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def fernet_key():
    """Generate and return a fresh Fernet key (bytes)."""
    return Fernet.generate_key()


@pytest.fixture
def fernet_instance(fernet_key):
    """Return a Fernet instance constructed from the generated key fixture."""
    return Fernet(fernet_key)


@pytest.fixture
def sample_file_small(tmp_path):
    """
    Create a 64 KB file filled with random bytes.
    Suitable for fast unit/integration tests.
    Returns the Path object pointing to the file.
    """
    p = tmp_path / "sample_small.bin"
    p.write_bytes(os.urandom(64 * 1024))
    return p


@pytest.fixture
def sample_file_medium(tmp_path):
    """
    Create a 2 MB file filled with random bytes.
    Suitable for integration tests.
    Returns the Path object pointing to the file.
    """
    p = tmp_path / "sample_medium.bin"
    p.write_bytes(os.urandom(2 * 1024 * 1024))
    return p


@pytest.fixture
def sample_file_large(tmp_path):
    """
    Create a 20 MB file filled with random bytes.
    Marked slow - only runs when explicitly included.
    Returns the Path object pointing to the file.
    """
    p = tmp_path / "sample_large.bin"
    # Write in chunks to avoid allocating 20 MB at once in urandom
    with p.open("wb") as f:
        for _ in range(20):
            f.write(os.urandom(1024 * 1024))
    return p


@pytest.fixture
def bunny_video():
    """
    Return the path to the Big Buck Bunny 1080p test video (~1 MB).
    Skips the test if the fixture file is not present.
    """
    if not os.path.isfile(BUNNY_VIDEO):
        pytest.skip("bunny_1080p.mp4 not found – run: make download-fixtures")
    return BUNNY_VIDEO


@pytest.fixture
def run_encoder(encoder_path):
    """
    Return a callable that runs the encoder binary against a given file.

    Usage:
        shard_paths = run_encoder(input_file, out_dir, data=8, par=4)

    The callable:
      - Invokes lib/encoder with --data, --par, --out, and the input filename
      - Raises subprocess.CalledProcessError on non-zero exit
      - Returns a sorted list of Path objects for the produced shard files

    The caller is responsible for providing a writable out_dir.
    """
    import pathlib

    def _run(input_file, out_dir, data=8, par=4):
        input_file = pathlib.Path(input_file)
        out_dir = pathlib.Path(out_dir)
        cmd = [
            encoder_path,
            "-data", str(data),
            "-par", str(par),
            "-out", str(out_dir),
            str(input_file),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        # Collect shard files: named <basename>.<N>
        basename = input_file.name
        total = data + par
        shards = []
        for i in range(total):
            shard = out_dir / f"{basename}.{i}"
            if shard.exists():
                shards.append(shard)
        return shards

    return _run
