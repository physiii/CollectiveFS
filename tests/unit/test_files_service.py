"""Unit tests for the folder tree built over flat file metadata."""

import pytest

from api.files_service import (
    FileTreeError,
    FolderStore,
    build_tree,
    collect_folders,
    list_directory,
    normalize_folder,
    validate_move,
)

pytestmark = pytest.mark.unit


def make_file(file_id, name, folder=None, size=100, chunks=None):
    return {
        "id": file_id,
        "name": name,
        "folder": folder,
        "size": size,
        "chunks": len(chunks or []),
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "stored",
        "chunk_list": chunks or [],
    }


# ── path normalisation ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("/", ""),
        (".", ""),
        ("a", "a"),
        ("/a/b/", "a/b"),
        ("a//b", "a/b"),
        ("a/./b", "a/b"),
        ("  a / b ", "a / b".replace(" ", "")),
    ],
)
def test_normalize_folder(raw, expected):
    assert normalize_folder(raw) == expected


@pytest.mark.parametrize("raw", ["../etc", "a/../../b", "a/b<c", "a/b\x00c"])
def test_normalize_folder_rejects_traversal_and_control_chars(raw):
    with pytest.raises(FileTreeError):
        normalize_folder(raw)


def test_normalize_folder_caps_depth_and_segment_length():
    with pytest.raises(FileTreeError, match="24 levels"):
        normalize_folder("/".join(["a"] * 25))
    with pytest.raises(FileTreeError, match="128 characters"):
        normalize_folder("x" * 129)


# ── explicit folder store ───────────────────────────────────────────


def test_folder_store_materialises_ancestors(tmp_path):
    store = FolderStore(tmp_path)
    assert store.add("a/b/c") == ["a", "a/b", "a/b/c"]


def test_folder_store_persists(tmp_path):
    FolderStore(tmp_path).add("keep/me")
    assert FolderStore(tmp_path).load() == ["keep", "keep/me"]


def test_folder_store_remove_takes_descendants(tmp_path):
    store = FolderStore(tmp_path)
    store.add("a/b/c")
    store.add("d")
    assert store.remove("a/b") == ["a", "d"]


def test_folder_store_ignores_corrupt_file(tmp_path):
    (tmp_path / "folders.json").write_text("nope")
    assert FolderStore(tmp_path).load() == []


# ── tree assembly ───────────────────────────────────────────────────


def test_collect_folders_unions_implied_and_explicit():
    files = [make_file("1", "a.txt", "docs/reports")]
    assert collect_folders(files, ["empty"]) == ["docs", "docs/reports", "empty"]


def test_tree_nests_and_rolls_up_sizes():
    files = [
        make_file("1", "a.txt", "docs", size=100),
        make_file("2", "b.txt", "docs/reports", size=250),
        make_file("3", "c.txt", None, size=7),
    ]
    tree = build_tree(files, [])

    assert tree["total_files"] == 3
    assert tree["total_size"] == 357

    root = tree["tree"]
    assert root["file_count"] == 3
    assert root["size"] == 357

    docs = next(child for child in root["children"] if child["name"] == "docs")
    assert docs["file_count"] == 2
    assert docs["size"] == 350

    reports = docs["children"][0]
    assert reports["name"] == "reports"
    assert reports["size"] == 250


def test_tree_counts_available_shards(tmp_path):
    present = tmp_path / "shard0"
    present.write_bytes(b"x")
    chunks = [{"path": str(present)}, {"path": str(tmp_path / "gone")}]
    tree = build_tree([make_file("1", "a.bin", None, chunks=chunks)], [])

    entry = tree["files"][0]
    assert entry["shards_total"] == 2
    assert entry["shards_available"] == 1


def test_in_flight_status_overlays_stored_status():
    tree = build_tree(
        [make_file("1", "a.txt")],
        [],
        {"1": {"status": "processing", "progress": 0.5}},
    )
    assert tree["files"][0]["status"] == "processing"
    assert tree["files"][0]["progress"] == 0.5


def test_file_with_an_invalid_folder_lands_at_the_root():
    tree = build_tree([make_file("1", "a.txt", "../escape")], [])
    assert tree["files"][0]["folder"] == ""


def test_empty_explicit_folder_appears_in_the_tree():
    tree = build_tree([], ["scratch"])
    assert [child["name"] for child in tree["tree"]["children"]] == ["scratch"]


# ── directory listing ───────────────────────────────────────────────


def test_list_directory_returns_direct_children_only():
    files = [
        make_file("1", "top.txt", None),
        make_file("2", "inside.txt", "docs"),
        make_file("3", "deep.txt", "docs/reports"),
    ]
    tree = build_tree(files, [])

    root = list_directory(tree, "")
    assert [item["name"] for item in root["files"]] == ["top.txt"]
    assert [item["name"] for item in root["folders"]] == ["docs"]

    docs = list_directory(tree, "docs")
    assert [item["name"] for item in docs["files"]] == ["inside.txt"]
    assert [item["name"] for item in docs["folders"]] == ["reports"]


def test_list_directory_breadcrumbs():
    tree = build_tree([make_file("1", "a.txt", "x/y/z")], [])
    crumbs = list_directory(tree, "x/y/z")["breadcrumbs"]
    assert [crumb["name"] for crumb in crumbs] == ["All Files", "x", "y", "z"]
    assert [crumb["path"] for crumb in crumbs] == ["", "x", "x/y", "x/y/z"]


def test_list_directory_rejects_unknown_folder():
    tree = build_tree([], [])
    with pytest.raises(FileTreeError, match="does not exist"):
        list_directory(tree, "nowhere")


# ── move / rename validation ────────────────────────────────────────


def test_move_allows_a_free_name():
    existing = [make_file("1", "a.txt", "docs")]
    assert validate_move("b.txt", "docs", existing, "1") == ("b.txt", "docs")


def test_move_rejects_a_sibling_collision():
    existing = [make_file("1", "a.txt", "docs"), make_file("2", "b.txt", "docs")]
    with pytest.raises(FileTreeError, match="already exists"):
        validate_move("b.txt", None, existing, "1")


def test_same_name_in_a_different_folder_is_fine():
    existing = [make_file("1", "a.txt", "docs"), make_file("2", "a.txt", "other")]
    assert validate_move(None, "other/sub", existing, "1") == (None, "other/sub")


@pytest.mark.parametrize("name", ["", "  ", "a/b", "a\x00b", "x" * 256])
def test_move_rejects_bad_names(name):
    with pytest.raises(FileTreeError):
        validate_move(name, None, [make_file("1", "a.txt")], "1")
