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

"""castxml vs clang through the header-AST backend protocol (lane B, B2b).

Both :class:`~abicheck.extract.headers.backend.HeaderAstBackend`
implementations parse the SAME headers from the SAME
:class:`~abicheck.extract.headers.backend.HeaderParseRequest`; the facts
read back through :func:`parse_header_ast_fields` must agree, facet by
facet. Every disagreement is listed in :data:`KNOWN_GAPS` with the exact
values each producer yields today, so a gap that closes (or widens) fails
here and the list is updated deliberately -- an unlisted disagreement is a
regression.

``tests/test_castxml_clang_parity_gate.py`` is the deep field-level parity
gate over ``dumper.dump``; this file pins the backend seam itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from _clang_ast_cache_isolation import _isolate_ast_cache, _reset_ast_memo

from abicheck.extract.header_ast_fields import parse_header_ast_fields
from abicheck.extract.headers.backend import HeaderAstBackend, HeaderParseRequest
from abicheck.extract.headers.castxml.backend import CastxmlBackend
from abicheck.extract.headers.clang.backend import ClangBackend

pytestmark = pytest.mark.skipif(
    shutil.which("castxml") is None
    or (shutil.which("clang") is None and shutil.which("clang++") is None)
    or (shutil.which("cc") is None and shutil.which("gcc") is None),
    reason="needs castxml, clang and a host C compiler",
)

_CORPUS: dict[str, str] = {
    # Agreement corpus: C++ declarations both frontends model alike.
    "cpp.h": """#pragma once
namespace lib {
struct Point { int x; int y; };
enum class Color { Red, Green = 5 };
class Widget { public: virtual ~Widget(); virtual int area() const; int w; };
int add(int a, int b);
typedef unsigned long handle_t;
extern int counter;
}
""",
    # Agreement corpus: plain C.
    "plain.h": """#pragma once
struct Node { struct Node *next; int value; };
enum Mode { MODE_A, MODE_B = 4 };
typedef int (*callback_t)(int);
int run(struct Node *head, callback_t cb);
extern const int version;
""",
    # Known gap: C tag namespace vs ordinary namespace (docs/contribute/
    # known-gaps.md, "dumper_clang.py's parse_types() conflates a C/C++
    # tag-namespace identity with an ordinary-namespace typedef identity").
    "tag.h": """struct Foo;
typedef struct { int x; } Foo;
int use_foo(Foo *f);
""",
    # A C-compatible POD header: the language the frontend picks decides
    # which C++-only layout traits exist at all.
    "pod.h": """struct Pod { int a; double b; };
int pod_fn(struct Pod *p);
""",
}


def _facts(backend: HeaderAstBackend, header: Path, lang: str | None) -> dict[str, Any]:
    request = HeaderParseRequest(
        headers=[header],
        extra_includes=[],
        lang=lang,
        public_header_paths=[str(header)],
    )
    fields = parse_header_ast_fields(backend.parse(request), producer=backend.name)
    return {
        "functions": sorted(
            f.name for f in fields.functions if not f.is_compiler_generated
        ),
        "compiler_generated": sorted(
            f.name for f in fields.functions if f.is_compiler_generated
        ),
        "variables": sorted(v.name for v in fields.variables),
        "types": sorted(
            (t.name, tuple(fl.name for fl in t.fields)) for t in fields.types
        ),
        "enums": sorted(
            (e.name, tuple((m.name, m.value) for m in e.members)) for e in fields.enums
        ),
        "typedefs": sorted(fields.typedefs),
        "standard_layout": sorted(
            (t.name, getattr(t, "is_standard_layout", None)) for t in fields.types
        ),
    }


#: ``(header, lang, facet) -> (castxml value, clang value)`` for every facet
#: on which the two backends disagree today. Keep each entry's reason.
KNOWN_GAPS: dict[tuple[str, str | None, str], tuple[Any, Any]] = {
    # Producer difference: castxml reports the implicit special members it
    # synthesizes for a C++ record; clang's AST omits implicit declarations
    # it never needed to instantiate.
    ("cpp.h", "c++", "compiler_generated"): (
        [
            "Point",
            "Point",
            "Point",
            "Widget",
            "Widget",
            "operator=",
            "operator=",
            "operator=",
            "~Point",
        ],
        [],
    ),
    ("pod.h", "c++", "compiler_generated"): (
        ["Pod", "Pod", "Pod", "operator=", "operator=", "~Pod"],
        [],
    ),
    # Unsupported on one producer: castxml exposes no C++ layout traits.
    ("cpp.h", "c++", "standard_layout"): (
        [("Point", None), ("Widget", None)],
        [("Point", True), ("Widget", False)],
    ),
    ("pod.h", "c++", "standard_layout"): ([("Pod", None)], [("Pod", True)]),
    # Known gap (tag-namespace conflation): clang keys `struct Foo` and the
    # anonymous struct typedef'd to `Foo` by one identity, so the opaque tag
    # declaration is dropped; castxml keeps both records.
    ("tag.h", "c", "types"): ([("Foo", ()), ("Foo", ("x",))], [("Foo", ("x",))]),
    ("tag.h", "c", "standard_layout"): (
        [("Foo", None), ("Foo", None)],
        [("Foo", None)],
    ),
}

_CASES = [
    ("cpp.h", "c++"),
    ("plain.h", "c"),
    ("tag.h", "c"),
    ("pod.h", "c++"),
    ("pod.h", None),
]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("backend-differential")
    for name, text in _CORPUS.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


@pytest.mark.integration
@pytest.mark.parametrize(("header", "lang"), _CASES)
def test_backends_agree_except_for_listed_gaps(
    corpus: Path,
    header: str,
    lang: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    castxml = _facts(CastxmlBackend(), corpus / header, lang)
    clang = _facts(ClangBackend(), corpus / header, lang)
    for facet in castxml:
        gap = KNOWN_GAPS.get((header, lang, facet))
        if gap is None:
            assert castxml[facet] == clang[facet], (
                f"unlisted castxml/clang disagreement on {header} ({lang}) {facet}"
            )
        else:
            assert (castxml[facet], clang[facet]) == gap, (
                f"listed gap on {header} ({lang}) {facet} changed; update KNOWN_GAPS"
            )


@pytest.mark.integration
def test_clang_only_forces_cpp_mode_when_asked(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Known gap (``dump --lang c++`` is silently discarded on the primary
    clang header-AST pass, docs/contribute/known-gaps.md): the backend honors
    an explicit ``lang="c++"``, but auto-detection parses a C-compatible
    header in C mode, where the C++-only layout traits do not exist. The gap
    is the callers that squash an explicit ``c++`` to ``None`` before the
    backend sees it; this pins the backend half of that behavior."""
    _isolate_ast_cache(monkeypatch, tmp_path)
    _reset_ast_memo()
    auto = _facts(ClangBackend(), corpus / "pod.h", None)
    forced = _facts(ClangBackend(), corpus / "pod.h", "c++")
    assert auto["standard_layout"] == [("Pod", None)]
    assert forced["standard_layout"] == [("Pod", True)]
    # Everything else matches across the two modes.
    for facet in ("functions", "variables", "types", "enums", "typedefs"):
        assert auto[facet] == forced[facet], facet


def test_every_known_gap_names_a_tested_case() -> None:
    cases = {(h, lang) for h, lang in _CASES}
    assert {(h, lang) for h, lang, _ in KNOWN_GAPS} <= cases
