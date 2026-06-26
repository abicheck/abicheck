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

"""Actionable hints for the recurring real-world header-parse aborts a bare
``-H include/`` hits on a conda/runtime package (field-eval P1): a missing
dependency/split-include header, a required config macro, and an undeclared
type from a missing umbrella/std prelude. Pure string-only (no compiler)."""

from __future__ import annotations

import pytest

from abicheck.dumper_clang_errors import diagnose_header_compile_failure


def test_no_hint_for_unrecognized_stderr() -> None:
    assert diagnose_header_compile_failure("") is None
    assert diagnose_header_compile_failure("some unrelated linker error") is None


def test_missing_dependency_header_clang_spelling() -> None:
    # protobuf headers pull in absl, which a runtime package does not ship.
    stderr = "foo.h:3:10: fatal error: 'absl/strings/string_view.h' file not found"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "absl/strings/string_view.h" in hint
    assert "-dev" in hint or "devel" in hint
    # A slash-bearing name is flagged as a likely dependency / split include root.
    assert "split include root" in hint or "dependency" in hint


def test_missing_split_include_root_gcc_spelling() -> None:
    # glib's gio/gio.h lives under a separate include root.
    stderr = "g.h:1:10: fatal error: gio/gio.h: No such file or directory"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "gio/gio.h" in hint
    assert "--include-dir" in hint or "-I" in hint


def test_required_config_macro() -> None:
    # pcre2 refuses to compile until PCRE2_CODE_UNIT_WIDTH is defined.
    stderr = "pcre2.h:50:4: error: #error PCRE2_CODE_UNIT_WIDTH must be defined"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "PCRE2_CODE_UNIT_WIDTH" in hint
    assert "--gcc-options" in hint


def test_undeclared_type_needs_umbrella_size_t() -> None:
    # libjpeg-turbo header used without the standard prelude for size_t.
    stderr = "jpeglib.h:120:5: error: unknown type name 'size_t'"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "size_t" in hint
    assert "umbrella" in hint


def test_undeclared_identifier_hdf5() -> None:
    # hdf5 C++ header parsed without its umbrella: hid_t/H5std_string undeclared.
    stderr = "H5Cpp.h:30:1: error: use of undeclared identifier 'hid_t'"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "hid_t" in hint
    assert "umbrella" in hint


def test_does_not_name_a_type_cpp() -> None:
    stderr = "H5Cpp.h:42:9: error: 'H5std_string' does not name a type"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "H5std_string" in hint


def test_macro_named_after_prose_is_captured_not_the_prose() -> None:
    # Regression (CodeRabbit/Codex): a macro phrased *after* the prose, e.g.
    # "You must define FOO_FEATURE", must capture the macro — never a lowercase
    # word like "must" (which a case-insensitive uppercase class would grab).
    stderr = "cfg.h:7:2: error: #error You must define FOO_FEATURE before use"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "FOO_FEATURE" in hint
    assert "-Dmust" not in hint
    assert "-DFOO_FEATURE" in hint


def test_macro_detection_is_case_sensitive_no_false_hint() -> None:
    # A generic #error with no ALL-CAPS macro token hits no known signature, so
    # the contract is a clean None — not a bogus macro hint built from prose.
    stderr = "x.h:1:2: error: #error this header must be configured first"
    assert diagnose_header_compile_failure(stderr) is None


def test_all_caps_prose_only_yields_no_macro_hint() -> None:
    # An #error whose only ALL-CAPS tokens are prose stopwords ("You MUST
    # define") must not fabricate a macro hint from them.
    stderr = "x.h:1:2: error: #error You MUST define it"
    assert diagnose_header_compile_failure(stderr) is None


@pytest.mark.parametrize("macro", ["_GNU_SOURCE", "__STDC_LIMIT_MACROS"])
def test_leading_underscore_config_macro(macro: str) -> None:
    # Config macros that start with an underscore (_GNU_SOURCE,
    # __STDC_LIMIT_MACROS) must still be recognized — `\b[A-Z]` would miss them.
    stderr = f"h.h:9:2: error: #error {macro} must be defined first"
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert macro in hint
    assert f"-D{macro}" in hint


def test_macro_takes_precedence_over_missing_header() -> None:
    # When both signatures appear, the required-macro hint (more specific and
    # earlier in the failure) wins so the user gets the unblocking action first.
    stderr = (
        "pcre2.h:50:4: error: #error PCRE2_CODE_UNIT_WIDTH must be defined\n"
        "pcre2.h:51:10: fatal error: 'extra.h' file not found"
    )
    hint = diagnose_header_compile_failure(stderr)
    assert hint is not None
    assert "PCRE2_CODE_UNIT_WIDTH" in hint
