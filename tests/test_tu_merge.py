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
"""ADR-050 D4 (G32 Phase C) — abicheck.tu_merge's real compatible-merge
lattice.

Two classes of test:

- Fast, pure-Python tests over fake :class:`~abicheck.tu_fragment.TuFragment`
  entities (module-level determinism/order-independence, the
  TuMergeError-is-not-a-ChangeKind structural guard). No compiler needed.
- Real end-to-end tests loading G32 Phase 0's own committed
  ``odr_safe``/``odr_conflict`` fixture headers (``tests/fixtures/g32/``)
  through the real clang header-AST backend, proving this module actually
  resolves the fixtures the plan names it against -- not just a
  semantically-equivalent fake. Self-skips when clang is unavailable, the
  same convention ``test_dumper_manifest.py``'s own real-backend tests use.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.checker_policy import ChangeKind
from abicheck.errors import TuMergeError
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.tu_fragment import MergedTuFragments, TuFragment
from abicheck.tu_merge import (
    _HETEROGENEOUS_LANG_MODE,
    HETEROGENEOUS_ABI_CONTEXT,
    INCONSISTENT_DECLARATION,
    merge_fragments,
)

_G32_DIR = Path(__file__).parent / "fixtures" / "g32"


def _fn(name: str, mangled: str | None = None, **overrides: object) -> Function:
    overrides.setdefault("return_type", "void")
    return Function(name=name, mangled=mangled or name, **overrides)  # type: ignore[arg-type]


def _var(name: str, mangled: str | None = None, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int", **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural guard: TuMergeError is not a ChangeKind (ADR-050 D4)
# ---------------------------------------------------------------------------


def test_tu_merge_error_codes_are_not_registered_as_change_kinds():
    change_kind_values = {kind.value for kind in ChangeKind} | {
        kind.name for kind in ChangeKind
    }
    assert INCONSISTENT_DECLARATION not in change_kind_values
    assert HETEROGENEOUS_ABI_CONTEXT not in change_kind_values
    assert INCONSISTENT_DECLARATION not in REGISTRY
    assert HETEROGENEOUS_ABI_CONTEXT not in REGISTRY


def test_tu_merge_error_is_a_snapshot_error():
    # Extraction-time failure, not a comparison finding -- existing
    # `except SnapshotError` handling around dump()/compare() must keep
    # catching it unchanged (ADR-050 D4, matching HeaderToolchainError /
    # IncompatibleSnapshotSchemaError precedent).
    from abicheck.errors import SnapshotError

    assert issubclass(TuMergeError, SnapshotError)


# ---------------------------------------------------------------------------
# Mixed AST producers across TUs (HETEROGENEOUS_ABI_CONTEXT)
# ---------------------------------------------------------------------------


def test_merge_fragments_raises_on_mixed_ast_producers():
    # --ast-frontend auto's per-TU fallback can land one TU on castxml and
    # another on clang within the same manifest even though the manifest's
    # own declared compiler/target is uniform -- dumper.py trusts a single
    # representative ast_producer for the whole merged snapshot (gating
    # DWARF layout backfill), so a genuine mix must be rejected rather than
    # silently mislabeled.
    a = TuFragment(tu_name="a", ast_producer="castxml")
    b = TuFragment(tu_name="b", ast_producer="clang")
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == HETEROGENEOUS_ABI_CONTEXT
    assert excinfo.value.tu_names == ("a", "b")


def test_merge_fragments_allows_uniform_ast_producer():
    a = TuFragment(tu_name="a", ast_producer="clang")
    b = TuFragment(tu_name="b", ast_producer="clang")
    merged = merge_fragments([a, b])
    assert merged.ast_producer == "clang"


def test_merge_fragments_raises_on_mixed_frontend_context_kind():
    # CodeRabbit review: every TU is parsed under the manifest's one, uniform
    # frontend_context request against one, uniform compiler binary, so this
    # should be structurally unreachable in practice -- but blindly copying
    # ordered[0]'s value would misrepresent the merged snapshot's provenance
    # if it ever happened, exactly like an unguarded ast_producer copy would.
    a = TuFragment(tu_name="a", ast_producer="clang", frontend_context_kind="host")
    b = TuFragment(tu_name="b", ast_producer="clang", frontend_context_kind="device")
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == HETEROGENEOUS_ABI_CONTEXT
    assert excinfo.value.tu_names == ("a", "b")


def test_merge_fragments_allows_uniform_frontend_context_kind():
    a = TuFragment(tu_name="a", ast_producer="clang", frontend_context_kind="device")
    b = TuFragment(tu_name="b", ast_producer="clang", frontend_context_kind="device")
    merged = merge_fragments([a, b])
    assert merged.frontend_context_kind == "device"


def test_merge_fragments_sentinels_resolved_lang_mode_when_tus_disagree():
    """Codex review, fresh evidence: unlike ast_producer/frontend_context_kind
    above, resolved_lang_mode is NOT guaranteed uniform across TUs -- an
    ordinary mixed-language manifest (some .c TUs, some .cpp TUs, one
    shared compiler) legitimately resolves it differently per TU. Blindly
    copying one representative TU's resolved_lang_mode would silently
    mislabel the whole merged snapshot's language_standard for every other
    TU, so a genuine mismatch must not raise -- this is an expected, common
    shape, unlike the producer/context mismatches above.

    Merely *dropping* the field (an earlier version of this fix) is also
    wrong: dropping it lets ``_resolve_standard_provenance`` fall back to a
    static re-derivation that can be confidently *wrong*, not just
    unknown, for a TU whose language mode came from something invisible to
    the manifest's combined public headers. The merge must instead write
    the explicit ``_HETEROGENEOUS_LANG_MODE`` sentinel, which that function
    recognizes and treats as "cannot determine this at all" (see
    ``dumper_toolchain._resolve_standard_provenance``'s own docstring)."""
    a = TuFragment(
        tu_name="a.c",
        ast_producer="clang",
        ast_toolchain={
            "compiler_selected": "/usr/bin/clang",
            "resolved_lang_mode": "c",
        },
    )
    b = TuFragment(
        tu_name="b.cpp",
        ast_producer="clang",
        ast_toolchain={
            "compiler_selected": "/usr/bin/clang",
            "resolved_lang_mode": "c++",
        },
    )
    merged = merge_fragments([a, b])
    assert merged.ast_toolchain["resolved_lang_mode"] == _HETEROGENEOUS_LANG_MODE
    # The rest of the representative fragment's ast_toolchain survives --
    # only the per-TU-varying field is overridden.
    assert merged.ast_toolchain["compiler_selected"] == "/usr/bin/clang"


def test_merge_fragments_keeps_resolved_lang_mode_when_tus_agree():
    a = TuFragment(
        tu_name="a.cpp",
        ast_producer="clang",
        ast_toolchain={
            "compiler_selected": "/usr/bin/clang",
            "resolved_lang_mode": "c++",
        },
    )
    b = TuFragment(
        tu_name="b.cpp",
        ast_producer="clang",
        ast_toolchain={
            "compiler_selected": "/usr/bin/clang",
            "resolved_lang_mode": "c++",
        },
    )
    merged = merge_fragments([a, b])
    assert merged.ast_toolchain["resolved_lang_mode"] == "c++"


# ---------------------------------------------------------------------------
# Determinism: merge is independent of fragment processing order
# ---------------------------------------------------------------------------


def test_merge_fragments_is_order_independent():
    fragments = [
        TuFragment(tu_name="alpha", functions=(_fn("shared", "_Z6sharedv"),)),
        TuFragment(
            tu_name="beta",
            functions=(_fn("shared", "_Z6sharedv"),),
            types=(RecordType(name="Point", kind="struct", is_opaque=True),),
        ),
        TuFragment(
            tu_name="gamma",
            types=(
                RecordType(
                    name="Point",
                    kind="struct",
                    fields=[TypeField(name="x", type="int")],
                ),
            ),
            typedefs={"size_type": "unsigned long"},
        ),
        TuFragment(tu_name="delta", constants={"MAX": "100"}),
    ]
    baseline = merge_fragments(fragments)
    for shuffled in itertools.permutations(fragments):
        assert merge_fragments(list(shuffled)) == baseline


def test_merge_fragments_output_order_is_content_derived_not_input_derived():
    # Swapping which fragment is passed first must not change which entity
    # "wins" a union merge, nor the final tuple order.
    a = TuFragment(tu_name="a", functions=(_fn("shared", "_Z6sharedv"),))
    b = TuFragment(tu_name="b", functions=(_fn("shared", "_Z6sharedv"),))
    assert merge_fragments([a, b]) == merge_fragments([b, a])


# ---------------------------------------------------------------------------
# Same-TU duplicates are not a merge concern (Phase B tolerance, preserved)
# ---------------------------------------------------------------------------


def test_merge_fragments_leaves_intra_fragment_duplicates_untouched():
    # A single TU's own parser output repeating a key (e.g. two destructors
    # both falling back to a synthesized no-mangled-name marker) is not a
    # cross-TU merge concern -- both entries pass through unmerged, even
    # though they'd conflict if compared as a genuine cross-TU pair.
    conflicting_but_same_tu = TuFragment(
        tu_name="a",
        functions=(
            _fn("dtor", "", return_type="void"),
            _fn("dtor2", "", return_type="int"),
        ),
    )
    merged = merge_fragments([conflicting_but_same_tu])
    assert len(merged.functions) == 2


# ---------------------------------------------------------------------------
# static-linkage functions are scoped by TU, not folded/conflicted across TUs
# ---------------------------------------------------------------------------


def test_merge_fragments_keeps_distinct_static_helpers_from_different_tus():
    # Two unrelated TUs each declaring their own private
    # `static void helper(int)` can mangle to the identical `_ZL...`-style
    # spelling (it never needs cross-TU uniqueness -- the symbol never
    # leaves its own object file), but they are two distinct, TU-local
    # entities, not the same declaration redeclared. Differing here (return
    # type) would previously raise a false INCONSISTENT_DECLARATION.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("helper", "_ZL6helperi", is_static=True, return_type="int"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("helper", "_ZL6helperi", is_static=True, return_type="double"),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_reconciles_identical_static_helper_within_one_tu():
    # A single TU's own static helper repeated (the ordinary intra-fragment
    # tolerance) still merges/dedupes normally -- the TU-scoping fix must
    # not turn every static function into a permanent 1-per-TU passthrough
    # that bypasses the "declaration + redeclaration" trivial merge.
    fragment = TuFragment(
        tu_name="a",
        functions=(_fn("helper", "_ZL6helperi", is_static=True),),
    )
    merged = merge_fragments([fragment])
    assert len(merged.functions) == 1


def test_merge_fragments_still_merges_non_static_functions_across_tus():
    # Sanity check that the TU-scoping only applies to static-linkage
    # functions -- an ordinary externally-linked redeclaration across TUs
    # still merges into one entity as before.
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fv"),))
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fv"),))
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_keeps_distinct_anonymous_namespace_functions_from_different_tus():
    # An anonymous-namespace function is exactly as TU-local as a `static`
    # one, but clang never sets storageClass="static" for it (verified
    # empirically) -- so is_static alone would miss this case entirely.
    # Two unrelated TUs' own `namespace { void helper(int); }` mangle
    # identically (`_ZN12_GLOBAL__N_1...`) without being the same entity.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "helper",
                "_ZN12_GLOBAL__N_16helperEi",
                is_static=False,
                return_type="int",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "helper",
                "_ZN12_GLOBAL__N_16helperEi",
                is_static=False,
                return_type="double",
            ),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_still_merges_static_member_functions_across_tus():
    # A static *member* function (`struct Widget { static int make(int); };`)
    # sets storageClass="static" too (verified empirically), but it has the
    # class's own ordinary *external* linkage -- nothing to do with
    # TU-locality. Its mangled name (e.g. "_ZN6Widget4makeEi") carries
    # neither the _ZL prefix nor a _GLOBAL__N_ component, so it must keep
    # merging normally across TUs, not get incorrectly TU-scoped just
    # because is_static happens to be True.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("make", "_ZN6Widget4makeEi", is_static=True),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("make", "_ZN6Widget4makeEi", is_static=True),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_keeps_distinct_static_variables_from_different_tus():
    a = TuFragment(tu_name="a", variables=(_var("state", "_ZL5state", value="1"),))
    b = TuFragment(tu_name="b", variables=(_var("state", "_ZL5state", value="2"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_keeps_distinct_anonymous_namespace_variables_from_different_tus():
    a = TuFragment(
        tu_name="a",
        variables=(_var("anon_state", "_ZN12_GLOBAL__N_110anon_stateE", value="1"),),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("anon_state", "_ZN12_GLOBAL__N_110anon_stateE", value="2"),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_still_merges_static_member_variables_across_tus():
    # `struct Widget { static int counter; };` -- ordinary external
    # linkage, mangles marker-free (e.g. "_ZN6Widget7counterE"); must keep
    # merging normally across TUs.
    a = TuFragment(tu_name="a", variables=(_var("counter", "_ZN6Widget7counterE"),))
    b = TuFragment(tu_name="b", variables=(_var("counter", "_ZN6Widget7counterE"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 1


# ---------------------------------------------------------------------------
# Empty / single-fragment base cases, directly against tu_merge (not just
# the dumper_manifest.merge_tu_fragments alias already covered elsewhere)
# ---------------------------------------------------------------------------


def test_merge_fragments_empty_returns_empty_result():
    merged = merge_fragments([])
    assert isinstance(merged, MergedTuFragments)
    assert merged == MergedTuFragments(
        functions=(),
        variables=(),
        types=(),
        enums=(),
        typedefs={},
        typedefs_qualified={},
        constants={},
        ast_producer="castxml",
        ast_toolchain={},
        ast_fallback_reason=None,
        ast_toolchain_supported=None,
        ast_toolchain_unsupported_reasons=(),
        frontend_context_kind=None,
    )


def test_merge_fragments_variable_union_prefers_non_none_value():
    a = TuFragment(tu_name="a", variables=(_var("g_x", "g_x", value=None),))
    b = TuFragment(tu_name="b", variables=(_var("g_x", "g_x", value="42"),))
    merged = merge_fragments([a, b])
    assert merged.variables[0].value == "42"


def test_merge_fragments_variable_raises_on_conflicting_value():
    # Two different non-None values for the same variable is a genuine
    # conflict, not a "declaration without an initializer" union case.
    a = TuFragment(tu_name="a", variables=(_var("g_x", "g_x", value="1"),))
    b = TuFragment(tu_name="b", variables=(_var("g_x", "g_x", value="2"),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("variable", "g_x")


def test_merge_fragments_function_raises_on_conflicting_default_argument():
    # f(int=1) vs f(int=2): two different non-None defaults for the same
    # parameter is a genuine conflict, not "an added default argument".
    param_one = Param(name="n", type="int", default="1")
    param_two = Param(name="n", type="int", default="2")
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fi", params=[param_one]),))
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fi", params=[param_two]),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("function", "_Z1fi")


def test_merge_fragments_function_unions_default_argument_across_tus():
    param_no_default = Param(name="n", type="int")
    param_with_default = Param(name="n", type="int", default="0")
    a = TuFragment(
        tu_name="a", functions=(_fn("f", "_Z1fi", params=[param_no_default]),)
    )
    b = TuFragment(
        tu_name="b", functions=(_fn("f", "_Z1fi", params=[param_with_default]),)
    )
    merged = merge_fragments([a, b])
    assert merged.functions[0].params[0].default == "0"


def test_merge_fragments_function_reconciles_differing_parameter_names():
    # void f(int value); vs void f(int n); -- parameter names are not part
    # of a C/C++ function's type, so this is a routine compatible
    # redeclaration, not a conflict.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fi", params=[Param(name="value", type="int")]),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fi", params=[Param(name="n", type="int")]),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_prefers_public_header_provenance():
    # TU "a" (sorted first) reaches the declaration only through a private,
    # differently-named header; TU "b" reaches the identical declaration
    # through the declared public one. Deliberately different basenames
    # ("detail.h" vs "api.h") so this doesn't accidentally pass via
    # provenance.classify_origin's own basename-fallback matching (D3) --
    # it must be the merge's public-origin preference doing the work, not a
    # same-basename coincidence. The merged entity must carry the public
    # location, not arbitrarily whichever TU happened to sort first --
    # otherwise a genuinely public declaration would misclassify as private
    # once abicheck.provenance.apply_provenance runs on the merged snapshot.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fv", source_location="internal/detail.h:1"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fv", source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert merged.functions[0].source_location == "include/api.h:1"


def test_merge_fragments_keeps_public_provenance_when_the_first_sorted_tu_is_public():
    # The mirror of the case above: TU "a" (sorted first) is itself the
    # public one this time, TU "b" is private -- `_more_public_of` must
    # short-circuit on `a` without needing to fall through to check `b`.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fv", source_location="include/api.h:1"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fv", source_location="internal/detail.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert merged.functions[0].source_location == "include/api.h:1"


def test_merge_fragments_function_keeps_parameter_names_from_the_winning_side():
    # A private `void f(int internal_name)` in TU "a" and the identical
    # public `void f(int public_name)` in TU "b" must merge to a function
    # attributed to the public header *and* spelled with the public
    # declaration's own parameter name -- not "internal_name" merely
    # because "a" sorted first and fed the parameter list unconditionally.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="internal_name", type="int")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="public_name", type="int")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    winner = merged.functions[0]
    assert winner.source_location == "include/api.h:1"
    assert winner.params[0].name == "public_name"


def test_merge_fragments_function_does_not_leak_default_from_private_side_onto_public_winner():
    # The public side ("b") wins both provenance and parameter names, and a
    # default argument only the *private* side ("a") declares must NOT be
    # pulled onto it: a private-only header adding `= 0` to a parameter the
    # public header declares without a default does not give the library's
    # real public consumers -- who never see the private header -- the
    # ability to call `f()` with that argument omitted. Unioning it in
    # anyway would misrepresent the public API's actual capability and
    # make a later change to the private-only default surface as a false
    # PARAM_DEFAULT_VALUE_REMOVED/CHANGED finding against the public
    # surface (Codex review, PR #635 round 17 -- this test previously
    # asserted the opposite, unioning-favorable behavior; that was itself
    # the bug).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="internal_name", type="int", default="0")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="public_name", type="int")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    winner = merged.functions[0]
    assert winner.params[0].default is None
    assert winner.source_location == "include/api.h:1"
    assert winner.params[0].name == "public_name"


def test_merge_fragments_function_unions_default_from_public_side_onto_private_winner():
    # The mirror case: the *public* side ("b") is the one declaring the
    # default this time. A default the public header itself grants must
    # still reach the merged declaration regardless of which side
    # _more_public_of happened to pick as the structural "base" --
    # here "a" (private) sorts first and has no public-header context of
    # its own to lose to, so it wins as the deterministic tie-break, but
    # the public side's own default is not something to discard.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="internal_name", type="int")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="public_name", type="int", default="0")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    winner = merged.functions[0]
    assert winner.source_location == "include/api.h:1"
    assert winner.params[0].default == "0"


def test_merge_fragments_function_unions_default_when_neither_side_is_public():
    # public_header_paths *is* supplied, but neither TU's declaration
    # matches it -- base is only the arbitrary tu_name tie-break, not a
    # proven-public side, so _other_is_strictly_less_public must not treat
    # `other` as less public than it (there's nothing to be less public
    # *than*), and the default still unions in as before round 17.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int", default="0")],
                source_location="internal/one.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int")],
                source_location="internal/two.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].params[0].default == "0"


def test_merge_fragments_function_unions_default_when_public_status_is_unknown():
    # With no public_header_paths/public_header_dirs supplied at all,
    # _other_is_strictly_less_public can't prove anything about either
    # side, so the pre-round-17 "union whichever side has a default"
    # behavior is unchanged -- the narrower private-leak check only
    # applies when public status is actually known.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn("f", "_Z1fi", params=[Param(name="n", type="int", default="0")]),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fi", params=[Param(name="n", type="int")]),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1
    assert merged.functions[0].params[0].default == "0"


def test_merge_fragments_without_public_set_keeps_deterministic_default():
    # No public_header_paths/public_header_dirs supplied -- origin
    # classification stays opt-in (matches abicheck.provenance's own
    # default), so the tu_name-sorted-first side's location wins, unchanged
    # from before this fix.
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fv", source_location="a.h:1"),))
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fv", source_location="b.h:1"),))
    merged = merge_fragments([a, b])
    assert merged.functions[0].source_location == "a.h:1"


def test_merge_fragments_function_tolerates_conflicting_default_from_private_side():
    # f(int = 1) on the public side vs f(int = 2) on a private-only
    # redeclaration is not visible to real public consumers -- who only
    # ever see the public default -- so the merge must keep the public
    # default rather than aborting extraction (Codex review, PR #635
    # round 18; this check was previously unconditional, running before
    # _other_is_strictly_less_public even had a chance to apply).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int", default="2")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int", default="1")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].params[0].default == "1"


def test_merge_fragments_function_raises_on_conflicting_default_when_neither_side_is_public():
    # public_header_paths is supplied but neither declaration matches it --
    # base is only the arbitrary tie-break, not a proven-public side, so a
    # genuine default conflict is still surfaced (unchanged from before
    # round 18's visibility carve-out).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int", default="2")],
                source_location="internal/one.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int", default="1")],
                source_location="internal/two.h:1",
            ),
        ),
    )
    with pytest.raises(TuMergeError):
        merge_fragments(
            [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
        )


def test_merge_fragments_function_does_not_leak_private_contract_attribute_onto_public():
    # The public declaration is unannotated; a private-only redeclaration
    # adds `[[nodiscard]]` (captured as `warn_unused_result`). That
    # attribute is not visible to real public consumers, so it must not
    # appear on the merged public declaration -- otherwise later removing
    # it from the private header alone would surface a false
    # FUNC_CONTRACT_ATTRIBUTE_REMOVED against a public surface that never
    # actually had it (Codex review, PR #635 round 18).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int")],
                contract_attributes=["warn_unused_result"],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int")],
                contract_attributes=[],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == []


def test_merge_fragments_function_keeps_calling_convention_attribute_from_private_side():
    # Unlike a source-facing semantic attribute, a calling-convention token
    # describes the actual compiled function's ABI regardless of which
    # header spells it -- so it is kept (and still validated for conflicts)
    # even when it only appears on the private side (Codex review, PR #635
    # round 18).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int")],
                contract_attributes=["ms_abi"],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="n", type="int")],
                contract_attributes=[],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["ms_abi"]


def test_merge_fragments_function_does_not_leak_private_deprecated_message():
    # The public declaration carries no deprecation; a private-only
    # redeclaration marks it [[deprecated]]. Real public consumers never
    # see that annotation, so it must not land on the merged public
    # declaration -- otherwise later removing it privately would surface a
    # false FUNC_DEPRECATED_REMOVED against the public surface (Codex
    # review, PR #635 round 18).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fv",
                deprecated="internal note",
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fv", source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].deprecated is None


def test_merge_fragments_variable_tolerates_conflicting_value_from_private_side():
    # extern int x = 2 on a private-only redeclaration vs the public
    # extern int x = 1 -- the variable analogue of the function
    # conflicting-default carve-out above (Codex review, PR #635 round 18).
    a = TuFragment(
        tu_name="a",
        variables=(_var("x", value="2", source_location="internal/detail.h:1"),),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("x", value="1", source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.variables) == 1
    assert merged.variables[0].value == "1"


def test_merge_fragments_variable_does_not_leak_private_value_onto_public_extern():
    # A private-only redeclaration provides an initializer the public
    # extern declaration doesn't have; that initializer is not visible to
    # public consumers and must not be pulled onto the merged public
    # declaration (Codex review, PR #635 round 18).
    a = TuFragment(
        tu_name="a",
        variables=(_var("x", value="2", source_location="internal/detail.h:1"),),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("x", value=None, source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.variables) == 1
    assert merged.variables[0].value is None


def test_merge_fragments_variable_does_not_leak_private_deprecated_message():
    a = TuFragment(
        tu_name="a",
        variables=(
            _var(
                "x",
                deprecated="internal note",
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("x", source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.variables) == 1
    assert merged.variables[0].deprecated is None


def test_merge_fragments_type_forward_decl_does_not_leak_private_deprecated_message():
    # _with_more_public_provenance's deprecated union must also respect a
    # provably-private fallback side (Codex review, PR #635 round 18).
    public_forward_decl = RecordType(
        name="X", kind="struct", is_opaque=True, source_location="include/api.h:1"
    )
    private_definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        deprecated="internal note",
        source_location="internal/detail.h:1",
    )
    a = TuFragment(tu_name="a", types=(public_forward_decl,))
    b = TuFragment(tu_name="b", types=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.source_location == "include/api.h:1"
    assert winner.fields == [TypeField(name="a", type="int")]
    assert winner.deprecated is None


def test_merge_fragments_type_two_definitions_does_not_leak_private_deprecated_message():
    # _merge_identical_modulo_provenance's deprecated union must likewise
    # respect a provably-private fallback side (Codex review, PR #635
    # round 18).
    public_definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        source_location="include/api.h:1",
    )
    private_definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        deprecated="internal note",
        source_location="internal/detail.h:1",
    )
    a = TuFragment(tu_name="a", types=(private_definition,))
    b = TuFragment(tu_name="b", types=(public_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.source_location == "include/api.h:1"
    assert winner.deprecated is None


# ---------------------------------------------------------------------------
# _merge_group: same-TU "extras" tolerance vs. genuine cross-TU conflicts
# ---------------------------------------------------------------------------


def test_merge_fragments_raises_when_a_tus_repeat_conflicts_with_another_tu():
    # TU "b" contributes two entries under the same key (f(): void and
    # f(): int -- e.g. two declarations that happen to collide on a
    # producer-derived key); TU "a" contributes a single, conflicting
    # f(): void. Whichever of "b"'s two entries is checked against "a"
    # must actually be checked -- previously, whichever entry ended up
    # sharing the *accumulator's* tu_name (an accident of processing
    # order, not the entry's own tu_name) rode through completely
    # unvalidated, so this conflict was silently absorbed into the merged
    # snapshot rather than raising (Codex review, PR #635 round 18).
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fv"),))
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("f", "_Z1fv"),
            _fn("f", "_Z1fv", return_type="int"),
        ),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_raises_on_a_tus_repeat_conflict_regardless_of_fragment_order():
    # Same scenario as above, but with "b"'s two entries listed in the
    # opposite order within its own fragment -- before round 18's fix,
    # this ordering difference alone flipped the outcome between "raises"
    # and "silently merges", even though the actual conflict is identical
    # (Codex review, PR #635 round 18).
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fv"),))
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("f", "_Z1fv", return_type="int"),
            _fn("f", "_Z1fv"),
        ),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_tolerates_a_single_tus_own_repeated_key():
    # TU "b" contributes two entries under the same key (the castxml
    # synthesized-no-mangled-name-marker scenario the "extras" carve-out
    # exists for); TU "a" contributes an unrelated function, so no *other*
    # TU contributes to the "f" key at all. There is no cross-TU
    # accumulator to validate "f"'s repeats against, so both ride through
    # untouched, exactly as before round 18.
    a = TuFragment(tu_name="a", functions=(_fn("g", "_Z1gv"),))
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("f", "_Z1fv"),
            _fn("f", "_Z1fv", return_type="int"),
        ),
    )
    merged = merge_fragments([a, b])
    f_entries = [fn for fn in merged.functions if fn.name == "f"]
    assert len(f_entries) == 2
    assert {fn.return_type for fn in f_entries} == {"void", "int"}


def test_merge_fragments_types_with_same_bare_name_different_namespace_do_not_collide():
    # RecordType.name is deliberately bare; the namespace lives in
    # qualified_name. Two genuinely unrelated types sharing a leaf name
    # ("one::X" and "two::X") must not spuriously conflict just because
    # both key on the bare name "X".
    one_x = RecordType(
        name="X",
        kind="struct",
        qualified_name="one::X",
        fields=[TypeField(name="a", type="int")],
    )
    two_x = RecordType(
        name="X",
        kind="struct",
        qualified_name="two::X",
        fields=[TypeField(name="b", type="double")],
    )
    a = TuFragment(tu_name="a", types=(one_x,))
    b = TuFragment(tu_name="b", types=(two_x,))
    merged = merge_fragments([a, b])
    assert {t.qualified_name for t in merged.types} == {"one::X", "two::X"}


def test_merge_fragments_enums_with_same_bare_name_different_namespace_do_not_collide():

    one_e = EnumType(
        name="E", qualified_name="one::E", members=[EnumMember(name="A", value=0)]
    )
    two_e = EnumType(
        name="E", qualified_name="two::E", members=[EnumMember(name="B", value=0)]
    )
    a = TuFragment(tu_name="a", enums=(one_e,))
    b = TuFragment(tu_name="b", enums=(two_e,))
    merged = merge_fragments([a, b])
    assert {e.qualified_name for e in merged.enums} == {"one::E", "two::E"}


def test_merge_fragments_type_forward_decl_provenance_wins_over_private_definition():
    # A public header forward-declares Point; the full definition lives
    # only in a private implementation header. The merged type must keep
    # the definition's fields but the *public* forward declaration's
    # source_location -- otherwise apply_provenance would read a genuinely
    # public type as private.
    public_forward = RecordType(
        name="Point", kind="struct", is_opaque=True, source_location="include/api.h:1"
    )
    private_definition = RecordType(
        name="Point",
        kind="struct",
        fields=[TypeField(name="x", type="int")],
        source_location="internal/detail.h:9",
    )
    a = TuFragment(tu_name="a", types=(public_forward,))
    b = TuFragment(tu_name="b", types=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.fields == private_definition.fields
    assert winner.source_location == "include/api.h:1"


def test_merge_fragments_type_two_definitions_with_differing_source_header_merges():
    # ADR-063 Phase 5 (Codex review, P1): RecordType.source_header now
    # carries a Fact[str | None] sibling. _blank_provenance blanks
    # source_header to None for this equality check, but a bare
    # dataclasses.replace() left the pre-blank source_header_fact carried
    # forward unchanged -- __post_init__'s "explicit Fact wins" rule then
    # read the two (genuinely differing, since each side's real
    # source_header sets Fact.present(<that header>)) facts as still
    # disagreeing even after the legacy field was blanked, so this
    # otherwise-routine cross-TU redeclaration spuriously raised
    # INCONSISTENT_DECLARATION instead of merging.
    a_side = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        source_header="a.h",
    )
    b_side = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="a", type="int")],
        source_header="b.h",
    )
    a = TuFragment(tu_name="a", types=(a_side,))
    b = TuFragment(tu_name="b", types=(b_side,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    winner = merged.types[0]
    # The two representations must agree on the merged winner too --
    # whichever source_header value it kept, its own source_header_fact
    # must describe that same value, not a stale one from either side.
    assert winner.source_header_fact.value == winner.source_header


def test_merge_fragments_type_forward_decl_source_header_fact_stays_synced():
    # ADR-063 Phase 5 (Codex review, P1): _with_more_public_provenance's
    # replace() call carries a new source_header value from the winning
    # side's provenance without also updating source_header_fact -- the
    # merged type's two representations must not disagree.
    public_forward = RecordType(
        name="Point",
        kind="struct",
        is_opaque=True,
        source_location="include/api.h:1",
        source_header="include/api.h",
    )
    private_definition = RecordType(
        name="Point",
        kind="struct",
        fields=[TypeField(name="x", type="int")],
        source_location="internal/detail.h:9",
        source_header="internal/detail.h",
    )
    a = TuFragment(tu_name="a", types=(public_forward,))
    b = TuFragment(tu_name="b", types=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.source_header == "include/api.h"
    assert winner.source_header_fact.value == "include/api.h"


def test_merge_fragments_enum_forward_decl_provenance_wins_over_private_definition():

    public_forward = EnumType(
        name="Color", members=[], source_location="include/api.h:1"
    )
    private_definition = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="internal/detail.h:9",
    )
    a = TuFragment(tu_name="a", enums=(public_forward,))
    b = TuFragment(tu_name="b", enums=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.enums) == 1
    winner = merged.enums[0]
    assert winner.members == private_definition.members
    assert winner.source_location == "include/api.h:1"


def test_merge_fragments_type_raises_on_conflicting_kind_for_opaque_pair():
    # A `union X;` forward declaration is not compatible with a
    # `struct X { ... };` definition, even though both key on the bare
    # name "X" -- the opaque/definition merge must check `kind` too.
    forward_union = RecordType(name="X", kind="union", is_opaque=True)
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward_union,))
    b = TuFragment(tu_name="b", types=(struct_definition,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_type_reconciles_class_and_struct_forward_definition_pair():
    # `class X;` forward-declared, then `struct X { ... };` defined -- valid,
    # ordinary C++ (both GCC and Clang accept it): struct/class are the same
    # underlying entity, differing only in default member access, unlike
    # `union` which is a genuinely different type category.
    forward_class = RecordType(name="X", kind="class", is_opaque=True)
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward_class,))
    b = TuFragment(tu_name="b", types=(struct_definition,))
    merged = merge_fragments([a, b])
    assert merged.types == (struct_definition,)


