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

"""Tests for :mod:`abicheck.model.mangled_name` (ADR-061 D1, split out of
``diff_cxx_rules.py`` so ``extract``'s ``dumper_clang_expr.py``/
``dumper_hybrid.py`` can use ``itanium_scope_components`` without a
forbidden ``extract -> compare`` edge; ``msvc_scope_components`` joined it
here later, closing Phase 2's "fourth, pre-existing tension" so
``buildsource/ctor_export_match.py``/``virtual_dispatch_graph.py`` can use
either decoder the same way).

Full behavioral coverage of the Itanium/MSVC parsing chains already exists
across many call sites (``test_dumper_hybrid.py``, ``test_dumper_clang.py``,
``test_type_reachability_mangling.py``, ``test_kde_compat_detectors.py``,
...) and is not duplicated here -- this file only pins the split itself: the
new canonical import path resolves and behaves, and ``diff_cxx_rules.py``'s
back-compat re-export is the identical function object, not a copy that
could drift.
"""

from __future__ import annotations

from abicheck import diff_cxx_rules
from abicheck.model import mangled_name


def test_itanium_scope_components_basic() -> None:
    assert mangled_name.itanium_scope_components("_ZN1C3barEv") == ["C", "bar"]
    assert mangled_name.itanium_scope_components("_Z4drawi") == ["draw"]
    assert mangled_name.itanium_scope_components("not a mangled name") is None


def test_msvc_scope_components_basic() -> None:
    assert mangled_name.msvc_scope_components("?run@Foo@@QEAAXXZ") == ["Foo", "run"]
    assert mangled_name.msvc_scope_components("?instantiate@@YAXXZ") == ["instantiate"]
    assert mangled_name.msvc_scope_components("not a mangled name") is None


def test_diff_cxx_rules_reexports_the_identical_function_object() -> None:
    assert (
        diff_cxx_rules.itanium_scope_components is mangled_name.itanium_scope_components
    )
    assert (
        diff_cxx_rules.itanium_scope_components_with_template_positions
        is mangled_name.itanium_scope_components_with_template_positions
    )
    assert diff_cxx_rules._itanium_strip_prefix is mangled_name._itanium_strip_prefix
    assert diff_cxx_rules.msvc_scope_components is mangled_name.msvc_scope_components
