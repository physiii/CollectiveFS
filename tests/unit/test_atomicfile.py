import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.atomicfile import write_json_atomic


def test_metadata_never_disappears_during_concurrent_upload_updates(tmp_path):
    path = tmp_path / "file.json"
    payloads = [{"status": "stored", "revision": i, "chunks": [str(i) * 4000]} for i in range(80)]
    write_json_atomic(path, payloads[0])
    done = threading.Event()
    reads = []

    def reader():
        while not done.is_set():
            value = json.loads(path.read_text())
            assert value in payloads
            reads.append(value["revision"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        reading = pool.submit(reader)
        try:
            list(pool.map(lambda value: write_json_atomic(path, value), payloads))
        finally:
            done.set()
        reading.result()
    assert reads
    assert list(tmp_path.iterdir()) == [path]


def test_failed_serialization_preserves_existing_metadata(tmp_path):
    path = tmp_path / "file.json"
    write_json_atomic(path, {"previous": "valid"})
    with pytest.raises(TypeError):
        write_json_atomic(path, {"invalid": object()})
    assert json.loads(path.read_text()) == {"previous": "valid"}
    assert list(tmp_path.iterdir()) == [path]
