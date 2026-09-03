# `main` branch-protection Ruleset — admin runbook

**Decision (2026-09): `main` does not require status checks to pass before a
merge.** This repo previously worked toward making CI completion a hard
merge gate (CLI cleanup phase two's PR 0B/P0 — see
`docs/contribute/plans/cli-cleanup-phase-two.md`'s "PR 0" section for that
history). That required-status-checks Ruleset was applied, and it did what
it says: it blocked pushes/merges to `main` until every required check
finished. The maintainer decided that trade-off isn't wanted — waiting on
CI to merge is a real cost, and this repo chooses to accept the risk of an
occasional red merge over paying it. See `.github/AGENTS.md`'s
"Required-status-check configuration" section for the full rationale and
what replaces the mechanism this runbook used to apply.

Concretely, that means:

- `branch-protection-ruleset.json` in this directory carries **no**
  `required_status_checks` rule anymore. `main`'s only Ruleset-enforced
  protection is `non_fast_forward` (no force-pushes/history rewrites).
- `.github/workflows/verify-merge-checks.yml` — the post-merge audit that
  used to flag a merge whose required checks hadn't actually finished — was
  **removed**. It existed only to compensate for the required-status-checks
  gate not yet being enforced; once the decision became "don't enforce that
  gate at all," the audit had nothing left to usefully report — every merge
  that skips ahead of CI is now expected behavior, not a finding.
- CI still runs on every PR and still matters for review — it's just
  informational with respect to the merge button. A red check does not
  block a maintainer who chooses to merge anyway.

**If this decision is ever reversed** and required-status-checks blocking
is wanted again: re-derive the required-check list from `.github/AGENTS.md`'s
"Required vs. informational workflows" table using that file's own rule (its
"Required-status-check configuration" section), add a `required_status_checks`
rule with that list to `branch-protection-ruleset.json`, and apply it the
same way described below for the `non_fast_forward` rule. Re-adding
`verify-merge-checks.yml` as a compensating post-merge audit is optional at
that point, not required — it only earns its keep while the Ruleset's
enforcement itself is unverified or being rolled out.

## Apply (`non_fast_forward` only)

`branch-protection-ruleset.json` in this directory is the exact API payload.
Create it with the GitHub CLI (needs an admin-scoped token / `gh auth login`
as an account with admin on this repo):

```bash
gh api --method POST -H "Accept: application/vnd.github+json" \
  /repos/abicheck/abicheck/rulesets \
  --input .github/branch-protection-ruleset.json
```

Equivalently, in the web UI: **Settings → Rules → Rulesets → New branch
ruleset**, target branch `main`, enforcement **Active**, add a "Restrict
force pushes" (non-fast-forward) rule. Do **not** add a "Require status
checks to pass" rule — that's the part this repo deliberately opted out of.

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
it.** A `PUT` replaces the *entire* ruleset object, every top-level field —
if the existing one also carries pull-request-review requirements,
signed-commit rules, merge-queue settings, `bypass_actors`, or broader
`conditions` (e.g. it also targets release branches, not just `main`),
overwriting it with this file's payload silently deletes or narrows every
one of those, not just `rules`/`bypass_actors`. In particular, if the
existing ruleset still carries a `required_status_checks` rule from before
this decision was made, fetch it first and remove that rule deliberately
rather than overwriting blind — the point of this update is specifically to
drop that rule, not to leave it in place by accident. Fetch the existing
ruleset first and decide from there:

```bash
gh api /repos/abicheck/abicheck/rulesets | jq '.[] | {id, name}'
gh api /repos/abicheck/abicheck/rulesets/<id> > /tmp/existing-ruleset.json
```

- If `/tmp/existing-ruleset.json`'s `conditions`, `rules`, and
  `bypass_actors` **all** already match what's checked in here (e.g. it was
  created from an earlier version of this same file) — check every one of
  those three, not just `rules`/`bypass_actors` — it's safe to `PUT` this
  file's payload verbatim:

  ```bash
  gh api --method PUT -H "Accept: application/vnd.github+json" \
    /repos/abicheck/abicheck/rulesets/<id> \
    --input .github/branch-protection-ruleset.json
  ```

- If it carries anything else in *any* of those three fields, **merge by
  hand instead of running the command above**: start from
  `/tmp/existing-ruleset.json`, drop any `required_status_checks` rule entry
  (per this decision) and keep/update the `non_fast_forward` rule, keep its
  `conditions` and every other rule/`bypass_actors` entry untouched, save
  the merged result to a file, and `PUT` *that* file (same command as
  above, with `--input` pointed at the merged file) — not
  `branch-protection-ruleset.json` verbatim. **Strip GET-only fields
  first**: `/tmp/existing-ruleset.json` is a raw `GET` response and carries
  server-managed properties (`id`, `node_id`, `source`, `source_type`,
  `created_at`, `updated_at`, `_links`, ...) that the
  [update-ruleset request schema](https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28#update-a-repository-ruleset)
  doesn't accept — `PUT`ting them back can fail validation instead of
  applying the update. Project the fetched object down to `name`, `target`,
  `enforcement`, `bypass_actors`, `conditions`, and `rules` before editing,
  e.g.:

  ```bash
  jq '{name, target, enforcement, bypass_actors, conditions, rules}' \
    /tmp/existing-ruleset.json > /tmp/merged-ruleset.json
  # edit /tmp/merged-ruleset.json by hand as described above, then:
  gh api --method PUT -H "Accept: application/vnd.github+json" \
    /repos/abicheck/abicheck/rulesets/<id> \
    --input /tmp/merged-ruleset.json
  ```
- When merging feels risky or the existing ruleset's purpose is unclear,
  the safe fallback is a **separate, additionally-named** Ruleset (the
  `POST` command in "Apply" above, under a distinct `name`) rather than
  touching the existing one at all — GitHub enforces every active ruleset
  matching a ref simultaneously, so a second ruleset adds to the existing
  protections instead of risking removing them. Note this cuts both ways
  here: a second ruleset can't *remove* a `required_status_checks` rule an
  existing one still enforces, so if the goal is dropping that rule, it has
  to be edited or deleted directly, not shadowed by an additional ruleset.

**If `main` is instead protected by *classic* branch protection** (the
`GET /repos/.../rulesets` call above returns nothing, but
`GET /repos/abicheck/abicheck/branches/main/protection` returns a config) —
`branch-protection-ruleset.json`'s Ruleset payload doesn't apply there at
all; a Ruleset object and a classic protection config are different
resources with different shapes. If that classic config has
`required_status_checks` enabled, disable/clear it there directly (GitHub's
[branch-protection endpoint](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection))
per this decision, and separately apply this Ruleset's `non_fast_forward`
rule as a new, additional Ruleset (the `POST` command in "Apply" above) if
force-push protection isn't already covered by the classic config too.
