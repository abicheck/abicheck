# G21 — One-shot deep compare & CLI usability

**Type:** Workflow/UX plan; tracked by the `planned` `usecase-registry.yaml`
entry `UC-WF-oneshot-deep` under gap G21.
**Effort:** M (depth presets + auto-discovery) · **Risk:** low — presets ride
on the existing `--collect-mode` machinery; no new analysis engine.
**Motivation:** the oneDAL field evaluation (2026-06) — see *Background*.

## Problem

abicheck's CLI exposes its internal **pipeline architecture** as the user
interface. The data flow is `dump → collect → merge → compare` (L0→L5), and to
get deep evidence a user must run all four stages by hand. The oneDAL eval hit
exactly this: the L0 verdict was defensible but `confidence: low` (the runtime
package shipped no headers/DWARF), and reaching high-confidence L4/L5 evidence
took six manual stages plus a hand-synthesized compile DB.

Structural picture (measured 2026-06): **394 options across 31 commands**;
`compare` alone has 62, `compat check` 75, `dump` 39, `collect` 32. Five
commands (`compare`, `compat check`, `appcompat`, `compare-release`,
`plugin-check`) all emit a verdict and differ only by operand, which is where
most option duplication comes from. `--help` is a flat, ungrouped list.

Two specific failure modes from the eval:

- **No one-shot path.** There is no single command that runs the available
  tiers and degrades per-tier. (UX-1.)
- **Deep layers succeed-but-empty.** `collect --source-abi` with no L3 build
  context exits 0 with a buried note. (UX-4. The strict-mode half of this is
  already fixed — see *Background*.)

This is an architecture-level UX problem, not a missing flag.

## Goal & acceptance criteria

- **G21.1 — Depth dial.** A single `--depth headers|build|graph|source|full`
  (with `--max` = `--depth full`), reusing the *same vocabulary and mapping as
  `scan --depth`* (`scan_levels.depth_to_method` + `method_to_collect_mode`) so
  the commands stay consistent. **Shipped on `dump`** (the real inline-collection
  entrypoint) in PR #422. `compare` gains it via the orchestrator (G21.9) —
  `compare`'s own `--collect-mode` does not yet collect from a source tree, so a
  bare alias there would be cosmetic.
- **G21.2 — Header/source auto-discovery.** When `-H`/sources are not given,
  look beside each input (sibling `include/`, source tree, an adjacent
  `compile_commands.json`) and report what was found. Compile-DB discovery
  already exists (`buildsource/inline.py:_autodiscover_compile_db`, 6 hint
  dirs); header sibling-path discovery is new.
- **G21.3 — Progressive-disclosure help (collapse M1).** rich-click option
  groups so each big command's `--help` leads with ~6 Common options. See
  *Option collapsing* below.
