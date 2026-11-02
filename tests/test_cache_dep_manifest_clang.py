# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""System headers validate a cached parse by what the parse read (ADR-062 D11).

Bug class ``cache.key_identity_must_equal_parse_input_content``, the system-
include half: the header-AST and whole-snapshot keys fold the compiler's
system include directories by *spelling*; the system headers a real clang
parse opened are recorded in the entry's dependency manifest from clang's
own depfile and re-checked on every lookup.

Oracle: an uncached parse of the same inputs in a fresh cache root, and the
*fixture's* include structure (which system files the parsed header reaches,
written by this test, not derived from the implementation). For every
mutation of a temp system tree -- included directly, transitively, through a
suffix-less name, not included at all, a touch, a deletion, a shadowing file
in an earlier search directory -- a warm call must be a hit exactly when no
file the parse reaches changed, and on a miss must equal the uncached parse
(or fail like it), never serve the stale tree.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract import cache_input_digest
from abicheck.extract.headers.clang import backend as clang_backend
from abicheck.extract.headers.clang.backend import clang_header_dump
from abicheck.storage import header_ast_cache

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang"),
]

_OLD = 1_600_000_000_000_000_000


def _tree(tmp: Path) -> dict[str, Path]:
    early = tmp / "sys_early"  # searched first; empty
    sysdir = tmp / "sys"
    proj = tmp / "proj"
    for d in (early, sysdir, proj):
        d.mkdir(parents=True)
    files = {
        "direct": sysdir / "direct.h",
        "transitive": sysdir / "transitive.h",
        "suffixless": sysdir / "vec",
        "unrelated": sysdir / "unrelated.h",
    }
    files["direct"].write_text('#include "transitive.h"\nint direct_fn(int);\n')
    files["transitive"].write_text("int transitive_fn(int);\n")
    files["suffixless"].write_text("int vec_fn(int);\n")
    files["unrelated"].write_text("int unrelated_fn(int);\n")
    header = proj / "api.h"
    header.write_text("#include <direct.h>\n#include <vec>\nint api_fn(int);\n")
    for p in (*files.values(), header):
        os.utime(p, ns=(_OLD, _OLD))
    return {**files, "header": header, "early": early, "sys": sysdir}


def _signature(root: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in root.get("inner", []):
        if node.get("kind") == "FunctionDecl":
            out.append((node.get("name", ""), node.get("type", {}).get("qualType", "")))
    return sorted(out)


class _Runner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, cmd: list[str], **kw: Any) -> Any:
        self.calls += 1
        return clang_backend.run_clang_ast(cmd, **kw)


def _dump(
    fx: dict[str, Path], mp: pytest.MonkeyPatch, cache: Path, runner: _Runner
) -> list[tuple[str, str]] | type[SnapshotError]:
    mp.setenv("XDG_CACHE_HOME", str(cache))
    mp.setenv("LOCALAPPDATA", str(cache))  # the Windows cache base
    mp.setattr(
        clang_backend,
        "_resolve_clang_system_includes",
        lambda *a, **k: (str(fx["early"]), str(fx["sys"])),
    )
    try:
        root, _kind, _cpp = clang_header_dump(
            [fx["header"]], [], "cc", lang="c", run_ast=runner
        )
    except SnapshotError:
        return SnapshotError
    return _signature(root)


def _edit(p: Path) -> None:
    p.write_text(p.read_text().replace("(int)", "(long)"))
    os.utime(p, ns=(_OLD + 10**9, _OLD + 10**9))


def _touch(p: Path) -> None:
    os.utime(p, ns=(_OLD + 5 * 10**9, _OLD + 5 * 10**9))


#: ``name -> (mutation, whether the parse's reachable inputs changed)``. The
#: expectation is the fixture's include structure, written above.
MUTATIONS: dict[str, tuple[Callable[[dict[str, Path]], None], bool]] = {
    "edit directly included": (lambda fx: _edit(fx["direct"]), True),
    "edit transitively included": (lambda fx: _edit(fx["transitive"]), True),
    "edit suffix-less include": (lambda fx: _edit(fx["suffixless"]), True),
    "edit not-included header": (lambda fx: _edit(fx["unrelated"]), False),
    "touch included header": (lambda fx: _touch(fx["direct"]), False),
    "delete included header": (lambda fx: fx["transitive"].unlink(), True),
    "shadow in earlier search dir": (
        lambda fx: (fx["early"] / "vec").write_text("int shadow_fn(int);\n"),
        True,
    ),
    "new file nothing includes": (
        lambda fx: (fx["sys"] / "brand_new.h").write_text("int n;\n"),
        False,
    ),
}


