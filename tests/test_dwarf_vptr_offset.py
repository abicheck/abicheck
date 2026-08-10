"""G31 Phase C: real DWARF-derived RecordType.vptr_offset_bits.

Split out of test_dwarf_snapshot.py (which hit the 2000-line hard cap) --
see that file's own C++ integration section for the sibling fixtures this
mirrors. Self-contained: defines its own g++ availability check rather than
importing across test modules.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
from abicheck.model import RecordType

_GPP = "g++"


def _has_gpp() -> bool:
    """Check if g++ is available on Linux for ELF C++ integration tests."""
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(
            [_GPP, "--version"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_HAS_GPP = _has_gpp()


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfSnapshotVptrOffset:
    """Real bit offset of a class's vtable pointer, read from DWARF's own
    artificial ``_vptr.<Class>`` member instead of assumed to be 0 for any
    polymorphic type -- see ``_finalize_vptr_offsets``'s docstring.
    """

    @pytest.fixture()
    def vptr_lib(self, tmp_path: Path) -> Path:
        """Compile a library exercising several vptr-placement shapes."""
        cpp_src = tmp_path / "vptr.cpp"
        cpp_src.write_text("""\
// Plain non-polymorphic type.
struct Plain { int x; };

// Introduces its own vtable at offset 0.
struct A { virtual void a(); int ai; };
struct B { virtual void b(); int bi; };

// Multiple inheritance: A is the primary base (offset 0); B's own vtable
// pointer sits at B's own base offset within C, not covered by this
// model's single vptr_offset_bits field (that's the still-open
// "secondary vtable placement" gap, not this fix).
struct C : A, B { virtual void c(); int ci; };

// Virtual base can never be primary -- D introduces its own vptr at 0.
struct D : virtual A { virtual void d(); int di; };

// Inherits its vtable from A without adding or overriding anything of its
// own -- no local vptr member in DWARF at all. This is the case the old
// "0 if own vtable list is non-empty else None" heuristic got wrong (its
// own vtable list is empty here), and the one this fix closes.
struct N : A { int ni; };

// Two-level inheritance chain, still no local vtable slot anywhere in the
// chain -- exercises the fixed-point resolution over the whole binary.
struct M : N { int mi; };

// A non-polymorphic base placed before a polymorphic one is reordered by
// the ABI so the polymorphic one is still primary (offset 0), confirmed
// against real GCC output before writing this test.
struct X { int x; };
struct Y : X, A { virtual void y(); int yi; };

void A::a() {}
void B::b() {}
void C::c() {}
void D::d() {}
void Y::y() {}

