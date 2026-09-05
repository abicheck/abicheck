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

"""Tests for the backend-independent ``canonical_finding_id`` report field
(schema 2.36/1.15) and its matching ``finding_id`` suppression selector —
P1-9 of the abicheck-bazel-lab architecture review: a suppression rule
written against one header backend's report (CastXML) should reliably
match the equivalent finding in another's (Clang), which the pre-existing
``finding_id`` field cannot guarantee (it folds in ``source_location``/
``description``, fields the two backends aren't guaranteed to spell
identically).
"""

from __future__ import annotations

import json

import pytest
import yaml

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.diff_helpers import make_change
from abicheck.finding_identity import (
    report_canonical_finding_id,
    report_finding_id,
    resolve_change_identity,
)
from abicheck.reporter import to_json
from abicheck.suppression import Suppression, SuppressionList


def _func_removed(
    symbol: str, *, description: str, source_location: str | None
) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=description,
        old_value=symbol,
        new_value=None,
        source_location=source_location,
    )


class TestReportCanonicalFindingId:
    def test_matches_across_differing_description_and_source_location(self):
        # Same underlying finding (same mangled symbol, same kind, same
        # old/new value) as two header backends might report it -- one with
        # a source_location, the other without; slightly different
        # description wording, e.g. from a differently-spelled parameter
        # type embedded in the text.
        a = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        b = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const *)",
            source_location=None,
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)
        # The ordinary finding_id, by contrast, is NOT stable across this --
        # that's the whole reason canonical_finding_id exists.
        assert report_finding_id(a) != report_finding_id(b)

    def test_never_returns_a_raw_x1f_delimited_primary_id(self):
        # Regression: an earlier revision of this function returned
        # resolve_change_identity(c).primary_id verbatim, which embeds a
        # literal \x1f (ASCII unit separator) field delimiter -- legal in a
        # JSON string (json.dumps escapes control characters) but rejected
        # outright by PyYAML's safe_load in a suppression YAML file's
        # finding_id: value. Caught by test_loads_from_yaml below actually
        # exercising the YAML round trip; pinned here directly too.
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        canonical = report_canonical_finding_id(change)
        assert "\x1f" not in canonical
        # And it must actually survive a real YAML round trip, not merely
        # avoid the one known-bad byte.
        yaml.safe_load(f"finding_id: {canonical}\n")

    def test_differs_for_a_genuinely_different_symbol(self):
        a = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        b = _func_removed(
            "_Z3bari", description="Function removed: bar(int)", source_location=None
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(b)

    def test_deterministic_across_repeated_calls(self):
        a = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(a)

    def test_degrades_gracefully_for_a_duck_typed_stub_lacking_qualified_name(self):
        # cli_scan_baseline._baseline_finding_dicts documents accepting
        # lightweight fakes/stubs, not just real Change instances -- a stub
        # missing `.qualified_name` (which resolve_change_identity accesses
        # via plain attribute access, unlike report_finding_id's getattr
        # fallbacks) must degrade to report_finding_id rather than raise.
        class _Stub:
            kind = ChangeKind.FUNC_REMOVED
            symbol = "_Z3fooi"
            description = "Function removed: foo(int)"
            old_value = "_Z3fooi"
            new_value = None
            source_location = None

        stub = _Stub()
        assert report_canonical_finding_id(stub) == report_finding_id(stub)

    def test_matches_across_differing_type_spelling_on_a_non_equivalent_category_kind(
        self,
    ):
        # Regression (Codex review, PR #753): FUNC_RETURN_CHANGED is not in
        # _EQUIVALENT_CHANGE_CATEGORIES, so old_value/new_value flow straight
        # into the discriminator -- and therefore into every identity tier,
        # including CANONICAL. Without canonicalizing them, CastXML's
        # "char const*" and Clang's "char const *" for the identical
        # return-type change hashed to two different canonical ids.
        def return_changed(old: str, new: str) -> Change:
            return Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol="_Z3fooi",
                description=f"Return type changed: {old} -> {new}",
                old_value=old,
                new_value=new,
                source_location=None,
            )

        castxml = return_changed("char const*", "int const*")
        clang = return_changed("char const *", "int const *")
        assert report_canonical_finding_id(castxml) == report_canonical_finding_id(
            clang
        )
        # A genuinely different return-type change must still differ.
        different = return_changed("char const*", "long const*")
        assert report_canonical_finding_id(castxml) != report_canonical_finding_id(
            different
        )


