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

"""Tests for the structured compile-context snapshot provenance fields
(schema v14, P1 toolchain-profile audit): ``AbiSnapshot.ast_resolved_standard``
/ ``ast_cplusplus_macro`` / ``ast_compile_args`` / ``ast_sysroot``, and the
``dumper.py`` helpers that compute them.

Split out of ``test_dumper_unit.py`` (already near the file-size cap) rather
than appended there.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper import (
    _ast_compile_provenance,
    _cplusplus_macro_for_standard,
    _resolve_standard_provenance,
)
from abicheck.dumper_toolchain import _extract_explicit_std_value
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict


class TestCplusplusMacroForStandard:
    def test_gnu_prefixed_editions(self):
        assert _cplusplus_macro_for_standard("gnu++11") == "201103L"
        assert _cplusplus_macro_for_standard("gnu++14") == "201402L"
        assert _cplusplus_macro_for_standard("gnu++17") == "201703L"
        assert _cplusplus_macro_for_standard("gnu++20") == "202002L"

    def test_bare_cxx_editions(self):
        assert _cplusplus_macro_for_standard("c++11") == "201103L"
        assert _cplusplus_macro_for_standard("c++2a") == "202002L"

    def test_unrecognized_edition_returns_none(self):
        assert _cplusplus_macro_for_standard("gnu++99") is None
        assert _cplusplus_macro_for_standard("c11") is None  # C, not C++

    def test_none_input_returns_none(self):
        assert _cplusplus_macro_for_standard(None) is None
        assert _cplusplus_macro_for_standard("") is None


class TestExtractExplicitStdValue:
    def test_from_gcc_option_tokens(self):
        assert _extract_explicit_std_value(None, ("-std=gnu++11",)) == "gnu++11"

    def test_from_gcc_options_string(self):
        assert _extract_explicit_std_value("-DFOO=1 -std=c++20", ()) == "c++20"

    def test_msvc_style_slash_std(self):
        assert _extract_explicit_std_value(None, ("/std:c++17",)) == "c++17"

    def test_no_explicit_std_returns_none(self):
        assert _extract_explicit_std_value("-DFOO=1", ()) is None

    def test_double_dash_long_form(self):
        assert _extract_explicit_std_value(None, ("--std=c++14",)) == "c++14"


class TestResolveStandardProvenance:
    def test_explicit_std_wins_even_with_cpp20_headers(self, tmp_path: Path):
        """An explicit standard is never overridden by the requires/concept
        heuristic — matches dumper.py's own force_cpp20 gating."""
        header = tmp_path / "h.h"
        header.write_text("template<class T> concept C = true;\n", encoding="utf-8")
        assert _resolve_standard_provenance([header], "-std=gnu++11", ()) == "gnu++11"

    def test_no_explicit_std_no_cpp20_syntax_returns_none(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text("int f(int x);\n", encoding="utf-8")
        assert _resolve_standard_provenance([header], None, ()) is None

    def test_error_requires_guard_does_not_force_cxx20(self, tmp_path: Path):
        """Regression: the PVXS #error-requires false positive must never
        surface as a resolved C++20 standard in provenance either."""
        header = tmp_path / "h.h"
        header.write_text(
            "#ifndef HAVE_BASE\n#error Foo requires Base\n#endif\nint f(int x);\n",
            encoding="utf-8",
        )
        assert _resolve_standard_provenance([header], None, ()) is None

    def test_real_requires_clause_resolves_to_gnu_cxx20(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text(
            "template<class T> requires true\nint f(T x);\n", encoding="utf-8"
        )
        assert _resolve_standard_provenance([header], None, ()) == "gnu++20"

    def test_no_headers_returns_none(self):
        assert _resolve_standard_provenance([], None, ()) is None


class TestAstCompileProvenance:
    def test_full_shape(self, tmp_path: Path):
        header = tmp_path / "h.h"
        header.write_text("int f(int x);\n", encoding="utf-8")
        sysroot = tmp_path / "sysroot"
        prov = _ast_compile_provenance([header], "-DFOO=1", ("-std=gnu++17",), sysroot)
        assert prov["ast_resolved_standard"] == "gnu++17"
        assert prov["ast_cplusplus_macro"] == "201703L"
        assert prov["ast_compile_args"] == ("-std=gnu++17", "-DFOO=1")
        assert prov["ast_sysroot"] == str(sysroot)

    def test_empty_inputs_are_all_none_or_empty(self):
        prov = _ast_compile_provenance([], None, (), None)
        assert prov == {
            "ast_resolved_standard": None,
            "ast_cplusplus_macro": None,
            "ast_compile_args": (),
            "ast_sysroot": None,
        }


class TestSnapshotSerializationRoundTrip:
    def test_new_fields_round_trip_through_json(self):
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            ast_resolved_standard="gnu++17",
            ast_cplusplus_macro="201703L",
            ast_compile_args=("-std=gnu++17", "-DFOO=1"),
            ast_sysroot="/opt/sysroot",
        )
        d = snapshot_to_dict(snap)
        assert d["schema_version"] == 14
        back = snapshot_from_dict(d)
        assert back.ast_resolved_standard == "gnu++17"
        assert back.ast_cplusplus_macro == "201703L"
        assert back.ast_compile_args == ("-std=gnu++17", "-DFOO=1")
        assert back.ast_sysroot == "/opt/sysroot"

    def test_pre_v14_snapshot_loads_new_fields_as_defaults(self):
        """Backward compatibility: a pre-v14 snapshot dict has none of the
        four new keys at all — loading it must default them, never crash."""
        d = {
            "schema_version": 13,
            "library": "legacy.so",
            "version": "1.0",
        }
        snap = snapshot_from_dict(d)
        assert snap.ast_resolved_standard is None
        assert snap.ast_cplusplus_macro is None
        assert snap.ast_compile_args == ()
        assert snap.ast_sysroot is None

    def test_wrong_typed_fields_in_dict_default_safely(self):
        """A hand-edited or corrupted snapshot dict with wrong types for the
        new fields must not raise -- defensive .get() parsing, same
        convention as every other provenance field here."""
        d = {
            "schema_version": 14,
            "library": "weird.so",
            "version": "1.0",
            "ast_resolved_standard": 123,
            "ast_cplusplus_macro": ["not", "a", "string"],
            "ast_compile_args": "not-a-list",
            "ast_sysroot": 42,
        }
        snap = snapshot_from_dict(d)
        assert snap.ast_resolved_standard is None
        assert snap.ast_cplusplus_macro is None
        assert snap.ast_compile_args == ()
        assert snap.ast_sysroot is None
