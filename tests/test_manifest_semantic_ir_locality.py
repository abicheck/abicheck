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

"""``extract.manifest_semantic_ir``'s ``_is_locally_linked_function``/
``_is_locally_linked_variable`` locality classifiers, exercised directly
against fake :class:`~abicheck.tu_fragment.TuFragment` entities -- no real
clang backend needed, the same fast, pure-Python pattern
``tests/test_tu_merge.py`` uses for the identical ``tu_merge.merge_
fragments`` locality logic these two classifiers deliberately mirror (see
this module's own docstring on why: ``extract/`` may not import the
root-level ``tu_merge`` module, ADR-061).

Darwin leading-underscore quirk coverage (macOS CI, fresh evidence): a
plain-C or static/extern "C" declaration on a Darwin target never
satisfies ``mangled == name`` (the platform's linker convention prepends a
leading underscore to every global symbol, mangled or not), so both
classifiers fall back to each backend's own Darwin-aware ``is_extern_c``
signal instead. These tests reproduce the exact shape a real Darwin clang
parse reports (``mangled='_helper'``/``'_state'`` against a bare ``name``,
with ``is_extern_c=True``) and assert the resulting persisted
``SemanticIR`` occurrence counts agree with ``tu_merge.merge_fragments``'s
own flat ``functions``/``variables`` counts for the identical fragments --
closing the gap a prior round of this fix left open (fixing only the flat
merge and leaving these two classifiers, and therefore the persisted
``SemanticIR``, still silently re-collapsing what the flat merge now
correctly keeps distinct).
"""

from __future__ import annotations

from abicheck.extract.manifest_semantic_ir import manifest_semantic_ir
from abicheck.model import Function, Variable
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.tu_fragment import TuFragment


def _fn(name: str, mangled: str, **overrides: object) -> Function:
    overrides.setdefault("return_type", "int")
    return Function(name=name, mangled=mangled, **overrides)  # type: ignore[arg-type]


def _var(name: str, mangled: str, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled, type="int", **overrides)  # type: ignore[arg-type]


def test_darwin_static_functions_from_different_tus_stay_distinct_occurrences():
    darwin_id = entity_id_for_function((), "helper", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "helper",
                "_helper",
                is_static=True,
                is_extern_c=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "helper",
                "_helper",
                is_static=True,
                is_extern_c=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(darwin_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert all(d.startswith("a:") or d.startswith("b:") for d in disambiguators)


def test_darwin_external_functions_from_different_tus_collapse_to_one_occurrence():
    darwin_id = entity_id_for_function((), "helper", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "helper",
                "_helper",
                is_static=False,
                is_extern_c=True,
                entity_id=darwin_id,
                source_location="shared.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "helper",
                "_helper",
                is_static=False,
                is_extern_c=True,
                entity_id=darwin_id,
                source_location="shared.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(darwin_id)
    assert len(occurrences) == 1


def test_darwin_static_variables_from_different_tus_stay_distinct_occurrences():
    darwin_id = entity_id_for_variable((), "state", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        variables=(
            _var(
                "state",
                "_state",
                is_static=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        variables=(
            _var(
                "state",
                "_state",
                is_static=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(darwin_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert all(d.startswith("a:") or d.startswith("b:") for d in disambiguators)


def test_darwin_external_variables_from_different_tus_collapse_to_one_occurrence():
    darwin_id = entity_id_for_variable((), "state", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        variables=(
            _var(
                "state",
                "_state",
                is_static=False,
                entity_id=darwin_id,
                source_location="shared.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        variables=(
            _var(
                "state",
                "_state",
                is_static=False,
                entity_id=darwin_id,
                source_location="shared.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(darwin_id)
    assert len(occurrences) == 1


def test_darwin_cxx_mangled_static_functions_from_different_tus_stay_distinct_occurrences():
    # Second Darwin quirk instance (Codex review, fresh evidence): a
    # *genuinely* Itanium-mangled Darwin symbol also carries an extra
    # leading underscore ("__ZL6helperi", not the plain Itanium
    # "_ZL6helperi") -- independent of the is_extern_c case above, and
    # unfixed by that first round's own fix.
    darwin_id = entity_id_for_function((), "helper", mangled_name="__ZL6helperi")
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "helper",
                "__ZL6helperi",
                is_static=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "helper",
                "__ZL6helperi",
                is_static=True,
                entity_id=darwin_id,
                source_location="a.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(darwin_id)
    assert len(occurrences) == 2
    disambiguators = {occ.disambiguator for occ in occurrences}
    assert len(disambiguators) == 2
    assert all(d.startswith("a:") or d.startswith("b:") for d in disambiguators)


def test_darwin_cxx_mangled_static_member_functions_from_different_tus_collapse_to_one_occurrence():
    # A static *member* function's Darwin-decorated mangled name carries
    # neither the `_ZL` nor `_GLOBAL__N_` marker, so it must still
    # collapse to one occurrence across TUs -- not TU-scoped just because
    # it also happens to have the extra leading underscore.
    member_id = entity_id_for_function((), "make", mangled_name="__ZN6Widget4makeEi")
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "make",
                "__ZN6Widget4makeEi",
                is_static=True,
                entity_id=member_id,
                source_location="widget.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "make",
                "__ZN6Widget4makeEi",
                is_static=True,
                entity_id=member_id,
                source_location="widget.h:1",
            ),
        ),
    )
    ir = manifest_semantic_ir([a, b])
    occurrences = ir.occurrences_for(member_id)
    assert len(occurrences) == 1
