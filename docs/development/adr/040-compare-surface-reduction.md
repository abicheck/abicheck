# ADR-040: `compare` Surface Reduction — Side-Aware Flags, Config Demotion, Run Profiles

**Status:** Accepted — phased implementation (Phase A run profiles + Phase B
evidence-family collapse landed; Phase C Lever-1 remainder landed except the
`ast-frontend` carve-out; Phase D landed as a constraint-aware subset —
debug-resolution + `--show-redundant` demoted to config, toolchain and
`--scope-public-headers` deliberately retained, see "Rollout"). Targets
**0.5.0** (hard break, no alias window — consistent with how ADR-037 removed
`--header-backend`).

## Context

ADR-037 (D10.5) gave `compare` a visible-flag budget with an explicit
end-state target of **~20** flags and an interim ceiling that only ratchets
up with a documented rationale (see `COMPARE_FLAG_BUDGET_RAISES`). As of
0.4.x `compare` sits at **79 visible flags** — ~4× the target and 1.5× the
next-largest command (`compat check` at 53). The budget mechanism has held
the *rate* of growth but has not moved the count *down*: the deprecation
window hid 12 flags but never removed them, and no structural reduction has
landed.

A breakdown of the 79 by nature shows the mass is **not** in analysis
features (each new capability is one honest per-run flag) but in two
structural groups:

| Group | Example flags | Count | Nature |
|-------|---------------|-------|--------|
| **(A) Per-side triples** | `--header` / `--old-header` / `--new-header`, and the same for `include`, `sources`, `build-info`, `ast-frontend`, `pdb-path`, `version`, `debug-info{1,2}`, `devel-pkg{1,2}`, `debug-root{1,2}`, `probe-matrix-{old,new}` | ~28 | one concept split across 2–3 spellings |
| **(B) Stable project properties** | toolchain (`--gcc-*`, `--sysroot`, `--nostdinc`), debug-resolution (`--debug-root*`, `--debuginfod*`), `--scope-public-headers`, `--show-redundant` | ~14 | reviewed-once project settings, not per-run decisions |
| **(C) Genuine per-run analysis inputs** | `--depth`, `--policy`, `--env-matrix`, `--post-manifest`, `--reconcile-build-context`, `--pattern-verdicts`, report shaping | ~20 | correctly CLI flags |

Group (C) is already at the ~20 target. The reduction problem is entirely
(A) + (B). This ADR specifies three levers that eliminate them.

## Decision

### Lever 1 — Side-aware flags (collapses group A)

Replace every `--old-X` / `--new-X` / `--X` triple with a **single repeatable
`--X`** that accepts an optional `old=` / `new=` / `both=` side prefix:

```text
--header PATH            # applies to both sides (was: -H / --header)
--header old=PATH        # old side only (was: --old-header)
--header new=PATH        # new side only (was: --new-header)
```

* **Repeatable**, so multiple headers/includes still work.
* A **bare value** (no recognised prefix) means both sides — the common case
  stays terminal-cheap and identical to today's `-H`.
* `both=` is an explicit escape hatch for the vanishingly rare path that
  literally begins `old=` / `new=`.
* One parser, `parse_sided_values()`, in `cli_options.py`, applied uniformly
  by a `sided_option(name, dest, help, ...)` decorator factory. The decorator
  emits exactly one Click option per concept.

Concepts collapsed (each 2–3 flags → 1): `header`, `include`, `sources`,
`build-info`, `ast-frontend`, `pdb-path`, `version`, `debug-info`,
`devel-pkg`, `debug-root`, `probe-matrix`. **~28 flags → ~11.**

**Boundary normalization keeps the blast radius shallow.** `compare_cmd`
forwards `**kwargs` to `cli_compare_helpers.run_compare`, which already
resolves per-side inputs via `_resolve_per_side_options`. The side-aware
decorator normalizes its parsed value back into the *existing* internal
kwargs (`headers`, `old_headers_only`, `new_headers_only`, …) **before**
`run_compare` sees them, so the engine, the Tier-2 service, and the ABICC
compat layer are unchanged — only the user-facing surface and its tests/docs
move.

`-H` / `-I` short aliases are retained (they are the muscle-memory spelling
and cost nothing against the budget beyond their long form, which is now the
canonical single flag).

### Lever 2 — Config demotion (eliminates group B)

