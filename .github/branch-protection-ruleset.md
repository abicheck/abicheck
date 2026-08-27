# `main` required-status-checks Ruleset — admin runbook

This is the one remaining manual step for CLI cleanup phase two's **PR 0B /
P0** (see `docs/contribute/plans/cli-cleanup-phase-two.md`'s "PR 0 — restore
a green CI baseline first" section and this directory's `AGENTS.md`
"Required-status-check configuration" section). Every code-side
prerequisite is implemented and tested
(`tests/test_required_checks_governance.py`); what is missing is that the
branch API still reports `main` as `protected: true` with
`required_status_checks.enforcement_level: off` and an empty required-checks
list — so today a red required check does **not** block a merge.

No tool available to an automated PR reaches repository-admin Ruleset
configuration (confirmed: neither the GitHub MCP server's tool set nor any
CLI available in this environment exposes branch-protection/Ruleset
administration). This has to be run by a human with admin access on the
`abicheck/abicheck` repository.

## Apply

`branch-protection-ruleset.json` in this directory is the exact API payload,
derived from — and kept in lockstep by
`tests/test_required_checks_governance.py::TestBranchRulesetArtifact` with —
the required-check list in this directory's `AGENTS.md` and the identical
`REQUIRED_CHECKS` array in `workflows/verify-merge-checks.yml`. Create it
with the GitHub CLI (needs an admin-scoped token / `gh auth login` as an
account with admin on this repo):

```bash
gh api --method POST -H "Accept: application/vnd.github+json" \
  /repos/abicheck/abicheck/rulesets \
  --input .github/branch-protection-ruleset.json
```

Equivalently, in the web UI: **Settings → Rules → Rulesets → New branch
ruleset**, target branch `main`, enforcement **Active**, add a
"Require status checks to pass" rule and add each of the 14 contexts listed
in the JSON file (exact spelling matters — GitHub matches a required status
check by its reported check-run *name*, not the workflow job id; see
`AGENTS.md`'s rule for why `docs-pr (required)`/`test-action (required)` are
the two neutral-aggregate gate jobs' names, not `build-docs`/`test-action
summary`).

## Update instead of duplicate

Check both mechanisms before applying anything — GitHub Rulesets and
*classic* branch protection are two separate systems with two separate APIs,
and only one of the two runbooks below applies depending on which one
`main` currently has (the two can also coexist; if both are present, the
more restrictive of the two wins per check, so applying this Ruleset
alongside an existing classic config is safe even if consolidating them
isn't done in the same pass).

**If a Ruleset with this exact name already exists** (`gh api
/repos/abicheck/abicheck/rulesets` returns one — this is what `main`'s repo
settings show today, since `protected: true` alone doesn't say which
mechanism supplies it), **do not blindly `PUT` this file's payload over
it.** A `PUT` replaces the *entire* ruleset object — if the existing one
also carries pull-request-review requirements, signed-commit rules,
merge-queue settings, or `bypass_actors`, overwriting it with this file's
`rules`/`bypass_actors` (`required_status_checks`/`non_fast_forward` only,
empty `bypass_actors`) silently deletes those unrelated protections. Fetch
the existing ruleset first and decide from there:

```bash
gh api /repos/abicheck/abicheck/rulesets | jq '.[] | {id, name}'
gh api /repos/abicheck/abicheck/rulesets/<id> > /tmp/existing-ruleset.json
```

- If `/tmp/existing-ruleset.json`'s `rules`/`bypass_actors` already match
  what's checked in here (e.g. it was created from an earlier version of
  this same file), `PUT` this file's payload — there's nothing to lose.
- If it carries anything else, **merge by hand**: start from
  `/tmp/existing-ruleset.json`, add/update only the
  `required_status_checks`/`non_fast_forward` rule entries from
  `branch-protection-ruleset.json`, keep its other rules and
  `bypass_actors` untouched, and `PUT` the merged result — not this file
  verbatim.
- When merging feels risky or the existing ruleset's purpose is unclear,
  the safe fallback is a **separate, additionally-named** Ruleset (the
  `POST` command in "Apply" above, under a distinct `name`) rather than
  touching the existing one at all — GitHub enforces every active ruleset
  matching a ref simultaneously, so a second ruleset adds to the existing
  protections instead of risking removing them.

**If `main` is instead protected by *classic* branch protection** (the
`GET /repos/.../rulesets` call above returns nothing, but
`GET /repos/abicheck/abicheck/branches/main/protection` returns a config) —
`branch-protection-ruleset.json`'s Ruleset payload doesn't apply there at
all; a Ruleset object and a classic protection config are different
resources with different shapes, and `PUT /rulesets/<id>` has no classic
equivalent. Either update the classic config directly with GitHub's
[branch-protection endpoint](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection),
translating this JSON's `required_status_checks.required_status_checks`
context list into that endpoint's own
`required_status_checks.contexts`/`checks` array, or — the simpler path,
and the one this repo's docs assume going forward — leave the classic
config as-is and `POST` this Ruleset payload as a *new*, additional
Ruleset (the `POST` command in "Apply" above); the Ruleset's own
requirements are enforced independently and at least as strictly either
way.

## Verify enforcement is real, not just configured

Configuring the rule is not the same as confirming it blocks a merge —
`branch-protection-ruleset.json` being applied only proves the rule exists,
not that GitHub is acting on it. Do a negative test:

1. Open a throwaway PR against `main` that fails one required check (e.g. a
   deliberate `ruff` violation to fail `lint-and-types`).
2. Confirm the PR's merge button is disabled/blocked while that check is red
   — not just "shows a warning."
3. Fix the violation, confirm the check goes green, confirm the merge button
   becomes available.
4. Close/delete the throwaway PR and branch; do not merge it.

Only after that negative test passes is PR 0B/P0 actually done — see
`workflows/verify-merge-checks.yml` for the complementary *post-merge*
detector, which catches a merge that slipped through a misconfigured or
momentarily-disabled ruleset after the fact, but cannot substitute for this
pre-merge check.

## If the required-check list changes

Don't hand-edit `branch-protection-ruleset.json`'s `required_status_checks`
list without re-deriving it from `AGENTS.md`'s rule first (see that file's
"Required-status-check configuration" section) — apply the rule fresh
against the current "Required vs. informational workflows" table, update
`AGENTS.md`'s list, `workflows/verify-merge-checks.yml`'s `REQUIRED_CHECKS`
array, and this JSON file together, then re-apply the ruleset with the `PUT`
form above. `tests/test_required_checks_governance.py` fails if any of the
three drift apart.
