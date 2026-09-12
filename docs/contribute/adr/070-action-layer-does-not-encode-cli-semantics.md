# ADR-070: The Action Layer Does Not Encode CLI Semantics

**Date:** 2026-09-12
**Status:** Accepted — D3 implemented for both `extra-args` tokenizers
(`action/run.sh`, `actions/check-target/action.yml`, which no longer carry a
hand-maintained option list); D1, D2 and D4 not yet implemented. Records the
boundary rule and the migration it forces. Extends
[037](037-cli-interface-contract.md)'s D10.1 front-end/engine boundary one
layer outward (from `cli*.py`-vs-Tier-1 to the composite Action-vs-CLI) and
constrains what
[047](047-github-actions-integration-model.md)'s shell layer may assert on
its own. Evidence and phasing:
[`docs/contribute/plans/action-cli-surface-drift.md`](../plans/action-cli-surface-drift.md).
Owners (on implementation): `action/run.sh`, `action/validate-inputs.sh`,
`actions/check-target/action.yml`, `actions/check-target/validate-inputs.sh`.

## Context

The composite Action is a hand-maintained shell adapter over the abicheck
CLI. It does not merely *call* the CLI — it independently re-states what the
CLI accepts, in three forms:

1. **Restriction mirrors.** `::error::` guards that reject an input
   combination because the CLI is believed to reject it.
2. **Fact copies.** Enumerations of CLI data — value-taking option names,
   `--format` choice sets — transcribed into `case` statements.
3. **Justification comments.** Prose explaining a guard by naming a CLI
   symbol or behaviour.

None of the three is checked against the CLI. A 2026-09-12 audit of every
such site (the plan above) found all three classes had rotted while review
passed repeatedly:

- `action/run.sh:2813`, duplicated at `action/validate-inputs.sh:281`, rejects
  the L2 compile context for a directory/package compare because "the
  per-library fan-out never threads" it. `cli_resolve.py` threads it; only a
  *sided* `--ast-frontend old=/new=` is rejected.
- `action/run.sh:2898` drops `--depth headers` as "still rejected by the CLI
  here". `cli_compare_options._resolve_depth_for_set_inputs` forwards every
  rung, and its own docstring records both former rejections as no longer
  true.
- Both `_extra_args_is_value_option` tables listed twelve options retired
  from the CLI entirely and omitted four live ones belonging to `dump`/`deps`.
  The guarding test checked one direction against one command, so neither
  class of error could fail it.
- Four exit-code comments cite `abicheck/cli_scan.py` and `scan_engine`,
  neither of which exists.
- The tokenizer's own comment asserted no live `abicheck` is reachable at run
  time, justifying the hand-maintained snapshot. `action.yml` installs
  abicheck at step 3 and runs `run.sh` at step 4. **That false justification
  was load-bearing:** the audit's first revision adopted it unchecked and
  designed a committed generated artifact around it.

The shared mechanism is not carelessness. It is that a copied CLI fact has no
owner: the CLI changes for its own reasons, nothing points back, and the copy
is discovered only when a user hits it or a reviewer happens to read both
sides. `AGENTS.md`'s "Shared semantics" invariant already says equivalent
resolved requests must decide equally across front ends; an Action that
refuses what the CLI accepts violates it silently.

## Decision

**The Action layer validates its own inputs. It does not re-implement,
restrict, or enumerate CLI semantics.**

Concretely, four rules:

- **D1 — Input grammar only.** A shell guard may reject what is wrong about
  *Action inputs*: an unrecognized `mode`, a required input absent, a pair
  that cannot both be set, a retired Action input, or a limitation of the
  Action's own machinery (e.g. check-target stages one project-wide
  `header:` input, so a bundle cannot be compared at `headers` depth —
  `actions/check-target/validate-inputs.sh:155`, a rule that survives this
  ADR because it is about the Action, not the CLI). It may not reject an
  input because the CLI is believed to reject it.
- **D2 — The CLI owns flag acceptance, configuration resolution, and
  precedence.** Where the Action previously pre-rejected, it forwards and
  lets the CLI answer. `run.sh` already surfaces a refusal well
  (`_is_cli_error()`, the exit-64 arm); that is the reporting path.
