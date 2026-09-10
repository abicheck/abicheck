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

"""An evidence-tier shortcut may not be taken when its substitute is absent.

Bug class ``evidence.tier_shortcut_without_substitute``
(``tests/regressions/manifest.py``). ``scan`` reduces DWARF to a presence
check at the ``HEADERS``/``BUILD`` rungs on the reasoning that the header AST
supplies type layout instead. Applied unconditionally, a run given *no*
headers reaches neither source: the header parse has nothing to read and
DWARF was already dropped, so every type-level finding disappears while the
report still names the rung that was asked for.

Two levels of guard, because the primitive and the behaviour can regress
independently:

* :class:`TestDebugShortcutProperties` states the primitive's contract as an
  exhaustive enumeration over the whole ``EvidenceDepth`` domain, against an
  oracle ("never defer to evidence that is not there") that is not the
  implementation's own membership test.
* :class:`TestPerSideHeaderEvidence` pins that the answer is per side. The
  same shortcut applied run-wide does not equalise the two snapshots' evidence
  tiers --- it only decides which side gets starved, and a starved *new* side
  reads as a wholesale removal.
* :class:`TestHeaderlessScanMatchesCompare` states the user-visible half
  against an *independent implementation* — ``compare``, which never applies
  this shortcut — over several independently-chosen ABI changes, every
  ``--depth`` a header-less pair can actually run, with and without a
  provenance-only ``--public-header-dir``, and once with headers on the
  baseline side alone: four axes rather than the single unpinned invocation
  that surfaced the bug.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_scan_helpers import (
    _uses_debug_presence_only,
    scan_debug_presence_only,
)
from abicheck.model.evidence_depth_levels import EvidenceDepth


class TestDebugShortcutProperties:
    """The primitive's contract, over its entire input domain."""

    def test_the_shortcut_is_never_taken_without_the_evidence_it_defers_to(
        self,
    ) -> None:
        """No rung may drop DWARF when there is no header evidence to replace it.

        Exhaustive over ``EvidenceDepth`` rather than the one rung the bug was
        reported at, so a rung added later cannot join the shortcut set without
        this failing.
        """
        offenders = [
            depth.value
            for depth in EvidenceDepth
            if _uses_debug_presence_only(depth, has_header_evidence=False)
        ]
        assert offenders == [], (
            "these rungs still defer DWARF to a header parse that has no "
            f"headers to read: {offenders}"
        )

    def test_header_evidence_can_only_ever_enable_the_shortcut(self) -> None:
        """Supplying headers never *removes* the shortcut from a rung.

        The monotonicity half: the flag is a permission, not a mode switch, so
        for every rung the with-headers answer implies-or-equals the
        without-headers one. Stated as an ordering rather than as the expected
        membership set, so it does not restate the implementation.
        """
        for depth in EvidenceDepth:
            without = _uses_debug_presence_only(depth, has_header_evidence=False)
            with_ = _uses_debug_presence_only(depth, has_header_evidence=True)
            assert without <= with_, depth.value

    def test_at_least_one_rung_still_takes_the_shortcut_when_headers_exist(
        self,
    ) -> None:
        """The guard above must not be satisfiable by disabling the shortcut.

        Without this, deleting the optimisation outright would pass every other
        assertion in this class.
        """
        assert any(
            _uses_debug_presence_only(depth, has_header_evidence=True)
            for depth in EvidenceDepth
        )


class TestPerSideHeaderEvidence:
    """Each side's shortcut is answered from that side's own header inputs."""

    def test_a_side_with_headers_may_take_the_shortcut(self) -> None:
        assert (
            scan_debug_presence_only(EvidenceDepth.HEADERS, (Path("inc/foo.h"),))
            is True
        )

    def test_a_side_without_headers_may_not(self) -> None:
        assert scan_debug_presence_only(EvidenceDepth.HEADERS, ()) is False

    def test_the_rung_still_governs_regardless_of_header_input(self) -> None:
        """Header evidence permits the shortcut; it never imposes it.

        ``SOURCE`` is not a rung that defers DWARF to a header parse, so no
        amount of header input may turn the shortcut on there.
        """
        assert (
            scan_debug_presence_only(EvidenceDepth.SOURCE, (Path("inc/foo.h"),))
            is False
        )

    def test_the_predicate_sees_one_side_only(self) -> None:
        """Structural guard against re-widening this to a run-wide answer.

        A revision that answered it once for the whole run starved whichever
        side lacked headers while the other got a header AST, which reads as a
        wholesale removal (see the function's own docstring). Pinning the arity
        makes reintroducing a second side's headers a visible signature change
        rather than a silent widening.
        """
        import inspect

        assert set(inspect.signature(scan_debug_presence_only).parameters) == {
            "depth",
            "side_headers",
        }


