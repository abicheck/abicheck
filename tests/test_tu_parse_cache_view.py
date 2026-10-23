"""The per-TU cache as a view over the one per-invocation parse store.

Plan ``l4-l2-extraction-convergence`` Phase 6. :class:`SourceAbiCache` stores its
dumps through :class:`~abicheck.storage.tu_parse_store.TuParseStore`, and its key
is built from the parsed :class:`CompilerInvocation`, the tool identity and the
input digests. Two properties are pinned here: a cache hit reproduces the cold
run exactly, and changing any key input misses.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit, Target
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_replay import (
    SourceAbiCache,
    _replay_cache_lookup,
    compute_tu_cache_key,
    run_source_replay,
)
from abicheck.storage.tu_parse_store import TuParseStore, content_key


class _CountingExtractor:
    """Returns one canned declaration per TU and records which TUs it parsed."""

    name = "fake-source"
    version = "9.9"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, compile_unit, *, public_header_roots, target_id=""):  # type: ignore[no-untyped-def]
        self.calls.append(compile_unit.id)
        entity = SourceEntity(
            id=f"id::{compile_unit.id}",
            kind="function",
            qualified_name="fn_a",
            mangled_name="_Z4fn_av",
            source_location=SourceLocation(
                path="include/foo.h", origin="PUBLIC_HEADER"
            ),
            visibility="public_header",
            api_relevant=True,
        )
        return SourceAbiTu(
            tu_id=compile_unit.id,
            target_id=target_id or compile_unit.target_id,
            extractor={"name": self.name, "version": self.version},
            source=compile_unit.source,
            public_header_roots=list(public_header_roots),
            functions=[entity],
            read_files=[compile_unit.source],
        )


def _spec(tmp_path: Path) -> dict[str, Any]:
    """A mutable description of one cacheable TU: its files, its key arguments and its unit."""
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    hdr = tmp_path / "foo.h"
    hdr.write_text("int f();\n")
    return {
        "src": src,
        "hdr": hdr,
        "name": "fake-source",
        "version": "9.9",
        "roots": [str(hdr)],
        "override": None,
        "unit": {
            "compiler": "g++",
            "language": "CXX",
            "standard": "c++17",
            "defines": {"FOO": "1"},
            "directory": str(tmp_path),
        },
        "argv": None,
    }


def _key_of(spec: dict[str, Any]) -> str | None:
    unit = spec["unit"]
    argv = spec["argv"] or [unit.get("compiler", "g++"), "-c", str(spec["src"])]
    cu = CompileUnit(
        id="cu://a",
        source=str(spec["src"]),
        target_id="target://t",
        argv=argv,
        **unit,
    )
    return compute_tu_cache_key(
        extractor_name=spec["name"],
        extractor_version=spec["version"],
        compile_unit=cu,
        public_header_roots=spec["roots"],
        compiler_override=spec["override"],
    )


def _mkdir(spec: dict[str, Any], name: str) -> str:
    d = Path(spec["unit"]["directory"]) / name
    d.mkdir(exist_ok=True)
    return str(d)


# -- equivalence: a cache hit reproduces the cold run -------------------------


def test_cache_hit_reproduces_cold_run(tmp_path: Path) -> None:
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    build = BuildEvidence(
        targets=[Target(id="target://t", public_headers=["foo.h"])],
        compile_units=[
            CompileUnit(
                id="cu://a",
                source=str(src),
                target_id="target://t",
                language="CXX",
                standard="c++17",
                defines={"FOO": "1"},
                directory=str(tmp_path),
            )
        ],
    )
    cache = SourceAbiCache(tmp_path / "cache")

    cold_extractor = _CountingExtractor()
    cold, cold_diagnostics = run_source_replay(
        build, cold_extractor, scope="full", public_header_roots=[], cache=cache
    )
    warm_extractor = _CountingExtractor()
    warm, warm_diagnostics = run_source_replay(
        build, warm_extractor, scope="full", public_header_roots=[], cache=cache
    )

    assert cold_extractor.calls == ["cu://a"]
    assert warm_extractor.calls == []
    assert (cache.hits, cache.misses) == (1, 1)
    # The facts are equal field for field. The only differences allowed are the
    # cache's own telemetry and wall-clock timing in ``coverage``.
    assert dataclasses.replace(warm, coverage={}) == dataclasses.replace(
        cold, coverage={}
    )
    assert _non_cache_coverage(warm) == _non_cache_coverage(cold)
    assert warm.coverage["cache_misses"] == 0
    assert cold.coverage["cache_misses"] == 1
    assert warm_diagnostics == cold_diagnostics


def _non_cache_coverage(surface: SourceAbiSurface) -> dict[str, Any]:
    """Coverage counts without the cache counters (``cache_*``) and timings (``*_s``)."""
    return {
        k: v
        for k, v in surface.coverage.items()
        if not k.startswith("cache_") and not k.endswith("_s")
    }


def test_stored_tu_round_trips_through_the_store(tmp_path: Path) -> None:
    cache = SourceAbiCache(tmp_path / "cache")
    tu = SourceAbiTu(
        tu_id="cu://a",
        functions=[SourceEntity(id="e", kind="function", qualified_name="f")],
        read_files=[],
    )
    cache.put("k", tu)
    assert cache.get("k") == tu


# -- key sensitivity: every key input misses when it changes -------------------

#: Each entry changes exactly one input of the key. The oracle is "the key changes":
#: a mutation that left the key alone would replay a stale dump under the new input.
MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "source_content": lambda s: s["src"].write_text("int b;\n"),
    "root_content": lambda s: s["hdr"].write_text("int g();\n"),
    "root_removed": lambda s: s.update(roots=[]),
    "extractor_name": lambda s: s.update(name="other-source"),
    "extractor_version": lambda s: s.update(version="10.0"),
    "compiler_override": lambda s: s.update(override="clang++"),
    "compiler": lambda s: s["unit"].update(compiler="clang++"),
    "language": lambda s: s["unit"].update(language="C"),
    "standard": lambda s: s["unit"].update(standard="c++20"),
    "define_value": lambda s: s["unit"].update(defines={"FOO": "2"}),
    "define_added": lambda s: s["unit"].update(defines={"FOO": "1", "BAR": ""}),
    "undefine": lambda s: s["unit"].update(undefines=["FOO"]),
    "include_dir": lambda s: s["unit"].update(include_paths=["inc"]),
    "system_include_dir": lambda s: s["unit"].update(
        system_include_paths=["/opt/sys/include"]
    ),
    "target_triple": lambda s: s["unit"].update(target_triple="aarch64-linux-gnu"),
    "sysroot": lambda s: s["unit"].update(sysroot="/opt/sysroot"),
    "abi_flag": lambda s: s["unit"].update(abi_relevant_flags=["-m32"]),
    "forced_include_in_argv": lambda s: s.update(
        argv=["g++", "-include", "pre.h", "-c", str(s["src"])]
    ),
    "cwd": lambda s: s["unit"].update(directory=_mkdir(s, "elsewhere")),
}


def test_base_key_is_computed_and_stable(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    first = _key_of(spec)
    assert first is not None
    assert _key_of(spec) == first


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_changing_a_key_input_misses(tmp_path: Path, name: str) -> None:
    spec = _spec(tmp_path)
    before = _key_of(spec)
    MUTATIONS[name](spec)
    after = _key_of(spec)
    assert before is not None and after is not None
    assert after != before


def test_unreadable_source_has_no_key(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec["src"].unlink()
    assert _key_of(spec) is None


# -- insensitivity: inputs the parse ignores must not split the cache ----------


def test_define_order_does_not_change_the_key(tmp_path: Path) -> None:
    # The parsed invocation is the same set of definitions, so the key is too.
    spec = _spec(tmp_path)
    spec["unit"]["defines"] = {"FOO": "1", "BAR": "2"}
    forward = _key_of(spec)
    spec["unit"]["defines"] = {"BAR": "2", "FOO": "1"}
    assert _key_of(spec) == forward


@pytest.mark.parametrize("output", ["build/liba.o", "other/liba.o"])
def test_output_path_does_not_change_the_key(tmp_path: Path, output: str) -> None:
    # The object file a unit writes never reaches the parse.
    spec = _spec(tmp_path)
    spec["unit"]["output"] = "build/liba.o"
    base = _key_of(spec)
    spec["unit"]["output"] = output
    assert _key_of(spec) == base


# -- the store itself ---------------------------------------------------------


def test_store_misses_when_a_recorded_dependency_changes(tmp_path: Path) -> None:
    dep = tmp_path / "dep.h"
    dep.write_text("#define N 1\n")
    store = TuParseStore(tmp_path / "store")
    store.publish("k", {"v": 1}, [str(dep)])
    assert store.lookup("k") == {"v": 1}
    dep.write_text("#define N 2\n")
    assert store.lookup("k") is None


def test_content_key_does_not_collide_across_part_boundaries() -> None:
    # A separator folded between parts keeps ("ab", "c") and ("a", "bc") apart.
    assert content_key(["ab", "c"]) != content_key(["a", "bc"])
    assert content_key(["ab", "c"]) == content_key(["ab", "c"])


def test_store_misses_when_a_recorded_dependency_vanishes(tmp_path: Path) -> None:
    dep = tmp_path / "dep.h"
    dep.write_text("#define N 1\n")
    store = TuParseStore(tmp_path / "store")
    store.publish("k", {"v": 1}, [str(dep)])
    dep.unlink()
    assert store.lookup("k") is None


def test_store_writes_nothing_when_a_dependency_is_unreadable(tmp_path: Path) -> None:
    dep = tmp_path / "late.h"
    store = TuParseStore(tmp_path / "store")
    store.publish("k", {"v": 1}, [str(dep)])
    assert not store.path("k").exists()
    # The path becomes readable afterwards. Nothing was recorded for it, so the
    # payload is not served unchecked.
    dep.write_text("int x;\n")
    assert store.lookup("k") is None


@pytest.mark.parametrize(
    "entry_text",
    ["not json", "[]", '{"deps": ["x"], "payload": {"v": 1}}', '{"deps": {}}'],
    ids=["non-json", "json-list", "deps-not-dict", "missing-payload"],
)
def test_store_misses_on_malformed_entry(tmp_path: Path, entry_text: str) -> None:
    store = TuParseStore(tmp_path / "store")
    store.publish("k", {"v": 1}, [])
    store.path("k").write_text(entry_text)
    assert store.lookup("k") is None


def test_cache_hit_takes_the_current_units_identity(tmp_path: Path) -> None:
    # Two units that compile the same source with the same flags share one
    # entry, because the key does not fold the unit's id or target. The hit must
    # still carry the identity of the unit it serves.
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")

    def unit(cu_id: str, target: str) -> CompileUnit:
        return CompileUnit(
            id=cu_id,
            source=str(src),
            target_id=target,
            language="CXX",
            standard="c++17",
            directory=str(tmp_path),
        )

    build = BuildEvidence(
        targets=[Target(id="target://one", public_headers=["foo.h"])],
        compile_units=[unit("cu://a", "target://one")],
    )
    cache = SourceAbiCache(tmp_path / "cache")
    run_source_replay(
        build, _CountingExtractor(), scope="full", public_header_roots=[], cache=cache
    )

    _, results, misses = _replay_cache_lookup(
        [unit("cu://b", "target://two")], _CountingExtractor(), [], cache, {}
    )
    assert misses == []
    served = results[0]
    assert served is not None
    assert (served.tu_id, served.target_id) == ("cu://b", "target://two")


def test_dependency_digest_is_none_when_the_file_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file that exists but cannot be read is an unreadable dependency: no digest,
    # so the entry it would have recorded is not published.
    from abicheck.storage import tu_parse_store

    dep = tmp_path / "locked.h"
    dep.write_text("int x;\n")

    def unreadable(_path: Path) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(tu_parse_store, "digest_file", unreadable)
    assert tu_parse_store.dep_digest(str(dep)) is None