Move stable project properties off the CLI into `.abicheck.yml` blocks, per
the ADR-037 D4 decision table ("stable project property, reviewed in PRs? →
config"). CLI keeps only a coarse per-run override where one is genuinely
useful.

| Family | New config block | CLI after |
|--------|------------------|-----------|
| Toolchain (`--gcc-path/-prefix/-options/-option`, `--sysroot`, `--nostdinc`) | `compile:` (already read for the L2 context) | **retained** — the family is declared in the `compare`/`dump`/`scan`-shared `@compile_context_options` decorator (ADR-037 D3 parity); demoting it for `compare` alone would fork that shared family, out of scope for a *compare* reduction. |
| Debug resolution (`--debuginfod`, `--debuginfod-url`, `--debug-format`, `--dwarf-only`) | `debug:` (new) | hidden + config-read (still overrides config); the coarse per-run `--debug-root` stays a **visible** override. |
| Public-surface scoping (`--show-redundant`) | `scope:` (already exists — `show_redundant` key) | hidden + config-read; `--scope-public-headers` **retained visible** (everyday on/off switch), `--show-filtered` debugging view kept. |

> **Amendment note (docs review, 2026-07):** the flag counts in this ADR
> (79, 62, 57, ~20, …) are point-in-time snapshots of the reduction as it
> landed, not a living count. Verified against the code at the time of this
> note: `COMPARE_FLAG_BUDGET_BASE == 57` and the live `compare` command
> exposes 61 visible flags (`BASE` + `COMPARE_FLAG_BUDGET_RAISES`), matching
> the Phase D end-state below — no drift found. `abicheck/cli_options.py`
> (`COMPARE_FLAG_BUDGET_BASE`/`_RAISES`/`_BUDGET`) is the machine-checked
> source of truth going forward; run `abicheck compare --help` or
> `tests/test_config_rebalance.py::TestFlagBudget` for the current number
> instead of trusting a number in this prose.

As implemented (the constraint-aware subset chosen for this PR), demotion follows
the established **hide-then-config** cadence used by the severity/suppression
families: the demoted flags are marked `hidden` and read their default from the
new/extended config block, while an explicit flag still wins (`CLI > config`).
Earlier drafts of this table proposed hard-removing the toolchain family and
`--scope-public-headers`; both are retained for the reasons in the cells above.

Historically the severity/suppression families were already `hidden` in 0.4.x;
this phase wires the debug/scope config home the same way. A later revision may
still remove the hidden flags whose config home now exists and are wired to read
from it. **~14 flags → ~2.**

### Lever 3 — Run profiles (removes the need to *type* common combos)

Add a single `--profile NAME` that expands to a named bundle of per-run
settings, mirroring how `--severity-preset` already collapses four severity
flags into one. Profiles are the "one token for a whole workflow" ergonomic
that keeps casual invocations short without adding one flag per knob.

```text
--profile ci-gate     # depth=headers, format=review, exit=severity
--profile release     # depth=full, recommend, format=markdown
--profile quick       # depth=binary, stat
```

* Precedence is **explicit flag > profile > project config > default**: a
  `--profile` is a per-run typed choice, so it overrides `.abicheck.yml`
  defaults, while a genuinely typed flag still overrides the profile. Injection
  is value-only (no command-line source stamping).
* Profiles are **single-pair-only**: they bundle single-pair knobs (`--depth`,
  `--exit-code-scheme`, the `review` format) the directory/package release
  fan-out rejects. `--profile` on set inputs is a usage error pointing at
  `.abicheck.yml` (the fan-out's config home) — consistent with the existing
  set-input flag rejections, and avoiding the per-key/per-value special cases a
  "apply the safe subset" rule would need. Public-surface scoping is the
  default, so profiles don't restate it.
* Profiles are data (`COMPARE_PROFILES` table), so a project can ship its own
  in `.abicheck.yml` under `profiles:` — but the built-ins cover the three
  documented workflows out of the box.
* One visible flag (`--profile`) replaces the *habit* of typing 4–6, without
  removing the underlying flags for power users.

### Net effect

| | flags | landed `BASE` |
|---|---|---|
| Today | 79 | 76 |
| After Lever 1 (−~17) | ~62 | 62 |
| After Lever 2 (this PR's constraint-aware subset, −5) | ~57 | 57 |
| After Lever 2 reaches the full reference target set (toolchain redesigned cross-command, later) | **~20** (+ `--profile`) | — |

The original `~50`/`~20` projection assumed the full Lever-2 table (hard-removing
the toolchain family and `--scope-public-headers`). This PR lands the
constraint-aware subset (−5 visible: the `debug:` block + `scope.show_redundant`);
the deeper cut needs the shared `@compile_context_options` family redesigned
across `compare`/`dump`/`scan`, tracked separately. The precise ~20 target set is
the reference list in `docs/development/adr/037-cli-interface-contract.md#d4` plus
the side-aware single flags and `--profile`.

## Consequences

* **Breaking (0.5.0, no alias window).** Existing invocations using
  `--old-header X --new-header Y` become `--header old=X --header new=Y`; the
  debug-resolution knobs (`--debug-format`/`--debuginfod`/`--debuginfod-url`/
  `--dwarf-only`) and `--show-redundant` move to `.abicheck.yml` (still accepted
  as hidden overrides). The toolchain family is retained. A migration table ships
  in `docs/user-guide/migration-0.5.md` and the CHANGELOG. This matches the
  ADR-037 precedent (hard removal of `--header-backend`).
* The `COMPARE_FLAG_BUDGET` ledger (`cli_options.py`) is the machine-checked
  scoreboard: each collapsed concept lowers `COMPARE_FLAG_BUDGET_BASE`, and
  the ceiling can only rise via a documented `COMPARE_FLAG_BUDGET_RAISES`
  entry (ADR-040 does not add any — it only removes).
* MCP parity (ADR-037 D10.3): the `abi_compare` param↔flag name map collapses
  each per-side pair to the single concept key; the map already keys by
  concept, so the change is a net simplification.
* The frozen `_OPTION_SET_SNAPSHOT` (`tests/test_cli_contract.py`) is updated
  once per landed slice — the deliberate-diff review gate that proves nothing
  drifted silently.

## Rollout

Each phase lands as an **independently green commit** (the surface change,
its boundary normalization, its tests, and its docs together) so the branch
is always shippable — a hard break is salami-sliced by concept, never left
half-migrated with red tests.

* **Phase A — Lever 3 (profiles).** Additive; no removals. *(landed)*
* **Phase B — Lever 1 evidence family.** `header`, `include`, `sources`,
  `build-info` side-aware (the primary flow). Highest-traffic concepts.
  *(landed — `COMPARE_FLAG_BUDGET_BASE` 76→70; the unregistered release engine
  keeps its per-side surface via `release_input_options`.)*
* **Phase C — Lever 1 remainder.** `pdb-path`, `debug-root`, `probe-matrix`
  *(slice 1, landed — `BASE` 70→65)*; `debug-info`, `devel-pkg` *(slice 2,
  landed — `BASE` 65→63)*; `version` *(slice 3, landed — `BASE` 63→62; a
  side-aware `--version` string flag with per-side defaults `old`/`new`)*.
  The `ast-frontend` triple is deliberately **not** collapsed: its base
  `--ast-frontend` is shared with `dump`/`scan` through
  `@compile_context_options`, so a side-aware collapse would fork that shared
  family for one command only — the two per-side overrides stay as-is.
* **Phase D — Lever 2 config demotion.** *(landed as a constraint-aware subset
  — `BASE` 62→57.)* A new `debug:` config block absorbs `--debug-format`,
  `--debuginfod`, `--debuginfod-url`, `--dwarf-only`; `--show-redundant` moves to
  `scope.show_redundant`. All five are now `hidden` and read from config, but
  still override it (CLI > config, the severity-family cadence) rather than being
  hard-removed. The coarse `--debug-root` stays a visible per-run override.
  **Deliberately retained** (not demoted): the toolchain family
  (`--gcc-path/-prefix/-options/-option`, `--sysroot`, `--nostdinc`) is declared
  in the shared `@compile_context_options` decorator that `compare`/`dump`/`scan`
  all compose (ADR-037 D3 parity), so demoting it for `compare` alone would fork
  that family — out of scope for a *compare*-surface reduction; and
  `--scope-public-headers` stays visible as the everyday on/off switch for the
  default public-surface scoping (moving it to config-only would make a
  one-token operation require editing a file).

Each phase updates `COMPARE_FLAG_BUDGET_BASE` downward and the
`_OPTION_SET_SNAPSHOT`; the `TestFlagBudget` ledger tests keep the count and
its rationale in lockstep.
