# ADR-037: CLI Interface Contract, Configuration Balance, and Extension Policy

**Date:** 2026-06-16
**Status:** Accepted — implemented. Tracked as G22 in
`docs/development/usecase-registry.yaml` (entry `UC-WF-cli-contract`, now
`complete`); phased work in
[plans/g22-cli-consolidation.md](../plans/g22-cli-consolidation.md). All seven
phases landed (D1–D12), enforced by the `cli-contract` AI-readiness gate and
`tests/test_cli_contract.py`. The single residual is the `--ast-frontend android`
value, which stays exposed on `collect`'s `--source-abi-extractor` rather than
the header-AST commands (it has no header-AST path).
**Decision maker:** (pending)

---

## Context

The CLI grew bottom-up: it exposes the internal pipeline
(`dump → collect → merge → compare`, layers L0–L5) almost verbatim. As of
2026-06 it is **~394 options across 31 commands**; `compare` alone has 62,
`compat check` 75, `dump` 39 (measured in G21). Five verdict-emitting commands
(`compare`, `compat check`, `appcompat`, `compare-release`, `plugin-check`)
differ only by *operand*, yet each re-declares the same option families inline.

ADR-035 (D10) already established the right shape for one engine: a typed
`ScanRequest`/`ScanResult` with `service.run_scan` and a `LayerProvider`
protocol. ADR-036 did the same for reporting (one view-model, one severity).
This ADR generalises that discipline to the **entire CLI/API surface** and
fixes the structural problems a flag-level audit surfaced:

1. **Two compare paths.** `cli.py compare` and MCP route through
   `service.run_compare`; `cli_compare_release.py` calls `checker.compare`
   directly with a *different* `scope_to_public_surface` default — so the same
   pair can be classified differently depending on which command ran it.
2. **Copy-pasted option families.** `cli_options.py` has shared decorators, but
   only `compare` uses them (and only for build-source / ADR-027). The severity
   block (5 flags), header/include block, policy/suppress block, and
   debug-resolution block are hand-copied across 4 commands and have already
   drifted (`appcompat` lacks `--strict-suppressions`; `compare-release` lacks
   `--header-backend`/`--demangle`; debug-resolution exists only on `compare`).
3. **Three vocabularies for one concept.** The L/S evidence dial is spelled
   `--collect-mode` (7 values) on `compare`, `--depth`/`--max` (5 values) on
   `deep-compare`/`dump`, and `--mode`+`--source-method`+`--depth` on `scan`.
4. **`--collect-mode` has two different defaults** for the same name
   (`source-target` in the dump decorator, `off` in the compare decorator).
5. **Three exit-code schemes**, switched by *flag presence* (passing any
   `--severity-*` flag silently changes the exit-code meaning).

This is an interface-contract problem, not a missing-feature problem. We are
pre-1.0, so we fix the surface now (with limited deprecation cost) and write
down the contract that keeps it from re-rotting.

---

## Decision

At a glance — the twelve decisions:

| # | Decision | One-line |
|---|----------|----------|
| D1 | Three named tiers | core / service / front-end; service is the only API |
| D2 | Options are data | frozen `*Request` dataclasses, not growing kwargs |
| D3 | One decorator per family | shared CLI options defined once in `cli_options.py` |
| D4 | CLI vs config balance | per-run → CLI; stable project contract → `.abicheck.yml` |
| D5 | One `--depth` dial | drop the "evidence/L-layer" vocabulary from the UI |
| D6 | L5 graph is internal | derived from `--depth source`, never its own mode |
| D7 | Command consolidation | fold `compare-release`/`deep-compare` into `compare` |
| D8 | `--ast-frontend` | rename `--header-backend`; spans header AST + L4 replay |
| D9 | Fail-fast validation | in Tier 2, so CLI and MCP error identically |
| D10 | CI gates the contract | `cli-contract` check makes the above non-optional |
| D11 | Extension procedure | the decision tree for adding any new surface |
| D12 | One exit-code scheme | explicit, never inferred from flag presence |

### D1. Three named tiers; the service layer is the only API

