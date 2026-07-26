# Copyright 2026 Nikolay Petrov
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
"""ADR-050 D3 (G32 Phase B) — the per-TU dump loop and placeholder merge.

Scope: pure-Python tests over abicheck.dumper_manifest's TuFragment/merge
logic (fake entities, no compiler) and the run_tu_fragment/run_tu_loop
orchestration (a stub header_ast_parser, so the per-TU wiring -- which
includes get which headers, which public_header_paths, optional-TU-skip --
is verified without needing castxml/clang.

Two classes of real, unmocked end-to-end tests -- deliberately *not* marked
``integration`` (that marker's Linux gate requires castxml; the point here is
proving the loop works with just clang, the same castxml-absent-host
reasoning ``test_clang_header_backend_integration.py`` documents for itself),
each self-skipping when clang/g++ are unavailable:

- ``test_run_tu_loop_real_clang_backend_*`` inject ``dumper._header_ast_parser``
  itself with ``backend="clang"`` over real header TUs, at the
  ``dumper_manifest.run_tu_loop`` layer directly.
- ``TestDumpWithManifest`` goes one layer up, through ``dumper.dump()``
  itself with a real compiled ELF ``.so`` and a real ``DumpManifest`` --
  proving the ELF format handler's manifest-driven branch (wired in
  ``_dump_elf``) end to end, not just ``run_tu_loop`` in isolation.
"""

from __future__ import annotations

import os as dm_os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from abicheck import process_resources as dm_process_resources
from abicheck.dump_manifest import DumpManifest, IncludeEntry, TranslationUnit
from abicheck.dumper import dump
from abicheck.dumper_manifest import (
    MergedTuFragments,
    TuFragment,
    entity_key,
    merge_tu_fragments,
    run_tu_fragment,
    run_tu_loop,
)
from abicheck.errors import SnapshotError, TuMergeError, ValidationError
from abicheck.model import EnumType, Function, Param, RecordType, TypeField, Variable


def _fn(
    name: str, mangled: str | None = None, params: list[Param] | None = None
) -> Function:
    return Function(
        name=name, mangled=mangled or name, return_type="void", params=params or []
    )


def _var(name: str, mangled: str | None = None) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int")


def _record(name: str) -> RecordType:
    return RecordType(name=name, kind="struct")


def _enum(name: str) -> EnumType:
    return EnumType(name=name)


# ---------------------------------------------------------------------------
# entity_key / merge_tu_fragments (no compiler needed)
# ---------------------------------------------------------------------------


def test_entity_key_is_kind_and_name_pair():
    assert entity_key("function", "_Z3fooi") == ("function", "_Z3fooi")


def test_merge_empty_fragments_returns_empty_result():
    merged = merge_tu_fragments([])
    assert isinstance(merged, MergedTuFragments)
    assert merged.functions == ()
    assert merged.typedefs == {}
    assert merged.ast_producer == "castxml"


def test_merge_concatenates_disjoint_fragments():
    a = TuFragment(tu_name="a", functions=(_fn("foo"),), ast_producer="clang")
    b = TuFragment(tu_name="b", functions=(_fn("bar"),), ast_producer="clang")
    merged = merge_tu_fragments([a, b])
    assert {fn.name for fn in merged.functions} == {"foo", "bar"}
    assert merged.ast_producer == "clang"


def test_merge_concatenates_every_entity_kind():
    a = TuFragment(
        tu_name="a",
        functions=(_fn("foo"),),
        variables=(_var("g_x"),),
        types=(_record("Point"),),
        enums=(_enum("Color"),),
        typedefs={"size_type": "unsigned long"},
        constants={"MAX": "100"},
    )
    merged = merge_tu_fragments([a])
    assert [fn.name for fn in merged.functions] == ["foo"]
    assert [v.name for v in merged.variables] == ["g_x"]
    assert [t.name for t in merged.types] == ["Point"]
    assert [e.name for e in merged.enums] == ["Color"]
    assert merged.typedefs == {"size_type": "unsigned long"}
    assert merged.constants == {"MAX": "100"}


def test_merge_reconciles_identical_function_redeclaration_across_tus():
    # ADR-050 D4: a plain redeclaration (identical decl in two TUs) is a
    # trivial merge, not a conflict -- Phase B's placeholder used to reject
    # any repeat outright; Phase C's real merge folds this into one entry.
    a = TuFragment(tu_name="a", functions=(_fn("foo", "_Z3fooi"),))
    b = TuFragment(tu_name="b", functions=(_fn("foo", "_Z3fooi"),))
    merged = merge_tu_fragments([a, b])
    assert [fn.name for fn in merged.functions] == ["foo"]


def test_merge_unions_default_argument_only_difference_across_tus():
    # ADR-050 D4's other named trivial-merge case: a difference confined to
    # a parameter's default argument merges rather than conflicts, and the
    # merged declaration keeps whichever side actually had the default.
    param_no_default = Param(name="n", type="int")
    param_with_default = replace(param_no_default, default="0")
    a = TuFragment(
        tu_name="a", functions=(_fn("foo", "_Z3fooi", params=[param_no_default]),)
    )
    b = TuFragment(
        tu_name="b", functions=(_fn("foo", "_Z3fooi", params=[param_with_default]),)
    )
    merged = merge_tu_fragments([a, b])
    assert len(merged.functions) == 1
    assert merged.functions[0].params[0].default == "0"


