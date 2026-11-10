"""Regression contracts for consumer-driven cache retention."""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

CACHE_RETENTION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.retention_decided_by_one_switch_not_by_consumers",
        invariant=(
            "A retention decision taken for several consumers at once must "
            "be resolved per consumer and per side, not by one boolean that "
            "any of them can flip for all of them. A shared switch is not "
            "merely coarse: it is unfalsifiable, because the side nobody "
            "reads looks exactly like the side everybody reads, and no test "
            "of the *output* can tell the difference -- the outputs are "
            "identical either way, which is precisely why the amplification "
            "survived. So the guard is a consumer inventory with an "
            "executable claim attached: the resolved retention says which "
            "side each requested output reads, and a test asserts the "
            "unread side is genuinely absent from the member entry rather "
            "than merely unused. `need_full_snapshots` pinned both sides' "
            "full `AbiSnapshot` for every member whenever JUnit or "
            "`--bundle-facts-out` was requested, while every consumer of "
            "either read only the OLD side; the NEW graph was retained for "
            "the whole release and never opened."
        ),
        fixed_by=(1332,),
        seed_tests=(
            "tests/test_dump_cache_single_tier.py",
            "tests/test_release_snapshot_retention.py",
            "tests/test_compare_release_contract_coverage.py",
            # The same class, one consumer further: JUnit was resolved as
            # an OLD-side full-snapshot consumer without anyone checking
            # what it read, which was four attributes. See
            # `perf.a_whole_document_retained_for_a_narrow_projection`.
            "tests/test_junit_symbol_inventory.py",
            # The last consumer: `--bundle-facts-out` still reads the whole
            # OLD document, so it is kept whole -- on disk, in a spool,
            # from the moment the member finishes (weakref reachability,
            # write peak, byte identity against the eager writer).
            "tests/test_bundle_facts_out_spool.py",
        ),
        public_surfaces=(),
        axes={
            "output": ("json", "junit", "bundle-facts-out", "junit+baseline"),
            "retained_where": ("resident", "spooled"),
            "side": ("old", "new"),
            "member_count": ("one", "several"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The consumer inventory is asserted against the call "
                    "sites as they are, not derived from them: a future "
                    "consumer that starts reading `_new_snapshot` would "
                    "fail the seed test (the key is absent) rather than be "
                    "prevented from being written, which is the intended "
                    "direction but is a test-time signal, not a type-level "
                    "one."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
