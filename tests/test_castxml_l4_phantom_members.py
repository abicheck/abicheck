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

"""``Function.is_compiler_generated`` — closes the castxml L4 extractor bug
documented in ``AGENTS.md``'s "PR C" known-gaps entry. Split out of
``test_source_extractors.py`` to keep that module under the AI-readiness
file-size hard cap; see ``test_castxml_compiler_generated.py`` (the castxml
parser level, hand-built XML), ``test_dumper_clang_compiler_generated.py``
(the direct-clang parser level), and
``test_serialization_function_compiler_generated.py``'s
``TestFunctionIsCompilerGeneratedRoundTrip`` for this same fix's other test
coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors.base import entity_from_function
from abicheck.model import Function, ScopeOrigin


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++20",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


def test_entity_from_function_excludes_confirmed_compiler_generated() -> None:
    # A confirmed compiler-generated declaration (is_compiler_generated is
    # True) must never be api_relevant, regardless of origin/access -- see
    # this module's own docstring for the false-positive it prevents.
    common = dict(
        name="Widget",
        mangled="_ZN6WidgetaSERKS_",
        return_type="Widget&",
        source_header="include/widget.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    synthesized = entity_from_function(Function(is_compiler_generated=True, **common))
    assert synthesized.api_relevant is False

    # The positive control: an identical, genuinely user-written declaration
    # (is_compiler_generated False) is unaffected.
    user_written = entity_from_function(
        Function(is_compiler_generated=False, **common)
    )
    assert user_written.api_relevant is True

    # An older snapshot / DWARF-only path that never captured the fact
    # (is_compiler_generated is None -- "not captured", not "confirmed
    # user-written") must not be excluded either -- only a confirmed True
    # excludes, matching every other tri-state confirmed-only exclusion in
    # this codebase.
    unknown = entity_from_function(Function(is_compiler_generated=None, **common))
    assert unknown.api_relevant is True


@pytest.mark.integration
def test_castxml_l4_extract_excludes_implicit_special_members_from_reachable_surface(
    tmp_path: Path,
) -> None:
    """Real castxml, real g++, the exact repro from AGENTS.md's "PR C"
    known-gaps entry: ``struct Widget { int x; int y; int sum() const; };``
    compiled and dumped through the actual ``CastxmlSourceExtractor.extract``
    -> ``link_source_abi`` pipeline (not a synthetic fixture), confirming the
    compiler-synthesized implicit special members (constructors, destructor,
    copy/move ``operator=``) no longer reach the linked reachable-declaration
    surface as public API, and the real ``sum()`` method still does.

    Before this fix, this exact shape reproduced a false-positive
    ``source_binary_provenance_mismatch``-shaped signal: 6 of 7 exportable
    declarations never mapped to any exported binary symbol, purely because
    5 of the 6 were phantom implicit members that are essentially never
    emitted as their own out-of-line symbol.
    """
    import shutil
    import subprocess

    from abicheck.buildsource.source_extractors.castxml import CastxmlSourceExtractor
    from abicheck.buildsource.source_link import link_source_abi

    if shutil.which("g++") is None:
        pytest.skip("needs a real g++ toolchain")

    header = tmp_path / "widget.h"
    header.write_text(
        "struct Widget { int x; int y; int sum() const; };\n", encoding="utf-8"
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint Widget::sum() const { return x + y; }\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", "libwidget.so", "widget.cpp"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    cu = _cu(
        source="widget.cpp",
        directory=str(tmp_path),
        standard="c++17",
        argv=["g++", "-std=c++17", "-fPIC", "-c", "widget.cpp", "-o", "widget.o"],
    )
    extractor = CastxmlSourceExtractor()
    if not extractor.available():
        pytest.skip("needs a real castxml toolchain")

    tu = extractor.extract(
        cu, public_header_roots=["widget.h"], target_id="target://widget"
    )

    # Ground truth: castxml really did emit the phantom entries (this is the
    # bug's own precondition, not this fix's own claim) -- more entities
    # than the one real user-written method.
    assert len(tu.functions) > 1

    # The fix itself: exactly one function entity is api_relevant (`sum`);
    # every phantom compiler-synthesized entry is excluded.
    relevant = [f for f in tu.functions if f.api_relevant]
    assert len(relevant) == 1
    assert relevant[0].mangled_name == "_ZNK6Widget3sumEv"

    surface = link_source_abi(
        [tu], exported_symbols=["_ZNK6Widget3sumEv"], library="libwidget.so"
    )
    reachable_names = {d.mangled_name for d in surface.reachable_declarations}
    assert reachable_names == {"_ZNK6Widget3sumEv"}
    # The one real declaration matched its real exported symbol -- a clean
    # 1/1, not the pre-fix 1/6 (or 1/7, including `sum` itself) that tripped
    # the false-positive provenance-mismatch heuristic.
    assert surface.coverage.get("matched_symbols") == 1
    assert not surface.unmatched.get("decls_without_symbol")