def test_merge_fragments_enum_raises_on_conflicting_underlying_type_for_forward_pair():
    # `enum E : int;` is not compatible with `enum E : unsigned { X };`,
    # even though the forward declaration has no members to compare against
    # the definition's -- the empty-members/definition merge must check
    # underlying_type/is_scoped too.

    forward = EnumType(name="E", members=[], underlying_type="int")
    definition = EnumType(
        name="E", members=[EnumMember(name="X", value=0)], underlying_type="unsigned"
    )
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "E")


def test_merge_fragments_variable_raises_on_conflicting_type():
    a = TuFragment(
        tu_name="a", variables=(Variable(name="g_x", mangled="g_x", type="int"),)
    )
    b = TuFragment(
        tu_name="b", variables=(Variable(name="g_x", mangled="g_x", type="double"),)
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("variable", "g_x")


def test_merge_fragments_enum_reconciles_forward_declaration_and_definition():
    forward = EnumType(name="Color", members=[])

    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    merged = merge_fragments([a, b])
    assert merged.enums == (definition,)


def test_merge_fragments_enum_raises_on_conflicting_members():

    a = TuFragment(
        tu_name="a",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=0)]),),
    )
    b = TuFragment(
        tu_name="b",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=1)]),),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "Color")


def test_merge_fragments_function_raises_on_differing_param_count():
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fi", params=[Param(name="x", type="int")]),),
    )
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fi", params=[]),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("function", "_Z1fi")


