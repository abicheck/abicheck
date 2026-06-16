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

- **G21.1 — Depth presets on `compare`.** A single dial (`--depth
  quick|standard|deep|max`, with `--max` shorthand) that expands to the right
  `--collect-mode` plus auto-discovery. `--max` ⇒ `--collect-mode graph-full`
  + discovered headers/sources for each side; `--quick` ⇒ L0 only. No new
  collection engine — the preset sets existing knobs.
- **G21.2 — Header/source auto-discovery.** When `-H`/sources are not given,
  look beside each input (sibling `include/`, source tree, an adjacent
  `compile_commands.json`) and report what was found. Compile-DB discovery
  already exists (`buildsource/inline.py:_autodiscover_compile_db`, 6 hint
  dirs); header sibling-path discovery is new.
- **G21.3 — Progressive-disclosure help.** Group options into sections
  (Common / Output / Scoping / Policy & Severity / Debug info / Evidence
  (L3–L5) / Advanced) so `compare --help` leads with ~6 options, not 62.
  Behaviour-preserving. Decided approach: **rich-click**.
- **G21.4 — Actionable coverage warnings.** `confidence.py` warnings carry a
  remediation hint ("no headers/DWARF — pass `-H` or install the `-devel`
  package"), not just the bare condition.

Acceptance = each behaviour-observable criterion (G21.1, G21.2, G21.4) has a
scenario in `tests/scenarios/` (validating `UC-WF-oneshot-deep`) plus a unit
test; G21.3 (help grouping) is behaviour-preserving and carries a unit test
only. The registry entry then flips to `complete` with real `evidence`.

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

## Files & surfaces

- New `abicheck/cli_max.py` (registered per the CLAUDE.md "Adding a new
  top-level command" pattern; `cli.py` is at the 2000-line cap) **or** a
  `--depth` option group added to `compare` — decide at implementation.
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

- `--depth max` expands to `graph-full`; `--quick` to L0-only.
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

- **`--gcc-option` (repeatable, whitespace-safe).** Reverted from PR #422 (the
  `shlex.quote` round-trip is Windows-broken; downstream splits with
  `posix=False`). A correct version threads the literal tokens as a **list** to
  the pure castxml command builder (no string round-trip) — its own follow-up
  PR with cross-platform tests and two-flag docs (`--gcc-option=-include
  --gcc-option="some header.h"`).
- **Auto-synthesizing a `compile_commands.json`** from headers when none is
  found — desirable but a separate change; tracked here as a non-goal for the
  first slice.
- **Default-on "fail loud" / inline-collection fail-loud.** Changing the
  permissive default exit code is a policy change to the best-effort evidence
  contract (ADR-028 D3) and needs its own decision. The opt-in `--collection-mode
  strict` half is already correct (see *Background*).
- **Collapsing the `--old/--new/--both` override triad and unifying
  cross-command flag vocabulary.** A larger refactor; worth its own ADR.
- **L2 clang-direct fallback** (route header AST through the clang backend on
  castxml toolchain-version failure) — separate PR, see G16.
- Conda/`dal-devel` fetching stays in the eval harness, not the tool.

## Background

From the oneDAL evaluation (conda-forge `dal` 2026.0.0 → 2026.1.0, modern Linux
host). L0 gave a defensible `BREAKING`/major-bump verdict at low confidence;
driving L4/L5 against the source tree with system clang-18 produced ~60× richer,
high-confidence evidence — but only after six manual stages. The eval's other
correctness fix from the same investigation — making `--collection-mode strict`
honest about an empty explicitly-requested L4 layer (record it `skipped`, not
`partial`, so strict fails loud) — shipped separately in PR #422.
