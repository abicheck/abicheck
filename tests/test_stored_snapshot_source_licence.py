# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Bug-class regression tests for the two P1 defects in source-derived
enrichment of *stored* snapshots.

Registered classes (``tests/regressions/manifest.py``):

``evidence.stored_snapshot_rederivation``
    A path recorded in a snapshot is provenance, not a licence to re-read the
    current filesystem for a historical fact. The reported instance was
    ``workflows/pattern_preprocessor_scan.py`` rebuilding pattern/preprocessor
    roots from a stored snapshot's ``source_header``/compile units and letting
    ``buildsource/pattern_facts.py`` ``read_text()`` them, so a baseline
    dumped elsewhere was re-characterised against today's runner.

``coverage.discovery_derived_completeness``
    Sufficiency for an *absence* claim must be computed from the **expected**
    input set, never from what a discovery walk happened to find. The reported
    instance was ``files_scanned > 0 and files_skipped == 0`` over a walk that
    silently ``continue``d past a non-existent root, so missing inputs read as
    "fully covered".

These are deliberately not written as one repro each. The provenance class is
exercised by an **exhaustive small-domain enumeration** of every way a source
checkout can move out from under a stored snapshot (delete a file, edit it,
relocate the tree, substitute an unrelated file at the same path, replace the
whole root with a directory) crossed with both pattern kinds and both
evolution directions; the completeness class by an **exhaustive enumeration**
of every ``SourceInputDisposition`` and, for the fold, of all sixteen
(old_hit, new_hit, old_sufficient, new_sufficient) combinations checked
against a hand-written truth table -- an oracle written independently of the
implementation, not a second call into it.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.pattern_facts import (
    PatternFactsResult,
    find_pattern_facts,
    resolve_expected_source_inputs,
)
from abicheck.buildsource.preprocessor_facts import PreprocessorFactsResult
from abicheck.buildsource.source_inputs import (
    GAP_DISPOSITIONS,
    WITHHELD_FOR_STORED_SNAPSHOT,
    SourceInput,
    SourceInputDisposition,
    SourceInputSet,
    SourceReadLicence,
    resolve_source_inputs,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.serialization import load_snapshot, write_snapshot
from abicheck.workflows.pattern_preprocessor_scan import (
    CHECK_HEADER_LEAK,
    CHECK_MACRO_DIVERGENCE,
    CHECK_PATTERN_ESCALATION,
    Sufficiency,
    _fold_evolution,
    compute_pattern_preprocessor_scan,
    snapshot_source_licence,
)

# ── Fixtures: a library whose declarations point at a real source tree ───────

#: Two independent escalating constructs, so a test can assert on one kind
#: while varying the other -- a single-kind fixture cannot tell "the fold
#: refused this finding" apart from "the scan produced nothing at all".
PACKED_SOURCE = "#pragma pack(push, 1)\nstruct S { int a; };\n#pragma pack(pop)\n"
TEMPLATE_SOURCE = "template class Widget<int>;\n"


def _write_tree(root: Path, contents: dict[str, str]) -> list[str]:
    paths = []
    for name, text in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        paths.append(str(target))
    return paths


def _snapshot_recording(headers: list[str], *, version: str = "1.0") -> AbiSnapshot:
    """A snapshot whose declarations record *headers* as their provenance."""
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z1f{i}v",
                return_type="void",
                params=[],
                source_header=h,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
            for i, h in enumerate(headers)
        ],
    )


def _stored(snapshot: AbiSnapshot, tmp_path: Path, name: str) -> AbiSnapshot:
    """Round-trip *snapshot* through the real storage codec, as a baseline is."""
    out = tmp_path / name
    write_snapshot(snapshot, out)
    return load_snapshot(out)