def test_merge_fragments_type_reconciles_definition_then_opaque_across_tus():
    # Same shape as the forward-decl/definition test above, but with the
    # roles swapped (a = definition, b = opaque) -- both directions of the
    # is_opaque branch must merge, not just a-opaque-first.
    definition = RecordType(
        name="Point", kind="struct", fields=[TypeField(name="x", type="int")]
    )
    forward = RecordType(name="Point", kind="struct", is_opaque=True)
    a = TuFragment(tu_name="a", types=(definition,))
    b = TuFragment(tu_name="b", types=(forward,))
    merged = merge_fragments([a, b])
    assert merged.types == (definition,)


def test_merge_fragments_type_raises_on_conflicting_kind_for_reversed_opaque_pair():
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    forward_union = RecordType(name="X", kind="union", is_opaque=True)
    a = TuFragment(tu_name="a", types=(struct_definition,))
    b = TuFragment(tu_name="b", types=(forward_union,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_enum_reconciles_definition_then_forward_across_tus():
    # Roles swapped from the enum forward-decl/definition test above (a =
    # definition, b = forward/empty) -- both directions must merge.

    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    forward = EnumType(name="Color", members=[])
    a = TuFragment(tu_name="a", enums=(definition,))
    b = TuFragment(tu_name="b", enums=(forward,))
    merged = merge_fragments([a, b])
    assert merged.enums == (definition,)


def test_merge_fragments_enum_raises_on_conflicting_underlying_type_for_reversed_forward_pair():

    definition = EnumType(
        name="E", members=[EnumMember(name="X", value=0)], underlying_type="unsigned"
    )
    forward = EnumType(name="E", members=[], underlying_type="int")
    a = TuFragment(tu_name="a", enums=(definition,))
    b = TuFragment(tu_name="b", enums=(forward,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "E")


def test_merge_fragments_enum_reconciles_identical_definitions_across_tus():
    # Both sides fully defined (non-empty members), identical modulo
    # provenance -- exercises the "_more_public_of" tie-break path for
    # enums, distinct from the forward-decl/definition path above.

    a_def = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="a.h:1",
    )
    b_def = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="b.h:1",
    )
    a = TuFragment(tu_name="a", enums=(a_def,))
    b = TuFragment(tu_name="b", enums=(b_def,))
    merged = merge_fragments([a, b])
    assert len(merged.enums) == 1
    assert merged.enums[0].members == [EnumMember(name="RED", value=0)]


def test_merge_fragments_raises_on_conflicting_constant_value():
    a = TuFragment(tu_name="a", constants={"MAX": "100"})
    b = TuFragment(tu_name="b", constants={"MAX": "200"})
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("constant", "MAX")


# ---------------------------------------------------------------------------
# Round 7 (Codex review, PR #635): nested-namespace static linkage, the
# deprecated attribute lost across a forward-decl/definition merge, and
# both-opaque struct/class forward-redeclaration compatibility.
# ---------------------------------------------------------------------------


def test_merge_fragments_keeps_distinct_namespaced_static_functions_from_different_tus():
    # `namespace ns { static void helper(int); }` mangles to
    # `_ZN2nsL6helperEi` -- the internal-linkage `L` marker sits *after* the
    # namespace component, not as a `_ZL` prefix, so two unrelated TUs' own
    # namespaced statics must still be kept distinct (empirically verified
    # against real clang: `nm` shows the lowercase/local symbol type for
    # exactly this mangled spelling).
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn("helper", "_ZN2nsL6helperEi", is_static=True, return_type="int"),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("helper", "_ZN2nsL6helperEi", is_static=True, return_type="double"),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_keeps_distinct_namespaced_static_variables_from_different_tus():
    # `namespace ns { static int state; }` mangles to `_ZN2nsL5stateE` --
    # the variable analogue of the namespaced-static-function case above.
    a = TuFragment(tu_name="a", variables=(_var("state", "_ZN2nsL5stateE", value="1"),))
    b = TuFragment(tu_name="b", variables=(_var("state", "_ZN2nsL5stateE", value="2"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_type_unions_deprecated_from_opaque_forward_declaration():
    # A public `class [[deprecated("old")]] X;` forward declaration merged
    # with an undecorated private definition must not silently lose the
    # deprecation -- picking the definition's fields wholesale, as before
    # this fix, always did, since the opaque side's own facts (besides
    # provenance) were never consulted.
    forward = RecordType(name="X", kind="class", is_opaque=True, deprecated="old")
    definition = RecordType(
        name="X", kind="class", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0].deprecated == "old"
    assert merged.types[0].fields == [TypeField(name="v", type="int")]


def test_merge_fragments_type_prefers_definitions_deprecated_message_over_forwards():
    # Differing deprecated messages are not a conflict (Codex review, PR
    # #635 round 13, verified empirically against GCC and Clang under
    # -pedantic-errors) -- the definition (the structural winner here) is
    # the merge's provenance representative, so its own message wins.
    forward = RecordType(
        name="X", kind="class", is_opaque=True, deprecated="old reason"
    )
    definition = RecordType(
        name="X",
        kind="class",
        fields=[TypeField(name="v", type="int")],
        deprecated="different reason",
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0].deprecated == "different reason"
    assert merged.types[0].fields == [TypeField(name="v", type="int")]


def test_merge_fragments_enum_unions_deprecated_from_forward_declaration():
    forward = EnumType(name="Color", members=[], deprecated="old")
    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    merged = merge_fragments([a, b])
    assert len(merged.enums) == 1
    assert merged.enums[0].deprecated == "old"


def test_merge_fragments_type_reconciles_both_opaque_class_and_struct():
    # `class X;` in one TU, `struct X;` (also opaque, no definition anywhere
    # in this manifest) in another -- both are mere forward declarations of
    # the same class-key-compatible entity, so this must not raise even
    # though neither side has fields to prefer over the other's.
    forward_class = RecordType(name="X", kind="class", is_opaque=True)
    forward_struct = RecordType(name="X", kind="struct", is_opaque=True)
    a = TuFragment(tu_name="a", types=(forward_class,))
    b = TuFragment(tu_name="b", types=(forward_struct,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0].is_opaque


def test_merge_fragments_type_raises_on_both_opaque_conflicting_kind():
    # `union X;` and `struct X;`, both opaque -- union is never compatible
    # with struct/class, even when neither side is a full definition.
    forward_union = RecordType(name="X", kind="union", is_opaque=True)
    forward_struct = RecordType(name="X", kind="struct", is_opaque=True)
    a = TuFragment(tu_name="a", types=(forward_union,))
    b = TuFragment(tu_name="b", types=(forward_struct,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_type_raises_on_conflicting_alignment_between_opaque_and_definition():
    # castxml populates alignment_bits even for an opaque/incomplete record
    # when the forward declaration itself carries an explicit
    # __attribute__((aligned(16))) -- an ABI-relevant fact independent of
    # the member layout, so this forward declaration is not interchangeable
    # with a naturally 4-byte-aligned definition (Codex review, PR #635
    # round 15).
    forward = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=128)
    definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        alignment_bits=32,
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_type_raises_on_conflicting_alignment_reversed_definition_first():
    # The mirror of the case above: the definition is `a` (sorts first),
    # the opaque forward declaration is `b` -- exercises the merge
    # branch's other ordering.
    definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        alignment_bits=32,
    )
    forward = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=128)
    a = TuFragment(tu_name="a", types=(definition,))
    b = TuFragment(tu_name="b", types=(forward,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_type_raises_on_conflicting_alignment_between_two_opaque():
    forward_a = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=128)
    forward_b = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=32)
    a = TuFragment(tu_name="a", types=(forward_a,))
    b = TuFragment(tu_name="b", types=(forward_b,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_type_reconciles_matching_alignment_between_opaque_and_definition():
    forward = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=32)
    definition = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        alignment_bits=32,
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0].alignment_bits == 32


def test_merge_fragments_type_reconciles_unset_alignment_on_definition_side():
    # alignment_bits=None means "not captured", not "no alignment" -- a
    # None side contributes no information and must not be treated as a
    # conflict with the other side's captured value. The captured value
    # (128, from the forward declaration's explicit
    # __attribute__((aligned(16)))) must also actually survive onto the
    # merged result, not just avoid raising -- the merge's chosen
    # provenance representative here is the definition, whose own
    # alignment_bits is None, so this only passes if the union step
    # explicitly restores the other side's captured fact (Codex review,
    # PR #635 round 16).
    forward = RecordType(name="X", kind="struct", is_opaque=True, alignment_bits=128)
    definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward,))
    b = TuFragment(tu_name="b", types=(definition,))
    merged = merge_fragments([a, b])
    assert len(merged.types) == 1
    assert merged.types[0].alignment_bits == 128


# ---------------------------------------------------------------------------
# Round 8 (Codex review, PR #635): deprecated unioned across ordinary
# redeclarations (not just forward-decl/definition pairs), and a
# TU-order-independent `kind` for the both-opaque struct/class merge.
# ---------------------------------------------------------------------------


def test_merge_fragments_function_unions_deprecated_across_ordinary_redeclaration():
    # Two otherwise-identical function redeclarations, only one of which
    # carries [[deprecated]] -- a routine cross-TU redeclaration, not a
    # conflict, the same as differing provenance.
    a = _fn("old_api", "_Z7old_apiv", deprecated="use new_api instead")
    b = _fn("old_api", "_Z7old_apiv")
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].deprecated == "use new_api instead"


def test_merge_fragments_function_picks_deterministic_message_on_differing_deprecated():
    # Differing deprecated messages are not a conflict (Codex review, PR
    # #635 round 13) -- with no public-header context, `_more_public_of`
    # deterministically prefers `a` (the tu_name-sorted-first side), so
    # its message wins rather than raising.
    a = _fn("old_api", "_Z7old_apiv", deprecated="reason one")
    b = _fn("old_api", "_Z7old_apiv", deprecated="reason two")
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].deprecated == "reason one"


