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

"""Payload-exclusion coverage for
``qualified_name_segments.renumber_anonymous_closure_identities``, split out
of ``test_lambda_identity_ordinal.py`` (mechanical extraction, unchanged
test bodies) once that file crossed the AI-readiness ``file-size`` gate's
1200-line test cap. Shares its fixtures/helpers with that module rather
than duplicating them -- the same cross-test-file import convention
``test_entity_id_carrier.py``/``test_dumper_hybrid.py`` and siblings
already use.

Two things are pinned here:

1. ``TestQualifiedNameFactIsRenumberedToo`` -- ``RecordType.qualified_name``
   has a ``Fact[str | None]`` sibling that must stay in sync with the
   renumbered legacy field.
2. ``TestPayloadTextIsNeverCorrupted`` -- a free-text/expression field
   (never a type/name spelling) can coincidentally contain a substring
   matching the closure marker syntax; such text must never be collected
   as identity evidence or rewritten, including inside a ``Fact[...]``
   sibling that legitimately wraps a *different*, non-excluded field's
   payload (``qualified_name_fact`` gets rewritten; ``source_header_fact``
   must not, even when it happens to share the identical marker text).
"""

from __future__ import annotations

from dataclasses import replace

from test_lambda_identity_ordinal import _closure, _record

from abicheck.model import AbiSnapshot
from abicheck.model.fact import replace_with_fact_sync
from abicheck.qualified_name_segments import renumber_anonymous_closure_identities


class TestQualifiedNameFactIsRenumberedToo:
    """ADR-063 Phase 5 (Codex review): ``RecordType.qualified_name`` now has
    a ``Fact[str | None]`` sibling. ``_walk_rewrite_strings`` walks it too
    (it's a real, non-excluded field), but ``_PAYLOAD_FIELD_EXCLUSIONS``'s
    ``"value"`` entry -- meant for ``Variable.value``'s own unrelated
    field -- coincidentally also matches ``Fact.value``'s field name, so
    the closure marker inside ``qualified_name_fact.value`` was silently
    left in its raw, unrenumbered ``:line:col`` form even after the legacy
    ``qualified_name`` field itself was correctly rewritten -- two
    conflicting spellings of the same identity persisted together."""

    def test_qualified_name_fact_value_matches_the_renumbered_qualified_name(
        self,
    ) -> None:
        rec = _record(
            f"raii_guard<{_closure('task_group.h', 522, 26)}>",
            qualified=f"ns::raii_guard<{_closure('task_group.h', 522, 26)}>",
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        renumbered = snap.types[0]
        assert "#" in renumbered.qualified_name
        assert ":522:" not in renumbered.qualified_name
        assert renumbered.qualified_name_fact.value == renumbered.qualified_name


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
        # ADR-063 Phase 5: source_header now has a Fact[str | None] sibling
        # -- a raw dataclasses.replace() carries the pre-existing (already-
        # resolved) source_header_fact forward unchanged, and
        # __post_init__'s "explicit Fact wins" bridge rule then discards
        # this call's own source_header update (model/fact.py's own
        # documented dataclasses.replace() trap). replace_with_fact_sync
        # derives the matching Fact.present(...) alongside the legacy
        # value instead, so the two representations can't disagree.
        rec = replace_with_fact_sync(
            _record("Widget", qualified="ns::Widget"),
            source_location=f"{path}:42",
            source_header=path,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        assert snap.types[0].source_location == f"{path}:42"
        assert snap.types[0].source_header == path
        assert snap.types[0].source_header_fact.value == path

    def test_a_source_header_matching_a_real_closures_own_marker_is_untouched(
        self,
    ) -> None:
        """Codex review, fresh evidence: a real closure identity and a
        legal source_header path can coincidentally share the identical
        normalized marker text (e.g. the type embeds
        ``(lambda:x.h:5:1)`` and the path is
        ``/tmp/(lambda:x.h:5:1)/api.h``) -- source_header_fact must stay
        untouched exactly like its own legacy source_header sibling,
        never rewritten just because qualified_name_fact legitimately is."""
        marker = _closure("x.h", 5, 1)
        closure_type = f"raii_guard<{marker}>"
        path = f"/tmp/{marker}/api.h"
        rec = replace_with_fact_sync(
            _record(closure_type, qualified=f"ns::{closure_type}"),
            source_header=path,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        renumbered = snap.types[0]
        # The real closure in qualified_name DOES get renumbered...
        assert "#1)" in renumbered.qualified_name
        assert renumbered.qualified_name_fact.value == renumbered.qualified_name
        # ...but the coincidentally-identical marker text inside
        # source_header (a payload-excluded path) must not be.
        assert renumbered.source_header == path
        assert renumbered.source_header_fact.value == path

    def test_a_source_location_does_not_fabricate_an_ordinal_for_a_real_closure(
        self,
    ) -> None:
        """Same collection-time exclusion check as the deprecated-message/
        constant siblings above, for source_location/source_header."""
        closure_type = f"raii_guard<{_closure('x.h', 5, 1)}>"
        path = f"/tmp/{_closure('x.h', 1, 1)}/api.h"
        rec = replace_with_fact_sync(
            _record(closure_type, qualified=f"ns::{closure_type}"),
            source_location=f"{path}:42",
            source_header=path,
        )
        snap = AbiSnapshot(library="lib.so", version="1.0", types=[rec])
        renumber_anonymous_closure_identities(snap)
        assert "#1)" in snap.types[0].qualified_name
        assert snap.types[0].source_location == f"{path}:42"
        assert snap.types[0].source_header == path
