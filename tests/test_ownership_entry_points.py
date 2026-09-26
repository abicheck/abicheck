# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Evidence-entity-model gap B2: every snapshot this run extracts records
its ownership, and a snapshot that recorded none reads ``unknown``.

ADR-075 D1/D2 routes every freshly extracted header snapshot through
``workflows.ownership_request.classify_extracted``. Only ``resolve_input``
and the release surface did; the header-only ``dump`` (and typed
``DumpRequest`` with no path), both ``compat`` live dumps and
``appcompat``'s own dumps returned a snapshot with no ``extraction_scope``,
so every reader fell back to ``ScopeOrigin`` for it.

Three statements:

* **Inventory** -- every production call to a snapshot producer is listed
  below with how its result is stamped (or why it needs none). A new call
  site fails until it is classified; that is the class, not the four
  instances.
* **Real entry points** -- ``dump -H`` with no binary and ``compat dump``
  record a scope and classify every declaration (oracle: the
  ``extraction_scope`` block the stored document carries, read back
  through the real codec).
* **Pre-v52** -- a stored snapshot from before the field loads ``unknown``
  (no scope, no per-declaration decision), never a guessed owner, and the
  ``public_not_exported`` coverage row says its obligations rest on header
  provenance only.
"""

from __future__ import annotations

import ast
import functools
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.serialization import load_snapshot

_ROOT = Path(__file__).resolve().parent.parent

#: Functions whose result is a freshly extracted snapshot.
_PRODUCERS = frozenset(
    {
        "dump",
        "run_dump",
        "cached_run_dump",
        "build_header_only_snapshot",
        "_dump_elf",
        "_dump_pe",
        "_dump_macho",
        "run_hybrid_dump",
    }
)

#: ``(module, enclosing function, producer) -> how ownership is recorded``.
_INVENTORY: dict[tuple[str, str, str], str] = {
    ("abicheck/workflows/input_resolution.py", "_resolve_input_impl", "cached_run_dump"): "stamped in resolve_input",
    ("abicheck/workflows/release_surface_acquisition.py", "_produce", "build_header_only_snapshot"): "stamped in _produce",
    ("abicheck/workflows/artifact/execute_header_only.py", "execute_header_only_dump_request", "build_header_only_snapshot"): "stamped in execute_header_only_dump_request",
    ("abicheck/compat/cli.py", "compat_dump_cmd", "dump"): "stamped in finish_live_compat_dump",
    ("abicheck/compat/cli.py", "_snapshot_from_compat_input", "dump"): "stamped in finish_live_compat_dump",
    ("abicheck/appcompat.py", "check_appcompat", "run_dump"): "stamped in check_appcompat",
    ("abicheck/stack_checker.py", "_run_abi_diff", "run_dump"): "no headers: binary-only, nothing to own",
    ("abicheck/probe_harness.py", "_snapshot_object_file", "dump"): "no headers (dwarf_only probe)",
    ("abicheck/cli_buildsource_helpers.py", "_exported_symbols_from_binary", "run_dump"): "no headers; reads export names only",
    ("abicheck/cli_resolve.py", "_dump_native_binary", "run_dump"): "no production caller (unit-tested helper)",
    ("abicheck/service_dump_cache.py", "_dump_uncached", "run_dump"): "inner layer of resolve_input",
    ("abicheck/service_dump_native.py", "_dump_elf", "dump"): "inner layer of run_dump",
    ("abicheck/service_dump_native.py", "_run_dump_uncached", "_dump_elf"): "inner layer of run_dump",
    ("abicheck/service_dump_native.py", "_run_dump_uncached", "_dump_pe"): "inner layer of run_dump",
    ("abicheck/service_dump_native.py", "_run_dump_uncached", "_dump_macho"): "inner layer of run_dump",
    ("abicheck/service_dump_native.py", "_run_dump_uncached", "run_dump"): "inner layer of run_dump",
    ("abicheck/dumper.py", "dump", "run_hybrid_dump"): "inner layer of dumper.dump",
    ("abicheck/workflows/aggregate/collection.py", "_write_json_atomic", "dump"): "json.dump, not a snapshot",
    ("abicheck/storage/ast_parse_exclusions.py", "_write_sidecar", "dump"): "json.dump, not a snapshot",
}  # fmt: skip


@functools.cache
def _parsed_sources() -> tuple[tuple[str, ast.Module], ...]:
    """Every ``abicheck/`` module parsed once, shared by the inventory tests
    (the whole-package parse is what dominates their cost)."""
    return tuple(
        (
            path.relative_to(_ROOT).as_posix(),
            ast.parse(path.read_text(encoding="utf-8")),
        )
        for path in sorted((_ROOT / "abicheck").rglob("*.py"))
    )


def _producer_calls() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for rel, tree in _parsed_sources():

        def walk(node: ast.AST, fn: str) -> None:
            for child in ast.iter_child_nodes(node):
                inner = fn
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inner = child.name
                if isinstance(child, ast.Call):
                    f = child.func
                    name = (
                        f.id
                        if isinstance(f, ast.Name)
                        else f.attr
                        if isinstance(f, ast.Attribute)
                        else None
                    )
                    if name in _PRODUCERS:
                        found.add((rel, inner, name))
                walk(child, inner)

        walk(tree, "<module>")
    return found


def test_every_snapshot_producer_call_is_classified() -> None:
    calls = _producer_calls()
    assert calls, "vacuity guard: the scan found no producer call at all"
    unlisted = calls - set(_INVENTORY)
    stale = set(_INVENTORY) - calls
    assert not unlisted, (
        f"classify these producer calls for ownership: {sorted(unlisted)}"
    )
    assert not stale, f"inventory names calls that no longer exist: {sorted(stale)}"


def test_stamped_sites_really_call_the_stamp() -> None:
    """A site listed as stamped names the function that stamps it, and that
    function really calls ``classify_extracted``."""
    bodies = {
        n.name: n
        for _, tree in _parsed_sources()
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for (module, fn, _), how in _INVENTORY.items():
        if not how.startswith("stamped in "):
            continue
        body = bodies[how.removeprefix("stamped in ")]
        called = {
            (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
            for c in ast.walk(body)
            if isinstance(c, ast.Call)
        }
        assert "classify_extracted" in called, (module, fn, how)


def test_appcompat_stamps_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    from abicheck import appcompat
    from abicheck.model import AbiSnapshot

    stamped: list[tuple[str, list[Path], list[Path]]] = []

    def _run_dump(
        path: Path,
        fmt: str,
        headers: Any,
        includes: Any,
        version: str,
        *a: Any,
        **k: Any,
    ) -> AbiSnapshot:
        return AbiSnapshot(library="l", version=version, from_headers=True)

    class _Stop(Exception):
        pass

    def _stop(*a: Any, **k: Any) -> None:
        raise _Stop

    monkeypatch.setattr("abicheck.service_dump_native.run_dump", _run_dump)
    monkeypatch.setattr(
        "abicheck.workflows.input_resolution.detect_binary_format", lambda p: "elf"
    )
    monkeypatch.setattr(
        "abicheck.workflows.ownership_request.classify_extracted",
        lambda snap, req, headers, dirs: stamped.append((snap.version, headers, dirs)),
    )
    monkeypatch.setattr("abicheck.workflows.compare_policy.compare_snapshots", _stop)
    with pytest.raises(_Stop):
        appcompat.check_appcompat(
            Path("app"), Path("old.so"), Path("new.so"),
            old_headers=[Path("o.h")], new_headers=[Path("n.h")], includes=[Path("inc")],
        )  # fmt: skip
    assert stamped == [
        ("old", [Path("o.h")], [Path("inc")]),
        ("new", [Path("n.h")], [Path("inc")]),
    ]


def test_header_only_executor_stamps_what_it_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The typed header-only executor (``dump -H`` with no binary) stamps the
    snapshot it built, with the request's headers and public directories."""
    from types import SimpleNamespace

    from abicheck.model import AbiSnapshot
    from abicheck.workflows.artifact import execute_header_only as mod

    built = AbiSnapshot(library="api.h", version="1", from_headers=True)
    stamped: list[tuple[object, list[Path], list[Path]]] = []
    monkeypatch.setattr(
        "abicheck.header_only_dump.build_header_only_snapshot", lambda **_: built
    )
    monkeypatch.setattr(
        "abicheck.workflows.ownership_request.classify_extracted",
        lambda snap, req, headers, dirs: stamped.append(
            (snap, list(headers), list(dirs))
        ),
    )
    resolved = SimpleNamespace(
        request=SimpleNamespace(
            input=SimpleNamespace(version="1", includes=[], compile=None)
        ),
        headers=[Path("inc/api.h")],
        evidence=SimpleNamespace(dump_manifest=None),
        header_backend="castxml",
        lang="c++",
        lang_explicit=False,
        public_headers=[],
        public_header_dirs=[Path("inc")],
        requested_depth=None,
        resolved_execution_context=None,
    )
    out = mod.execute_header_only_dump_request(resolved, SimpleNamespace())  # type: ignore[arg-type]
    assert out.snapshot is built
    assert stamped == [(built, [Path("inc/api.h")], [Path("inc")])]