def test_merge_fragments_function_unions_additive_contract_attribute():
    # clang accepts `int f(int);` alongside a later
    # `[[nodiscard]] int f(int);` redeclaration -- routine, not a conflict.
    a = _fn("f", "_Z1fi", contract_attributes=["warn_unused_result"])
    b = _fn("f", "_Z1fi", contract_attributes=[])
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["warn_unused_result"]


def test_merge_fragments_function_treats_unset_contract_attributes_as_unknown():
    # None ("not captured") contributes no information -- the other side's
    # value, however incomplete, is kept as-is rather than forcing a match.
    a = _fn("f", "_Z1fi", contract_attributes=None)
    b = _fn("f", "_Z1fi", contract_attributes=["nonnull(1)"])
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["nonnull(1)"]


def test_merge_fragments_function_treats_other_side_unset_contract_attributes_as_unknown():
    # The mirror of the a=None case above -- b=None must be equally "no
    # information", not "definitely no attributes".
    a = _fn("f", "_Z1fi", contract_attributes=["nonnull(1)"])
    b = _fn("f", "_Z1fi", contract_attributes=None)
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["nonnull(1)"]


def test_merge_fragments_function_unions_contract_attributes_with_shared_token():
    # A token both sides already agree on ("nonnull(1)") is deduplicated,
    # not doubled; a genuinely new, unrelated token ("warn_unused_result")
    # from the other side is still additive.
    a = _fn("f", "_Z1fi", contract_attributes=["nonnull(1)", "noreturn"])
    b = _fn("f", "_Z1fi", contract_attributes=["nonnull(1)", "warn_unused_result"])
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == [
        "nonnull(1)",
        "noreturn",
        "warn_unused_result",
    ]