def test_merge_raises_on_genuinely_conflicting_function_across_tus():
    # Same entity_key (mangled name excludes return type), but the two TUs
    # disagree on the return type itself -- a genuine ODR conflict, not a
    # forward-decl/definition or default-argument-only difference.
    a = TuFragment(
        tu_name="a",
        functions=(replace(_fn("compute", "_Z7computei"), return_type="int"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(replace(_fn("compute", "_Z7computei"), return_type="double"),),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_tu_fragments([a, b])
    assert excinfo.value.code == "INCONSISTENT_DECLARATION"
    assert excinfo.value.entity_key == ("function", "_Z7computei")


def test_merge_reconciles_identical_type_redeclaration_across_tus():
    a = TuFragment(tu_name="a", types=(_record("Point"),))
    b = TuFragment(tu_name="b", types=(_record("Point"),))
    merged = merge_tu_fragments([a, b])
    assert [t.name for t in merged.types] == ["Point"]


def test_merge_reconciles_forward_declaration_and_definition_across_tus():
    # ADR-050 D4's canonical trivial-merge case: a forward declaration
    # (is_opaque, no fields) in one TU paired with the full definition in
    # another merges to the definition.
    forward = RecordType(name="Point", kind="struct", is_opaque=True)
    definition = RecordType(
        name="Point",
        kind="struct",
        fields=[TypeField(name="x", type="int"), TypeField(name="y", type="int")],
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    merged = merge_tu_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0] == definition


def test_merge_raises_on_genuinely_conflicting_type_across_tus():
    a = TuFragment(
        tu_name="a", types=(RecordType(name="Point", kind="struct", size_bits=64),)
    )
    b = TuFragment(
        tu_name="b", types=(RecordType(name="Point", kind="struct", size_bits=128),)
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_tu_fragments([a, b])
    assert excinfo.value.code == "INCONSISTENT_DECLARATION"
    assert excinfo.value.entity_key == ("type", "Point")


def test_merge_reconciles_identical_typedef_across_tus():
    a = TuFragment(tu_name="a", typedefs={"size_type": "unsigned long"})
    b = TuFragment(tu_name="b", typedefs={"size_type": "unsigned long"})
    merged = merge_tu_fragments([a, b])
    assert merged.typedefs == {"size_type": "unsigned long"}


def test_merge_raises_on_conflicting_typedef_value_across_tus():
    a = TuFragment(tu_name="a", typedefs={"size_type": "unsigned long"})
    b = TuFragment(tu_name="b", typedefs={"size_type": "unsigned int"})
    with pytest.raises(TuMergeError) as excinfo:
        merge_tu_fragments([a, b])
    assert excinfo.value.code == "INCONSISTENT_DECLARATION"
    assert excinfo.value.entity_key == ("typedef", "size_type")


def test_merge_does_not_raise_on_duplicate_within_a_single_fragment():
    # A single TU's own parser output is trusted as internally consistent;
    # only *cross*-TU repeats are this placeholder's concern.
    a = TuFragment(tu_name="a", functions=(_fn("foo"), _fn("foo")))
    merged = merge_tu_fragments([a])
    assert len(merged.functions) == 2


def test_merge_uses_first_fragment_ast_provenance_as_representative():
    a = TuFragment(tu_name="a", ast_producer="clang", ast_toolchain={"id": "clang-18"})
    b = TuFragment(tu_name="b", ast_producer="clang", ast_toolchain={"id": "clang-18"})
    merged = merge_tu_fragments([a, b])
    assert merged.ast_toolchain == {"id": "clang-18"}


# ---------------------------------------------------------------------------
# run_tu_fragment / run_tu_loop (stub header_ast_parser, no compiler needed)
# ---------------------------------------------------------------------------


class _StubParser:
    """Minimal stand-in for _CastxmlParser/_ClangAstParser."""

    def __init__(self, functions=(), fail: bool = False):
        self._functions = functions
        self._fail = fail
        if fail:
            raise SnapshotError("stub parser: simulated extraction failure")

    def parse_functions(self):
        return list(self._functions)

    def parse_variables(self):
        return []

    def parse_types(self):
        return []

    def parse_enums(self):
        return []

    def parse_typedefs(self):
        return {}

    def parse_constants(self):
        return {}


def _make_stub_header_ast_parser(calls: list, *, fail_for: frozenset = frozenset()):
    def _stub(headers, extra_includes, **kwargs):
        calls.append(
            {
                "headers": list(headers),
                "extra_includes": list(extra_includes),
                "public_header_paths": list(kwargs["public_header_paths"]),
                "public_dir_paths": list(kwargs["public_dir_paths"]),
            }
        )
        tu_marker = str(headers[0]) if headers else "<empty>"
        return _StubParser(
            functions=(_fn(Path(tu_marker).stem),),
            fail=tu_marker in fail_for,
        )

    return _stub


def _tu(
    name: str,
    header: str,
    *includes: str,
    required: bool = True,
    contributes: bool = True,
) -> TranslationUnit:
    return TranslationUnit(
        name=name,
        forced_includes=(Path(header),),
        includes=tuple(IncludeEntry(path=Path(p)) for p in includes),
        required=required,
        contributes_to_abi=contributes,
    )


def test_run_tu_fragment_calls_parser_with_tu_own_headers_and_includes():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tu = _tu("main", "foo.h", "vendor")
    fragment = run_tu_fragment(
        tu,
        header_ast_parser=stub,
        backend="auto",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=["foo.h"],
        public_dir_paths=[],
    )
    assert fragment.tu_name == "main"
    assert [fn.name for fn in fragment.functions] == ["foo"]
    assert calls[0]["headers"] == [Path("foo.h")]
    assert calls[0]["extra_includes"] == [Path("vendor")]


def test_run_tu_fragment_ast_producer_defaults_to_castxml_for_non_clang_parser():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tu = _tu("main", "foo.h")
    fragment = run_tu_fragment(
        tu,
        header_ast_parser=stub,
        backend="auto",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert fragment.ast_producer == "castxml"


def test_run_tu_loop_calls_once_per_tu_with_shared_public_header_paths():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    merged = run_tu_loop(
        tus,
        header_ast_parser=stub,
        roots=[Path("a.h"), Path("b.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert len(calls) == 2
    # Every TU's call sees the *manifest's* roots, not just its own header.
    assert calls[0]["public_header_paths"] == ["a.h", "b.h"]
    assert calls[1]["public_header_paths"] == ["a.h", "b.h"]
    assert {fn.name for fn in merged.functions} == {"a", "b"}


def test_run_tu_loop_required_tu_failure_propagates():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"a.h"}))
    tus = (_tu("a", "a.h", required=True),)
    with pytest.raises(SnapshotError, match="simulated extraction failure"):
        run_tu_loop(
            tus,
            header_ast_parser=stub,
            roots=[Path("a.h")],
            backend="auto",
            compiler="c++",
            exported_dynamic=set(),
            exported_static=set(),
        )


def test_run_tu_loop_optional_tu_failure_is_skipped():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"a.h"}))
    tus = (
        _tu("a", "a.h", required=False, contributes=False),
        _tu("b", "b.h"),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=stub,
        roots=[Path("b.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert {fn.name for fn in merged.functions} == {"b"}


# ── per-TU pool (ADR-050 D6, G32 Phase E) ──────────────────────────────────


def test_run_tu_loop_required_tu_failure_propagates_under_real_pool(monkeypatch):
    # Force a real multi-worker pool (not the len(tus)<=1 sequential
    # fast-path test_run_tu_loop_required_tu_failure_propagates already
    # covers) so a required failure still propagates once several TUs are
    # genuinely running concurrently.
    monkeypatch.setenv("ABICHECK_TU_JOBS", "4")
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"b.h"}))
    tus = (_tu("a", "a.h"), _tu("b", "b.h", required=True), _tu("c", "c.h"))
    with pytest.raises(SnapshotError, match="simulated extraction failure"):
        run_tu_loop(
            tus,
            header_ast_parser=stub,
            roots=[Path("a.h"), Path("b.h"), Path("c.h")],
            backend="auto",
            compiler="c++",
            exported_dynamic=set(),
            exported_static=set(),
        )


def test_run_tu_loop_optional_failure_does_not_cancel_siblings_under_pool(
    monkeypatch,
):
    monkeypatch.setenv("ABICHECK_TU_JOBS", "4")
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"b.h"}))
    tus = (
        _tu("a", "a.h"),
        _tu("b", "b.h", required=False, contributes=False),
        _tu("c", "c.h"),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=stub,
        roots=[Path("a.h"), Path("c.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    # Both non-failing TUs' declarations survive -- the optional failure
    # must not have short-circuited or cancelled its siblings.
    assert {fn.name for fn in merged.functions} == {"a", "c"}


def test_run_tu_fragments_preserves_declared_order_despite_completion_order(
    monkeypatch,
):
    """A later-declared TU that finishes FIRST must not reorder the
    returned fragment list -- merge_tu_fragments treats TU order as
    significant (which TU "wins" an ODR-compatible merge), so the pool must
    collect results in submission order, not completion order."""
    from abicheck.dumper_manifest import _run_tu_fragments

    monkeypatch.setenv("ABICHECK_TU_JOBS", "2")
    order: list = []

    def _stub(headers, extra_includes, **kwargs):
        name = Path(headers[0]).stem
        if name == "a":
            # Let "b" (submitted second) finish first.
            time.sleep(0.05)
        order.append(name)
        return _StubParser(functions=(_fn(name),))

    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    fragments = _run_tu_fragments(
        tus,
        header_ast_parser=_stub,
        backend="auto",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=["a.h", "b.h"],
        public_dir_paths=[],
        extra_hash_dirs=(),
    )
    # "b" really did finish executing before "a" (proves genuine concurrency,
    # not an accidentally-serial pool)...
    assert order == ["b", "a"]
    # ...yet the returned fragment order still matches the manifest's own
    # declared TU order, not completion order.
    assert [f.tu_name for f in fragments] == ["a", "b"]


def test_run_tu_fragments_cancels_pending_futures_promptly_on_required_failure(
    monkeypatch,
):
    """Codex review, PR #636: a required TU's failure must be observed (and
    its siblings' cancellation attempted) in COMPLETION order, not
    submission order -- otherwise a fast-failing, later-submitted required
    TU sits unnoticed behind an earlier-submitted, merely slow TU's future,
    letting the pool keep queued heavyweight AST work starting during that
    delay. Deterministic via a Future.cancel spy + a manually-held Event
    (not wall-clock timing): cancellation must happen while "slow" is still
    deliberately blocked, proving it wasn't gated behind waiting on "slow"'s
    own future first.
    """
    import threading
    from concurrent.futures import Future

    from abicheck.dumper_manifest import _run_tu_fragments
    from abicheck.errors import SnapshotError

    monkeypatch.setenv("ABICHECK_TU_JOBS", "2")

    cancel_called = threading.Event()
    orig_cancel = Future.cancel

    def _spy_cancel(self):
        cancel_called.set()
        return orig_cancel(self)

    monkeypatch.setattr(Future, "cancel", _spy_cancel)

    slow_release = threading.Event()

    def _stub(headers, extra_includes, **kwargs):
        name = Path(headers[0]).stem
        if name == "slow":
            slow_release.wait(timeout=5)
            return _StubParser(functions=(_fn("slow"),))
        if name == "bad":
            return _StubParser(fail=True)
        return _StubParser(functions=(_fn(name),))

    tus = (_tu("slow", "slow.h"), _tu("bad", "bad.h", required=True))

    def _run() -> None:
        try:
            _run_tu_fragments(
                tus,
                header_ast_parser=_stub,
                backend="auto",
                compiler="c++",
                gcc_path=None,
                gcc_prefix=None,
                gcc_options=None,
                gcc_option_tokens=(),
                sysroot=None,
                nostdinc=False,
                lang=None,
                exported_dynamic=set(),
                exported_static=set(),
                public_header_paths=[],
                public_dir_paths=[],
                extra_hash_dirs=(),
            )
        except SnapshotError:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        cancelled_promptly = cancel_called.wait(timeout=5)
    finally:
        slow_release.set()
        t.join(timeout=5)
    assert cancelled_promptly


def test_run_tu_fragments_propagates_active_deadline_into_pool_workers(monkeypatch):
    """Codex review, PR #636: ``contextvars`` don't cross a
    ``ThreadPoolExecutor`` boundary, so a worker submitted from inside an
    active ``deadline.deadline_scope`` (e.g. a future ``scan --budget`` caller)
    would otherwise see no deadline at all and silently fall back to each
    TU's unbudgeted default timeout -- the same class of gap PR #591 already
    closed for ``source_replay.py``'s L4 pool. Each stub "TU" records what
    ``deadline.remaining()`` sees on its own (pool worker) thread; if the
    deadline didn't propagate, that's ``None`` even though the submitting
    thread had an active scope.
    """
    from abicheck import deadline
    from abicheck.dumper_manifest import _run_tu_fragments

    monkeypatch.setenv("ABICHECK_TU_JOBS", "2")

    seen_remaining: dict[str, float | None] = {}

    def _stub(headers, extra_includes, **kwargs):
        name = Path(headers[0]).stem
        seen_remaining[name] = deadline.remaining()
        return _StubParser(functions=(_fn(name),))

    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    with deadline.deadline_scope(60.0):
        fragments = _run_tu_fragments(
            tus,
            header_ast_parser=_stub,
            backend="auto",
            compiler="c++",
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            gcc_option_tokens=(),
            sysroot=None,
            nostdinc=False,
            lang=None,
            exported_dynamic=set(),
            exported_static=set(),
            public_header_paths=[],
            public_dir_paths=[],
            extra_hash_dirs=(),
        )

    assert [f.tu_name for f in fragments] == ["a", "b"]
    assert seen_remaining["a"] is not None
    assert seen_remaining["b"] is not None
    # Each worker's remaining budget must be close to the 60s scope, not the
    # unbounded "no deadline" None a dropped ContextVar would produce.
    assert 0 < seen_remaining["a"] <= 60.0
    assert 0 < seen_remaining["b"] <= 60.0


def test_tu_jobs_env_override_forces_serial(monkeypatch):
    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.setenv("ABICHECK_TU_JOBS", "1")
    assert _tu_jobs(100) == 1


def test_tu_jobs_auto_capped_at_cpu_and_eight(monkeypatch):
    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.delenv("ABICHECK_TU_JOBS", raising=False)
    monkeypatch.setattr(dm_process_resources, "mem_cap", lambda budget: None)
    monkeypatch.setattr(dm_os, "cpu_count", lambda: 4)
    assert _tu_jobs(1000) == 4  # min(units, cpu, 8)
    assert _tu_jobs(2) == 2


def test_tu_jobs_clamped_by_available_memory(monkeypatch):
    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.delenv("ABICHECK_TU_JOBS", raising=False)
    monkeypatch.delenv("ABICHECK_TU_JOB_MEM_GIB", raising=False)
    monkeypatch.setattr(dm_os, "cpu_count", lambda: 8)
    monkeypatch.setattr(dm_process_resources, "available_mem_gib", lambda: 6.0)
    assert _tu_jobs(1000) == 2  # 6 GiB / 3.0 GiB-per-worker default == 2


def test_tu_jobs_invalid_env_falls_back_to_serial(monkeypatch):
    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.setenv("ABICHECK_TU_JOBS", "not-a-number")
    assert _tu_jobs(100) == 1


def test_tu_jobs_explicit_env_clamped_by_oversubscription_ceiling(monkeypatch, caplog):
    import logging

    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.setenv("ABICHECK_TU_JOBS", "64")
    monkeypatch.setattr(dm_process_resources, "mem_cap", lambda budget: None)
    monkeypatch.setattr(dm_os, "cpu_count", lambda: 4)
    with caplog.at_level(logging.WARNING):
        jobs = _tu_jobs(100)
    assert jobs == 8  # max(8, 2*4)
    assert any("oversubscription" in r.message for r in caplog.records)


def test_tu_jobs_explicit_env_clamped_by_available_memory(monkeypatch, caplog):
    import logging

    from abicheck.dumper_manifest import _tu_jobs

    monkeypatch.setenv("ABICHECK_TU_JOBS", "8")
    monkeypatch.delenv("ABICHECK_TU_JOB_MEM_GIB", raising=False)
    monkeypatch.setattr(dm_process_resources, "available_mem_gib", lambda: 6.0)
    with caplog.at_level(logging.WARNING):
        jobs = _tu_jobs(100)
    assert jobs == 2  # 6 GiB / 3.0 GiB-per-worker default == 2, below the ceiling
    assert any("OOM" in r.message for r in caplog.records)


def test_run_tu_loop_reconciles_identical_entity_across_tus():
    calls: list = []

    def _stub(headers, extra_includes, **kwargs):
        calls.append(1)
        return _StubParser(functions=(_fn("shared", "_Z6sharedv"),))

    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    merged = run_tu_loop(
        tus,
        header_ast_parser=_stub,
        roots=[Path("a.h"), Path("b.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert [fn.name for fn in merged.functions] == ["shared"]


def test_run_tu_loop_merge_prefers_explicit_public_path_over_root():
    # `roots` is a *scope* declaration (D1's scope_fingerprint input), not
    # an ADR-015 provenance input -- the merge step's public-surface
    # preference must match what the later, authoritative apply_provenance
    # call (dumper.py) classifies origin against: the manifest's *explicit*
    # public_header_paths/public_header_dirs only, roots excluded. Feeding
    # the roots-augmented set into the merge instead lets a root-only
    # declaration wrongly "win" the merge's provenance-representative slot
    # over one reached through a genuinely public header, only for
    # apply_provenance to then reclassify it private/unknown downstream,
    # hiding a real public API change (Codex review, PR #635 round 14).
    def _stub(headers, extra_includes, **kwargs):
        marker = str(headers[0]) if headers else "<empty>"
        location = "root.h:1" if marker == "root.h" else "public/api.h:1"
        fn = Function(
            name="shared",
            mangled="_Z6sharedv",
            return_type="void",
            source_location=location,
        )
        return _StubParser(functions=(fn,))

    tus = (_tu("a", "root.h"), _tu("b", "public/api.h"))
    merged = run_tu_loop(
        tus,
        header_ast_parser=_stub,
        roots=[Path("root.h")],
        public_header_paths=[Path("public/api.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].source_location == "public/api.h:1"


def test_run_tu_loop_raises_on_genuinely_conflicting_entity_across_tus():
    def _stub(headers, extra_includes, **kwargs):
        # Return a different return_type per TU, keyed off which header this
        # invocation is for -- a genuine cross-TU conflict, unlike the
        # identical-redeclaration case above.
        tu_marker = str(headers[0])
        return_type = "int" if tu_marker.endswith("a.h") else "double"
        return _StubParser(
            functions=(replace(_fn("shared", "_Z6sharedv"), return_type=return_type),)
        )

    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    with pytest.raises(TuMergeError) as excinfo:
        run_tu_loop(
            tus,
            header_ast_parser=_stub,
            roots=[Path("a.h"), Path("b.h")],
            backend="auto",
            compiler="c++",
            exported_dynamic=set(),
            exported_static=set(),
        )
    assert excinfo.value.code == "INCONSISTENT_DECLARATION"


def test_run_tu_loop_empty_tus_returns_empty_merge():
    merged = run_tu_loop(
        (),
        header_ast_parser=_make_stub_header_ast_parser([]),
        roots=[Path("a.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert merged.functions == ()


# ---------------------------------------------------------------------------
# Real clang backend, no stub (see module docstring)
# ---------------------------------------------------------------------------


def test_run_tu_loop_real_clang_backend_merges_two_translation_units(tmp_path):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")
    from abicheck.dumper import _header_ast_parser

    header_a = tmp_path / "a.h"
    header_a.write_text("int add_a(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("int add_b(int a, int b);\n")

    tus = (
        _tu("tu_a", str(header_a)),
        _tu("tu_b", str(header_b)),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=_header_ast_parser,
        roots=[header_a, header_b],
        backend="clang",
        compiler="c++",
        exported_dynamic={"add_a", "add_b"},
        exported_static=set(),
    )
    assert {fn.name for fn in merged.functions} == {"add_a", "add_b"}
    assert merged.ast_producer == "clang"


def test_run_tu_loop_real_clang_backend_merges_identical_redeclaration_across_tus(
    tmp_path,
):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")
    from abicheck.dumper import _header_ast_parser

    header_a = tmp_path / "a.h"
    header_a.write_text("int shared_fn(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("int shared_fn(int a, int b);\n")

    tus = (
        _tu("tu_a", str(header_a)),
        _tu("tu_b", str(header_b)),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=_header_ast_parser,
        roots=[header_a, header_b],
        backend="clang",
        compiler="c++",
        exported_dynamic={"shared_fn"},
        exported_static=set(),
    )
    assert [fn.name for fn in merged.functions] == ["shared_fn"]


def test_run_tu_loop_real_clang_backend_raises_on_genuinely_conflicting_across_tus(
    tmp_path,
):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")
    from abicheck.dumper import _header_ast_parser

    header_a = tmp_path / "a.h"
    header_a.write_text("int shared_fn(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("double shared_fn(int a, int b);\n")

    tus = (
        _tu("tu_a", str(header_a)),
        _tu("tu_b", str(header_b)),
    )
    with pytest.raises(TuMergeError) as excinfo:
        run_tu_loop(
            tus,
            header_ast_parser=_header_ast_parser,
            roots=[header_a, header_b],
            backend="clang",
            compiler="c++",
            exported_dynamic={"shared_fn"},
            exported_static=set(),
        )
    assert excinfo.value.code == "INCONSISTENT_DECLARATION"


# ---------------------------------------------------------------------------
# dumper.dump() itself, through the ELF format handler's manifest branch
# ---------------------------------------------------------------------------


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "dump_manifest is ELF-only so far (ADR-050 D3) -- a `gcc -shared "
        "-o *.so` build produces a genuine ELF binary only on Linux; on "
        "macOS the same command produces Mach-O despite the .so extension, "
        "which would exercise the (deliberate) Mach-O rejection path "
        "instead of the ELF one this class tests. Same reasoning as "
        "test_clang_header_backend_integration.py's own platform gate."
    ),
)
class TestDumpWithManifest:
    """dumper.dump(so_path, [], dump_manifest=...) -- the real ELF entry
    point, not just run_tu_loop in isolation. Requires clang + g++ (real
    compile, real clang -ast-dump=json; no castxml needed)."""

    def _build_two_tu_lib(self, tmp_path: Path) -> Path:
        header_a = tmp_path / "a.h"
        header_a.write_text("int add_a(int a, int b);\n")
        header_b = tmp_path / "b.h"
        header_b.write_text("int add_b(int a, int b);\n")
        src = tmp_path / "lib.c"
        src.write_text(
            "int add_a(int a, int b) { return a + b; }\n"
            "int add_b(int a, int b) { return a - b; }\n"
        )
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        return so

    def _manifest(self, tmp_path: Path) -> DumpManifest:
        return DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(tmp_path / "a.h", tmp_path / "b.h"),
            translation_units=(
                TranslationUnit(name="tu_a", forced_includes=(tmp_path / "a.h",)),
                TranslationUnit(name="tu_b", forced_includes=(tmp_path / "b.h",)),
            ),
        )

    def test_dump_merges_two_tus_into_one_snapshot(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        snap = dump(
            so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=self._manifest(tmp_path),
        )
        assert snap.from_headers is True
        assert {f.name for f in snap.functions} == {"add_a", "add_b"}
        assert snap.ast_producer == "clang"
        assert snap.contract is not None
        assert snap.contract.scope_fingerprint is not None

    def test_dump_rejects_headers_with_manifest(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="mutually exclusive"):
            dump(
                so,
                [tmp_path / "a.h"],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_rejects_public_headers_with_manifest(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="mutually exclusive"):
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                public_headers=[tmp_path / "a.h"],
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_rejects_extra_includes_with_manifest(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="mutually exclusive"):
            dump(
                so,
                [],
                [tmp_path],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_rejects_manifest_for_hybrid_frontend(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="hybrid"):
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="hybrid",
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_manifest_merges_identical_redeclaration_across_tus(
        self, tmp_path: Path
    ):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        header_a = tmp_path / "a.h"
        header_a.write_text("int shared_fn(int a, int b);\n")
        header_b = tmp_path / "b.h"
        header_b.write_text("int shared_fn(int a, int b);\n")
        src = tmp_path / "lib.c"
        src.write_text("int shared_fn(int a, int b) { return a + b; }\n")
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header_a, header_b),
            translation_units=(
                TranslationUnit(name="tu_a", forced_includes=(header_a,)),
                TranslationUnit(name="tu_b", forced_includes=(header_b,)),
            ),
        )
        snapshot = dump(
            so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=manifest,
        )
        assert [fn.name for fn in snapshot.functions] == ["shared_fn"]

    def test_dump_manifest_raises_on_genuinely_conflicting_across_tus(
        self, tmp_path: Path
    ):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        header_a = tmp_path / "a.h"
        header_a.write_text("int shared_fn(int a, int b);\n")
        header_b = tmp_path / "b.h"
        header_b.write_text("double shared_fn(int a, int b);\n")
        src = tmp_path / "lib.c"
        src.write_text("int shared_fn(int a, int b) { return a + b; }\n")
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header_a, header_b),
            translation_units=(
                TranslationUnit(name="tu_a", forced_includes=(header_a,)),
                TranslationUnit(name="tu_b", forced_includes=(header_b,)),
            ),
        )
        with pytest.raises(TuMergeError) as excinfo:
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                dump_manifest=manifest,
            )
        assert excinfo.value.code == "INCONSISTENT_DECLARATION"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "dump_manifest is ELF-only so far (ADR-050 D3) -- a `gcc -shared "
        "-o *.so` build produces a genuine ELF binary only on Linux; see "
        "TestDumpWithManifest's own gate for the same reasoning."
    ),
)
class TestManifestIncludeOwnershipAndScopeFingerprint:
    """ADR-050 D1's manifest-idiom ``includes: [{path, project_owned}]``
    escape hatch, exercised through the real ``dumper.dump()`` manifest
    path -- not just the legacy CLI's labeled ``--include old:LABEL=PATH``
    form. Requires clang + gcc (real compile, real ``clang -ast-dump=json``;
    no castxml needed)."""

    def _build_lib_with_sibling_include(
        self, tmp_path: Path, *, priv_content: str, project_owned: bool
    ) -> tuple[Path, DumpManifest]:
        # `inc_dir`/`src_dir` are SIBLINGS under tmp_path -- src_dir is not an
        # ancestor of the declared header, so the ancestor rule alone would
        # classify it external; only the explicit `project_owned` marker (or
        # lack of it) decides which side of that this test exercises.
        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        header = inc_dir / "foo.h"
        header.write_text('#include "priv.h"\nint f(int a, int b);\n')
        (src_dir / "priv.h").write_text(priv_content)
        c_src = tmp_path / "lib.c"
        c_src.write_text("int f(int a, int b) { return a + b; }\n")
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(c_src)],
            check=True,
            capture_output=True,
        )
        manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header,),
            translation_units=(
                TranslationUnit(
                    name="main",
                    forced_includes=(header,),
                    includes=(IncludeEntry(path=src_dir, project_owned=project_owned),),
                ),
            ),
        )
        return so, manifest

    def test_project_owned_sibling_include_keeps_profile_fingerprint_stable(
        self, tmp_path: Path
    ):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_so, old_manifest = self._build_lib_with_sibling_include(
            old_dir, priv_content="#define PRIV_V1 1\n", project_owned=True
        )
        new_so, new_manifest = self._build_lib_with_sibling_include(
            new_dir, priv_content="#define PRIV_V2 2\n", project_owned=True
        )
        old_snap = dump(
            old_so,
            [],
            version="old",
            compiler="cc",
            header_backend="clang",
            dump_manifest=old_manifest,
        )
        new_snap = dump(
            new_so,
            [],
            version="new",
            compiler="cc",
            header_backend="clang",
            dump_manifest=new_manifest,
        )
        assert old_snap.contract is not None and new_snap.contract is not None
        assert (
            old_snap.contract.profile_fingerprint
            == new_snap.contract.profile_fingerprint
        )

    def test_bare_sibling_include_classifies_external_not_project_owned(
        self, tmp_path: Path
    ):
        """A bare (unmarked) manifest ``includes: [../src]`` entry on a
        sibling-of-the-header directory is NOT project-owned -- proving the
        mapping form's ``project_owned: true`` marker is a genuine
        classification change, not a no-op parse flag.

        Compares two identically-shaped dumps (same directory layout, same
        private-header content) differing only in the ``project_owned``
        marker: the owned one gets the manifest-idiom ``label:<relative
        path>`` per-slot token (ADR-050 D1's stable, mount-point-independent
        token for a manifest entry), the bare one falls through to the
        ordinary ``ext:<content-hash>`` external-bucket token -- so the two
        fingerprints differ even for byte-identical inputs.
        """
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        owned_dir, bare_dir = tmp_path / "owned", tmp_path / "bare"
        owned_dir.mkdir()
        bare_dir.mkdir()
        owned_so, owned_manifest = self._build_lib_with_sibling_include(
            owned_dir, priv_content="#define X 1\n", project_owned=True
        )
        bare_so, bare_manifest = self._build_lib_with_sibling_include(
            bare_dir, priv_content="#define X 1\n", project_owned=False
        )
        owned_snap = dump(
            owned_so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=owned_manifest,
        )
        bare_snap = dump(
            bare_so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=bare_manifest,
        )
        assert owned_snap.contract is not None and bare_snap.contract is not None
        assert (
            owned_snap.contract.profile_fields["include_sequence"] == '["0:label:src"]'
        )
        assert bare_snap.contract.profile_fields["include_sequence"].startswith(
            '["0:ext:sha256:'
        )
        assert (
            owned_snap.contract.profile_fingerprint
            != bare_snap.contract.profile_fingerprint
        )

    def test_manifest_public_header_dirs_changes_scope_fingerprint(
        self, tmp_path: Path
    ):
        """A manifest's own public_header_paths/public_header_dirs base-
        profile fields are a genuine replacement for the legacy CLI's
        --public-header/--public-header-dir -- not a no-op -- the same way
        those flags already change scope_fingerprint on the legacy path."""
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        header = tmp_path / "foo.h"
        header.write_text("int f(int a, int b);\n")
        pub_dir = tmp_path / "pub"
        pub_dir.mkdir()
        c_src = tmp_path / "lib.c"
        c_src.write_text("int f(int a, int b) { return a + b; }\n")
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(c_src)],
            check=True,
            capture_output=True,
        )
        base_manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header,),
            translation_units=(
                TranslationUnit(name="main", forced_includes=(header,)),
            ),
        )
        with_pub_dir_manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header,),
            public_header_dirs=(pub_dir,),
            translation_units=(
                TranslationUnit(name="main", forced_includes=(header,)),
            ),
        )
        base_snap = dump(
            so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=base_manifest,
        )
        with_pub_dir_snap = dump(
            so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=with_pub_dir_manifest,
        )
        assert base_snap.contract is not None and with_pub_dir_snap.contract is not None
        assert (
            base_snap.contract.scope_fingerprint
            != with_pub_dir_snap.contract.scope_fingerprint
        )

    def _build_lib_with_outside_base_dir_include(
        self, root: Path, *, priv_content: str
    ) -> tuple[Path, DumpManifest]:
        # `checkout/` is the manifest's own base_dir; `src/` is a SIBLING of
        # `checkout/` (one level up, ../src relative to base_dir) -- the
        # exact layout dump_manifest.py's own schema docstring uses as its
        # primary `project_owned` example, and the one case where
        # `Path.relative_to(base_dir)` cannot succeed (the include resolves
        # OUTSIDE base_dir, not under it).
        checkout = root / "checkout"
        checkout.mkdir()
        src_dir = root / "src"
        src_dir.mkdir()
        header = checkout / "foo.h"
        header.write_text('#include "priv.h"\nint f(int a, int b);\n')
        (src_dir / "priv.h").write_text(priv_content)
        c_src = checkout / "lib.c"
        c_src.write_text("int f(int a, int b) { return a + b; }\n")
        so = checkout / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(c_src)],
            check=True,
            capture_output=True,
        )
        manifest = DumpManifest(
            base_dir=checkout,
            compiler="cc",
            roots=(header,),
            translation_units=(
                TranslationUnit(
                    name="main",
                    forced_includes=(header,),
                    includes=(IncludeEntry(path=src_dir, project_owned=True),),
                ),
            ),
        )
        return so, manifest

    def test_project_owned_include_outside_base_dir_stable_across_mount_points(
        self, tmp_path: Path
    ):
        """The documented ``../src`` sibling-of-checkout case (a project_owned
        include resolving OUTSIDE the manifest's own base_dir, not under it)
        must still produce a stable, mount-point-independent token -- not the
        raw absolute resolved path, which would differ between two checkouts
        of the identical relative layout at different filesystem locations.
        """
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        # Two ENTIRELY SEPARATE roots (distinct temp trees), simulating two
        # different absolute mount points/checkouts, each with the identical
        # relative layout (checkout/ + sibling src/).
        old_root = tmp_path / "mount_a"
        new_root = tmp_path / "mount_b" / "nested"
        old_root.mkdir(parents=True)
        new_root.mkdir(parents=True)
        old_so, old_manifest = self._build_lib_with_outside_base_dir_include(
            old_root, priv_content="#define PRIV_V1 1\n"
        )
        new_so, new_manifest = self._build_lib_with_outside_base_dir_include(
            new_root, priv_content="#define PRIV_V2 2\n"
        )
        old_snap = dump(
            old_so,
            [],
            version="old",
            compiler="cc",
            header_backend="clang",
            dump_manifest=old_manifest,
        )
        new_snap = dump(
            new_so,
            [],
            version="new",
            compiler="cc",
            header_backend="clang",
            dump_manifest=new_manifest,
        )
        assert old_snap.contract is not None and new_snap.contract is not None
        assert (
            old_snap.contract.profile_fields["include_sequence"] == '["0:label:../src"]'
        )
        assert (
            new_snap.contract.profile_fields["include_sequence"] == '["0:label:../src"]'
        )
        assert (
            old_snap.contract.profile_fingerprint
            == new_snap.contract.profile_fingerprint
        )
