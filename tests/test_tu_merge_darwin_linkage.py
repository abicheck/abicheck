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

"""``tu_merge``'s Darwin leading-underscore quirk (macOS CI, fresh
evidence), split out of ``tests/test_tu_merge.py`` purely to stay under
that file's ADR-061 no-growth line baseline rather than growing it
further -- see that file's own module docstring for the sibling coverage
of everything else in ``abicheck/tu_merge.py``.

A plain-C or static/extern "C" declaration on a Darwin target never
satisfies ``mangled == name`` (the platform's linker convention prepends a
leading underscore to every global symbol, mangled or not), so
``_function_key``/``_variable_key`` must fall back to each backend's own
Darwin-aware ``is_extern_c`` signal instead. Fake fragments reproduce the
exact shape a real Darwin clang parse reports (``mangled='_helper'``/
``'_state'`` against a bare ``name``, with ``is_extern_c=True``) without
needing a macOS toolchain to generate it -- these are the primitive-level
property tests the module's own docstrings describe, pinned as real,
executable regression tests per this repository's bug-class regression-
test contract (a prior round of this same fix shipped without any of
them).
"""

from __future__ import annotations

from abicheck.model import Function, Variable
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.tu_fragment import TuFragment
from abicheck.tu_merge import merge_fragments


def _fn(name: str, mangled: str | None = None, **overrides: object) -> Function:
    overrides.setdefault("return_type", "void")
    return Function(name=name, mangled=mangled or name, **overrides)  # type: ignore[arg-type]


def _var(name: str, mangled: str | None = None, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int", **overrides)  # type: ignore[arg-type]


def test_merge_fragments_keeps_distinct_darwin_static_functions_from_different_tus():
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
                return_type="int",
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
                return_type="double",
            ),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_still_merges_darwin_external_functions_across_tus():
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
            ),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_keeps_distinct_darwin_static_variables_from_different_tus():
    darwin_id = entity_id_for_variable((), "state", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        variables=(
            _var("state", "_state", is_static=True, entity_id=darwin_id, value="1"),
        ),
    )
    b = TuFragment(
        tu_name="b",
        variables=(
            _var("state", "_state", is_static=True, entity_id=darwin_id, value="2"),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_still_merges_darwin_external_variables_across_tus():
    darwin_id = entity_id_for_variable((), "state", is_extern_c=True)
    a = TuFragment(
        tu_name="a",
        variables=(_var("state", "_state", is_static=False, entity_id=darwin_id),),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("state", "_state", is_static=False, entity_id=darwin_id),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 1


def test_merge_fragments_darwin_static_and_external_same_mangled_name_stay_distinct():
    # The mixed-linkage collision case (Codex review, PR #635, third
    # round -- see `_function_key`'s own docstring): a plain-C function's
    # `EntityId` construction does not encode static-vs-external linkage
    # at all, so an externally-linked and an unrelated TU-local `static`
    # declaration sharing the identical Darwin-decorated mangled spelling
    # must still land in two distinct buckets, not collapse or raise
    # INCONSISTENT_DECLARATION between them.
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
            ),
        ),
    )
    c = TuFragment(
        tu_name="c",
        functions=(
            _fn(
                "helper",
                "_helper",
                is_static=True,
                is_extern_c=True,
                entity_id=darwin_id,
            ),
        ),
    )
    merged = merge_fragments([a, b, c])
    assert len(merged.functions) == 2


# ---------------------------------------------------------------------------
# Darwin leading-underscore quirk, second instance (Codex review, fresh
# evidence): a *genuinely* Itanium-mangled Darwin symbol (real C++ name
# mangling applied, not the plain-C/extern-"C" case above) also carries one
# extra platform leading underscore -- "__ZL6helperi", not the plain
# Itanium "_ZL6helperi" -- which `_has_local_linkage_mangling`'s own
# `mangled.startswith("_Z")`-gated marker check rejected outright before
# this fix, independently of the is_extern_c branch above.
# ---------------------------------------------------------------------------


def test_merge_fragments_keeps_distinct_darwin_cxx_static_functions_from_different_tus():
    a = TuFragment(
        tu_name="a",
        functions=(_fn("helper", "__ZL6helperi", is_static=True, return_type="int"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("helper", "__ZL6helperi", is_static=True, return_type="double"),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_keeps_distinct_darwin_nested_namespace_static_functions():
    # The nested-namespace-`L` shape ("static int state;" inside
    # "namespace ns") mangles to "_ZN2nsL5stateE", not a leading "_ZL"
    # prefix -- see `_has_local_linkage_mangling`'s own docstring. Darwin
    # decorates this identically to the global-scope case: one extra
    # leading underscore over the plain Itanium spelling.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn("nested", "__ZN2nsL6nestedEi", is_static=True, return_type="int"),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn("nested", "__ZN2nsL6nestedEi", is_static=True, return_type="double"),
        ),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 2
    assert {fn.return_type for fn in merged.functions} == {"int", "double"}


def test_merge_fragments_still_merges_darwin_cxx_static_member_functions_across_tus():
    # A static *member* function has the class's own ordinary external
    # linkage -- its Darwin-decorated mangled name carries neither the
    # `_ZL` nor `_GLOBAL__N_` marker, so it must keep merging normally
    # across TUs (the identical Linux-mangled-name case is pinned in
    # test_tu_merge.py's own
    # test_merge_fragments_still_merges_static_member_functions_across_tus).
    a = TuFragment(
        tu_name="a",
        functions=(_fn("make", "__ZN6Widget4makeEi", is_static=True),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("make", "__ZN6Widget4makeEi", is_static=True),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_keeps_distinct_darwin_cxx_static_variables_from_different_tus():
    a = TuFragment(tu_name="a", variables=(_var("state", "__ZL5statei", value="1"),))
    b = TuFragment(tu_name="b", variables=(_var("state", "__ZL5statei", value="2"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_still_merges_darwin_cxx_static_member_variables_across_tus():
    a = TuFragment(tu_name="a", variables=(_var("counter", "__ZN6Widget7counterE"),))
    b = TuFragment(tu_name="b", variables=(_var("counter", "__ZN6Widget7counterE"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 1
