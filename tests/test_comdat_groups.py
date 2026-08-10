# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""COMDAT-group introspection — the only evidence that proves vague linkage.

``.dynsym`` shows an inline function and an ``__attribute__((weak))``
out-of-line function identically, as ``WEAK``. Only the object file, before
the linker discards its section groups, records which of the two the compiler
actually emitted. These tests pin that discrimination against a real compiler,
because the whole value of the module is a claim about what real toolchains
emit — a synthetic fixture would only restate the parser back to itself.

The compiler-free half (the ``ComdatScan`` contract) is tested unmarked so the
fast lane still covers the "not established" gate every consumer depends on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.buildsource.comdat_groups import (
    ComdatScan,
    collect_vague_linkage_symbols,
    scan_object_comdat_symbols,
)

#: One TU carrying every shape the demotion cares about, plus the two
#: negatives that must never be mistaken for vague linkage.
_SOURCE = """
inline int inline_fn(int x) { return x + 1; }
template <class T> T tpl(T v) { return v + v; }
struct Poly { virtual ~Poly(); virtual int f() { return 1; } };
Poly::~Poly() {}
struct Sub : Poly { int f() override { return 2; } };

__attribute__((weak)) int weak_out_of_line(int x) { return x + 2; }
int strong(int x) { return x + 3; }

int use() {
  Sub s; Poly* p = &s;
  return inline_fn(1) + tpl<int>(2) + p->f();
}
"""


def _compile_object(tmp_path: Path) -> Path:
    src = tmp_path / "u.cpp"
    src.write_text(_SOURCE, encoding="utf-8")
    obj = tmp_path / "u.o"
    subprocess.run(
        ["g++", "-c", "-fPIC", "-O0", "-o", str(obj), str(src)],
        check=True,
        capture_output=True,
    )
    return obj


requires_gxx = pytest.mark.skipif(
    shutil.which("g++") is None, reason="needs g++ to produce a real object file"
)


@pytest.mark.integration
@requires_gxx
class TestAgainstARealCompiler:
    def test_an_inline_function_is_vague(self, tmp_path: Path) -> None:
        syms = scan_object_comdat_symbols(_compile_object(tmp_path))
        assert syms is not None
        assert any("inline_fn" in s for s in syms), sorted(syms)

    def test_a_template_instantiation_is_vague(self, tmp_path: Path) -> None:
        syms = scan_object_comdat_symbols(_compile_object(tmp_path))
        assert syms is not None
        assert any(s.startswith("_Z3tplI") for s in syms), sorted(syms)

    def test_an_attribute_weak_function_is_not_vague(self, tmp_path: Path) -> None:
        """The discrimination the whole module exists for.

        Both this and the inline above are ``WEAK`` in ``.dynsym``; only the
        object file distinguishes them, and reading them as the same thing is
        what would demote a real load-time break.
        """
        syms = scan_object_comdat_symbols(_compile_object(tmp_path))
        assert syms is not None
        assert not any("weak_out_of_line" in s for s in syms), sorted(syms)

    def test_a_strong_definition_is_not_vague(self, tmp_path: Path) -> None:
        syms = scan_object_comdat_symbols(_compile_object(tmp_path))
        assert syms is not None
        assert not any("strong" in s for s in syms), sorted(syms)

    def test_every_destructor_variant_is_collected(self, tmp_path: Path) -> None:
        """Signature-only collection would miss these.

        The group is signed ``...D5Ev``, an alias that is never an ELF symbol,
        while ``D0``/``D1``/``D2`` are the names that actually reach
        ``.dynsym`` — so the scan must read defined symbols, not signatures.
        """
        syms = scan_object_comdat_symbols(_compile_object(tmp_path))
        assert syms is not None
        variants = sorted(s for s in syms if s.startswith("_ZN3SubD"))
        # D0 (deleting), D1 (complete), D2 (base) — the names that reach
        # `.dynsym`. The group's own signature is D5, which is not one of them.
        assert {s[-4:-2] for s in variants} >= {"D1", "D2"}, variants
        assert not any(s.startswith("_ZN3SubD5") for s in variants), variants

    def test_the_linked_library_no_longer_carries_it(self, tmp_path: Path) -> None:
        """Why this reads objects and not the shared library.

        The linker resolves and discards section groups, so the same code
        linked into a ``.so`` yields nothing — which is exactly why no
        ``.dynsym``- or DWARF-based check can answer this question.
        """
        obj = _compile_object(tmp_path)
        so = tmp_path / "libu.so"
        subprocess.run(
            ["g++", "-shared", "-fPIC", "-o", str(so), str(obj)],
            check=True,
            capture_output=True,
        )
        assert scan_object_comdat_symbols(so) == frozenset()


class TestTheScanContract:
    """Compiler-free: the "not established" gate consumers must honour."""

    def test_nothing_scanned_is_not_resolvable(self) -> None:
        assert not collect_vague_linkage_symbols([]).resolvable

    def test_an_unreadable_object_degrades_to_a_diagnostic(
        self, tmp_path: Path
    ) -> None:
        junk = tmp_path / "not-elf.o"
        junk.write_bytes(b"this is not an object file")
        scan = collect_vague_linkage_symbols([junk, tmp_path / "absent.o"])
        assert scan.objects_failed == 2
        assert not scan.resolvable, "failures must not read as evidence"
        assert scan.diagnostics

    def test_an_empty_result_from_a_real_scan_is_still_resolvable(self) -> None:
        """ "Scanned, found none" and "nothing scanned" are different facts, and
        a consumer must be able to tell them apart — an empty symbol set alone
        cannot."""
        scan = ComdatScan(symbols=frozenset(), objects_scanned=3)
        assert scan.resolvable

    def test_it_round_trips(self) -> None:
        scan = ComdatScan(
            symbols=frozenset({"_Z1fv"}),
            objects_scanned=2,
            objects_failed=1,
            diagnostics=["unreadable object file: x.o"],
        )
        back = ComdatScan.from_dict(scan.to_dict())
        assert back == scan

    def test_a_missing_key_loads_as_not_established(self) -> None:
        assert not ComdatScan.from_dict({}).resolvable