class TestCanonicalFindingIdScopedCanonicalization:
    """Regressions from two follow-up Codex review rounds on PR #753's
    initial fix: canonicalizing every kind's old/new corrupted non-type
    values, and unconditionally dropping description (the first attempted
    fix for that) collided distinct findings that differ only by a
    per-item identity (e.g. a field/parameter name) description embeds via
    ``change_registry``'s ``{detail}`` template placeholder.
    """

    def test_distinct_parameters_on_the_same_function_stay_distinct(self):
        # PARAM_POINTER_LEVEL_CHANGED is not a type-bearing kind (old/new
        # are pointer-depth integers); its per-parameter identity lives
        # only in description via {detail}. Two different parameters with
        # the identical depth transition must not collide.
        a = make_change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
            symbol="_Z3fooPiPi",
            name="foo",
            detail="a",
            old="1",
            new="2",
        )
        b = make_change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
            symbol="_Z3fooPiPi",
            name="foo",
            detail="b",
            old="1",
            new="2",
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(b)

    def test_whitespace_sensitive_macro_values_are_not_corrupted(self):
        # PUBLIC_MACRO_VALUE_CHANGED's old/new are raw macro replacement
        # text, not a type spelling -- canonicalize_type_name's whitespace
        # collapse must not be applied to it, or two semantically distinct
        # macro-value transitions would hash identically.
        a = make_change(
            ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
            symbol="FOO",
            name="FOO",
            old="a  b",
            new="c  d",
            description="Macro FOO changed",
        )
        b = make_change(
            ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
            symbol="FOO",
            name="FOO",
            old="a b",
            new="c d",
            description="Macro FOO changed",
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(b)

    def test_struct_field_type_changed_is_backend_stable_but_field_identity_survives(
        self,
    ):
        # struct_field_type_changed's own description_template embeds BOTH
        # a per-field identity ({detail}) AND the raw old/new type spelling
        # -- the hardest case: canonicalizing the whole description must
        # normalize the backend-sensitive type text while leaving the
        # field-name discriminator intact.
        def field_changed(field: str, old: str, new: str) -> Change:
            return make_change(
                ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
                symbol="Widget",
                name="Widget",
                detail=field,
                old=old,
                new=new,
            )

        castxml = field_changed("ptr", "char const*", "int const*")
        clang = field_changed("ptr", "char const *", "int const *")
        assert report_canonical_finding_id(castxml) == report_canonical_finding_id(
            clang
        )
        # A different field with the identical type transition must differ.
        other_field = field_changed("other", "char const*", "int const*")
        assert report_canonical_finding_id(castxml) != report_canonical_finding_id(
            other_field
        )

    def test_func_params_changed_matches_across_differing_pointer_spacing(self):
        # Regression (Codex review, PR #753, fourth round): func_params_changed
        # was missing from the allowlist despite old/new being
        # _format_params()'s comma-joined type spellings.
        a = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3fooPKc",
            name="foo",
            old="char const*",
            new="int const*",
        )
        b = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3fooPKc",
            name="foo",
            old="char const *",
            new="int const *",
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)

    def test_func_params_changed_canonicalizes_every_parameter_not_just_the_first(
        self,
    ):
        # Regression (Codex review, PR #753, round 6): canonicalize_type_name's
        # const-reorder/struct-prefix passes are anchored to the START of the
        # string -- calling it on the whole comma-joined _format_params()
        # list only ever fixes the FIRST parameter. A real change on
        # parameter 2 plus an unrelated const-spelling difference on
        # parameter 1 (unchanged) must still collapse to one canonical id
        # across producers.
        a = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3fooPKci",
            name="foo",
            old="char const*, int",
            new="char const*, long",
        )
        b = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3fooPKci",
            name="foo",
            old="const char *, int",
            new="const char *, long",
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)
        # A genuinely different second-parameter change must still differ.
        c = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3fooPKci",
            name="foo",
            old="const char *, int",
            new="const char *, double",
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(c)

    def test_func_params_changed_respects_template_argument_commas(self):
        # A parameter's own type can contain a comma (template arguments) --
        # splitting on every comma would misalign fields across the list,
        # e.g. treating "std::pair<int" and " int>*" as two separate
        # parameters instead of one. Uses only the sigil-spacing
        # normalization (canonicalize_type_name declines to reorder const
        # for a templated base at all, by design -- see its own docstring),
        # which is global/unanchored and so applies at any split position
        # as long as the split itself stayed aligned with the real
        # parameter boundaries.
        a = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3foo",
            name="foo",
            old="std::pair<int, int>*, int",
            new="std::pair<int, int>*, long",
        )
        b = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3foo",
            name="foo",
            old="std::pair<int, int> *, int",
            new="std::pair<int, int> *, long",
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)
        # A genuinely different second-parameter change must still differ,
        # confirming the split didn't collapse the two parameters together.
        c = make_change(
            ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="_Z3foo",
            name="foo",
            old="std::pair<int, int> *, int",
            new="std::pair<int, int> *, double",
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(c)

    def test_struct_field_type_changed_canonicalizes_embedded_description_spelling(
        self,
    ):
        # Regression (Codex review, PR #753, round 7): struct_field_type_changed's
        # description_template embeds "{old} -> {new}" mid-sentence, not at
        # the start -- canonicalize_type_name's anchored struct-prefix/
        # const-reorder passes never reach it, so the OLD fix (canonicalizing
        # the whole description sentence) left the embedded spelling raw
        # even though old_value/new_value themselves were already fixed.
        a = make_change(
            ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
            symbol="Foo",
            name="Foo",
            detail="x",
            old="struct Bar*",
            new="struct Bar*",
        )
        b = make_change(
            ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
            symbol="Foo",
            name="Foo",
            detail="x",
            old="Bar *",
            new="Bar *",
        )
        assert report_canonical_finding_id(a) == report_canonical_finding_id(b)
        # A genuinely different field type must still differ.
        c = make_change(
            ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
            symbol="Foo",
            name="Foo",
            detail="x",
            old="Bar *",
            new="Baz *",
        )
        assert report_canonical_finding_id(a) != report_canonical_finding_id(c)

    @pytest.mark.parametrize(
        ("kind", "detail", "old_castxml", "new_castxml", "old_clang", "new_clang"),
        [
            # diff_atomic.py: `detail` is a fixed direction string
            # ("qualifier added"/"qualifier removed"), never a per-item
            # identity -- old/new are the raw iter_type_slot_changes()
            # type-slot spellings. CastXML cannot model _Atomic at all and
            # spells the qualified side as the bare, lossy sentinel
            # "_Atomic" (dumper_castxml.py's own _type_name()); Clang keeps
            # the real wrapped spelling -- canonicalize_atomic_slot is what
            # collapses these to the same canonical id.
            (
                ChangeKind.ATOMIC_QUALIFIER_CHANGED,
                "qualifier added",
                "struct Foo*",
                "_Atomic",
                "struct Foo *",
                "_Atomic(struct Foo *)",
            ),
            # diff_char8t.py: `detail` is "char-family → char8_t" or the
            # reverse, again fixed rather than per-item.
            (
                ChangeKind.CHAR8T_MIGRATION,
                "char-family → char8_t",
                "char const*",
                "char8_t const*",
                "char const *",
                "char8_t const *",
            ),
            # diff_bit_int.py: `detail` embeds the width transition itself
            # (e.g. "_BitInt width changed 17 → 33"), not a field/parameter
            # name, so it stays identical across backends for the identical
            # transition.
            (
                ChangeKind.BIT_INT_WIDTH_CHANGED,
                "_BitInt width changed 17 → 33",
                "_BitInt(17)*",
                "_BitInt(33) *",
                "_BitInt(17) *",
                "_BitInt(33)*",
            ),
        ],
    )
    def test_type_slot_kinds_are_backend_stable(
        self,
        kind: ChangeKind,
        detail: str,
        old_castxml: str,
        new_castxml: str,
        old_clang: str,
        new_clang: str,
    ):
        # Regression (canonical-id-backend-gap): ATOMIC_QUALIFIER_CHANGED,
        # CHAR8T_MIGRATION, and BIT_INT_WIDTH_CHANGED were all missing from
        # _TYPE_BEARING_DISCRIMINATOR_KINDS/_DESCRIPTION_EMBEDS_VALUES_KINDS
        # despite passing iter_type_slot_changes()'s raw type-slot spellings
        # straight through as old_value/new_value, and description_template
        # embedding "{old} → {new}" mid-sentence after a leading {detail}
        # clause (same shape as struct_field_type_changed) -- so a CastXML
        # report's canonical_finding_id never matched Clang's equivalent
        # finding for these three kinds.
        def slot_change(field: str, old: str, new: str) -> Change:
            return make_change(
                kind,
                symbol="_Z3fooPKc",
                name="parameter 0 of '_Z3fooPKc'",
                detail=field,
                old=old,
                new=new,
            )

        castxml = slot_change(detail, old_castxml, new_castxml)
        clang = slot_change(detail, old_clang, new_clang)
        assert report_canonical_finding_id(castxml) == report_canonical_finding_id(
            clang
        )

        # A suppression rule minted from one backend's report matches the
        # equivalent finding reported by the other.
        rule = Suppression(
            finding_id=report_canonical_finding_id(castxml), reason="accepted"
        )
        assert rule.matches(clang)

    def test_type_slot_kinds_still_distinguish_semantically_different_transitions(
        self,
    ):
        # A genuinely different transition on the same symbol/kind must not
        # collapse to the same canonical id -- verified across all three
        # kinds' own distinguishing axis (direction for atomic/char8_t,
        # width for _BitInt).
        atomic_added = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="qualifier added",
            old="struct Foo*",
            new="_Atomic(struct Foo*)",
        )
        atomic_removed = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="qualifier removed",
            old="_Atomic(struct Foo*)",
            new="struct Foo*",
        )
        assert report_canonical_finding_id(atomic_added) != report_canonical_finding_id(
            atomic_removed
        )

        char8_to_char = make_change(
            ChangeKind.CHAR8T_MIGRATION,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="char8_t → char-family",
            old="char8_t const*",
            new="char const*",
        )
        char_to_char8 = make_change(
            ChangeKind.CHAR8T_MIGRATION,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="char-family → char8_t",
            old="char const*",
            new="char8_t const*",
        )
        assert report_canonical_finding_id(
            char8_to_char
        ) != report_canonical_finding_id(char_to_char8)

        width_17_to_33 = make_change(
            ChangeKind.BIT_INT_WIDTH_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="_BitInt width changed 17 → 33",
            old="_BitInt(17)*",
            new="_BitInt(33)*",
        )
        width_17_to_65 = make_change(
            ChangeKind.BIT_INT_WIDTH_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="_BitInt width changed 17 → 65",
            old="_BitInt(17)*",
            new="_BitInt(65)*",
        )
        assert report_canonical_finding_id(
            width_17_to_33
        ) != report_canonical_finding_id(width_17_to_65)

    def test_atomic_qualifier_changed_castxml_lossy_sentinel_matches_clang(self):
        # Regression (Codex review, canonical-id-backend-gap, P1 finding):
        # dumper_castxml.py cannot model C11 _Atomic at all and spells the
        # qualified side as the bare, lossy sentinel "_Atomic" -- Clang keeps
        # the real wrapped spelling ("_Atomic(struct Foo *)"). Plain
        # canonicalize_type_name has no notion that these name the same
        # qualified type, so the fix's first revision (canonicalize_type_name
        # alone) still failed to match here even after atomic_qualifier_changed
        # was added to the allowlist -- canonicalize_atomic_slot is what
        # closes it.
        castxml = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="qualifier added",
            old="struct Foo*",
            new="_Atomic",
        )
        clang = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="qualifier added",
            old="struct Foo *",
            new="_Atomic(struct Foo *)",
        )
        assert report_canonical_finding_id(castxml) == report_canonical_finding_id(
            clang
        )

    def test_atomic_qualifier_changed_preserves_outer_declarator(self):
        # Regression (Codex review, canonical-id-backend-gap, second P1
        # finding): an earlier revision of canonicalize_atomic_slot
        # collapsed the ENTIRE qualified spelling to the bare "_Atomic"
        # sentinel regardless of what followed it -- but "atomic pointer to
        # T" (_Atomic(T *) on Clang, "_Atomic" on CastXML -- the Atomic node
        # wraps the whole pointer, so CastXML's PointerType-appends-"*"
        # logic never runs) and "pointer to atomic T" (_Atomic(T) * on
        # Clang, "_Atomic*" on CastXML -- PointerType wraps the Atomic node
        # and appends its own suffix) are two different qualified types.
        # Collapsing both to the same sentinel would let a finding_id
        # suppression accepted for one silently suppress the other.
        atomic_pointer = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPi",
            name="parameter 0 of '_Z3fooPi'",
            detail="qualifier added",
            old="int *",
            new="_Atomic(int *)",
        )
        pointer_to_atomic = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPi",
            name="parameter 0 of '_Z3fooPi'",
            detail="qualifier added",
            old="int *",
            new="_Atomic(int) *",
        )
        assert report_canonical_finding_id(
            atomic_pointer
        ) != report_canonical_finding_id(pointer_to_atomic)

        # And each still matches its own CastXML-spelled equivalent.
        atomic_pointer_castxml = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPi",
            name="parameter 0 of '_Z3fooPi'",
            detail="qualifier added",
            old="int*",
            new="_Atomic",
        )
        pointer_to_atomic_castxml = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPi",
            name="parameter 0 of '_Z3fooPi'",
            detail="qualifier added",
            old="int*",
            new="_Atomic*",
        )
        assert report_canonical_finding_id(
            atomic_pointer
        ) == report_canonical_finding_id(atomic_pointer_castxml)
        assert report_canonical_finding_id(
            pointer_to_atomic
        ) == report_canonical_finding_id(pointer_to_atomic_castxml)
        assert report_canonical_finding_id(
            atomic_pointer_castxml
        ) != report_canonical_finding_id(pointer_to_atomic_castxml)

    def test_atomic_qualifier_changed_preserves_compound_payload_change(self):
        # Regression (Codex review, canonical-id-backend-gap, third P1
        # finding): a slot that both gains the qualifier AND changes its
        # base type in the same transition (e.g. int -> _Atomic(long)) must
        # not collapse to the same canonical id as a pure qualifier-only
        # change on the identical unqualified type (int -> _Atomic(int)) --
        # these are two structurally different ABI transitions, and a
        # finding_id suppression accepted for one must not silently
        # suppress the other.
        qualifier_only = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="int",
            new="_Atomic(int)",
        )
        compound_change = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="int",
            new="_Atomic(long)",
        )
        assert report_canonical_finding_id(
            qualifier_only
        ) != report_canonical_finding_id(compound_change)

        # The compound change still matches an equivalent spelling of
        # itself (same payload, different pointer spacing).
        compound_change_respaced = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="int",
            new="_Atomic( long )",
        )
        assert report_canonical_finding_id(
            compound_change
        ) == report_canonical_finding_id(compound_change_respaced)

    def test_atomic_qualifier_changed_normalizes_cv_qualified_spellings(self):
        # Regression (Codex review, canonical-id-backend-gap, fourth P1
        # finding): a const/volatile-qualified atomic slot spells
        # differently across backends too -- CastXML's CvQualifiedType
        # wraps the lossy Atomic node as "const _Atomic" (prefix form, since
        # the wrapped node isn't a pointer value), while Clang keeps
        # "const _Atomic(int)". The leading "const "/"volatile " prefix
        # broke the anchored _Atomic-detection regex, so both fell back to
        # plain canonicalize_type_name and stayed distinct.
        castxml = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="const int",
            new="const _Atomic",
        )
        clang = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="const int",
            new="const _Atomic(int)",
        )
        assert report_canonical_finding_id(castxml) == report_canonical_finding_id(
            clang
        )
        # A cv-qualified compound change (payload also differs) must still
        # stay distinct from the plain cv-qualified qualifier-only change.
        clang_compound = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooi",
            name="parameter 0 of '_Z3fooi'",
            detail="qualifier added",
            old="const int",
            new="const _Atomic(long)",
        )
        assert report_canonical_finding_id(clang) != report_canonical_finding_id(
            clang_compound
        )

    def test_atomic_qualifier_changed_description_substitution_handles_containment(
        self,
    ):
        # Regression (Codex review, fresh evidence): _change_discriminator's
        # description substitution replaced raw_old_str, then raw_new_str,
        # unconditionally in that order. For a "qualifier added" transition
        # the unqualified raw_old spelling ("struct Foo *") is a literal
        # substring of the qualified raw_new spelling
        # ("_Atomic(struct Foo *)") -- replacing raw_old first also rewrote
        # the copy embedded inside raw_new's own span, so raw_new's exact
        # text no longer appeared in the description and its own replace
        # silently no-opped, leaving the qualified side's stale,
        # backend-specific spelling in the discriminator. Verified directly
        # against the rendered description, not just the opaque hash.
        clang = make_change(
            ChangeKind.ATOMIC_QUALIFIER_CHANGED,
            symbol="_Z3fooPKc",
            name="parameter 0 of '_Z3fooPKc'",
            detail="qualifier added",
            old="struct Foo *",
            new="_Atomic(struct Foo *)",
        )
        canonical_desc_fragment = "Foo * → _Atomic"
        # The rendered description must fully collapse the qualified side to
        # the bare sentinel -- not leave a stale "_Atomic(Foo *)" behind.
        assert (
            canonical_desc_fragment
            in resolve_change_identity(clang, canonicalize_values=True).primary_id
        )
        assert (
            "_Atomic(Foo *)"
            not in resolve_change_identity(clang, canonicalize_values=True).primary_id
        )

    def test_equivalent_category_kinds_still_distinguish_old_new(self):
        # Regression (Codex review, PR #753, fourth round): a bare
        # "category:type_size_change" discriminator (used to collapse
        # rich-vs-L0 sibling kinds for reconciliation) dropped old/new
        # entirely, so a `finding_id:` suppression rule accepted for one
        # TYPE_SIZE_CHANGED transition also silently suppressed a later,
        # structurally different one on the same symbol.
        first = make_change(
            ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Widget",
            name="Widget",
            old="8",
            new="16",
        )
        second = make_change(
            ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Widget",
            name="Widget",
            old="16",
            new="32",
        )
        assert report_canonical_finding_id(first) != report_canonical_finding_id(second)

    def test_equivalent_category_kinds_still_collapse_rich_and_l0_siblings(self):
        # The reconciliation use of _EQUIVALENT_CHANGE_CATEGORIES this fix
        # must not break: the same removal reported as FUNC_REMOVED (rich)
        # vs. FUNC_REMOVED_ELF_ONLY (L0) with the same old/new still
        # collapses to one canonical id.
        rich = make_change(
            ChangeKind.FUNC_REMOVED,
            symbol="_Z3fooi",
            name="foo",
            old="_Z3fooi",
            new=None,
            description="Function foo removed",
        )
        l0 = make_change(
            ChangeKind.FUNC_REMOVED_ELF_ONLY,
            symbol="_Z3fooi",
            name="foo",
            old="_Z3fooi",
            new=None,
            description="Function foo removed (ELF)",
        )
        assert report_canonical_finding_id(rich) == report_canonical_finding_id(l0)

    def test_equivalent_category_size_kinds_normalize_units_before_comparing(self):
        # Regression (Codex review, PR #753, fifth round): diff_types.py's
        # TYPE_SIZE_CHANGED/TYPE_ALIGNMENT_CHANGED stamp RecordType's *bit*
        # counts, while their collapsed sibling STRUCT_SIZE_CHANGED/
        # STRUCT_ALIGNMENT_CHANGED (diff_platform.py) stamp StructLayout's
        # *byte* counts -- the identical 8-byte layout change must produce
        # the same canonical id regardless of which detector supplied it.
        type_kind = make_change(
            ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Widget",
            name="Widget",
            old="64",
            new="128",
        )
        struct_kind = make_change(
            ChangeKind.STRUCT_SIZE_CHANGED,
            symbol="Widget",
            name="Widget",
            old="8",
            new="16",
        )
        assert report_canonical_finding_id(type_kind) == report_canonical_finding_id(
            struct_kind
        )
        # A genuinely different size transition on the same symbol must
        # still differ, unit conversion included.
        different = make_change(
            ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Widget",
            name="Widget",
            old="128",
            new="256",
        )
        assert report_canonical_finding_id(type_kind) != report_canonical_finding_id(
            different
        )

    def test_func_removal_category_collapses_across_producers_with_inconsistent_values(
        self,
    ):
        # Regression (Codex review, PR #753, round 5): diff_symbols.py's
        # rich ELF detector stamps old_value=f_old.name for FUNC_REMOVED,
        # while diff_platform.py's PE/Mach-O export-table detectors leave
        # old_value/new_value unset for the *identical* removed-symbol
        # event -- folding those inconsistent values into the discriminator
        # defeated the whole point of the category fold for this pair. The
        # discriminator must drop old/new for "func_removal"/"func_addition"
        # entirely and collapse on category alone.
        rich = make_change(
            ChangeKind.FUNC_REMOVED,
            symbol="_Z3fooi",
            name="foo",
            old="foo",
            new=None,
            description="Function foo removed",
        )
        raw_export = make_change(
            ChangeKind.FUNC_REMOVED,
            symbol="_Z3fooi",
            name="foo",
            old=None,
            new=None,
            description="new export in DLL: foo",
        )
        assert report_canonical_finding_id(rich) == report_canonical_finding_id(
            raw_export
        )
        # A genuinely different symbol's removal must still differ.
        other = make_change(
            ChangeKind.FUNC_REMOVED,
            symbol="_Z3bari",
            name="bar",
            old=None,
            new=None,
            description="new export in DLL: bar",
        )
        assert report_canonical_finding_id(rich) != report_canonical_finding_id(other)


