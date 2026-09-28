"""Contracts for the release-scan CPU hotspot fixes.

Each fix here is a cache or a skipped computation, and a cache is only
correct if it is indistinguishable from recomputation. So every class states
that equivalence against an independent oracle (the undecorated function,
or a fresh computation), plus the property the cache could break: returned
values are not shared mutable state, a stale filesystem invalidates, and a
concurrent burst computes once.
"""

from __future__ import annotations

import logging
import os
import random
import string
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource import header_include_memo as memo_mod
from abicheck.model import type_identifiers as ti_mod
from abicheck.name_classification import canonicalize_type_name


def _type_spellings(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    atoms = [
        "int",
        "const",
        "struct Foo",
        "ns::Bar",
        "unsigned long long",
        "std::vector<ns::Baz>",
        "*",
        "&",
        "char const*",
        "class  X",
        "enum (unnamed enum at /a/b/foo.h:5:3)",
        "volatile",
        "a::b::C",
    ]
    out = []
    for _ in range(n):
        k = rng.randint(1, 5)
        s = " ".join(rng.choice(atoms) for _ in range(k))
        if rng.random() < 0.2:
            s = "  " + s + "".join(rng.choice(" *&") for _ in range(2))
        if rng.random() < 0.1:
            s += " " + "".join(rng.choice(string.ascii_letters) for _ in range(6))
        out.append(s)
    return out


class TestCanonicalizeTypeNameCache:
    @pytest.mark.parametrize("seed", range(4))
    def test_cached_equals_uncached(self, seed: int) -> None:
        raw = canonicalize_type_name.__wrapped__
        for s in _type_spellings(300, seed):
            # Twice: the second call is served from the cache.
            assert canonicalize_type_name(s) == raw(s)
            assert canonicalize_type_name(s) == raw(s)


@pytest.mark.parametrize("module", [ti_mod])
class TestTypeIdentifiersCache:
    @staticmethod
    def _oracle(module: Any, type_str: str | None) -> set[str]:
        # Independent re-derivation of the documented behaviour.
        if not type_str:
            return set()
        out: set[str] = set()
        for tok in module.IDENT_RE.findall(type_str):
            if tok in module.TYPE_NOISE:
                continue
            out.add(tok)
            if "::" in tok:
                out.add(tok.rsplit("::", 1)[1])
        return out

    def test_matches_oracle(self, module: Any) -> None:
        for s in [None, "", *_type_spellings(300, 7)]:
            assert module.type_identifiers(s) == self._oracle(module, s)
            assert module.type_identifiers(s) == self._oracle(module, s)

    def test_mutating_a_result_does_not_poison_the_cache(self, module: Any) -> None:
        # Callers do `idents |= ...` on the returned set.
        spelling = "ns::Widget const *"
        first = module.type_identifiers(spelling)
        expected = set(first)
        first |= {"Injected"}
        first.discard("Widget")
        assert module.type_identifiers(spelling) == expected
        assert module.type_identifiers(spelling) is not module.type_identifiers(
            spelling
        )


class TestSemanticIrConsistencyCheckAvoidsReduction:
    def test_load_boundary_check_builds_no_canonical_reduction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.model import semantic_ir as ir_mod
        from abicheck.model.fact import Fact
        from abicheck.model.identity import entity_id_for_constant
        from abicheck.model.occurrence import OccurrenceId
        from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
        from abicheck.model.semantic_ir_legacy_adapter import (
            assert_snapshot_semantic_ir_consistent,
        )

        calls = {"n": 0}
        real = ir_mod.SemanticIR.canonical_entities

        def counting(self: SemanticIR) -> Any:
            calls["n"] += 1
            return real(self)

        monkeypatch.setattr(ir_mod.SemanticIR, "canonical_entities", counting)
        eid = entity_id_for_constant((), "K")
        ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(canonical_spelling=Fact.present("1"))
            }
        )

        class _Snap:
            semantic_ir = ir
            typedef_entity_ids: dict = {}
            constant_entity_ids = {"K": eid}

        assert_snapshot_semantic_ir_consistent(_Snap())  # type: ignore[arg-type]
        assert calls["n"] == 0

        class _Bad(_Snap):
            constant_entity_ids = {"K": entity_id_for_constant((), "Other")}

        from abicheck.errors import SemanticIrAuthorityError

        with pytest.raises(SemanticIrAuthorityError):
            assert_snapshot_semantic_ir_consistent(_Bad())  # type: ignore[arg-type]


@pytest.fixture(autouse=False)
def fresh_memo():
    memo_mod.clear_include_memo()
    yield
    memo_mod.clear_include_memo()


