"""Regression contracts for repeated content-derived work."""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

DERIVED_WORK_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.pure_content_digest_recomputed_per_consumer",
        invariant=(
            "A pure, expensive, content-derived value reached by several "
            "independent consumers within one run must be computed at "
            "most once per distinct input, not once per consumer. The "
            "guard is a count of the expensive work actually performed "
            "during a real run, bounded by the number of distinct inputs "
            "— never an assertion that one named consumer uses the cache, "
            "which forecloses exactly that call site and says nothing "
            "about the next one added."
        ),
        # v19 oneAPI scan: every graph-shaped compare regressed ~1.7-1.8x
        # against v18 (oneTBB pair 431s -> 740s, self-compare 406s -> 726s).
        # cProfile put 404s of a 620s run inside
        # `serialization.snapshot_content_digest` at n=6 over two snapshots;
        # v18 was n=2. A fourth `same_persisted_content` caller had been
        # added without anyone noticing the third, and nothing anywhere
        # measured the count.
        fixed_by=(1245,),
        seed_tests=(
            "tests/test_surface_metrics_fan_in_index.py",
            "tests/test_snapshot_digest_recomputation.py",
            # The same class in the clang header backend: three pure,
            # AST-derived template-parameter indexes rebuilt once per
            # constructed parser rather than once per distinct AST, so a
            # six-member shared-header fan-out ran each builder 14 times for
            # 2 distinct answers. Same guard shape, per this class's own
            # invariant -- a count of the builds a real run performs, bounded
            # by the number of distinct inputs, not an assertion that one
            # named call site consults the cache.
            "tests/test_clang_template_index_reuse.py",
            "tests/test_clang_template_index_reuse_e2e.py",
        ),
        public_surfaces=("cli", "python-api"),
        axes={
            "front_end": ("cli", "typed_api"),
            "pairing": ("content-identical", "genuinely-different"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The bound is enforced for the pairwise `compare` "
                    "paths that open a digest scope. Other snapshot-digest "
                    "consumers outside one — `workflows/"
                    "bundle_stored_pair_compare.py`'s stored-pair route — "
                    "are correct but unmemoized, and no gate fails if a "
                    "future front end forgets to open a scope."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
)