class TestReporterEmitsCanonicalFindingId:
    def test_change_to_dict_includes_canonical_finding_id(self):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="lib", changes=[change]
        )
        report = json.loads(to_json(result))
        (entry,) = report["changes"]
        assert entry["canonical_finding_id"]
        assert entry["canonical_finding_id"] == report_canonical_finding_id(change)
        # Joinable with, but distinct from, the pre-existing finding_id.
        assert entry["finding_id"] == report_finding_id(change)

    def test_out_of_surface_changes_include_canonical_finding_id_without_contract_evaluation(
        self,
    ):
        # Regression (Codex review, PR #753): _add_contract_evaluation_fields
        # used to return before stamping finding_id/canonical_finding_id
        # whenever contract_relevance was unset -- which is every ordinary
        # run without --contract -- so out_of_surface_changes
        # entries (built by the same helper) silently lacked both ids.
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        change.surface_exclusion_reason = "non-public-type"
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="lib",
            changes=[],
            scope_to_public_surface=True,
            out_of_surface_changes=[change],
        )
        report = json.loads(to_json(result))
        (entry,) = report["surface_scope"]["out_of_surface_changes"]
        assert entry["finding_id"] == report_finding_id(change)
        assert entry["canonical_finding_id"] == report_canonical_finding_id(change)
        # No contract_evaluation=True on this run, so the contract-specific
        # fields must still be absent -- only the id stamping is unconditional.
        assert "contract_relevance" not in entry


