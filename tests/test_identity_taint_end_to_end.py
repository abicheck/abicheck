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

The shared ``_HEADER``/``_SOURCE`` fixture (and every one of its
transformed variants) deliberately includes a lambda-parameterized
``invoke_with<...>`` instantiation alongside the ordinary named
declarations -- an ordinary named struct/function's identity does not
embed its source location at all, so a fixture built only from those
would pass every transformation below regardless of whether path/line
taint is actually handled correctly (Codex review, PR #898). Each
positive test asserts (``_assert_exercises_closure_taint``) that this
closure-tainted symbol is genuinely present before trusting the
NO_CHANGE oracle below.

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
template <class F> inline int invoke_with(F f) { return f(); }
inline int uses_lambda() { return invoke_with([]() { return 7; }); }
// A CLASS template instantiated over a closure, not just a function
// template -- castxml's own real closure-taint mechanism (AGENTS.md's
// "Lambda-closure churn" entry) is specifically synthetic ctor/dtor keys
// on a class template instantiated with a lambda argument, which embed
// the owning class's own template-argument spelling as literal text in
// Function.mangled. A bare function-template parameter's own type
// (invoke_with above) is NOT guaranteed to carry that marker on every
// backend: castxml genuinely cannot resolve a closure class declared
// inside a function body to anything but the unresolvable-type sentinel
// "?" for that shape (confirmed by a real CI failure on this exact
// fixture, PR #898 -- see AGENTS.md's own note on this castxml
// limitation), so this class-template construct is what actually gives
// castxml a location-bearing identity to carry.
template <class F> class Guard {
public:
    explicit Guard(F f) : fn_(f) {}
    ~Guard() {}
    int run() { return fn_(); }
private:
    F fn_;
};
inline int uses_guard() { Guard g([]() { return 13; }); return g.run(); }
}
"""

_SOURCE = """
#include "api.h"
namespace lib {
int add(int a, int b) noexcept { return a + b; }
int Widget::value() const { return hidden_; }
int touch_lambda() { return uses_lambda(); }
int touch_guard() { return uses_guard(); }
}
"""


def _require_toolchain() -> None:
    # dump()'s default `compiler="c++"` resolves to `clang++`, not the bare
    # `clang` driver (dumper_clang._resolve_clang_bin) -- checking only
    # `clang` both proceeds-then-fails on a minimal toolchain that has
    # `clang` but no `clang++`, and skips (losing real coverage) on a
    # `clang++`-only install with no bare `clang` symlink (Codex review,
    # PR #898).
    if not (_have("clang++") and _have("g++")):
        pytest.skip("clang++ and g++ are required for this end-to-end test")


def _assert_exercises_closure_taint(snap: object) -> None:
    """The positive (NO_CHANGE-expected) cases in this module are only
    meaningful if the fixture actually produces a closure/lambda-embedded
    identity -- otherwise a regression in path/line-drift handling for
    exactly that identity shape (the historical taint mechanism) would go
    uncaught while the test stays green for an unrelated reason (ordinary
    named declarations don't embed source location in their identity at
    all). Checks for a ``(lambda`` marker anywhere a backend could
    plausibly embed one: a parameter/return type spelling (direct-clang's
    own representation for `invoke_with`'s closure argument), a function's
    own ``mangled`` (castxml's synthetic ctor/dtor keys embed the OWNING
    class's own template-argument spelling there -- e.g. ``Guard``'s ctor/
    dtor above, per AGENTS.md's "Lambda-closure churn" entry), or a type's
    ``name``/``qualified_name``. Not merely a mangled-name SUBSTRING check
    on its own, though: the GENERIC, uninstantiated template pattern
    itself (e.g. ``invoke_with``, no lambda involved at all) also matches
    an ``"invoke_with" in f.mangled`` check, so that alone would stay
    green even if the actual lambda-parameterized specialization vanished
    from the snapshot -- this only accepts the literal ``(lambda`` marker
    text, never a bare template-name substring (Codex review, PR #898).
    Fails loudly, not silently, if the fixture stops producing one."""
    functions = snap.functions  # type: ignore[attr-defined]
    types = snap.types  # type: ignore[attr-defined]
    candidates = {
        t
        for f in functions
        for t in ([f.mangled, f.return_type] + [p.type for p in f.params])
    } | {t for ty in types for t in (ty.name, ty.qualified_name)}
    assert any(c and "(lambda" in c for c in candidates), (
        f"expected a '(lambda...)' marker somewhere in the snapshot's "
        f"function signatures/mangled names or type identities, found: "
        f"{candidates}"
    )


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

        _assert_exercises_closure_taint(snap_a)
        _assert_exercises_closure_taint(snap_b)
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
        marker-discipline reasoning). Only g++ (to build the .so) and
        castxml (the header backend under test) are actually invoked here
        -- deliberately does NOT gate on clang, unlike the direct-clang
        variant above, since this variant never touches it (Codex review,
        PR #898)."""
        if not (_have("g++") and _have("castxml")):
            pytest.skip("g++ and castxml are required for this cross-backend variant")
        root_a = tmp_path / "checkout_a"
        root_b = tmp_path / "an/unrelated/deeper/checkout_b"
        root_a.mkdir(parents=True)
        root_b.mkdir(parents=True)

        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, _HEADER, _SOURCE)

        snap_a = _dump(so_a, header_a, header_backend="castxml")
        snap_b = _dump(so_b, header_b, header_backend="castxml")

        _assert_exercises_closure_taint(snap_a)
        _assert_exercises_closure_taint(snap_b)
        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestSymlinkedCheckoutRootIsANoOp:
    """A checkout reached through a symlinked root must compare identically
    to the same checkout reached directly.

    Confirmed empirically (Codex review, PR #898): the real direct-clang
    backend resolves a symlinked header path to its REAL target before
    recording ``source_location``/``source_header`` -- clang's own file-
    open canonicalization, not anything abicheck does -- so both
    invocations record the identical resolved path (verified below, not
    assumed), and the closure-taint marker's own basename-only spelling
    (see ``_assert_exercises_closure_taint``'s docstring) never carried a
    directory component to vary in the first place. This test's real
    invariant is therefore an end-to-end sanity check that a symlinked
    entry point resolves and dumps identically to its real target, not a
    test of abicheck-specific symlink-canonicalization logic -- there is
    none to test here, since clang already does the canonicalizing
    upstream of anything abicheck's own model sees."""

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

        _assert_exercises_closure_taint(snap_direct)
        _assert_exercises_closure_taint(snap_via_symlink)

        # Prove the canonicalization claim above rather than merely assert
        # it: both sides record the REAL (non-symlinked) path, confirming
        # clang resolved the symlink before abicheck's model ever saw it.
        add_direct = next(f for f in snap_direct.functions if f.name.endswith("add"))
        add_via_symlink = next(
            f for f in snap_via_symlink.functions if f.name.endswith("add")
        )
        assert add_direct.source_header == add_via_symlink.source_header
        assert str(real_root) in (add_direct.source_header or "")
        assert str(symlink_root) not in (add_direct.source_header or "")

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

        # The closure-bearing declarations sit BELOW the inserted
        # comments/blank lines, so their line:col actually shifts relative
        # to _HEADER -- this is the exact #868 shape (a lambda's identity
        # embeds its own source line, so unrelated earlier drift changes
        # it), and is what makes this test a genuine exercise of the
        # taint-handling fix rather than a vacuous pass on declarations
        # whose identity never depended on line number to begin with
        # (Codex review, PR #898).
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
template <class F> inline int invoke_with(F f) { return f(); }
inline int uses_lambda() { return invoke_with([]() { return 7; }); }
template <class F> class Guard {
public:
    explicit Guard(F f) : fn_(f) {}
    ~Guard() {}
    int run() { return fn_(); }
private:
    F fn_;
};
inline int uses_guard() { Guard g([]() { return 13; }); return g.run(); }
}
"""
        so_a, header_a = _build(root_a, _HEADER, _SOURCE)
        so_b, header_b = _build(root_b, noisy_header, _SOURCE)

        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        _assert_exercises_closure_taint(snap_a)
        _assert_exercises_closure_taint(snap_b)
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

        # The closure-bearing declarations moved to the FRONT (a different
        # position than in _HEADER, hence a different line:col too), so
        # this exercises the same taint mechanism as the line-drift case
        # above under a reordering rather than an insertion (Codex review,
        # PR #898).
        reordered_header = """
#pragma once
namespace lib {
template <class F> inline int invoke_with(F f) { return f(); }
inline int uses_lambda() { return invoke_with([]() { return 7; }); }
template <class F> class Guard {
public:
    explicit Guard(F f) : fn_(f) {}
    ~Guard() {}
    int run() { return fn_(); }
private:
    F fn_;
};
inline int uses_guard() { Guard g([]() { return 13; }); return g.run(); }
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

        _assert_exercises_closure_taint(snap_a)
        _assert_exercises_closure_taint(snap_b)
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
        # genuinely distinct CANONICAL closure identities within the SAME
        # snapshot -- not merely "have different linker-mangled symbols",
        # which is trivially true regardless of any identity bug here
        # (run_one/run_two are themselves different enclosing functions, so
        # real Itanium mangling already guarantees distinct symbols on its
        # own), and not merely "the set has >= 2 entries", which the bare,
        # uninstantiated generic template pattern (mangled == "call_with",
        # no lambda at all) can also satisfy on its own. What must actually
        # stay distinct is the RENUMBERED parameter-type spelling dump()
        # already produces (production wiring: dumper.dump() calls
        # renumber_anonymous_closure_identities before returning) -- a
        # regression that rewrote BOTH specializations' parameter types to
        # the SAME ordinal (e.g. both "(lambda:api.h#1)") would pass a
        # bare mangled-symbol-count check while silently over-merging the
        # two closures' identity (Codex review, PR #898).
        def _lambda_param_spellings(functions: object) -> set[str]:
            return {
                p.type
                for f in functions  # type: ignore[attr-defined]
                if "call_with" in f.mangled
                for p in f.params
                if p.type and "(lambda" in p.type
            }

        lambda_spellings = _lambda_param_spellings(snap.functions)
        assert len(lambda_spellings) >= 2, (
            "expected two distinct call_with<lambda> canonical parameter "
            f"identities, got: {lambda_spellings}"
        )

        # Now relocate the whole checkout and confirm the SAME two
        # instantiations are still distinct from each other post-move
        # (not merged into one by the relocation), while the snapshot as a
        # whole still compares NO_CHANGE against its own un-relocated self.
        relocated_root = tmp_path / "relocated" / "checkout"
        relocated_root.mkdir(parents=True)
        so2, header2 = _build(relocated_root, header_text, source_text)
        snap2 = _dump(so2, header2)

        relocated_lambda_spellings = _lambda_param_spellings(snap2.functions)
        assert len(relocated_lambda_spellings) >= 2

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

    # The two tests above only apply the RELOCATION transformation to the
    # negative-control fixtures. Since neither fixture's declaration order
    # changes under relocation, they can't distinguish "distinct identities
    # stay distinct" from "distinct identities were never at risk of
    # merging in the first place" -- the Phase 4 plan asks for the
    # distinct-entity control under every transformation, and the two
    # transformations that could plausibly make an ordinal-assignment bug
    # over-merge two distinct closures (unrelated line drift, reordering)
    # were untested here (Codex review, PR #898).

    def test_two_lambdas_stay_distinct_across_unrelated_line_drift(
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
        drifted_header = """
#pragma once
// An unrelated comment inserted before everything below.


namespace lib {
// Another unrelated comment.
template <class F> int call_with(F f) { return f(); }
inline int run_one() { return call_with([]() { return 1; }); }
inline int run_two() { return call_with([]() { return 2; }); }
}
"""
        source_text = """
#include "api.h"
namespace lib { int touch() { return run_one() + run_two(); } }
"""

        def _lambda_param_spellings(functions: object) -> set[str]:
            return {
                p.type
                for f in functions  # type: ignore[attr-defined]
                if "call_with" in f.mangled
                for p in f.params
                if p.type and "(lambda" in p.type
            }

        root_a = tmp_path / "plain"
        root_b = tmp_path / "with_drift"
        root_a.mkdir()
        root_b.mkdir()
        so_a, header_a = _build(root_a, header_text, source_text)
        so_b, header_b = _build(root_b, drifted_header, source_text)
        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        assert len(_lambda_param_spellings(snap_a.functions)) >= 2
        assert len(_lambda_param_spellings(snap_b.functions)) >= 2

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []

    def test_two_lambdas_stay_distinct_after_reordering(self, tmp_path: Path) -> None:
        """Reordering two SAME-KIND lambdas RELATIVE TO EACH OTHER is a
        documented, accepted limitation of the ordinal-renumbering fix
        itself, not a NO_CHANGE case:
        qualified_name_segments.py's own module docstring states the scope
        boundary explicitly -- "As long as an edit doesn't reorder or
        add/remove same-header, same-kind lambdas relative to each other,
        both sides of a comparison assign the identical ordinal to the
        identical closure." Swapping run_one/run_two's declaration order
        swaps which one gets ordinal #1 vs #2 -- and since compare()
        matches functions by their real, order-independent mangled symbol
        (stable across reordering, since it encodes the enclosing
        function's own name), the matched pair's ORDINAL-derived parameter
        spelling genuinely differs, producing a real (accepted) finding.
        This test pins BOTH halves: the two closures never collapse into
        ONE shared identity within either single snapshot (the actual
        negative-control invariant), while confirming compare() reports
        this documented boundary honestly rather than silently swallowing
        it as NO_CHANGE (Codex review, PR #898 -- confirmed empirically:
        asserting NO_CHANGE here fails against the real fix, as expected)."""
        _require_toolchain()
        header_text = """
#pragma once
namespace lib {
template <class F> int call_with(F f) { return f(); }
inline int run_one() { return call_with([]() { return 1; }); }
inline int run_two() { return call_with([]() { return 2; }); }
}
"""
        reordered_header = """
#pragma once
namespace lib {
template <class F> int call_with(F f) { return f(); }
inline int run_two() { return call_with([]() { return 2; }); }
inline int run_one() { return call_with([]() { return 1; }); }
}
"""
        source_text = """
#include "api.h"
namespace lib { int touch() { return run_one() + run_two(); } }
"""

        def _lambda_param_spellings(functions: object) -> set[str]:
            return {
                p.type
                for f in functions  # type: ignore[attr-defined]
                if "call_with" in f.mangled
                for p in f.params
                if p.type and "(lambda" in p.type
            }

        root_a = tmp_path / "original_order"
        root_b = tmp_path / "reordered"
        root_a.mkdir()
        root_b.mkdir()
        so_a, header_a = _build(root_a, header_text, source_text)
        so_b, header_b = _build(root_b, reordered_header, source_text)
        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        spellings_a = _lambda_param_spellings(snap_a.functions)
        spellings_b = _lambda_param_spellings(snap_b.functions)
        assert len(spellings_a) >= 2
        assert len(spellings_b) >= 2

        # The documented, accepted limitation: reordering swaps which
        # closure gets which ordinal, so the two sides' matched-by-mangled-
        # symbol pair genuinely differs -- a real finding, not NO_CHANGE.
        result = compare(snap_a, snap_b)
        assert result.verdict is not Verdict.NO_CHANGE
        assert result.changes != []

    def test_two_lambdas_stay_distinct_when_an_unrelated_declaration_moves(
        self, tmp_path: Path
    ) -> None:
        """The genuinely untested transformation the test above cannot
        cover: reordering an UNRELATED declaration around the two lambdas
        while their own relative order to EACH OTHER stays fixed -- unlike
        swapping run_one/run_two themselves, this does not touch the
        documented same-kind-reorder limitation at all, so it must both
        keep the two closures distinct AND compare as NO_CHANGE (Codex
        review, PR #898 -- the previous test's swap of run_one/run_two
        was a different, already-covered case)."""
        _require_toolchain()
        header_text = """
#pragma once
namespace lib {
int unrelated_fn(int x);
template <class F> int call_with(F f) { return f(); }
inline int run_one() { return call_with([]() { return 1; }); }
inline int run_two() { return call_with([]() { return 2; }); }
}
"""
        reordered_header = """
#pragma once
namespace lib {
template <class F> int call_with(F f) { return f(); }
inline int run_one() { return call_with([]() { return 1; }); }
inline int run_two() { return call_with([]() { return 2; }); }
int unrelated_fn(int x);
}
"""
        source_text = """
#include "api.h"
namespace lib {
int unrelated_fn(int x) { return x; }
int touch() { return run_one() + run_two(); }
}
"""

        def _lambda_param_spellings(functions: object) -> set[str]:
            return {
                p.type
                for f in functions  # type: ignore[attr-defined]
                if "call_with" in f.mangled
                for p in f.params
                if p.type and "(lambda" in p.type
            }

        root_a = tmp_path / "original_order"
        root_b = tmp_path / "reordered"
        root_a.mkdir()
        root_b.mkdir()
        so_a, header_a = _build(root_a, header_text, source_text)
        so_b, header_b = _build(root_b, reordered_header, source_text)
        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        assert len(_lambda_param_spellings(snap_a.functions)) >= 2
        assert len(_lambda_param_spellings(snap_b.functions)) >= 2

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []

    def test_nested_records_stay_distinct_across_unrelated_line_drift(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        header_text = """
#pragma once
namespace api { struct Outer { struct Inner { int a; }; }; }
namespace detail { struct Outer { struct Inner { int b; long c; }; }; }
"""
        drifted_header = """
#pragma once
// An unrelated comment inserted before everything below.


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

        def _inner_qualified(types: object) -> set[str]:
            return {
                t.qualified_name
                for t in types  # type: ignore[attr-defined]
                if t.name == "Inner" and t.qualified_name
            }

        root_a = tmp_path / "plain"
        root_b = tmp_path / "with_drift"
        root_a.mkdir()
        root_b.mkdir()
        so_a, header_a = _build(root_a, header_text, source_text)
        so_b, header_b = _build(root_b, drifted_header, source_text)
        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        assert len(_inner_qualified(snap_a.types)) >= 2
        assert len(_inner_qualified(snap_b.types)) >= 2

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []

    def test_nested_records_stay_distinct_after_reordering(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        header_text = """
#pragma once
namespace api { struct Outer { struct Inner { int a; }; }; }
namespace detail { struct Outer { struct Inner { int b; long c; }; }; }
"""
        reordered_header = """
#pragma once
namespace detail { struct Outer { struct Inner { int b; long c; }; }; }
namespace api { struct Outer { struct Inner { int a; }; }; }
"""
        source_text = """
#include "api.h"
namespace lib {
int touch_api(api::Outer::Inner x) { return x.a; }
long touch_detail(detail::Outer::Inner x) { return x.c; }
}
"""

        def _inner_qualified(types: object) -> set[str]:
            return {
                t.qualified_name
                for t in types  # type: ignore[attr-defined]
                if t.name == "Inner" and t.qualified_name
            }

        root_a = tmp_path / "original_order"
        root_b = tmp_path / "reordered"
        root_a.mkdir()
        root_b.mkdir()
        so_a, header_a = _build(root_a, header_text, source_text)
        so_b, header_b = _build(root_b, reordered_header, source_text)
        snap_a = _dump(so_a, header_a)
        snap_b = _dump(so_b, header_b)

        assert len(_inner_qualified(snap_a.types)) >= 2
        assert len(_inner_qualified(snap_b.types)) >= 2

        result = compare(snap_a, snap_b)
        assert result.verdict is Verdict.NO_CHANGE
        assert result.changes == []


class TestFindingIdentityIsCheckoutPathInvariant:
    """Every positive case above compares semantically identical libraries
    and asserts an EMPTY ``changes`` list -- the registry's own
    ``report_canonical_finding_id`` invariant (used for cross-run/
    cross-producer suppression matching) is otherwise never exercised at
    all here: checkout-path taint could leak into a REAL finding's own
    canonical identity while every other test in this module stayed green
    (Codex review, PR #898). This introduces one genuine, controlled ABI
    difference -- a second lambda-parameterized ``invoke_with<...>``
    instantiation added on the "new" side -- and confirms the SAME
    canonical finding id is produced whether the before/after pair is
    built under one checkout root or a completely different one."""

    def test_added_lambda_instantiation_gets_the_same_canonical_id_under_different_roots(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        new_header = _HEADER.replace(
            "inline int uses_lambda() { return invoke_with([]() { return 7; }); }",
            "inline int uses_lambda() { return invoke_with([]() { return 7; }); }\n"
            "inline int uses_lambda2() { return invoke_with([]() { return 9; }); }",
        )
        new_source = _SOURCE.replace(
            "int touch_lambda() { return uses_lambda(); }",
            "int touch_lambda() { return uses_lambda(); }\n"
            "int touch_lambda2() { return uses_lambda2(); }",
        )
        assert new_header != _HEADER and new_source != _SOURCE  # sanity

        def _old_new_snapshots(root: Path) -> tuple[object, object]:
            old_root = root / "old"
            new_root = root / "new"
            old_root.mkdir(parents=True)
            new_root.mkdir(parents=True)
            so_old, header_old = _build(old_root, _HEADER, _SOURCE)
            so_new, header_new = _build(new_root, new_header, new_source)
            return _dump(so_old, header_old), _dump(so_new, header_new)

        old_a, new_a = _old_new_snapshots(tmp_path / "checkout_a")
        old_b, new_b = _old_new_snapshots(tmp_path / "an/unrelated/deeper/checkout_b")

        result_a = compare(old_a, new_a)
        result_b = compare(old_b, new_b)

        from abicheck.finding_identity import report_canonical_finding_id

        def _added_lambda_canonical_ids(changes: object) -> set[str]:
            candidates = [
                c
                for c in changes  # type: ignore[attr-defined]
                if c.symbol and "uses_lambda2" in c.symbol
            ]
            assert candidates, (
                "expected at least one finding naming the added "
                f"uses_lambda2 symbol, got: "
                f"{[c.symbol for c in changes]}"  # type: ignore[attr-defined]
            )
            return {report_canonical_finding_id(c) for c in candidates}

        ids_a = _added_lambda_canonical_ids(result_a.changes)
        ids_b = _added_lambda_canonical_ids(result_b.changes)

        assert ids_a == ids_b, (
            "the same controlled ABI addition produced different canonical "
            f"finding ids purely from a different checkout root: {ids_a} "
            f"vs {ids_b}"
        )

    def test_type_bearing_finding_gets_the_same_canonical_id_under_different_roots(
        self, tmp_path: Path
    ) -> None:
        """The test above doesn't actually exercise this fix's own
        renumbering mechanism: FUNC_ADDED/UNNAMED_TYPE_IN_PUBLIC_ABI are
        both matched by a real, compiler-assigned mangled symbol -- or an
        Itanium-native ``{lambda()#N}`` demangled discriminator, which is a
        structural ordinal the compiler itself assigns, unrelated to this
        repo's own ``renumber_anonymous_closure_identities`` -- both
        inherently checkout-path-invariant on their own, with or without
        this fix (Codex review, PR #898). A genuinely TYPE-bearing finding
        kind instead folds ``old_value``/``new_value`` VERBATIM into its
        canonical id (``report_canonical_finding_id``'s NORMALIZED tier,
        per its own docstring, for any kind outside
        ``_EQUIVALENT_CHANGE_CATEGORIES``) -- so it is THIS fix's
        renumbered ``Param.type``/``RecordType.qualified_name`` spelling,
        not a real ELF symbol, whose checkout-path-invariance actually
        matters for such a kind's identity to be stable.

        The direct-clang backend used elsewhere in this module doesn't
        emit a closure as its own standalone ``RecordType`` (confirmed
        empirically -- ``snap.types`` carries no closure entry at all, only
        ``Function.params[*].type`` embeds the ``(lambda:...)`` spelling),
        so a genuine end-to-end ``compare()`` can't be coerced into
        producing a real TYPE-bearing finding for a closure without
        castxml (a separate backend, unavailable in this sandbox -- see
        this module's own docstring for the castxml/clang split). This
        builds the ``Change`` directly instead -- but from a REAL,
        dump()-produced parameter-type spelling (not a hand-built AST
        fragment the way the pre-existing seed tests for this class do),
        obtained from the SAME real toolchain the rest of this module
        uses, under two different checkout roots. Since
        ``renumber_anonymous_closure_identities`` already runs
        automatically inside ``dump()`` (production wiring) before any
        ``Change`` is ever constructed, this is exactly the value a real
        finding's ``old_value``/``new_value`` would carry in production --
        confirming that value is checkout-path-invariant is precisely
        confirming this fix's own effect on finding-identity stability."""
        _require_toolchain()

        # Root A compiles _HEADER as-is; root B compiles a semantics-
        # preserving variant with unrelated comments/blank lines inserted
        # BEFORE the closure, shifting its line number -- varying the raw
        # coordinates the ordinal-renumbering mechanism (not just the
        # checkout-path-stripping one) exists to neutralize (Codex review,
        # PR #898: comparing the SAME header/line/col under two roots would
        # still pass this assertion even if ordinal renumbering itself
        # stopped running, since only checkout-path taint -- not line
        # drift -- would differ between the two calls in that case).
        drifted_header = _HEADER.replace(
            "namespace lib {",
            "namespace lib {\n// unrelated inserted comment\n\n// another one\n",
            1,
        )
        assert drifted_header != _HEADER  # sanity: a real, non-trivial edit
        assert drifted_header.count("\n") != _HEADER.count("\n")  # line count differs

        def _closure_param_spelling(root: Path, header_text: str) -> str:
            root.mkdir(parents=True)
            so, header = _build(root, header_text, _SOURCE)
            snap = _dump(so, header)
            for f in snap.functions:  # type: ignore[attr-defined]
                for p in f.params:
                    if p.type and "(lambda" in p.type:
                        return p.type
            raise AssertionError(
                "expected a lambda-parameterized invoke_with<...> "
                "instantiation in the snapshot"
            )

        spelling_a = _closure_param_spelling(tmp_path / "checkout_a", _HEADER)
        spelling_b = _closure_param_spelling(
            tmp_path / "an/unrelated/deeper/checkout_b", drifted_header
        )

        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.finding_identity import report_canonical_finding_id

        def _template_param_change(spelling: str) -> Change:
            return Change(
                kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
                symbol="lib::invoke_with",
                description=f"parameter type changed to {spelling}",
                old_value="F",
                new_value=spelling,
            )

        id_a = report_canonical_finding_id(_template_param_change(spelling_a))
        id_b = report_canonical_finding_id(_template_param_change(spelling_b))
        assert id_a == id_b, (
            "a type-bearing finding's canonical id differed purely from "
            f"checkout root: {spelling_a!r} -> {id_a} vs {spelling_b!r} -> "
            f"{id_b}"
        )