@pytest.fixture(autouse=True)
def _fresh_memos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_input_digest, "_FILE_MEMO", {})
    monkeypatch.setattr(cache_input_digest, "_DIRTY", {})
    header_ast_cache._ast_memo_slot.value = None


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_warm_hit_iff_a_read_system_header_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    mutate, inputs_changed = MUTATIONS[name]
    fx = _tree(tmp_path / "w")
    cache = tmp_path / "cache"
    cold = _Runner()
    first = _dump(fx, monkeypatch, cache, cold)
    assert cold.calls == 1 and first != SnapshotError
    assert ("direct_fn", "int (int)") in first  # the system tree is really read

    mutate(fx)
    warm = _Runner()
    got = _dump(fx, monkeypatch, cache, warm)
    oracle = _dump(fx, monkeypatch, tmp_path / f"fresh-{name}", _Runner())

    assert warm.calls == (1 if inputs_changed else 0), name
    assert got == oracle
    if inputs_changed:
        assert got != first


def test_corrupt_or_missing_manifest_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _tree(tmp_path / "w")
    cache = tmp_path / "cache"
    _dump(fx, monkeypatch, cache, _Runner())
    (manifest,) = cache.rglob("*.json.deps")
    for damage in (lambda: manifest.write_text("{not json"), manifest.unlink):
        damage()
        runner = _Runner()
        assert _dump(fx, monkeypatch, cache, runner) != SnapshotError
        assert runner.calls == 1
        assert manifest.is_file()  # the re-parse stored a fresh one


@pytest.mark.parametrize(
    "name", ["edit transitively included", "edit not-included header"]
)
def test_snapshot_cache_validates_the_union_of_its_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The whole-snapshot entry carries the dependencies of the AST parse
    that fed it (plumbed through ``cache_dep_manifest.recording``), so a warm
    dump re-runs iff a system header that parse read changed, and the
    re-run equals a dump with no cache at all."""
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.workflows.dump.cache import store
    from abicheck.workflows.dump.cache.api import cached_run_dump

    mutate, inputs_changed = MUTATIONS[name]
    fx = _tree(tmp_path / "w")
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"\x7fELF fake")
    dumps: list[int] = []

    def run_dump(path, fmt, headers, includes, version, lang, **kw):  # noqa: ARG001
        dumps.append(1)
        sig = _dump(fx, monkeypatch, tmp_path / "ast", _Runner())
        assert sig != SnapshotError
        funcs = [
            Function(name=n, mangled=n, return_type=t, visibility=Visibility.PUBLIC)
            for n, t in sig
        ]
        return AbiSnapshot(library="lib.so", version="1", functions=funcs)

    monkeypatch.setattr(store, "_CACHE_DIR", tmp_path / "snapshots")
    first = cached_run_dump(run_dump, binary, "elf", [], [], "1", "c")
    again = cached_run_dump(run_dump, binary, "elf", [], [], "1", "c")
    assert again.declarations.functions == first.declarations.functions
    assert dumps == [1]  # the second call hit
    mutate(fx)
    got = cached_run_dump(run_dump, binary, "elf", [], [], "1", "c")
    assert len(dumps) == (2 if inputs_changed else 1)
    oracle = run_dump(binary, "elf", [], [], "1", "c")
    assert [(f.name, f.return_type) for f in got.declarations.functions] == [
        (f.name, f.return_type) for f in oracle.declarations.functions
    ]


@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
def test_castxml_entry_records_and_checks_the_system_headers_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """castxml forwards ``-MD -MF``: its entry's manifest names the system
    header the test's own source includes, a warm call hits, and a recorded
    digest that no longer matches (what an edited ``/usr/include`` file looks
    like to the manifest) makes the next call re-run castxml."""
    import json

    from abicheck.extract.headers.castxml import backend as cx

    header = tmp_path / "api.h"
    header.write_text("#include <stddef.h>\nsize_t api_len(void);\n")
    os.utime(header, ns=(_OLD, _OLD))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # LOCALAPPDATA is the cache base Windows resolves instead of XDG_CACHE_HOME.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    runs: list[int] = []

    def run(cmd: list[str], **kw: Any) -> Any:
        if "-E" not in cmd:  # the parse, not a preprocessed-text replay
            runs.append(1)
        return cx.run_castxml(cmd, **kw)

    cx.castxml_dump([header], [], "cc", lang="c", run=run)
    (side,) = (tmp_path / "cache").rglob("castxml/*.json.deps")
    doc = json.loads(side.read_text())
    stddef = [p for p in doc["files"] if Path(p).name == "stddef.h"]
    assert stddef, sorted(doc["files"])
    assert not any("abicheck_castxml_" in p for p in doc["files"])
    cx.castxml_dump([header], [], "cc", lang="c", run=run)
    assert len(runs) == 1
    doc["files"][stddef[0]] = "0" * 64
    side.write_text(json.dumps(doc))
    cx.castxml_dump([header], [], "cc", lang="c", run=run)
    assert len(runs) == 2
