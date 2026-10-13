"""Branch coverage for the dump cache's on-disk store (``workflows/dump/cache/store.py``).

The store is the only place entries are read, written and evicted. Each test
pins one branch: the cache-directory fallbacks, the write policy, a warm hit,
a corrupt or legacy entry, a failed write that must never reach the caller,
and LRU eviction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.dump.cache import store


def _snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libstore.so.1",
        version="1.0",
        functions=[
            Function(
                name="store_init",
                mangled="_Z10store_initv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "snapshots"
    monkeypatch.setattr(store, "_CACHE_DIR", directory)
    return directory


# ---- the cache directory ---------------------------------------------------


def test_cache_dir_honours_xdg_cache_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert store._get_cache_dir() == tmp_path / "xdg" / "abi_check" / "snapshots"


def test_cache_dir_falls_back_to_home_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert (
        store._get_cache_dir()
        == tmp_path / "home" / ".cache" / "abi_check" / "snapshots"
    )


def test_cache_dir_falls_back_to_tempdir_without_a_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    def no_home() -> Path:
        raise RuntimeError("no home directory")

    monkeypatch.setattr(Path, "home", no_home)
    monkeypatch.setattr(store.tempfile, "gettempdir", lambda: str(tmp_path))
    assert store._get_cache_dir() == tmp_path / "abi_check" / "snapshots"


# ---- the write policy ------------------------------------------------------


@pytest.mark.parametrize(("value", "enabled"), [("1", True), ("0", False)])
def test_write_policy_follows_the_env_flag(
    monkeypatch: pytest.MonkeyPatch, value: str, enabled: bool
) -> None:
    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", value)
    assert store._write_enabled() is enabled


# ---- lookup and store ------------------------------------------------------


def test_an_empty_key_is_never_looked_up_or_stored(
    cache_dir: Path, tmp_path: Path
) -> None:
    assert store._lookup_key("", tmp_path / "lib.so") is None
    store._store_key(_snap(), "", tmp_path / "lib.so")
    assert not cache_dir.exists()


def test_a_stored_entry_round_trips_as_a_warm_hit(
    cache_dir: Path, tmp_path: Path
) -> None:
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"\x7fELF")
    store._store_key(_snap(), "k" * 32, binary)
    assert (cache_dir / ("k" * 32 + ".json.zst")).exists()
    hit = store._lookup_key("k" * 32, binary)
    assert hit is not None
    assert hit.library == "libstore.so.1"


def test_a_miss_returns_none(cache_dir: Path, tmp_path: Path) -> None:
    assert store._lookup_key("m" * 32, tmp_path / "lib.so") is None


def test_a_corrupt_compressed_entry_is_a_miss_not_an_error(
    cache_dir: Path, tmp_path: Path
) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / ("c" * 32 + ".json.zst")).write_bytes(b"not a zstd frame")
    assert store._lookup_key("c" * 32, tmp_path / "lib.so") is None


def test_a_legacy_plain_json_entry_is_still_read(
    cache_dir: Path, tmp_path: Path
) -> None:
    binary = tmp_path / "lib.so"
    store._store_key(_snap(), "l" * 32, binary)
    compressed = cache_dir / ("l" * 32 + ".json.zst")
    assert compressed.exists()
    from abicheck.serialization import write_snapshot

    write_snapshot(_snap(), cache_dir / ("l" * 32 + ".json"))
    compressed.unlink()
    hit = store._lookup_key("l" * 32, binary)
    assert hit is not None
    assert hit.library == "libstore.so.1"


def test_storing_over_a_legacy_entry_removes_the_legacy_file(
    cache_dir: Path, tmp_path: Path
) -> None:
    cache_dir.mkdir(parents=True)
    legacy = cache_dir / ("s" * 32 + ".json")
    legacy.write_text("{}")
    store._store_key(_snap(), "s" * 32, tmp_path / "lib.so")
    assert not legacy.exists()
    assert (cache_dir / ("s" * 32 + ".json.zst")).exists()


def test_a_failed_write_never_reaches_the_caller(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import abicheck.serialization as serialization

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(serialization, "write_snapshot", boom)
    # Caching is an optimisation over a dump that already succeeded.
    store._store_key(_snap(), "f" * 32, tmp_path / "lib.so")
    assert not (cache_dir / ("f" * 32 + ".json.zst")).exists()


def test_a_hit_refreshes_the_entry_for_lru(cache_dir: Path, tmp_path: Path) -> None:
    binary = tmp_path / "lib.so"
    store._store_key(_snap(), "h" * 32, binary)
    entry = cache_dir / ("h" * 32 + ".json.zst")
    os.utime(entry, (1_000_000_000, 1_000_000_000))
    store._lookup_key("h" * 32, binary)
    assert entry.stat().st_mtime > 1_000_000_000


# ---- eviction --------------------------------------------------------------


def test_eviction_removes_the_oldest_entries_beyond_the_cap(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 2)
    cache_dir.mkdir(parents=True)
    for index, name in enumerate(["old", "mid", "new"]):
        entry = cache_dir / f"{name}.json.zst"
        entry.write_bytes(b"x")
        os.utime(entry, (1_000_000_000 + index, 1_000_000_000 + index))
    store._evict_if_needed()
    remaining = sorted(p.name for p in cache_dir.iterdir())
    assert remaining == ["mid.json.zst", "new.json.zst"]


def test_eviction_is_a_no_op_under_the_cap(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 5)
    cache_dir.mkdir(parents=True)
    (cache_dir / "only.json.zst").write_bytes(b"x")
    store._evict_if_needed()
    assert (cache_dir / "only.json.zst").exists()


def test_eviction_tolerates_a_missing_cache_directory(cache_dir: Path) -> None:
    assert not cache_dir.exists()
    store._evict_if_needed()  # glob on a missing directory: nothing to evict


def test_safe_mtime_reads_zero_for_a_vanished_file(tmp_path: Path) -> None:
    assert store._safe_mtime(tmp_path / "gone.json.zst") == 0.0