```text
TIER 1  CORE       checker.compare(old_snap, new_snap, *, request) -> DiffResult
                   pure: snapshots in, result out. no Click, no I/O, no format.

TIER 2  SERVICE    service.py — the public Python API. typed request in, result out.
                   run_dump · run_compare · run_scan · render_output · resolve_input.
                   notify-callback, never print.

TIER 3  FRONT-ENDS thin adapters. parse → build request struct → call Tier 2 → exit.
                   cli (Click) · mcp_server (FastMCP) · compat (ABICC dialect).
                   ZERO business logic. ZERO direct Tier-1 calls.
```

**Rule (enforced, see D10):** every verdict-emitting front-end command calls a
`service.run_*` verb. No front-end imports `checker.compare` /
`diff_*` directly. `compare-release` and `appcompat` are loops/wrappers over
`service.run_compare`, not parallel reimplementations.

### D2. Options are data, not signatures

Tier-2 verbs take **frozen request dataclasses**, mirroring ADR-035's
`ScanRequest`. No more growing `run_compare(... 18 kwargs ...)`.

```python
@dataclass(frozen=True)
class InputSpec:
    path: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    pdb: Path | None = None
    debug_roots: tuple[Path, ...] = ()

@dataclass(frozen=True)
class CompareRequest:
    old: InputSpec
    new: InputSpec
    lang: str = "c++"
    frontend: str = "auto"            # D8
    depth: AnalysisDepth = AnalysisDepth.AUTO   # D5
    policy: PolicySpec = field(default_factory=PolicySpec.default)
    suppression: SuppressionSpec | None = None
    scope_public: bool = True
    severity: SeverityConfig = field(default_factory=SeverityConfig.default)
    pattern_verdicts: bool = False
    # new feature == new field with a default. never a signature break.
```

Note the `field(default_factory=...)` for the struct-valued fields: a frozen
dataclass still evaluates a bare `PolicySpec.default()` once at import (a shared
mutable default). Use a factory, not a call.

`compare-release` builds one `CompareRequest` per library pair and calls the
same verb. MCP builds it from JSON. The CLI builds it from flags+config. One
code path classifies; defaults cannot diverge between front-ends (fixes the
`scope_public` True-vs-False drift).

### D3. One decorator per shared option family

`cli_options.py` becomes the single source of truth. Every shared family is a
decorator; commands compose them. Inline re-declaration of a shared family is a
contract violation (CI-checked, D10).

| Decorator | Bundles |
|-----------|---------|
| `@two_sided_input_options` | `-H/--header`, `-I/--include`, `--old/new-header`, `--old/new-include`, `--old/new-version` |
| `@policy_options` | `--policy` (name **or** path — see D4), `--suppress` |
| `@severity_options` | `--severity-preset` only on CLI (per-category lives in config — D4) |
| `@scope_options` | `--scope-public-headers/--no-`, `--show-filtered` |
| `@debug_resolution_options` | `--debug-root{,1,2}`, `--debuginfod[-url]`, `--debug-format`, `--dwarf-only` |
| `@output_options` | `--format`, `-o/--output` |
| `@evidence_options` | `--depth`, `--max`, `--sources` + per-side `--old/new-sources`, `--build-info` + per-side `--old/new-build-info` (D5) |
| `@compile_context_options` | `--ast-frontend`, `--gcc-path`, `--gcc-prefix`, `--gcc-options`, `--gcc-option`, `--sysroot`, `--nostdinc` — the L2 header-AST compile context (D8.1) |