def test_merge_fragments_function_raises_on_conflicting_contract_attribute_args():
    # Same attribute family ("format"), different arguments -- a genuine
    # conflict, not an additive difference.
    a = _fn("f", "_Z1fPKcz", contract_attributes=["format(printf,1,2)"])
    b = _fn("f", "_Z1fPKcz", contract_attributes=["format(scanf,1,2)"])
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments(
            [
                TuFragment(tu_name="a", functions=(a,)),
                TuFragment(tu_name="b", functions=(b,)),
            ]
        )
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_function_unions_set_valued_nonnull_arguments():
    # nonnull(1) and nonnull(2) from separate TUs both apply -- GCC/clang
    # accumulate constraints across separate nonnull attributes rather than
    # treating a second one as contradicting the first.
    a = _fn("f", "_Z1fPvS_", contract_attributes=["nonnull(1)"])
    b = _fn("f", "_Z1fPvS_", contract_attributes=["nonnull(2)"])
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["nonnull(1)", "nonnull(2)"]


def test_merge_fragments_function_raises_on_conflicting_calling_convention():
    # ms_abi and sysv_abi are different bare families (no shared "(" prefix
    # to collide on) but are mutually exclusive as a calling-convention
    # group -- unioning both onto one function would be nonsensical, and
    # diff_symbols.py treats exactly this pair as CALLING_CONVENTION_CHANGED
    # when comparing two already-merged snapshots.
    a = _fn("f", "_Z1fi", contract_attributes=["ms_abi"])
    b = _fn("f", "_Z1fi", contract_attributes=["sysv_abi"])
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments(
            [
                TuFragment(tu_name="a", functions=(a,)),
                TuFragment(tu_name="b", functions=(b,)),
            ]
        )
    assert excinfo.value.code == INCONSISTENT_DECLARATION


