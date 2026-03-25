"""
bench_crypto.py — Encryption benchmarks for CollectiveFS.

Measures Fernet encrypt/decrypt throughput, parallel speedup, key generation,
overhead, and comparison with raw AES-256-GCM.
"""

from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from benchmarks.common import (
    BenchResult,
    BenchSuite,
    fernet_key,
    make_result,
    skipped_result,
    throughput_mbps,
    console,
)

CATEGORY = "Crypto"
ITERATIONS = 5


class BenchmarkCrypto:
    """Encryption/decryption benchmarks."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations

    # ------------------------------------------------------------------
    # Fernet encrypt throughput by chunk size
    # ------------------------------------------------------------------

    def test_fernet_encrypt_throughput(self) -> List[BenchResult]:
        results = []
        chunk_sizes = {
            "4KB":   4 * 1024,
            "64KB":  64 * 1024,
            "512KB": 512 * 1024,
            "4MB":   4 * 1024 * 1024,
        }
        console.print("  [dim]fernet_encrypt_throughput...[/dim]")

        for label, size in chunk_sizes.items():
            name = f"fernet_encrypt_throughput [{label}]"
            key = fernet_key()
            f = Fernet(key)
            plaintext = os.urandom(size)
            samples: List[float] = []

            # warmup
            f.encrypt(plaintext)
            for _ in range(self.iterations):
                t0 = time.perf_counter()
                f.encrypt(plaintext)
                samples.append(time.perf_counter() - t0)

            mbps = [throughput_mbps(size, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s", {"chunk_size": label})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Fernet decrypt throughput by chunk size
    # ------------------------------------------------------------------

    def test_fernet_decrypt_throughput(self) -> List[BenchResult]:
        results = []
        chunk_sizes = {
            "4KB":   4 * 1024,
            "64KB":  64 * 1024,
            "512KB": 512 * 1024,
            "4MB":   4 * 1024 * 1024,
        }
        console.print("  [dim]fernet_decrypt_throughput...[/dim]")

        for label, size in chunk_sizes.items():
            name = f"fernet_decrypt_throughput [{label}]"
            key = fernet_key()
            f = Fernet(key)
            plaintext = os.urandom(size)
            token = f.encrypt(plaintext)
            samples: List[float] = []

            # warmup
            f.decrypt(token)
            for _ in range(self.iterations):
                t0 = time.perf_counter()
                f.decrypt(token)
                samples.append(time.perf_counter() - t0)

            mbps = [throughput_mbps(size, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s", {"chunk_size": label})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Fernet encrypt+decrypt roundtrip MB/s
    # ------------------------------------------------------------------

    def test_fernet_encrypt_decrypt_roundtrip_mbps(self) -> List[BenchResult]:
        results = []
        chunk_sizes = {
            "64KB":  64 * 1024,
            "512KB": 512 * 1024,
            "4MB":   4 * 1024 * 1024,
        }
        console.print("  [dim]fernet_encrypt_decrypt_roundtrip_mbps...[/dim]")

        for label, size in chunk_sizes.items():
            name = f"fernet_roundtrip_mbps [{label}]"
            key = fernet_key()
            f = Fernet(key)
            plaintext = os.urandom(size)
            samples: List[float] = []

            # warmup
            f.decrypt(f.encrypt(plaintext))
            for _ in range(self.iterations):
                t0 = time.perf_counter()
                token = f.encrypt(plaintext)
                f.decrypt(token)
                samples.append(time.perf_counter() - t0)

            mbps = [throughput_mbps(size, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s", {"chunk_size": label})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Parallel encrypt speedup (thread pool)
    # ------------------------------------------------------------------

    def test_parallel_encrypt_speedup(self) -> List[BenchResult]:
        results = []
        thread_counts = [1, 2, 4, 8, 12]
        chunk_size = 512 * 1024   # 512 KB per chunk
        n_chunks = 12             # total 12 chunks (realistic: 12 shards)
        console.print("  [dim]parallel_encrypt_speedup...[/dim]")

        key = fernet_key()
        chunks = [os.urandom(chunk_size) for _ in range(n_chunks)]

        def encrypt_one(data: bytes) -> bytes:
            return Fernet(key).encrypt(data)

        for workers in thread_counts:
            name = f"parallel_encrypt_speedup [threads={workers}]"
            samples: List[float] = []

            def _run(w=workers):
                t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=w) as pool:
                    futs = [pool.submit(encrypt_one, c) for c in chunks]
                    for fut in as_completed(futs):
                        fut.result()
                return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

            total_bytes = chunk_size * n_chunks
            mbps = [throughput_mbps(total_bytes, s) for s in samples]
            r = make_result(name, CATEGORY, mbps, "MB/s",
                            {"threads": workers, "chunks": n_chunks,
                             "chunk_size": "512KB"})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Key generation ops/sec
    # ------------------------------------------------------------------

    def test_key_generation_ops_per_sec(self) -> BenchResult:
        name = "key_generation_ops_per_sec"
        console.print("  [dim]key_generation_ops_per_sec...[/dim]")

        n_keys = 200
        samples: List[float] = []

        def _run():
            t0 = time.perf_counter()
            for _ in range(n_keys):
                Fernet.generate_key()
            return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ops_per_sec = [n_keys / s for s in samples]
        r = make_result(name, CATEGORY, ops_per_sec, "ops/s",
                        {"n_keys_per_run": n_keys})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Encryption overhead bytes (ciphertext - plaintext)
    # ------------------------------------------------------------------

    def test_encryption_overhead_bytes(self) -> List[BenchResult]:
        results = []
        sizes = {
            "4KB":   4 * 1024,
            "64KB":  64 * 1024,
            "512KB": 512 * 1024,
        }
        console.print("  [dim]test_encryption_overhead_bytes...[/dim]")

        for label, size in sizes.items():
            name = f"encryption_overhead_bytes [{label}]"
            key = fernet_key()
            f = Fernet(key)
            plaintext = os.urandom(size)
            token = f.encrypt(plaintext)
            overhead = len(token) - len(plaintext)
            overhead_pct = overhead / size * 100

            r = make_result(name, CATEGORY, [float(overhead)], "bytes",
                            {"plaintext_bytes": size,
                             "ciphertext_bytes": len(token),
                             "overhead_pct": round(overhead_pct, 2)})
            self.suite.add(r)
            results.append(r)

        return results

    # ------------------------------------------------------------------
    # Fernet vs raw AES-256-GCM throughput comparison
    # ------------------------------------------------------------------

    def test_fernet_vs_raw_aes_gcm(self) -> List[BenchResult]:
        results = []
        size = 1 * 1024 * 1024  # 1 MB
        plaintext = os.urandom(size)
        console.print("  [dim]fernet_vs_raw_aes_gcm...[/dim]")

        # --- Fernet encrypt ---
        name_fernet_enc = "fernet_vs_aesgcm [fernet_encrypt, 1MB]"
        fkey = fernet_key()
        fernet_obj = Fernet(fkey)
        samples_fernet_enc: List[float] = []
        fernet_obj.encrypt(plaintext)  # warmup
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            fernet_obj.encrypt(plaintext)
            samples_fernet_enc.append(time.perf_counter() - t0)
        r = make_result(name_fernet_enc, CATEGORY,
                        [throughput_mbps(size, s) for s in samples_fernet_enc],
                        "MB/s", {"algorithm": "Fernet", "op": "encrypt"})
        self.suite.add(r)
        results.append(r)

        # --- AES-256-GCM encrypt ---
        name_gcm_enc = "fernet_vs_aesgcm [aesgcm_encrypt, 1MB]"
        gcm_key_bytes = AESGCM.generate_key(bit_length=256)
        aesgcm = AESGCM(gcm_key_bytes)
        nonce = os.urandom(12)
        samples_gcm_enc: List[float] = []
        aesgcm.encrypt(nonce, plaintext, None)  # warmup
        for _ in range(self.iterations):
            nonce = os.urandom(12)
            t0 = time.perf_counter()
            aesgcm.encrypt(nonce, plaintext, None)
            samples_gcm_enc.append(time.perf_counter() - t0)
        r = make_result(name_gcm_enc, CATEGORY,
                        [throughput_mbps(size, s) for s in samples_gcm_enc],
                        "MB/s", {"algorithm": "AES-256-GCM", "op": "encrypt"})
        self.suite.add(r)
        results.append(r)

        # --- Fernet decrypt ---
        name_fernet_dec = "fernet_vs_aesgcm [fernet_decrypt, 1MB]"
        token = fernet_obj.encrypt(plaintext)
        samples_fernet_dec: List[float] = []
        fernet_obj.decrypt(token)  # warmup
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            fernet_obj.decrypt(token)
            samples_fernet_dec.append(time.perf_counter() - t0)
        r = make_result(name_fernet_dec, CATEGORY,
                        [throughput_mbps(size, s) for s in samples_fernet_dec],
                        "MB/s", {"algorithm": "Fernet", "op": "decrypt"})
        self.suite.add(r)
        results.append(r)

        # --- AES-256-GCM decrypt ---
        name_gcm_dec = "fernet_vs_aesgcm [aesgcm_decrypt, 1MB]"
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        samples_gcm_dec: List[float] = []
        aesgcm.decrypt(nonce, ciphertext, None)  # warmup
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            aesgcm.decrypt(nonce, ciphertext, None)
            samples_gcm_dec.append(time.perf_counter() - t0)
        r = make_result(name_gcm_dec, CATEGORY,
                        [throughput_mbps(size, s) for s in samples_gcm_dec],
                        "MB/s", {"algorithm": "AES-256-GCM", "op": "decrypt"})
        self.suite.add(r)
        results.append(r)

        return results

    # ------------------------------------------------------------------
    # Encrypt all 12 shards of a 4MB file (total time)
    # ------------------------------------------------------------------

    def test_encrypt_12_shards_total_time(self) -> BenchResult:
        name = "encrypt_12_shards_total_time [4MB]"
        console.print("  [dim]encrypt_12_shards_total_time...[/dim]")

        file_size = 4 * 1024 * 1024
        n_shards = 12
        shard_size = file_size // 8  # 8 data shards → ~512KB each (parity included too)
        shards = [os.urandom(shard_size) for _ in range(n_shards)]
        key = fernet_key()
        samples: List[float] = []

        def _run():
            f = Fernet(key)
            t0 = time.perf_counter()
            for shard in shards:
                f.encrypt(shard)
            return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        ms_samples = [s * 1000 for s in samples]
        r = make_result(name, CATEGORY, ms_samples, "ms",
                        {"n_shards": n_shards, "shard_size_kb": shard_size // 1024,
                         "file_size": "4MB"})
        self.suite.add(r)
        return r

    # ------------------------------------------------------------------
    # Parallel 12 shards vs sequential
    # ------------------------------------------------------------------

    def test_parallel_12_shards_vs_sequential(self) -> List[BenchResult]:
        results = []
        console.print("  [dim]parallel_12_shards_vs_sequential...[/dim]")

        file_size = 4 * 1024 * 1024
        n_shards = 12
        shard_size = file_size // 8
        shards = [os.urandom(shard_size) for _ in range(n_shards)]
        key = fernet_key()

        def encrypt_one(data: bytes) -> bytes:
            return Fernet(key).encrypt(data)

        # Sequential
        seq_samples: List[float] = []

        def _seq():
            t0 = time.perf_counter()
            for shard in shards:
                encrypt_one(shard)
            return time.perf_counter() - t0

        _seq()  # warmup
        for _ in range(self.iterations):
            seq_samples.append(_seq())

        r_seq = make_result(
            "parallel_12_shards [sequential]", CATEGORY,
            [s * 1000 for s in seq_samples], "ms",
            {"mode": "sequential", "shards": n_shards}
        )
        self.suite.add(r_seq)
        results.append(r_seq)

        # Parallel (thread pool)
        par_samples: List[float] = []

        def _par():
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(encrypt_one, s) for s in shards]
                for fut in as_completed(futs):
                    fut.result()
            return time.perf_counter() - t0

        _par()  # warmup
        for _ in range(self.iterations):
            par_samples.append(_par())

        seq_mean = statistics.mean(seq_samples)
        par_mean = statistics.mean(par_samples)
        speedup = seq_mean / par_mean if par_mean > 0 else 1.0

        r_par = make_result(
            "parallel_12_shards [4 threads]", CATEGORY,
            [s * 1000 for s in par_samples], "ms",
            {"mode": "parallel", "shards": n_shards, "speedup": round(speedup, 2)}
        )
        self.suite.add(r_par)
        results.append(r_par)

        return results

    # ------------------------------------------------------------------
    # HMAC verification overhead (tampered token)
    # ------------------------------------------------------------------

    def test_hmac_verification_overhead(self) -> List[BenchResult]:
        results = []
        console.print("  [dim]hmac_verification_overhead...[/dim]")

        size = 512 * 1024
        plaintext = os.urandom(size)
        key = fernet_key()
        f = Fernet(key)
        valid_token = f.encrypt(plaintext)

        # Tamper: flip the last byte of the token
        token_bytes = bytearray(valid_token)
        token_bytes[-1] ^= 0xFF
        tampered_token = bytes(token_bytes)

        # Valid decrypt
        name_valid = "hmac_verification_overhead [valid_token, 512KB]"
        valid_samples: List[float] = []
        f.decrypt(valid_token)  # warmup
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            f.decrypt(valid_token)
            valid_samples.append(time.perf_counter() - t0)
        r_valid = make_result(name_valid, CATEGORY,
                              [s * 1000 for s in valid_samples], "ms",
                              {"token": "valid", "size": "512KB"})
        self.suite.add(r_valid)
        results.append(r_valid)

        # Tampered — expect InvalidToken
        name_tampered = "hmac_verification_overhead [tampered_token, 512KB]"
        tampered_samples: List[float] = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            try:
                f.decrypt(tampered_token)
            except (InvalidToken, Exception):
                pass
            tampered_samples.append(time.perf_counter() - t0)

        valid_mean = statistics.mean(valid_samples) * 1000
        tampered_mean = statistics.mean(tampered_samples) * 1000
        overhead_ms = tampered_mean - valid_mean   # should be ~0 or negative (fail-fast)

        r_tampered = make_result(name_tampered, CATEGORY,
                                 [s * 1000 for s in tampered_samples], "ms",
                                 {"token": "tampered",
                                  "overhead_vs_valid_ms": round(overhead_ms, 3),
                                  "size": "512KB"})
        self.suite.add(r_tampered)
        results.append(r_tampered)

        return results

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self) -> BenchSuite:
        console.print(f"\n[bold cyan]Running Crypto benchmarks[/bold cyan]")
        self.test_fernet_encrypt_throughput()
        self.test_fernet_decrypt_throughput()
        self.test_fernet_encrypt_decrypt_roundtrip_mbps()
        self.test_parallel_encrypt_speedup()
        self.test_key_generation_ops_per_sec()
        self.test_encryption_overhead_bytes()
        self.test_fernet_vs_raw_aes_gcm()
        self.test_encrypt_12_shards_total_time()
        self.test_parallel_12_shards_vs_sequential()
        self.test_hmac_verification_overhead()
        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from benchmarks.common import BenchSuite
    suite = BenchSuite("Crypto Benchmarks")
    runner = BenchmarkCrypto(suite)
    runner.run_all()
    suite.print_table()
