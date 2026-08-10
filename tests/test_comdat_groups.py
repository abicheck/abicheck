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
import sys
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


#: ELF only, and deliberately so: ``SHT_GROUP`` is an ELF construct. On macOS
#: ``g++`` emits Mach-O and on Windows PE/COFF, where the parser correctly
#: reports "not an ELF file" — so these compile-and-read tests are meaningless
#: off Linux and failed there before this guard (CI, macOS + Windows
#: integration lanes). The synthetic-object tests below carry the coverage on
#: every platform, since they build the bytes directly.
requires_elf_toolchain = pytest.mark.skipif(
    shutil.which("g++") is None or not sys.platform.startswith("linux"),
    reason="needs g++ on an ELF platform (SHT_GROUP is ELF-only)",
)


@pytest.mark.integration
@requires_elf_toolchain
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


def _synthetic_object(
    *, little_endian: bool = True, comdat: bool = True, symbol: str = "_Z3foov"
) -> bytes:
    """A minimal but genuinely parseable ELF64 relocatable object.

    Exists so the ELF walk is covered without a compiler: the marked tests
    below need g++ and therefore never run in the lane that measures coverage,
    which left the whole parser unmeasured. It also gives the big-endian path
    an end-to-end fixture, which no available toolchain here can produce.

    Layout: [0] NULL, [1] .text.foo, [2] .group, [3] .symtab, [4] .strtab,
    [5] .shstrtab — with the group signing section 1 and the symbol table
    defining *symbol* there.
    """
    import struct

    e = "<" if little_endian else ">"
    names = b"\0.text.foo\0.group\0.symtab\0.strtab\0.shstrtab\0"
    n = {
        s: names.index(b"\0" + s.encode() + b"\0") + 1
        for s in (".text.foo", ".group", ".symtab", ".strtab", ".shstrtab")
    }
    strtab = b"\0" + symbol.encode() + b"\0"
    text = b"\x90\x90\x90\x90"
    group = struct.pack(f"{e}2I", 1 if comdat else 0, 1)
    symtab = struct.pack(f"{e}IBBHQQ", 0, 0, 0, 0, 0, 0) + struct.pack(
        f"{e}IBBHQQ", 1, (1 << 4), 0, 1, 0, 0
    )
    secs = [
        (0, 0, b""),
        (n[".text.foo"], 1, text),
        (n[".group"], 17, group),
        (n[".symtab"], 2, symtab),
        (n[".strtab"], 3, strtab),
        (n[".shstrtab"], 3, names),
    ]
    off = 64
    offs = []
    for _, _, data in secs:
        offs.append(off)
        off += len(data)
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = 2
    hdr[5] = 1 if little_endian else 2
    hdr[6] = 1
    struct.pack_into(f"{e}HHI", hdr, 16, 1, 62, 1)
    struct.pack_into(f"{e}QQQ", hdr, 24, 0, 0, off)
    struct.pack_into(f"{e}IHHHHH", hdr, 48, 0, 64, 0, 0, 64, len(secs))
    struct.pack_into(f"{e}H", hdr, 62, 5)
    out = bytes(hdr) + b"".join(d for _, _, d in secs)
    for i, ((name, typ, data), o) in enumerate(zip(secs, offs)):
        out += struct.pack(
            f"{e}IIQQQQIIQQ",
            name,
            typ,
            0,
            0,
            o,
            len(data),
            {3: 4, 2: 3}.get(i, 0),
            {3: 1, 2: 1}.get(i, 0),
            1,
            24 if i == 3 else (4 if i == 2 else 0),
        )
    return out


class TestTheElfWalkWithoutACompiler:
    """The parser end to end on a synthesised object, in both byte orders."""

    def _write(self, tmp_path: Path, **kw: object) -> Path:
        obj = tmp_path / "synth.o"
        obj.write_bytes(_synthetic_object(**kw))  # type: ignore[arg-type]
        return obj

    @pytest.mark.parametrize("little_endian", [True, False], ids=["le", "be"])
    def test_a_comdat_symbol_is_found(
        self, tmp_path: Path, little_endian: bool
    ) -> None:
        obj = self._write(tmp_path, little_endian=little_endian)
        assert scan_object_comdat_symbols(obj) == frozenset({"_Z3foov"})

    @pytest.mark.parametrize("little_endian", [True, False], ids=["le", "be"])
    def test_a_plain_group_yields_nothing(
        self, tmp_path: Path, little_endian: bool
    ) -> None:
        """A section group without GRP_COMDAT is not vague linkage."""
        obj = self._write(tmp_path, little_endian=little_endian, comdat=False)
        assert scan_object_comdat_symbols(obj) == frozenset()

    def test_it_reaches_the_collection_sweep(self, tmp_path: Path) -> None:
        obj = self._write(tmp_path)
        scan = collect_vague_linkage_symbols([obj, tmp_path / "missing.o"])
        assert scan.symbols == frozenset({"_Z3foov"})
        assert scan.objects_scanned == 1
        assert scan.objects_failed == 1
        assert scan.resolvable

    def test_a_truncated_object_is_a_diagnostic(self, tmp_path: Path) -> None:
        obj = tmp_path / "cut.o"
        obj.write_bytes(_synthetic_object()[:40])
        assert scan_object_comdat_symbols(obj) is None


