### Fixed

- **Internal: two more real Codex findings on this PR's own reconciliation
  work.**
  - `find_triggers()` (`scripts/check_docs_review_triggers.py`) reads each
    page's own `depends_on` front matter — not `docs/_meta/topics.yaml`'s
    `fact_sources`, a separate, unrelated ownership-verification mechanism.
    An earlier round only updated `topics.yaml`, so `docs/use/python-api.md`
    (which had no `depends_on` block at all) and `docs/use/policies.md`
    (front matter present, but no `depends_on`) stayed invisible to the
    review-trigger workflow for `workflows/contracts.py`,
    `workflows/request_inputs.py`, `model/header_ast_frontends.py`, and
    `policy/reclassify.py`. Added `depends_on` to both pages (mirroring
    each page's already-correct `topics.yaml` `fact_sources`), and added
    `policy/evidence_status.py` to `docs/learn/verdicts.md`'s `depends_on`
    plus the `verdicts` topic's `fact_sources` (missed entirely in an
    earlier round). New parametrized cases in
    `tests/test_docs_review_triggers.py` prove `find_triggers()` now fires
    on all five paths against the live `docs/` tree.
  - `architecture/dispositions.yaml`'s `contract_coverage_ledger.py`
    "retain" rationale (written in the previous round) overclaimed: it said
    the two `frontends` call sites this record's original trial run
    flagged (`cli_compare_release_pairwise.py:424`,
    `cli_scan_baseline.py:949`) were routed through the real owner
    directly. They weren't, and per the `frontends -> policy` dependency
    rule (forbidden without a `workflows`-owned wrapper), they couldn't be
    without first landing one — the same `frontends -> policy` conflict
    the pre-migration record already named. Corrected the rationale to
    state the real, honest reason the facade stays retained (it's the
    escape hatch for those two specific callers, not just external
    compatibility) and left the wrapper as recorded, open follow-up work
    rather than silently claiming it done.