**D8.1 — `dump` and `scan` share the L2 compile context (no drift).** The
cross-toolchain + frontend flags that tell the header frontend *how* to parse the
public headers were declared inline on `dump` but **absent from `scan`** — so a
`scan` of a library whose headers need an include root, a `-std`, or a `-D`
feature macro (e.g. oneTBB's `oneapi/tbb.h`) had no way to supply them and L2
silently failed, dropping the scan to a binary-strict scope that flags internal
removals as BREAKING. The whole family is now defined **once** in
`@compile_context_options` and composed by **both** `dump` and `scan`
(registered-but-not-required, like `@evidence_options`: only the
header-parsing commands carry it). Threaded as a frozen
`service_scan.CompileContext` through `run_dump`/`run_scan` (D2). A
`tests/test_compile_context_parity.py` guard asserts the two commands expose an
identical compile-context flag set, so they cannot drift again.

**Capability parity is part of the frontend contract (D8).** `--ast-frontend`
picks *which* frontend, but the two are only interchangeable (the ADR-003 parity
promise) if they see the **same** translation-unit context. castxml gets that for
free — `castxml --castxml-cc-gnu g++` runs the real compiler to discover its
built-in system include paths. The clang backend (`clang -ast-dump=json`) does
not, so it now auto-probes the host GNU compiler for its system include dirs and
injects them as `-isystem` (on by default; suppressed by `--nostdinc`, an
explicit `--sysroot`, or `ABICHECK_AUTO_SYSTEM_INCLUDES=0`). Auto-detection
recovers the *system* headers (libstdc++/libc); the *project's own* include
roots, `-D` feature macros, and exact `-std` still come from
`-I`/`--gcc-options`, a compile DB, or the config `compile:` block (D4) — see the
limitations in `docs/concepts/limitations.md`.

A command that legitimately wants a *subset* opts out **explicitly with a code
comment stating why** — the absence becomes a deliberate, reviewable decision
instead of an accident.

### D4. CLI vs config — the balance

Two homes, one rule:

> **CLI = the invocation** (what changes per run, what a human/CI types each
> time). **Config = the project's stable contract** (what is version-controlled
> and reviewed in a PR).

`.abicheck.yml` (already introduced in ADR-035 D6) is the project contract; CLI
flags override it. The decision test for any setting:

| Question | → home |
|----------|--------|
| Differs between two runs of the same project? (paths, version labels, output, format) | **CLI** |
| Stable project property, reviewed in PRs? (policy, severity map, suppressions, frozen namespaces, public-surface list, build-query command) | **config** |
| Security-sensitive (spawns subprocess)? | **config only**, never a bare flag (already true: `build.query` needs trusted `--build-config` + `--allow-build-query`) |
| Structured / a long list? (per-kind overrides, per-category severity, cohorts) | **config** |
| One scalar a human flips for a single run? | **CLI** |

Concrete moves into config (CLI keeps a coarse override only):

- **Per-category severity** → `severity:` block in config; CLI keeps only
  `--severity-preset` as a one-shot override. (Removes 4 flags × 4 commands.)
- **FP-tuning** (`--collapse-versioned-symbols`, `--show-redundant`,
  `--public-symbol*`) → `scope:` block. These are stable project properties,
  not per-run decisions. CLI keeps `--show-filtered` (a debugging view).