class TestFindingIdSuppressionSelector:
    def test_matches_by_canonical_id_alone(self):
        change = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        canonical = report_canonical_finding_id(change)
        rule = Suppression(finding_id=canonical, reason="accepted")
        assert rule.matches(change)

    def test_matches_the_same_finding_reported_by_a_different_backend(self):
        # The scenario P1-9 exists for: a suppression rule minted from a
        # CastXML report's canonical_finding_id must still match the same
        # change as reported by Clang (differing description/source_location).
        castxml_side = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const*)",
            source_location="lib.h:12",
        )
        clang_side = _func_removed(
            "_Z3fooPKc",
            description="Function removed: foo(char const *)",
            source_location=None,
        )
        rule = Suppression(
            finding_id=report_canonical_finding_id(castxml_side), reason="accepted"
        )
        assert rule.matches(clang_side)

    def test_does_not_match_a_different_finding(self):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        other = _func_removed(
            "_Z3bari", description="Function removed: bar(int)", source_location=None
        )
        rule = Suppression(
            finding_id=report_canonical_finding_id(other), reason="accepted"
        )
        assert not rule.matches(change)

    def test_standalone_selector_satisfies_at_least_one_requirement(self):
        # Must not raise -- finding_id alone is a valid, sufficient selector.
        Suppression(finding_id="0123456789abcdef", reason="ok")

    def test_no_selector_at_all_still_rejected(self):
        with pytest.raises(ValueError, match="finding_id"):
            Suppression(reason="no selector given")

    def test_empty_finding_id_loaded_from_yaml_still_rejected(self, tmp_path):
        # Regression (Codex review, PR #753, round 7): an explicit
        # `finding_id: ""` must not be accepted as a real, standalone
        # selector -- it can never match any actual finding.
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            'version: 1\nsuppressions:\n  - finding_id: ""\n    reason: r\n'
        )
        with pytest.raises(ValueError, match="finding_id"):
            SuppressionList.load(yaml_path)

    def test_loads_from_yaml(self, tmp_path):
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        canonical = report_canonical_finding_id(change)
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            f"  - finding_id: {canonical}\n"
            "    reason: accepted via canonical id\n"
        )
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 1
        assert suppressions.is_suppressed(change)

    def test_loads_from_yaml_when_the_digest_is_all_decimal(self, tmp_path):
        # Regression (Codex review, PR #753, fresh evidence): a
        # report_canonical_finding_id digest is 16 hex characters, so it
        # has a real chance of landing entirely within 0-9. An unquoted
        # all-decimal scalar in YAML parses as a Python int, not str --
        # if that raw int reached Suppression.finding_id unchanged, the
        # rule would silently match nothing (int != str comparison).
        all_decimal_id = "3327221372985366"
        assert all_decimal_id.isdigit()  # sanity: this scalar parses as int
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            f"  - finding_id: {all_decimal_id}\n"
            "    reason: accepted via canonical id\n"
        )
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 1
        rule = suppressions._suppressions[0]
        assert rule.finding_id == all_decimal_id
        assert isinstance(rule.finding_id, str)

    def test_loads_from_yaml_when_the_digest_looks_octal(self, tmp_path):
        # Regression (Codex review, PR #753, round 2): an unquoted,
        # leading-zero, all-octal-digit (0-7) scalar is read by PyYAML's
        # YAML 1.1 resolver as an OCTAL literal, not decimal --
        # "0123456701234567" resolves to a completely different int
        # (5744368105847), which str()-coercing the already-parsed value
        # can never recover the original digest from. Also covers a
        # zero-padded digest reducing to a single digit.
        octal_looking_id = "0123456701234567"
        zero_padded_id = "0000000000000001"
        assert int(octal_looking_id, 8) != int(octal_looking_id)  # sanity
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            "version: 1\n"
            "suppressions:\n"
            f"  - finding_id: {octal_looking_id}\n"
            "    reason: octal-looking digest\n"
            f"  - finding_id: {zero_padded_id}\n"
            "    reason: zero-padded digest\n"
        )
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 2
        rule_a, rule_b = suppressions._suppressions
        assert rule_a.finding_id == octal_looking_id
        assert rule_b.finding_id == zero_padded_id
        assert isinstance(rule_a.finding_id, str)
        assert isinstance(rule_b.finding_id, str)

    def test_loads_from_yaml_with_a_merge_key(self, tmp_path):
        # Regression (Codex review, PR #753, round 3): a merge key
        # (`<<: *anchor`) puts the referenced mapping's pairs one level of
        # indirection away from the entry's own `.value` list -- the raw
        # scan needs to resolve through it, not just scan direct pairs.
        octal_looking_id = "0123456701234567"
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text(
            "version: 1\n"
            "defaults: &d\n"
            f"  finding_id: {octal_looking_id}\n"
            "suppressions:\n"
            "  - <<: *d\n"
            "    reason: merged\n"
            "  - <<: *d\n"
            "    finding_id: '9999999999999999'\n"
            "    reason: local override wins over the merged value\n"
        )
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 2
        merged_rule, overridden_rule = suppressions._suppressions
        assert merged_rule.finding_id == octal_looking_id
        assert overridden_rule.finding_id == "9999999999999999"

    def test_version_key_still_parses_as_a_real_int(self, tmp_path):
        # Guard against the alternative fix (disabling YAML int resolution
        # loader-wide) reappearing: this file's own `version: 1` key must
        # stay a real int, since a resolver has no way to selectively
        # spare only the finding_id key.
        yaml_path = tmp_path / "suppress.yaml"
        yaml_path.write_text("version: 1\nsuppressions: []\n")
        suppressions = SuppressionList.load(yaml_path)
        assert len(suppressions) == 0  # would raise on a bad 'version' first


