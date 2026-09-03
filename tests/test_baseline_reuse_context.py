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

"""The baseline-context-reuse rule, tested as a primitive (PR 3A blocker 6).

`service_input_resolution.BaselineReuseContext` /
`resolve_baseline_compile_context` is the pair-shaped decision `scan --against`
has to make: may the candidate's own P0.3 L3->L2 folded `CompileContext` also
parse the baseline, or must the baseline fall back to the caller's plain,
unfolded one?

Until this slice it was a four-clause boolean inline in
`scan_engine.run_scan_core`, and the root `AGENTS.md`'s L3->L2-fold entry
records three separate review rounds correcting it (findings 12, 13 and 15):
each fix was written against the one input the previous round's reviewer had
named, and each left a neighbouring input shape wrong. AGENTS.md's own
"Primitive-level property tests" guidance is explicit about what that history
calls for -- "a small, standalone property-test class stating its contract as
invariants, decoupled from any one caller's domain logic" -- so the contract
is stated here directly against the primitive, not only through `scan`.

The three invariants:

* **Nothing to decide** -- with no hint (a lone dump/compare side) the folded
  context is simply this side's own.
* **Same scope reuses** -- an absent *or content-equal* baseline scope reuses
  the fold. Content, not truthiness: that distinction is finding 13, where
  gating on truthiness dropped the fold for the ordinary shared `-H api.h`
  case and reintroduced the very `NOT_COMPARABLE` bug the fold exists to
  prevent.
* **Either axis diverging falls back** -- headers and includes are built
  independently by `cli_scan`, so neither axis may be checked alone (finding
  15).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.compile_context import CompileContext
from abicheck.service_input_resolution import (
    BaselineReuseContext,
    resolve_baseline_compile_context,
)

FOLDED = CompileContext(gcc_options="-std=c++17 -DFOLDED=1")
UNFOLDED = CompileContext(gcc_options="")

HDRS = [Path("api.h")]
INCS = [Path("build/include")]


def _resolve(hint: BaselineReuseContext | None) -> CompileContext | None:
    return resolve_baseline_compile_context(
        hint, folded=FOLDED, unfolded=UNFOLDED, headers=HDRS, effective_includes=INCS
    )


class TestBaselineReuseContract:
    def test_no_hint_means_nothing_to_decide(self) -> None:
        assert _resolve(None) is FOLDED

    def test_absent_baseline_scope_reuses_the_fold(self) -> None:
        """No `-H old=`/`-I old=` at all: the baseline reuses the candidate's."""
        assert _resolve(BaselineReuseContext()) is FOLDED

    def test_shared_bare_header_reuses_the_fold(self) -> None:
        """Finding 13, pinned: content equality, not truthiness.

        `cli_scan` builds `baseline_header = header_both + header_old`, so a
        bare shared `-H api.h` makes the baseline list truthy and *identical*.
        A truthiness gate drops the fold here -- for the most common
        invocation there is.
        """
        hint = BaselineReuseContext(baseline_headers=(Path("api.h"),))
        assert _resolve(hint) is FOLDED

    def test_diverging_headers_fall_back_to_the_unfolded_context(self) -> None:
        """A real `-H old=PATH` override: no old-side fold exists to derive."""
        hint = BaselineReuseContext(baseline_headers=(Path("old/api.h"),))
        assert _resolve(hint) is UNFOLDED

    def test_diverging_includes_fall_back_even_with_shared_headers(self) -> None:
        """Finding 15, pinned: the include axis is checked independently.

        `-H api.h -I old=old-build -I new=new-build` shares one header list
        while routing each side through a different include tree, so a
        header-only check forwards the new build's `-D`/`-std`/sysroot flags
        into a parse of the old binary.
        """
        hint = BaselineReuseContext(
            baseline_headers=(Path("api.h"),),
            baseline_includes=(Path("old-build/include"),),
        )
        assert _resolve(hint) is UNFOLDED

    def test_matching_includes_with_shared_headers_reuse_the_fold(self) -> None:
        hint = BaselineReuseContext(
            baseline_headers=(Path("api.h"),),
            baseline_includes=(Path("build/include"),),
        )
        assert _resolve(hint) is FOLDED

    @pytest.mark.parametrize(
        ("headers", "includes"),
        [
            ((), ()),
            ((Path("api.h"),), ()),
            ((), (Path("build/include"),)),
            ((Path("api.h"),), (Path("build/include"),)),
        ],
    )
    def test_reuse_is_exactly_the_predicate(
        self, headers: tuple[Path, ...], includes: tuple[Path, ...]
    ) -> None:
        """The resolver never disagrees with its own predicate.

        Stated as an invariant rather than case-by-case so a future change
        that adds a third axis to one and not the other fails immediately.
        """
        hint = BaselineReuseContext(
            baseline_headers=headers, baseline_includes=includes
        )
        reusable = hint.folded_context_is_reusable(
            headers=HDRS, effective_includes=INCS
        )
        assert (_resolve(hint) is FOLDED) is reusable

    def test_order_matters_because_include_search_order_matters(self) -> None:
        """Two lists with the same *members* in a different order diverge.

        Deliberate, not an oversight: an include search path is
        first-match-wins, so a reordered `-I` chain is a genuinely different
        resolution, not an equivalent one. Pinned so a future "compare as
        sets" simplification has to argue with this test rather than pass
        silently.
        """
        hint = BaselineReuseContext(
            baseline_includes=(Path("b"), Path("a")),
        )
        assert resolve_baseline_compile_context(
            hint,
            folded=FOLDED,
            unfolded=UNFOLDED,
            headers=HDRS,
            effective_includes=[Path("a"), Path("b")],
        ) is UNFOLDED


