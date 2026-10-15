# `main` branch-protection Ruleset — admin runbook

**Decision (2026-10-15): one check gates merges into `main`: `lint-and-types`.**
The `lint-and-types` job in `.github/workflows/ci.yml` (lint, format, mypy,
strict docs build, generated example docs) is the only required status check.
No other check is required. This reverses the 2026-09 decision that `main`
requires no status checks. See `.github/AGENTS.md`'s
"Required-status-check configuration" section for the rationale.

What the Ruleset enforces on `main`:

- `non_fast_forward`: no force-pushes or history rewrites.
- `required_status_checks` with the single context `lint-and-types`. The
  check-run name is the job id, because the job sets no `name:`. The job has
  no path filter and runs on every pull request to `main`.
- A repository-admin bypass (`bypass_actors`, `RepositoryRole`, admin, actor
  id `5`, `bypass_mode: always`) so an urgent merge is possible. This is a
  maintainer choice. Remove the entry to make the gate bind admins too.

Applying the JSON is an **admin step**. Nothing in this repository applies
it. Until a repository admin applies it, `main` does not enforce the check.

`.github/workflows/verify-merge-checks.yml` (the post-merge audit from the
2026-09 era) stays removed. It is not needed to enforce the required check.

**To change the required set:** edit `branch-protection-ruleset.json`, then
update `tests/test_required_checks_governance.py` (it pins the set to exactly
`lint-and-types`) and `.github/AGENTS.md`'s section together. Re-apply the
Ruleset afterwards. Do not add a bridge job that polls another workflow's
check: `tests/test_required_checks_governance.py` rejects it.

## Apply

`branch-protection-ruleset.json` in this directory is the exact API payload.
Create it with the GitHub CLI (needs an admin-scoped token / `gh auth login`
as an account with admin on this repo):

```bash
gh api --method POST -H "Accept: application/vnd.github+json" \
  /repos/abicheck/abicheck/rulesets \
  --input .github/branch-protection-ruleset.json
```

Equivalently, in the web UI: **Settings → Rules → Rulesets → New branch
ruleset**, target branch `main`, enforcement **Active**. Add a "Restrict
force pushes" (non-fast-forward) rule and a "Require status checks to pass"
rule with the single check `lint-and-types`. Under bypass, add the
Repository admin role (the bypass the JSON declares), or omit it if the gate
should bind admins too.

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
existing ruleset carries a `required_status_checks` rule, check that its
contexts are exactly `lint-and-types` before overwriting it, rather than
overwriting blind. Fetch the existing ruleset first and decide from there:

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
  `/tmp/existing-ruleset.json`, make its `required_status_checks` rule (if
  any) require exactly `lint-and-types`, keep the `non_fast_forward` rule,
  keep its `conditions` and every other rule/`bypass_actors` entry untouched, save
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
  here: a second ruleset can't *narrow* a `required_status_checks` rule an
  existing one already enforces (for example, one requiring more checks than
  `lint-and-types`), so such a rule has to be edited directly.

**If `main` is instead protected by *classic* branch protection** (the
`GET /repos/.../rulesets` call above returns nothing, but
`GET /repos/abicheck/abicheck/branches/main/protection` returns a config) —
`branch-protection-ruleset.json`'s Ruleset payload doesn't apply there at
all; a Ruleset object and a classic protection config are different
resources with different shapes. If that classic config has
`required_status_checks` enabled, make it require exactly `lint-and-types`
there (GitHub's
[branch-protection endpoint](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection)),
and separately apply this Ruleset (the `POST` command in "Apply" above) if
force-push protection or the admin bypass isn't already covered by the
classic config too.
