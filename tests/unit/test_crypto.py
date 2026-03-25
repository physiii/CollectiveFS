"""
Unit tests for CollectiveFS encryption/decryption logic.

These tests exercise the Fernet-based encryption primitives used in cfs.py's
encryptChunk / decryptChunk flow.  They run entirely in-process without
touching the encoder/decoder binaries.

Coverage:
- Key generation format and length
- Encrypt/decrypt round-trip correctness
- File-based encrypt/decrypt (mirroring cfs.py's on-disk flow)
- Probabilistic IV uniqueness (different ciphertexts for identical plaintext)
- HMAC integrity: tampered ciphertext raises InvalidToken
- Key serialisation / persistence
- Bulk per-chunk independence
- Overhead of Fernet framing vs raw plaintext
"""

import base64
import os
import pytest

from cryptography.fernet import Fernet, InvalidToken


# ---------------------------------------------------------------------------
# Parametrised chunk sizes used across several tests
# ---------------------------------------------------------------------------
CHUNK_SIZES = [512, 4096, 65536, 1024 * 1024]
CHUNK_SIZE_IDS = ["512B", "4KB", "64KB", "1MB"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fernet_key_generation():
    """
    Fernet.generate_key() must produce a URL-safe base64-encoded 32-byte key.
    The encoded form is always exactly 44 characters (32 bytes → 44 base64 chars).
    """
    key = Fernet.generate_key()
    assert isinstance(key, bytes), "Key must be bytes"
    assert len(key) == 44, f"Expected 44 chars, got {len(key)}"
    # Must be valid URL-safe base64
    decoded = base64.urlsafe_b64decode(key)
    assert len(decoded) == 32, "Decoded key must be 32 bytes (256-bit)"


@pytest.mark.unit
@pytest.mark.parametrize("size", CHUNK_SIZES, ids=CHUNK_SIZE_IDS)
def test_fernet_encrypt_decrypt_roundtrip(fernet_instance, size):
    """
    Encrypting arbitrary bytes and immediately decrypting must yield the
    original plaintext, at every supported chunk size.
    """
    plaintext = os.urandom(size)
    token = fernet_instance.encrypt(plaintext)
    recovered = fernet_instance.decrypt(token)
    assert recovered == plaintext, "Decrypted output does not match original"


@pytest.mark.unit
@pytest.mark.parametrize("size", CHUNK_SIZES, ids=CHUNK_SIZE_IDS)
def test_fernet_chunk_encrypt_decrypt(tmp_path, fernet_key, size):
    """
    Simulate the cfs.py encryptChunk / decryptChunk flow:
      1. Write plaintext to a file.
      2. Read it, encrypt in-memory, overwrite the file.
      3. Re-read the file, decrypt, compare with original plaintext.

    This mirrors the exact pattern used in cfs.py lines 88-98.
    """
    shard_path = tmp_path / "chunk.bin"
    original = os.urandom(size)
    shard_path.write_bytes(original)

    f = Fernet(fernet_key)

    # --- encryptChunk ---
    with open(shard_path, "rb") as fh:
        data = fh.read()
    encrypted = f.encrypt(data)
    with open(shard_path, "wb") as fh:
        fh.write(encrypted)

    # --- decryptChunk ---
    with open(shard_path, "rb") as fh:
        token = fh.read()
    decrypted = f.decrypt(token)

    assert decrypted == original, "File-based encrypt/decrypt round-trip failed"


@pytest.mark.unit
def test_fernet_different_ciphertexts_same_plaintext(fernet_instance):
    """
    Fernet uses a random 128-bit IV per encryption call.  Encrypting the same
    plaintext twice must produce two distinct ciphertexts.
    """
    plaintext = b"identical plaintext for iv uniqueness check"
    token1 = fernet_instance.encrypt(plaintext)
    token2 = fernet_instance.encrypt(plaintext)
    assert token1 != token2, "Expected different ciphertexts due to random IV"
    # Both must still decrypt correctly
    assert fernet_instance.decrypt(token1) == plaintext
    assert fernet_instance.decrypt(token2) == plaintext


@pytest.mark.unit
@pytest.mark.parametrize("size", CHUNK_SIZES, ids=CHUNK_SIZE_IDS)
def test_fernet_tampered_ciphertext_raises(fernet_instance, size):
    """
    Any modification to the ciphertext (flip a single byte) must cause
    Fernet to raise InvalidToken due to HMAC-SHA256 verification failure.
    """
    plaintext = os.urandom(size)
    token = bytearray(fernet_instance.encrypt(plaintext))

    # Flip a byte in the middle of the token (avoid the version byte at index 0)
    flip_pos = len(token) // 2
    token[flip_pos] ^= 0xFF

    with pytest.raises(InvalidToken):
        fernet_instance.decrypt(bytes(token))


@pytest.mark.unit
def test_fernet_key_persistence(tmp_path, fernet_key):
    """
    Write a Fernet key to disk (as cfs.py does to ~/.collective/key), reload it,
    and verify that encrypt/decrypt works correctly with the reloaded key.
    """
    key_file = tmp_path / "key"
    key_file.write_bytes(fernet_key)

    # Reload – simulates the startup path in cfs.py
    loaded_key = key_file.read_bytes()
    assert loaded_key == fernet_key

    f = Fernet(loaded_key)
    plaintext = b"key persistence test payload"
    token = f.encrypt(plaintext)
    assert f.decrypt(token) == plaintext


@pytest.mark.unit
def test_all_chunks_independently_encrypted(fernet_instance):
    """
    Encrypting 12 chunks (8 data + 4 parity) from the same source data must
    produce 12 mutually distinct ciphertexts — each has its own random IV.
    """
    chunk_data = b"shared chunk content" * 100  # fixed plaintext
    tokens = [fernet_instance.encrypt(chunk_data) for _ in range(12)]

    # All 12 tokens must be pairwise distinct
    assert len(set(tokens)) == 12, (
        "Expected all 12 ciphertexts to be unique (each has a random IV)"
    )

    # All 12 must decrypt correctly
    for token in tokens:
        assert fernet_instance.decrypt(token) == chunk_data


@pytest.mark.unit
@pytest.mark.parametrize("size", CHUNK_SIZES, ids=CHUNK_SIZE_IDS)
def test_chunk_size_overhead(fernet_instance, size):
    """
    A Fernet token is always larger than the plaintext because it includes:
      version (1 B) + timestamp (8 B) + IV (16 B) + ciphertext + HMAC (32 B).
    The minimum overhead is 57 bytes before base64 expansion.
    """
    plaintext = os.urandom(size)
    token = fernet_instance.encrypt(plaintext)
    assert len(token) > size, (
        f"Token ({len(token)} bytes) must be larger than plaintext ({size} bytes)"
    )
    # Sanity-check the raw (pre-base64) overhead is at least 57 bytes
    raw_token = base64.urlsafe_b64decode(token + b"==")
    assert len(raw_token) > size + 56, (
        "Expected at least 57 bytes of overhead in raw (decoded) Fernet token"
    )