@pytest.mark.usefixtures("fresh_memo")
class TestHeaderIncludeMemo:
    @staticmethod
    def _files(tmp_path: Path) -> tuple[list[str], list[str], str]:
        inc = tmp_path / "inc"
        inc.mkdir()
        dep = inc / "dep.h"
        dep.write_text("int x;\n")
        hdr = tmp_path / "a.h"
        hdr.write_text('#include "dep.h"\n')
        return [str(hdr)], [str(inc)], str(dep)

    def test_second_call_is_served_and_results_are_copies(self, tmp_path: Path) -> None:
        headers, includes, dep = self._files(tmp_path)
        n = {"calls": 0}

        def compute():
            n["calls"] += 1
            return {"h": [dep]}, ["diag"]

        a = memo_mod.memoized_include_extract(("k",), headers, includes, compute)
        a[0]["h"].append("junk")
        a[1].append("junk")
        b = memo_mod.memoized_include_extract(("k",), headers, includes, compute)
        assert n["calls"] == 1
        assert b == ({"h": [dep]}, ["diag"])

    def test_distinct_keys_do_not_share(self, tmp_path: Path) -> None:
        headers, includes, dep = self._files(tmp_path)
        a = memo_mod.memoized_include_extract(
            ("k1",), headers, includes, lambda: ({"h": [dep]}, [])
        )
        b = memo_mod.memoized_include_extract(
            ("k2",), headers, includes, lambda: ({}, ["other"])
        )
        assert a != b

    @pytest.mark.parametrize("which", ["header", "dependency", "include_dir"])
    def test_a_changed_input_file_invalidates(self, tmp_path: Path, which: str) -> None:
        headers, includes, dep = self._files(tmp_path)
        n = {"calls": 0}

        def compute():
            n["calls"] += 1
            return {"h": [dep]}, []

        memo_mod.memoized_include_extract(("k",), headers, includes, compute)
        target = {
            "header": headers[0],
            "dependency": dep,
            "include_dir": includes[0],
        }[which]
        if which == "include_dir":
            (Path(target) / "new.h").write_text("")
        else:
            Path(target).write_text(Path(target).read_text() + "// edit\n")
        # Force a visible mtime change even on coarse-timestamp filesystems.
        st = os.stat(target)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        memo_mod.memoized_include_extract(("k",), headers, includes, compute)
        assert n["calls"] == 2

    def test_concurrent_callers_compute_once(self, tmp_path: Path) -> None:
        headers, includes, dep = self._files(tmp_path)
        n = {"calls": 0}
        lock = threading.Lock()

        def compute():
            with lock:
                n["calls"] += 1
            time.sleep(0.05)
            return {"h": [dep]}, []

        results: list[Any] = []

        def worker():
            results.append(
                memo_mod.memoized_include_extract(("k",), headers, includes, compute)
            )

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert n["calls"] == 1
        assert all(r == ({"h": [dep]}, []) for r in results)

    def test_extractor_routes_through_the_memo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.buildsource.header_graph import ClangHeaderIncludeExtractor

        headers, includes, dep = self._files(tmp_path)
        n = {"calls": 0}

        def fake_uncached(self, hs, incs, **kw):
            n["calls"] += 1
            return {"h": [dep]}, []

        monkeypatch.setattr(ClangHeaderIncludeExtractor, "available", lambda self: True)
        monkeypatch.setattr(
            ClangHeaderIncludeExtractor, "_extract_uncached", fake_uncached
        )
        ex = ClangHeaderIncludeExtractor(clang_bin="clang++")
        for _ in range(3):
            assert ex.extract(headers, includes, language="CXX") == ({"h": [dep]}, [])
        assert n["calls"] == 1
        # Any argument reaching the argv is part of the key.
        ex.extract(headers, includes, language="C")
        ex.extract(headers, includes, language="CXX", nostdinc=True)
        ex.extract(headers, includes, language="CXX", gcc_options="-DX=1")
        ClangHeaderIncludeExtractor(clang_bin="clang-18").extract(headers, includes)
        assert n["calls"] == 5


class TestHeaderGraphAstFailureIsNotSilent:
    def test_failed_clang_ast_marks_call_graph_degraded(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import abicheck.service_header_graph_attach as attach_mod
        from abicheck.errors import SnapshotError
        from abicheck.model import AbiSnapshot
        from abicheck.model.source_graph_coverage import HEADER_CALL_GRAPH_PASS

        def failing_dump(*_a: Any, **_k: Any):
            raise SnapshotError(
                "omp.h:341:45: error: '__malloc__' attribute takes no arguments"
            )

        monkeypatch.setattr("abicheck.dumper._clang_header_dump", failing_dump)
        monkeypatch.setattr(attach_mod, "expand_header_inputs", lambda h: list(h))
        monkeypatch.setattr(
            attach_mod, "resolve_inferred_header_roots", lambda *a, **k: ([], [])
        )
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
        )
        with caplog.at_level(logging.WARNING, logger=attach_mod.__name__):
            out = attach_mod._attach_header_graph(
                snap,
                header_graph=True,
                header_graph_includes=False,
                headers=[Path("/nonexistent/foo.h")],
                includes=[],
                lang=None,
                compile=None,
                public_headers=None,
                public_header_dirs=None,
            )
        graph = out.build_source.source_graph
        assert graph.degraded_passes.get(HEADER_CALL_GRAPH_PASS) is True
        assert not graph.extractor_passes.get(HEADER_CALL_GRAPH_PASS)
        assert any("__malloc__" in r.getMessage() for r in caplog.records)