#: Independently-chosen ABI changes, each detectable only from type evidence
#: (DWARF here, since no headers are supplied) except the symbol-table one,
#: which anchors the pair as detectable at all.
_CASES: dict[str, tuple[str, str]] = {
    "record_field_added": (
        'struct Cfg { int a; };\nextern "C" int use(struct Cfg *c) { return c->a; }\n',
        "struct Cfg { int a; int b; };\n"
        'extern "C" int use(struct Cfg *c) { return c->a; }\n',
    ),
    "record_field_type_widened": (
        "struct Box { int v; };\n"
        'extern "C" int read_box(struct Box *b) { return b->v; }\n',
        "struct Box { long long v; };\n"
        'extern "C" int read_box(struct Box *b) { return (int)b->v; }\n',
    ),
    "enum_member_added": (
        'enum Mode { A, B };\nextern "C" int pick(enum Mode m) { return (int)m; }\n',
        'enum Mode { A, B, C };\nextern "C" int pick(enum Mode m) { return (int)m; }\n',
    ),
    "symbol_removed": (
        'extern "C" void keep(void) {}\nextern "C" void drop(void) {}\n',
        'extern "C" void keep(void) {}\n',
    ),
}


def _has_debug_evidence(payload: dict) -> bool:
    """Whether the scanned candidate actually carried DWARF/PDB debug info.

    Read from the report's own `L1_debug` coverage row, not inferred from the
    platform: what matters to the shortcut is whether a debug fallback exists
    for this operand, and a stripped Linux build has no more of one than an
    MSVC-built DLL does.
    """
    for row in payload.get("coverage") or ():
        if row.get("layer") == "L1_debug":
            return bool(row.get("status") == "present")
    return False


def _verdict(payload: dict) -> str | None:
    """The compatibility verdict, wherever the format happens to carry it."""
    run_outcome = payload.get("run_outcome") or {}
    return run_outcome.get("compatibility") or payload.get("verdict")


