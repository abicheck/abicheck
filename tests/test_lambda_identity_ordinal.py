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

"""A closure's identity must not embed its source ``:line:col``.

Reported (real oneTBB 2021.13.0 -> 2022.3.0 comparison, AGENTS.md's
"Lambda-closure churn" entry, item 2): an unrelated edit anywhere earlier in
a header shifts every lambda declared below it to a new line/column. Since
:func:`~abicheck.name_classification.strip_anonymous_type_location` keeps
``:<line>:<col>`` as the only discriminator between two distinct lambdas in
one header, an unchanged closure-parameterized type/function then compares
as removed-plus-added purely from that line drift -- three separate noise
classes in one real report: a spurious ``type_removed``/``type_added`` pair,
a paired ``func_removed``/``func_added`` on every ctor/dtor/method of that
instantiation (via castxml's synthetic ctor/dtor keys, which embed the same
owner spelling), and a ``declaration_renamed`` RISK finding whose entire
content is the line-number text.

:func:`~abicheck.qualified_name_segments.renumber_anonymous_closure_identities` fixes this by
replacing the line:col discriminator with a stable ordinal -- "the Nth
lambda of this marker kind declared in this header" -- computed once per
snapshot. As long as an edit doesn't reorder or add/remove same-header,
same-kind lambdas relative to each other (true for every reported case,
which is unrelated line drift), both sides of a comparison assign the
identical ordinal to the identical closure.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from abicheck.buildsource.graph_facts import GraphEdge, GraphNode
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.identity import (
    Namespace,
    Record,
    entity_id_for_function,
    entity_id_for_type,
)
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import (
    _walk_rewrite_strings,
    apply_anonymous_type_ordinals,
    collect_anonymous_type_ordinals,
    renumber_anonymous_closure_identities,
)
from abicheck.serialization import snapshot_from_dict


# Frozen/mutable fixtures for TestFrozenDataclassesReachableFromTheWalkAreRebuilt
# below. Deliberately local, minimal shapes rather than real model classes, so
# the invariant is stated about `_walk_rewrite_strings` itself and stays true
# for whatever frozen field the model grows next.
@dataclass(frozen=True)
class _FrozenLeaf:
    name: str


@dataclass(frozen=True)
class _FrozenHolder:
    child: _FrozenLeaf


@dataclass
class _MutableHolder:
    child: _FrozenLeaf


@dataclass(frozen=True)
class _FrozenWithNonInit:
    name: str
    derived: str = field(default="", init=False)


def _collected_strings(value: object) -> list[str]:
    """Every string reachable from *value*, for asserting on a rewritten tree
    without hard-coding the shape each parametrized wrapper produced."""
    out: list[str] = []
    _collect_strings_into(value, out)
    return out


def _collect_strings_into(value: object, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif hasattr(value, "__dataclass_fields__"):
        for name in value.__dataclass_fields__:
            _collect_strings_into(getattr(value, name), out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings_into(item, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect_strings_into(k, out)
            _collect_strings_into(v, out)


def _closure(header: str, line: int, col: int) -> str:
    return strip_anonymous_type_location(f"(lambda at /src/x/{header}:{line}:{col})")


class TestOrdinalsAreStableAcrossLineDrift:
    def test_two_lambdas_keep_their_relative_order(self) -> None:
        old_names = [
            f"raii_guard<{_closure('task_group.h', 522, 26)}>",
            f"raii_guard<{_closure('task_group.h', 520, 18)}>",
        ]
        new_names = [
            f"raii_guard<{_closure('task_group.h', 539, 26)}>",
            f"raii_guard<{_closure('task_group.h', 528, 18)}>",
        ]
        old_ordinals = collect_anonymous_type_ordinals(old_names)
        new_ordinals = collect_anonymous_type_ordinals(new_names)
        old_final = [apply_anonymous_type_ordinals(n, old_ordinals) for n in old_names]
        new_final = [apply_anonymous_type_ordinals(n, new_ordinals) for n in new_names]
        assert old_final == new_final

    def test_different_headers_never_collide(self) -> None:
        names = [
            f"raii_guard<{_closure('a.h', 4, 3)}>",
            f"raii_guard<{_closure('b.h', 4, 3)}>",
        ]
        ordinals = collect_anonymous_type_ordinals(names)
        rewritten = [apply_anonymous_type_ordinals(n, ordinals) for n in names]
        assert rewritten[0] != rewritten[1]

    def test_a_marker_absent_from_the_ordinal_map_is_left_untouched(self) -> None:
        name = f"raii_guard<{_closure('a.h', 4, 3)}>"
        assert apply_anonymous_type_ordinals(name, {}) == name

    def test_quoted_ntt_string_looking_like_a_marker_is_not_rewritten(self) -> None:
        quoted = 'Tag<"(lambda:a.h:1:2)">'
        ordinals = collect_anonymous_type_ordinals([quoted])
        assert ordinals == {}
        assert (
            apply_anonymous_type_ordinals(quoted, {("(lambda", "a.h", 1, 2): 1})
            == quoted
        )


def _record(name: str, qualified: str | None = None) -> RecordType:
    return RecordType(name=name, kind="class", qualified_name=qualified, size_bits=8)


class TestSnapshotRenumbering:
    def _snapshot(self, version: str, line1: int, line2: int) -> AbiSnapshot:
        owner1 = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line1, 26)}>"
        owner2 = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line2, 18)}>"
        types = [
            _record(owner1.rsplit("::", 1)[-1], qualified=owner1),
            _record(owner2.rsplit("::", 1)[-1], qualified=owner2),
        ]
        ctor = Function(
            name="raii_guard::raii_guard",
            mangled=f"__abicheck_ctor__{owner1}()",
            return_type="void",
            visibility=Visibility.PUBLIC,
            params=[Param(name="p", type=f"{owner1} &&")],
        )
        return AbiSnapshot(
            library="libtbb.so", version=version, types=types, functions=[ctor]
        )

    def test_renumbering_makes_an_unrelated_line_shift_a_no_op(self) -> None:
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)

        renumber_anonymous_closure_identities(old)
        renumber_anonymous_closure_identities(new)

        assert old.types[0].qualified_name == new.types[0].qualified_name
        assert old.types[1].qualified_name == new.types[1].qualified_name
        assert old.functions[0].mangled == new.functions[0].mangled
        assert old.functions[0].params[0].type == new.functions[0].params[0].type

    def test_without_renumbering_the_line_shift_is_visible(self) -> None:
        # Sanity check that the fixture actually reproduces the bug absent
        # the fix, so the assertions above are testing something real.
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        assert old.types[0].qualified_name != new.types[0].qualified_name
        assert old.functions[0].mangled != new.functions[0].mangled

    def test_compare_reports_no_findings_for_pure_line_drift(self) -> None:
        """End-to-end: renumbering both sides before compare() eliminates the
        func_removed/func_added pair and the type-identity churn a plain
        line-number identity would otherwise report."""
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        renumber_anonymous_closure_identities(old)
        renumber_anonymous_closure_identities(new)

        result = compare(old, new)
        noisy_kinds = {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.TYPE_ADDED,
        }
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    def test_compare_without_renumbering_reports_the_reported_noise(self) -> None:
        old = self._snapshot("2021.13.0", 522, 520)
        new = self._snapshot("2022.3.0", 539, 528)
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_ADDED in kinds

    def test_renumbering_is_idempotent(self) -> None:
        snap = self._snapshot("2021.13.0", 522, 520)
        renumber_anonymous_closure_identities(snap)
        once = (snap.types[0].qualified_name, snap.functions[0].mangled)
        renumber_anonymous_closure_identities(snap)
        twice = (snap.types[0].qualified_name, snap.functions[0].mangled)
        assert once == twice

    def test_a_snapshot_with_no_closures_is_untouched(self) -> None:
        snap = AbiSnapshot(
            library="libplain.so",
            version="1.0",
            types=[_record("Widget", qualified="ns::Widget")],
            functions=[
                Function(
                    name="Widget::Widget",
                    mangled="_ZN2ns6WidgetC1Ev",
                    return_type="void",
                )
            ],
        )
        before_type = snap.types[0].qualified_name
        before_func = snap.functions[0].mangled
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].qualified_name == before_type
        assert snap.functions[0].mangled == before_func


class TestPayloadTextIsNeverCorrupted:
    """Codex review: a free-text/expression field (never a type/name
    spelling) can coincidentally contain a substring matching the closure
    marker syntax -- e.g. a deprecation message that literally quotes one.
    Such text must never be collected as identity evidence or rewritten,
    or a snapshot's own human-readable payload silently corrupts."""

    def test_a_deprecated_message_matching_the_marker_syntax_is_untouched(
        self,
    ) -> None:
        message = f"avoid {_closure('x.h', 10, 2)}"
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                replace(_record("Widget", qualified="ns::Widget"), deprecated=message)
            ],
        )
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].deprecated == message

    def test_a_default_initializer_matching_the_marker_syntax_is_untouched(
        self,
    ) -> None:
        from abicheck.model import TypeField

        expr = f"get_default({_closure('x.h', 10, 2)})"
        rec = replace(
            _record("Widget", qualified="ns::Widget"),
            fields=[TypeField(name="f", type="int", default=expr)],
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].fields[0].default == expr

    def test_payload_text_does_not_fabricate_an_ordinal_for_a_real_closure(
        self,
    ) -> None:
        """A deprecated message's coincidental marker must not consume an
        ordinal slot that a real, identity-bearing closure would otherwise
        get -- confirming exclusion happens at collection time too, not
        only at rewrite time."""
        closure_type = f"raii_guard<{_closure('x.h', 5, 1)}>"
        message = f"avoid {_closure('x.h', 1, 1)}"
        rec = replace(
            _record(closure_type, qualified=f"ns::{closure_type}"),
            deprecated=message,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        # The real closure gets ordinal #1 (the only identity-bearing one
        # collected) -- not #2, which it would get if the deprecated
        # message's coincidental marker at line 1 (earlier than line 5)
        # had also been collected as a competing coordinate.
        assert "#1)" in snap.types[0].qualified_name
        assert snap.types[0].deprecated == message

    def test_a_variable_initializer_value_matching_the_marker_syntax_is_untouched(
        self,
    ) -> None:
        """Codex review, fresh evidence: ``Variable.value`` (its compile-time
        constant initializer) is the identical payload shape as
        ``deprecated``/``default`` -- reached by the dataclass-field walk,
        not previously excluded."""
        from abicheck.model import Variable, Visibility

        value = f"text {_closure('x.h', 10, 2)}"
        var = Variable(
            name="v",
            mangled="_ZN1vE",
            type="const char *",
            visibility=Visibility.PUBLIC,
            value=value,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", variables=[var])
        renumber_anonymous_closure_identities(snap)
        assert snap.variables[0].value == value

    def test_a_constant_value_matching_the_marker_syntax_is_untouched(self) -> None:
        """Codex review, fresh evidence: ``AbiSnapshot.constants`` (a
        ``#define``/``constexpr`` name -> value string dict) is payload,
        never a type-name spelling -- the generic dict walk previously
        rewrote its values along with any genuine identity-bearing dict's."""
        value = f"text {_closure('x.h', 10, 2)}"
        snap = AbiSnapshot(library="lib.so", version="1.0", constants={"MSG": value})
        renumber_anonymous_closure_identities(snap)
        assert snap.constants["MSG"] == value

    def test_a_constant_value_does_not_fabricate_an_ordinal_for_a_real_closure(
        self,
    ) -> None:
        """Same collection-time exclusion check as the deprecated-message
        sibling above, for a constant's payload value."""
        closure_type = f"raii_guard<{_closure('x.h', 5, 1)}>"
        value = f"text {_closure('x.h', 1, 1)}"
        rec = _record(closure_type, qualified=f"ns::{closure_type}")
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[rec],
            constants={"MSG": value},
        )
        renumber_anonymous_closure_identities(snap)
        assert "#1)" in snap.types[0].qualified_name
        assert snap.constants["MSG"] == value

    def test_a_source_location_matching_the_marker_syntax_is_untouched(
        self,
    ) -> None:
        """Codex review, fresh evidence: source_location/source_header
        (ADR-015 provenance -- a filesystem path, never a type/name
        spelling) is the identical payload shape as deprecated/default/
        value -- a legal path containing marker-shaped text of its own
        (e.g. a directory literally named "(lambda:a.h:1:2)") was rewritten
        even for a snapshot with no real closure at all, corrupting
        persisted declaration provenance."""
        path = f"/tmp/{_closure('x.h', 10, 2)}/api.h"
        rec = replace(
            _record("Widget", qualified="ns::Widget"),
            source_location=f"{path}:42",
            source_header=path,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].source_location == f"{path}:42"
        assert snap.types[0].source_header == path

    def test_a_source_location_does_not_fabricate_an_ordinal_for_a_real_closure(
        self,
    ) -> None:
        """Same collection-time exclusion check as the deprecated-message/
        constant siblings above, for source_location/source_header."""
        closure_type = f"raii_guard<{_closure('x.h', 5, 1)}>"
        path = f"/tmp/{_closure('x.h', 1, 1)}/api.h"
        rec = replace(
            _record(closure_type, qualified=f"ns::{closure_type}"),
            source_location=f"{path}:42",
            source_header=path,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        assert "#1)" in snap.types[0].qualified_name
        assert snap.types[0].source_location == f"{path}:42"
        assert snap.types[0].source_header == path


class TestLegacyPersistedSnapshotsAreRenumberedOnLoad:
    """A snapshot persisted by a pre-fix abicheck still carries the raw
    ``:<line>:<col>`` closure identity. Comparing it (via ``compare``'s
    normal saved-baseline-vs-fresh-dump workflow) against a freshly-dumped
    snapshot -- which IS renumbered -- must not manufacture a
    removed+added pair purely from the encoding change (Codex review on
    the original PR)."""

    def _legacy_dict(self, line: int) -> dict:
        owner = f"tbb::detail::d1::raii_guard<{_closure('task_group.h', line, 26)}>"
        return {
            "library": "libtbb.so",
            "version": "2021.13.0",
            "schema_version": 25,
            "types": [
                {
                    "name": owner.rsplit("::", 1)[-1],
                    "qualified_name": owner,
                    "kind": "class",
                    "size_bits": 8,
                }
            ],
            "functions": [
                {
                    "name": "raii_guard::raii_guard",
                    "mangled": f"__abicheck_ctor__{owner}()",
                    "return_type": "void",
                }
            ],
        }

    def test_loading_a_legacy_snapshot_renumbers_it(self) -> None:
        loaded = snapshot_from_dict(self._legacy_dict(522))
        assert "#" in loaded.types[0].qualified_name
        assert ":522:" not in loaded.types[0].qualified_name

    def test_legacy_baseline_agrees_with_a_fresh_dump_across_line_drift(
        self,
    ) -> None:
        legacy_baseline = snapshot_from_dict(self._legacy_dict(522))

        fresh = AbiSnapshot(
            library="libtbb.so",
            version="2022.3.0",
            types=[
                _record(
                    f"raii_guard<{_closure('task_group.h', 539, 26)}>",
                    qualified=(
                        "tbb::detail::d1::raii_guard<"
                        f"{_closure('task_group.h', 539, 26)}>"
                    ),
                )
            ],
            functions=[
                Function(
                    name="raii_guard::raii_guard",
                    mangled=(
                        "__abicheck_ctor__tbb::detail::d1::raii_guard<"
                        f"{_closure('task_group.h', 539, 26)}>()"
                    ),
                    return_type="void",
                )
            ],
        )
        renumber_anonymous_closure_identities(fresh)

        assert legacy_baseline.types[0].qualified_name == fresh.types[0].qualified_name
        assert legacy_baseline.functions[0].mangled == fresh.functions[0].mangled

        result = compare(legacy_baseline, fresh)
        noisy_kinds = {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_ADDED,
            ChangeKind.TYPE_REMOVED,
            ChangeKind.TYPE_ADDED,
        }
        assert not ({c.kind for c in result.changes} & noisy_kinds)

    def test_a_snapshot_already_in_ordinal_form_round_trips_unchanged(self) -> None:
        already_ordinal = {
            "library": "libtbb.so",
            "version": "2022.3.0",
            "schema_version": 25,
            "types": [
                {
                    "name": "raii_guard<(lambda:task_group.h#1)>",
                    "kind": "class",
                }
            ],
        }
        loaded = snapshot_from_dict(already_ordinal)
        assert loaded.types[0].name == "raii_guard<(lambda:task_group.h#1)>"


class TestKnownLimitationDifferentFilesSharingABasename:
    """Documented, accepted limitation (Codex review): the ordinal group
    key is ``(marker, header basename)`` -- the same checkout-independent
    basename :func:`strip_anonymous_type_location` already reduces a
    full path to -- so two genuinely different files sharing a basename
    share one ordinal sequence. This test pins the documented behavior so
    a future change to the grouping key is a deliberate decision, not a
    silent regression in either direction."""

    def test_an_edit_in_one_file_can_reorder_an_unrelated_same_basename_file(
        self,
    ) -> None:
        # Two DIFFERENT physical files, both named "config.h" (a vendored
        # dependency shape), each declaring one lambda -- before either
        # snapshot has an edit, both compare identically to a snapshot of
        # themselves alone.
        before = [
            strip_anonymous_type_location("(lambda at /vendor/a/config.h:100:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:5:1)"),
        ]
        before_ordinals = collect_anonymous_type_ordinals(before)
        before_final = [
            apply_anonymous_type_ordinals(n, before_ordinals) for n in before
        ]

        # An unrelated lambda is added to vendor/b/config.h *only*, at a
        # line ahead of vendor/a's own (unedited) lambda.
        after = [
            strip_anonymous_type_location("(lambda at /vendor/a/config.h:100:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:1:1)"),
            strip_anonymous_type_location("(lambda at /vendor/b/config.h:5:1)"),
        ]
        after_ordinals = collect_anonymous_type_ordinals(after)
        after_final = [apply_anonymous_type_ordinals(n, after_ordinals) for n in after]

        # vendor/a's own, completely unedited lambda is reassigned a
        # different ordinal purely because of the unrelated insertion in
        # vendor/b -- the documented, accepted limitation.
        assert before_final[0] != after_final[0]


class TestBasenameWithParensIsStillRenumbered:
    """Codex review: ``strip_anonymous_type_location`` legitimately produces
    a marker like ``(lambda:foo(test).hpp:10:2)`` for a header whose
    basename itself contains parens -- the ordinal regex's old ``[^:()]+``
    basename capture could never match this, so the ordinal map stayed
    empty and unrelated line drift in such a header still produced the
    removed/added findings this whole mechanism exists to eliminate."""

    def test_line_drift_in_a_parenthesized_basename_still_collapses(self) -> None:
        old = [strip_anonymous_type_location("(lambda at /src/foo(test).hpp:10:2)")]
        new = [strip_anonymous_type_location("(lambda at /src/foo(test).hpp:14:2)")]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:foo(test).hpp#1)"

    def test_two_closures_in_the_same_parenthesized_basename_get_distinct_ordinals(
        self,
    ) -> None:
        names = [
            strip_anonymous_type_location("(lambda at /src/foo(test).hpp:10:2)"),
            strip_anonymous_type_location("(lambda at /src/foo(test).hpp:20:2)"),
        ]
        ordinals = collect_anonymous_type_ordinals(names)
        rewritten = [apply_anonymous_type_ordinals(n, ordinals) for n in names]
        assert rewritten == [
            "(lambda:foo(test).hpp#1)",
            "(lambda:foo(test).hpp#2)",
        ]

    def test_a_second_marker_after_a_parenthesized_basename_is_not_swallowed(
        self,
    ) -> None:
        """A parenthesized basename's own balanced ``()`` must not let the
        regex bleed past this marker's closing paren into a second, later
        marker's own text."""
        combined = (
            f"Wrap<{strip_anonymous_type_location('(lambda at /src/foo(x).h:1:2)')}, "
            f"{strip_anonymous_type_location('(lambda at /src/bar.h:3:4)')}>"
        )
        ordinals = collect_anonymous_type_ordinals([combined])
        assert ("(lambda", "foo(x).h", 1, 2) in ordinals
        assert ("(lambda", "bar.h", 3, 4) in ordinals
        rewritten = apply_anonymous_type_ordinals(combined, ordinals)
        assert rewritten == "Wrap<(lambda:foo(x).h#1), (lambda:bar.h#1)>"

    def test_two_levels_of_nested_parens_in_a_basename_still_match(self) -> None:
        """Codex review, follow-up: the regex-based basename capture
        (``\\([^()]*\\)``) can only ever balance ONE level of nesting, so a
        legal basename with two -- ``foo(a(b)).hpp`` -- fell through to no
        match at all, silently leaving the ordinal map empty for it."""
        old = [strip_anonymous_type_location("(lambda at /src/foo(a(b)).hpp:10:2)")]
        new = [strip_anonymous_type_location("(lambda at /src/foo(a(b)).hpp:14:2)")]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:foo(a(b)).hpp#1)"

    def test_a_second_marker_after_a_doubly_nested_basename_is_not_swallowed(
        self,
    ) -> None:
        combined = (
            f"Wrap<{strip_anonymous_type_location('(lambda at /src/foo(a(b)).h:1:2)')}, "
            f"{strip_anonymous_type_location('(lambda at /src/bar.h:3:4)')}>"
        )
        ordinals = collect_anonymous_type_ordinals([combined])
        assert ("(lambda", "foo(a(b)).h", 1, 2) in ordinals
        assert ("(lambda", "bar.h", 3, 4) in ordinals
        rewritten = apply_anonymous_type_ordinals(combined, ordinals)
        assert rewritten == "Wrap<(lambda:foo(a(b)).h#1), (lambda:bar.h#1)>"

    def test_an_unmatched_closing_paren_in_a_basename_still_matches(self) -> None:
        """Codex review, follow-up: a legal basename can contain an
        UNMATCHED ``)`` of its own (``foo)bar.hpp``) -- the depth-0 ``)``
        it produces must not be mistaken for the marker's own terminator
        before the real trailing coordinates are ever reached."""
        old = [strip_anonymous_type_location("(lambda at /src/foo)bar.hpp:10:2)")]
        new = [strip_anonymous_type_location("(lambda at /src/foo)bar.hpp:14:2)")]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:foo)bar.hpp#1)"

    def test_a_second_marker_after_an_unmatched_paren_basename_is_not_swallowed(
        self,
    ) -> None:
        combined = (
            f"Wrap<{strip_anonymous_type_location('(lambda at /src/foo)bar.h:1:2)')}, "
            f"{strip_anonymous_type_location('(lambda at /src/baz.h:3:4)')}>"
        )
        ordinals = collect_anonymous_type_ordinals([combined])
        assert ("(lambda", "foo)bar.h", 1, 2) in ordinals
        assert ("(lambda", "baz.h", 3, 4) in ordinals
        rewritten = apply_anonymous_type_ordinals(combined, ordinals)
        assert rewritten == "Wrap<(lambda:foo)bar.h#1), (lambda:baz.h#1)>"


class TestMarkerLikeTextInsideABasenameIsNotASecondMatch:
    """Codex review: a legal basename can itself contain a *complete*
    marker-shaped substring, e.g. ``(lambda:a.h:1:2).hpp`` -- a real,
    if unusual, filename. Before this fix, the outer scan and an
    independently-found inner prefix match (found by
    ``_ANON_TYPE_MARKER_PREFIX_RE.finditer`` re-matching the nested
    ``"(lambda:"`` text) both produced overlapping ``_AnonTypeMatch``
    results, and ``apply_anonymous_type_ordinals``'s splice-based rewrite
    then corrupted the string by rewriting both overlapping ranges."""

    def test_nested_marker_shaped_basename_produces_exactly_one_match(self) -> None:
        # A legal, if unusual, basename that is itself a complete marker
        # (already in normalized, post-strip form, as the codex report's
        # own example is -- exercising _anon_type_ordinal_matches directly
        # rather than routing through strip_anonymous_type_location, which
        # only ever normalizes the first "at <path>" occurrence).
        outer = "(lambda:(lambda:a.h:1:2).hpp:10:2)"
        ordinals = collect_anonymous_type_ordinals([outer])
        # Exactly one closure identity was recorded -- the outer marker,
        # whose declaring-header basename is the whole nested string --
        # not two overlapping ones.
        assert len(ordinals) == 1
        (key,) = ordinals
        assert key == ("(lambda", "(lambda:a.h:1:2).hpp", 10, 2)

    def test_nested_marker_shaped_basename_rewrites_without_corruption(self) -> None:
        outer = "(lambda:(lambda:a.h:1:2).hpp:10:2)"
        rewritten = apply_anonymous_type_ordinals(
            outer, collect_anonymous_type_ordinals([outer])
        )
        # A single, well-formed rewrite of the *outer* marker only -- the
        # nested marker-shaped basename text is left completely untouched,
        # never independently rewritten to "...#1)" of its own.
        assert rewritten == "(lambda:(lambda:a.h:1:2).hpp#1)"

    def test_line_drift_still_collapses_for_a_marker_shaped_basename(self) -> None:
        old = ["(lambda:(lambda:a.h:1:2).hpp:10:2)"]
        new = ["(lambda:(lambda:a.h:1:2).hpp:14:2)"]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:(lambda:a.h:1:2).hpp#1)"


class TestBasenameWithAnUnmatchedOpeningParenIsStillRenumbered:
    """Codex review, fresh evidence: a legal basename can contain an
    UNMATCHED ``(`` of its own (``foo(bar.hpp``, legal on POSIX) -- the
    mirror image of the already-fixed unmatched-``)`` case. Depth never
    returns to 0 by the time the marker's real closing paren is reached,
    so the depth-tracking scan alone finds no match at all and the
    closure keeps its line:col discriminator, leaving unrelated line
    drift in such a header still producing the removed/added findings
    this whole mechanism exists to eliminate."""

    def test_line_drift_in_a_basename_with_an_unmatched_open_paren_still_collapses(
        self,
    ) -> None:
        old = [strip_anonymous_type_location("(lambda at /src/foo(bar.hpp:10:2)")]
        new = [strip_anonymous_type_location("(lambda at /src/foo(bar.hpp:14:2)")]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:foo(bar.hpp#1)"

    def test_a_second_marker_after_an_unmatched_open_paren_basename_is_not_swallowed(
        self,
    ) -> None:
        combined = (
            f"Wrap<{strip_anonymous_type_location('(lambda at /src/foo(bar.h:1:2)')}, "
            f"{strip_anonymous_type_location('(lambda at /src/baz.h:3:4)')}>"
        )
        ordinals = collect_anonymous_type_ordinals([combined])
        assert ("(lambda", "foo(bar.h", 1, 2) in ordinals
        assert ("(lambda", "baz.h", 3, 4) in ordinals
        rewritten = apply_anonymous_type_ordinals(combined, ordinals)
        assert rewritten == "Wrap<(lambda:foo(bar.h#1), (lambda:baz.h#1)>"


class TestBasenameWithEmbeddedCoordinateShapedTextIsStillRenumbered:
    """Codex review, fresh evidence: a legal basename can contain
    coordinate-shaped text of its own before the real terminator
    (``foo:1:2)bar.hpp``) -- the depth-tracking scan's first depth-0
    candidate was ``foo:1:2)``, not the real trailing ``:10:2)`` at the
    end, corrupting the marker into ``(lambda:foo#1)bar.hpp:10:2)``
    instead of assigning an ordinal to the real coordinates. The scan now
    prefers the LAST depth-0 candidate found, not the first."""

    def test_line_drift_with_an_embedded_coordinate_shaped_basename_collapses(
        self,
    ) -> None:
        old = [strip_anonymous_type_location("(lambda at /src/foo:1:2)bar.hpp:10:2)")]
        new = [strip_anonymous_type_location("(lambda at /src/foo:1:2)bar.hpp:14:2)")]
        old_final = apply_anonymous_type_ordinals(
            old[0], collect_anonymous_type_ordinals(old)
        )
        new_final = apply_anonymous_type_ordinals(
            new[0], collect_anonymous_type_ordinals(new)
        )
        assert old_final == new_final == "(lambda:foo:1:2)bar.hpp#1)"

    def test_a_second_marker_after_an_embedded_coordinate_basename_is_not_swallowed(
        self,
    ) -> None:
        combined = (
            f"Wrap<{strip_anonymous_type_location('(lambda at /src/foo:1:2)bar.h:10:2)')}, "
            f"{strip_anonymous_type_location('(lambda at /src/baz.h:3:4)')}>"
        )
        ordinals = collect_anonymous_type_ordinals([combined])
        assert ("(lambda", "foo:1:2)bar.h", 10, 2) in ordinals
        assert ("(lambda", "baz.h", 3, 4) in ordinals
        rewritten = apply_anonymous_type_ordinals(combined, ordinals)
        assert rewritten == "Wrap<(lambda:foo:1:2)bar.h#1), (lambda:baz.h#1)>"


class TestHybridMergeDefersRenumbering:
    """Codex review: ``--ast-frontend hybrid`` runs castxml and clang over
    the same headers and merges the two snapshots by identity key
    (``type_map_key``/mangled name). If each backend's own closure ordinal
    were computed independently -- BEFORE the merge -- a backend that sees
    a header's lambdas in a different count (one omits an earlier
    same-header lambda the other captures) would assign the SAME later
    closure a DIFFERENT ordinal on each side, and the merge's identity
    lookup would silently miss the join. Deferring renumbering until after
    the merge means both backends' RAW ``:line:col`` spellings -- which
    agree by construction, since both describe the same physical source
    location -- are what the merge actually keys on.
    """

    def test_shared_closure_merges_despite_differing_lambda_counts(self) -> None:
        from abicheck.dumper_hybrid import run_hybrid_dump

        shared = _closure("widget.h", 20, 5)
        owner_castxml = f"Foo<{shared}>"
        owner_clang = f"Foo<{shared}>"

        castxml_snap = AbiSnapshot(
            library="lib.so",
            version="old",
            from_headers=True,
            types=[
                # castxml sees an EARLIER lambda too (a template castxml
                # instantiates that clang's own leg never reaches), so its
                # own independent ordinal for the shared closure would be
                # #2, not #1.
                _record(f"Earlier<{_closure('widget.h', 5, 1)}>"),
                _record(owner_castxml, qualified=owner_castxml),
            ],
        )
        clang_snap = AbiSnapshot(
            library="lib.so",
            version="old",
            from_headers=True,
            types=[
                # clang's leg never instantiates the earlier template, so
                # its own independent ordinal for the SAME shared closure
                # would be #1 -- a real fact only clang captures rides
                # along on this type, to prove the merge actually joined.
                replace(_record(owner_clang, qualified=owner_clang), is_abstract=True),
            ],
        )

        def fake_dump(so_path, headers, *, header_backend, **kwargs):
            return castxml_snap if header_backend == "castxml" else clang_snap

        merged = run_hybrid_dump(fake_dump, Path("lib.so"), [])

        matched = [t for t in merged.types if t.name.startswith("Foo<")]
        assert len(matched) == 1, "the shared closure must merge into one type"
        assert matched[0].is_abstract is True, (
            "clang's is_abstract fact must have reached the merged type -- "
            "if the merge missed the join (mismatched per-leg ordinals), "
            "this would be None (castxml's own value) instead"
        )
        # And the final, merged snapshot IS in ordinal form -- renumbering
        # happened, just deferred to after the merge.
        assert "#" in matched[0].name
        assert ":20:" not in matched[0].name


class TestServiceRunDumpHybridAlsoDefersRenumbering:
    """Codex review on PR #868, fresh evidence: ``service.run_dump``'s own
    hybrid recursion (the real Tier-2 entry point the CLI routes through,
    per its own comment -- distinct from ``dumper_hybrid.run_hybrid_dump``,
    which is for direct ``dumper.dump()`` Python-API callers) is the exact
    same shape ``TestHybridMergeDefersRenumbering`` above tests, but was
    missing the defer/renumber-after-merge fix entirely: each recursive
    ``run_dump()`` call independently renumbered its own closure markers
    before the merge, reproducing the identical ordinal-desync bug.
    """

    def test_shared_closure_merges_despite_differing_lambda_counts(
        self, tmp_path
    ) -> None:
        from unittest.mock import patch

        from abicheck.service import run_dump

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)

        shared = _closure("widget.h", 20, 5)
        owner = f"Foo<{shared}>"

        castxml_snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            ast_producer="castxml",
            types=[
                # castxml sees an EARLIER lambda too, so its own
                # independent ordinal for the shared closure would be #2.
                _record(f"Earlier<{_closure('widget.h', 5, 1)}>"),
                _record(owner, qualified=owner),
            ],
        )
        clang_snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            from_headers=True,
            ast_producer="clang",
            types=[
                # clang's leg never sees the earlier template, so its own
                # independent ordinal for the SAME closure would be #1 --
                # a real clang-only fact rides along to prove the merge
                # actually joined rather than missing due to desync.
                replace(_record(owner, qualified=owner), is_abstract=True),
            ],
        )

        def _fake_dump_elf(*args, **kwargs):
            compile_ctx = kwargs.get("compile")
            if compile_ctx is not None and compile_ctx.frontend == "clang":
                return clang_snap
            return castxml_snap

        with patch("abicheck.service_dump_native._dump_elf", side_effect=_fake_dump_elf):
            merged = run_dump(p, "elf", header_backend="hybrid")

        matched = [t for t in merged.types if t.name.startswith("Foo<")]
        assert len(matched) == 1, "the shared closure must merge into one type"
        assert matched[0].is_abstract is True, (
            "clang's is_abstract fact must have reached the merged type -- "
            "a per-leg ordinal desync would miss the join"
        )
        assert "#" in matched[0].name
        assert ":20:" not in matched[0].name


