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

"""Phase C fact-completeness — ``EnumType.underlying_type`` on castxml.

Previously clang-only: castxml's ``parse_enums`` never read the
``<Enumeration type=...>`` attribute, so a castxml-backed (and therefore
hybrid) enum silently kept the model default ``"int"`` regardless of the
real underlying type. No diff detector reads this field directly
(``enum_underlying_size_changed`` is computed from DWARF instead), but
``tu_merge.py``'s cross-TU ODR-conflict check for enums does — see
``test_tu_merge.py::test_merge_fragments_enum_raises_on_conflicting_underlying_type_for_forward_pair``
for that check's own coverage, which this closes the castxml-input gap for.

Two layers, mirroring ``test_castxml_var_access_value.py``:

1. Synthetic-XML unit tests against ``_CastxmlParser`` directly.
2. A live, real-``castxml``-invoking ``integration``-marked test, confirming
   the synthetic shapes above match real castxml 0.6.3 output — the
   fixed-underlying-type spelling (``enum E : short``, target-independent)
   and the implementation-chosen one for a plain, unfixed enum (target-
   dependent: Itanium-ABI value-range-derived on Linux/macOS, always ``int``
   under the MSVC ABI on Windows).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, parse as et_parse

import pytest

from abicheck.dumper import _CastxmlParser


def _xml_root(*children: Element) -> Element:
    root = Element("CastXML", attrib={"format": "1.4.0"})
    for child in children:
        root.append(child)
    return root


class TestCastxmlEnumUnderlyingType:
    def test_no_type_attribute_falls_back_to_default(self) -> None:
        """A synthetic element with no `type` attribute -- the shape every
        pre-existing enum-parsing unit test already uses -- keeps the
        dataclass default, unchanged from before this fact was read."""
        e = Element("Enumeration", id="e1", name="Color")
        SubElement(e, "EnumValue", name="RED", init="0")
        root = _xml_root(e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert enums[0].underlying_type == "int"

    def test_fixed_underlying_type_from_fundamental_type(self) -> None:
        """`enum class Color : short` -- `type` resolves directly to a
        `FundamentalType`."""
        ft = Element("FundamentalType", id="t1", name="short int")
        e = Element("Enumeration", id="e1", name="Color", type="t1")
        SubElement(e, "EnumValue", name="RED", init="0")
        root = _xml_root(ft, e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert enums[0].underlying_type == "short int"

    def test_unfixed_enum_gets_the_compiler_chosen_type(self) -> None:
        """A plain, unfixed `enum Status { OK, FAIL }` still carries a real
        `type` id -- the compiler's own value-range-derived choice, not a
        placeholder -- verified against real castxml output in the
        integration test below."""
        ft = Element("FundamentalType", id="t1", name="unsigned int")
        e = Element("Enumeration", id="e1", name="Status", type="t1")
        SubElement(e, "EnumValue", name="OK", init="0")
        SubElement(e, "EnumValue", name="FAIL", init="1")
        root = _xml_root(ft, e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert enums[0].underlying_type == "unsigned int"

    def test_follows_typedef_chain(self) -> None:
        """`enum E : my_int_t` -- `type` resolves through a Typedef to its
        concrete FundamentalType, mirroring `parse_typedefs`' own chain
        following (`_underlying_type_name` is shared by both)."""
        ft = Element("FundamentalType", id="t1", name="unsigned int")
        td = Element("Typedef", id="td1", name="my_int_t", type="t1")
        e = Element("Enumeration", id="e1", name="Color", type="td1")
        SubElement(e, "EnumValue", name="RED", init="0")
        root = _xml_root(ft, td, e)
        p = _CastxmlParser(root, set(), set())
        enums = p.parse_enums()
        assert enums[0].underlying_type == "unsigned int"


@pytest.mark.integration
class TestCastxmlEnumUnderlyingTypeAgainstRealCastxml:
    """The synthetic-XML shapes above, confirmed against a live castxml run.

    Marked ``integration`` per ``AGENTS.md``'s "if a test needs castxml,
    mark it `@pytest.mark.integration`" rule, so the canonical fast unit
    lane never becomes host-dependent on whether castxml happens to be
    installed.
    """

    def _dump_enums(self, tmp_path: Path, source: str) -> dict[str, str]:
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
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        return {e.name: e.underlying_type for e in parser.parse_enums()}

    def test_fixed_and_unfixed_enums(self, tmp_path: Path) -> None:
        underlying = self._dump_enums(
            tmp_path,
            """
            enum class Color : short { Red, Green, Blue };
            enum Status { OK, FAIL };
            enum class Big : unsigned long long { A };
            """,
        )
        # A *fixed* underlying type is a literal language feature -- its
        # spelling never depends on the target ABI.
        assert underlying["Color"] == "short int"
        assert underlying["Big"] == "long long unsigned int"
        # An *unfixed* enum's underlying type is the one part of this test
        # that IS target-dependent: castxml's bundled Clang targets the
        # Itanium ABI on Linux/macOS (value-range-derived -- `unsigned int`
        # covers {0, 1}) but the MSVC ABI on Windows, where an unfixed enum
        # is always plain `int` regardless of its value range (Codex review
        # -- confirmed the CI Windows `integration` leg installs a real
        # MSVC-targeting castxml).
        assert underlying["Status"] == (
            "int" if sys.platform == "win32" else "unsigned int"
        )