static Plain g_plain;
static N g_n;
static M g_m;
static Y g_y;
extern "C" Plain* get_plain() { return &g_plain; }
extern "C" N* get_n() { return &g_n; }
extern "C" M* get_m() { return &g_m; }
extern "C" Y* get_y() { return &g_y; }
""")
        so_path = tmp_path / "libvptr.so"
        result = subprocess.run(
            [_GPP, "-shared", "-fPIC", "-g", "-O0", "-o", str(so_path), str(cpp_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    @staticmethod
    def _types_by_name(lib: Path) -> dict[str, RecordType]:
        from abicheck.dwarf_unified import parse_dwarf
        from abicheck.elf_metadata import parse_elf_metadata

        elf_meta = parse_elf_metadata(lib)
        dwarf_meta, dwarf_adv = parse_dwarf(lib)
        snap = build_snapshot_from_dwarf(lib, elf_meta, dwarf_meta, dwarf_adv)
        return {t.name: t for t in snap.types}

    def test_non_polymorphic_is_none(self, vptr_lib: Path) -> None:
        """A type with no vtable anywhere in its hierarchy stays None."""
        types = self._types_by_name(vptr_lib)
        assert types["Plain"].vptr_offset_bits is None

    def test_own_vtable_is_offset_zero(self, vptr_lib: Path) -> None:
        """A class introducing its own vtable has its vptr at bit offset 0."""
        types = self._types_by_name(vptr_lib)
        assert types["A"].vptr_offset_bits == 0
        assert types["B"].vptr_offset_bits == 0

    def test_multiple_inheritance_primary_base_at_zero(self, vptr_lib: Path) -> None:
        """The primary base's vtable pointer (offset 0) is still resolved
        correctly under multiple inheritance -- this model tracks only the
        primary vptr, not B's own secondary one at a non-zero offset."""
        types = self._types_by_name(vptr_lib)
        assert types["C"].vptr_offset_bits == 0
        assert types["C"].base_offsets.get("B") != 0  # B is the secondary base

    def test_virtual_base_gets_own_local_vptr(self, vptr_lib: Path) -> None:
        """D has a real data member of its own (forcing a non-degenerate
        layout), so GCC gives it its own local vptr member at offset 0
        rather than sharing A's -- see
        test_nearly_empty_virtual_primary_base_falls_back_to_zero below for
        the case where a virtual base's vptr IS shared and no local member
        is emitted at all."""
        types = self._types_by_name(vptr_lib)
        assert types["D"].vptr_offset_bits == 0

    def test_inherited_vtable_with_no_own_override_resolves(
        self, vptr_lib: Path
    ) -> None:
        """A class that adds/overrides nothing of its own still has a real
        vptr offset resolved via its primary base -- the case the old
        "0 if own vtable non-empty else None" heuristic missed entirely
        (its own DWARF-visible vtable list is empty here)."""
        types = self._types_by_name(vptr_lib)
        assert types["N"].vptr_offset_bits == 0
        assert not types["N"].vtable  # confirms this exercises the empty-own-vtable path

    def test_two_level_inheritance_chain_resolves(self, vptr_lib: Path) -> None:
        """A derived-of-derived chain (M : N : A) resolves regardless of
        which of the three DWARF happened to emit first."""
        types = self._types_by_name(vptr_lib)
        assert types["M"].vptr_offset_bits == 0

    def test_reordered_base_still_resolves_to_zero(self, vptr_lib: Path) -> None:
        """A non-polymorphic base declared first is laid out after the
        polymorphic one; the primary-base lookup follows the real (ABI)
        offset, not declaration order."""
        types = self._types_by_name(vptr_lib)
        assert types["Y"].base_offsets.get("X") != 0
        assert types["Y"].vptr_offset_bits == 0

    @pytest.fixture()
    def namespaced_vptr_lib(self, tmp_path: Path) -> Path:
        """Namespaced inherited-only classes, plus an unrelated type sharing
        the same bare name in a different namespace -- reproduces the gap
        Codex review found: ``RecordType.bases`` stores each base's *bare*
        name, but ``self.types`` is keyed by *qualified* name once a
        namespace is involved, so a name-only lookup can silently fail (or,
        worse, resolve to the wrong same-named type in a different scope)."""
        cpp_src = tmp_path / "ns_vptr.cpp"
        cpp_src.write_text("""\
namespace ns {
struct A { virtual void a(); int ai; };
struct N : A { int ni; };      // inherited-only, same shape as the plain
                                // (non-namespaced) N case above
struct M : N { int mi; };      // two-level chain, still namespaced
}

// A DIFFERENT, non-polymorphic type sharing the bare name "A" in another
// namespace -- must never be confused with ns::A during resolution.
namespace other { struct A { int x; }; }

void ns::A::a() {}

static ns::N g_n;
static ns::M g_m;
static other::A g_other_a;
extern "C" ns::N* get_n() { return &g_n; }
extern "C" ns::M* get_m() { return &g_m; }
extern "C" other::A* get_other_a() { return &g_other_a; }
""")
        so_path = tmp_path / "libnsvptr.so"
        result = subprocess.run(
            [_GPP, "-shared", "-fPIC", "-g", "-O0", "-o", str(so_path), str(cpp_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_namespaced_inherited_vtable_resolves(
        self, namespaced_vptr_lib: Path
    ) -> None:
        """A namespaced class whose vtable is entirely inherited still
        resolves -- the base-name lookup must not be defeated by
        RecordType.bases storing a bare name while self.types is keyed by
        the qualified name."""
        types = self._types_by_name(namespaced_vptr_lib)
        assert types["ns::N"].vptr_offset_bits == 0
        assert types["ns::M"].vptr_offset_bits == 0

    def test_same_bare_base_name_in_different_namespace_not_confused(
        self, namespaced_vptr_lib: Path
    ) -> None:
        """other::A (non-polymorphic) must never satisfy ns::N's lookup for
        its base "A" just because the bare names collide."""
        types = self._types_by_name(namespaced_vptr_lib)
        assert types["other::A"].vptr_offset_bits is None
        assert types["ns::N"].vptr_offset_bits == 0

    @pytest.fixture()
    def cross_cu_vptr_lib(self, tmp_path: Path) -> Path:
        """A base class defined in one TU, inherited-only from another --
        the derived class's own CU only carries a declaration-only stub DIE
        for the base (or, depending on emission, its own separate full
        definition later deduplicated), never the retained definition's own
        DIE. Reproduces the gap Codex review found: a base-class reference
        can point at a DIE that is neither the retained definition nor
        registered anywhere, unless that DIE is itself resolved back to the
        real type's qualified name."""
        (tmp_path / "a.h").write_text(
            "#pragma once\n"
            "namespace ns { struct A { virtual void a(); int ai; }; }\n"
        )
        (tmp_path / "u1.cpp").write_text(
            '#include "a.h"\n'
            "void ns::A::a() {}\n"
            "static ns::A g_a;\n"
            "extern \"C\" ns::A* get_a() { return &g_a; }\n"
        )
        (tmp_path / "u2.cpp").write_text(
            '#include "a.h"\n'
            "namespace ns { struct D : A { int di; }; }\n"
            "static ns::D g_d;\n"
            "extern \"C\" ns::D* get_d() { return &g_d; }\n"
        )
        o1 = tmp_path / "u1.o"
        o2 = tmp_path / "u2.o"
        for src, obj in ((tmp_path / "u1.cpp", o1), (tmp_path / "u2.cpp", o2)):
            result = subprocess.run(
                [_GPP, "-g", "-O0", "-fPIC", "-c", str(src), "-o", str(obj)],
                capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        so_path = tmp_path / "libcrosscu.so"
        result = subprocess.run(
            [_GPP, "-shared", "-o", str(so_path), str(o1), str(o2)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Link failed: {result.stderr}"
        return so_path

    def test_inherited_vtable_across_translation_units_resolves(
        self, cross_cu_vptr_lib: Path
    ) -> None:
        """A base defined in one TU and only inherited-from (never
        redefined) in another must still resolve -- the inheriting TU's own
        reference to the base is not the retained definition's DIE."""
        types = self._types_by_name(cross_cu_vptr_lib)
        assert types["ns::A"].vptr_offset_bits == 0
        assert types["ns::D"].vptr_offset_bits == 0

    @pytest.fixture()
    def same_bare_name_direct_bases_lib(self, tmp_path: Path) -> Path:
        """One class directly inheriting two DISTINCT bases that happen to
        share a bare name in different namespaces -- reproduces the second
        gap Codex review found: a bare-name-keyed structure can't represent
        two simultaneous direct-base edges with the same name without one
        silently overwriting the other's offset/identity."""
        cpp_src = tmp_path / "collide.cpp"
        cpp_src.write_text("""\
namespace one { struct A { virtual void a(); int ai; }; }
namespace two { struct A { int aj; }; }

// one::A (polymorphic, real offset 0) and two::A (non-polymorphic, a
// non-zero offset) are both named bare "A" -- D's own vptr must resolve
// via one::A specifically, not be lost to the bare-name collision.
struct D : one::A, two::A { int di; };

void one::A::a() {}
static D g_d;
extern "C" D* get_d() { return &g_d; }
""")
        so_path = tmp_path / "libcollide.so"
        result = subprocess.run(
            [_GPP, "-shared", "-fPIC", "-g", "-O0", "-o", str(so_path), str(cpp_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_same_bare_name_direct_bases_do_not_collide(
        self, same_bare_name_direct_bases_lib: Path
    ) -> None:
        """D's primary base (one::A, at the real offset 0) must resolve
        even though a second, unrelated direct base shares its bare name."""
        types = self._types_by_name(same_bare_name_direct_bases_lib)
        assert types["one::A"].vptr_offset_bits == 0
        assert types["two::A"].vptr_offset_bits is None
        assert types["D"].vptr_offset_bits == 0

    @pytest.fixture()
    def nearly_empty_virtual_base_lib(self, tmp_path: Path) -> Path:
        """A class whose ONLY base is a "nearly empty" (no data members of
        its own) virtual base -- the Itanium ABI's virtual-primary-base
        optimization can let the two SHARE one vptr slot at offset 0, with
        GCC emitting no local vptr member for the derived class at all.
        Reproduces the gap Codex review found: because the base is virtual,
        it's entirely excluded from bases/base_edges (mirroring
        base_offsets's own exclusion — a virtual base's offset is dynamic),
        so the primary-base walk never even considers it, and the class
        would resolve to None without the final "0 if polymorphic"
        fallback."""
        cpp_src = tmp_path / "nearly_empty.cpp"
        cpp_src.write_text("""\
// A has no data members of its own -- "nearly empty" for the Itanium ABI's
// virtual-primary-base sharing rule.
struct A { virtual void a(); };
struct E : virtual A { virtual void e(); };

void A::a() {}
void E::e() {}
static E g_e;
extern "C" E* get_e() { return &g_e; }
""")
        so_path = tmp_path / "libnearlyempty.so"
        result = subprocess.run(
            [_GPP, "-shared", "-fPIC", "-g", "-O0", "-o", str(so_path), str(cpp_src)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_nearly_empty_virtual_primary_base_falls_back_to_zero(
        self, nearly_empty_virtual_base_lib: Path
    ) -> None:
        """E's vtable is non-empty (its own e()) but E has no local vptr
        member and its only base is virtual (excluded from the primary-base
        walk) -- must fall back to the pre-fix "0 if polymorphic" heuristic
        rather than regress to None."""
        types = self._types_by_name(nearly_empty_virtual_base_lib)
        assert types["E"].vtable  # confirms this exercises the no-local-member path
        assert types["E"].vptr_offset_bits == 0
