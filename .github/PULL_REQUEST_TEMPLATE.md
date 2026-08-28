## Summary

<!-- One-line description of what this PR does. -->

## Motivation

<!-- Why is this change needed? Link related issues: Closes #NNN -->

## Changes

<!-- List the key changes made. -->

- 

## Bug-fix test contract

<!-- bugfix-test-contract -->
<!--
Required for `fix:` / `perf:` / `security:` PRs; delete this section otherwise.
Enforced by scripts/check_bugfix_test_contract.py. Answer on one line each,
after the colon. The conditional rows only apply if the diff touches that area
— the checker will tell you which ones it wants.

CI reads the live pull-request description from the GitHub event. Updating
this template or committing answers in a repository file does not backfill an
already-open PR: edit that PR's description and keep this marker plus the
answered rows there.

Each question exists because a merged fix in this repo missed exactly that:
#699 -> #721 (tested its own wrong formula, at a toy scale), #753 -> #759 (a
missing list entry fails nothing), #705 -> #758 (asserted YAML text instead of
executing the attack).

The regression test targets the bug CLASS, not only the one reported input
(AGENTS.md "Decision-making principles" -> "A bug fix's regression test
targets the bug class, not the one reported input"; full analysis:
docs/contribute/plans/bug-class-regression-testing.md). "General invariant"
is not satisfied by prose alone: name the test exercising it with inputs
beyond the reported one (generated/property-based, an exhaustive
small-domain enumeration, or several independently-chosen sibling cases)
against a stated oracle that is not the same formula/helper the
implementation itself uses. Check `tests/regressions/manifest.py`
(`BUG_CLASSES`/`get()`) first for a matching bug class before restating
the invariant from scratch — name its `id` here if it already has a home,
or add a new entry there (not just prose) if this fix closes a genuinely
new class.
-->

- Bug class:
- Publicly observable failure:
- Regression test fails on base:
- Negative control:
- Public-surface test:
- Axes covered:
- General invariant:

<!-- Conditional — a menu, not a place to answer. GitHub hides comment
regions from the rendered description, and the checker ignores them for
exactly that reason: an answer nobody can see is not evidence. Copy the
rows the checker asks for OUT of this comment, above it:
- Real-dependency test:
- Malicious fixture + side-effect absence:
- Must-merge / must-not-merge pair:
- False-positive removed / real break preserved:
- Verdict, gate and exit code checked independently:
- Known unsupported cases:
-->

## Checklist

- [ ] Tests added / updated for new behaviour
- [ ] Changelog fragment added (`scriv create`) if this touches `abicheck/**/*.py` — see `changelog.d/README.md`
- [ ] Docs updated if user-visible behaviour changed
- [ ] `pre-commit run --all-files` passes locally
- [ ] No new `mypy` or `ruff` errors introduced
