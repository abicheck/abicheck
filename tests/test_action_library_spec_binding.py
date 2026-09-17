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

"""``bind_declaration``: filling a component spec's build-decided values.

**Bug class:** ``config.textual_substitution_into_a_structured_document``. A
checked-in component declaration cannot spell an install prefix or a host-arch
directory component, so something has to fill them in -- and the obvious
something, a ``sed``-style pass over the serialized JSON, is wrong for a whole
family of values at once rather than for one unlucky one. ``&`` means "the
whole match" in a replacement, a quote closes a JSON string, a backslash is
eaten, and the delimiter character fails outright. None of those is rare in a
filesystem path, and none is the caller's to constrain.

This is a reusable substitution primitive, so it gets the treatment root
``AGENTS.md`` prescribes for one: a standalone property-test class stating the
contract as invariants, decoupled from any caller's domain. The invariants:

* **Structural fidelity.** For *any* binding value, the result is the value
  verbatim in the right slot and valid JSON. Asserted over a generated set of
  hostile values rather than one, because the sed failure mode is a family.
* **One pass.** A bound value containing ``${...}`` is never re-expanded, so
  there is no fixed point to reason about and no recursion to bound.
* **Closed allowlist.** An unbound placeholder is refused -- never left as
  text, never filled from the environment.
* **Order independence.** The result does not depend on which binding was
  declared first, nor on the order the document's keys happen to be in.

The oracle is deliberately independent of the implementation: expected
results are built by constructing the *already-substituted* document
directly, not by calling the substituter a second time or re-deriving it with
the same regex.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from typing import Any

import pytest

from abicheck.frontends.action.library_selection import (
    SelectionError,
    bind_declaration,
)

#: Values chosen because each breaks a *different* textual substitution. They
#: are ordinary filesystem/arch strings, not exotica: `&`, quotes, backslashes
#: and pipes all occur in real install prefixes and in Windows paths.
HOSTILE_VALUES = (
    "linux-x86_64",
    "/opt/epics & co",
    '/opt/"quoted"/base',
    "C:\\epics\\base",
    "/opt/a|b",
    "/opt/with space/base",
    "/opt/100%/base",
    "/opt/$HOME/base",
    "/opt/`id`/base",
    "/opt/newline-free-but-$(weird)",
    "",
    "/opt/../base",
)


class TestSubstitutionIsStructuralNotTextual:
    """Any value lands verbatim, in the right slot, in valid JSON.

    The sweep is over values rather than over one example because textual
    substitution does not fail for one unlucky string -- it fails for a
    family, differently in each member, and a single hand-picked case
    documents whichever member its author happened to think of.
    """

    @pytest.mark.parametrize("value", HOSTILE_VALUES)
    def test_a_value_reaches_its_slot_unchanged(self, value: str) -> None:
        spec = [
            {
                "name": "libfoo",
                "artifact": "lib/${ARCH}/libfoo.so*",
                "include": ["include", "${PREFIX}/include"],
            }
        ]
        bound = bind_declaration(spec, {"ARCH": "a1", "PREFIX": value})
        # The oracle: the already-substituted document, built directly.
        assert bound == [
            {
                "name": "libfoo",
                "artifact": "lib/a1/libfoo.so*",
                "include": ["include", f"{value}/include"],
            }
        ]

    @pytest.mark.parametrize("value", HOSTILE_VALUES)
    def test_the_result_is_still_serializable_json(self, value: str) -> None:
        bound = bind_declaration(["${PREFIX}"], {"PREFIX": value})
        assert json.loads(json.dumps(bound)) == bound

    @pytest.mark.parametrize("value", HOSTILE_VALUES)
    def test_a_value_is_not_interpreted_as_a_pattern_or_a_shell_word(
        self, value: str
    ) -> None:
        # `&`, `\1`, backticks and `$(...)` all mean something to *some*
        # substitution machinery. Here they mean themselves.
        assert bind_declaration(["${X}"], {"X": value}) == [value]

    def test_several_placeholders_in_one_string(self) -> None:
        assert bind_declaration(["${A}/${B}/${A}"], {"A": "x", "B": "y"}) == ["x/y/x"]

    def test_substitution_reaches_keys_as_well_as_values(self) -> None:
        assert bind_declaration({"${K}": "${V}"}, {"K": "k", "V": "v"}) == {"k": "v"}

    def test_non_string_leaves_are_untouched(self) -> None:
        spec = {"a": 1, "b": True, "c": None, "d": 1.5, "e": []}
        assert bind_declaration(spec, {"X": "y"}) == spec


class TestOnePassNoFixedPoint:
    """A bound value is data, not more template.

    Without this, a binding whose value contains ``${...}`` expands again --
    and the substituter needs a recursion bound, a cycle check, and an
    answer for what a partially-expanded document means. Inserting literally
    means none of those questions exist.
    """

    def test_a_value_containing_a_placeholder_is_not_re_expanded(self) -> None:
        assert bind_declaration(["${A}"], {"A": "${B}", "B": "boom"}) == ["${B}"]

    def test_a_value_that_names_itself_terminates(self) -> None:
        assert bind_declaration(["${A}"], {"A": "${A}"}) == ["${A}"]

    def test_a_two_step_cycle_terminates(self) -> None:
        assert bind_declaration(["${A}"], {"A": "${B}", "B": "${A}"}) == ["${B}"]


class TestTheAllowlistIsClosed:
    def test_an_unbound_placeholder_is_refused(self) -> None:
        with pytest.raises(SelectionError) as excinfo:
            bind_declaration(["${NOPE}"], {"A": "x"})
        assert "NOPE" in str(excinfo.value)

    def test_an_unbound_placeholder_is_not_left_as_text(self) -> None:
        # Leaving it produces a path with a brace in it that fails much
        # later as "no such file", naming a file nobody wrote.
        with pytest.raises(SelectionError):
            bind_declaration(["lib/${ARCH}/libfoo.so"], {})

    def test_the_environment_is_never_consulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARCH", "from-the-environment")
        with pytest.raises(SelectionError):
            bind_declaration(["${ARCH}"], {})
        assert os.environ["ARCH"] == "from-the-environment"

    def test_an_unused_binding_is_harmless(self) -> None:
        # The allowlist is what MAY be named, not what must be.
        assert bind_declaration(["literal"], {"A": "x"}) == ["literal"]

    @pytest.mark.parametrize("name", ["X y", "1X", "", "X-Y", "X.Y", "${X}"])
    def test_a_binding_name_that_could_never_be_named_is_refused(
        self, name: str
    ) -> None:
        with pytest.raises(SelectionError):
            bind_declaration(["literal"], {name: "v"})

    @pytest.mark.parametrize("value", [1, None, True, [], {}])
    def test_a_non_string_binding_value_is_refused(self, value: object) -> None:
        with pytest.raises(SelectionError):
            bind_declaration(["${A}"], {"A": value})  # type: ignore[dict-item]


class TestOnlyTheBracedFormIsAPlaceholder:
    """Everything a shell would expand that this deliberately does not.

    A placeholder syntax that grows an operator is a language the caller now
    has to reason about -- and each operator is a new way for a value to
    stop being a value.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "$ARCH",
            "$(ARCH)",
            "${ARCH:-default}",
            "${ARCH%%/*}",
            "$$ARCH",
            "%ARCH%",
            "{ARCH}",
            "${ ARCH }",
            "${9ARCH}",
        ],
    )
    def test_it_is_left_alone_and_does_not_refuse(self, text: str) -> None:
        # Left alone, not refused: these are not this function's syntax, so
        # they are ordinary characters in a path, and a path is allowed to
        # contain them.
        assert bind_declaration([text], {"ARCH": "x"}) == [text]

    def test_the_braced_form_itself_is_recognised(self) -> None:
        # Vacuity guard for the row above: if nothing were recognised, every
        # case there would pass while the function did nothing at all.
        assert bind_declaration(["${ARCH}"], {"ARCH": "x"}) == ["x"]