def test_merge_fragments_function_allows_matching_calling_convention():
    a = _fn("f", "_Z1fi", contract_attributes=["ms_abi"])
    b = _fn("f", "_Z1fi", contract_attributes=["ms_abi"])
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", functions=(a,)),
            TuFragment(tu_name="b", functions=(b,)),
        ]
    )
    assert len(merged.functions) == 1
    assert merged.functions[0].contract_attributes == ["ms_abi"]


def test_merge_fragments_variable_unions_deprecated_across_ordinary_redeclaration():
    a = _var("g_old", "g_old", deprecated="use g_new instead")
    b = _var("g_old", "g_old")
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", variables=(a,)),
            TuFragment(tu_name="b", variables=(b,)),
        ]
    )
    assert len(merged.variables) == 1
    assert merged.variables[0].deprecated == "use g_new instead"


def test_merge_fragments_variable_picks_deterministic_message_on_differing_deprecated():
    # Differing deprecated messages are not a conflict (Codex review, PR
    # #635 round 13); `a` deterministically wins with no public-header
    # context.
    a = _var("g_old", "g_old", deprecated="reason one")
    b = _var("g_old", "g_old", deprecated="reason two")
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", variables=(a,)),
            TuFragment(tu_name="b", variables=(b,)),
        ]
    )
    assert len(merged.variables) == 1
    assert merged.variables[0].deprecated == "reason one"


