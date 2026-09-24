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


"""One index build per raw AST, proven on real compiled artifacts.

The end-to-end half of ``test_clang_template_index_reuse.py``, split out when
the combined file crossed the architecture gate's 1200-line test-file cap --
a thematic split, not a trim to fit (``AGENTS.md``: shrink such a file by
moving responsibility to a properly-owned module). Its sibling owns the unit
layer: what the builders answer, how the bundle is shared and scoped, and
that it is read-only. This file owns the only claim those cannot make --
that a real ``clang``/``castxml`` parse of real ``g++``-compiled libraries
produces the identical findings, snapshots and rendered report with the
reuse off and on.

One rule governs every test here, and getting it wrong is what made the
first version of this suite fail CI while passing locally: **both arms run
inside an acquisition scope and vary only the reuse.** A scoped-versus-
unscoped comparison also flips which parse ``semantic_ir`` is derived from
(``header_ast_fields._parse_header_ast_fields`` uses the neutral parse
inside a scope and the legacy export-bound one outside it, PR #1306), which
this change does not touch and which different clang builds disagree about.
Each test also proves, within itself, that its two arms really ran
differently rather than one being served the other's answer.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.dumper_cache import ast_acquisition_scope

_CLANG_L2 = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or shutil.which("clang") is None
    or shutil.which("g++") is None
    or platform.machine().lower() not in {"x86_64", "amd64"},
    reason="clang L2 end-to-end parity needs clang+g++ on x86-64 Linux",
)

_TEMPLATE_HEADER = """
#pragma once
namespace fx {
template <class T, class U> struct Box;
template <class X, class Y = X> struct Box { X x; Y y; X get() const; };

template <class T, class U = int> struct Pair { T first; U second; T take() const; };
template <> struct Pair<float, int> { float first; int second; float take() const; };

template <class T> struct Outer { template <class V = T> struct Inner { V v; }; T value; };
template <> struct Outer<int> { template <class V = int> struct Inner { V v; }; int value; };

template <class T> struct Wrap { using type = Box<T, T>; };
template <class T> using WrapT = typename Wrap<T>::type;

struct Node : Box<double> {
  virtual ~Node();
  virtual double weight() const;
%(extra_virtual)s  WrapT<float> axis() const;
};

double reduce(const Node& n);
%(removed)s}
"""

_SOURCE = """
#include "api.hpp"
namespace fx {
template struct Box<int, int>;
template struct Box<double, double>;
float Pair<float, int>::take() const { return first; }
Node::~Node() {}
double Node::weight() const { return x + y; }
%(extra_virtual_def)s WrapT<float> Node::axis() const { return WrapT<float>(); }
double reduce(const Node& n) { return n.weight(); }
%(removed_def)s}
"""


def _build_side(root: Path, *, broken: bool) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    header = root / "api.hpp"
    header.write_text(
        _TEMPLATE_HEADER
        % {
            # The new side adds a virtual (a vtable change through a
            # defaulted-specialization base) and drops a public function.
            "extra_virtual": "  virtual double norm() const;\n" if broken else "",
            "removed": "" if broken else "double shape_count();\n",
        },
        encoding="utf-8",
    )
    source = root / "api.cpp"
    source.write_text(
        _SOURCE
        % {
            "extra_virtual_def": "double Node::norm() const { return x; }"
            if broken
            else "",
            "removed_def": "" if broken else "double shape_count() { return 1.0; }\n",
        },
        encoding="utf-8",
    )
    so = root / "libfx.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            "-std=c++17",
            f"-I{root}",
            str(source),
            "-o",
            str(so),
        ],
        check=True,
        capture_output=True,
    )
    return so, header


def _compare_sides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    old_so, old_h = _build_side(tmp_path / "old", broken=False)
    new_so, new_h = _build_side(tmp_path / "new", broken=True)
    old_snap = dump(old_so, [old_h], [old_h.parent])
    new_snap = dump(new_so, [new_h], [new_h.parent])
    return compare(old_snap, new_snap), old_snap, new_snap


@_CLANG_L2
def test_a_real_comparison_keeps_its_full_finding_set_under_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole real comparison under reuse off vs. on, not a verdict or a count.

    Both arms run **inside** an acquisition scope, and the only thing that
    varies is whether the index bundle is shared. That is load-bearing, and
    the first version of this test got it wrong: it compared a scoped run
    against an *unscoped* one, which also flips something this PR does not
    touch -- `header_ast_fields._parse_header_ast_fields` derives
    `semantic_ir` from the NEUTRAL parse inside a scope and from the LEGACY
    (export-bound) parse outside one. The two happen to agree on the
    fixture under one clang and not under another, so the test passed
    locally and failed on CI's 3.12 lane, asserting a difference that was
    never this change's. Holding the scope constant is what makes the
    comparison about the reuse.

    `AGENTS.md` asks a change to be proven through the public workflow and
    the rendered report, so this compares the *entire* serialized snapshot
    of each side plus the rendered JSON report -- not a verdict or a count,
    either of which would pass against an implementation that silently
    swapped one finding for another.
    """
    import json as _json

    from abicheck import dumper_clang
    from abicheck.reporter import to_json

    builds: dict[bool, int] = {}

    def _run(reuse: bool) -> tuple[Any, Any, Any]:
        """One comparison with reuse on or forced off, inside a scope."""
        count = 0
        real = dumper_clang.build_template_param_indexes

        def counted(root: dict[str, Any]) -> Any:
            nonlocal count
            count += 1
            return real(root)

        with monkeypatch.context() as patch:
            patch.setattr(dumper_clang, "build_template_param_indexes", counted)
            if not reuse:
                # The pre-change behaviour: rebuild per constructed parser.
                patch.setattr(dumper_clang, "_template_param_indexes_for", counted)
            with ast_acquisition_scope():
                result = _compare_sides(
                    tmp_path / ("arm_one" if reuse else "arm_two"), monkeypatch
                )
        builds[reuse] = count
        return result

    reuse_result, reuse_old, reuse_new = _run(True)
    plain_result, plain_old, plain_new = _run(False)

    # Prove both configurations really ran differently, by observing the
    # mechanism rather than its output -- otherwise an equality that held
    # because the second arm was served the first's answer would pass
    # (`AGENTS.md`: "a differential test must prove both of its
    # configurations actually ran").
    assert builds[True] == 2, builds
    assert builds[False] > builds[True], builds

    def _scrub_paths(value: Any) -> Any:
        """*value* with the two build roots' absolute paths normalized away.

        The arms build under two roots so neither can be served the other's
        AST cache entry; their paths therefore differ by construction and
        say nothing about the parse.
        """
        # Equal-length names on purpose: the compiled `.so` embeds its own
        # compile path, so roots of differing length would move `source_size`
        # for a reason that has nothing to do with the parse, and that field
        # would have to be excluded instead of checked.
        roots = (str(tmp_path / "arm_one"), str(tmp_path / "arm_two"))
        if isinstance(value, str):
            for root in roots:
                value = value.replace(root, "<root>")
            return value
        if isinstance(value, dict):
            return {k: _scrub_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_scrub_paths(v) for v in value]
        return value

    def _findings(result: Any) -> list[tuple[Any, ...]]:
        # `c.kind.value` (the slug), not `str(c.kind)` (which renders
        # "ChangeKind.X") -- the kind assertions below read as the vocabulary
        # the reports and docs use.
        return sorted(
            (c.kind.value, c.symbol or "", str(c.old_value), str(c.new_value))
            for c in result.changes
        )

    assert _findings(plain_result), "the fixture must produce real findings"
    assert _findings(reuse_result) == _findings(plain_result)
    assert reuse_result.verdict == plain_result.verdict

    # The expected breaks themselves, not only that the two arms agree: an
    # equality assertion alone would still hold if BOTH arms had silently
    # stopped detecting them.
    kinds = {kind for kind, *_ in _findings(reuse_result)}
    assert "type_vtable_changed" in kinds, kinds
    removed = {
        symbol
        for kind, symbol, *_ in _findings(reuse_result)
        if "removed" in kind.lower()
    }
    assert any("shape_count" in s for s in removed), (
        f"the export-loss break disappeared; kinds={kinds} removed={removed}"
    )

    def _surface(snap: Any) -> dict[str, Any]:
        """The whole serialized snapshot, minus what a rerun legitimately varies.

        Deliberately the full document rather than a hand-listed set of
        attributes -- the claim is that *nothing* the header AST decides
        moved, and a hand-listed projection only proves it for the fields
        whoever wrote the list thought of.
        """
        from abicheck.serialization import snapshot_to_dict

        document = snapshot_to_dict(snap)
        for volatile in (
            "library",
            "path",
            "timestamp",
            "created_at",
            "provenance",
            "source_mtime",
        ):
            document.pop(volatile, None)
        return _scrub_paths(document)

    assert _surface(reuse_old) == _surface(plain_old)
    assert _surface(reuse_new) == _surface(plain_new)

    # Vacuity guard on that equality: it is only evidence about template
    # handling if the parse actually reconstructed specialization spellings.
    rendered = _json.dumps(_surface(plain_old))
    assert "Outer<int>" in rendered, (
        "the fixture no longer exercises specialization-spelling "
        "reconstruction, so the snapshot equality proves nothing here"
    )

    def _stable(section: object) -> object:
        """*section* without `finding_id`, which folds in `source_location`.

        Two trees built under two roots legitimately disagree on it and on
        nothing else. `canonical_finding_id` -- the identity that is supposed
        to be stable across spellings and locations -- stays in the
        comparison, and matches.
        """
        if isinstance(section, list):
            return [_stable(item) for item in section]
        if isinstance(section, dict):
            return {k: _stable(v) for k, v in section.items() if k != "finding_id"}
        return section

    plain_report = _scrub_paths(_json.loads(to_json(plain_result)))
    reuse_report = _scrub_paths(_json.loads(to_json(reuse_result)))
    for section in ("changes", "summary", "analysis_assurance"):
        assert _stable(reuse_report.get(section)) == _stable(
            plain_report.get(section)
        ), section
    assert all(c.get("canonical_finding_id") for c in reuse_report["changes"])