def _needs_tools() -> None:
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("needs gcc and castxml")


def _fixture(tmp: Path) -> tuple[Path, Path]:
    inc = tmp / "inc"
    inc.mkdir()
    (inc / "api.h").write_text("struct S { int x; };\nint f(struct S *s);\n")
    (tmp / "x.c").write_text('#include "api.h"\nint f(struct S *s){return s->x;}\n')
    lib = tmp / "libx.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", f"-I{inc}", str(tmp / "x.c"), "-o", str(lib)],
        check=True,
    )
    return inc / "api.h", lib


def _assert_classified(path: Path) -> None:
    snap = load_snapshot(path)
    assert snap.from_headers
    assert snap.extraction_scope is not None
    decls = [*snap.functions, *snap.variables, *snap.types, *snap.enums]
    assert decls
    assert all(getattr(d, "ownership_fact", None) is not None for d in decls)


@pytest.mark.integration
def test_header_only_dump_records_ownership(tmp_path: Path) -> None:
    _needs_tools()
    header, _ = _fixture(tmp_path)
    out = tmp_path / "ho.json"
    res = CliRunner().invoke(main, ["dump", "-H", str(header), "-o", str(out)])
    assert res.exit_code == 0, res.output
    _assert_classified(out)


@pytest.mark.integration
def test_compat_dump_records_ownership(tmp_path: Path) -> None:
    _needs_tools()
    header, lib = _fixture(tmp_path)
    desc = tmp_path / "d.xml"
    desc.write_text(
        f"<version>1</version>\n<headers>{header}</headers>\n<libs>{lib}</libs>\n"
    )
    out = tmp_path / "c.json"
    res = CliRunner().invoke(
        main,
        ["compat", "dump", "-lib", "x", "-dump", str(desc), "-dump-path", str(out)],
    )
    assert res.exit_code == 0, res.output
    _assert_classified(out)