class TestReportFindingIdDisambiguator:
    """Regression coverage for Codex review, PR #1078, eighteenth round:
    two ODR/multi-TU occurrence findings that now legitimately survive
    `diff_filtering._dedup_exact` (distinguished only by `Change.
    disambiguator`) would otherwise collide on the same `report_finding_id`
    too, since that function never looked at `disambiguator` at all.
    """

    def test_two_findings_differing_only_by_disambiguator_get_distinct_ids(
        self,
    ) -> None:
        a = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        a.disambiguator = "tu-a"
        b = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        b.disambiguator = "tu-b"
        assert report_finding_id(a) != report_finding_id(b)

    def test_a_pre_existing_finding_with_no_disambiguator_hashes_unchanged(
        self,
    ) -> None:
        """Backward compatibility: a `Change` that never sets
        `disambiguator` (every finding predating this field, and the
        overwhelming majority of findings today) must hash exactly as
        before -- appending an always-empty segment would rehash every
        finding id this function has ever produced."""
        change = _func_removed(
            "_Z3fooi", description="Function removed: foo(int)", source_location=None
        )
        assert change.disambiguator is None
        key = "\x1f".join(
            [
                change.kind.value,
                change.symbol,
                change.old_value or "",
                change.new_value or "",
                change.source_location or "",
                change.description or "",
            ]
        )
        import hashlib

        expected = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        assert report_finding_id(change) == expected
