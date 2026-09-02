---
doc_type: explanation
audience:
  - ci-owner
  - library-maintainer
level: intermediate
canonical_for:
  - compatibility-governance
summarizes:
  - policies
  - suppressions
  - project-integration
depends_on:
  - abicheck/suppression.py
  - abicheck/policy_file.py
  - action.yml
  - actions/check-target/action.yml
lifecycle: active
generated: false
---

# Rollout and Governance

Turning a compatibility check on is not a flag day. A project that has never
run one has an unknown amount of drift already in it, a team that has never
read a compatibility report, and consumers whose expectations nobody has
written down. This page is the order to do it in, and the two artefacts —
a suppression rule and a policy — that let the check say *who accepted
what, until when* instead of quietly hiding it.

## 1. Advisory first

Run the check for real, publish the report for real, and fail nothing. The
per-target Action has an advisory mode for exactly this; the composite
Action has no advisory mode of its own, so every gate it carries has to be
switched off by hand:

```yaml
# actions/check-target: the same check as any scenario, with its gate off
- uses: abicheck/abicheck/actions/check-target@main
  with:
    name: libfoo
    profile: linux-gcc              # a profiles: id from .abicheck.yml
    baseline-channel: accepted-main
    requested-depth: headers
    new-library: build/libfoo.so
    gate-mode: advisory

# the composite Action: relax the break gate and make every category advisory
- uses: abicheck/abicheck@v1
  with:
    old-library: baseline/libfoo.so
    new-library: build/libfoo.so
    new-header: include/
    fail-on-breaking: false
    severity-preset: info-only
```

