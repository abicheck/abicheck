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

"""G31 Phase C closure — using-declaration re-export vs. real castxml XML.

``AGENTS.md``'s "Known gaps" section documented a reported (but, at the
time, unverified — no castxml binary was available in that environment)
mechanism: a using-declaration re-exporting a namespace-scope constant or
type (``namespace v1 { constexpr int x = 1; } using v1::x;``) was believed
to make castxml emit *two* full ``<Variable>``/``<Struct>`` XML elements —
one under the original namespace, one under the re-exporting namespace —
which ``dumper_castxml.py``'s flat, once-per-element iteration
(``_iter_public_constants``/``parse_types``) would then read as two
independent, same-valued declarations.

This locks in the finding from actually reproducing that construct against
a real, policy-conformant castxml build (``>=0.6.11,<0.8.0`` per
``castxml_policy.py``): castxml emits exactly **one** element for the
target declaration, shared by *both* namespaces' ``<Namespace
members="...">`` attribute — never a duplicate. So the alias spelling
(``detail::cpu_feature_map``/``detail::range`` below) never enters the
snapshot at all; the failure mode is a silent false negative, matching
the direct-clang backend's already-documented behavior for the identical
construct, not the "castxml over-reports" asymmetry originally reported.

If a future castxml release changes this (starts emitting a second
element, or an equivalent using-shadow back-reference), these tests
should start failing loudly rather than silently keeping the codebase's
prose out of sync with reality — see AGENTS.md's "Option (b) closed" note
for the full reasoning and what a real fix would need.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from xml.etree.ElementTree import parse as et_parse

import pytest

from abicheck.dumper import _CastxmlParser

pytestmark = pytest.mark.integration


def _run_castxml(tmp_path: Path, source: str) -> _CastxmlParser:
    if shutil.which("castxml") is None:
        pytest.skip("castxml not installed")
    header = tmp_path / "lib.hpp"
    header.write_text(textwrap.dedent(source))
    xml_path = tmp_path / "lib.xml"
    proc = subprocess.run(
        [
            "castxml",
            "--castxml-output=1",
            "-std=c++17",
            str(header),
            "-o",
            str(xml_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"castxml failed: {proc.stderr}"
    root = et_parse(str(xml_path)).getroot()
    return _CastxmlParser(
        root,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[str(header)],
    )


class TestUsingReexportDoesNotDuplicateAgainstRealCastxml:
    def test_reexported_constant_is_not_duplicated(self, tmp_path: Path) -> None:
        parser = _run_castxml(
            tmp_path,
            """
            namespace detail {
            namespace v1 {
            constexpr int cpu_feature_map = 42;
            }
            using v1::cpu_feature_map;
            }
            """,
        )
        constants = parser.parse_constants()
        # The original (versioned-namespace) spelling is captured...
        assert constants.get("detail::v1::cpu_feature_map") == "42"
        # ...but the using-declaration alias is NOT a second, independent
        # entry — it is simply absent (a false negative, not a duplicate
        # key). This is the behavior this test locks in: exactly one
        # entry for this declaration, not two.
        assert "detail::cpu_feature_map" not in constants
        assert sum(1 for k in constants if "cpu_feature_map" in k) == 1

    def test_reexported_type_is_not_duplicated(self, tmp_path: Path) -> None:
        parser = _run_castxml(
            tmp_path,
            """
            namespace detail {
            namespace v1 {
            struct range {
                int lo;
                int hi;
            };
            }
            using v1::range;
            }
            """,
        )
        types = parser.parse_types()
        names = [t.qualified_name or t.name for t in types if "range" in (t.name or "")]
        # Exactly one `range` record, qualified under `v1` — never a second
        # one reachable only as `detail::range`.
        assert names == ["detail::v1::range"]