@pytest.mark.integration
class TestHeaderlessScanMatchesCompare:
    """``scan`` and ``compare`` must see the same ABI, given the same evidence.

    ``compare`` is the oracle: it resolves the same two binaries without ever
    taking the DWARF shortcut, so any rung at which ``scan`` reports less is
    ``scan`` discarding evidence rather than the pair genuinely lacking it.

    ``--depth binary`` is deliberately **not** in the matrix, and not because
    it passes. The two commands really do diverge there --- ``scan`` extracts
    symbols only, while ``compare --depth binary`` still reads DWARF and
    reports ``type_size_changed`` --- but that is a question about whether
    ``compare``'s binary rung honours its own pin, not about this shortcut,
    and it predates ADR-068 Phase 4 on both sides. Recorded as a known gap on
    the ``evidence.tier_shortcut_without_substitute`` entry in
    ``tests/regressions/manifest.py`` rather than folded into this fix.
    """

    @staticmethod
    def _libs(tmp_path: Path, case: str) -> tuple[Path, Path]:
        from tests._libabigail import compile_shared_lib

        v1, v2 = _CASES[case]
        old = tmp_path / "libcase.so.1"
        new = tmp_path / "libcase.so.2"
        compile_shared_lib(v1, old, lang="cpp", soname="libcase.so.1")
        compile_shared_lib(v2, new, lang="cpp", soname="libcase.so.1")
        return old, new

    @staticmethod
    def _run(runner: CliRunner, argv: list[str], out: Path) -> tuple[int, dict]:
        res = runner.invoke(main, [*argv, "--format", "json", "-o", str(out)])
        assert res.exit_code in (0, 1, 2, 4), res.output
        return res.exit_code, json.loads(out.read_text())

    @pytest.mark.parametrize("case", sorted(_CASES))
    @pytest.mark.parametrize("depth", [None, "headers"])
    @pytest.mark.parametrize("provenance", [False, True])
    def test_a_headerless_scan_sees_what_compare_sees(
        self, tmp_path: Path, case: str, depth: str | None, provenance: bool
    ) -> None:
        runner = CliRunner()
        old, new = self._libs(tmp_path, case)
        pin = ["--depth", depth] if depth is not None else []
        # `--public-header-dir` names headers but feeds none to the AST parse
        # -- it is a provenance boundary only. A run carrying it is still a
        # header-less run for this contract, and counting it as evidence is a
        # real regression this axis pins (Codex review, PR #1186).
        scoped = tmp_path / "provenance-only"
        if provenance:
            scoped.mkdir(exist_ok=True)
            (scoped / "unrelated.h").write_text("struct Unrelated { int z; };\n")
            pin = [*pin, "--public-header-dir", str(scoped)]

        compare_code, compare_json = self._run(
            runner,
            ["compare", str(old), str(new), *(["--depth", depth] if depth else [])],
            tmp_path / "compare.json",
        )
        scan_code, scan_json = self._run(
            runner,
            ["scan", str(new), "--against", str(old), *pin],
            tmp_path / "scan.json",
        )

        where = f"{case} at depth={depth} provenance={provenance}"
        assert _verdict(scan_json) == _verdict(compare_json), (
            f"{where}: scan={_verdict(scan_json)} compare={_verdict(compare_json)}"
        )
        assert scan_code == compare_code, (
            f"{where}: scan exit={scan_code} compare exit={compare_code}"
        )

    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_one_sided_headers_do_not_starve_the_other_side(
        self, tmp_path: Path, case: str
    ) -> None:
        """``--header old=DIR`` must not invent findings on the header-less side.

        The asymmetric half of the same contract, and the one a run-wide answer
        gets wrong: give only the baseline headers and a shared shortcut leaves
        the old side with header-AST types and the new side with none, so every
        type reads as removed. Verified against ``compare`` as the oracle
        rather than against an expected finding list, since what matters is
        that the extra evidence on one side does not *change the verdict*.

        **The premise is that the header-less side has DWARF to fall back on.**
        That is what the fix preserves; where the candidate carries no debug
        info at all there is nothing to preserve, the starvation is real rather
        than self-inflicted, and `type_removed` is the honest answer. The first
        revision of this test asserted the invariant unconditionally and failed
        on the `windows-latest` integration lane, where the fixture is built by
        MSVC and carries a PDB rather than DWARF. The precondition is now read
        off the run's own `L1_debug` coverage row rather than gated on a
        platform name -- a Linux build stripped of debug info must skip for the
        same reason a Windows one does, and naming the OS would have hidden
        that.
        """
        runner = CliRunner()
        old, new = self._libs(tmp_path, case)
        old_inc = tmp_path / "old-include"
        old_inc.mkdir()
        (old_inc / "case.h").write_text(_CASES[case][0])

        _, baseline_json = self._run(
            runner,
            ["compare", str(old), str(new)],
            tmp_path / "compare.json",
        )
        _, scan_json = self._run(
            runner,
            [
                "scan",
                str(new),
                "--against",
                str(old),
                "--header",
                f"old={old_inc}",
            ],
            tmp_path / "scan.json",
        )

        if not _has_debug_evidence(scan_json):
            pytest.skip(
                "candidate carries no debug info (MSVC/PDB, or a stripped "
                "build), so there is no DWARF fallback for the shortcut to "
                "preserve and this invariant does not apply"
            )

        kinds = {f["kind"] for f in (scan_json.get("diff") or {}).get("findings", [])}
        assert "type_removed" not in kinds, (
            f"{case}: baseline-only headers fabricated a type_removed; "
            f"scan reported {sorted(kinds)}"
        )
        # Deliberately *not* verdict equality. Extra evidence on one side may
        # legitimately raise the risk tier -- an asymmetric run really does
        # earn `layer_coverage_asymmetric`, and reporting it is the honest
        # outcome, not a regression (`enum_member_added` moves COMPATIBLE ->
        # COMPATIBLE_WITH_RISK for exactly that reason). What one-sided headers
        # must never do is manufacture a *break* the symmetric run does not
        # see, which is what the starved-side failure produced.
        breaks = {"BREAKING", "API_BREAK"}
        if _verdict(baseline_json) not in breaks:
            assert _verdict(scan_json) not in breaks, (
                f"{case}: baseline-only headers manufactured "
                f"{_verdict(scan_json)} where compare reports "
                f"{_verdict(baseline_json)}"
            )