`fail-on-breaking: false` on its own relaxes only the binary-break gate: a
severity category configured as `error`, a contract-coverage or
analysis-assurance failure under those opt-in inputs, and a removed
library under its own input still fail the step. `severity-preset:
info-only` is what makes every finding advisory, and as an explicit input
it outranks a `severity:` block in `.abicheck.yml` — which is why the
`addition: error` line in [§6](#6-a-minimal-abicheckyml) belongs to the
gated stage, not to this one. An operational error (a missing baseline, an
unreadable binary) still fails either Action, as it should: advisory covers
the *compatibility* verdict, never a broken check.

An advisory run still records its real gate decision in the report; it
only never turns it into a red check. Keep whatever gate you have today
running beside it until a stretch of real pull requests has produced no
finding you could not explain. The scenario is
[S26](../integration/scenarios/migration-and-rollout.md#s26-gate-mode-advisory);
the per-target Action's inputs are in
[check-target](../reference/check-target.md#gate-mode), and the
`.abicheck.yml` topology it reads is the
[project integration](../integration/concepts.md) layer.

## 2. Then gate on the strongest signal only

Flip `fail-on-breaking` back to its default and leave everything else
advisory: a binary break is the one finding nobody argues with. Source-level
API breaks, risk findings and additions stay visible in the report and the
PR comment, and become gates one at a time — `fail-on-api-break` when the
team has seen a few and agrees they are real, a `severity:` block when
additions need a decision ([Report the Surface, Not Only the
Breaks](surface-growth.md)). How each knob feeds the exit code, and the two
exit-code schemes, are owned by [CI Gating](../use/ci-gating.md).

## 3. An intentional break is a labelled, reviewed event

A break you mean to ship is still a break: it must be *detected*, *recorded*
and *reviewed*, and only the gate is relaxed. The label pattern —
`intentional-breaking-change` on the pull request flips the gate off for
that PR and nothing else — does exactly that
([S27](../integration/scenarios/migration-and-rollout.md#s27-a-scoped-visible-relaxation-for-one-pr)).
Two things never happen: the check is not skipped, because a skipped check
leaves the accepted-main baseline stale and every later PR fails against
it ([Where in the Pipeline § Merge to main](where-in-the-pipeline.md#merge-to-main));
and the release baseline is not touched, because that channel records what
shipped, not what was merged
([Baseline Management](../use/baseline-management.md#two-kinds-of-baseline-release-contract-vs-accepted-main)).

The pull request description records what the finding was, which consumers
it affects, what the migration is, and which release carries it — the
report says *what* broke, and only the PR can say *why that is acceptable*.

## 4. Suppressions are contract statements

A suppression is not "make the finding go away". It is a sentence in the
contract: *this change is accepted, by this owner, for this reason, until
this date.* Written that way, a rule is reviewable in the PR that adds it
and auditable in the run that applies it:

```yaml
version: 1
suppressions:
  - namespace: "foo::detail::**"
    reason: "Implementation namespace; not part of the SDK contract (owner: platform team)"
  - symbol: _ZN3foo6LegacyD1Ev
    expires: 2026-12-31
    label: v3-migration
    reason: "Removed in 3.0; consumers on 2.x rebuild before 2027 per the deprecation notice"
```

Two `.abicheck.yml` keys hold the team to that shape:
`suppression.require_justification: true` refuses a rule with no `reason`,
and `suppression.strict: true` turns an expired rule into a failed run
instead of a silently re-appearing finding. The audit flag lists what each
rule is actually doing — matched nothing (stale), matched a breaking change
(worth a second look), expired, or about to:

```bash
abicheck compare old.json new.so -H include/ \
  --suppress suppressions.yaml --audit-suppressions
```

The rule above with the broad `namespace` selector has one more property:
it will not hide a `detail::` change that turns out to be reachable from
the public surface. A broad rule matches only unreachable changes unless it
says `allow_public_break: true`, so a private-looking removal that a public
inline function still calls stays in the report
([case192](../reference/examples/case192_call_graph_break_survives_suppression.md)).
A narrow rule naming one symbol is already an audited decision and is not
gated this way. File format, every selector, and the reachability values
are owned by [Suppressions](../use/suppressions.md); the reachability rule
itself is in
[Suppressions § Reachability-aware suppression](../use/suppressions.md#reachability-aware-suppression).

## 5. Policies name the contract shape

Where a suppression accepts one finding, a policy states what *kind* of
contract the library has, and so how a whole class of findings is scored.
Three base profiles ship: `strict_abi` (the default — a shared library with
unknown consumers), `sdk_vendor` (consumers rebuild on a schedule, so some
source-level churn is accepted) and `plugin_abi` (a host/plugin boundary).
Ecosystem profiles build on `strict_abi` where a platform's own documented
rules differ — `qt_kde_cpp`, `msvc_pe`, `mach_o_dylib`, `rust_c_ffi`,
`glibc_symbol_versioned` among them
([How System Libraries Stay Compatible](system-library-discipline.md) gives
that last one its full treatment):

```bash
abicheck compare old.json new.so -H include/ --policy sdk_vendor
```

What a policy is *for* is easiest to see in the internal-change cases: a
struct nobody outside the library can name gains a field
([case118](../reference/examples/case118_internal_struct_field_added_scoped.md)),
loses one
([case119](../reference/examples/case119_internal_struct_field_removed_scoped.md))
or is reordered
([case120](../reference/examples/case120_internal_struct_reordered_scoped.md)),
and under public-surface scoping the verdict is `NO_CHANGE` — the change is
real, and outside the contract the policy describes. A custom policy file
adds your own `internal_namespaces` convention and per-kind overrides; a
`--pack` is the same overrides as a versioned document shared across
projects, which never outranks a value stated explicitly. Profile contents,
the custom file format, and packs are owned by
[Policy Profiles](../use/policies.md).

## 6. A minimal `.abicheck.yml`

Everything this page has discussed that lives in the config file:

```yaml
version: 1

severity:
  preset: default          # error on abi_breaking and potential_breaking; additions and quality report only
  addition: error          # ...unless the API is frozen: then an unplanned addition gates too (drop this line for a growing SDK)

suppression:
  require_justification: true   # a rule with no `reason` fails at load time
  strict: true                  # an expired rule fails the run instead of silently reappearing

exit_code_scheme: severity  # 0/1/2/4 from the severity tiers above, not the legacy verdict codes
```

There is no `policy:` key: the profile is selected per run with `--policy`
(or the Action's `policy` input), so that the same repository can be
checked as a strict shared library in one job and an SDK in another. Every
key and its type is in the
[Config Keys Reference](../reference/config-keys-reference.md); what the
severity categories mean is owned by [Severity](../use/severity.md).

---

**Ladder:** ← [Report the Surface, Not Only the Breaks](surface-growth.md) · Tier 5 · Practice · [Triage a Suspicious Finding](triage-a-finding.md) →