class TestFactProvenanceKeysAreRenumberedToo:
    """A hybrid snapshot's ``fact_provenance`` dict is keyed by composite
    strings (``fact_provenance.type_fact_key``/``field_fact_key``) that
    embed the exact same closure-parameterized type-name spelling
    ``types``/``functions``/etc. carry. If renumbering rewrote only the
    ABI-surface fields and left these keys in ``:line:col`` form, a
    renamed type's provenance would become unreachable through
    ``fact_provenance.fact_producer()`` -- silently defeating every
    provenance-gated detector for that declaration (Codex review on
    PR #868, fresh evidence)."""

    def test_type_fact_key_is_renumbered_in_place(self) -> None:
        from abicheck.fact_provenance import type_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={type_fact_key(owner, "is_abstract"): "castxml"},
        )
        renumber_anonymous_closure_identities(snap)

        new_name = snap.types[0].qualified_name
        assert new_name is not None
        assert "#" in new_name
        assert type_fact_key(new_name, "is_abstract") in snap.fact_provenance
        assert type_fact_key(owner, "is_abstract") not in snap.fact_provenance
        assert snap.fact_provenance[type_fact_key(new_name, "is_abstract")] == (
            "castxml"
        )

    def test_field_fact_key_is_renumbered_in_place(self) -> None:
        from abicheck.fact_provenance import field_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={field_fact_key(owner, "x", "default"): "clang"},
        )
        renumber_anonymous_closure_identities(snap)

        new_name = snap.types[0].qualified_name
        assert new_name is not None
        assert field_fact_key(new_name, "x", "default") in snap.fact_provenance

    def test_fact_producer_resolves_after_renumbering(self) -> None:
        """End-to-end through the real reader, not just the raw dict key."""
        from abicheck.fact_provenance import fact_producer, type_fact_key

        owner = f"Foo<{_closure('widget.h', 20, 5)}>"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            from_headers=True,
            ast_producer="hybrid",
            types=[_record(owner, qualified=owner)],
            fact_provenance={type_fact_key(owner, "is_abstract"): "castxml"},
        )
        renumber_anonymous_closure_identities(snap)
        renamed_owner = snap.types[0].qualified_name
        assert renamed_owner is not None
        key = type_fact_key(renamed_owner, "is_abstract")
        assert fact_producer(snap, key) == "castxml"


