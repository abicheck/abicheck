# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Bug classes about *evidence provenance and coverage sufficiency*.

Split out of `manifest.py` (which is at its architecture-gate size baseline)
the same way `manifest_guards.py`/`manifest_report.py`/
`manifest_tool_surface.py` already are; `manifest.py` concatenates them into
the single `BUG_CLASSES` tuple.

The two classes here share a root cause worth naming once: both are about a
check answering a question its *evidence* does not support -- one by reading
today's filesystem for a historical fact, the other by treating whatever a
discovery pass happened to find as the full set it was supposed to look at.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EVIDENCE_BUG_CLASSES"]


EVIDENCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="evidence.stored_snapshot_rederivation",
        invariant=(
            "A path recorded in a snapshot is provenance, not a licence to "
            "re-read the current filesystem for a historical fact. Any "
            "analysis of a stored snapshot must answer from what that "
            "snapshot carries, or from an explicitly supplied, "
            "provenance-verified source context for that side -- never from "
            "whatever now lives at a same-looking path on the current "
            "runner. So a stored snapshot's derived facts are invariant "
            "under every mutation of the source checkout it names (edited, "
            "truncated, substituted, deleted, relocated, made unreadable, "
            "replaced by a directory), and where the confirmed historical "
            "facts are absent the result states that the historical "
            "evaluation was not possible rather than substituting today's. "
            "The licence is resolved **per evidence source**, not per side: "
            "one source's live provenance never licenses reading another's "
            "recorded paths, and a partially-licensed side reports the half "
            "it read while establishing no absence over the half it did not."
        ),
        fixed_by=(1141, 1236),
        seed_tests=("tests/test_stored_snapshot_source_licence.py",),
        axes={
            "side_provenance": (
                "live_extraction",
                "stored_snapshot",
                "verified_context",
            ),
            "front_end": ("cli", "typed_python_api", "abicc_compat"),
            # A side holds up to two independently-provenanced source-evidence
            # sources at once, and each is licensed on its own: a live header
            # dump merged with a pre-captured --build-info pack is live for its
            # declared headers and historical for the pack's compile units.
            "evidence_source": ("declared_headers", "embedded_build_pack"),
            "source_mutation": (
                "unchanged",
                "delete_file",
                "edit_file",
                "truncate_file",
                "substitute_unrelated_file",
                "relocate_tree",
                "replace_file_with_directory",
                "make_unreadable",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the pattern/preprocessor pre-scan reads recorded "
                    "source paths today, so the licence is enforced at that "
                    "one workflow plus the two primitives under it. Nothing "
                    "mechanically forbids a *future* consumer from reading "
                    "AbiSnapshot.source_header (or a compile unit's source) "
                    "without resolving a licence first -- that would need an "
                    "AI-readiness-style AST gate of its own, the way "
                    "fact-field-readers guards Fact[T] reads."
                ),
                reference="docs/contribute/known-gaps.md",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "The licence is object-level, not content-verified: a "
                    "live extraction grants it for the whole run rather than "
                    "digesting each source file and re-checking it at read "
                    "time. Persisting per-input digests (a snapshot schema "
                    "bump) would let a stored snapshot re-derive safely when "
                    "the tree genuinely matches, instead of declining."
                ),
                reference="docs/contribute/known-gaps.md",
                canary_test=None,
            ),
        ),
    ),
    BugClass(
        id="coverage.discovery_derived_completeness",
        invariant=(
            "Sufficiency for an *absence* claim is computed from the "
            "**expected** input set, never from what a discovery pass "
            "happened to find. Every declared input is accounted for by "
            "exactly one disposition -- scanned, missing, unreadable, "
            "unsupported, deliberately excluded, or not licensed -- and any "
            "of those gap states keeps the side insufficient, so a root that "
            "did not exist can never yield 'fully covered'. Sufficiency is "
            "answered per check (each check answering to its own evidence), "
            "not by one global tally. Establishment is then per finding: "
            "presence is established by observation, absence only by "
            "sufficiency, so a fold may claim 'introduced'/'resolved' only "
            "when the side whose *absence* it asserts is established, and an "
            "undecidable identity is reported as not-evaluated rather than "
            "dropped."
        ),
        fixed_by=(1141,),
        seed_tests=(
            "tests/test_source_input_completeness.py",
            "tests/test_pattern_preprocessor_scan_coverage.py",
            "tests/test_preprocessor_probe_families.py",
        ),
        axes={
            "disposition": (
                "selected",
                "scanned",
                "missing",
                "unreadable",
                "unsupported",
                "excluded",
                "not_licensed",
            ),
            "check": ("pattern_escalation", "macro_divergence", "header_leak"),
            "probe_family": ("macro", "header"),
            "input_failure_mode": (
                "missing_root",
                "unreadable_file",
                "unenumerable_directory",
                "unexaminable_candidate",
                "unstattable_candidate",
                "unsupported_node",
            ),
            "fold_state": (
                "persistent",
                "introduced",
                "resolved",
                "not_evaluated",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "A file the classifier cannot *examine* must be told apart "
            "from a file that was never evidence: a two-state "
            "scannable/not-scannable predicate collapses them, and the "
            "unreadable one then vanishes from the account instead of "
            "registering as a gap. The lexical pattern scan's sufficiency is "
            "per *check*, "
                    "but its one check shares a single expected-input set: a "
                    "gap in any declared root makes every PatternKind's "
                    "absence unestablished, since a lexical scan cannot say "
                    "which kind an unread file would have contained. A finer "
                    "per-kind answer would need per-kind input attribution "
                    "the scanner does not have."
                ),
                reference="abicheck/workflows/pattern_preprocessor_scan.py",
                canary_test=None,
            ),
        ),
    ),
)