class TestOrderIndependence:
    """The result cannot depend on declaration or iteration order.

    A substituter that applied bindings sequentially over the whole document
    -- replace every ${A}, then every ${B} -- gives a different answer
    depending on which came first, as soon as one value contains another's
    placeholder. Walking once and resolving each placeholder in place is
    what makes the question meaningless, so it is asserted directly.
    """

    BINDINGS = {"A": "${B}", "B": "beta", "C": "gamma"}

    @pytest.mark.parametrize("order", list(itertools.permutations("ABC")))
    def test_declaration_order_does_not_matter(self, order: tuple[str, ...]) -> None:
        bindings = {name: self.BINDINGS[name] for name in order}
        assert bind_declaration(["${A}/${B}/${C}"], bindings) == ["${B}/beta/gamma"]

    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_document_key_order_does_not_matter(self, order: tuple[int, ...]) -> None:
        pairs = [("one", "${B}"), ("two", "${C}"), ("three", "literal")]
        document = {pairs[i][0]: pairs[i][1] for i in order}
        assert bind_declaration(document, self.BINDINGS) == {
            "one": "beta",
            "two": "gamma",
            "three": "literal",
        }


class TestItIsWiredIntoTheResolver:
    """Binding runs before parsing, so fields validate the real value."""

    def test_the_resolver_accepts_bindings(self, tmp_path: Any) -> None:
        from abicheck.frontends.action.library_selection import resolve_library_set

        # No artifact exists, so this must fail on *resolution* -- but the
        # message has to name the SUBSTITUTED pattern, which is what proves
        # binding happened before parsing rather than after.
        spec = [{"name": "libfoo", "artifact": "lib/${ARCH}/libfoo.so*"}]
        with pytest.raises(SelectionError) as excinfo:
            resolve_library_set(spec, root=tmp_path, bindings={"ARCH": "linux-x86_64"})
        assert "linux-x86_64" in str(excinfo.value)
        assert "${ARCH}" not in str(excinfo.value)

    def test_without_bindings_an_unbound_spec_is_unchanged(self, tmp_path: Any) -> None:
        # Back-compat: a declaration with no placeholders, and no bindings
        # given, must behave exactly as before -- the brace-free path never
        # enters the binder at all.
        from abicheck.frontends.action.library_selection import resolve_library_set

        with pytest.raises(SelectionError) as excinfo:
            resolve_library_set(
                [{"name": "libfoo", "artifact": "lib/libfoo.so*"}], root=tmp_path
            )
        assert "lib/libfoo.so*" in str(excinfo.value)


def test_the_placeholder_pattern_matches_what_the_docstring_says() -> None:
    """Guard on the pattern itself, stated independently of the module's copy."""
    from abicheck.frontends.action.library_selection import _PLACEHOLDER

    independent = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for sample in ("${A}", "x${LONG_name9}y", "$A", "${9}", "${a-b}", "${}"):
        assert bool(_PLACEHOLDER.search(sample)) == bool(independent.search(sample))
        assert _PLACEHOLDER.findall(sample) == independent.findall(sample)