class TestL5SourceGraphIdentitiesAreNotRenumbered:
    """Phase 4 of ``docs/contribute/plans/bug-class-regression-testing.md``
    (the ``identity.environment_taint`` bug class): a dedicated canary for
    the residual AGENTS.md already documents under "A named follow-on" --
    "The L5 source graph's own node identities are not renumbered
    alongside the flat snapshot's closure markers" (PR #868's own
    follow-up note).

    ``_LAMBDA_IDENTITY_FIELDS`` (``qualified_name_segments.py``) lists
    exactly ``functions``/``variables``/``types``/``enums``/``typedefs``/
    ``typedefs_qualified``/``fact_provenance`` -- ``build_source`` (which
    carries L5's own ``source_graph``) is absent from that list, so a
    closure-parameterized node label there is invisible to the rewrite
    walk entirely, regardless of content. This asserts the residual's OWN
    bound per ``KnownGap``'s docstring rule (not the eventually-correct
    behavior): the SAME marker renumbers on the flat side and is left
    completely untouched on the L5 side, in one snapshot, so a future fix
    that starts renumbering ``source_graph`` node labels too breaks this
    test loudly (and a regression that also stops renumbering the flat
    side breaks it the same way) -- either direction of drift is caught.
    """

    def test_flat_side_renumbers_while_l5_node_label_does_not(self) -> None:
        """Uses production-shaped identity-bearing node/edge ids (the real
        ``decl://``/``type://`` scheme :func:`~abicheck.buildsource.
        graph_facts._decl_node_id`/``_type_node_id`` mint, embedding the
        raw identity string verbatim), not an unrelated opaque id -- and a
        real edge referencing that node -- so a future fix that renumbers
        node ids/edge endpoints (not just human-readable labels) is caught
        too, not only one that starts renumbering labels (Codex review,
        PR #898)."""
        owner = f"Foo<{_closure('widget.h', 522, 26)}>"
        node_label = "(lambda at /src/x/widget.h:522:26)"
        node_id = f"decl://{owner}"
        edge_dst_id = "type://Other"
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[_record(owner, qualified=owner)],
            build_source=BuildSourcePack(
                root=Path("/src/x"),
                source_graph=SourceGraphSummary(
                    nodes=[GraphNode(id=node_id, kind="source_decl", label=node_label)],
                    edges=[
                        GraphEdge(src=node_id, dst=edge_dst_id, kind="DECL_HAS_TYPE")
                    ],
                ),
            ),
        )
        # SourceGraphSummary.__post_init__ (via add_node -> ensure_facts_and_
        # resolve) already normalizes a decl/type node's id AND label at
        # *construction* time -- but that is the pre-existing, unrelated
        # checkout-PATH-taint normalization (graph_facts._normalize_graph_
        # identity), not this fix's ORDINAL renumbering. It still leaves the
        # raw :line:col intact (only the surrounding path/spelling changes),
        # which is exactly the residual this test targets -- so capture the
        # already-normalized-but-still-:line:col-bearing label/id/edge AFTER
        # construction, as the true "before renumber_anonymous_closure_
        # identities" baseline, rather than asserting against the literal
        # strings passed into the constructor.
        graph = snap.build_source.source_graph  # type: ignore[union-attr]
        assert graph is not None
        pre_renumber_id = graph.nodes[0].id
        pre_renumber_label = graph.nodes[0].label
        pre_renumber_edge_src = graph.edges[0].src
        assert "522" in pre_renumber_label and "26" in pre_renumber_label

        renumber_anonymous_closure_identities(snap)

        # The flat side did its job: the raw :line:col marker is gone.
        renamed = snap.types[0].qualified_name
        assert renamed is not None
        assert "522" not in renamed and "26" not in renamed

        # The L5 side is untouched by THIS pass -- still exactly the
        # pre-renumber spelling, in the node's identity-bearing id, its
        # label, AND the edge endpoint referencing it. This is the
        # documented gap's own bound, not a desired outcome. Re-fetches
        # ``source_graph`` from ``snap`` AFTER the call rather than reusing
        # the ``graph`` reference captured before it (Codex review, PR
        # #898): a future fix that closes this gap by constructing a
        # rewritten ``SourceGraphSummary``/``BuildSourcePack`` and
        # reassigning it onto ``snap.build_source`` -- rather than mutating
        # the existing objects in place -- would leave the stale ``graph``
        # reference showing the old, unrenumbered values forever, making
        # this canary pass for the wrong reason even after the gap closed.
        assert snap.build_source is not None
        post_renumber_graph = snap.build_source.source_graph
        assert post_renumber_graph is not None
        assert post_renumber_graph.nodes[0].id == pre_renumber_id
        assert post_renumber_graph.nodes[0].label == pre_renumber_label
        assert (
            "522" in post_renumber_graph.nodes[0].label
            and "26" in post_renumber_graph.nodes[0].label
        )
        assert post_renumber_graph.edges[0].src == pre_renumber_edge_src


