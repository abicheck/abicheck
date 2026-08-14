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

Each question exists because a merged fix in this repo missed exactly that:
#699 -> #721 (tested its own wrong formula, at a toy scale), #753 -> #759 (a
missing list entry fails nothing), #705 -> #758 (asserted YAML text instead of
executing the attack).
-->

- Bug class:
- Publicly observable failure:
- Regression test fails on base:
- Negative control:
- Public-surface test:
- Axes covered:
- General invariant:

<!-- Conditional — answer the ones the checker asks for:
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