def _live(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Mark *snapshot* as produced by a live extraction in this run."""
    snapshot.live_source_evidence = True
    return snapshot


# ── Class 1: evidence.stored_snapshot_rederivation ───────────────────────────


class TestStoredSnapshotIsNeverReDerivedFromTodaysFilesystem:
    """A stored snapshot's recorded paths are provenance, never a read licence."""

    def test_storage_round_trip_never_grants_a_licence(self, tmp_path: Path) -> None:
        """The licence cannot survive serialization -- it is not a persisted
        field, so no on-disk content can make a loaded snapshot claim one."""
        live = _live(_snapshot_recording([str(tmp_path / "a.hpp")]))
        assert snapshot_source_licence(live).permitted is True

        stored = _stored(live, tmp_path, "snap.json")
        assert stored.live_source_evidence is False
        assert snapshot_source_licence(stored).permitted is False

    @pytest.mark.parametrize(
        "mutation",
        [
            "unchanged",
            "delete_file",
            "edit_file",
            "truncate_file",
            "substitute_unrelated_file",
            "relocate_tree",
            "replace_file_with_directory",
            "make_unreadable",
        ],
    )
    @pytest.mark.parametrize("seed_construct", [PACKED_SOURCE, TEMPLATE_SOURCE])
    def test_stored_snapshot_facts_are_invariant_under_source_mutation(
        self, tmp_path: Path, mutation: str, seed_construct: str
    ) -> None:
        """**The acceptance invariant.** Exhaustive over every way a checkout
        can move out from under a snapshot: an unchanged stored snapshot must
        neither gain nor lose a historical fact when the tree it names is
        mutated, deleted, relocated, or replaced.

        The oracle is the ``unchanged`` arm of this same enumeration, captured
        *before* any mutation -- not a recomputation through the code under
        test with the same inputs.
        """
        tree = tmp_path / "checkout"
        header = tree / "include" / "pub.hpp"
        _write_tree(tree, {"include/pub.hpp": seed_construct})

        old = _stored(
            _snapshot_recording([str(header)], version="1.0"), tmp_path, "old.json"
        )
        new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "new.json"
        )
        before = compute_pattern_preprocessor_scan(old, new).to_dict()

        if mutation == "delete_file":
            header.unlink()
        elif mutation == "edit_file":
            header.write_text(
                seed_construct + "\n__attribute__((packed)) struct T{};\n"
            )
        elif mutation == "truncate_file":
            header.write_text("")
        elif mutation == "substitute_unrelated_file":
            header.write_text("int unrelated_symbol;\n")
        elif mutation == "relocate_tree":
            tree.rename(tmp_path / "moved")
        elif mutation == "replace_file_with_directory":
            header.unlink()
            header.mkdir()
        elif mutation == "make_unreadable":
            header.chmod(0o000)

        after = compute_pattern_preprocessor_scan(old, new).to_dict()
        assert after == before, f"stored facts changed under mutation {mutation!r}"

        if mutation == "make_unreadable":
            header.chmod(0o600)

    def test_unlicensed_side_touches_no_filesystem_api_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stronger than "the answer is stable": the unlicensed path must not
        *reach* the filesystem, so a same-looking path on the current runner
        cannot influence the result even by existing.

        Asserted by executing the real code with the filesystem primitives
        booby-trapped, not by asserting on source text (the #705->#758 lesson
        in AGENTS.md: a defense must be executed, not read)."""
        tripped: list[str] = []

        def _trap(name: str) -> Any:
            def _fail(*_a: Any, **_kw: Any) -> Any:
                tripped.append(name)
                raise AssertionError(f"unlicensed scan touched {name}")

            return _fail

        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _stored(_snapshot_recording([str(header)]), tmp_path, "o.json")
        new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "n.json"
        )

        monkeypatch.setattr(Path, "read_text", _trap("Path.read_text"))
        monkeypatch.setattr(Path, "is_file", _trap("Path.is_file"))
        monkeypatch.setattr(Path, "is_dir", _trap("Path.is_dir"))
        monkeypatch.setattr(Path, "exists", _trap("Path.exists"))
        monkeypatch.setattr(os, "walk", _trap("os.walk"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert tripped == []
        assert result.pattern_escalation_evolution == {}
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is False

    def test_live_extraction_still_reads_and_reports(self, tmp_path: Path) -> None:
        """The fix must not simply disable the feature: a side that really was
        extracted from today's tree keeps its facts."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _live(_snapshot_recording([str(header)]))
        new = _live(_snapshot_recording([str(header)], version="2.0"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is True
        assert set(result.pattern_escalation_evolution.values()) == {"persistent"}

    def test_explicit_verified_context_is_the_only_override(
        self, tmp_path: Path
    ) -> None:
        """A caller that has independently verified the recorded tree can
        supply a licence; nothing else re-enables reading."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        stored_old = _stored(_snapshot_recording([str(header)]), tmp_path, "o.json")
        stored_new = _stored(
            _snapshot_recording([str(header)], version="2.0"), tmp_path, "n.json"
        )

        withheld = compute_pattern_preprocessor_scan(stored_old, stored_new)
        assert withheld.pattern_escalation_evolution == {}

        verified = SourceReadLicence.verified_context("checkout pinned to snapshot sha")
        granted = compute_pattern_preprocessor_scan(
            stored_old,
            stored_new,
            old_source_licence=verified,
            new_source_licence=verified,
        )
        assert set(granted.pattern_escalation_evolution.values()) == {"persistent"}

    def test_default_licence_is_deny(self) -> None:
        """Deny-by-default: a snapshot that establishes nothing reads nothing."""
        assert WITHHELD_FOR_STORED_SNAPSHOT.permitted is False
        assert (
            snapshot_source_licence(AbiSnapshot(library="l", version="1")).permitted
            is False
        )

    def test_withheld_licence_stats_nothing_even_for_a_real_path(
        self, tmp_path: Path
    ) -> None:
        """At the primitive level: the roots are accounted for as
        ``not_licensed`` regardless of whether they exist."""
        real = tmp_path / "real.hpp"
        real.write_text(PACKED_SOURCE)
        gone = tmp_path / "gone.hpp"

        result = find_pattern_facts(
            [str(real), str(gone)], licence=WITHHELD_FOR_STORED_SNAPSHOT
        )
        assert result.facts == []
        assert result.sufficient is False
        assert {i.disposition for i in result.inputs.inputs} == {
            SourceInputDisposition.NOT_LICENSED
        }


# ── Class 2: coverage.discovery_derived_completeness ─────────────────────────


class TestExpectedInputSetReplacesDiscoveryDerivedCompleteness:
    """Sufficiency comes from the expected input set, not from the survivors."""

    def test_missing_root_is_recorded_not_dropped(self, tmp_path: Path) -> None:
        """**The reported bug.** A declared root that does not exist used to
        vanish from the walk entirely: zero inputs, zero skips, and one
        surviving file then read as "fully covered"."""
        present = tmp_path / "there.hpp"
        present.write_text(PACKED_SOURCE)
        absent = tmp_path / "gone.hpp"

        result = find_pattern_facts([str(present), str(absent)])
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(absent)] is SourceInputDisposition.MISSING
        assert dispositions[str(present)] is SourceInputDisposition.SCANNED
        # It scanned a file and skipped none -- the exact state the old
        # `files_scanned > 0 and files_skipped == 0` signal called complete.
        assert result.files_scanned == 1
        assert result.files_skipped == 0
        assert result.sufficient is False

    @pytest.mark.parametrize("disposition", list(SourceInputDisposition))
    def test_every_disposition_is_classified_as_gap_or_not_exhaustively(
        self, disposition: SourceInputDisposition
    ) -> None:
        """Exhaustive over the whole enum, so a disposition added later cannot
        default into "not a gap" unnoticed: a set holding a gap is never
        sufficient, and a set of non-gap inputs is sufficient iff something
        was actually scanned."""
        inputs = SourceInputSet(
            inputs=(SourceInput(path="x", disposition=disposition),),
            licence=SourceReadLicence.live_extraction(),
        )
        if disposition in GAP_DISPOSITIONS:
            assert inputs.sufficient is False
            assert inputs.insufficiency_reason() != ""
        elif disposition is SourceInputDisposition.SCANNED:
            assert inputs.sufficient is True
        else:  # EXCLUDED: deliberately out of scope, but nothing was scanned
            assert inputs.sufficient is False

    @pytest.mark.parametrize(
        "count_missing,count_unreadable,count_scanned",
        list(itertools.product(range(3), range(3), range(3))),
    )
    def test_sufficiency_is_conjunctive_over_the_whole_expected_set(
        self, count_missing: int, count_unreadable: int, count_scanned: int
    ) -> None:
        """Generated over every small mix of gaps and successes. The oracle is
        stated independently: sufficient iff at least one input scanned AND no
        gap of any kind, which is *not* how the implementation is written
        (it folds over a disposition set)."""
        items = (
            [
                SourceInput(path=f"m{i}", disposition=SourceInputDisposition.MISSING)
                for i in range(count_missing)
            ]
            + [
                SourceInput(path=f"u{i}", disposition=SourceInputDisposition.UNREADABLE)
                for i in range(count_unreadable)
            ]
            + [
                SourceInput(path=f"s{i}", disposition=SourceInputDisposition.SCANNED)
                for i in range(count_scanned)
            ]
        )
        inputs = SourceInputSet(
            inputs=tuple(items), licence=SourceReadLicence.live_extraction()
        )
        expected = count_scanned > 0 and count_missing == 0 and count_unreadable == 0
        assert inputs.sufficient is expected

    def test_deliberately_excluded_input_is_not_a_gap(self, tmp_path: Path) -> None:
        """ "Deliberately excluded" is a distinct state from "missing": the
        caller asked for the narrowing, so it does not spoil sufficiency."""
        kept = tmp_path / "kept.hpp"
        dropped = tmp_path / "dropped.hpp"
        kept.write_text(PACKED_SOURCE)
        dropped.write_text(TEMPLATE_SOURCE)

        result = find_pattern_facts(
            [str(kept), str(dropped)], changed_paths=["kept.hpp"]
        )
        dispositions = {i.path: i.disposition for i in result.inputs.inputs}
        assert dispositions[str(dropped)] is SourceInputDisposition.EXCLUDED
        assert result.sufficient is True

    def test_unsupported_input_is_its_own_state(self, tmp_path: Path) -> None:
        """A root that exists but is not a readable file (a FIFO here) is
        ``unsupported`` -- neither silently dropped nor mislabelled missing."""
        fifo = tmp_path / "pipe"
        os.mkfifo(fifo)
        scanned = tmp_path / "ok.hpp"
        scanned.write_text(PACKED_SOURCE)

        inputs = resolve_expected_source_inputs([str(fifo), str(scanned)])
        dispositions = {i.path: i.disposition for i in inputs.inputs}
        assert dispositions[str(fifo)] is SourceInputDisposition.UNSUPPORTED
        assert inputs.sufficient is False

    def test_sufficiency_is_answered_per_check_not_globally(
        self, tmp_path: Path
    ) -> None:
        """The three checks answer to different evidence, so one global
        ``files_scanned > 0`` cannot speak for all of them."""
        header = tmp_path / "pub.hpp"
        header.write_text(PACKED_SOURCE)
        old = _live(_snapshot_recording([str(header)]))
        new = _live(_snapshot_recording([str(header)], version="2.0"))

        result = compute_pattern_preprocessor_scan(old, new)
        assert set(result.coverage) == {
            CHECK_PATTERN_ESCALATION,
            CHECK_MACRO_DIVERGENCE,
            CHECK_HEADER_LEAK,
        }
        # The lexical scan is established; the preprocessor checks are not
        # (no L3 build evidence) -- three answers, not one.
        assert result.coverage[CHECK_PATTERN_ESCALATION]["old"].established is True
        assert result.coverage[CHECK_MACRO_DIVERGENCE]["old"].established is False
        assert result.coverage[CHECK_HEADER_LEAK]["old"].established is False

    def test_macro_and_leak_sufficiency_can_disagree(self) -> None:
        """A run with compile units but no public headers fully establishes
        macro divergence while establishing nothing about leaks -- the single
        global predicate this replaces gave both the same answer."""
        from abicheck.workflows.pattern_preprocessor_scan import (
            _header_leak_sufficiency,
            _macro_divergence_sufficiency,
        )

        res = PreprocessorFactsResult(
            ran=True, attempted=2, succeeded=2, tus_scanned=2, headers_scanned=0
        )
        assert _macro_divergence_sufficiency(res).established is True
        assert _header_leak_sufficiency(res).established is False


# ── The fold's own contract, exhaustively ────────────────────────────────────


def _oracle(
    old_hit: bool, new_hit: bool, old_sufficient: bool, new_sufficient: bool
) -> str:
    """Independent truth table for :func:`_fold_evolution`.

    Written from the *contract* rather than from the implementation: presence
    is established by having observed it; absence is established only by that
    side's sufficiency; a state whose premises are not all established is
    ``not_evaluated``.
    """
    knows_old = old_hit or old_sufficient
    knows_new = new_hit or new_sufficient
    if not knows_old or not knows_new:
        return "not_evaluated"
    if old_hit and new_hit:
        return "persistent"
    if new_hit:
        return "introduced"
    return "resolved"


class TestFoldEvolutionPerFindingEstablishment:
    @pytest.mark.parametrize(
        "old_hit,new_hit,old_sufficient,new_sufficient",
        [c for c in itertools.product([False, True], repeat=4) if c[0] or c[1]],
    )
    def test_exhaustive_against_independent_truth_table(
        self,
        old_hit: bool,
        new_hit: bool,
        old_sufficient: bool,
        new_sufficient: bool,
    ) -> None:
        """All twelve reachable (old_hit, new_hit, old_suf, new_suf) states --
        an identity absent on both sides never enters the union at all."""
        folded = _fold_evolution(
            old=Sufficiency(established=old_sufficient, reason=""),
            new=Sufficiency(established=new_sufficient, reason=""),
            old_keys={"k"} if old_hit else set(),
            new_keys={"k"} if new_hit else set(),
        )
        assert folded == {
            "k": _oracle(old_hit, new_hit, old_sufficient, new_sufficient)
        }

    def test_introduced_requires_established_absence_in_old(self) -> None:
        """The headline refusal: NEW flags it, OLD does not, and OLD's
        coverage is not established -- ``introduced`` would be a claim about
        what OLD did not contain, which nothing supports."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="root missing"),
            new=Sufficiency(established=True),
            old_keys=set(),
            new_keys={"k"},
        )
        assert folded == {"k": "not_evaluated"}

    def test_resolved_requires_established_absence_in_new(self) -> None:
        folded = _fold_evolution(
            old=Sufficiency(established=True),
            new=Sufficiency(established=False, reason="root missing"),
            old_keys={"k"},
            new_keys=set(),
        )
        assert folded == {"k": "not_evaluated"}

    def test_persistent_rests_on_observation_not_on_sufficiency(self) -> None:
        """Both sides *observed* the construct. Neither side's incompleteness
        can un-see a hit, so ``persistent`` is established for this identity
        even though neither side's absence claims would be -- this is what
        "the OLD side's coverage **for that specific finding**" means, as
        opposed to one global completeness flag."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="one unreadable file"),
            new=Sufficiency(established=False, reason="one unreadable file"),
            old_keys={"k"},
            new_keys={"k"},
        )
        assert folded == {"k": "persistent"}

    def test_an_identity_is_never_silently_dropped(self) -> None:
        """Every key either side flagged appears in the result, whatever its
        state -- a bare ``continue`` that drops evidence is worse than an
        honest ``not_evaluated``."""
        folded = _fold_evolution(
            old=Sufficiency(established=False, reason="one unreadable file"),
            new=Sufficiency(established=False, reason="one unreadable file"),
            old_keys={"a"},
            new_keys={"b"},
        )
        # "a" was seen only by the (insufficient) OLD side and "b" only by the
        # (insufficient) NEW side: neither direction is decidable, and both
        # keys must still be listed rather than dropped.
        assert folded == {"a": "not_evaluated", "b": "not_evaluated"}


def test_pattern_facts_result_without_an_input_account_is_never_sufficient() -> None:
    """Deny-by-default at the result level too: a hand-built result that
    reports only the legacy tallies cannot claim sufficiency."""
    assert PatternFactsResult(files_scanned=5, files_skipped=0).sufficient is False


def test_resolve_source_inputs_without_a_licence_never_touches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("filesystem touched under a withheld licence")

    monkeypatch.setattr(os, "walk", _fail)
    monkeypatch.setattr(Path, "is_file", _fail)
    monkeypatch.setattr(Path, "is_dir", _fail)
    monkeypatch.setattr(Path, "exists", _fail)

    inputs = resolve_source_inputs([str(tmp_path), "/nonexistent/x.h"])
    assert [i.disposition for i in inputs.inputs] == [
        SourceInputDisposition.NOT_LICENSED
    ] * 2
