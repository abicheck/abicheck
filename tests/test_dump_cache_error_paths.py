"""The dump cache's failure branches: every one degrades to a miss or to a
distinct key, never to a caller-visible error or a silently shared entry.

Each test forces one failure and asserts the documented fallback. Together
they keep the key from ever depending on a file it could not read without
saying so.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.compare import manifest_tu_scope
from abicheck.compile_context import CompileContext
from abicheck.dump_manifest import parse_manifest
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.dump.cache import api, key, store
from tests._dump_cache_request import dump_request


def _base(tmp_path: Path) -> Path:
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"\x7fELF errors")
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "api.h").write_text("int api(void);\n")
    return binary


def _snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="liberr.so.1",
        version="1.0",
        functions=[
            Function(
                name="err_init",
                mangled="_Z8err_initv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


# ---- key derivation: an input it cannot read is hashed as a marker ----------


def test_a_missing_header_input_hashes_as_a_marker(tmp_path: Path) -> None:
    binary = _base(tmp_path)
    present = dump_request(path=binary, headers=[tmp_path / "inc" / "api.h"])
    missing = dump_request(path=binary, headers=[tmp_path / "inc" / "gone.h"])
    key_present = api._cache_key_for(present)
    key_missing = api._cache_key_for(missing)
    assert key_present and key_missing
    assert key_present != key_missing


def test_an_unreadable_header_directory_is_marked_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _base(tmp_path)
    request = dump_request(
        path=binary, headers=[tmp_path / "inc"], includes=[tmp_path / "inc"]
    )
    base_key = api._cache_key_for(request)
    real_walk = key.iter_cache_header_files

    def failing_walk(path: Path):  # type: ignore[no-untyped-def]
        raise OSError("permission denied")

    monkeypatch.setattr(key, "iter_cache_header_files", failing_walk)
    unreadable = api._cache_key_for(request)
    monkeypatch.setattr(key, "iter_cache_header_files", real_walk)
    assert unreadable
    assert unreadable != base_key


def test_an_unreadable_include_directory_is_marked_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _base(tmp_path)
    request = dump_request(path=binary, headers=None, includes=[tmp_path / "inc"])
    base_key = api._cache_key_for(request)
    real_walk = key.iter_cache_header_files

    def failing_walk(path: Path):  # type: ignore[no-untyped-def]
        raise OSError("permission denied")

    monkeypatch.setattr(key, "iter_cache_header_files", failing_walk)
    unreadable = api._cache_key_for(request)
    monkeypatch.setattr(key, "iter_cache_header_files", real_walk)
    assert unreadable != base_key


def test_a_failing_compiler_resolution_is_folded_in_as_a_distinct_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _base(tmp_path)
    request = dump_request(
        path=binary,
        headers=[tmp_path / "inc" / "api.h"],
        includes=[tmp_path / "inc"],
        header_backend="castxml",
    )
    from abicheck.extract.headers import ast_config

    base_key = api._cache_key_for(request)

    def no_compiler(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no compiler on PATH")

    monkeypatch.setattr(ast_config, "_resolve_compiler_binary", no_compiler)
    unresolved = api._cache_key_for(request)
    assert unresolved
    assert unresolved != base_key


def test_a_defensive_compile_context_miss_yields_the_empty_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate already vetted the compile context; the derivation re-checks it
    and must never key a request it cannot fully read."""
    binary = _base(tmp_path)
    request = dump_request(
        path=binary,
        headers=[tmp_path / "inc" / "api.h"],
        compile=CompileContext(gcc_option_tokens=("-DX=1",)),
    )
    real = api.compile_context_header_inputs
    calls: list[int] = []

    def gate_passes_then_unreadable(compile: object | None):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(compile) if len(calls) == 1 else None

    monkeypatch.setattr(
        api, "compile_context_header_inputs", gate_passes_then_unreadable
    )
    assert api._cache_key_for(request) == ""


# ---- manifest scope: a path that cannot be made relative falls back ---------


def test_a_manifest_path_with_no_relative_form_uses_the_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _base(tmp_path)
    manifest = parse_manifest(
        "roots: [inc/api.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [inc/api.h]\n",
        base_dir=tmp_path,
        source="<errors>",
    )

    def no_relative(*args: object, **kwargs: object) -> str:
        raise ValueError("path is on another drive")

    monkeypatch.setattr(manifest_tu_scope.os.path, "relpath", no_relative)
    encoded = manifest_tu_scope.manifest_tu_scope_field(manifest)
    assert "api.h" in encoded


# ---- the on-disk store: every filesystem failure is a miss or a skip -------


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "snapshots"
    monkeypatch.setattr(store, "_CACHE_DIR", directory)
    return directory


def test_a_failed_recency_touch_still_returns_the_hit(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _base(tmp_path)
    store._store_key(_snap(), "t" * 32, binary)

    def no_touch(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("read-only cache")

    monkeypatch.setattr(Path, "touch", no_touch)
    hit = store._lookup_key("t" * 32, binary)
    assert hit is not None and hit.library == "liberr.so.1"


def test_a_failed_legacy_removal_does_not_fail_the_store(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _base(tmp_path)
    cache_dir.mkdir(parents=True)
    (cache_dir / ("u" * 32 + ".json")).write_text("{}")

    def no_unlink(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("busy")

    monkeypatch.setattr(Path, "unlink", no_unlink)
    store._store_key(_snap(), "u" * 32, binary)
    assert (cache_dir / ("u" * 32 + ".json.zst")).exists()


def test_a_failed_eviction_removal_is_skipped(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ENTRIES", 1)
    cache_dir.mkdir(parents=True)
    for index, name in enumerate(["a", "b"]):
        entry = cache_dir / f"{name}.json.zst"
        entry.write_bytes(b"x")
        os.utime(entry, (1_000_000_000 + index, 1_000_000_000 + index))

    def no_unlink(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("busy")

    monkeypatch.setattr(Path, "unlink", no_unlink)
    store._evict_if_needed()  # must not raise; the entries simply stay
    assert len(list(cache_dir.iterdir())) == 2


def test_eviction_on_an_unreadable_directory_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "snapshots"
    cache_dir.mkdir()
    monkeypatch.setattr(store, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(store, "MAX_ENTRIES", 1)
    entries = [cache_dir / f"{name}.json.zst" for name in ("a", "b", "c")]
    for entry in entries:
        entry.write_bytes(b"x")

    def no_glob(self: Path, pattern: str):  # type: ignore[no-untyped-def]
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "glob", no_glob)
    store._evict_if_needed()  # over the cap, yet nothing can be listed
    # Nothing was listed, so nothing was removed.
    assert all(entry.exists() for entry in entries)
