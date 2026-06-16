# G22 — CLI consolidation & interface-contract enforcement

**Type:** Refactor / UX plan. Implements [ADR-037](../adr/037-cli-interface-contract.md).
Tracked by `usecase-registry.yaml` entry `UC-WF-cli-contract` (gap G22).
**Effort:** L (touches every `cli_*.py`, `service.py`, `mcp_server.py`,
`cli_options.py`, plus a new CI gate) · **Risk:** medium — behaviour-preserving
for the common path, but folds commands and moves settings to config.
**Builds on:** G21 (depth dial, one-shot `deep-compare`), ADR-035 (typed
requests, `.abicheck.yml`), ADR-036 (report view-model).

## Problem

The CLI exposes the internal pipeline (~394 options / 31 commands). Five
verdict-emitting commands differ only by operand yet re-declare option families
inline; `compare-release` bypasses `service.run_compare` and classifies with a
different `scope_public` default; the evidence dial has three vocabularies;
exit-code meaning is inferred from flag presence. See ADR-037 §Context for the
full audit.

## Goal & acceptance criteria

1. Every verdict-emitting front-end command routes through a `service.run_*`
   verb. **AC:** new `cli-contract` CI check (ADR-037 D10.1) passes; no
   `cli*.py` imports `checker.compare`/`diff_*`.
2. Shared option families exist once as decorators. **AC:** D10.2 coverage check
   passes; `INTENTIONAL_SUBSET` allowlist holds every deliberate exception with
   a reason.
3. One depth vocabulary. **AC:** `--collect-mode`/`--mode`/`--source-method`
   resolve as deprecated aliases of `--depth`; `--help` shows one dial; `graph`
   is no longer a user-facing depth (D6).
4. `compare-release` and `deep-compare` are deprecated aliases; `compare`
   accepts dir/package inputs and `--max`. **AC:** existing
   `compare-release`/`deep-compare` tests pass against the alias; new tests
   cover `compare <dir> <dir>`.
5. `--header-backend` → `--ast-frontend` (header AST + source-ABI), old name
   aliased. **AC:** alias test; `--ast-frontend android` without `--sources`
   errors.
6. Per-category severity, FP-tuning, suppression hygiene, and precise S-axis
   move to `.abicheck.yml`; CLI keeps the coarse overrides only. **AC:**
   config round-trips; CLI flag override beats config; per-command flag count
   under budget (D10.5).
7. One explicit exit-code scheme (`--exit-code-scheme`, default `auto`). **AC:**
   passing `--severity-*` no longer silently changes the scheme.
8. Validation is fail-fast and lives in Tier 2. **AC:** identical error text
   from CLI and MCP for the same bad request.
9. MCP params and CLI flags share one name map. **AC:** D10.3 completeness test.

Non-goals: changing detection/classification logic; the ABICC `compat` dialect
(it intentionally mirrors abi-compliance-checker spelling — it routes through
Tier 2 but keeps its own flags).

## Phases

### Phase 1 — Typed requests + single chokepoint (ADR-037 D1, D2)
- Add `api_types.py`: `InputSpec`, `CompareRequest` (+ `validate()`),
  `OutputSpec`. Reuse `SeverityConfig`, `PolicySpec`, `SuppressionSpec`,
  `AnalysisDepth`.
- Refactor `service.run_compare` to take `CompareRequest`; keep a thin
  back-compat shim for the old kwargs during the phase.
- Route `cli_compare_release._run_compare_pair` and `cli_appcompat` through
  `service.run_compare` (kills the `scope_public` default drift).
- **Gate:** add `cli-contract` D10.1 (no Tier-skip) to
  `scripts/check_ai_readiness.py` + `tests/test_cli_contract.py`.

### Phase 2 — Decorator-ize shared families (D3)
- Define the 7 decorators in `cli_options.py` (ADR-037 D3 table).
- Replace inline declarations in `compare`, `compare-release`, `appcompat`,
  `deep-compare`, `dump`, `scan` (where applicable).
- Add `INTENTIONAL_SUBSET` allowlist + D10.2/D10.4 checks.
- This phase alone removes the §Context divergences and is independently
  shippable.

### Phase 3 — Depth vocabulary + L5-internal (D5, D6)
- `--depth {symbols,headers,build,source,full}` + `--max` via `@evidence_options`.
- `--collect-mode`/`--mode`/standalone `--source-method` → deprecated aliases
  (record in `DEPRECATED_FLAGS`).
- Make the L5 graph an internal consequence of `--depth source`; remove
  `graph-*` rungs; add `source.graph: summary|full` config knob.

### Phase 4 — Command consolidation (D7)
- `compare` input-type dispatch: file / snapshot / directory / package / (app →
  hint to `appcompat`). Move `-j`, `--dso-only`, bundle opts under set-inputs.
- `compare-release` and `deep-compare` → thin deprecated aliases.
- Keep `appcompat`/`plugin-check` as distinct verbs (different question).

### Phase 5 — CLI↔config rebalance (D4)
- Extend `.abicheck.yml` schema: `severity:` (per-category), `scope:`
  (FP-tuning, public-surface list), `suppression:` (strict/justification
  policy), `source:` (precise S-axis, graph detail).
- CLI keeps coarse overrides (`--severity-preset`, `--show-filtered`,
  `--depth`). Override precedence: CLI > config > built-in default.
- `--exit-code-scheme` (D12). Add D10.5 flag-count budget (WARN).

### Phase 6 — `--ast-frontend`, MCP name-map, validation, docs (D8, D9, D10.3)
- Rename `--header-backend` → `--ast-frontend` (+ env, + alias); wire to L4
  extractor selection.
- `MCP_CLI_NAME_MAP` single table; align `mcp_server.py` params; D10.3 check.
- `CompareRequest.validate()` covers mutually-exclusive flags, enum values,
  depth feasibility (D9).
- Regenerate CLI docs (`docs/user-guide/cli-flags.md` from `--help`), update
  `docs/reference/exit-codes.md`, add `.abicheck.yml` schema reference.

### Phase 7 — Backward-compat scaffolding (future-enabled)
- `DEPRECATED_FLAGS` table + resolver + stderr notes; test asserting each alias
  resolves. Left **advisory** (not ERROR) until 1.0 per ADR-037
  §Backward compatibility.
- Add `version:` to `.abicheck.yml`; unknown keys warn.

## Sequencing notes

- Phases 1–2 are the highest value / lowest risk and land first (they fix the
  drift and the bypass without changing user-visible behaviour much).
- Phases 3–4 are user-visible; ship with deprecation aliases so nothing breaks
  hard in one release.
- Phase 5 is the biggest UX shift (flags → config) — do it after the structure
  is sound so settings move to a stable home.

## Definition of done (when implementation lands)

This is a docs-only PR; G22 stays `planned` and ADR-037 stays `Proposed` until
the work below merges. The gap is considered closed only once registry
`UC-WF-cli-contract` can flip to `complete` with evidence pointing at
`api_types.py`, the `cli-contract` gate, `tests/test_cli_contract.py`, and the
alias/round-trip tests — at which point ADR-037 moves to Accepted — implemented.
