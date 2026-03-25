"""
bench_io.py — Read/Write I/O benchmarks for every stage of the CollectiveFS pipeline.

Measures raw disk I/O vs. encoder-only vs. encode+encrypt vs. full pipeline,
for file sizes: 64KB, 256KB, 1MB, 4MB, 16MB, 64MB.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import List

from cryptography.fernet import Fernet

from benchmarks.common import (
    BenchResult,
    BenchSuite,
    FILE_SIZES,
    encoder_available,
    decoder_available,
    fernet_key,
    make_random_file,
    make_result,
    run_decoder,
    run_encoder,
    skipped_result,
    throughput_mbps,
    timed_with_warmup,
    console,
)

ITERATIONS = 5
DATA_SHARDS = 8
PAR_SHARDS = 4
CATEGORY = "I/O Pipeline"


class BenchmarkIO:
    """Full pipeline I/O benchmarks."""

    def __init__(self, suite: BenchSuite, iterations: int = ITERATIONS):
        self.suite = suite
        self.iterations = iterations

    # ------------------------------------------------------------------
    # Raw disk write
    # ------------------------------------------------------------------

    def test_raw_disk_write(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"raw_disk_write [{size_label}]"
        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                dst = Path(tmp) / "raw_write.bin"
                data = os.urandom(size_bytes)
                t0 = time.perf_counter()
                dst.write_bytes(data)
                return time.perf_counter() - t0

        # warmup
        _run()
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s", {"file_size": size_label})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Raw disk read
    # ------------------------------------------------------------------

    def test_raw_disk_read(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"raw_disk_read [{size_label}]"
        samples: List[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            src = make_random_file(Path(tmp) / "raw_read.bin", size_bytes)

            def _run():
                t0 = time.perf_counter()
                _ = src.read_bytes()
                return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s", {"file_size": size_label})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # CFS encode write (encoder binary only)
    # ------------------------------------------------------------------

    def test_cfs_encode_write(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"cfs_encode_write [{size_label}]"
        if not encoder_available():
            result = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
            self.suite.add(result)
            return result

        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                out_dir = Path(tmp) / "shards"
                elapsed = run_encoder(src, out_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                return elapsed

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s",
                             {"file_size": size_label, "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # CFS encode + Fernet encrypt each shard
    # ------------------------------------------------------------------

    def test_cfs_encode_decrypt_write(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"cfs_encode_encrypt_write [{size_label}]"
        if not encoder_available():
            result = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
            self.suite.add(result)
            return result

        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                shard_dir = Path(tmp) / "shards"
                enc_dir = Path(tmp) / "enc_shards"
                enc_dir.mkdir()
                t0 = time.perf_counter()
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                key = fernet_key()
                f = Fernet(key)
                for shard in sorted(shard_dir.iterdir()):
                    ct = f.encrypt(shard.read_bytes())
                    (enc_dir / shard.name).write_bytes(ct)
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s",
                             {"file_size": size_label, "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Full write pipeline (encode + encrypt, no network)
    # ------------------------------------------------------------------

    def test_cfs_full_write_pipeline(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"cfs_full_write_pipeline [{size_label}]"
        if not encoder_available():
            result = skipped_result(name, CATEGORY, "encoder binary not found", "MB/s")
            self.suite.add(result)
            return result

        samples: List[float] = []
        key = fernet_key()

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                src = make_random_file(Path(tmp) / "src.bin", size_bytes)
                shard_dir = Path(tmp) / "shards"
                enc_dir = Path(tmp) / "enc_shards"
                enc_dir.mkdir()
                t0 = time.perf_counter()
                # Stage 1: encode
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                # Stage 2: encrypt every shard
                f = Fernet(key)
                for shard in sorted(shard_dir.iterdir()):
                    ct = f.encrypt(shard.read_bytes())
                    (enc_dir / shard.name).write_bytes(ct)
                return time.perf_counter() - t0

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s",
                             {"file_size": size_label, "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Full read pipeline (decrypt + decode, no network)
    # ------------------------------------------------------------------

    def test_cfs_decode_read(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"cfs_decode_read [{size_label}]"
        if not encoder_available() or not decoder_available():
            result = skipped_result(name, CATEGORY, "encoder/decoder binary not found", "MB/s")
            self.suite.add(result)
            return result

        samples: List[float] = []

        # Build a fixed encrypted shard set once, then repeatedly decrypt+decode
        _tmp_holder = tempfile.mkdtemp()
        try:
            src = make_random_file(Path(_tmp_holder) / "src.bin", size_bytes)
            shard_dir = Path(_tmp_holder) / "shards"
            enc_dir = Path(_tmp_holder) / "enc_shards"
            enc_dir.mkdir()
            key = fernet_key()
            run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
            f = Fernet(key)
            for shard in sorted(shard_dir.iterdir()):
                ct = f.encrypt(shard.read_bytes())
                (enc_dir / shard.name).write_bytes(ct)

            def _run():
                with tempfile.TemporaryDirectory() as tmp:
                    dec_shard_dir = Path(tmp) / "dec_shards"
                    dec_shard_dir.mkdir()
                    out_dir = Path(tmp) / "out"
                    t0 = time.perf_counter()
                    # Stage 1: decrypt each shard
                    fobj = Fernet(key)
                    for enc_shard in sorted(enc_dir.iterdir()):
                        pt = fobj.decrypt(enc_shard.read_bytes())
                        (dec_shard_dir / enc_shard.name).write_bytes(pt)
                    # Stage 2: decode
                    run_decoder(dec_shard_dir, out_dir, "src.bin",
                                data=DATA_SHARDS, par=PAR_SHARDS)
                    return time.perf_counter() - t0

            _run()  # warmup
            for _ in range(self.iterations):
                samples.append(_run())
        finally:
            shutil.rmtree(_tmp_holder, ignore_errors=True)

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s",
                             {"file_size": size_label, "data": DATA_SHARDS, "par": PAR_SHARDS})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Full roundtrip (encode+encrypt → decrypt+decode → verify SHA256)
    # ------------------------------------------------------------------

    def test_cfs_full_roundtrip(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"cfs_full_roundtrip [{size_label}]"
        if not encoder_available() or not decoder_available():
            result = skipped_result(name, CATEGORY, "encoder/decoder binary not found", "MB/s")
            self.suite.add(result)
            return result

        samples: List[float] = []

        def _run():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                original_sha = hashlib.sha256(src.read_bytes()).hexdigest()

                key = fernet_key()
                f = Fernet(key)

                t0 = time.perf_counter()

                # Write path
                shard_dir = tmpdir / "shards"
                enc_dir = tmpdir / "enc_shards"
                enc_dir.mkdir()
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                for shard in sorted(shard_dir.iterdir()):
                    ct = f.encrypt(shard.read_bytes())
                    (enc_dir / shard.name).write_bytes(ct)

                # Read path
                dec_shard_dir = tmpdir / "dec_shards"
                dec_shard_dir.mkdir()
                out_dir = tmpdir / "out"
                fobj = Fernet(key)
                for enc_shard in sorted(enc_dir.iterdir()):
                    pt = fobj.decrypt(enc_shard.read_bytes())
                    (dec_shard_dir / enc_shard.name).write_bytes(pt)
                run_decoder(dec_shard_dir, out_dir, "src.bin",
                            data=DATA_SHARDS, par=PAR_SHARDS)

                elapsed = time.perf_counter() - t0

                # Verify integrity
                recovered = (out_dir / "src.bin").read_bytes()
                recovered_sha = hashlib.sha256(recovered).hexdigest()
                assert recovered_sha == original_sha, "SHA256 mismatch after roundtrip!"

                return elapsed

        _run()  # warmup
        for _ in range(self.iterations):
            samples.append(_run())

        mbps = [throughput_mbps(size_bytes, s) for s in samples]
        result = make_result(name, CATEGORY, mbps, "MB/s",
                             {"file_size": size_label, "verified": "sha256"})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Pipeline overhead % vs raw write
    # ------------------------------------------------------------------

    def test_pipeline_overhead_pct(self, size_label: str, size_bytes: int) -> BenchResult:
        name = f"pipeline_overhead_pct [{size_label}]"
        if not encoder_available():
            result = skipped_result(name, CATEGORY, "encoder binary not found", "%")
            self.suite.add(result)
            return result

        def _raw():
            with tempfile.TemporaryDirectory() as tmp:
                dst = Path(tmp) / "raw.bin"
                data = os.urandom(size_bytes)
                t0 = time.perf_counter()
                dst.write_bytes(data)
                return time.perf_counter() - t0

        def _pipeline():
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = make_random_file(tmpdir / "src.bin", size_bytes)
                shard_dir = tmpdir / "shards"
                enc_dir = tmpdir / "enc_shards"
                enc_dir.mkdir()
                key = fernet_key()
                f = Fernet(key)
                t0 = time.perf_counter()
                run_encoder(src, shard_dir, data=DATA_SHARDS, par=PAR_SHARDS)
                for shard in sorted(shard_dir.iterdir()):
                    ct = f.encrypt(shard.read_bytes())
                    (enc_dir / shard.name).write_bytes(ct)
                return time.perf_counter() - t0

        # warmup both
        _raw()
        _pipeline()

        raw_samples = [_raw() for _ in range(self.iterations)]
        pipe_samples = [_pipeline() for _ in range(self.iterations)]

        import statistics
        raw_mean = statistics.mean(raw_samples)
        pipe_mean = statistics.mean(pipe_samples)
        overhead_pct = (pipe_mean / raw_mean - 1.0) * 100.0 if raw_mean > 0 else 0.0

        overhead_samples = [
            (p / r - 1.0) * 100.0
            for p, r in zip(pipe_samples, raw_samples)
        ]
        result = make_result(name, CATEGORY, overhead_samples, "%",
                             {"file_size": size_label,
                              "raw_mean_ms": round(raw_mean * 1000, 2),
                              "pipe_mean_ms": round(pipe_mean * 1000, 2)})
        self.suite.add(result)
        return result

    # ------------------------------------------------------------------
    # Master runner
    # ------------------------------------------------------------------

    def run_all(self, sizes: dict | None = None) -> BenchSuite:
        if sizes is None:
            sizes = FILE_SIZES

        console.print(f"\n[bold cyan]Running I/O benchmarks ({len(sizes)} file sizes × {self.iterations} iters)[/bold cyan]")

        for label, sz in sizes.items():
            console.print(f"  [yellow]→ {label}[/yellow]")
            self.test_raw_disk_write(label, sz)
            self.test_raw_disk_read(label, sz)
            self.test_cfs_encode_write(label, sz)
            self.test_cfs_encode_decrypt_write(label, sz)
            self.test_cfs_full_write_pipeline(label, sz)
            self.test_cfs_decode_read(label, sz)
            self.test_cfs_full_roundtrip(label, sz)
            self.test_pipeline_overhead_pct(label, sz)

        return self.suite


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    suite = BenchSuite("I/O Benchmarks")
    runner = BenchmarkIO(suite)
    runner.run_all()
    suite.print_table()
