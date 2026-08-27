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

"""Phase 4 of ``docs/contribute/plans/bug-class-regression-testing.md``:
the ``identity.environment_taint`` bug class (#837 -> #843 -> #846 -> #868),
generalized with real, end-to-end (python-api surface, real clang + g++
subprocesses) metamorphic coverage.

The pre-existing seed tests for this class
(``test_castxml_anonymous_type_location.py``,
``test_anon_type_location_properties.py``) are excellent at the
``strip_anonymous_type_location`` unit level, but -- per this class's own
``tests/regressions/manifest.py`` known-gap entry -- both feed a hand-built
AST-node/XML fragment into an internal parser class directly: no real
compiler subprocess, no CLI, no python-api call. This module closes that
gap for the transformations that are genuinely reproducible on this
sandbox's toolchain (real g++ + direct-clang, no castxml):

* relocate the checkout root
* symlink the checkout root
* insert/remove blank lines and comments (unrelated line drift)
* reorder unrelated declarations

Two transformations from the plan's list are deliberately NOT attempted
here, and are recorded as an honest known gap rather than faked:
Windows-style path separators (this sandbox has no such filesystem to
produce a genuine backslash-separated compiler-recorded path) and archive
member order (a `.a` static-archive concept; this class's own escape
history, and every extraction path exercised here, is ELF `.so`-only).
Changing the compilation-database root is a real, reproducible
transformation too, but is exercised by the pre-existing L3-focused
`test_build_context_completeness.py`/`test_dump_scan_l3_comparability.py`
suites already (a real `--build-info` root move is exactly what those
already prove is comparability-safe) -- repeating it here as a fifth
transformation would just re-derive that existing coverage under a new
name, not close a real gap.

Oracle: for every transformation, comparing the base library against the
transformed one must produce ``Verdict.NO_CHANGE`` with zero emitted
findings -- the two are, semantically, the exact same library.

Negative control (do not over-merge): reusing the exact counterexamples
AGENTS.md's "using-declaration" known-gap entry names as the control that
would have caught the reverted name-shape-heuristic attempts -- two
lambdas in one header, and two same-named nested records in different
namespaces -- confirming each pair keeps genuinely DISTINCT identities
even after the identical relocation transformation that collapses every
positive case above to NO_CHANGE.

Most tests here need only clang + g++ and stay unmarked, matching
``test_clang_header_backend_integration.py``'s own documented reasoning
(this module isn't marked ``integration`` since the point is running on a
castxml-absent host too). The one test that additionally needs castxml
(a cross-backend variant of the core #843 checkout-relocation case) DOES
carry a per-test ``@pytest.mark.integration``, for the identical reason
``test_clang_castxml_origin_parity.py`` documents: without it, a host with
castxml installed would have it silently selected and executed by the
fast/PR "not integration" lane.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.dumper import dump

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="real g++/clang ELF end-to-end test is Linux-scoped",
)


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _build(tmp_path: Path, header_text: str, source_text: str) -> tuple[Path, Path]:
    header = tmp_path / "api.h"
    header.write_text(header_text)
    src = tmp_path / "api.cpp"
    src.write_text(source_text)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def _dump(so: Path, header: Path, header_backend: str = "clang"):
    return dump(so, [header], header_backend=header_backend, public_headers=[header])


_HEADER = """
#pragma once
namespace lib {
struct Point { int x; int y; };
int add(int a, int b) noexcept;
class Widget {
public:
    int value() const;
private:
    int hidden_;
};
}
"""

_SOURCE = """
#include "api.h"
namespace lib {
int add(int a, int b) noexcept { return a + b; }
int Widget::value() const { return hidden_; }
}
"""


def _require_toolchain() -> None:
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this end-to-end test")


class TestRelocatingTheCheckoutRootIsANoOp:
    """The #843 path-taint bug's own shape, generalized: the SAME headers
    and source, byte-for-byte, compiled and dumped under two entirely
    different absolute directories, must compare as NO_CHANGE."""

    def test_two_independent_checkouts_compare_identical(self, tmp_path: Path) -> None:
        _require_toolchain()
        root_a = tmp_path / "checkout_a"
        root_b = tmp_path / "an/unrelated/deeper/checkout_b"
        root_a.mkdir(parents=True)
        root_b.mkdir(parents=True)

        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, _HEADER, _SOURCE)

        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []

    @pytest.mark.integration
    def test_two_independent_checkouts_compare_identical_via_castxml(
        self, tmp_path: Path
    ) -> None:
        """Cross-backend closure of this same case: the #843 bug was
        originally a CastXML-specific ctor/dtor identity taint, so the
        checkout-relocation invariant is verified against the real castxml
        backend here too, not only direct-clang above -- marked
        ``integration`` since it needs castxml in addition to clang/g++
        (see ``test_clang_castxml_origin_parity.py`` for the identical
        marker-discipline reasoning)."""
        if not _have("castxml"):
            pytest.skip("castxml required for this cross-backend variant")
        _require_toolchain()
        root_a = tmp_path / "checkout_a"
        root_b = tmp_path / "an/unrelated/deeper/checkout_b"
        root_a.mkdir(parents=True)
        root_b.mkdir(parents=True)

        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, _HEADER, _SOURCE)

        snap_a = _dump(so_a, header_a, header_backend="castxml")
        snap_b = _dump(so_b, header_b, header_backend="castxml")

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestSymlinkedCheckoutRootIsANoOp:
    """A checkout reached through a symlinked root must compare identically
    to the same checkout reached directly -- the recorded absolute path
    differs (a real symlink resolves to a different literal string than
    its target unless the extractor canonicalizes it), but the ABI is the
    exact same binary content."""

    def test_symlink_vs_direct_path_compare_identical(self, tmp_path: Path) -> None:
        _require_toolchain()
        real_root = tmp_path / "real_checkout"
        real_root.mkdir()
        so, header = _build(real_root, _HEADER, _SOURCE)

        symlink_root = tmp_path / "checkout_via_symlink"
        symlink_root.symlink_to(real_root, target_is_directory=True)
        so_via_link = symlink_root / so.name
        header_via_link = symlink_root / header.name

        snap_direct = _dump(so, header)
        snap_via_symlink = _dump(so_via_link, header_via_link)

        result = compare(snap_direct, snap_via_symlink)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestUnrelatedLineDriftIsANoOp:
    """Inserting/removing blank lines and comments earlier in the header
    shifts every declaration below to new line numbers -- the exact #868
    lambda-closure line-drift shape, generalized to plain (non-closure)
    declarations too."""

    def test_inserted_blank_lines_and_comments_compare_identical(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        root_a = tmp_path / "plain"
        root_b = tmp_path / "with_drift"
        root_a.mkdir()
        root_b.mkdir()

        noisy_header = """
