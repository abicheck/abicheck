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

"""A ``str``-subclass ``Enum`` field reachable from
``qualified_name_segments._walk_rewrite_strings`` must survive the walk as
the same enum member, not a demoted plain ``str``.

Split out of ``test_lambda_identity_ordinal.py`` (which sits at its own
AI-readiness test-file line-count cap) rather than appended there -- same
``_walk_rewrite_strings``/closure-marker fixture family, but a distinct
class of field (a closed-vocabulary ``str, Enum``, not a closure-marker
string) and its own bug class,
``serialization.str_enum_downcast_via_generic_rewrite``
(``tests/regressions/manifest.py``).

Independently reported against real oneDAL corpora: a stored snapshot
containing so much as one lambda/anonymous-type marker anywhere in
``functions``/``variables``/``types``/``enums`` ran every reachable string --
``Visibility``/``AccessLevel``/``ParamKind`` members included -- through
``rewrite()``. ``rewrite()`` (``strip_anonymous_type_location`` on load,
``apply_anonymous_type_ordinals`` on renumber) returns a *new* plain ``str``
object even when the text is unchanged, since neither helper special-cases
its input's exact type. The walk's own object-identity check (``new is
old``) then always saw a change for an enum member (identical text,
different type) and ``setattr``'d the plain string back onto the dataclass
field, permanently demoting e.g. ``Function.visibility`` from
``Visibility.HIDDEN`` to the bare string ``"hidden"``. Every later
``.visibility.value`` access -- e.g. ``diff_symbols._check_removed_function``
-- then raised ``AttributeError: 'str' object has no attribute 'value'`` on
ANY ``compare()`` involving that snapshot, whether or not the finding being
produced had anything to do with the marker that triggered the walk in the
first place. Same root cause as this repository's ``ParamKind`` case
(``tests/test_param_kind_enum_identity.py``), through a different reachable
field.

The fix is stated as a primitive-level property (any ``str`` subclass that
isn't exactly ``str`` passes through unrewritten) rather than a field-name
exclusion, so it covers every closed-vocabulary enum the model carries today
(``Visibility``, ``AccessLevel``, ``ParamKind``, ``ScopeOrigin``,
``ElfVisibility``) and whatever str-Enum field is added next -- not just the
one (``Visibility``) the incident happened to hit.
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.vocabulary import AccessLevel, ParamKind
from abicheck.name_classification import strip_anonymous_type_location
from abicheck.qualified_name_segments import (
    _walk_rewrite_strings,
    apply_anonymous_type_ordinals,
    collect_anonymous_type_ordinals,
    renumber_anonymous_closure_identities,
)
from abicheck.serialization import snapshot_from_dict


def _closure(header: str, line: int, col: int) -> str:
    return strip_anonymous_type_location(f"(lambda at /src/x/{header}:{line}:{col})")


def _walk_iterable_values(value: object):
    """Yield every leaf value from a tuple/list/single-entry dict wrapper,
    for asserting on the one payload item a parametrized wrap() produced
    without hard-coding which wrapper shape it used."""
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_iterable_values(item)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _walk_iterable_values(v)
    else:
        yield value


class TestStrSubclassEnumsSurviveTheWalkUnrewritten:
    """A ``str, Enum`` closed-vocabulary field (``Function.visibility`` etc.)
    reachable from :data:`_LAMBDA_IDENTITY_FIELDS` must come out of
    :func:`_walk_rewrite_strings` as the SAME enum member it went in as --
    not a plain ``str`` that happens to compare equal. See this module's own
    docstring for the reported failure mode.
    """

    @staticmethod
    def _ordinals() -> dict[tuple[str, str, int, int], int]:
        return collect_anonymous_type_ordinals([_closure("h.h", 10, 2)])

    def _renumber_rewrite(self, value: object) -> object:
        ordinals = self._ordinals()
        return _walk_rewrite_strings(
            value, lambda text: apply_anonymous_type_ordinals(text, ordinals)
        )

    @staticmethod
    def _new_object_rewrite(text: str) -> str:
        """A content-preserving rewrite that always constructs a brand-new
        ``str`` -- what ``strip_anonymous_type_location`` (the real
        load-time ``rewrite`` in the reported crash) actually does, since it
        builds its result via slicing/concatenation rather than returning
        its input unchanged when nothing needed stripping.
        ``apply_anonymous_type_ordinals`` is NOT used here on purpose: its
        own ``"(" not in name`` fast path happens to hand back the exact
        input object for marker-free text (e.g. plain ``"hidden"``), which
        would let a primitive-level test built on it pass even against the
        unfixed walk -- masking exactly the bug this class exists to pin.
        """
        return "".join(text)

    @pytest.mark.parametrize(
        "member",
        [
            pytest.param(Visibility.HIDDEN, id="Visibility"),
            pytest.param(AccessLevel.PROTECTED, id="AccessLevel"),
            pytest.param(ParamKind.RVALUE_REF, id="ParamKind"),
        ],
    )
    def test_bare_enum_member_is_returned_as_the_same_object(
        self, member: object
    ) -> None:
        result = _walk_rewrite_strings(member, self._new_object_rewrite)
        assert result is member
        assert type(result) is type(member)

    def test_a_plain_str_sibling_is_still_rewritten(self) -> None:
        # Proof the fix isn't "the walk skips everything str-shaped" --
        # only a str SUBCLASS is left alone; an exact ``str`` still goes
        # through ``rewrite`` and comes back as its (new) result.
        result = _walk_rewrite_strings("hidden", self._new_object_rewrite)
        assert result == "hidden"
        assert type(result) is str

    @pytest.mark.parametrize(
        "wrap",
        [
            pytest.param(lambda m: (m,), id="in-a-tuple"),
            pytest.param(lambda m: [m], id="in-a-list"),
            pytest.param(lambda m: {"k": m}, id="as-a-dict-value"),
        ],
    )
    def test_enum_member_survives_alongside_a_real_rewrite(self, wrap: object) -> None:
        # A plain-str sibling in the same container must still come back
        # rewritten -- this isn't "the walk skips everything", only the
        # enum member specifically.
        container = wrap((Visibility.HIDDEN, "plain"))  # type: ignore[operator]
        result = _walk_rewrite_strings(container, self._new_object_rewrite)
        leaves = list(_walk_iterable_values(result))
        assert leaves[0] is Visibility.HIDDEN
        assert leaves[1] == "plain"
        assert type(leaves[1]) is str

    def test_function_visibility_survives_renumbering_a_sibling_closure(self) -> None:
        # The concrete reported shape through the real production entry
        # point: one function's mangled name carries a lambda marker
        # (triggering the walk over the whole `functions` container), a
        # DIFFERENT function's `visibility` must still come out as a real
        # `Visibility` enum, not a demoted `str`.
        marked = Function(
            name="f",
            mangled=f"_Z1f{_closure('h.h', 10, 2)}",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        hidden = Function(
            name="g", mangled="_Z1gv", return_type="void", visibility=Visibility.HIDDEN
        )
        snap = AbiSnapshot(library="lib.so", version="1", functions=[marked, hidden])
        renumber_anonymous_closure_identities(snap)

        assert "#1" in snap.functions[0].mangled
        assert isinstance(snap.functions[1].visibility, Visibility)
        assert snap.functions[1].visibility is Visibility.HIDDEN
        # The actual crash site: `.value` must resolve.
        assert snap.functions[1].visibility.value == "hidden"

    def test_loading_a_legacy_snapshot_preserves_visibility_enum_identity(self) -> None:
        # End-to-end through the real load path: a legacy (raw-marker)
        # persisted snapshot triggers `normalize_anonymous_type_spellings_
        # on_load`'s walk over the whole `functions` container on load,
        # same as the reported bundle-facts crash.
        owner = f"raii_guard<{_closure('task_group.h', 522, 26)}>"
        legacy = {
            "library": "libtbb.so",
            "version": "1.0",
            "schema_version": 25,
            "types": [{"name": owner, "qualified_name": owner, "kind": "class"}],
            "functions": [
                {
                    "name": "f",
                    "mangled": f"_Z1f{owner}",
                    "return_type": "void",
                    "visibility": "hidden",
                },
                {
                    "name": "g",
                    "mangled": "_Z1gv",
                    "return_type": "void",
                    "visibility": "hidden",
                },
            ],
        }
        loaded = snapshot_from_dict(legacy)
        assert "#" in loaded.types[0].qualified_name
        for fn in loaded.functions:
            assert isinstance(fn.visibility, Visibility)
            assert fn.visibility is Visibility.HIDDEN
            assert fn.visibility.value == "hidden"
