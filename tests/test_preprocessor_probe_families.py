# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Direct tests for the S2 preprocessor tier's probe-family primitive.

``preprocessor_probe_families.py`` is a small, reusable
tally-and-cap primitive, and AGENTS.md asks for such a primitive to be tested
against its own contract rather than only through its highest-level caller
("Primitive-level property tests"). It also exists *because* two callers hand-
copied "trim, add to the count, append a message" and the copies drifted, so
tests that only reach it through one ``capture_*`` method would miss exactly the
class of bug it was extracted to prevent.

Its contract:

- every probe is counted against exactly one family, and the per-family tallies
  sum to what the caller observed;
- a family's tallies are unaffected by any other family's activity — the
  cross-contamination that made one check's sufficiency depend on the other's
  failures;
- the cap trims to the configured bound, tallies precisely what it dropped, and
  says so.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.preprocessor_probe_families import (
    DEFAULT_MAX_PROBES,
    HEADER_PROBES,
    MACRO_PROBES,
    PROBE_FAMILIES,
    ProbeTallies,
    apply_probe_cap,
    max_probes,
)


class TestProbeTallies:
    def test_a_successful_probe_counts_once_against_its_family(self) -> None:
        t = ProbeTallies()
        t.record(MACRO_PROBES, ok=True)
        assert t.attempted == {MACRO_PROBES: 1}
        assert t.succeeded == {MACRO_PROBES: 1}

    def test_a_failed_probe_is_attempted_but_not_succeeded(self) -> None:
        t = ProbeTallies()
        t.record(MACRO_PROBES, ok=False)
        assert t.attempted == {MACRO_PROBES: 1}
        assert t.succeeded == {}

    @pytest.mark.parametrize(
        "macro_ok,macro_bad,header_ok,header_bad",
        list(itertools.product(range(3), repeat=4)),
    )
    def test_families_never_contaminate_each_other(
        self, macro_ok: int, macro_bad: int, header_ok: int, header_bad: int
    ) -> None:
        """Generated over every small mix of the two families' outcomes: each
        family's tallies depend only on its own probes. This is the invariant
        whose absence let a truncated compile-unit set mark the header-leak
        check insufficient."""
        t = ProbeTallies()
        for _ in range(macro_ok):
            t.record(MACRO_PROBES, ok=True)
        for _ in range(macro_bad):
            t.record(MACRO_PROBES, ok=False)
        for _ in range(header_ok):
            t.record(HEADER_PROBES, ok=True)
        for _ in range(header_bad):
            t.record(HEADER_PROBES, ok=False)

        assert t.attempted.get(MACRO_PROBES, 0) == macro_ok + macro_bad
        assert t.succeeded.get(MACRO_PROBES, 0) == macro_ok
        assert t.attempted.get(HEADER_PROBES, 0) == header_ok + header_bad
        assert t.succeeded.get(HEADER_PROBES, 0) == header_ok
        # And the aggregate a caller derives is the sum of the parts.
        assert (
            sum(t.attempted.values()) == macro_ok + macro_bad + header_ok + header_bad
        )

    @pytest.mark.parametrize("count", [-3, -1, 0])
    def test_a_non_positive_truncation_records_nothing(self, count: int) -> None:
        """ "Nothing was dropped" must not create a zero entry that later reads
        as a gap."""
        t = ProbeTallies()
        t.record_truncated(MACRO_PROBES, count)
        assert t.truncated == {}

    def test_truncations_accumulate_per_family(self) -> None:
        t = ProbeTallies()
        t.record_truncated(MACRO_PROBES, 2)
        t.record_truncated(MACRO_PROBES, 3)
        t.record_truncated(HEADER_PROBES, 1)
        assert t.truncated == {MACRO_PROBES: 5, HEADER_PROBES: 1}

    def test_snapshot_is_detached_from_further_recording(self) -> None:
        """A result object holds a snapshot, so probes that run afterwards (or
        a shared extractor reused across sides) cannot mutate it."""
        t = ProbeTallies()
        t.record(MACRO_PROBES, ok=True)
        taken = t.snapshot()
        t.record(MACRO_PROBES, ok=True)
        t.record_truncated(MACRO_PROBES, 4)
        assert taken.attempted == {MACRO_PROBES: 1}
        assert taken.truncated == {}
        assert t.attempted == {MACRO_PROBES: 2}

    def test_to_dict_uses_the_report_key_spelling(self) -> None:
        t = ProbeTallies()
        t.record(HEADER_PROBES, ok=True)
        t.record_truncated(HEADER_PROBES, 2)
        assert t.to_dict() == {
            "family_attempted": {HEADER_PROBES: 1},
            "family_succeeded": {HEADER_PROBES: 1},
            "family_truncated": {HEADER_PROBES: 2},
        }

    def test_to_dict_copies_rather_than_aliasing(self) -> None:
        t = ProbeTallies()
        t.record(MACRO_PROBES, ok=True)
        emitted = t.to_dict()
        t.record(MACRO_PROBES, ok=True)
        assert emitted["family_attempted"] == {MACRO_PROBES: 1}

    def test_concurrent_recording_loses_no_probe(self) -> None:
        """A dict entry's ``+= 1`` is not atomic, and these are updated from the
        probe thread pool — so the lock is load-bearing, not decorative."""
        import threading

        t = ProbeTallies()
        per_thread = 200

        def _worker(family: str) -> None:
            for _ in range(per_thread):
                t.record(family, ok=True)

        threads = [
            threading.Thread(target=_worker, args=(f,))
            for f in (MACRO_PROBES, HEADER_PROBES) * 4
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert t.attempted == {
            MACRO_PROBES: per_thread * 4,
            HEADER_PROBES: per_thread * 4,
        }
        assert t.succeeded == t.attempted


class TestMaxProbes:
    def test_default_applies_with_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", raising=False)
        assert max_probes() == DEFAULT_MAX_PROBES

    @pytest.mark.parametrize("raw", ["7", " 7 ", "7\n"])
    def test_a_positive_override_is_honored_and_stripped(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", raw)
        assert max_probes() == 7

    @pytest.mark.parametrize("raw", ["", "   ", "0", "-4", "nonsense", "1.5", "7x"])
    def test_a_non_positive_or_unparsable_override_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Exhaustive over the malformed shapes: the cap must never become 0 or
        negative, which would silently truncate *every* probe and read as a
        total coverage gap."""
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", raw)
        assert max_probes() == DEFAULT_MAX_PROBES


class TestApplyProbeCap:
    @pytest.mark.parametrize("family", PROBE_FAMILIES)
    @pytest.mark.parametrize(
        "total,cap", list(itertools.product(range(5), range(1, 5)))
    )
    def test_cap_trims_and_tallies_exactly_what_it_dropped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        family: str,
        total: int,
        cap: int,
    ) -> None:
        """Exhaustive over every small (total, cap) pair for both families,
        against an independently stated rule: keep ``min(total, cap)``, tally
        the remainder, and emit a diagnostic exactly when something was
        dropped."""
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", str(cap))
        tallies = ProbeTallies()
        items = list(range(total))

        kept, diagnostic = apply_probe_cap(items, family, tallies)

        expected_dropped = max(0, total - cap)
        assert kept == items[: min(total, cap)]
        assert tallies.truncated.get(family, 0) == expected_dropped
        assert (diagnostic is not None) is (expected_dropped > 0)
        if diagnostic:
            assert str(cap) in diagnostic
            assert str(expected_dropped) in diagnostic
            assert "ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES" in diagnostic

    def test_each_family_names_its_own_unit_in_the_diagnostic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message a user reads must say what was skipped; the two families
        skip different things."""
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "1")
        macro_msg = apply_probe_cap([1, 2], MACRO_PROBES, ProbeTallies())[1]
        header_msg = apply_probe_cap([1, 2], HEADER_PROBES, ProbeTallies())[1]
        assert macro_msg and "compile unit(s)" in macro_msg and "macro" in macro_msg
        assert header_msg and "public header(s)" in header_msg
        assert "header-leak" in header_msg

    @pytest.mark.parametrize("family", PROBE_FAMILIES)
    def test_capping_one_family_never_tallies_against_the_other(
        self, monkeypatch: pytest.MonkeyPatch, family: str
    ) -> None:
        """The cross-contamination invariant, at the cap: the reported defect
        was a truncated compile-unit set counting against the header check."""
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "1")
        tallies = ProbeTallies()
        apply_probe_cap([1, 2, 3], family, tallies)
        other = next(f for f in PROBE_FAMILIES if f != family)
        assert tallies.truncated.get(other, 0) == 0

    def test_the_cap_is_read_per_call_not_frozen_at_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "1")
        assert len(apply_probe_cap([1, 2, 3], MACRO_PROBES, ProbeTallies())[0]) == 1
        monkeypatch.setenv("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "3")
        assert len(apply_probe_cap([1, 2, 3], MACRO_PROBES, ProbeTallies())[0]) == 3


def test_every_family_is_enumerated() -> None:
    """`PROBE_FAMILIES` is what stops a third family being added without
    choosing where it counts — the same exhaustiveness discipline the
    `ChangeKind` partition gate applies."""
    assert set(PROBE_FAMILIES) == {MACRO_PROBES, HEADER_PROBES}
    assert len(PROBE_FAMILIES) == len(set(PROBE_FAMILIES))
