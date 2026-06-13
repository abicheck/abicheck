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

"""P19: a missing *generated* header during L4 replay should produce an
actionable 'build the target first' hint, not an opaque parse failure."""

from __future__ import annotations

from abicheck.buildsource.source_extractors.clang import (
    _missing_generated_header_hint,
)


def test_llvm_tablegen_inc_gives_build_hint():
    # The real LLVM shape: a configure-only tree lacks the TableGen `.inc`.
    stderr = (
        "In file included from llvm/IR/Attributes.h:88:\n"
        "llvm/IR/Attributes.h:88:14: fatal error: 'llvm/IR/Attributes.inc' "
        "file not found\n"
    )
    hint = _missing_generated_header_hint(stderr)
    assert "missing generated header 'llvm/IR/Attributes.inc'" in hint
    assert "build the target" in hint


def test_gcc_no_such_file_wording_also_matched():
    stderr = "foo.cpp:1:10: fatal error: config.h: No such file or directory\n"
    hint = _missing_generated_header_hint(stderr)
    assert "missing generated header 'config.h'" in hint  # config.h matches the heuristic


def test_plain_header_is_header_not_generated():
    stderr = "a.c:1:10: fatal error: 'foo/bar.h' file not found\n"
    hint = _missing_generated_header_hint(stderr)
    assert "missing header 'foo/bar.h'" in hint  # plain header, not flagged generated
    assert "missing generated header" not in hint


def test_unrelated_error_yields_no_hint():
    assert _missing_generated_header_hint("error: use of undeclared identifier 'x'") == ""
    assert _missing_generated_header_hint("") == ""
