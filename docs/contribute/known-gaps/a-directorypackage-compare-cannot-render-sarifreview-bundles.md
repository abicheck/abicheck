---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A directory/package `compare` cannot render sarif/review; `bundles:` cannot state scope policy

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-23): PARTIAL.** The format set is closed: `sarif` and `review` now render for a directory/package `compare` (`abicheck/report/render_release_sarif.py`; `RELEASE_FORMATS` in `frontends/cli/commands/compare_routing.py`). Part 2 below (bundle-level scope policy) is still open.


Two halves of `one-comparison-product.md`'s bundle-parity item that this
pass did *not* close, recorded with what each actually needs. The rest of
that item did land (`assurance.require_complete` accepted, `--write`
repeatable, the machine document no longer implicitly truncated) — see the
changelog entry and `tests/test_one_comparison_product_parity.py`.

**1. The format set.** `compare` renders
`json`/`markdown`/`sarif`/`html`/`junit`/`review`/`oneline`; a directory or
package operand renders `json`/`markdown`/`junit`/`oneline`/`html` and rejects the
rest
(`frontends/cli/commands/compare_routing.py`'s `_RELEASE_FORMATS`). The rejection is
loud rather than silent, and it is not arbitrary — the missing formats are
the ones whose renderers take a single `DiffResult`:

- `sarif` is **closed**: the fan-out keeps each member's `DiffResult` only
  for the pair formats (`junit`, `sarif`; `frontends/cli/release_summary._PAIR_FORMATS`),
  and the SARIF log is one run per member, each run the single-pair
  `to_sarif` projection, with every result stamped `properties.releaseMember`.
  Each run keeps its own rule catalog, so `ruleIndex` values stay valid.
- `review` is **closed** as a release-level digest: the verdict, exit code,
  per-member counts and each member's breaking findings, rendered from the
  release JSON document. The per-member `review_digest_document` needs a
  per-member `ReportEnvelope` (old and new snapshots) that the fan-out does
  not retain, so it is not reused per member. The aggregate shape question
  is answered by that digest, not by a per-library PR comment.
`oneline` was the fourth of that list and is **closed**: it never needed a
`DiffResult` at all (it is a count summary, and the release summary already
carries every count), so `report/release_oneline.py` folds the per-library
counts through the same `format_stat_line` a single-pair `compare` renders
and the release path accepts `-o oneline=...`/`-o oneline=...` like
any other. `html` is **closed** the same way: `report/render_release_html.py`
renders the release JSON document itself (headline, per-member table,
comparison scope, release coherence findings and the recorded dependency
graph), so it needs no member `DiffResult` either. It is a release page, not
the single-pair HTML report; per-member finding detail stays in the JSON.

Library count no longer changes which formats a release can render:
`sarif` and `review` are available for one library and for N.

**2. Scope policy at bundle level.** ADR-065's scope policy is fully
expressible for a directory/package `compare` — `--select`,
`--select-required` and `.abicheck.yml`'s `scope.on_incomplete` all apply to
a release operand, and `--select-required` naming an absent member really
does floor the exit code (verified live). What has no expression is the
*project-config* bundle surface: `buildsource/project_targets.py`'s
`BundleSpec` accepts only `targets:` and `checks:`, so a `bundles:` entry
cannot state required members beyond its own membership list, nor its own
permitted-incompleteness policy, nor a selection narrower than the whole
bundle. A project that wants "these four of the six must be present, the
other two may be missing" has to say it per invocation rather than in the
config the Action reads. That is a `BundleSpec` schema addition plus a
`check-target` input, in the ADR-047 project-targets layer — not a
`compare` change, which is why it is listed separately from the format set
above.