def test_pre_v52_snapshot_reads_unknown_never_a_guessed_owner(tmp_path: Path) -> None:
    from abicheck.buildsource.cross_source_checks import (
        OWNERSHIP_UNRECORDED_NOTE,
        CrosscheckConfig,
        _check_public_not_exported,
    )
    from abicheck.compare.ownership_relations import contract_relations
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
    from abicheck.model.vocabulary import ScopeOrigin
    from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

    fn = Function(
        name="f", mangled="_Z1fP1S", return_type="int", visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER, source_location="/p/inc/api.h:2",
    )  # fmt: skip
    snap = AbiSnapshot(
        library="x", version="1", functions=[fn], from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name="g")]),
    )  # fmt: skip
    doc = snapshot_to_dict(snap)
    doc["schema_version"] = 51
    doc.pop("extraction_scope", None)
    old = snapshot_from_dict(doc)
    assert old.extraction_scope is None
    assert all(getattr(f, "ownership_fact", None) is None for f in old.functions)
    rel = contract_relations(old)
    assert rel.identities is None
    out = _check_public_not_exported(old, CrosscheckConfig())  # type: ignore[arg-type]
    assert OWNERSHIP_UNRECORDED_NOTE in out.detail
    # Obligation still rests on header provenance: the missing export is kept.
    assert {c.symbol for c in out.findings} == {"_Z1fP1S"}