def test_merge_fragments_type_unions_deprecated_across_two_full_definitions():
    # Both sides fully defined (non-opaque), identical modulo provenance and
    # deprecated -- exercises _merge_identical_modulo_provenance's
    # deprecated union, distinct from the opaque/definition path.
    a_def = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        deprecated="old",
    )
    b_def = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", types=(a_def,)),
            TuFragment(tu_name="b", types=(b_def,)),
        ]
    )
    assert len(merged.types) == 1
    assert merged.types[0].deprecated == "old"


def test_merge_fragments_type_picks_deterministic_message_on_differing_deprecated_for_two_full_definitions():
    # Differing deprecated messages are not a conflict (Codex review, PR
    # #635 round 13); `a` deterministically wins with no public-header
    # context.
    a_def = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        deprecated="reason one",
    )
    b_def = RecordType(
        name="X",
        kind="struct",
        fields=[TypeField(name="v", type="int")],
        deprecated="reason two",
    )
    merged = merge_fragments(
        [
            TuFragment(tu_name="a", types=(a_def,)),
            TuFragment(tu_name="b", types=(b_def,)),
        ]
    )
    assert len(merged.types) == 1
    assert merged.types[0].deprecated == "reason one"


def test_merge_fragments_type_opaque_kind_is_independent_of_tu_name_order():
    # `class X;` and `struct X;`, both opaque -- the surviving `kind` must
    # be a function of the two kind strings alone (lexicographically
    # smallest, "class"), never of which TU's name happens to sort first.
    # The discriminating case is TU "a" (sorts first) holding the `struct`
    # declaration: the old, pre-round-8 behavior picked whichever side was
    # `a` unconditionally, so it would have kept "struct" here -- the fix
    # must still produce "class" regardless.
    forward_class = RecordType(name="X", kind="class", is_opaque=True)
    forward_struct = RecordType(name="X", kind="struct", is_opaque=True)

    struct_sorts_first = merge_fragments(
        [
            TuFragment(tu_name="a", types=(forward_struct,)),
            TuFragment(tu_name="b", types=(forward_class,)),
        ]
    )
    class_sorts_first = merge_fragments(
        [
            TuFragment(tu_name="a", types=(forward_class,)),
            TuFragment(tu_name="b", types=(forward_struct,)),
        ]
    )
    assert struct_sorts_first.types[0].kind == "class"
    assert class_sorts_first.types[0].kind == "class"