@_CLANG_L2
def test_a_non_template_control_comparison_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The small control case: a header with no templates at all.

    A performance change that only ever fires on template-heavy input still
    has to leave the ordinary case bit-for-bit alone, and the regression it
    could plausibly cause there (an empty index shared where a populated one
    was expected) is invisible in the template fixture above.
    """
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    header_text = """
#pragma once
namespace plain {
struct Point { int x; int y; };
double distance(const Point& a, const Point& b);
struct Base { virtual ~Base(); virtual int kind() const; };
}
"""
    source_text = """
#include "plain.hpp"
namespace plain {
double distance(const Point& a, const Point& b) { return a.x - b.x; }
Base::~Base() {}
int Base::kind() const { return 1; }
}
"""
    from abicheck import dumper_clang

    results = []
    # Both arms inside a scope, varying only the reuse -- same reasoning as
    # the template test above: a scoped-vs-unscoped comparison would also
    # flip which parse `semantic_ir` is derived from, which this PR does not
    # touch.
    for reuse in (True, False):
        root = tmp_path / ("arm_one" if reuse else "arm_two")
        root.mkdir(parents=True, exist_ok=True)
        (root / "plain.hpp").write_text(header_text, encoding="utf-8")
        (root / "plain.cpp").write_text(source_text, encoding="utf-8")
        so = root / "libplain.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                "-std=c++17",
                f"-I{root}",
                str(root / "plain.cpp"),
                "-o",
                str(so),
            ],
            check=True,
            capture_output=True,
        )
        with monkeypatch.context() as patch:
            if not reuse:
                patch.setattr(
                    dumper_clang,
                    "_template_param_indexes_for",
                    dumper_clang.build_template_param_indexes,
                )
            with ast_acquisition_scope():
                snap = dump(so, [root / "plain.hpp"], [root])
                results.append((compare(snap, snap), snap))
    reuse_result, reuse_snap = results[0]
    plain_result, plain_snap = results[1]
    assert reuse_result.verdict == plain_result.verdict
    assert [str(c.kind) for c in reuse_result.changes] == [
        str(c.kind) for c in plain_result.changes
    ]
    # A self-comparison of an unchanged library must not be judged a break --
    # stated outright rather than inherited from the two arms agreeing, which
    # would also hold if both had started reporting the same spurious break.
    # Not "no findings at all": a self-compare with a header context legitimately
    # reports `header_binary_context_mismatch`, which is pre-existing and
    # nothing to do with this change, so the claim is about the verdict.
    assert str(reuse_result.verdict) == str(plain_result.verdict)
    assert not [
        c.kind.value
        for c in reuse_result.changes
        if c.kind.value not in {"header_binary_context_mismatch"}
    ], [c.kind.value for c in reuse_result.changes]
    # And the control must actually have parsed a real surface, or "clean"
    # is vacuous.
    assert any("distance" in (f.name or "") for f in reuse_snap.functions), [
        f.name for f in reuse_snap.functions
    ]


_CASTXML_CONTROL = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or shutil.which("castxml") is None
    or shutil.which("g++") is None,
    reason="the castxml control needs castxml + g++ on Linux",
)


@_CASTXML_CONTROL
@pytest.mark.integration
def test_the_castxml_backend_is_an_unchanged_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other L2 header backend must be untouched by this change.

    castxml has its own parser and never constructs a `_ClangAstParser`, so
    nothing here should reach it -- which is exactly why it is worth asserting
    rather than assuming. A shared-state change that accidentally leaked
    across backends (a key namespace collision in the one acquisition table,
    say) would show up here and nowhere else in this file.

    Two claims, because the weaker one alone would not catch a collision:
    the castxml comparison is identical with reuse on and off, **and** the
    clang-side builder never ran during it.
    """
    from abicheck import dumper_clang
    from abicheck.checker import compare
    from abicheck.dumper import dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxml")
    builds: list[int] = []
    results = []
    for reuse in (True, False):
        count = 0
        real = dumper_clang.build_template_param_indexes

        def counted(root: dict[str, Any], _real: Any = real) -> Any:
            nonlocal count
            count += 1
            return _real(root)

        root = tmp_path / ("arm_one" if reuse else "arm_two")
        old_so, old_h = _build_side(root / "old", broken=False)
        new_so, new_h = _build_side(root / "new", broken=True)
        with monkeypatch.context() as patch:
            patch.setattr(dumper_clang, "build_template_param_indexes", counted)
            if not reuse:
                patch.setattr(dumper_clang, "_template_param_indexes_for", counted)
            with ast_acquisition_scope():
                old_snap = dump(old_so, [old_h], [old_h.parent])
                new_snap = dump(new_so, [new_h], [new_h.parent])
                results.append(compare(old_snap, new_snap))
        builds.append(count)
        # The control is only a control if castxml really parsed the headers.
        # Without this it would still "pass" after silently degrading to a
        # symbols-only or DWARF-only dump, which is the failure mode a
        # backend-scoped assertion exists to catch.
        assert getattr(old_snap, "ast_producer", None) == "castxml", getattr(
            old_snap, "ast_producer", None
        )
        spellings = {t.name for t in old_snap.types if "<" in (t.name or "")}
        assert spellings, sorted(t.name for t in old_snap.types)

    # Nothing in this change is reachable from the castxml path at all.
    assert builds == [0, 0], builds

    reuse_result, plain_result = results

    def _findings(result: Any) -> list[tuple[Any, ...]]:
        return sorted(
            (c.kind.value, c.symbol or "", str(c.old_value), str(c.new_value))
            for c in result.changes
        )

    assert _findings(reuse_result), "the castxml control must produce findings"
    assert _findings(reuse_result) == _findings(plain_result)
    assert str(reuse_result.verdict) == str(plain_result.verdict)