#pragma once
// A comment that was not here before.


namespace lib {
// Another unrelated comment.
struct Point { int x; int y; };

int add(int a, int b) noexcept;
class Widget {
public:
    int value() const;
private:
    int hidden_;
};
}
"""
        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, noisy_header, _SOURCE)

        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestReorderingUnrelatedDeclarationsIsANoOp:
    """Reordering unrelated top-level declarations must not itself register
    as a change -- only the individual declarations' own facts matter, not
    their position in the source."""

    def test_reordered_declarations_compare_identical(self, tmp_path: Path) -> None:
        _require_toolchain()
        root_a = tmp_path / "original_order"
        root_b = tmp_path / "reordered"
        root_a.mkdir()
        root_b.mkdir()

        reordered_header = """
#pragma once
namespace lib {
class Widget {
public:
    int value() const;
private:
    int hidden_;
};
int add(int a, int b) noexcept;
struct Point { int x; int y; };
}
"""
        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, reordered_header, _SOURCE)

        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestNegativeControlDistinctEntitiesStayDistinct:
    """AGENTS.md's "using-declaration" known-gap entry names exactly these
    two counterexamples as the control that would have caught the reverted
    name-shape-heuristic attempts: distinct local entities with similar
    source shapes must never collapse to one identity, even after the
    identical relocation transformation the positive tests above show
    collapses a genuinely unchanged library to NO_CHANGE."""

    def test_two_lambdas_in_one_header_stay_distinct_across_relocation(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        header_text = """