class TestTheScanContract:
    """Compiler-free: the "not established" gate consumers must honour."""

    def test_nothing_scanned_is_not_resolvable(self) -> None:
        assert not collect_vague_linkage_symbols([]).resolvable

    def test_the_diagnostic_list_is_bounded(self, tmp_path: Path) -> None:
        """A build whose objects are all missing must not mint one diagnostic
        per file into the report."""
        scan = collect_vague_linkage_symbols(
            [tmp_path / f"gone{i}.o" for i in range(50)]
        )
        assert scan.objects_failed == 50
        assert len(scan.diagnostics) == 20

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


class TestItRidesTheL3Evidence:
    """`BuildEvidence.comdat` — additive, tri-state, union-merging."""

    @staticmethod
    def _ev(**kw):
        from abicheck.buildsource.build_evidence import BuildEvidence

        return BuildEvidence(**kw)

    def test_a_build_with_no_object_outputs_stays_unscanned(self) -> None:
        """`None`, not an empty scan: a build that recorded no objects has not
        established anything, and only a real scan may license a demotion."""
        ev = self._ev()
        ev.scan_comdat()
        assert ev.comdat is None

    def test_it_round_trips_through_the_evidence(self) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence

        ev = self._ev(comdat=ComdatScan(frozenset({"_Z1fv"}), objects_scanned=2))
        back = BuildEvidence.from_dict(ev.to_dict())
        assert back.comdat is not None
        assert back.comdat.symbols == frozenset({"_Z1fv"})
        assert back.comdat.resolvable

    def test_an_older_pack_loads_as_unscanned(self) -> None:
        """The field is additive — a pack written before it existed must load
        as "not established" rather than as an empty (and therefore
        demotion-licensing) scan."""
        from abicheck.buildsource.build_evidence import BuildEvidence

        assert BuildEvidence.from_dict({}).comdat is None

    def test_an_unscanned_build_omits_the_key_entirely(self) -> None:
        assert "comdat" not in self._ev().to_dict()

    def test_merging_two_adapters_unions_their_findings(self) -> None:
        """An entity emitted vaguely by any TU is vague for the library, and
        each adapter sees only its own slice."""
        a = self._ev(comdat=ComdatScan(frozenset({"a"}), objects_scanned=1))
        a.merge(self._ev(comdat=ComdatScan(frozenset({"b"}), objects_scanned=1)))
        assert a.comdat is not None
        assert a.comdat.symbols == frozenset({"a", "b"})
        assert a.comdat.objects_scanned == 2

    def test_merging_into_an_unscanned_side_adopts_the_scan(self) -> None:
        a = self._ev()
        a.merge(self._ev(comdat=ComdatScan(frozenset({"b"}), objects_scanned=1)))
        assert a.comdat is not None and a.comdat.resolvable

    def test_merging_an_unscanned_other_keeps_what_we_had(self) -> None:
        a = self._ev(comdat=ComdatScan(frozenset({"a"}), objects_scanned=1))
        a.merge(self._ev())
        assert a.comdat is not None and a.comdat.symbols == frozenset({"a"})


class TestItResolvesOutputLabelsBackToFiles:
    """`CompileUnit.output` is normalized for *persistence*, not for reading.

    Opening it verbatim marks real objects unreadable whenever collection runs
    outside the build directory or under the user's home, silently turning
    "this build has objects" into "nothing scanned".
    """

    @staticmethod
    def _unit(**kw: str):
        from abicheck.buildsource.build_evidence import CompileUnit

        return CompileUnit(id="cu://1", **kw)

    @staticmethod
    def _ev(units: list):
        from abicheck.buildsource.build_evidence import BuildEvidence

        return BuildEvidence(compile_units=units)

    def test_an_output_relative_to_its_directory_is_found(self, tmp_path: Path) -> None:
        """Ninja/Make/Bazel record the output relative to `directory`."""
        (tmp_path / "obj").mkdir()
        (tmp_path / "obj" / "foo.o").write_bytes(_synthetic_object())
        ev = self._ev([self._unit(output="obj/foo.o", directory=str(tmp_path))])
        ev.scan_comdat()
        assert ev.comdat is not None
        assert ev.comdat.symbols == frozenset({"_Z3foov"})

    def test_a_home_redacted_output_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-032 D7 rewrites a home-rooted path to `~/...`; `open()` does
        not expand that, so the redacted label must be expanded back."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        (tmp_path / "foo.o").write_bytes(_synthetic_object())
        ev = self._ev([self._unit(output="~/foo.o")])
        ev.scan_comdat()
        assert ev.comdat is not None
        assert ev.comdat.symbols == frozenset({"_Z3foov"})

    def test_a_label_naming_no_file_leaves_the_build_unscanned(
        self, tmp_path: Path
    ) -> None:
        """Not an empty scan: an unresolvable label established nothing, and
        an empty-but-resolvable scan is exactly what licenses a demotion."""
        ev = self._ev([self._unit(output="obj/gone.o", directory=str(tmp_path))])
        ev.scan_comdat()
        assert ev.comdat is None

    def test_a_reused_pack_keeps_its_loaded_scan(self, tmp_path: Path) -> None:
        """The `base_build` path: `merge()` restores a pack's own scan, and a
        rescan over persisted labels that resolve to nothing must not replace
        it with an empty, unresolvable one."""
        ev = self._ev([self._unit(output="obj/gone.o", directory=str(tmp_path))])
        ev.comdat = ComdatScan(frozenset({"_Z1fv"}), objects_scanned=3)
        ev.scan_comdat()
        assert ev.comdat is not None
        assert ev.comdat.symbols == frozenset({"_Z1fv"})
        assert ev.comdat.objects_scanned == 3

    def test_an_unreadable_object_does_not_displace_a_loaded_scan(
        self, tmp_path: Path
    ) -> None:
        """The file exists, so it is opened — but it parses as nothing, and a
        scan that established nothing must not overwrite one that did."""
        (tmp_path / "cut.o").write_bytes(_synthetic_object()[:40])
        ev = self._ev([self._unit(output=str(tmp_path / "cut.o"))])
        ev.comdat = ComdatScan(frozenset({"_Z1fv"}), objects_scanned=3)
        ev.scan_comdat()
        assert ev.comdat is not None and ev.comdat.symbols == frozenset({"_Z1fv"})

    def test_collection_does_not_scan_unless_asked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No detector consumes this evidence yet, so parsing every object's
        symbol table on every collection would be cost with no result."""
        from abicheck.buildsource.build_evidence import comdat_scan_requested

        monkeypatch.delenv("ABICHECK_COLLECT_COMDAT", raising=False)
        assert comdat_scan_requested() is False
        for on in ("1", "true", "YES", " true "):
            monkeypatch.setenv("ABICHECK_COLLECT_COMDAT", on)
            assert comdat_scan_requested() is True, on
        monkeypatch.setenv("ABICHECK_COLLECT_COMDAT", "0")
        assert comdat_scan_requested() is False

    def test_the_collection_call_site_is_behind_that_gate(self) -> None:
        """The gate only means anything if `collect_inline_pack` reads it."""
        import inspect

        from abicheck.buildsource import inline

        src = inspect.getsource(inline.collect_inline_pack)
        assert "if comdat_scan_requested():\n            merged.scan_comdat()" in src


@pytest.mark.integration
@requires_elf_toolchain
def test_scan_build_evidence_reads_compile_unit_outputs(tmp_path: Path) -> None:
    """The collection path end to end: L3 compile units -> real objects ->
    a vague-linkage set that excludes the `__attribute__((weak))` decoy."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    obj = _compile_object(tmp_path)
    ev = BuildEvidence(compile_units=[CompileUnit(id="1", output=str(obj))])
    ev.scan_comdat()
    assert ev.comdat is not None and ev.comdat.resolvable
    assert any("inline_fn" in s for s in ev.comdat.symbols)
    assert not any("weak_out_of_line" in s for s in ev.comdat.symbols)


class TestByteOrder:
    """``SHT_GROUP`` words use the file's ``EI_DATA`` order, not the host's.

    Hardcoding little-endian decodes ``GRP_COMDAT`` (1) as ``0x01000000`` on
    s390x/ppc64, so every group is skipped and the scan returns an empty set —
    indistinguishable from "this build has nothing vague", which is the
    failure mode that would silently license a demotion (Codex/CodeRabbit).
    """

    @staticmethod
    def _payload(order: str) -> bytes:
        import struct

        return struct.pack(f"{order}3I", 1, 7, 9)  # GRP_COMDAT + sections 7, 9

    def test_little_endian_object(self) -> None:
        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(self._payload("<"), little_endian=True) == frozenset({7, 9})

    def test_big_endian_object(self) -> None:
        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(self._payload(">"), little_endian=False) == frozenset(
            {7, 9}
        )

    def test_reading_big_endian_data_as_little_loses_the_group(self) -> None:
        """The bug itself, pinned: the flag no longer reads as GRP_COMDAT and
        the members vanish."""
        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(self._payload(">"), little_endian=True) == frozenset()

    def test_a_non_comdat_group_is_ignored(self) -> None:
        import struct

        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(struct.pack("<2I", 0, 7), little_endian=True) == frozenset()

    def test_a_truncated_payload_is_dropped_not_raised(self) -> None:
        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(b"\x01\x00", little_endian=True) == frozenset()
        assert decode_group(b"", little_endian=True) == frozenset()

    def test_a_flag_with_no_members_yields_nothing(self) -> None:
        """A well-formed GRP_COMDAT header with an empty member array — legal,
        and it must not index past the payload."""
        import struct

        from abicheck.buildsource.comdat_groups import decode_group

        assert decode_group(struct.pack("<I", 1), little_endian=True) == frozenset()
