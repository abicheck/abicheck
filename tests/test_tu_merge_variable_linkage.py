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
"""``tu_merge``/``manifest_semantic_ir`` plain-C ``static``-vs-``extern``
variable-linkage tests, split out of ``tests/test_tu_merge.py`` to keep
that file under the AI-readiness hard line-count cap (PR #1024 review,
CodeRabbit).

Unlike a C++ ``static`` variable (which mangles with a ``_ZL``/
``_GLOBAL__N_`` marker ``_has_local_linkage_mangling`` can read), a
plain-C (or ``extern "C"``) file-scope ``static`` variable's mangled
spelling equals its bare name -- carrying no marker at all. Before
``Variable.is_static`` existed, ``tu_merge._variable_key`` and
``extract.manifest_semantic_ir._is_locally_linked_variable`` had no
signal to fall back on for this case (unlike ``Function``, which already
had ``is_static`` and its own ``fn.mangled == fn.name -> fn.is_static``
fallback branch), so a same-named plain-C ``static`` and ``extern``
variable across translation units silently collided: `merge_fragments`
folded them into one ``Variable``, discarding one declaration outright,
and `manifest_semantic_ir` collapsed them onto one occurrence. Confirmed
empirically against the real clang backend before this fix (see the
real-backend test at the bottom of this file).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.model import Variable
from abicheck.tu_fragment import TuFragment
from abicheck.tu_merge import merge_fragments


def _var(name: str, mangled: str | None = None, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int", **overrides)  # type: ignore[arg-type]


def test_merge_fragments_keeps_distinct_plain_c_static_variables_from_different_tus():
    # PR #1024 review (CodeRabbit), fresh evidence: this must fall through
    # to `Variable.is_static`, mirroring `Function`'s own plain-C fallback
    # branch (`fn.mangled == fn.name` -> `fn.is_static`) exactly.
    a = TuFragment(
        tu_name="a", variables=(_var("counter", "counter", is_static=True, value="1"),)
    )
    b = TuFragment(
        tu_name="b", variables=(_var("counter", "counter", is_static=True, value="2"),)
    )
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    assert {v.value for v in merged.variables} == {"1", "2"}


def test_merge_fragments_still_merges_plain_c_extern_variables_from_different_tus():
    # Negative control: an ordinary plain-C `extern`-linkage variable
    # (`is_static=False`, mangled == name) must still merge normally --
    # `is_static` must not be misread as "always TU-local".
    a = TuFragment(tu_name="a", variables=(_var("g_count", "g_count", value=None),))
    b = TuFragment(tu_name="b", variables=(_var("g_count", "g_count", value="7"),))
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 1
    assert merged.variables[0].value == "7"


def test_merge_fragments_keeps_plain_c_static_and_extern_of_the_same_name_distinct():
    # The exact collision reported by CodeRabbit review, PR #1024: a
    # plain-C file-scope `static int counter;` in one TU and an unrelated
    # `extern int counter;` in another TU share the identical bare-name
    # mangled spelling and, before `Variable.is_static` existed, had no
    # signal distinguishing them -- `merge_fragments` silently folded them
    # into ONE `Variable`, discarding one declaration outright (confirmed
    # empirically against the real clang backend before this fix).
    a = TuFragment(
        tu_name="a",
        variables=(_var("counter", "counter", is_static=True, value="1"),),
    )
    b = TuFragment(
        tu_name="b",
        variables=(_var("counter", "counter", is_static=False, value=None),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.variables) == 2
    is_static_flags = {v.is_static for v in merged.variables}
    assert is_static_flags == {True, False}


def test_plain_c_static_and_extern_variables_of_the_same_name_stay_distinct_real_clang(
    tmp_path: Path,
) -> None:
    """Real clang, C mode (``-x c``) end-to-end regression for the
    CodeRabbit review finding on PR #1024: a plain-C file-scope
    ``static`` variable has no Itanium mangling marker to distinguish it
    from an unrelated ``extern``-linkage variable of the same name (its
    mangled spelling is simply its bare name, identical to the extern
    one's) -- before ``Variable.is_static`` existed, ``_variable_key``
    had no signal to read and this collision silently discarded one of
    the two declarations during ``merge_fragments``. Two TUs, each
    ``#include``-ing its own header (one declaring the ``static``
    variable, the other the unrelated ``extern`` variable of the same
    name) must produce two distinct ``Variable`` entries, not one."""
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    static_h = tmp_path / "static_counter.h"
    static_h.write_text("static int counter = 1;\n")
    extern_h = tmp_path / "extern_counter.h"
    extern_h.write_text("int counter;\n")

    tu_static = TranslationUnit(name="tu_static", forced_includes=(static_h,))
    tu_extern = TranslationUnit(name="tu_extern", forced_includes=(extern_h,))
    merged = run_tu_loop(
        (tu_static, tu_extern),
        header_ast_parser=_header_ast_parser,
        roots=[static_h, extern_h],
        backend="clang",
        compiler="cc",
        lang="c",
        exported_dynamic={"counter"},
        exported_static=set(),
    )
    counters = [v for v in merged.variables if v.name == "counter"]
    assert len(counters) == 2, (
        "the static and extern 'counter' are two distinct declarations "
        "and must not collapse into one merely because a plain-C mangled "
        "name carries no linkage marker"
    )
    assert {v.is_static for v in counters} == {True, False}
    # The two remain merged onto one EntityId (a separate, pre-existing,
    # documented identity-construction limitation -- a plain-C variable's
    # EntityId carries no static-vs-external distinction at all, see this
    # module's docstring precedent for the identical function-level fact)
    # but must still surface as two distinct occurrences in the IR.
    entity_ids = {v.entity_id for v in counters}
    assert None not in entity_ids
    assert len(entity_ids) == 1
    (entity_id,) = entity_ids
    assert merged.semantic_ir is not None
    occurrences = merged.semantic_ir.occurrences_for(entity_id)
    assert len(occurrences) == 2
