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

"""When a bundle-internal import is a removal, a risk, or nothing at all.

Three questions that look like one and are not, and the tests that hold them
apart. A symbol no bundle member ever exported -- in either release -- is
not a removal however loudly the old code said so; an import OLD already
carried unresolved is evidence of externality; a *new* unresolved import is
evidence of nothing and stays visible.

Split out of ``tests/test_bundle.py``, which carries a ``no_growth``
baseline in ``architecture/debt.yaml``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.bundle import compare_bundle
from abicheck.bundle_detector_heuristics import (
    DEFAULT_SYSTEM_SYMBOLS,
    _looks_system_symbol,
)
from abicheck.bundle_models import BundleSnapshot, ResolutionGraph
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.elf_metadata import ElfMetadata
from abicheck.workflows.bundle_import_evidence import extra_needed_all_system

sys.path.insert(0, str(Path(__file__).parent))

from test_bundle import _meta, _snapshot  # noqa: E402


class TestIntraDepRemovedRequiresAnOldInBundleProvider:
    """The bug class behind MKL's 293 false ``bundle_intra_dep_removed``
    findings: a symbol no bundle member ever exported, in *either* release,
    reported as a removed intra-bundle dependency of the new one.

    ``bundle_intra_dep_removed`` is ``BREAKING`` and its description asserts
    that runtime load *will* fail with an undefined symbol. Both claims rest
    on a version-compatible in-bundle sibling having provided the symbol to
    this consumer in OLD. This class states that as an executable invariant
    over the whole input grid, rather than pinning the one reported repro:
    the original defect passed on three of the eight cells below for the
    wrong reason (the DT_NEEDED allow-list happened to fire), so a test of
    any single cell proves nothing about the mechanism.

    The oracle is deliberately independent of the detector: a case's label
    is derived from how the *fixture* was built (did OLD have an in-bundle
    exporter of this symbol?), never from anything the detector computes.
    """

    SYMBOL = "fflush"  # not in DEFAULT_SYSTEM_SYMBOLS; see test_the_grid_is_not_vacuous
    VENDOR_SYMBOL = "vendor_op"

    @staticmethod
    def _pair(
        *,
        old_has_in_bundle_provider: bool,
        needed: list[str],
        symbol: str,
        versioned: bool = False,
    ) -> tuple[BundleSnapshot, BundleSnapshot]:
        """One (old, new) pair. NEW never exports *symbol* from any member.

        ``old_has_in_bundle_provider`` is the single fact the invariant
        turns on, and it is applied here -- to the fixture -- so the
        expectation below never reads anything the detector derived.
        """
        # A vendor-namespaced version label, deliberately not a toolchain
        # one: `_import_is_external` short-circuits every GLIBC_/CXXABI_-
        # shaped requirement as external before the code under test runs,
        # which would make the whole versioned half of this grid vacuous.
        import_versions = {symbol: "VENDOR_1.0"} if versioned else None
        consumer = _meta(
            soname="libconsumer.so.1",
            needed=needed,
            imports=[symbol],
            import_versions=import_versions,
        )
        old_libs: dict[str, ElfMetadata] = {"libconsumer.so": consumer}
        new_libs: dict[str, ElfMetadata] = {"libconsumer.so": consumer}
        if old_has_in_bundle_provider:
            # The provider is in OLD and reachable from the consumer (its
            # soname is among the consumer's DT_NEEDED), and is gone in NEW.
            old_libs["libprovider.so"] = _meta(
                soname="libprovider.so.1",
                exports=[symbol],
                export_versions={symbol: "VENDOR_1.0"} if versioned else None,
            )
            new_libs["libprovider.so"] = _meta(soname="libprovider.so.1")
        return _snapshot(old_libs), _snapshot(new_libs)

    #: (label, DT_NEEDED edges of the consumer in both releases).
    #: "libprovider.so.1" is appended by _pair's caller where an in-bundle
    #: provider exists, so the consumer genuinely reaches it in OLD.
    NEEDED_SHAPES: tuple[tuple[str, list[str]], ...] = (
        ("no DT_NEEDED at all", []),
        ("system-only DT_NEEDED", ["libc.so.6"]),
        ("mixed DT_NEEDED", ["libc.so.6", "libvendor.so.1"]),
        ("non-system DT_NEEDED", ["libvendor.so.1"]),
    )

    def test_never_reported_when_no_old_in_bundle_provider(self) -> None:
        """The invariant, swept across every DT_NEEDED shape x versioned/
        unversioned x allow-listed/not-allow-listed symbol name.

        Batched so one run names every disagreeing cell at once rather than
        stopping at the first.
        """
        offenders: list[str] = []
        for needed_label, needed in self.NEEDED_SHAPES:
            for versioned in (False, True):
                for sym_label, symbol in (
                    ("allow-listed-shape symbol", self.SYMBOL),
                    ("vendor-shaped symbol", self.VENDOR_SYMBOL),
                ):
                    old, new = self._pair(
                        old_has_in_bundle_provider=False,
                        needed=needed,
                        symbol=symbol,
                        versioned=versioned,
                    )
                    result = compare_bundle(old, new, per_library_results=[])
                    bad = [
                        f
                        for f in result.bundle_findings
                        if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
                        and f.symbol == symbol
                    ]
                    if bad:
                        vlabel = "versioned" if versioned else "unversioned"
                        offenders.append(f"{needed_label} / {vlabel} / {sym_label}")
        assert not offenders, (
            "bundle_intra_dep_removed claims a diff-confirmed removal for a "
            "symbol no bundle member ever exported, in these cells: "
            + "; ".join(offenders)
        )

    def test_a_real_removal_is_still_reported_across_the_same_grid(self) -> None:
        """The other direction, over the identical grid: when OLD *did*
        carry a reachable in-bundle provider, every cell must still fire.

        Without this, the fix above is indistinguishable from deleting the
        detector -- which is exactly the failure mode the repository's
        matrix-test guidance warns about (an implementation returning
        "no findings" for every input passing the suite in full).
        """
        missing: list[str] = []
        for needed_label, needed in self.NEEDED_SHAPES:
            for versioned in (False, True):
                old, new = self._pair(
                    old_has_in_bundle_provider=True,
                    needed=[*needed, "libprovider.so.1"],
                    symbol=self.VENDOR_SYMBOL,
                    versioned=versioned,
                )
                result = compare_bundle(old, new, per_library_results=[])
                fired = any(
                    f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
                    and f.symbol == self.VENDOR_SYMBOL
                    for f in result.bundle_findings
                )
                if not fired:
                    vlabel = "versioned" if versioned else "unversioned"
                    missing.append(f"{needed_label} / {vlabel}")
        assert not missing, (
            "a genuinely removed intra-bundle provider went unreported in "
            "these cells: " + "; ".join(missing)
        )

    def test_self_comparison_never_reports_a_removal(self) -> None:
        """The metamorphic form of the same invariant: comparing *any*
        bundle against itself removes nothing, so no shape of input may
        produce ``bundle_intra_dep_removed``.

        Stated separately because this is the property that nine fixtures
        in ``TestIntraDepRemoved`` were unknowingly violating -- each used
        ``compare_bundle(new, new, ...)`` as shorthand for "no member
        exports this symbol" and then asserted a removal was reported.
        A shortcut like that is invisible one test at a time; as an
        invariant over every shape it is immediate.
        """
        offenders: list[str] = []
        for needed_label, needed in self.NEEDED_SHAPES:
            for versioned in (False, True):
                for symbol in (self.SYMBOL, self.VENDOR_SYMBOL):
                    for with_provider in (False, True):
                        _old, new = self._pair(
                            old_has_in_bundle_provider=with_provider,
                            needed=needed,
                            symbol=symbol,
                            versioned=versioned,
                        )
                        result = compare_bundle(new, new, per_library_results=[])
                        if any(
                            f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED
                            for f in result.bundle_findings
                        ):
                            offenders.append(
                                f"{needed_label} / {symbol} / "
                                f"versioned={versioned} / provider={with_provider}"
                            )
        assert not offenders, (
            "a bundle compared against itself reported a removed "
            "intra-bundle dependency in these cells: " + "; ".join(offenders)
        )

    def test_the_grid_is_not_vacuous(self) -> None:
        """Vacuity guard on the two sweeps above.

        Both would pass trivially if the symbols they use were suppressed
        before the code path under test is even reached -- by the
        symbol-name allow-list, or by every fixture failing to register a
        consumer at all. Assert the preconditions directly so a later
        allow-list broadening cannot silently hollow the grid out.
        """
        assert self.VENDOR_SYMBOL not in DEFAULT_SYSTEM_SYMBOLS
        assert not _looks_system_symbol(self.VENDOR_SYMBOL)
        # Every fixture actually presents the consumer's import to the
        # detector -- i.e. the symbol reaches `resolution.consumers`.
        for _label, needed in self.NEEDED_SHAPES:
            old, _new = self._pair(
                old_has_in_bundle_provider=False,
                needed=needed,
                symbol=self.VENDOR_SYMBOL,
            )
            assert "libconsumer.so" in {
                c.library for c in old.resolution.consumers[self.VENDOR_SYMBOL]
            }

    def test_unaccounted_symbol_downgrades_to_the_unresolved_kind(self) -> None:
        """Suppressing the false BREAKING must not silently discard the
        observation. A symbol with no in-bundle history that the outward
        DT_NEEDED evidence does *not* account for (a non-system edge) and
        whose name is not allow-listed is still surfaced -- under the kind
        that claims no diff confirmation, at COMPATIBLE_WITH_RISK.
        """
        old, new = self._pair(
            old_has_in_bundle_provider=False,
            needed=["libvendor.so.1"],
            symbol=self.VENDOR_SYMBOL,
        )
        result = compare_bundle(old, new, per_library_results=[])
        kinds = {
            f.kind for f in result.bundle_findings if f.symbol == self.VENDOR_SYMBOL
        }
        assert ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY in kinds
        assert ChangeKind.BUNDLE_INTRA_DEP_REMOVED not in kinds
        from abicheck.change_registry import REGISTRY

        meta = REGISTRY.get(ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY.value)
        assert meta is not None
        assert meta.default_verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_zero_dt_needed_consumer_is_fully_suppressed(self) -> None:
        """The MKL shape specifically: a consumer declaring no DT_NEEDED at
        all, importing a libc symbol no bundle member ever exported, emits
        nothing -- not a BREAKING removal and not a RISK either. A library
        with no declared dependencies resolves every undefined symbol from
        the global namespace its host process assembles, which is outside
        the bundle's scope by construction.
        """
        old, new = self._pair(
            old_has_in_bundle_provider=False, needed=[], symbol=self.SYMBOL
        )
        result = compare_bundle(old, new, per_library_results=[])
        assert [f for f in result.bundle_findings if f.symbol == self.SYMBOL] == []


class TestExtraNeededAllSystemPrimitive:
    """Property tests for the shared primitive behind both unresolved-import
    detectors (``workflows.bundle_import_evidence.extra_needed_all_system``),
    stated as its own contract and decoupled from either caller's domain
    logic -- AGENTS.md's "primitive-level property tests" rule. Both call
    sites hand-rolled this and both hand-rolled the same empty-sequence bug,
    which is precisely the evidence that it wanted to be one tested
    primitive rather than two inline expressions.
    """

    @staticmethod
    def _graph(edges: list[str]) -> ResolutionGraph:
        return ResolutionGraph(extra_needed={"lib": list(edges)})

    def test_no_outward_edges_is_true(self) -> None:
        """The empty case, which is the bug both callers had. A library
        declaring no outward DT_NEEDED edge has no non-system outward
        dependency -- vacuously, and that is the correct reading."""
        assert extra_needed_all_system("lib", self._graph([]), set()) is True

    def test_unknown_library_is_true(self) -> None:
        """A library absent from the graph is the empty case, not an error."""
        assert extra_needed_all_system("absent", self._graph([]), set()) is True

    def test_universally_quantified_over_the_edges(self) -> None:
        """The contract is "every edge", so adding any non-covered edge to a
        covered set must flip the answer, and the result must not depend on
        the order the edges appear in."""
        import itertools

        covered = ["libc.so.6", "libm.so.6"]
        assert extra_needed_all_system("lib", self._graph(covered), set()) is True
        for perm in itertools.permutations([*covered, "libvendor.so.1"]):
            assert (
                extra_needed_all_system("lib", self._graph(list(perm)), set()) is False
            ), f"order-dependent result for {perm}"

    def test_explicit_allow_list_covers_an_otherwise_uncovered_edge(self) -> None:
        """The allow-list is the second of the two independent ways an edge
        is covered, and it composes with the shape heuristic rather than
        replacing it."""
        g = self._graph(["libc.so.6", "libvendor.so.1"])
        assert extra_needed_all_system("lib", g, set()) is False
        assert extra_needed_all_system("lib", g, {"libvendor.so.1"}) is True

    def test_answers_only_about_the_named_library(self) -> None:
        """A sibling's uncovered edges never leak into this library's answer."""
        g = ResolutionGraph(
            extra_needed={"lib": ["libc.so.6"], "other": ["libvendor.so.1"]}
        )
        assert extra_needed_all_system("lib", g, set()) is True
        assert extra_needed_all_system("other", g, set()) is False


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason=(
        "the zero-DT_NEEDED shape this reproduces is ELF-specific: `gcc "
        "-shared -nostdlib` is not accepted the same way on Darwin, and "
        "Mach-O/PE have no DT_NEEDED at all. The emission rule itself is "
        "covered platform-independently by the property grid above; this "
        "is the end-to-end reproduction of the reported ELF case."
    ),
)
class TestNewlyIntroducedUnresolvedImportsStayVisible:
    """A *new* unresolved import is never dropped by removal-era evidence.

    ``ever_provided_in_bundle`` answers whether a bundle sibling used to
    satisfy an import; it does not answer whether the import *existed*
    before. Conflating the two is how a newly introduced unresolved import
    disappeared: for a zero-``DT_NEEDED`` consumer ``extra_needed_all_system``
    is vacuously true, so the suppression branch fired for an import nothing
    anywhere shows was ever satisfied -- a vendor-symbol typo in a new library
    then produced a clean bundle result while failing at load time (Codex
    review).

    The grid below is the point rather than any one cell: the defect is
    invisible in the cells where some *other* evidence happens to suppress or
    report, so a single example proves nothing about the rule. The oracle is
    the fixture's own construction -- did OLD carry this import? -- never
    anything the detector computes.
    """

    #: ``DT_NEEDED`` shapes. Zero edges is MKL's shape and the one that made
    #: ``extra_needed_all_system`` vacuously true; the others must behave the
    #: same way, since the import's *history* is what decides, not its edges.
    _NEEDED = {
        "zero": [],
        "system-only": ["libc.so.6"],
        "non-system": ["libvendor_other.so.3"],
    }
    #: A recognised system symbol is suppressed on its own name in either
    #: case -- a new library calling libc is the ordinary case, and that
    #: evidence does not depend on release history. A vendor-shaped name has
    #: no such standing.
    #: ``memcpy`` is on ``DEFAULT_SYSTEM_SYMBOLS``; ``fflush`` deliberately
    #: is *not* (checked by ``test_the_grid_is_not_vacuous``), which is why
    #: MKL's 293 ``fflush``/``sincos``/``MPI_Finalize`` imports reached the
    #: removal path at all -- the name allow-list never covered them.
    _SYMBOLS = {"allow-listed": "memcpy", "vendor-shaped": "acme_new_op"}

    def _bundles(
        self, needed: list[str], symbol: str, *, in_old: bool
    ) -> tuple[BundleSnapshot, BundleSnapshot]:
        """NEW imports *symbol* unresolved; OLD carries the same import only
        when *in_old*. Neither side has any in-bundle provider for it, so
        ``ever_provided_in_bundle`` is false in both cases -- the *only*
        difference between the two is the import's history."""
        new = _snapshot(
            {
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1", needed=list(needed), imports=[symbol]
                ),
            }
        )
        old = _snapshot(
            {
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1",
                    needed=list(needed),
                    imports=[symbol] if in_old else [],
                ),
            }
        )
        return old, new

    def _findings(self, old: BundleSnapshot, new: BundleSnapshot, symbol: str) -> list:
        return [
            f
            for f in compare_bundle(old, new, per_library_results=[]).bundle_findings
            if f.symbol == symbol
        ]

    def test_a_new_unresolved_vendor_import_is_reported_across_the_grid(self) -> None:
        """Swept, and batched so one run names every disagreeing cell."""
        disagreed: list[str] = []
        for shape, needed in self._NEEDED.items():
            old, new = self._bundles(needed, "acme_new_op", in_old=False)
            kinds = {f.kind for f in self._findings(old, new, "acme_new_op")}
            if ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY not in kinds:
                disagreed.append(f"{shape}: reported {sorted(k.value for k in kinds)}")
        assert not disagreed, disagreed

    def test_it_is_never_reported_as_a_removal(self) -> None:
        """The kind matters as much as the presence: nothing was removed, so
        the diff-confirmed ``BREAKING`` kind must not be the one that fires.
        This is what distinguishes the fix from simply restoring the original
        over-report."""
        offenders: list[str] = []
        for shape, needed in self._NEEDED.items():
            for label, symbol in self._SYMBOLS.items():
                for in_old in (True, False):
                    old, new = self._bundles(needed, symbol, in_old=in_old)
                    for f in self._findings(old, new, symbol):
                        if f.kind == ChangeKind.BUNDLE_INTRA_DEP_REMOVED:
                            offenders.append(f"{shape}/{label}/in_old={in_old}")
        assert not offenders, offenders

    def test_an_import_old_already_carried_is_still_suppressed(self) -> None:
        """The negative control. Without it, "report everything" would pass
        the test above in full -- and reporting every pre-existing external
        import is the 293-finding false positive this whole area exists to
        stop. Suppression here rests on real evidence: the same unresolved
        import shipped before, and that release loaded."""
        still_reported: list[str] = []
        for shape, needed in self._NEEDED.items():
            if needed and not all(n.startswith("libc") for n in needed):
                # A non-system outward edge is not evidence of externality,
                # so this shape is legitimately still reported -- the
                # allow-list branch never claimed otherwise.
                continue
            old, new = self._bundles(needed, "acme_new_op", in_old=True)
            if self._findings(old, new, "acme_new_op"):
                still_reported.append(shape)
        assert not still_reported, still_reported

    def test_a_new_import_of_a_system_symbol_is_suppressed_on_its_name(self) -> None:
        """The symbol-name allow-list is history-independent, so a new library
        calling ``memcpy`` is not a finding even though the import is new."""
        for shape, needed in self._NEEDED.items():
            old, new = self._bundles(needed, "memcpy", in_old=False)
            assert not self._findings(old, new, "memcpy"), shape

    def test_the_grid_is_not_vacuous(self) -> None:
        """Guards the fixtures themselves: if ``acme_new_op`` were quietly
        allow-listed, or the consumer's import never reached the resolution
        graph, every assertion above would pass while testing nothing."""
        old, new = self._bundles([], "acme_new_op", in_old=False)
        assert "acme_new_op" in new.resolution.consumers
        assert not new.resolution.providers_for("acme_new_op")
        assert self._findings(old, new, "acme_new_op")
        assert "acme_new_op" not in DEFAULT_SYSTEM_SYMBOLS
        assert "memcpy" in DEFAULT_SYSTEM_SYMBOLS
        # The symbol the real MKL report was flooded with is *not* on the
        # allow-list: had it been, none of those 293 findings would have
        # reached the removal path and this area's defect would have stayed
        # hidden. Pinned so a future allow-list edit cannot quietly turn the
        # grid above into a test of the name check instead of the history
        # check.
        assert "fflush" not in DEFAULT_SYSTEM_SYMBOLS