# ---------------------------------------------------------------------------
# Real end-to-end: G32 Phase 0's own committed odr_safe/odr_conflict fixtures
# ---------------------------------------------------------------------------


def test_odr_safe_fixture_merges_cleanly_through_real_clang_backend():
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_safe" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_safe" / "tu_b.h",)
    )
    merged = run_tu_loop(
        (tu_a, tu_b),
        header_ast_parser=_header_ast_parser,
        roots=[_G32_DIR / "odr_safe" / "tu_a.h", _G32_DIR / "odr_safe" / "tu_b.h"],
        backend="clang",
        compiler="c++",
        lang="c++",
        exported_dynamic={"touches_point"},
        exported_static=set(),
    )
    assert [t.name for t in merged.types] == ["Point"]
    # tu_b.h's full definition is what won the merge, not tu_a.h's forward
    # declaration -- the type must carry real fields, not an opaque marker.
    assert merged.types[0].is_opaque is False
    assert {f.name for f in merged.types[0].fields} == {"x", "y"}
    assert [fn.name for fn in merged.functions] == ["touches_point"]


def test_odr_conflict_fixture_raises_through_real_clang_backend():
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_conflict" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_conflict" / "tu_b.h",)
    )
    # No explicit lang="c++" here (unlike the odr_safe test above, which
    # genuinely needs it for the bare `Point` forward-declaration syntax) --
    # these headers are valid under either C or C++ linkage, and forcing
    # lang="c++" was observed to route Windows' clang toward MSVC-mode name
    # mangling, where int/double-returning `compute(int)` overloads did not
    # collide under the same entity_key the way they reliably do under
    # Itanium mangling (CI: windows-latest, PR #635) -- matching
    # test_dumper_manifest.py's own real-backend conflict test, which
    # likewise leaves lang unset and passes on every platform.
    with pytest.raises(TuMergeError) as excinfo:
        run_tu_loop(
            (tu_a, tu_b),
            header_ast_parser=_header_ast_parser,
            roots=[
                _G32_DIR / "odr_conflict" / "tu_a.h",
                _G32_DIR / "odr_conflict" / "tu_b.h",
            ],
            backend="clang",
            compiler="c++",
            exported_dynamic={"compute"},
            exported_static=set(),
        )
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key[0] == "function"