#pragma once
namespace lib {
template <class F> int call_with(F f) { return f(); }
inline int run_one() { return call_with([]() { return 1; }); }
inline int run_two() { return call_with([]() { return 2; }); }
}
"""
        source_text = """
#include "api.h"
namespace lib { int touch() { return run_one() + run_two(); } }
"""
        root = tmp_path / "one_checkout"
        root.mkdir()
        so, header = _build(root, header_text, source_text)
        snap = _dump(so, header)

        # The two lambda-parameterized instantiations of call_with must be
        # genuinely distinct types/functions within the SAME snapshot --
        # not merely "not equal to each other by accident", but present as
        # two separate entries.
        lambda_functions = [f for f in snap.functions if "call_with" in f.mangled]
        distinct_mangled = {f.mangled for f in lambda_functions}
        assert len(distinct_mangled) >= 2, (
            "expected two distinct call_with<lambda> instantiations, "
            f"got: {distinct_mangled}"
        )

        # Now relocate the whole checkout and confirm the SAME two
        # instantiations are still distinct from each other post-move
        # (not merged into one by the relocation), while the snapshot as a
        # whole still compares NO_CHANGE against its own un-relocated self.
        relocated_root = tmp_path / "relocated" / "checkout"
        relocated_root.mkdir(parents=True)
        so2, header2 = _build(relocated_root, header_text, source_text)
        snap2 = _dump(so2, header2)

        relocated_mangled = {
            f.mangled for f in snap2.functions if "call_with" in f.mangled
        }
        assert len(relocated_mangled) >= 2

        result = compare(snap, snap2)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []

    def test_same_named_nested_records_in_different_namespaces_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        header_text = """
#pragma once
namespace api { struct Outer { struct Inner { int a; }; }; }
namespace detail { struct Outer { struct Inner { int b; long c; }; }; }
"""
        source_text = """
#include "api.h"
namespace lib {
int touch_api(api::Outer::Inner x) { return x.a; }
long touch_detail(detail::Outer::Inner x) { return x.c; }
}
"""
        root = tmp_path / "one_checkout"
        root.mkdir()
        so, header = _build(root, header_text, source_text)
        snap = _dump(so, header)

        inner_types = [t for t in snap.types if t.name == "Inner"]
        qualified = {t.qualified_name for t in inner_types if t.qualified_name}
        assert len(qualified) >= 2, (
            f"expected api::Outer::Inner and detail::Outer::Inner to stay "
            f"distinct, got: {qualified}"
        )
        # The two must genuinely differ in shape too (a genuinely different
        # entity, not a coincidental name collision the test failed to
        # notice): api::...::Inner has one int field, detail::...::Inner
        # has two fields of different types.
        by_qname = {t.qualified_name: t for t in inner_types}
        api_inner = next(t for q, t in by_qname.items() if q and q.startswith("api::"))
        detail_inner = next(
            t for q, t in by_qname.items() if q and q.startswith("detail::")
        )
        assert len(api_inner.fields) == 1
        assert len(detail_inner.fields) == 2

        # Relocate and confirm both stay distinct post-move too, while the
        # whole snapshot still compares NO_CHANGE against its own
        # un-relocated self.
        relocated_root = tmp_path / "relocated" / "checkout"
        relocated_root.mkdir(parents=True)
        so2, header2 = _build(relocated_root, header_text, source_text)
        snap2 = _dump(so2, header2)

        inner_types2 = [t for t in snap2.types if t.name == "Inner"]
        qualified2 = {t.qualified_name for t in inner_types2 if t.qualified_name}
        assert len(qualified2) >= 2

        result = compare(snap, snap2)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []
