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

"""The compact member evidence must answer exactly as the full snapshot does.

``BundleSignatureEvidence`` stopped holding ``Function``/``Variable``
objects and now holds two booleans per symbol, resolved up front. That is a
memory change only if it is *not* a behaviour change, so the invariant
tested here is the whole point:

    for every symbol, and every snapshot shape, the compact projection and
    the original ``AbiSnapshot`` give the same answer to both questions.

It is asserted per symbol over generated snapshots that deliberately mix
every state the underlying predicates distinguish -- because those
distinctions are exactly what a projection to booleans could flatten:

* export evidence as a real ``Fact`` versus a pre-split snapshot's
  conflated ``Visibility``, under both ``elf_only_mode`` settings (where
  ``ELF_ONLY`` means two different things);
* an uncaptured ``is_variadic`` and an uncaptured ``contract_attributes``
  (``None``, not ``False``/``[]``) -- tri-state fields whose "unknown" must
  keep reading as *insufficient*;
* unresolved type spellings in a return type, a parameter and a variable;
* export-table-only records;
* a name present in both maps, which must resolve as the function on both
  sides (lookup precedence);
* a symbol in neither map, which both sides answer ``False`` for.

The oracle is the real ``AbiSnapshot`` run through the same public
predicates -- not a restatement of their rules here, which would be the
implementation written twice.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.bundle_models import SymbolSignatureStatus
from abicheck.bundle_signature_evidence import (
    _symbol_evidence_sufficient,
    _symbol_was_exported,
    symbol_signature_statuses,
)
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.workflows.bundle_symbol_status import (
    build_bundle_signature_evidence,
)


def _fn(
    name: str,
    *,
    visibility: Visibility = Visibility.PUBLIC,
    return_type: str = "int",
    params: list[Param] | None = None,
    is_variadic: bool | None = False,
    contract_attributes: list[str] | None = None,
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=return_type,
        params=params or [],
        visibility=visibility,
        is_variadic=is_variadic,
        contract_attributes=(
            [] if contract_attributes is None else list(contract_attributes)
        ),
    )


def _fn_unknown_variadic(name: str) -> Function:
    return _fn(name, is_variadic=None)


def _fn_unknown_attrs(name: str) -> Function:
    f = _fn(name)
    f.contract_attributes = None
    return f


def _var(name: str, *, type_: str = "int", visibility=Visibility.PUBLIC) -> Variable:
    return Variable(name=name, mangled=name, type=type_, visibility=visibility)


def _snapshot(
    functions: list[Function],
    variables: list[Variable],
    *,
    elf_only_mode: bool = False,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version="1.0",
        functions=functions,
        variables=variables,
        elf_only_mode=elf_only_mode,
    )


#: Every distinguishable declaration shape, named so a failure says which.
_FUNCTION_SHAPES = {
    "public_plain": lambda n: _fn(n),
    "elf_only": lambda n: _fn(n, visibility=Visibility.ELF_ONLY),
    "hidden": lambda n: _fn(n, visibility=Visibility.HIDDEN),
    "unknown_variadic": _fn_unknown_variadic,
    "unknown_contract_attrs": _fn_unknown_attrs,
    "unresolved_return": lambda n: _fn(n, return_type="__unresolved_type"),
    "unresolved_param": lambda n: _fn(
        n, params=[Param(name="p", type="__unresolved_type")]
    ),
    "resolved_param": lambda n: _fn(n, params=[Param(name="p", type="int")]),
}

_VARIABLE_SHAPES = {
    "public_var": lambda n: _var(n),
    "elf_only_var": lambda n: _var(n, visibility=Visibility.ELF_ONLY),
    "hidden_var": lambda n: _var(n, visibility=Visibility.HIDDEN),
    "unresolved_var": lambda n: _var(n, type_="__unresolved_type"),
}


def _mixed_snapshot(elf_only_mode: bool) -> AbiSnapshot:
    functions = [make(f"fn_{key}") for key, make in _FUNCTION_SHAPES.items()]
    variables = [make(f"var_{key}") for key, make in _VARIABLE_SHAPES.items()]
    # A name carried by both maps: precedence must resolve it as the
    # function on both the compact and the full path.
    functions.append(_fn("both"))
    variables.append(_var("both", type_="__unresolved_type"))
    return _snapshot(functions, variables, elf_only_mode=elf_only_mode)


class TestProjectionEquivalence:
    @pytest.mark.parametrize("elf_only_mode", [False, True])
    def test_every_symbol_answers_identically(self, elf_only_mode: bool) -> None:
        snapshot = _mixed_snapshot(elf_only_mode)
        compact = build_bundle_signature_evidence(snapshot)
        symbols = [
            *snapshot.function_map,
            *snapshot.variable_map,
            "never_declared_at_all",
        ]
        disagreements = []
        for symbol in symbols:
            full = (
                _symbol_was_exported(symbol, snapshot),
                _symbol_evidence_sufficient(symbol, snapshot),
            )
            proj = (
                _symbol_was_exported(symbol, compact),
                _symbol_evidence_sufficient(symbol, compact),
            )
            if full != proj:
                disagreements.append((symbol, full, proj))
        assert not disagreements

    @pytest.mark.parametrize("elf_only_mode", [False, True])
    def test_the_fixture_exercises_both_answers_for_both_questions(
        self, elf_only_mode: bool
    ) -> None:
        """Vacuity guard on the sweep above.

        A fixture whose symbols all answered ``(False, False)`` would make
        the equivalence assertion pass against a projection that returned
        ``False`` unconditionally -- which is precisely the failure mode of
        flattening tri-state evidence into a boolean.
        """
        snapshot = _mixed_snapshot(elf_only_mode)
        statuses = symbol_signature_statuses(snapshot)
        assert {s.exported for s in statuses.values()} == {True, False}
        assert {s.evidence_sufficient for s in statuses.values()} == {True, False}

    def test_elf_only_mode_changes_answers_and_the_projection_follows(self) -> None:
        """The one field whose meaning flips with snapshot provenance.

        ``Visibility.ELF_ONLY`` means "exported, no header corroboration"
        on a headerless dump and "declared but *not* dynamically exported"
        on a header-parsed one. A projection computed without regard to
        ``elf_only_mode`` would answer one of the two wrongly, and the
        equivalence sweep alone cannot show the two differ -- so assert it.
        """
        headerless = symbol_signature_statuses(_mixed_snapshot(True))
        parsed = symbol_signature_statuses(_mixed_snapshot(False))
        assert headerless["fn_elf_only"].exported is True
        assert parsed["fn_elf_only"].exported is False

    def test_a_name_in_both_maps_resolves_as_the_function(self) -> None:
        snapshot = _mixed_snapshot(False)
        compact = build_bundle_signature_evidence(snapshot)
        # The function is well-formed, the variable has an unresolved type;
        # function precedence therefore means "sufficient".
        assert _symbol_evidence_sufficient("both", snapshot) is True
        assert _symbol_evidence_sufficient("both", compact) is True

    def test_an_undeclared_symbol_is_absent_rather_than_recorded_false(self) -> None:
        """Absence and ``(False, False)`` must be the same answer.

        Recording every queried-but-absent symbol would reintroduce
        unbounded growth keyed by whatever the consumer happened to ask
        about.
        """
        snapshot = _mixed_snapshot(False)
        compact = build_bundle_signature_evidence(snapshot)
        assert "never_declared_at_all" not in compact.symbol_status
        assert _symbol_was_exported("never_declared_at_all", compact) is False
        assert _symbol_evidence_sufficient("never_declared_at_all", compact) is False

    @pytest.mark.parametrize(
        ("shape", "elf_only_mode"),
        list(itertools.product(sorted(_FUNCTION_SHAPES), [False, True])),
    )
    def test_each_function_shape_agrees_in_isolation(
        self, shape: str, elf_only_mode: bool
    ) -> None:
        """Per-shape, so a failure names the distinction that was flattened.

        The mixed sweep proves the set agrees; this proves each state does,
        which is what makes a regression legible rather than a single
        failing list.
        """
        snapshot = _snapshot(
            [_FUNCTION_SHAPES[shape]("sym")], [], elf_only_mode=elf_only_mode
        )
        compact = build_bundle_signature_evidence(snapshot)
        assert _symbol_was_exported("sym", compact) == _symbol_was_exported(
            "sym", snapshot
        )
        assert _symbol_evidence_sufficient(
            "sym", compact
        ) == _symbol_evidence_sufficient("sym", snapshot)

    @pytest.mark.parametrize("shape", sorted(_VARIABLE_SHAPES))
    def test_each_variable_shape_agrees_in_isolation(self, shape: str) -> None:
        snapshot = _snapshot([], [_VARIABLE_SHAPES[shape]("sym")])
        compact = build_bundle_signature_evidence(snapshot)
        assert _symbol_was_exported("sym", compact) == _symbol_was_exported(
            "sym", snapshot
        )
        assert _symbol_evidence_sufficient(
            "sym", compact
        ) == _symbol_evidence_sufficient("sym", snapshot)


class TestRetention:
    """The projection must actually release the declarations."""

    @staticmethod
    def _reachable_from(root: object) -> set[int]:
        """Ids reachable from *root* through data structures only.

        Deliberately **not** ``gc.get_referents``: an object's referents
        include its class, and from there the module globals, so an
        unbounded walk reaches every declaration in the process and the
        check becomes a lottery over what the session imported (an earlier
        version of this test passed or failed on exactly that). This
        traverses containers and dataclass fields -- the ways a projection
        could actually pin a declaration -- and stops at classes, modules
        and functions.
        """
        seen: set[int] = set()
        stack = [root]
        while stack:
            obj = stack.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if isinstance(obj, dict):
                stack.extend(obj.keys())
                stack.extend(obj.values())
            elif isinstance(obj, (list, tuple, set, frozenset)):
                stack.extend(obj)
            elif hasattr(obj, "__dict__") and not isinstance(obj, type):
                stack.extend(vars(obj).values())
            slots = getattr(type(obj), "__slots__", ())
            if not isinstance(obj, type):
                for slot in slots:
                    if hasattr(obj, slot):
                        stack.append(getattr(obj, slot))
        return seen

    def test_no_declaration_is_reachable_from_the_projection(self) -> None:
        """The memory claim itself: holding the evidence must not hold the
        declarations.

        Transitive, because the failure this guards against is a
        declaration pinned *indirectly* -- a list of them stashed on the
        evidence object, say, which a one-level referrer check happily
        misses (verified: such a mutation passed the referrer-based version
        of this test and fails this one).
        """
        snapshot = _mixed_snapshot(False)
        declarations = [*snapshot.functions, *snapshot.variables]
        assert declarations, "fixture declares nothing"
        compact = build_bundle_signature_evidence(snapshot)
        reachable = self._reachable_from(compact)
        pinned = [d for d in declarations if id(d) in reachable]
        assert not pinned, f"{len(pinned)} declarations reachable from the evidence"
        # Vacuity guard on the walk: it must reach the evidence's own
        # contents, or "nothing found" would be true of a walk that did
        # nothing at all.
        assert {id(v) for v in compact.symbol_status.values()} <= reachable

    def test_the_status_values_hold_only_plain_booleans(self) -> None:
        """Nothing richer sneaks back in.

        A future "just carry the Function too, it is convenient" change
        would restore the retention this type removed; the value shape is
        the cheapest place to state that it must not.
        """
        snapshot = _mixed_snapshot(False)
        compact = build_bundle_signature_evidence(snapshot)
        for key, value in compact.symbol_status.items():
            assert isinstance(key, str)
            assert isinstance(value, SymbolSignatureStatus)
            assert type(value.exported) is bool
            assert type(value.evidence_sufficient) is bool

    def test_the_status_mapping_covers_exactly_the_declared_symbols(self) -> None:
        snapshot = _mixed_snapshot(False)
        compact = build_bundle_signature_evidence(snapshot)
        assert set(compact.symbol_status) == set(snapshot.function_map) | set(
            snapshot.variable_map
        )

    def test_the_status_type_is_immutable(self) -> None:
        status = SymbolSignatureStatus(exported=True, evidence_sufficient=False)
        with pytest.raises(Exception):
            status.exported = False  # type: ignore[misc]