class TestFrozenDataclassesReachableFromTheWalkAreRebuilt:
    """``_walk_rewrite_strings`` must handle a FROZEN dataclass anywhere in
    the object graph it walks — rebuilding it rather than ``setattr``-ing it.

    The bug this pins is not a wrong ordinal, it is an outright crash plus a
    latent identity taint. ``setattr`` on a frozen instance raises
    ``FrozenInstanceError``, so the moment any field reachable from
    ``functions``/``variables``/``types``/``enums`` held one, every dump of
    a lambda-bearing library aborted (found this way: ADR-063 Phase 2's
    ``entity_id`` carrier is exactly such a field, and ten end-to-end tests
    in ``test_identity_taint_end_to_end.py`` failed on it at once). And had
    the walk merely *skipped* frozen values instead, the marker inside one
    would have survived in raw ``:line:col`` form — a path/line-tainted
    identity sitting next to the normalized ones, which is precisely the
    taint class ``identity.environment_taint`` exists to forbid.

    The invariant is therefore stated against the primitive itself, over
    several independently-chosen frozen shapes (nested in a tuple, in a
    list, as a dict value, at two levels of nesting, and one carrying an
    ``init=False`` field), not only against the one carrier that exposed
    it — a later frozen model field must be covered by construction rather
    than by someone remembering to add a case.
    """

    @staticmethod
    def _ordinals() -> dict[tuple[str, str, int, int], int]:
        return collect_anonymous_type_ordinals(
            [_closure("h.h", 10, 2), _closure("h.h", 20, 4)]
        )

    def _rewrite(self, value: object) -> object:
        ordinals = self._ordinals()
        return _walk_rewrite_strings(
            value, lambda text: apply_anonymous_type_ordinals(text, ordinals)
        )

    def test_frozen_dataclass_is_rebuilt_with_rewritten_strings(self) -> None:
        original = _FrozenLeaf(name=_closure("h.h", 20, 4))
        result = self._rewrite(original)
        assert isinstance(result, _FrozenLeaf)
        # Rebuilt, not mutated: the original instance is untouched.
        assert original.name == _closure("h.h", 20, 4)
        assert result is not original
        assert "20" not in result.name and "#2" in result.name

    @pytest.mark.parametrize(
        "wrap",
        [
            pytest.param(lambda leaf: (leaf,), id="in-a-tuple"),
            pytest.param(lambda leaf: [leaf], id="in-a-list"),
            pytest.param(lambda leaf: {"k": leaf}, id="as-a-dict-value"),
            pytest.param(lambda leaf: ([leaf],), id="two-levels-deep"),
            pytest.param(lambda leaf: _MutableHolder(child=leaf), id="on-a-mutable-parent"),
            pytest.param(
                lambda leaf: _FrozenHolder(child=leaf), id="on-a-frozen-parent"
            ),
        ],
    )
    def test_frozen_leaf_is_rewritten_at_any_position(self, wrap: object) -> None:
        result = self._rewrite(wrap(_FrozenLeaf(name=_closure("h.h", 10, 2))))  # type: ignore[operator]
        found = [s for s in _collected_strings(result) if "(lambda" in s]
        assert found, "the walk never reached the frozen leaf at all"
        assert all("#1" in s for s in found)
        assert all(":10:2" not in s for s in found)

    def test_frozen_dataclass_with_a_non_init_field_does_not_crash(self) -> None:
        # ``dataclasses.replace`` rejects an ``init=False`` field, so the
        # rebuild must not try to hand it one. The field is unreachable to
        # a caller by construction; the requirement here is only that the
        # walk still completes and still rewrites what it legitimately can.
        result = self._rewrite(_FrozenWithNonInit(name=_closure("h.h", 10, 2)))
        assert isinstance(result, _FrozenWithNonInit)
        assert "#1" in result.name

    def test_a_changed_non_init_field_is_itself_rewritten(self) -> None:
        # A field populated in `__post_init__` (simulated via the identical
        # `object.__setattr__` escape hatch) independently holds its own
        # closure marker; `name` never changes, so `replacements` stays
        # empty -- discarding the rewrite left it stale (Codex review), and
        # rebuilding a NEW instance only when `replacements` was non-empty
        # instead mutated `original` in place (CodeRabbit review), PR #943.
        original = _FrozenWithNonInit(name="plain::Type")
        object.__setattr__(original, "derived", _closure("h.h", 10, 2))
        original_derived = original.derived
        result = self._rewrite(original)
        assert isinstance(result, _FrozenWithNonInit)
        assert result.name == "plain::Type"
        assert "#1" in result.derived
        assert ":10:2" not in result.derived
        assert result is not original
        assert original.derived == original_derived

    def test_unchanged_frozen_dataclass_is_returned_as_the_same_object(self) -> None:
        # No marker to rewrite -> no rebuild, so a snapshot with no closures
        # anywhere keeps object identity exactly as it did before.
        original = _FrozenLeaf(name="plain::Type")
        assert self._rewrite(original) is original

    def test_entity_id_carrier_on_a_real_snapshot_is_renumbered(self) -> None:
        # The concrete reachable case, through the public entry point: a
        # frozen ``EntityId`` hanging off each of the four carriers, with a
        # closure marker in the scope segment, the leaf name, and ``extra``.
        marker = _closure("task_group.h", 20, 4)
        rec = _record("R", qualified=f"ns::{marker}::R")
        rec.entity_id = entity_id_for_type((Namespace("ns"), Record(marker)), "R")
        fn = Function(
            name="f",
            mangled=f"_Z1f{marker}",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        fn.entity_id = entity_id_for_function((), "f", mangled_name=fn.mangled)
        snap = AbiSnapshot(
            library="lib.so",
            version="1",
            types=[rec, _record("Other", qualified=_closure("task_group.h", 10, 2))],
            functions=[fn],
        )
        renumber_anonymous_closure_identities(snap)

        renumbered_record_id = snap.types[0].entity_id
        assert renumbered_record_id is not None
        scope_names = [getattr(seg, "name", "") for seg in renumbered_record_id.scope]
        assert any("#2" in name for name in scope_names), scope_names
        assert not any(":20:4" in name for name in scope_names)

        renumbered_fn_id = snap.functions[0].entity_id
        assert renumbered_fn_id is not None
        assert any("#2" in part for part in renumbered_fn_id.extra)
        assert not any(":20:4" in part for part in renumbered_fn_id.extra)
        # ...and the carrier agrees with the flat spelling it was built
        # from, which is the whole point of renumbering it at all.
        assert renumbered_fn_id.extra[1] == snap.functions[0].mangled
