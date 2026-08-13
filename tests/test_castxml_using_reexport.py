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
``castxml_policy.py``), invoked the same way ``_build_castxml_command``
invokes it (``--castxml-cc-gnu g++`` compiler emulation, not castxml's bare
default): castxml emits exactly **one** element for the target
declaration, shared by *both* namespaces' ``<Namespace members="...">``
attribute — never a duplicate. So the alias spelling
(``detail::cpu_feature_map``/``detail::range`` below) never enters the
snapshot at all; the failure mode is a silent false negative, matching
the direct-clang backend's already-documented behavior for the identical
construct, not the "castxml over-reports" asymmetry originally reported.

Two independent layers are checked per case, so a future castxml release
that changes this is caught either way:

1. The **raw XML** itself — the exact element count for the tag in
   question, and that the single element's id is listed in *both*
   namespaces' own ``members=`` attribute. This is deliberately not
   routed through ``_CastxmlParser`` at all: if a future castxml starts
   emitting an equivalent using-shadow/back-reference element under some
   *other* tag name (not ``Variable``/``Struct``), this raw check does not
   see it and only the parser-level check below would catch it via a
   changed constant/type count.
2. The **parser-level result** (``parse_constants()``/``parse_types()``),
   confirming the flat-iteration behavior ``dumper_castxml.py`` actually
   exhibits reads through correctly to the model.

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
from xml.etree.ElementTree import Element, parse as et_parse

import pytest

from abicheck.dumper import _CastxmlParser

pytestmark = pytest.mark.integration

# Bounds the castxml invocation below so a hung process fails the test
# instead of blocking the integration job indefinitely.
_CASTXML_TIMEOUT_SECONDS = 60


def _run_castxml(tmp_path: Path, source: str) -> tuple[Element, _CastxmlParser]:
    """Run real castxml on *source* and return both the raw XML root and a
    ``_CastxmlParser`` over it, so tests can assert on either layer."""
    if shutil.which("castxml") is None:
        pytest.skip("castxml not installed")
    header = tmp_path / "lib.hpp"
    header.write_text(textwrap.dedent(source))
    xml_path = tmp_path / "lib.xml"
    # Mirrors `_build_castxml_command`'s real compiler-emulation flag
    # (`--castxml-cc-gnu g++`) rather than castxml's bare default, so this
    # reproduces the exact XML shape the production dump path sees.
    try:
        proc = subprocess.run(
            [
                "castxml",
                "--castxml-output=1",
                "--castxml-cc-gnu",
                "g++",
                "-std=c++17",
                str(header),
                "-o",
                str(xml_path),
            ],
            capture_output=True,
            text=True,
            timeout=_CASTXML_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"castxml did not finish within {_CASTXML_TIMEOUT_SECONDS}s: {exc}")
    assert proc.returncode == 0, f"castxml failed: {proc.stderr}"
    root = et_parse(str(xml_path)).getroot()
    parser = _CastxmlParser(
        root,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[str(header)],
    )
    return root, parser


def _namespace_members(root: Element, name: str) -> set[str]:
    """The ``members`` id set of the ``<Namespace name="...">`` element."""
    for el in root.iter("Namespace"):
        if el.get("name") == name:
            return set((el.get("members") or "").split())
    raise AssertionError(f"no <Namespace name={name!r}> element in castxml output")


class TestUsingReexportDoesNotDuplicateAgainstRealCastxml:
    def test_reexported_constant_is_not_duplicated(self, tmp_path: Path) -> None:
        root, parser = _run_castxml(
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

        # --- Raw XML layer -------------------------------------------------
        # Exactly one <Variable name="cpu_feature_map"> element, full stop —
        # not two, regardless of what tag a future castxml might otherwise
        # use for an alias.
        variable_els = [
            el for el in root.iter("Variable") if el.get("name") == "cpu_feature_map"
        ]
        assert len(variable_els) == 1
        var_id = variable_els[0].get("id")
        assert var_id is not None
        # That single element is shared: both `v1` (its declaring namespace)
        # and `detail` (the using-declaration's enclosing namespace) list
        # the same id in their own `members=` attribute — a reference, not
        # a second, independent declaration.
        assert var_id in _namespace_members(root, "v1")
        assert var_id in _namespace_members(root, "detail")

        # --- Parser layer ----------------------------------------------------
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
        root, parser = _run_castxml(
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

        # --- Raw XML layer -------------------------------------------------
        struct_els = [el for el in root.iter("Struct") if el.get("name") == "range"]
        assert len(struct_els) == 1
        struct_id = struct_els[0].get("id")
        assert struct_id is not None
        assert struct_id in _namespace_members(root, "v1")
        assert struct_id in _namespace_members(root, "detail")

        # --- Parser layer ----------------------------------------------------
        types = parser.parse_types()
        names = [t.qualified_name or t.name for t in types if "range" in (t.name or "")]
        # Exactly one `range` record, qualified under `v1` — never a second
        # one reachable only as `detail::range`.
        assert names == ["detail::v1::range"]