- **G21.4 — Actionable coverage warnings.** `confidence.py` warnings carry a
  remediation hint ("no headers/DWARF — pass `-H` or install the `-devel`
  package"), not just the bare condition.
- **G21.5 — Repeatable `--gcc-option` (cross-platform-correct).** Thread the
  literal tokens as a **list** to the pure castxml command builder (no
  `shlex.quote`/`shlex.split` string round-trip, which is Windows-broken) so a
  flag value with spaces survives intact; docs show the two-flag form
  (`--gcc-option=-include --gcc-option="some header.h"`). (Reinstated from
  out-of-scope.)
- **G21.6 — Auto-synthesize a `compile_commands.json`** from discovered
  headers/sources when none is found, with a clear log line; slots into
  `inline.py:_resolve_compile_db` after auto-discovery returns nothing.
  (Reinstated.)
- **G21.7 — Fail loud on an empty *requested* layer.** Surface an explicitly
  requested but empty L4/L5 layer via exit code / prominent warning rather than
  a buried note. The opt-in `--collection-mode strict` half already does this
  (shipped in PR #422); this adds a default-visible signal for the inline
  (`dump --sources` / a future `compare` orchestrator) path. Changing the
  *permissive default exit code* remains gated on an ADR-028 D3 decision.
  (Reinstated.)
- **G21.8 — Option collapsing (M1–M6).** Reduce the 394-option surface — see
  the dedicated section below.
- **G21.9 — One-shot deep `compare`.** The orchestrator that dumps both sides
  with `--sources` at the chosen `--depth` (inline L3–L5 embed) then compares,
  with auto-discovery (G21.2) — the headline "deep compare in one command".

Acceptance = each behaviour-observable criterion has a scenario in
`tests/scenarios/` (validating `UC-WF-oneshot-deep`) plus a unit test;
presentation-only criteria (G21.3 help grouping) carry a unit test only. The
registry entry flips to `complete` with real `evidence` once G21.1/2/9 land.

## Design

- **Presets are a thin front, not a new engine.** `--collect-mode` already maps
  modes → layers via `collection_for_ci_mode()`
  (`buildsource/source_replay.py`); `compare` already accepts
  `--old-sources`/`--new-sources` and inline collection (ADR-033). `--depth`
  expands to those.
- **Keep the primitives.** `dump`/`collect`/`merge` stay as the explicit,
  cacheable stages for CI power-users (run `collect` at build time where the
  compile DB is live, attach its pack to a later `compare`). The depth preset
  only removes the need to know them for the common case. Do **not** merge
  `dump` and `collect` — their operand/timing split is legitimate (artifact
  stage vs build stage; ADR-028/031/033).
- **Auto-discovery is best-effort and loud.** Discovery never fails the run;
  it logs exactly what it found/used so the result is reproducible.

## Option collapsing (M1–M6) — G21.8

The 394-option surface (31 commands; `compare` 62, `compat check` 75, `dump`
39, `collect` 32) collapses along six mechanisms, ordered by leverage/risk:

- **M1 — rich-click option groups (presentation, 0 behaviour change).** Each
  big command's `--help` leads with ~6 *Common* options; the rest fold into
  *Output / Scoping / Policy & Severity / Debug info / Evidence (L3–L5) /
  Per-side overrides / Advanced*. Declared via
  `rich_click.OPTION_GROUPS["abicheck compare"] = [...]`. Biggest perceived-
  complexity win, lowest risk. (= G21.3.)
- **M2 — presets that subsume clusters.** `--depth`/`--max` (shipped on dump)
  subsumes the 7 `--collect-mode` values; `--severity-preset` (exists) subsumes
  the 4 `--severity-*`. Granular flags survive as Advanced overrides.
- **M3 — degenerate boolean families → one `Choice`.** Already applied
  (`--btf/--ctf/--dwarf/--dwarf-only` → `--debug-format`, booleans hidden);
  audit for any other family.
- **M4 — the old/new/both triad (presentation).** 5 inputs × 3 = 15 options
  (header/include/version/pdb/debug-root). Per-side overrides can't be removed;
  collapse = group all `--old-*`/`--new-*` under one *Per-side overrides*
  section (via M1) so the shared `-X` leads.
- **M5 — cross-command vocabulary unification (real, deprecation cycle).**
  Canonical `-H/--header` (vs `--headers` on collect), align
  `--build-info`/`--sources`; old names become hidden aliases for one release.
- **M6 — entrypoint signposting.** The five verdict commands
  (`compare`/`compat check`/`appcompat`/`compare-release`/`plugin-check`) can't
  merge (different operands); add a "which command?" decision to the
  `compare --help` epilog and make `compare` the obvious front door.

Headline = M1 + M2 (lowest risk, biggest cognitive collapse). M5 needs a
deprecation cycle. M4/M6 are presentation. M3 is mostly done.

## Files & surfaces

- New `abicheck/cli_max.py` for the one-shot orchestrator (G21.9), registered
  per the CLAUDE.md "Adding a new top-level command" pattern (`cli.py` is at the
  2000-line cap).
- `abicheck/buildsource/inline.py` — header sibling-path discovery alongside
  the existing compile-DB discovery.
- `abicheck/confidence.py` — remediation text on coverage warnings.
- `pyproject.toml` — add `rich-click` dependency for G21.3.

## Tests

Scenarios (`tests/scenarios/*.yaml`, each `validates: UC-WF-oneshot-deep`):

- `SC-WF-MAX-DEEP` — `compare --max` over a source tree populates L3–L5
  (non-empty `evidence_tiers`).
- `SC-WF-AUTODISCOVER` — headers/compile-DB found beside inputs without
  explicit flags.
- `SC-RPT-COVERAGE-ACTIONABLE` — a coverage warning contains a remediation
  hint.

Unit tests (CliRunner pattern, e.g. `tests/test_build_source_cli.py`,
`tests/test_cov95_cli.py`):

- `--depth full`/`--max` expands to `graph-full`; `--depth headers` to `off`
  (done for `dump` in PR #422; `resolve_dump_depth` unit-tested per mapping).
- auto-discovery picks the sibling header/compile-DB; logs what it used.
- `compare --help` shows the group headers (extend the existing
  substring-in-help assertions).

## Example fixtures

No new `examples/case*` is required — these are workflow/UX features, not new
`ChangeKind`s, and the examples harness is verdict-diff calibration. The
purpose-built home is `tests/scenarios/`. **Optional** follow-up: one demo case
(like the existing L3 `case130–133`) showing depth-recovery — L0-only → low
confidence vs `--max` → high confidence — as living documentation.

## Effort & risk

M overall. G21.1/G21.2 are the headline win and ride on existing machinery
(low risk). G21.3 is additive (no behaviour change). G21.4 is small.

## Out of scope

`--gcc-option` (G21.5) and inline-path fail-loud (G21.7) and the vocab alias
(M5) shipped. What remains out of scope:

- **G21.6 `compile_commands.json` auto-synthesis** and **G21.2 header/source
  auto-discovery** — both *guess the inputs*, which collides with the deliberate
  **P09** design (`buildsource/inline.py` `embed_build_source` intentionally
  **warns** — "no `compile_commands.json`; run `bear -- make` or pass
  `--build-info`" — rather than fabricating a flag-less DB or grabbing sibling
  headers, which would silently produce a wrong/low-confidence ABI surface). If
  pursued, they must be **explicit opt-in** flags (e.g. `--synthesize-compile-db`,
  `--auto-headers`) so the P09 default is preserved — a deliberate decision, not
  a default behaviour change. Deferred.
- **Changing the *permissive default* exit code** so an empty layer fails
  without `--collection-mode strict`. That is a policy change to the best-effort
  evidence contract (ADR-028 D3) and needs its own decision; G21.7 only adds a
  default-visible *warning* plus the existing opt-in strict failure.
- **L2 clang-direct fallback** (route header AST through the clang backend on a
  castxml toolchain-version failure) — separate PR, see G16.
- Conda/`dal-devel` fetching stays in the eval harness, not the tool.

The one substantial item still open is **G21.9** — the one-shot `compare`
orchestrator (dump both sides with `--sources` at `--depth`, then compare). It
is P09-compatible (the user supplies sources explicitly) and is the headline
deep-compare feature; sized for its own focused PR.

## Background

From the oneDAL evaluation (conda-forge `dal` 2026.0.0 → 2026.1.0, modern Linux
host). L0 gave a defensible `BREAKING`/major-bump verdict at low confidence;
driving L4/L5 against the source tree with system clang-18 produced ~60× richer,
high-confidence evidence — but only after six manual stages. The eval's other
correctness fix from the same investigation — making `--collection-mode strict`
honest about an empty explicitly-requested L4 layer (record it `skipped`, not
`partial`, so strict fails loud) — shipped separately in PR #422.