class TestScanRoutesThroughTheSharedRule:
    """`run_scan_core` must not keep a second copy of the decision.

    Pinned behaviourally, not by matching `run_scan_core`'s source text (which
    an earlier revision of this class did, and which says nothing about what
    actually reaches the baseline parse): since CLI cleanup phase two's PR 3A,
    `_build_new_snapshot` routes through the shared resolver and the answer
    arrives already computed on `SideResolution.baseline_compile_context`. So
    `run_scan_core`'s remaining responsibilities are exactly two, and both are
    checked here through a real `scan --against` invocation:

    1. it hands the *resolved* old-side header/include scopes in as the
       `baseline_reuse_hint` (so the shared rule decides against the real
       scopes, not against the raw CLI values), and
    2. it forwards the resolution's own answer to `_run_baseline_compare`
       rather than deriving a second one from the values it got back.

    A sentinel object is used for the answer precisely so a re-derivation
    cannot accidentally reproduce it: nothing but forwarding can return it.
    """

    def _run(self, tmp_path: Path, monkeypatch, extra_args: list[str]) -> dict:
        import json

        from click.testing import CliRunner

        from abicheck import scan_engine as cs
        from abicheck.cli import main
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol
        from abicheck.model import AbiSnapshot, Function
        from abicheck.serialization import snapshot_to_dict
        from abicheck.service_input_resolution import SideResolution

        def _snap(version: str) -> AbiSnapshot:
            return AbiSnapshot(
                library="libfoo.so",
                version=version,
                from_headers=True,
                functions=[Function(name="foo", mangled="_Z3foov", return_type="void")],
                elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
            )

        old = tmp_path / "old.abi.json"
        old.write_text(json.dumps(snapshot_to_dict(_snap("1.0"))), encoding="utf-8")
        new = tmp_path / "new.abi.json"
        new.write_text(json.dumps(snapshot_to_dict(_snap("2.0"))), encoding="utf-8")

        sentinel = object()
        seen: dict = {}

        def _fake_build_new_snapshot(*_a, **kw):
            seen["hint"] = kw.get("baseline_reuse_hint")
            return SideResolution(
                snapshot=_snap("2.0"),
                effective_includes=(),
                effective_compile_context=CompileContext(gcc_options="-DCANDIDATE=1"),
                baseline_compile_context=sentinel,
            )

        def _fake_baseline_compare(*_a, **kw):
            seen["forwarded"] = kw.get("compile_context")
            return (
                "compatible",
                0,
                {"breaking": 0, "api_break": 0, "risk": 0, "compatible": 0},
            )

        monkeypatch.setattr(cs, "_build_new_snapshot", _fake_build_new_snapshot)
        monkeypatch.setattr(cs, "_run_baseline_compare", _fake_baseline_compare)

        result = CliRunner().invoke(
            main, ["scan", str(new), "--against", str(old), *extra_args]
        )
        assert result.exit_code == 0, result.output
        seen["sentinel"] = sentinel
        return seen

    def test_the_resolutions_own_answer_is_what_reaches_the_baseline_parse(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        seen = self._run(tmp_path, monkeypatch, [])
        assert seen["forwarded"] is seen["sentinel"], (
            "run_scan_core derived its own baseline compile context instead of "
            "forwarding the one the shared resolver already decided"
        )

    def test_the_hint_carries_the_resolved_old_side_scopes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        old_hdr = tmp_path / "old.h"
        old_hdr.write_text("int f(void);\n", encoding="utf-8")
        old_inc = tmp_path / "oldinc"
        old_inc.mkdir()

        seen = self._run(
            tmp_path,
            monkeypatch,
            ["-H", f"old={old_hdr}", "-I", f"old={old_inc}"],
        )
        hint = seen["hint"]
        assert isinstance(hint, BaselineReuseContext)
        assert old_hdr in hint.baseline_headers
        assert old_inc in hint.baseline_includes


class TestSharedPrimitiveAcceptsTheHint:
    """`_resolve_side_snapshot_impl`'s optional `baseline_reuse_hint`.

    The extension point blocker 6 asks for: a *narrow, opt-in* parameter on
    the shared per-input resolver, deliberately not a widening of its general
    single-input contract. These exercise it end to end through the real
    function (with the extraction stubbed) rather than only asserting the
    parameter exists, and pin the "no hint changes nothing" direction, which
    is what makes it safe for every caller that never passes one.
    """

    def _resolution(self, monkeypatch, tmp_path: Path, hint):
        # ADR-061 Phase 3: patch the implementation owner
        # (`workflows.artifact.execute`), not the `service_input_resolution`
        # facade -- `_resolve_side_snapshot_impl` reads the owner's own module
        # global, so a patch on the facade would leave the real seed running.
        from abicheck import service
        from abicheck.api_types import InputSpec
        from abicheck.model import AbiSnapshot
        from abicheck.service_compare_evidence import SideEvidence
        from abicheck.workflows.artifact import execute as sir

        hdr = tmp_path / "api.h"
        hdr.write_text("int f(void);\n", encoding="utf-8")

        monkeypatch.setattr(
            sir,
            "_seeded_includes_and_compile_context",
            lambda *_a, **_k: ([Path("build/include")], FOLDED, True, []),
        )
        monkeypatch.setattr(
            service,
            "resolve_input",
            lambda *_a, **_k: AbiSnapshot(
                library="lib", version="1", from_headers=True
            ),
        )
        side = InputSpec(path=tmp_path / "lib.so", headers=(hdr,), compile=UNFOLDED)
        evidence = SideEvidence(
            headers=[hdr],
            compile=UNFOLDED,
            collect_mode="off",
            dump_manifest=None,
        )
        return sir._resolve_side_snapshot_impl(
            side,
            evidence,
            lang="c++",
            header_backend="auto",
            fmt="elf",
            public_headers=[hdr],
            public_header_dirs=[],
            baseline_reuse_hint=hint,
        )

    def test_without_a_hint_the_baseline_context_is_this_side_s_own(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        resolution = self._resolution(monkeypatch, tmp_path, None)
        assert resolution.effective_compile_context is FOLDED
        assert resolution.baseline_compile_context is FOLDED

    def test_a_diverging_hint_reports_the_unfolded_context(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        hint = BaselineReuseContext(baseline_headers=(Path("old/api.h"),))
        resolution = self._resolution(monkeypatch, tmp_path, hint)
        # The side's own resolution is untouched -- only the *baseline*
        # answer changes, which is the whole point of an opt-in hint.
        assert resolution.effective_compile_context is FOLDED
        assert resolution.baseline_compile_context is UNFOLDED
