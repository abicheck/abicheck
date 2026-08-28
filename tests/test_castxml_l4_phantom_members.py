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

"""``Function.is_compiler_generated`` — closes the castxml L4 extractor bug
documented in ``AGENTS.md``'s "PR C" known-gaps entry. Split out of
``test_source_extractors.py`` to keep that module under the AI-readiness
file-size hard cap; see ``test_castxml_compiler_generated.py`` (the castxml
parser level, hand-built XML), ``test_dumper_clang_compiler_generated.py``
(the direct-clang parser level), and
``test_serialization_function_compiler_generated.py``'s
``TestFunctionIsCompilerGeneratedRoundTrip`` for this same fix's other test
coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_abi import SourceAbiTu
from abicheck.buildsource.source_extractors.base import entity_from_function
from abicheck.buildsource.source_link import link_source_abi
from abicheck.model import Function, ScopeOrigin


def _no_demangler() -> bool:
    """Mirrors ``test_source_abi.py``'s own helper -- some CI runners (macOS,
    Windows) have no working C++ demangler (cxxfilt/c++filt), and the
    demangled-identity second-tier rematch degrades to a no-op without one."""
    from abicheck.demangle import demangle

    return demangle("_ZN6WidgetC1Ev") is None


needs_demangler = pytest.mark.skipif(
    _no_demangler(), reason="no C++ demangler (cxxfilt/c++filt) available"
)


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++20",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


def test_entity_from_function_stamps_compiler_generated_ownership() -> None:
    # A confirmed compiler-generated declaration (is_compiler_generated is
    # True) stays api_relevant -- the export-table-blind, per-TU mapping
    # stage cannot know whether it is an ODR-used implicit member with a
    # real weak export (Codex review, PR #930) -- but records the fact as
    # `ownership` evidence for link_source_abi's own `_route_declaration` to
    # act on downstream, once the export table is actually known. See the
    # test below for that gated exclusion.
    common = dict(
        name="Widget",
        mangled="_ZN6WidgetaSERKS_",
        return_type="Widget&",
        source_header="include/widget.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    synthesized = entity_from_function(Function(is_compiler_generated=True, **common))
    assert synthesized.api_relevant is True
    assert synthesized.ownership.get("compiler_generated") == "true"

    # The positive control: an identical, genuinely user-written declaration
    # (is_compiler_generated False) carries no such hint.
    user_written = entity_from_function(
        Function(is_compiler_generated=False, **common)
    )
    assert user_written.api_relevant is True
    assert "compiler_generated" not in user_written.ownership

    # An older snapshot / DWARF-only path that never captured the fact
    # (is_compiler_generated is None -- "not captured", not "confirmed
    # user-written") carries no hint either -- only a confirmed True stamps
    # one, matching every other tri-state confirmed-only marker in this
    # codebase.
    unknown = entity_from_function(Function(is_compiler_generated=None, **common))
    assert unknown.api_relevant is True
    assert "compiler_generated" not in unknown.ownership


def test_link_source_abi_drops_unmatched_compiler_generated_declarations() -> None:
    """The actual exclusion: `link_source_abi` gives a `compiler_generated`
    entity one export-match attempt, then drops it outright on a confirmed
    miss -- never reaching `reachable_declarations`/`source_decl_to_binary_
    symbol` at all, unlike an ordinary unmatched declaration (which is kept
    and recorded as unmatched)."""
    common = dict(
        name="Widget",
        mangled="_ZN6WidgetaSERKS_",
        return_type="Widget&",
        source_header="include/widget.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    phantom = entity_from_function(Function(is_compiler_generated=True, **common))
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[phantom])

    # A real, non-empty export table was consulted and genuinely doesn't
    # name this symbol -- the trivial, never-emitted-out-of-line case this
    # fix exists for. (An *empty* export set is "unresolved", not "confirmed
    # absent" -- see the next test.)
    surface = link_source_abi([tu], exported_symbols=["_Z9unrelatedv"])
    assert phantom.id not in {d.id for d in surface.reachable_declarations}
    assert phantom.identity() not in surface.mappings["source_decl_to_binary_symbol"]

    # A real export IS present -- the ODR-used case (e.g. a public function
    # returning Widget by value calls this implicit copy assignment). It
    # must be linked like any ordinary declaration, not dropped.
    surface_matched = link_source_abi(
        [tu], exported_symbols=["_ZN6WidgetaSERKS_"]
    )
    assert phantom.id in {d.id for d in surface_matched.reachable_declarations}
    assert (
        surface_matched.mappings["source_decl_to_binary_symbol"][phantom.identity()]
        == "_ZN6WidgetaSERKS_"
    )


def test_link_source_abi_keeps_compiler_generated_entities_when_exports_unresolved() -> (
    None
):
    """Codex review, PR #930: a Flow-2/parallel-baseline source-only link
    (`link_source_abi([tu], exported_symbols=[])`, the exact shape
    `relink_surface_exports`'s own docstring describes) must not drop a
    `compiler_generated` candidate outright -- an EMPTY export set means
    the export table has not been resolved yet, not that a real one was
    checked and came up empty. Dropping it here would permanently lose it
    before `relink_surface_exports`'s later pass, against the real export
    set, ever gets a chance to recover it."""
    common = dict(
        name="Widget",
        mangled="_ZN6WidgetaSERKS_",
        return_type="Widget&",
        source_header="include/widget.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    phantom = entity_from_function(Function(is_compiler_generated=True, **common))
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[phantom])

    surface = link_source_abi([tu], exported_symbols=[])
    assert phantom.id in {d.id for d in surface.reachable_declarations}

    from abicheck.buildsource.source_link import relink_surface_exports

    relinked = relink_surface_exports(surface, ["_ZN6WidgetaSERKS_"])
    assert (
        relinked.mappings["source_decl_to_binary_symbol"][phantom.identity()]
        == "_ZN6WidgetaSERKS_"
    )


def test_relink_surface_exports_drops_a_generated_candidate_confirmed_unmatched() -> (
    None
):
    """Codex review, PR #930: `relink_surface_exports()` (the parallel-
    baseline/Flow-2 relink, run once the real export set finally becomes
    known) must apply the identical `compiler_generated`-miss drop rule
    `_route_declaration` applies at first link -- not merely recompute the
    mapping for whatever survived the empty-export-set first link. A
    candidate first linked with `exported_symbols=[]` (kept, per the
    "unresolved, not confirmed-miss" rule) must be dropped from
    `reachable_declarations`/`decls_without_symbol` once relinked against a
    real export set that genuinely never mentions it -- the same outcome
    linking directly against that binary would have produced. Also asserts
    `coverage["reachable_declarations"]` is refreshed to the post-drop
    count, not left at the empty-export first link's unfiltered stamp --
    `crosscheck._surface_boundary_counters` prefers that coverage value
    over the live list length whenever it's nonzero, so a stale count would
    keep counting the removed phantom past this relink (Codex review)."""
    common = dict(
        name="Widget",
        mangled="_ZN6WidgetaSERKS_",
        return_type="Widget&",
        source_header="include/widget.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    phantom = entity_from_function(Function(is_compiler_generated=True, **common))
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[phantom])

    surface = link_source_abi([tu], exported_symbols=[])
    assert phantom.id in {d.id for d in surface.reachable_declarations}
    assert surface.coverage["reachable_declarations"] == 1

    from abicheck.buildsource.source_link import relink_surface_exports

    # A real, non-empty export set that never mentions this symbol at all.
    relinked = relink_surface_exports(surface, ["_ZN5UnrelatedC1Ev"])
    assert phantom.id not in {d.id for d in relinked.reachable_declarations}
    assert phantom.identity() not in relinked.mappings["source_decl_to_binary_symbol"]
    assert relinked.coverage["reachable_declarations"] == 0
    assert phantom.qualified_name not in relinked.unmatched["decls_without_symbol"]


def test_link_source_abi_rescues_a_synthetic_ctor_key_with_a_real_export() -> None:
    """Codex review, PR #930: a castxml synthetic constructor key (no real
    mangled name -- see ``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX``) can
    never match `_match_export`'s direct name comparison, so an ODR-used
    implicit constructor with a real Itanium export (`_ZN6WidgetC1ERKS_`/
    `_ZN6WidgetC2ERKS_`) needs the class-level ctor/dtor owner-index rescue
    (`ctor_export_match`) to be preserved rather than lost."""
    from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

    synthetic_ctor = entity_from_function(
        Function(
            name="Widget",
            mangled=f"{SYNTHETIC_CTOR_KEY_PREFIX}Widget(Widget const&)",
            return_type="",
            source_header="include/widget.h",
            origin=ScopeOrigin.PUBLIC_HEADER,
            is_compiler_generated=True,
        )
    )
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[synthetic_ctor])

    # A real export for a *different* class's constructor -- confirms this
    # isn't a vacuous "any ctor export rescues everything" match.
    surface_no_match = link_source_abi(
        [tu], exported_symbols=["_ZN5OtherC1ERKS_"]
    )
    assert synthetic_ctor.id not in {
        d.id for d in surface_no_match.reachable_declarations
    }

    # The real export IS for this class's constructor (a different clone --
    # C2 vs. the synthetic key's own unspecified overload -- deliberately
    # imprecise at the per-overload level, see ctor_export_match's own
    # docstring): rescued.
    surface_matched = link_source_abi(
        [tu], exported_symbols=["_ZN6WidgetC2ERKS_"]
    )
    assert synthetic_ctor.id in {d.id for d in surface_matched.reachable_declarations}


def test_link_source_abi_rescues_a_synthetic_ctor_key_for_an_abi_tagged_owner() -> (
    None
):
    """Codex review, PR #930: `itanium_scope_components` renders an ABI-tagged
    owner (`__attribute__((abi_tag("v1")))`) as `"Widget[abi:v1]"`, but
    castxml's own synthetic ctor key encodes only the plain source-level
    class name (`"Widget"`), never the tag. The owner-index rescue must
    strip the tag before matching, or an ODR-used implicit constructor of
    an ABI-tagged public class is wrongly dropped even though its real
    weak C1/C2 exports exist."""
    from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

    synthetic_ctor = entity_from_function(
        Function(
            name="Widget",
            mangled=f"{SYNTHETIC_CTOR_KEY_PREFIX}Widget(Widget const&)",
            return_type="",
            source_header="include/widget.h",
            origin=ScopeOrigin.PUBLIC_HEADER,
            is_compiler_generated=True,
        )
    )
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[synthetic_ctor])

    # A real, ABI-tagged export of this same class's constructor.
    surface = link_source_abi([tu], exported_symbols=["_ZN6WidgetB2v1C1ERKS_"])
    assert synthetic_ctor.id in {d.id for d in surface.reachable_declarations}


def test_link_source_abi_rescues_a_synthetic_ctor_key_via_msvc_export() -> None:
    """Codex review, PR #930: on a Windows/MSVC L4 run (castxml's own
    ``--castxml-cc-msvc`` emulation mode), an ODR-used implicit
    constructor/destructor's real export is MSVC-mangled
    (``??0Widget@@QEAA@XZ``), not Itanium -- `itanium_scope_components`
    alone never recognizes it, so the owner-index rescue must also
    recognize the MSVC plain-ctor/dtor operator codes (`??0`/`??1`)."""
    from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

    synthetic_ctor = entity_from_function(
        Function(
            name="Widget",
            mangled=f"{SYNTHETIC_CTOR_KEY_PREFIX}Widget(Widget const&)",
            return_type="",
            source_header="include/widget.h",
            origin=ScopeOrigin.PUBLIC_HEADER,
            is_compiler_generated=True,
        )
    )
    tu = SourceAbiTu(tu_id="cu://widget.cpp#cfg", functions=[synthetic_ctor])

    # A real export for a *different* class's MSVC-mangled constructor --
    # confirms this isn't a vacuous "any MSVC ctor export rescues everything".
    surface_no_match = link_source_abi(
        [tu], exported_symbols=["??0Other@@QEAA@XZ"]
    )
    assert synthetic_ctor.id not in {
        d.id for d in surface_no_match.reachable_declarations
    }

    # The real, MSVC-mangled export IS for this class's constructor: rescued.
    surface_matched = link_source_abi(
        [tu], exported_symbols=["??0Widget@@QEAA@XZ"]
    )
    assert synthetic_ctor.id in {d.id for d in surface_matched.reachable_declarations}


@needs_demangler
def test_link_source_abi_rescues_a_generated_operator_via_demangled_rematch() -> None:
    """Codex review, PR #930: a `compiler_generated` entity must reach
    `_demangled_rematch`'s second-tier substitution/ABI-tag-drift rescue
    the same way an ordinary declaration does. castxml's implicit
    `operator=`'s own real mangled name (`_ZN1AaSERKS_`, using the `S_`
    self-substitution shorthand) and a real export spelled without that
    substitution (`_ZN1AaSERK1A`) demangle identically
    (`A::operator=(A const&)`) but are exact-match unequal -- dropping the
    entity before this second tier runs (as `_route_declaration` used to)
    would lose it even though the export genuinely exists."""
    copy_assign = entity_from_function(
        Function(
            name="A",
            mangled="_ZN1AaSERKS_",
            return_type="A&",
            source_header="include/a.h",
            origin=ScopeOrigin.PUBLIC_HEADER,
            is_compiler_generated=True,
        )
    )
    tu = SourceAbiTu(tu_id="cu://a.cpp#cfg", functions=[copy_assign])

    surface = link_source_abi([tu], exported_symbols=["_ZN1AaSERK1A"])
    assert copy_assign.id in {d.id for d in surface.reachable_declarations}
    assert (
        surface.mappings["source_decl_to_binary_symbol"][copy_assign.identity()]
        == "_ZN1AaSERK1A"
    )


@pytest.mark.integration
def test_castxml_l4_extract_excludes_implicit_special_members_from_reachable_surface(
    tmp_path: Path,
) -> None:
    """Real castxml, real g++, the exact repro from AGENTS.md's "PR C"
    known-gaps entry: ``struct Widget { int x; int y; int sum() const; };``
    compiled and dumped through the actual ``CastxmlSourceExtractor.extract``
    -> ``link_source_abi`` pipeline (not a synthetic fixture), confirming the
    compiler-synthesized implicit special members (constructors, destructor,
    copy/move ``operator=``) no longer reach the linked reachable-declaration
    surface as public API, and the real ``sum()`` method still does.

    Before this fix, this exact shape reproduced a false-positive
    ``source_binary_provenance_mismatch``-shaped signal: 6 of 7 exportable
    declarations never mapped to any exported binary symbol, purely because
    5 of the 6 were phantom implicit members that are essentially never
    emitted as their own out-of-line symbol.
    """
    import shutil
    import subprocess

    from abicheck.buildsource.source_extractors.castxml import CastxmlSourceExtractor
    from abicheck.buildsource.source_link import link_source_abi

    if shutil.which("g++") is None:
        pytest.skip("needs a real g++ toolchain")

    header = tmp_path / "widget.h"
    header.write_text(
        "struct Widget { int x; int y; int sum() const; };\n", encoding="utf-8"
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint Widget::sum() const { return x + y; }\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", "libwidget.so", "widget.cpp"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    cu = _cu(
        source="widget.cpp",
        directory=str(tmp_path),
        standard="c++17",
        argv=["g++", "-std=c++17", "-fPIC", "-c", "widget.cpp", "-o", "widget.o"],
    )
    extractor = CastxmlSourceExtractor()
    if not extractor.available():
        pytest.skip("needs a real castxml toolchain")

    tu = extractor.extract(
        cu, public_header_roots=["widget.h"], target_id="target://widget"
    )

    # Ground truth: castxml really did emit the phantom entries (this is the
    # bug's own precondition, not this fix's own claim) -- more entities
    # than the one real user-written method.
    assert len(tu.functions) > 1

    # Every phantom entry is stamped with the ownership hint that lets
    # `link_source_abi` (below) give it one export-match chance rather than
    # excluding it outright at this per-TU stage -- an ODR-used implicit
    # member can have a real weak export the extractor cannot know about in
    # isolation (Codex review, PR #930).
    phantom_count = sum(
        1 for f in tu.functions if f.ownership.get("compiler_generated") == "true"
    )
    assert phantom_count == len(tu.functions) - 1

    surface = link_source_abi(
        [tu], exported_symbols=["_ZNK6Widget3sumEv"], library="libwidget.so"
    )
    reachable_names = {d.mangled_name for d in surface.reachable_declarations}
    assert reachable_names == {"_ZNK6Widget3sumEv"}
    # The one real declaration matched its real exported symbol -- a clean
    # 1/1, not the pre-fix 1/6 (or 1/7, including `sum` itself) that tripped
    # the false-positive provenance-mismatch heuristic.
    assert surface.coverage.get("matched_symbols") == 1
    assert not surface.unmatched.get("decls_without_symbol")