- **D3 — Derive, never transcribe.** Where the Action genuinely needs a CLI
  fact, it asks the installed CLI. Both `extra-args` tokenizers run after
  `action.yml`'s own `pip install`, so this is available to them, and a live
  query is *more* correct than any snapshot: it matches the abicheck version
  the workflow actually installed, which may differ from the Action's
  checkout. A committed generated artifact is permitted **only** for
  `action/validate-inputs.sh`, which by design runs before install.
- **D4 — A justification names a checkable thing or is deleted.** A comment
  explaining a guard by citing CLI behaviour must cite a symbol that exists.
  Prose asserting what the CLI does, with nothing verifying it, is the defect
  this ADR exists to retire — not documentation of it.

## Consequences

**Accepted cost: a slower, more generic error for some invalid inputs.**
`validate-inputs.sh` exists to fail fast — an unsupported combination today
fails in seconds, before a multi-minute toolchain install. Deleting a
restriction mirror moves that failure after the install, and the message
becomes the CLI's rather than the Action's tailored one. This is a real
regression for the cases that motivated `validate-inputs.sh`, accepted
because the alternative is what the audit found: fast, specific, *wrong*
rejections of input the CLI supports. D1 keeps fail-fast for every guard
whose rule is the Action's own.

**A user-visible behaviour change.** An input combination the Action refuses
today may succeed after this lands, which is why this needs an ADR rather
than a routine edit (`AGENTS.md`'s Authority rule). Each deleted guard is a
documented migration note, not a silent widening.

**D3 fails closed; it does not fall back.** When the resolved interpreter
cannot import abicheck — a documented self-hosted-runner case that already
warns (`action/run.sh:2095`) — the derivation has no answer, and an
*undetermined* option table is not the same thing as "no option takes a
value".

An earlier revision of this ADR prescribed exactly that conflation, claiming
an opaque-token fallback degrades in the "under-recognition direction, whose
worst case is visible … never a silent wrong analysis". **That was wrong, and
a reviewer's counterexample disproved it** (Codex, PR #1234, P2):
`extra-args: --version --dry-run` is argv the CLI accepts by consuming
`--dry-run` as `--version`'s own value, leaving `dry_run=False` and running a
normal comparison — verified directly against the installed CLI. An opaque
tokenizer reports a real `--dry-run` instead, so the Action skips its
`--write json=`/`-o` injection as it must for a genuine dry run, the
comparison then runs in full, and the requested output is never written while
the report-reading floors go blind. That is silent, and it is an
*over*-detection of `--dry-run` rather than under-detection of anything — so
the direction argument was wrong too, not merely the severity.

The rule is therefore: **an undetermined table is fatal when, and only when,
`extra-args` is non-empty.** With no `extra-args` there is nothing to tokenize
and no decision to get wrong, so a runner whose `python3` cannot import
abicheck keeps working for every invocation that does not use the escape
hatch; that scoping is what keeps this from being a blanket hard failure on a
mismatch the script already merely warns about. Guessing is refused precisely
where a guess would change what runs. A baked static list stays forbidden
here — it would reintroduce what D3 removes, in the one code path production
never exercises.

**Non-goals.** This ADR does not change the Action's input surface, its
outputs, its exit-code interpretation (the audit found all seven values
agree), or the gate algorithm. It does not make the Action introspect at
*authoring* time what it can ask at *run* time.

## Alternatives considered

- **Keep the copies, check them harder.** A bidirectional, multi-command
  introspection test over the option tables landed first (plan Phase 1) and
  immediately found 17 discrepancies, so the mechanism works. It is still
  rejected as the endpoint: it can only check a copy that is *enumerable*.
  Five of the six stale findings were prose reasoning, which no table check
  reaches, and the test cannot know the version the workflow installs. Worth
  keeping as a guard over the derivation; not worth keeping as the answer.
- **Pin each guard to the CLI symbol it mirrors** (plan Phase 4). Cheap, and
  it catches all the deleted-module citations. Rejected as primary because
  symbol *existence* is far weaker than symbol *behaviour*: both known
  drifts live inside functions that still exist and merely reject less. A
  gate trusted for more than it proves is worse than none, so if this lands
  it must document that limit.
- **Run the Action's shell against a real CLI over an input matrix** (plan
  Phase 5). The only candidate that tests guard semantics with no modelling,
  and the only one that can earn the bug class a real `public_surfaces`
  entry. Rejected as primary on cost — the guard space is combinatorial and
  the cells need real binaries and a toolchain — and kept as a curated set
  after D1/D2 shrink the guard set to something enumerable.