- **Suppression hygiene** (`--strict-suppressions`, `--require-justification`)
  → `suppression:` policy in config (it's a project rule). CLI keeps nothing;
  CI inherits the project rule automatically.
- **Precise S-axis** (`--source-method s0..s6`) → config `source:` block for
  power users. CLI exposes only the coarse `--depth` (D5).

Result: a configured project runs `abicheck compare old new` and everything
else comes from `.abicheck.yml`. The CLI surface for `compare` drops from ~62
to ~20 flags.

*Accepted tradeoff:* a one-off, single-category severity override (e.g. "treat
quality as error just this once") is no longer a CLI flag — the user picks a
whole `--severity-preset` or edits the config. This is deliberate: per-category
tuning is a reviewed project decision, and the rare one-off is served by the
three presets. If real demand appears, a single escape hatch
(`--severity KEY=LEVEL`, repeatable) can be added later under D11 without
reopening this decision.

### D5. One analysis-depth dial — and drop the "evidence" vocabulary

The user-facing concept is **how deep we analyse**, not "which evidence layer."
"Evidence/L0–L5" stays *internal* implementation vocabulary. The request field
is `AnalysisDepth`; the flag is `--depth`:

```text
--depth {symbols, headers, build, source, full}    # coarse, user-facing
--max                                               # sugar for --depth full
# (auto): depth inferred from inputs — pass --sources ⇒ depth ≥ source
```

| `--depth` | Uses | Replaces |
|-----------|------|----------|
| `symbols` | L0/L1 exported symbols + binary metadata | `--collect-mode off` |
| `headers` | + L2 header AST | (default today) |
| `build` | + L3 build/toolchain context | `--collect-mode build` |
| `source` | + L4 source-ABI replay **and the L5 graph** (D6) | `source-target`, `graph-*` |
| `full`/`--max` | everything available, deepest scope | `graph-full` |

`--collect-mode` (compare), `--mode` (scan), and the standalone
`--source-method` enum are **deprecated aliases** for one release, then removed.
`scan`'s presets (`PR/release/beta`) remain as named bundles that *set*
`--depth` + config, not as a separate vocabulary.

**Migration note — the G21 `--depth graph` value.** G21 already shipped
`--depth {headers,build,graph,source,full}`. This ADR drops `graph` as a rung
(D6) and adds `symbols` at the bottom, so the canonical ladder becomes
`{symbols,headers,build,source,full}`. The just-shipped `--depth graph` value
is therefore itself a **deprecated alias** (→ `--depth source`, which now builds
the graph internally) and goes in `DEPRECATED_FLAGS` alongside `--collect-mode`
et al. — it must not vanish silently in the same release it appeared.

**Why not "evidence":** it leaks the internal L-layer model into the UI and
forces users to learn "graph-summary vs source-target." Depth is a single
monotone ladder a user already understands ("look at symbols / headers / the
build / the source"). The `EvidenceSpec` name proposed earlier is rejected for
the same reason — the request field is `AnalysisDepth`.

### D6. The L5 source graph is internal, not a user-facing mode

The source graph is a *derived* artifact: whenever we have L4 source + L3 build
data we can build it cheaply. It must not be its own depth rung. So:

- `--depth source` builds the graph automatically (default **summary** detail).
- Graph cost variants (`summary` vs `full`) are a **config knob**
  (`source.graph: summary|full`) for the rare project that wants to cap or
  deepen it — never a CLI mode.
- The `graph-build` / `graph-summary` / `graph-full` collect-modes are removed.

This collapses the 7-value `--collect-mode` into the 5-value `--depth` ladder
and matches reality: getting any L3/L4 data already implies graph construction.

### D7. Command consolidation — fewer verbs, clear "new command" bar

`compare-release` and `deep-compare` answer the **same question** (`compare`)
on a different *quantity* or *depth* of operand. Fold them in:

- **`compare-release` → `compare`.** `compare` accepts directories/packages
  (RPM/deb/tar) as inputs and auto-expands to per-library pairs. Multi-library
  concerns become flags that are only meaningful for set inputs (`-j/--jobs`,
  `--dso-only`, `--output-dir` for per-library reports, bundle options) —
  documented as such, and a no-op-with-warning when the inputs are single
  files. `compare-release` becomes a thin deprecated alias. The set case keeps
  its two-level output (a summary on stdout/`-o`, per-library reports under
  `--output-dir`) — folding the command must not lose the fan-out.
  - *Dispatch edge:* file-vs-app detection is heuristic — a PIE executable is
    `ET_DYN`, indistinguishable from a `.so` by ELF type alone. Dispatch must
    not silently treat a binary as the wrong operand kind; when the kind is
    ambiguous (`ET_DYN` without a `DT_SONAME`, or with `DT_FLAGS_1` `PIE`),
    require the user to disambiguate rather than guess. Tracked as a Phase-4
    edge, not a clean switch.
- **`deep-compare` → `compare --max`.** Once `--depth` auto-detects from inputs
  and `--max` exists on `compare`, the orchestrator is redundant. Alias, then
  remove.
- **`appcompat` stays a separate command.** It answers a *different question*
  (consumer-side: "is this application still satisfied?") with a different
  verdict semantics (affected/irrelevant), different operands (app + lib(s)),
  and a weak mode (`--check-against`). But it routes through the same Tier-2
  service and uses the same decorators — duplication dies even though the verb
  stays.

**Bar for a new top-level command** (vs a flag): a command earns its own verb
only if it (a) asks a **different question** (different verdict semantics), or
(b) takes a **fundamentally different operand shape**. "Same question, more
operands / more depth / different format" is a **flag or input-type dispatch**,
never a new command. (Applies retroactively: `compare-release`/`deep-compare`
fail the bar; `appcompat`/`plugin-check`/`scan`/`surface-report` pass it.)

### D8. `--header-backend` → `--ast-frontend` (not per-layer, not generic-vague)

`--header-backend {auto,castxml,clang}` selects the C/C++ frontend that turns
source into the model. The **same engines** also back L4 source-ABI replay
(`--source-abi-extractor {auto,clang,castxml,android}`). They are the same
choice applied at two pipeline stages. So:

- Rename to **`--ast-frontend {auto,castxml,clang}`** (env
  `ABICHECK_AST_FRONTEND`, old names aliased). It governs **both** header AST
  parsing and source-ABI replay — one knob for "which frontend."
- `android` stays a source-ABI-only value (it has no header-AST path);
  selecting it for a header-only run is a validation error (D9).

**Why not a generic `--backend`:** "backend of what?" is ambiguous — we have
ELF/DWARF/PE/PDB/Mach-O *parser* backends too, which are auto-selected by
artifact type and are not user choices. `--ast-frontend` names exactly the one
axis the user actually picks (the source→AST frontend) and is correctly *not*
tied to "headers." It is generic across pipeline stages, specific in meaning.

### D9. Input validation (fail fast, fail clear)

Front-ends validate the assembled request *before* any heavy work:

- **Mutually-exclusive flags** declared once (Click `mutually_exclusive` group
  or an explicit check) — e.g. `--depth` vs deprecated `--collect-mode`;
  `--policy <name>` semantics vs a `--policy <path>` that doesn't exist.
- **Value validation** at parse time: a frontend/depth/format value not in the
  enum errors with the allowed set; `--ast-frontend android` with no
  `--sources` errors.
- **Pre-flight feasibility:** requesting `--depth source` with no `--sources`
  and no embedded source pack is a hard error (not a silent empty layer) —
  ADR-035 D-strict already established "fail loud on an empty requested layer";
  this generalises it to all depths.
- Validation lives in Tier 2 (`CompareRequest.validate()`), so MCP and CLI get
  identical errors.

### D10. Enforcement — CI gates the contract

Add a `cli-contract` check to `scripts/check_ai_readiness.py` (ERROR severity)
plus a unit test, asserting:

1. **No Tier-skip:** no `cli*.py` module *calls* the Tier-1 entry points
   (`checker.compare`, the `diff_*` orchestration functions) directly —
   front-ends must go through `service`. AST scan on call sites, not bare
   imports: importing a `diff_*` / `checker_types` **type** for annotations or
   result handling is allowed (and unavoidable for rendering), so the gate keys
   on the call expression, not the `import` statement.
2. **Shared-decorator coverage:** every command in the verdict-emitting set
   carries the required decorators from `cli_options.py` (introspect the
   command's params against each decorator's param set); a command missing one
   must be on an explicit `INTENTIONAL_SUBSET` allowlist with a reason string.
3. **MCP↔CLI name map complete:** a single `MCP_CLI_NAME_MAP` table is the
   source of truth; the test fails if an MCP tool param or CLI flag is absent
   from it (so they cannot silently diverge — fixes `output_format` vs
   `--format`, `include_dirs` vs `-I`).
4. **One default per flag name:** a flag name declared in two decorators with
   two defaults fails (catches the `--collect-mode` double-default).
5. **Option-count budget (WARN):** per-command flag count over a threshold
   warns, nudging settings into config (D4).

### D11. Extension procedure (how to grow the CLI without re-rotting)

When a feature needs new surface, walk this tree:

1. **Is it the project's stable contract?** → add a config key (D4), not a
   flag. Document in the `.abicheck.yml` schema.
2. **Is it a per-run scalar/path/format?** → add a CLI flag.
   a. Belongs to an existing shared family? → extend the **decorator** (D3),
      never inline it on one command.
   b. New family used by ≥2 commands? → add a new decorator.
3. **Is it a different question / operand shape?** → new command (D7 bar);
   otherwise it is a flag or input-type dispatch on an existing command.
4. **Always:** add the corresponding `*Request` field (D2), the MCP param +
   name-map row (D10.3), validation (D9), and `--help` text. The CI gate (D10)
   fails the PR if any of these are skipped.

### D12. One exit-code scheme, declared not inferred

The legacy/severity schemes are kept for back-compat but the active one is
**explicit**, never inferred from flag presence:

- `--exit-code-scheme {auto,legacy,severity}` (default `auto` = current
  behaviour, documented). Passing `--severity-*` no longer *silently* switches
  meaning — it is recorded as a deliberate scheme selection and surfaced in
  `--help` and the run header.
- The chosen scheme is a *project-stable* decision (CI scripts key on it), so it
  is also settable in `.abicheck.yml` (`exit_code_scheme:`) per D4, with the CLI
  flag as the per-run override. This keeps a project's CI contract in the
  reviewed config rather than scattered across workflow YAML.
- The ABICC `compat` command keeps its own distinct exit-code taxonomy (see
  `compat/cli.py`); it is **not** offered as a scheme value on native `compare`
  — mixing the two vocabularies on one command is precisely the inference
  ambiguity this decision removes.

---

## Worked scenarios (design validation)

Walking real invocations through the contract — both to show the intended UX and
to prove the tiers/decorators actually compose. Each scenario names the tier
path and what each layer does. Frictions found while writing these were folded
back into D4/D5/D7/D12 above.

### S1 — PR gate, configured project (the 90% case)
```text
abicheck compare libfoo.so.1 libfoo.so.2
```
`.abicheck.yml` supplies policy, severity map, suppressions, scope, and
`exit_code_scheme`. CLI builds a `CompareRequest` (D2) from two `InputSpec`s +
config; Tier 2 `run_compare` classifies; exit code per the configured scheme
(D12). **Twenty-flag command, two-token invocation.** This is the payoff.

### S2 — release / package comparison (absorbs `compare-release`)
```text
abicheck compare ./old/ ./new/ -j8 --dso-only --output-dir reports/
```
Input-type dispatch (D7) sees directories, expands to per-library pairs, runs
`run_compare` per pair in parallel, writes a summary to stdout and per-library
reports under `--output-dir`. Same classifier as S1 → a library compared here
and in S1 gets an identical verdict (D1). `compare-release ...` still works as a
deprecated alias.

### S3 — deep one-shot with source evidence (absorbs `deep-compare`)
```text
abicheck compare libfoo.so.1 libfoo.so.2 --old-sources ./v1 --new-sources ./v2 --max
```
`--max` ⇒ `depth=full` (D5); per-side `--old/new-sources` ride the
`@evidence_options` decorator (D3). Tier 2 collects L3/L4 and builds the L5
graph internally (D6) — no `graph-*` mode to learn. `deep-compare ...` is a
deprecated alias.

### S4 — application compatibility (stays its own verb)
```text
abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2
abicheck appcompat ./myapp --check-against libfoo.so.2     # weak mode
```
Different *question* (consumer-side), so a distinct command per the D7 bar — but
it shares `@policy_options`/`@severity_options`/`@scope_options` and calls the
same Tier-2 service, so no option drift.

### S5 — AI agent over MCP
```json
{"tool": "abi_compare",
 "args": {"old": "a.so", "new": "b.so", "depth": "source", "policy": "strict_abi"}}
```
`MCP_CLI_NAME_MAP` (D10.3) translates JSON keys to the same `CompareRequest`;
`request.validate()` (D9) runs before any work, so a bad `depth` yields the
*same* error text a CLI user sees. One classifier, one validation, two
front-ends. (Keys shown are the post-name-map target spelling; the live tool
today uses `old_input`/`new_input`/`old_headers` — the map is exactly what
reconciles them.)

### S6 — snapshot now, compare later (offline / cross-machine)
```text
abicheck dump libfoo.so.2 --max --sources ./v2 -o v2.abi.json     # build host
abicheck compare v1.abi.json v2.abi.json                          # CI host
```
`dump` shares `@evidence_options`, so `--depth`/`--max`/`--sources` mean exactly
what they mean on `compare`. `resolve_input` (Tier 2) accepts the JSON snapshot
transparently — the second invocation needs no source/build access.

### S7 — cross-compiled lib with split debug + clang frontend
```text
abicheck compare arm/old.so arm/new.so --debug-root ./dbg --debuginfod --ast-frontend clang
```
`@debug_resolution_options` (D3) is now present on `compare` (it was
`compare`-only before, but the *decorator* makes it uniformly available). One
`--ast-frontend` (D8) drives both header AST and any L4 replay.

**What the walk-through surfaced** (now fixed above): per-side `--old/new-sources`
must live on `@evidence_options` (S3); set-input runs need `--output-dir`
fan-out (S2); `exit_code_scheme` belongs in config too (S1). All three were
gaps in the first draft — the imagination game earned its keep.

---

## Backward compatibility (designed now, enforced post-1.0)

We are pre-1.0; this cleanup may break invocations, and that is acceptable
**now**. But the *mechanism* for stability is defined here so it can be switched
on at 1.0 without redesign:

- **Deprecation window.** A removed/renamed flag becomes a hidden alias that
  still works and prints a deprecation note to stderr for **one minor release**,
  then is removed. Registry: a `DEPRECATED_FLAGS` table (name → replacement →
  removal version) is the single source of truth; a test asserts every alias in
  it still resolves.
- **Stable machine output.** `--format json`/`sarif`/`junit` carry a
  `schema_version` (already true for snapshots, ADR-028); within a major
  version the schema is additive-only. Breaking a machine schema bumps the
  major.
- **Stable exit codes.** Within a major version, exit-code *meanings* are
  frozen per scheme (D12). New conditions reuse existing codes or are gated
  behind a new scheme value.
- **Config schema versioning.** `.abicheck.yml` gets a top-level `version:`;
  unknown keys warn (forward-compat) rather than error.
- **Switch-on criteria.** At the 1.0 tag: freeze the flag set, enable the
  deprecation-window test as ERROR, and document the compatibility promise in
  `docs/reference/`. Until then the deprecation table is advisory.

---

## Consequences

**Positive**

- One classification path: a pair gets the same verdict from `compare`,
  `compare-release`, `appcompat`, and MCP.
- Shared families defined once; the §Context divergences disappear and any
  future one is caught by CI, not by a user.
- `compare`'s CLI surface ~62 → ~20 flags; commands ~31 → fewer (compare-release
  and deep-compare fold in). The rest moves to a reviewable project config.
- One depth vocabulary across `compare`/`scan`/MCP; `--help` is teachable.
- Adding a feature is a dataclass field + decorator/config key + map row, not a
  4-command copy-paste.

**Negative / cost**

- Breaking changes to current invocations (mitigated by the deprecation-alias
  mechanism, even though we are not yet *contractually* bound to it).
- `compare` gains input-type dispatch (file vs dir vs package vs app), adding
  branching at the front-end — but removing a whole command's worth of
  duplication.
- A new CI gate to maintain (offset by the drift it prevents).

---

## Alternatives considered

- **Leave commands separate, just share decorators.** Fixes duplication but not
  the two-path classification drift (D1) or the three-vocabulary problem (D5);
  keeps 5 near-identical verbs. Rejected — half the win.
- **Fold *everything* (incl. appcompat/plugin-check) into `compare` with
  modes.** Maximally small surface, but overloads one command with flags only
  valid in sub-cases — the exact conditional-flag mess we are removing. Rejected
  by the D7 bar (different *question* ⇒ different command).
- **Keep "evidence" vocabulary / `EvidenceSpec`.** Rejected (D5): leaks
  internal L-layer model into the UI.
- **A generic `--backend` flag.** Rejected (D8): ambiguous across parser vs
  frontend backends.
- **Per-category severity stays on the CLI.** Rejected (D4): it is structured,
  stable project policy — belongs in version-controlled config; CLI keeps only
  the coarse preset override.

---

## Relationship to existing ADRs

- **ADR-035 (D10)** — established typed request/result + `service.run_scan`;
  D2 generalises that pattern to `CompareRequest`/`run_compare`.
- **ADR-035 (D6)** — `.abicheck.yml`; D4 extends its scope to the severity,
  scope, and suppression-policy blocks.
- **ADR-036** — one report view-model / severity; D5/D12 keep the *input* side
  as disciplined as ADR-036 made the *output* side.
- **ADR-028** — snapshot/output `schema_version`; reused for machine-output
  stability in §Backward compatibility.
- **G21** — shipped the `--depth` dial and one-shot `deep-compare`; D5 refines
  the vocabulary (drops `graph` as a user rung, D6) and D7 folds the
  orchestrator back into `compare`.

## References

- `docs/development/plans/g22-cli-consolidation.md` — phased implementation.
- Flag-divergence audit (2026-06): `compare` vs `compare-release` vs
  `appcompat` vs MCP `abi_compare`.