class TestZeroDtNeededBundleEndToEnd:
    """The reported defect against real compiled binaries, through the public
    ``compare`` workflow rather than the detector alone.

    The property grid above proves the emission rule. This proves the thing
    that was actually broken: a release of libraries carrying **zero**
    ``DT_NEEDED`` entries -- Intel MKL's shape -- whose undefined libc imports
    were reported as removed intra-bundle dependencies, taking a compatible
    release to exit 4. ``-nostdlib`` reproduces that shape exactly: the
    libraries import ``fflush``/``sin`` and declare no dependencies at all.

    Validating through the CLI matters here beyond habit. The unit fixtures
    build their resolution graph from hand-written ``ElfMetadata``; only a
    real binary proves that a real toolchain produces the input shape the
    fix is written against.
    """

    @staticmethod
    def _build(root: Path, *, with_addition: bool) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        core = root / "core.c"
        core.write_text(
            "#include <stdio.h>\nint mkl_core_op(void){ fflush(stdout); return 1; }\n",
            encoding="utf-8",
        )
        rt = root / "rt.c"
        body = (
            "#include <stdio.h>\n#include <math.h>\n"
            "int mkl_rt_op(void){ fflush(stdout); return (int)sin(1.0); }\n"
        )
        if with_addition:
            body += "int mkl_rt_new_op(void){ return 2; }\n"
        rt.write_text(body, encoding="utf-8")
        for src, out in ((core, "libmkl_core.so"), (rt, "libmkl_rt.so")):
            subprocess.run(
                # -nostdlib is what produces the zero-DT_NEEDED shape.
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-nostdlib",
                    "-o",
                    str(root / out),
                    str(src),
                ],
                check=True,
            )
        return root

    def test_zero_dt_needed_libc_imports_are_not_a_breaking_release(
        self, tmp_path: Path
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        old = self._build(tmp_path / "old", with_addition=False)
        new = self._build(tmp_path / "new", with_addition=True)

        # Guard the fixture's own premise: if a future toolchain starts
        # emitting DT_NEEDED here, this test silently stops exercising the
        # condition and would pass for the wrong reason.
        needed = subprocess.run(
            ["readelf", "-d", str(new / "libmkl_rt.so")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "NEEDED" not in needed, (
            "fixture no longer has the zero-DT_NEEDED shape this test exists "
            "to exercise"
        )

        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--depth", "binary"]
        )
        assert "bundle_intra_dep_removed" not in result.output, result.output
        # 0 == compatible under the legacy verdict scheme; 4 was the reported
        # false ABI break.
        assert result.exit_code == 0, result.output


class TestAuditModeSkipsWhatItCannotJudge:
    """Two branches the audit detector takes before it can decide anything.

    Both were reachable and untested. Each is a *skip*, and an untested skip
    is the dangerous kind: if either stopped firing, the detector would go on
    to judge an import on evidence it does not have, and the finding it
    invented would look exactly like a real one.
    """

    def _detect(self, snapshot: BundleSnapshot, system_providers=None):
        from abicheck.bundle import _detect_unresolved_intra_dependency

        return _detect_unresolved_intra_dependency(snapshot, system_providers or set())

    def test_a_consumer_with_no_parsed_metadata_is_skipped(self) -> None:
        """The resolution graph can name a consumer whose own metadata never
        parsed. Nothing is known about its outward edges, so nothing can be
        concluded about its imports -- least of all that they are unresolved.
        """
        snap = _snapshot(
            {"libconsumer.so": _meta(soname="libconsumer.so.1", imports=["mystery_op"])}
        )
        # The consumer stays in the resolution graph; only its parsed
        # metadata goes away, which is the state this branch exists for.
        del snap.metadata["libconsumer.so"]
        assert self._detect(snap) == []

        # Negative control: with metadata present the same bundle *does*
        # report, so the skip above is the branch under test and not an
        # accidentally inert fixture.
        intact = _snapshot(
            {"libconsumer.so": _meta(soname="libconsumer.so.1", imports=["mystery_op"])}
        )
        assert [f.symbol for f in self._detect(intact)] == ["mystery_op"]

    def test_a_versioned_import_a_provider_outside_the_bundle_satisfies(self) -> None:
        """``_import_is_external`` resolves a versioned import to its verneed
        provider. When that provider is not in the bundle, the import is
        external by observation -- not an unresolved intra-dependency."""
        snap = _snapshot(
            {
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1",
                    needed=["libthirdparty.so.4"],
                    imports=["tp_entry"],
                    import_versions={"tp_entry": "TP_4.0"},
                    import_version_sonames={"tp_entry": "libthirdparty.so.4"},
                    versions_required={"libthirdparty.so.4": ["TP_4.0"]},
                ),
            }
        )
        assert self._detect(snap) == []

        # The same import with its provider *inside* the bundle is a real
        # finding, so the branch is doing the discriminating work.
        with_sibling = _snapshot(
            {
                "libconsumer.so": _meta(
                    soname="libconsumer.so.1",
                    needed=["libsibling.so.1"],
                    imports=["tp_entry"],
                    import_versions={"tp_entry": "TP_4.0"},
                    import_version_sonames={"tp_entry": "libsibling.so.1"},
                    versions_required={"libsibling.so.1": ["TP_4.0"]},
                ),
                "libsibling.so.1": _meta(soname="libsibling.so.1"),
            }
        )
        assert [f.symbol for f in self._detect(with_sibling)] == ["tp_entry"]