@_CLANG_L2
def test_the_header_graph_pass_is_unchanged_under_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header-graph attach is another parser over the same AST -- cover it.

    ``service_dump_native`` builds the L2 semantic header graph by calling
    ``dumper._clang_header_dump`` a second time, which constructs one more
    ``_ClangAstParser`` over the tree the main pass already parsed. That is
    precisely a consumer this change shares indexes with, and the sibling
    tests miss it entirely: they call ``dumper.dump`` directly, and the graph
    attach only happens on the ``service.run_dump`` path (``_HEADER_GRAPH_
    ENABLED``), so their snapshots legitimately carry no graph at all.

    So this one goes through the service entry point and compares the whole
    serialized snapshot -- graph included -- with the reuse off and on, and
    asserts the graph is actually populated so the comparison cannot pass
    vacuously against two equally empty graphs.
    """
    from abicheck import dumper_clang
    from abicheck.serialization import snapshot_to_dict
    from abicheck.service import run_dump

    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")

    builds: dict[bool, int] = {}

    def _run(reuse: bool) -> dict[str, Any]:
        root = tmp_path / ("arm_one" if reuse else "arm_two")
        so, header = _build_side(root, broken=False)
        count = 0
        real = dumper_clang.build_template_param_indexes

        def counted(node: dict[str, Any]) -> Any:
            nonlocal count
            count += 1
            return real(node)

        with monkeypatch.context() as patch:
            patch.setattr(dumper_clang, "build_template_param_indexes", counted)
            if not reuse:
                patch.setattr(dumper_clang, "_template_param_indexes_for", counted)
            with ast_acquisition_scope():
                snap = run_dump(so, "elf", headers=[header], includes=[header.parent])
        builds[reuse] = count
        document = snapshot_to_dict(snap)
        for volatile in (
            "library",
            "path",
            "timestamp",
            "created_at",
            "provenance",
            "source_mtime",
        ):
            document.pop(volatile, None)
        roots = (str(tmp_path / "arm_one"), str(tmp_path / "arm_two"))

        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                for r in roots:
                    value = value.replace(r, "<root>")
                return value
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [scrub(v) for v in value]
            return value

        return scrub(document)

    reuse_doc = _run(True)
    plain_doc = _run(False)

    # Prove the two arms really ran differently before comparing them. Without
    # this the test is vacuous under any change that disables reuse globally:
    # both arms then rebuild per parser and agree trivially. Found by mutating
    # `_ClangAstParser.__init__` to call the builder directly -- that mutation
    # was caught by a sibling test and silently passed here.
    assert builds[True] == 1, builds
    assert builds[False] > builds[True], builds

    # Vacuity guard first: if no graph was built, the equality below says
    # nothing about the graph pass, which is the whole point of this test.
    graph = reuse_doc.get("surface_graph")
    assert graph, sorted(reuse_doc)
    assert graph.get("nodes"), graph.keys()
    assert graph.get("edges"), graph.keys()

    # `graph_id` is a digest the loader recomputes, so a stored graph no
    # longer carries it (evidence-entity-model Phase 5c) -- and with it goes
    # the only graph field that embedded each arm's absolute header paths in
    # a form the scrub below could not reach. Everything else, scrubbed, is
    # compared whole.
    assert "graph_id" not in reuse_doc["surface_graph"]
    assert "graph_id" not in plain_doc["surface_graph"]

    assert reuse_doc == plain_doc
